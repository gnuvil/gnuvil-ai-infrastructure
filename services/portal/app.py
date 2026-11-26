"""Gnuvil AI Lab: server-rendered UI and a small Unix-socket client."""

import asyncio
from datetime import datetime, timedelta, timezone
import hmac
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.datastructures import MutableHeaders

from service_config import load_yaml, validate_portal

BASE = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("PORTAL_CONFIG", BASE.parent.parent / "configs/portal.yaml"))
SETTINGS = validate_portal(load_yaml(CONFIG_PATH))
SOCKET_PATH = os.environ.get("PORTAL_SOCKET", "/run/gnuvil-ai/controller.sock")
PROXY_TOKEN = os.environ.get("PORTAL_PROXY_TOKEN", "")
COOKIE = "gnuvil_csrf"
STATES = {"stopped": "중지됨", "starting": "시작 중", "ready": "접속 가능", "busy": "작업 중",
          "stopping": "종료 중", "error": "오류", "unknown": "확인 불가"}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[*SETTINGS["allowed_hosts"], "127.0.0.1"])
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


async def controller_request(payload):
    writer = None
    try:
        async with asyncio.timeout(4):
            reader, writer = await asyncio.open_unix_connection(SOCKET_PATH, limit=262144)
            writer.write(json.dumps(payload).encode() + b"\n")
            await writer.drain()
            response = await reader.readline()
            if not response.endswith(b"\n"):
                raise ValueError("Incomplete controller response")
            return json.loads(response)
    finally:
        if writer:
            writer.close()
            await writer.wait_closed()


_cache = None
_cache_until = 0
_cache_lock = asyncio.Lock()


async def snapshot():
    global _cache, _cache_until
    async with _cache_lock:
        if _cache is not None and time.monotonic() < _cache_until:
            return _cache
        try:
            response = await controller_request({"action": "snapshot"})
            if response.get("ok") is not True:
                raise ValueError("Controller snapshot failed")
            data = response["data"]
            data["connected"] = True
        except (OSError, ValueError, KeyError, TimeoutError):
            data = {"connected": False, "operation": None, "metrics": {}, "services": {
                key: {"state": "unknown", "reason": "서버와 연결되지 않았습니다. 운영자에게 문의하세요.", "can_stop": False}
                for key in SETTINGS["services"]
            }}
        _cache, _cache_until = data, time.monotonic() + 2
        return data


class SecurityMiddleware:
    """Authenticate without buffering or cancelling streamed static responses."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        denied = None
        if request.url.path != "/healthz":
            provided = request.headers.get("x-portal-token", "")
            if len(PROXY_TOKEN) < 32 or not hmac.compare_digest(provided.encode(), PROXY_TOKEN.encode()):
                denied = "인증된 Reverse Proxy를 통해 접속해 주세요."
            elif request.method not in {"GET", "HEAD"}:
                cookie = request.cookies.get(COOKIE, "")
                csrf = request.headers.get("x-csrf-token", "")
                if (request.headers.get("origin") != SETTINGS["public_origin"] or len(cookie) < 32
                        or not hmac.compare_digest(cookie.encode(), csrf.encode())
                        or request.headers.get("sec-fetch-site") == "cross-site"):
                    denied = "요청을 확인할 수 없습니다. 페이지를 새로고침해 주세요."

        async def secure_send(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).update({
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "same-origin",
                    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                })
            await send(message)

        if denied:
            return await HTMLResponse(denied, status_code=403)(scope, receive, secure_send)
        await self.app(scope, receive, secure_send)


app.add_middleware(SecurityMiddleware)


def render(request, template, data, *, message=None):
    csrf = request.cookies.get(COOKIE) or secrets.token_urlsafe(32)
    collected_at = data.get("collected_at")
    collected_time = None
    if collected_at:
        try:
            collected_time = datetime.fromisoformat(collected_at).astimezone(
                timezone(timedelta(hours=9), "KST")
            ).strftime("%Y-%m-%d %H:%M:%S KST")
        except (TypeError, ValueError):
            pass
    services = []
    active_gpu = data.get("active_gpu")
    for key, definition in SETTINGS["services"].items():
        state = data["services"].get(key, {"state": "unknown", "reason": "상태를 확인할 수 없습니다.", "can_stop": False})
        services.append({**definition, **state, "id": key,
                         "needs_confirm": "gpu_service" in definition and (active_gpu is None or any(other != definition["gpu_service"] for other in active_gpu))})
    response = templates.TemplateResponse(request=request, name=template, context={
        "settings": SETTINGS, "services": services, "data": data, "states": STATES, "csrf": csrf, "message": message, "collected_time": collected_time,
    })
    response.set_cookie(COOKIE, csrf, httponly=True, secure=SETTINGS["public_origin"].startswith("https:"),
                        samesite="strict", path="/")
    return response


@app.get("/healthz")
async def health():
    # Liveness remains healthy during GPU or controller failure; no host details exposed.
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return render(request, "index.html", await snapshot())


@app.get("/status", response_class=HTMLResponse)
async def status(request: Request):
    return render(request, "dashboard.html", await snapshot())


@app.post("/services/{service}/{action}", response_class=HTMLResponse)
async def control(request: Request, service: str, action: str):
    global _cache_until
    if service not in SETTINGS["services"] or action not in {"start", "stop", "switch"}:
        return JSONResponse({"detail": "허용되지 않은 서비스 또는 요청입니다."}, status_code=400)
    try:
        raw = b""
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > 1024:
                return JSONResponse({"detail": "요청이 너무 큽니다."}, status_code=413)
        if request.headers.get("content-type", "").split(";")[0] == "application/x-www-form-urlencoded":
            form = parse_qs(raw.decode(), strict_parsing=True) if raw else {}
            if set(form) - {"confirm"} or form.get("confirm", ["false"]) not in [["true"], ["false"]]:
                raise ValueError("Invalid form")
            payload = {"confirm": form.get("confirm") == ["true"]}
        else:
            payload = json.loads(raw or b"{}")
        if not isinstance(payload, dict) or set(payload) - {"confirm"} or type(payload.get("confirm", False)) is not bool:
            raise ValueError("Invalid confirmation")
        if action in {"start", "switch"}:
            data = await snapshot()
            state = data["services"].get(service, {})
            if state.get("state") in {"stopped", "error", "unknown"} and not state.get("can_start", False):
                return render(request, "dashboard.html", data,
                              message=state.get("start_reason") or "현재 서비스를 시작할 수 없습니다.")
        result = await controller_request({"action": action, "service": service, "confirm": payload.get("confirm", False)})
        message = result.get("message", "요청을 처리할 수 없습니다.")
    except ValueError:
        return JSONResponse({"detail": "올바르지 않은 요청입니다."}, status_code=400)
    except (OSError, TimeoutError):
        message = "Controller에 연결할 수 없습니다. 실제 서비스 상태를 확인한 뒤 다시 시도해 주세요."
    _cache_until = 0
    return render(request, "dashboard.html", await snapshot(), message=message)
