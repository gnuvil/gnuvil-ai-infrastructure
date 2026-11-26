"""Host-only lifecycle control for the pinned ComfyUI PromptQueue."""

import json
from pathlib import Path
import time

SOCKET_PATH = Path("/run/gnuvil-comfyui/lifecycle.sock")


class WorkAdmissionClosed(RuntimeError):
    pass


class QueueLifecycle:
    def __init__(self, server):
        self.queue = server.prompt_queue
        self.enqueue = self.queue.put
        self.notify_queue_updated = server.queue_updated
        self.drained = False
        self.idle_since = None

    def state(self, *, action="state", minimum_idle=0):
        with self.queue.mutex:
            busy = self.queue.get_tasks_remaining() > 0
            now = time.monotonic()
            if busy:
                self.idle_since = None
            elif self.idle_since is None:
                self.idle_since = now
            idle_seconds = 0 if busy else int(now - self.idle_since)
            if action == "resume":
                self.drained = False
            elif action == "drain" and not busy and idle_seconds >= minimum_idle:
                self.drained = True
            return {"ready": True, "busy": busy, "drained": self.drained, "idle_seconds": idle_seconds}

    def put(self, item):
        # The same RLock protects both job admission and the drain decision.
        with self.queue.mutex:
            if self.drained:
                raise WorkAdmissionClosed("ComfyUI is shutting down. Submit again after it starts.")
            return self.enqueue(item)

    def queue_updated(self):
        self.state()
        self.notify_queue_updated()


def install(server, socket_path=SOCKET_PATH):
    from aiohttp import web

    lifecycle = QueueLifecycle(server)

    def put(item):
        try:
            return lifecycle.put(item)
        except WorkAdmissionClosed as error:
            raise web.HTTPServiceUnavailable(
                text=json.dumps({"error": {"type": "service_stopping", "message": str(error)}}),
                content_type="application/json",
            ) from error

    async def state(_request):
        return web.json_response(lifecycle.state())

    async def drain(request):
        try:
            payload = await request.json()
        except ValueError as error:
            raise web.HTTPBadRequest() from error
        if not isinstance(payload, dict) or payload.keys() - {"min_idle_seconds"}:
            raise web.HTTPBadRequest()
        minimum_idle = payload.get("min_idle_seconds", 0)
        if type(minimum_idle) is not int or minimum_idle < 0:
            raise web.HTTPBadRequest()
        return web.json_response(lifecycle.state(action="drain", minimum_idle=minimum_idle))

    async def resume(_request):
        return web.json_response(lifecycle.state(action="resume"))

    async def control_socket(_app):
        control = web.Application(client_max_size=1024)
        control.add_routes([web.get("/state", state), web.post("/drain", drain), web.post("/resume", resume)])
        runner = web.AppRunner(control, access_log=None)
        await runner.setup()
        if socket_path.is_socket():
            socket_path.unlink()
        site = web.UnixSite(runner, str(socket_path))
        await site.start()
        socket_path.chmod(0o600)
        lifecycle.state()
        try:
            yield
        finally:
            await runner.cleanup()
            socket_path.unlink(missing_ok=True)

    server.app.cleanup_ctx.append(control_socket)
    server.prompt_queue.put = put
    server.queue_updated = lifecycle.queue_updated
