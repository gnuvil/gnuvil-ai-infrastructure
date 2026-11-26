"""Health and cooperative shutdown protocol shared by CLI and controller.

A probe must atomically block new work before acknowledging POST /drain.
No probe means work state is unknown and web shutdown is refused.
"""

import http.client
import json
import socket
import urllib.request


class ControlRefused(RuntimeError):
    """A safety restriction, rather than a service execution failure."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Lifecycle probe redirects are forbidden")


PROBE_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=3)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def probe(spec, action="state", *, min_idle_seconds=None):
    if not (spec.get("probe_url") or spec.get("probe_socket")):
        raise ControlRefused("작업 상태와 안전한 종료 방법이 등록되지 않았습니다. 운영자에게 문의하세요.")
    request_body = {} if min_idle_seconds is None else {"min_idle_seconds": min_idle_seconds}
    body = None if action == "state" else json.dumps(request_body).encode()
    method = "GET" if action == "state" else "POST"
    headers = {"Content-Type": "application/json"}
    if spec.get("probe_socket"):
        connection = UnixHTTPConnection(spec["probe_socket"])
        try:
            connection.request(method, f"/{action}", body, headers)
            with connection.getresponse() as response:
                if response.status != 200:
                    raise ControlRefused("서비스의 작업 상태를 확인할 수 없습니다.")
                payload = response.read(4097)
        except (OSError, http.client.HTTPException) as error:
            raise ControlRefused("서비스의 작업 상태를 확인할 수 없습니다.") from error
        finally:
            connection.close()
    else:
        request = urllib.request.Request(f"{spec['probe_url']}/{action}", body, headers, method=method)
        with PROBE_HTTP.open(request, timeout=3) as response:
            payload = response.read(4097)
    if len(payload) > 4096:
        raise RuntimeError("Lifecycle probe response is too large")
    state = json.loads(payload)
    if not isinstance(state, dict) or any(type(state.get(k)) is not bool for k in ("ready", "busy", "drained")):
        raise RuntimeError("Lifecycle probe returned an invalid state")
    return state


def idle_for(state, seconds):
    idle_seconds = state.get("idle_seconds")
    return type(idle_seconds) is int and idle_seconds >= seconds


def drain_services(specs, *, min_idle_seconds=None):
    """All-or-nothing preparation. Resume even a failed probe: its POST may have arrived."""
    attempted = []
    try:
        for spec in specs:
            attempted.append(spec)
            if min_idle_seconds is None:
                state = probe(spec, "drain")
            else:
                state = probe(spec, "drain", min_idle_seconds=min_idle_seconds)
            if state["busy"] or not state["drained"] or not state["ready"]:
                raise ControlRefused("진행 중인 작업이 있거나 신규 작업 차단을 확인할 수 없어 종료하지 않았습니다.")
            if min_idle_seconds is not None and not idle_for(state, min_idle_seconds):
                raise ControlRefused("유휴 시간이 기준에 도달했는지 확인할 수 없어 종료하지 않았습니다.")
    except Exception as error:
        for spec in attempted:
            try:
                probe(spec, "resume")
            except Exception:
                pass  # Remain drained on uncertainty; never force a restart/termination.
        if min_idle_seconds is not None and not isinstance(error, ControlRefused):
            raise ControlRefused("유휴 상태를 다시 확인할 수 없어 자동 종료하지 않았습니다.") from error
        raise


def http_ready(spec):
    if not spec.get("ready_url"):
        return False
    try:
        with PROBE_HTTP.open(spec["ready_url"], timeout=3) as response:
            return response.status == 200
    except Exception:
        return False


def container_state(run, docker, project, spec, container_ids):
    containers = []
    for target in spec["targets"]:
        ids = container_ids(project, target, running=False)
        if not ids:
            containers.append({"Status": "absent"})
        else:
            for container in ids:
                rows = json.loads(run((*docker, "inspect", "--format", "{{json .State}}", container)))
                containers.append(rows)
    if all(c["Status"] in {"absent", "exited", "dead", "created"} for c in containers):
        if any(c.get("ExitCode", 0) != 0 or c["Status"] == "dead" for c in containers):
            return {"state": "error", "reason": "컨테이너가 비정상 종료되었습니다. 운영자에게 문의하세요.", "can_stop": False}
        return {"state": "stopped", "reason": "", "can_stop": False}
    if spec["mode"] == "job":
        return {"state": "busy", "reason": "일회성 작업이 실행 중입니다. 완료될 때까지 기다려 주세요.", "can_stop": False}
    if any(c.get("Health", {}).get("Status") == "unhealthy" for c in containers):
        return {"state": "error", "reason": "서비스 Health Check에 실패했습니다.", "can_stop": False}
    running = all(c["Status"] == "running" for c in containers)
    healthy = all(c.get("Health", {}).get("Status") == "healthy" for c in containers)
    if not running or not (healthy or http_ready(spec)):
        return {"state": "starting", "reason": "모든 컨테이너의 Health Check 통과를 기다리고 있습니다.", "can_stop": False}
    try:
        work = probe(spec)
    except Exception:
        return {"state": "ready", "reason": "접속은 가능하지만 작업 상태를 확인할 수 없어 웹 종료·전환이 제한됩니다.", "can_stop": False}
    if not work["ready"]:
        return {"state": "starting", "reason": "서비스 준비를 기다리고 있습니다.", "can_stop": False}
    state = {"state": "busy" if work["busy"] else "ready",
             "reason": "진행 중인 작업이 있습니다." if work["busy"] else ("신규 작업이 차단되어 있습니다. 운영자 확인이 필요합니다." if work["drained"] else ""),
             "can_stop": not work["busy"]}
    if not work["busy"] and not work["drained"] and idle_for(work, 0):
        state["idle_seconds"] = work["idle_seconds"]
    return state
