"""Track requests to the private llama.cpp router and coordinate safe shutdown."""

import asyncio
from contextlib import suppress
import json
import math
import os
from pathlib import Path
import signal
import sys

from aiohttp import ClientError, ClientSession, ClientTimeout, web
from yarl import URL

ROUTER = "http://127.0.0.1:8082"
SOCKET = Path("/run/gnuvil-llamacpp/lifecycle.sock")
METRICS = {"llamacpp:requests_processing", "llamacpp:requests_deferred"}
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade"}


def forwarded_headers(headers):
    tokens = {part.strip().lower() for value in headers.getall("Connection", ()) for part in value.split(",")}
    return [(key, value) for key, value in headers.items()
            if key.lower() not in HOP_HEADERS | tokens | {"host"}]


def worker_idle(metrics):
    counts = {}
    for line in metrics.splitlines():
        parts = line.split()
        if parts and parts[0] in METRICS:
            if len(parts) != 2 or parts[0] in counts:
                raise ValueError("Invalid router activity metrics")
            count = float(parts[1])
            if not math.isfinite(count) or count < 0 or not count.is_integer():
                raise ValueError("Invalid router activity count")
            counts[parts[0]] = count
    if counts.keys() != METRICS:
        raise ValueError("Missing router activity metrics")
    return not any(counts.values())


class Lifecycle:
    def __init__(self, session, router=ROUTER):
        self.session = session
        self.router = router
        self.active = 0
        self.drained = False
        self.control_lock = asyncio.Lock()

    async def backend_busy(self):
        # A completed or disconnected HTTP request does not prove its worker is idle.
        async with asyncio.timeout(2):
            async with self.session.get(self.router + "/v1/models", allow_redirects=False) as response:
                if response.status != 200:
                    raise ValueError("Model status unavailable")
                models = (await response.json())["data"]
            if not isinstance(models, list):
                raise ValueError("Invalid model list")
            for model in models:
                state = model["status"]["value"]
                if state in {"unloaded", "sleeping"}:
                    continue
                if state == "loading":
                    return True
                if state != "loaded" or not isinstance(model["id"], str) or not model["id"]:
                    raise ValueError("Invalid model state")
                params = {"model": model["id"], "autoload": "false"}
                async with self.session.get(self.router + "/metrics", params=params, allow_redirects=False) as response:
                    if response.status != 200 or not worker_idle(await response.text()):
                        return True
            return False

    async def state(self, *, drain=False):
        async with self.control_lock:
            if self.active:
                return {"ready": True, "busy": True, "drained": self.drained}
            previous = self.drained
            if drain:
                # Admission and the counter run on this event loop. Close the gate
                # before awaiting the worker check so new requests cannot race it.
                self.drained = True
            try:
                busy = await self.backend_busy()
                ready = True
            except (ClientError, TimeoutError, KeyError, TypeError, ValueError):
                busy, ready = True, False
            if drain and busy:
                self.drained = previous
            return {"ready": ready, "busy": busy or self.active > 0, "drained": self.drained}

    async def control(self, request):
        if request.path == "/resume":
            async with self.control_lock:
                self.drained = False
        if request.path == "/drain":
            # This service does not advertise a continuous idle-time counter.
            # Refuse automatic drain requests that specify an idle threshold.
            try:
                body = await request.json()
            except ValueError as error:
                raise web.HTTPBadRequest() from error
            if body != {}:
                raise web.HTTPBadRequest()
        return web.json_response(await self.state(drain=request.path == "/drain"))

    async def forward(self, request):
        if request.method in {"GET", "HEAD"} and request.path in {"/health", "/v1/health"}:
            return await self.relay(request)
        if self.drained:
            return web.json_response({"error": {"message": "llama.cpp is shutting down"}}, status=503)
        self.active += 1
        try:
            return await self.relay(request)
        finally:
            self.active -= 1

    async def relay(self, request):
        target = URL(self.router + request.rel_url.raw_path_qs, encoded=True)
        try:
            upstream = await self.session.request(
                request.method, target, headers=forwarded_headers(request.headers),
                data=request.content.iter_chunked(65536) if request.can_read_body else None,
                allow_redirects=False,
            )
        except (ClientError, TimeoutError):
            return web.json_response({"error": {"message": "llama.cpp is unavailable"}}, status=502)
        async with upstream:
            response = web.StreamResponse(status=upstream.status, headers=forwarded_headers(upstream.headers))
            connected = True
            try:
                await response.prepare(request)
            except ConnectionError:
                connected = False
            async for chunk in upstream.content.iter_any():
                if connected:
                    try:
                        await response.write(chunk)
                    except ConnectionError:
                        # Keep reading the model output after a client disconnect.
                        connected = False
            if connected:
                with suppress(ConnectionError):
                    await response.write_eof()
            return response


async def main(args):
    os.umask(0o077)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    child = await asyncio.create_subprocess_exec(
        "/opt/llama.cpp/build/bin/llama-server", *args, "--host", "127.0.0.1", "--port", "8082", "--metrics",
    )
    child_exit = asyncio.create_task(child.wait())
    stop_request = asyncio.create_task(stop.wait())
    timeout = ClientTimeout(total=None, sock_connect=3, sock_read=None)
    try:
        async with ClientSession(timeout=timeout, auto_decompress=False, skip_auto_headers={"Accept-Encoding"}) as session:
            lifecycle = Lifecycle(session)
            public = web.Application()
            public.router.add_route("*", "/{path:.*}", lifecycle.forward)
            control = web.Application(client_max_size=1024)
            control.add_routes([web.get("/state", lifecycle.control), web.post("/drain", lifecycle.control),
                                web.post("/resume", lifecycle.control)])
            # Preserve compressed bodies and keep handling requests after client disconnect.
            public_runner = web.AppRunner(public, access_log=None, auto_decompress=False)
            control_runner = web.AppRunner(control, access_log=None)
            try:
                await public_runner.setup()
                await control_runner.setup()
                await web.TCPSite(public_runner, "0.0.0.0", 8080).start()
                if SOCKET.is_socket():
                    SOCKET.unlink()
                await web.UnixSite(control_runner, str(SOCKET)).start()
                SOCKET.chmod(0o600)
                await asyncio.wait((child_exit, stop_request), return_when=asyncio.FIRST_COMPLETED)
                lifecycle.drained = True
                if child.returncode is None:
                    with suppress(ProcessLookupError):
                        child.terminate()
                return await child_exit
            finally:
                await control_runner.cleanup()
                await public_runner.cleanup()
                if SOCKET.is_socket():
                    SOCKET.unlink()
    finally:
        stop_request.cancel()
        if child.returncode is None:
            with suppress(ProcessLookupError):
                child.terminate()
            await child_exit


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
