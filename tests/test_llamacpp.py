import asyncio
import sys
import unittest
from pathlib import Path

try:
    from aiohttp import ClientSession, web
    from aiohttp.test_utils import TestClient, TestServer
except ModuleNotFoundError:
    ClientSession = None

if ClientSession is not None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "llamacpp"))
    from lifecycle import Lifecycle
    sys.path.pop(0)


@unittest.skipUnless(ClientSession, "requires the llama.cpp image's aiohttp dependency")
class LlamaLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stream_end = asyncio.Event()
        self.metrics_check = asyncio.Event()
        self.metrics_end = asyncio.Event()
        self.metrics_end.set()
        self.models = [{"id": "model / 1", "status": {"value": "loaded"}}]
        self.processing = 0
        self.deferred = 0

        upstream = web.Application()
        upstream.add_routes([
            web.get("/v1/models", self.model_list),
            web.get("/metrics", self.metrics),
            web.post("/v1/chat/completions", self.stream),
            web.get("/health", self.health),
        ])
        self.upstream = TestServer(upstream)
        self.addAsyncCleanup(self.upstream.close)
        await self.upstream.start_server()
        session = ClientSession()
        self.addAsyncCleanup(session.close)
        self.lifecycle = Lifecycle(session, str(self.upstream.make_url("/")).rstrip("/"))

        public = web.Application()
        public.router.add_route("*", "/{path:.*}", self.lifecycle.forward)
        self.public = await self.start_client(public)
        control = web.Application()
        control.add_routes([web.get("/state", self.lifecycle.control), web.post("/drain", self.lifecycle.control),
                            web.post("/resume", self.lifecycle.control)])
        self.control = await self.start_client(control)
        self.addCleanup(self.stream_end.set)
        self.addCleanup(self.metrics_end.set)

    async def start_client(self, application):
        client = TestClient(TestServer(application))
        self.addAsyncCleanup(client.close)
        await client.start_server()
        return client

    async def model_list(self, request):
        return web.json_response({"data": self.models})

    async def health(self, request):
        return web.json_response({"status": "ok"})

    async def metrics(self, request):
        self.assertEqual(dict(request.query), {"model": "model / 1", "autoload": "false"})
        self.metrics_check.set()
        await self.metrics_end.wait()
        return web.Response(text=f"llamacpp:requests_processing {self.processing}\n"
                                 f"llamacpp:requests_deferred {self.deferred}\n")

    async def stream(self, request):
        self.assertEqual(await request.json(), {"model": "model / 1", "stream": True})
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b"data: first\n\n")
        await self.stream_end.wait()
        await response.write(b"data: [DONE]\n\n")
        return response

    async def drain(self):
        async with self.control.post("/drain", json={}) as response:
            self.assertEqual(response.status, 200)
            return await response.json()

    async def test_stream_and_disconnect_block_drain_until_backend_finishes(self):
        response = await self.public.post("/v1/chat/completions", json={"model": "model / 1", "stream": True})
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.content.readline(), b"data: first\n")
        self.assertEqual(await self.drain(), {"ready": True, "busy": True, "drained": False})
        response.close()
        self.assertEqual(await self.drain(), {"ready": True, "busy": True, "drained": False})
        self.stream_end.set()
        async with asyncio.timeout(1):
            while self.lifecycle.active:
                await asyncio.sleep(0.01)
        self.assertEqual(await self.drain(), {"ready": True, "busy": False, "drained": True})

    async def test_drain_blocks_new_requests_during_check_and_until_resume(self):
        self.metrics_end.clear()
        drain = asyncio.create_task(self.drain())
        async with asyncio.timeout(1):
            await self.metrics_check.wait()
        async with self.public.get("/v1/models") as response:
            self.assertEqual(response.status, 503)
        async with self.public.get("/health") as response:
            self.assertEqual(response.status, 200)
        self.metrics_end.set()
        self.assertTrue((await drain)["drained"])
        async with self.public.get("/v1/models") as response:
            self.assertEqual(response.status, 503)
        async with self.control.post("/resume", json={}) as response:
            self.assertFalse((await response.json())["drained"])
        async with self.public.get("/v1/models") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), {"data": self.models})

    async def test_queued_loading_or_unverifiable_work_prevents_shutdown(self):
        self.deferred = 1
        self.assertEqual(await self.drain(), {"ready": True, "busy": True, "drained": False})
        self.models[0]["status"]["value"] = "loading"
        self.assertEqual(await self.drain(), {"ready": True, "busy": True, "drained": False})
        self.models[0]["status"]["value"] = "unknown"
        self.assertEqual(await self.drain(), {"ready": False, "busy": True, "drained": False})
        async with self.control.post("/drain", json={"min_idle_seconds": 14400}) as response:
            self.assertEqual(response.status, 400)
