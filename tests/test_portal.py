"""Safety regressions: no Docker daemon, GPU, models or host changes required."""

import asyncio
from copy import deepcopy
import importlib.util
import io
import http.client
import socket
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from service_config import load_yaml, portal_spec, service_spec, validate_portal
import service_runtime as runtime


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


gpu = module("gpu_test", ROOT / "scripts/gpu-switch.py")
controller_module = module("controller_test", ROOT / "scripts/service-controller.py")
comfy_lifecycle = module("comfy_lifecycle", ROOT / "services/comfyui/gnuvil_lifecycle/lifecycle.py")
with patch.dict("os.environ", {"PORTAL_PROXY_TOKEN": "a" * 48}):
    web = module("portal_test", ROOT / "services/portal/app.py")
from fastapi.testclient import TestClient


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.portal = load_yaml(ROOT / "configs/portal.yaml")
        self.policy = gpu.read_policy()

    def test_actual_configuration_and_job_shorthand(self):
        validate_portal(self.portal, self.policy)
        self.assertEqual(service_spec("gpu-smoke", "job")["targets"], ["gpu-smoke"])

    def test_duplicate_yaml_keys_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.yaml"
            path.write_text("title: one\ntitle: two\n")
            with self.assertRaises(ValueError):
                load_yaml(path)

    def test_unknown_gpu_mapping_rejected(self):
        self.portal["services"]["gpu-smoke"]["gpu_service"] = "unknown"
        with self.assertRaises(ValueError):
            validate_portal(self.portal, self.policy)

    def test_invalid_identifiers_and_probe_destinations(self):
        for identifier in ("../etc", "--help", "a;id", "$(id)", "a b"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                service_spec(identifier, "service")
        for url in ("http://example.com:80", "file:///etc/passwd", "http://127.0.0.1:80/path", "http://user@127.0.0.1:80"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                service_spec("chat", {"mode": "service", "probe_url": url})

    def test_multicontainer_service_and_cpu_service(self):
        self.policy["services"]["chat"] = {"mode": "service", "companions": ["frontend"], "probe_url": "http://127.0.0.1:9001"}
        self.portal["services"]["chat"] = {"name": "Chat", "description": "Test", "category": "chat", "icon": "message-square", "gpu_service": "chat"}
        self.portal["services"]["cpu"] = {"name": "CPU", "description": "Test", "category": "cpu", "icon": "chip", "cpu_service": {"service": "cpu-ui"}}
        validate_portal(self.portal, self.policy)
        self.assertEqual(portal_spec(self.portal["services"]["chat"], self.policy)["targets"], ["chat", "frontend"])

    def test_two_cards_can_control_one_registered_gpu_group(self):
        self.portal["services"]["second-card"] = dict(self.portal["services"]["comfyui"])
        validate_portal(self.portal, self.policy)

    def test_cpu_cannot_control_gpu_or_portal(self):
        for name in ("gpu-smoke", "portal"):
            self.portal["services"]["cpu"] = {"name": "CPU", "description": "Test", "category": "cpu", "icon": "chip", "cpu_service": {"service": name}}
            with self.assertRaises(ValueError):
                validate_portal(self.portal, self.policy)

    def test_unsafe_public_url_and_wildcard_host_rejected(self):
        self.portal["services"]["gpu-smoke"]["url"] = "javascript:alert(1)"
        with self.assertRaises(ValueError):
            validate_portal(self.portal, self.policy)
        self.portal = load_yaml(ROOT / "configs/portal.yaml")
        self.portal["allowed_hosts"] = ["*"]
        with self.assertRaises(ValueError):
            validate_portal(self.portal)

    def test_compose_gpu_allowlist_mismatch(self):
        with self.assertRaises(ValueError):
            gpu.validate_compose(self.policy, {"services": {}})

    def test_idle_shutdown_requires_a_positive_timeout_and_lifecycle_probe(self):
        for value in (0, -1, True, "14400", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                service_spec("chat", {"mode": "service", "probe_url": "http://127.0.0.1:9001",
                                      "idle_shutdown_seconds": value})
        for mode in ("job", "service"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                service_spec("chat", {"mode": mode, "idle_shutdown_seconds": 14400})


class ComfyLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.server = Mock()
        self.queue = self.server.prompt_queue
        self.queue.mutex = threading.RLock()
        self.queue.get_tasks_remaining.return_value = 0
        self.lifecycle = comfy_lifecycle.QueueLifecycle(self.server)

    def test_pending_or_running_work_prevents_drain_and_submission_remains_open(self):
        self.queue.get_tasks_remaining.return_value = 1
        state = self.lifecycle.state(action="drain")
        self.assertTrue(state["busy"])
        self.assertFalse(state["drained"])
        self.lifecycle.put("next")
        self.queue.put.assert_called_once_with("next")

    def test_empty_queue_drain_blocks_submissions_until_resumed(self):
        state = self.lifecycle.state(action="drain")
        self.assertTrue(state["drained"])
        with self.assertRaises(comfy_lifecycle.WorkAdmissionClosed):
            self.lifecycle.put("must not be accepted")
        self.queue.put.assert_not_called()
        self.lifecycle.state(action="resume")
        self.lifecycle.put("accepted again")
        self.queue.put.assert_called_once_with("accepted again")

    def test_short_job_between_samples_resets_idle_time(self):
        with patch.object(comfy_lifecycle.time, "monotonic", side_effect=[0, 500, 501, 502]):
            self.lifecycle.state()
            self.queue.get_tasks_remaining.return_value = 1
            self.lifecycle.queue_updated()
            self.queue.get_tasks_remaining.return_value = 0
            self.lifecycle.queue_updated()
            state = self.lifecycle.state(action="drain", minimum_idle=400)
        self.assertEqual(state["idle_seconds"], 1)
        self.assertFalse(state["drained"])


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.spec = service_spec("chat", {"mode": "service", "probe_url": "http://127.0.0.1:9001"})

    def test_busy_or_undrained_probe_never_authorizes_shutdown(self):
        for state in ({"ready": True, "busy": True, "drained": True}, {"ready": True, "busy": False, "drained": False}):
            with patch.object(runtime, "probe", return_value=state) as probe:
                with self.assertRaises(RuntimeError):
                    runtime.drain_services([self.spec])
                self.assertEqual(probe.call_args_list[-1].args[1], "resume")

    def test_probe_failure_resumes_every_attempted_service(self):
        with patch.object(runtime, "probe", side_effect=[{"ready": True, "busy": False, "drained": True}, TimeoutError(), {}, {}]) as probe:
            with self.assertRaises(TimeoutError):
                runtime.drain_services([self.spec, self.spec])
            self.assertEqual([call.args[1] for call in probe.call_args_list], ["drain", "drain", "resume", "resume"])

    def test_idle_shutdown_rechecks_activity_after_draining(self):
        for idle_seconds in (None, True, 0, 14399, 14400):
            work = {"ready": True, "busy": False, "drained": True, "idle_seconds": idle_seconds}
            with self.subTest(idle_seconds=idle_seconds), patch.object(runtime, "probe", return_value=work) as probe:
                if idle_seconds == 14400:
                    runtime.drain_services([self.spec], min_idle_seconds=14400)
                    probe.assert_called_once_with(self.spec, "drain", min_idle_seconds=14400)
                else:
                    with self.assertRaises(runtime.ControlRefused):
                        runtime.drain_services([self.spec], min_idle_seconds=14400)
                    probe.assert_called_with(self.spec, "resume")

    def test_missing_probe_denies_safe_shutdown(self):
        with self.assertRaises(RuntimeError):
            runtime.drain_services([service_spec("chat", "service")])

    def test_healthy_container_is_not_enough_to_allow_stop(self):
        run = Mock(return_value=json.dumps({"Status": "running", "Health": {"Status": "healthy"}}))
        with patch.object(runtime, "probe", side_effect=TimeoutError()):
            state = runtime.container_state(run, ("docker",), "test", self.spec, Mock(return_value=["id"]))
        self.assertEqual(state["state"], "ready")
        self.assertFalse(state["can_stop"])

    def test_running_without_health_is_starting(self):
        state = runtime.container_state(Mock(return_value=json.dumps({"Status": "running"})), ("docker",),
                                        "test", self.spec, Mock(return_value=["id"]))
        self.assertEqual(state["state"], "starting")

    def test_existing_container_without_health_can_use_registered_ready_endpoint(self):
        self.spec["ready_url"] = "http://127.0.0.1:8188/system_stats"
        with patch.object(runtime, "http_ready", return_value=True), patch.object(runtime, "probe", side_effect=RuntimeError("no drain")):
            state = runtime.container_state(Mock(return_value=json.dumps({"Status": "running"})), ("docker",),
                                            "test", self.spec, Mock(return_value=["id"]))
        self.assertEqual(state["state"], "ready")
        self.assertFalse(state["can_stop"])

    def test_unhealthy_and_nonzero_exit(self):
        for container in ({"Status": "running", "Health": {"Status": "unhealthy"}}, {"Status": "exited", "ExitCode": 1}):
            state = runtime.container_state(Mock(return_value=json.dumps(container)), ("docker",), "test", self.spec, Mock(return_value=["id"]))
            self.assertEqual(state["state"], "error")

    def test_busy_probe_is_busy_and_not_stoppable(self):
        with patch.object(runtime, "probe", return_value={"ready": True, "busy": True, "drained": False}):
            state = runtime.container_state(Mock(return_value=json.dumps({"Status": "running", "Health": {"Status": "healthy"}})),
                                            ("docker",), "test", self.spec, Mock(return_value=["id"]))
        self.assertEqual(state["state"], "busy")
        self.assertFalse(state["can_stop"])

    def test_probe_schema_redirect_and_size_are_restricted(self):
        with self.assertRaises(RuntimeError):
            runtime.NoRedirect().redirect_request(None, None, 302, "", {}, "http://example.com")
        for payload in (b'{"ready":1,"busy":false,"drained":true}', b"x" * 4097):
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = payload
            with patch.object(runtime.PROBE_HTTP, "open", return_value=response), self.assertRaises(RuntimeError):
                runtime.probe(self.spec)


class GPUTransitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.lock = patch.object(gpu, "LOCK_PATH", Path(self.temp.name) / "gpu.lock")
        self.lock.start()
        self.addCleanup(self.lock.stop)
        self.policy = {"services": {"chat": {"mode": "service", "probe_url": "http://127.0.0.1:9001"}, "image": "service"},
                       "stop_timeout_seconds": 1, "idle_timeout_seconds": 1, "idle_memory_mib": 64}
        self.config = {"services": {"chat": {"image": "chat:1"}, "image": {"image": "image:1"}}}

    def test_start_from_idle_uses_same_transition(self):
        with patch.object(gpu, "active_containers", return_value=[]), patch.object(gpu, "check_images"), \
             patch.object(gpu, "gpu_idle", return_value=True), patch.object(gpu, "start_service") as start:
            gpu.transition("test", "chat", self.policy, self.config, safe=True)
        start.assert_called_once()

    def test_duplicate_start_does_not_restart(self):
        with patch.object(gpu, "active_containers", side_effect=lambda project, targets: ["id"] if "chat" in targets else []), \
             patch.object(gpu, "container_ids", return_value=["id"]), patch.object(gpu, "start_service") as start:
            gpu.transition("test", "chat", self.policy, self.config, safe=True)
        start.assert_not_called()

    def test_switch_requires_confirmation_under_lock(self):
        with patch.object(gpu, "active_containers", side_effect=lambda project, targets: ["id"] if "image" in targets else []), \
             patch.object(gpu, "stop_managed_services") as stop:
            with self.assertRaises(RuntimeError):
                gpu.transition("test", "chat", self.policy, self.config, safe=True)
        stop.assert_not_called()

    def test_vram_failure_prevents_next_start(self):
        with patch.object(gpu, "active_containers", return_value=[]), patch.object(gpu, "check_images"), \
             patch.object(gpu, "wait_until", side_effect=RuntimeError("VRAM")), patch.object(gpu, "start_service") as start:
            with self.assertRaises(RuntimeError):
                gpu.transition("test", "chat", self.policy, self.config, safe=True)
        start.assert_not_called()

    def test_busy_and_unknown_stop_never_signal(self):
        with patch.object(gpu, "active_containers", return_value=["id"]), \
             patch.object(gpu, "container_ids", return_value=["id"]), \
             patch.object(gpu, "drain_services", side_effect=RuntimeError("busy")), patch.object(gpu, "stop_targets") as stop:
            with self.assertRaises(RuntimeError):
                gpu.stop_managed_services("test", self.policy, safe=True)
        stop.assert_not_called()

    def test_web_never_stops_running_job(self):
        self.policy["services"] = {"gpu-smoke": "job"}
        with patch.object(gpu, "active_containers", return_value=["id"]), patch.object(gpu, "stop_targets") as stop:
            with self.assertRaises(RuntimeError):
                gpu.stop_managed_services("test", self.policy, safe=True)
        stop.assert_not_called()

    def test_safe_stop_drains_before_sigterm_and_idle_check(self):
        calls = []
        with patch.object(gpu, "active_containers", return_value=["id"]), \
             patch.object(gpu, "container_ids", return_value=["id"]), \
             patch.object(gpu, "drain_services", side_effect=lambda specs: calls.append("drain")), \
             patch.object(gpu, "stop_targets", side_effect=lambda *args: calls.append("stop")), \
             patch.object(gpu, "gpu_idle", side_effect=lambda *args: calls.append("idle") or True):
            gpu.stop_managed_services("test", self.policy, safe=True)
        self.assertEqual(calls, ["drain", "stop", "idle"])

    def test_oneoff_compose_container_cannot_be_stopped_by_a_service_probe(self):
        with patch.object(gpu, "container_ids", side_effect=lambda *args, running: ["oneoff"] if running else []), \
             patch.object(gpu, "stop_targets") as stop, patch.object(gpu, "drain_services") as drain:
            with self.assertRaises(RuntimeError):
                gpu.safe_stop("test", [service_spec("chat", "service")], self.policy, require_gpu_idle=True)
        stop.assert_not_called()
        drain.assert_not_called()

    def test_cli_confirmation_behavior_preserved(self):
        with patch.object(gpu, "active_containers", return_value=["id"]), patch.object(gpu, "stop_targets") as stop, \
             patch.object(gpu, "gpu_idle", return_value=True):
            with self.assertRaises(RuntimeError):
                gpu.stop_managed_services("test", self.policy)
            gpu.stop_managed_services("test", self.policy, confirm_stop=True)
        stop.assert_called_once()

    def test_comfyui_sigint_is_preserved_without_forced_kill(self):
        spec = service_spec("comfyui", "service")
        config = {"services": {"comfyui": {"stop_signal": "SIGINT"}}}
        with patch.object(gpu, "active_containers", side_effect=[["id"], []]), \
             patch.object(gpu, "container_ids", return_value=["id"]), patch.object(gpu, "run") as run:
            gpu.stop_targets("test", [spec], self.policy, config)
        self.assertEqual(run.call_args.args[0], (*gpu.DOCKER, "kill", "--signal=SIGINT", "id"))

    def test_external_gpu_process_or_vram_blocks_idle(self):
        for output in (["123", "0"], ["", "100"]):
            with patch.object(gpu, "run", side_effect=output):
                self.assertFalse(gpu.gpu_idle(64))

    def test_health_failure_and_start_failure_propagate(self):
        with patch.object(gpu, "run"), patch.object(gpu, "container_state", return_value={"state": "error", "reason": "health failed"}):
            with self.assertRaises(RuntimeError):
                gpu.start_service("test", service_spec("chat", "service"), self.policy)
        with patch.object(gpu, "run", side_effect=subprocess.CalledProcessError(1, ["docker"])):
            with self.assertRaises(subprocess.CalledProcessError):
                gpu.start_service("test", service_spec("chat", "service"), self.policy)

    def test_concurrent_cli_and_web_share_nonblocking_lock(self):
        entered, release = threading.Event(), threading.Event()
        def hold(*args):
            entered.set()
            release.wait(2)
        with patch.object(gpu, "active_containers", return_value=[]), patch.object(gpu, "check_images"), \
             patch.object(gpu, "gpu_idle", return_value=True), patch.object(gpu, "start_service", side_effect=hold):
            thread = threading.Thread(target=gpu.transition, args=("test", "chat", self.policy, self.config))
            thread.start()
            try:
                self.assertTrue(entered.wait(1))
                with self.assertRaises(RuntimeError):
                    gpu.transition("test", "image", self.policy, self.config, safe=True)
            finally:
                release.set()
                thread.join()


class ControllerTests(unittest.TestCase):
    def setUp(self):
        policy = gpu.read_policy()
        portal = load_yaml(ROOT / "configs/portal.yaml")
        targets = {target: {} for name, value in policy["services"].items()
                   for target in service_spec(name, value)["targets"]}
        with patch.object(controller_module.gpu, "read_policy", return_value=policy), \
             patch.object(controller_module.gpu, "compose_config", return_value={"name": "test", "services": targets}), \
             patch.object(controller_module.gpu, "validate_compose"):
            self.controller = controller_module.Controller(start_sampler=False)
        self.addCleanup(self.controller.worker.shutdown)

    def test_unknown_service_arbitrary_action_and_bad_confirmation_rejected(self):
        for key, action, confirm in (("../../etc/passwd", "start", False), ("gpu-smoke", "exec", False),
                                     ("gpu-smoke", "idle-stop", False), ("gpu-smoke", "start", "yes")):
            with self.assertRaises(ValueError):
                self.controller.submit(key, action, confirm)

    def test_idle_shutdown_schedules_only_an_opted_in_ready_service_at_threshold(self):
        self.assertNotIn("gpu-smoke", self.controller.idle_shutdowns)
        self.controller.idle_shutdowns = {"comfyui": 14400}
        ready = {"state": "ready", "can_stop": True, "idle_seconds": 14400}
        states = {"comfyui": ready, "gpu-smoke": ready}
        with patch.object(self.controller, "queue_operation", return_value={"ok": True}) as queue:
            for state in ({**ready, "state": "busy"}, {**ready, "can_stop": False},
                          {**ready, "idle_seconds": 14399}, {**ready, "idle_seconds": None}):
                self.controller.schedule_idle_shutdown({**states, "comfyui": state})
            queue.assert_not_called()
            self.controller.schedule_idle_shutdown(states)
            self.controller.schedule_idle_shutdown(states)
            queue.assert_called_once_with("comfyui", controller_module.IDLE_STOP, False)

    def test_automatic_shutdown_uses_the_shared_stop_path_with_the_idle_threshold(self):
        self.controller.idle_shutdowns = {"comfyui": 14400}
        with patch.object(controller_module.gpu, "control_registered_service", return_value=True) as stop:
            self.controller.operation_lock.acquire()
            self.controller.execute("comfyui", controller_module.IDLE_STOP, False)
        self.assertEqual(stop.call_args.args[-1], "stop")
        self.assertEqual(stop.call_args.kwargs, {"gpu_service": True, "min_idle_seconds": 14400})

    def test_long_operation_does_not_block_snapshot_or_queue_more_work(self):
        entered, release = threading.Event(), threading.Event()
        def work(*args, **kwargs):
            entered.set()
            release.wait(2)
        with patch.object(controller_module.gpu, "transition", side_effect=work):
            try:
                self.assertTrue(self.controller.submit("gpu-smoke", "start", False)["ok"])
                self.assertTrue(entered.wait(1))
                self.assertTrue(self.controller.submit("gpu-smoke", "start", False)["ok"])
                self.assertFalse(self.controller.submit("gpu-smoke", "stop", False)["ok"])
                self.assertEqual(self.controller.snapshot()["services"]["gpu-smoke"]["state"], "starting")
            finally:
                release.set()
                self.controller.worker.shutdown()

    def test_running_job_is_busy_while_worker_waits_for_exit(self):
        with self.controller.state_lock:
            self.controller.operation = ("gpu-smoke", "start")
        self.controller.cache = {"services": {key: {"state": "busy", "reason": "job", "can_stop": False} for key in self.controller.portal["services"]}, "metrics": {}}
        self.controller.cache_time = time.monotonic()
        self.assertEqual(self.controller.snapshot()["services"]["gpu-smoke"]["state"], "busy")

    def test_stale_stop_does_not_stop_other_service(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(controller_module.gpu, "LOCK_PATH", Path(directory) / "lock"), \
             patch.object(controller_module.gpu, "active_containers", return_value=[]), \
             patch.object(controller_module.gpu, "stop_targets") as stop:
            self.controller.operation_lock.acquire()
            self.controller.execute("gpu-smoke", "stop", False)
        stop.assert_not_called()

    def test_shared_gpu_cards_are_sampled_once_and_show_the_same_operation(self):
        self.controller.portal["services"]["second-card"] = dict(self.controller.portal["services"]["comfyui"])
        with patch.object(controller_module, "container_state", return_value={"state": "ready", "reason": "", "can_stop": True}) as inspect, \
             patch.object(controller_module, "system_metrics", return_value=({}, None)), \
             patch.object(controller_module.gpu, "active_containers", return_value=[]):
            self.controller.refresh()
        targets = [tuple(call.args[3]["targets"]) for call in inspect.call_args_list]
        self.assertEqual(len(targets), len(set(targets)))
        self.controller.operation = ("second-card", "stop")
        states = self.controller.snapshot()["services"]
        for key in ("second-card", "comfyui"):
            self.assertEqual(states[key]["state"], "stopping")
            self.assertFalse(states[key]["can_stop"])

    def test_sample_time_is_stable_until_new_collection_and_retained_when_stale(self):
        with patch.object(controller_module, "container_state", return_value={"state": "stopped", "reason": "", "can_stop": False}), \
             patch.object(controller_module, "system_metrics", return_value=({"cpu_percent": 42}, None)), \
             patch.object(controller_module.gpu, "active_containers", return_value=[]):
            self.controller.refresh()
        first = self.controller.snapshot()
        self.assertEqual(first["collected_at"], self.controller.snapshot()["collected_at"])
        self.assertFalse(first["stale"])
        self.controller.cache_time -= 20
        stale = self.controller.snapshot()
        self.assertEqual(first["collected_at"], stale["collected_at"])
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["metrics"]["cpu_percent"], 42)
        self.assertEqual(stale["services"]["gpu-smoke"]["state"], "unknown")

    def test_unsafe_switch_is_disabled_from_actual_gpu_state(self):
        self.controller.cache = {"services": {
            "gpu-smoke": {"state": "stopped", "reason": "", "can_stop": False},
            "comfyui": {"state": "ready", "reason": "", "can_stop": False},
        }, "metrics": {}, "active_gpu": ["comfyui"]}
        self.controller.cache_time = time.monotonic()
        state = self.controller.snapshot()["services"]["gpu-smoke"]
        self.assertFalse(state["can_start"])
        self.assertIn("작업 상태", state["start_reason"])

    def test_safety_refusal_does_not_change_container_state_to_error(self):
        self.controller.cache = {"services": {key: {"state": "stopped", "reason": "", "can_stop": False}
                                              for key in self.controller.portal["services"]}, "metrics": {}, "active_gpu": []}
        self.controller.cache_time = time.monotonic()
        with patch.object(controller_module.gpu, "transition", side_effect=runtime.ControlRefused("안전한 종료 확인 불가")):
            self.controller.operation_lock.acquire()
            self.controller.execute("gpu-smoke", "start", True)
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot["services"]["gpu-smoke"]["state"], "stopped")
        self.assertEqual(snapshot["notice"], "안전한 종료 확인 불가")

    def test_restart_reconstructs_container_state(self):
        with patch.object(controller_module, "container_state", return_value={"state": "busy", "reason": "job", "can_stop": False}), \
             patch.object(controller_module, "system_metrics", return_value=({}, None)), \
             patch.object(controller_module.gpu, "active_containers", return_value=[]):
            self.controller.refresh()
        self.assertEqual(self.controller.snapshot()["services"]["gpu-smoke"]["state"], "busy")

    def test_failed_command_does_not_leak_stderr(self):
        with patch.object(controller_module.gpu, "transition", side_effect=subprocess.CalledProcessError(1, "docker", stderr="SECRET_TOKEN")):
            self.controller.operation_lock.acquire()
            self.controller.execute("gpu-smoke", "start", False)
        snapshot = self.controller.snapshot()
        self.assertNotIn("SECRET_TOKEN", json.dumps(snapshot))
        self.assertEqual(snapshot["services"]["gpu-smoke"]["state"], "error")

    def test_unix_protocol_rejects_extra_fields_and_non_newline(self):
        for payload in (b'{"action":"snapshot","command":"id"}\n', b'{"action":"snapshot"}', b'x' * 4097):
            handler = object.__new__(controller_module.Handler)
            handler.request = Mock()
            handler.rfile, handler.wfile = io.BytesIO(payload), io.BytesIO()
            handler.server = Mock(controller=self.controller)
            handler.handle()
            self.assertFalse(json.loads(handler.wfile.getvalue())["ok"])


class UnixTransportTests(unittest.TestCase):
    def test_web_client_and_host_server_round_trip_without_docker(self):
        with tempfile.TemporaryDirectory(prefix="gnuvil-", dir="/private/tmp") as directory:
            path = str(Path(directory) / "controller.sock")
            controller = Mock()
            controller.snapshot.return_value = {"services": {}, "metrics": {}}
            controller.submit.return_value = {"ok": True, "message": "accepted"}
            try:
                server = controller_module.Server(path, controller)
            except PermissionError:
                self.skipTest("Sandbox denies local Unix socket binding; rerun with local test permissions")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(web, "SOCKET_PATH", path):
                    response = asyncio.run(web.controller_request({"action": "snapshot"}))
                    self.assertEqual(response, {"ok": True, "data": {"services": {}, "metrics": {}}})
                    response = asyncio.run(web.controller_request({"action": "start", "service": "gpu-smoke", "confirm": False}))
                    self.assertTrue(response["ok"])
                controller.submit.assert_called_once_with("gpu-smoke", "start", False)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


class StaticStreamingTests(unittest.TestCase):
    def test_actual_http_server_sends_complete_logo_with_security_headers(self):
        import uvicorn
        listener = socket.socket()
        try:
            listener.bind(("127.0.0.1", 0))
        except PermissionError:
            listener.close()
            self.skipTest("Sandbox denies loopback HTTP binding")
        listener.listen(16)
        server = uvicorn.Server(uvicorn.Config(web.app, host="127.0.0.1", log_level="critical", lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(server.started)
            expected = (ROOT / "services/portal/static/gnuvil-logo.png").read_bytes()
            for _ in range(2):
                connection = http.client.HTTPConnection("127.0.0.1", listener.getsockname()[1], timeout=5)
                try:
                    connection.request("GET", "/static/gnuvil-logo.png", headers={"X-Portal-Token": "a" * 48})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), expected)
                    self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
                finally:
                    connection.close()
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            listener.close()


class WebTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(web.app, base_url="http://localhost:8080")
        self.addCleanup(self.client.close)
        web._cache, web._cache_until = None, 0
        # Fresh locks per isolated TestClient event loop.
        web._cache_lock = asyncio.Lock()
        self.headers = {"X-Portal-Token": "a" * 48}
        self.data = {"services": {"gpu-smoke": {"state": "stopped", "reason": "", "can_stop": False, "can_start": True, "start_reason": ""}},
                     "metrics": {}, "operation": None, "active_gpu": []}
        self.mock = patch.object(web, "controller_request", side_effect=self.respond)
        self.request = self.mock.start()
        self.addCleanup(self.mock.stop)

    async def respond(self, payload):
        if payload["action"] == "snapshot":
            return {"ok": True, "data": deepcopy(self.data)}
        return {"ok": True, "message": "요청 접수"}

    def csrf_headers(self):
        self.client.get("/", headers=self.headers)
        return {**self.headers, "Origin": "http://localhost:8080", "X-CSRF-Token": self.client.cookies.get(web.COOKIE)}

    def test_unauthenticated_rejected_but_liveness_works(self):
        self.assertEqual(self.client.get("/").status_code, 403)
        self.assertEqual(self.client.post("/services/gpu-smoke/start").status_code, 403)
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.request.assert_not_called()

    def test_unconfigured_or_wrong_proxy_token_rejected(self):
        with patch.object(web, "PROXY_TOKEN", ""):
            self.assertEqual(self.client.get("/", headers=self.headers).status_code, 403)
        self.assertEqual(self.client.get("/", headers={"X-Portal-Token": "wrong"}).status_code, 403)

    def test_csrf_missing_or_cross_origin_rejected(self):
        self.assertEqual(self.client.post("/services/gpu-smoke/start", headers=self.headers).status_code, 403)
        headers = self.csrf_headers()
        headers["Origin"] = "https://evil.example"
        self.assertEqual(self.client.post("/services/gpu-smoke/start", headers=headers).status_code, 403)

    def test_service_actions_and_form_submission(self):
        headers = self.csrf_headers()
        self.assertEqual(self.client.post("/services/gpu-smoke/start", headers=headers, data={"confirm": "false"}).status_code, 200)
        self.assertIn({"action": "start", "service": "gpu-smoke", "confirm": False}, [call.args[0] for call in self.request.call_args_list])

    def test_unknown_service_and_bad_action_and_bad_body_rejected(self):
        headers = self.csrf_headers()
        for url in ("/services/unknown/start", "/services/gpu-smoke/exec"):
            self.assertEqual(self.client.post(url, headers=headers).status_code, 400)
        self.assertEqual(self.client.post("/services/gpu-smoke/start", headers=headers, json={"command": "id"}).status_code, 400)
        self.assertNotIn("command", str(self.request.call_args))

    def test_gpu_absence_and_controller_failure_render_unknown_not_fake_values(self):
        with patch.object(web, "controller_request", side_effect=OSError("SECRET_PATH")):
            response = self.client.get("/", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Controller 연결 확인 필요", response.text)
        self.assertIn("확인 불가", response.text)
        self.assertNotIn("SECRET_PATH", response.text)
        self.assertNotIn("0%", response.text)
        self.assertNotIn("<meter ", response.text)

    def test_dashboard_shows_resource_usage_bars_and_percentages(self):
        self.data["metrics"] = {
            "gpu": {"name": "Tesla V100", "utilization": 0, "memory_used": 6144, "memory_total": 16384},
            "cpu_percent": 8, "ram_used_mib": 8192, "ram_total_mib": 16384,
        }
        response = self.client.get("/status", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        for label, value, text in (("GPU 사용률", 0.0, "0%"), ("GPU 메모리 (VRAM)", 37.5, "37.5%"),
                                   ("시스템 메모리 (RAM)", 50.0, "50%")):
            self.assertIn(f'aria-label="{label}" min="0" max="100" value="{value}"', response.text)
            self.assertIn(f"<strong>{text}</strong>", response.text)
        self.assertIn("6.0 / 16.0 GB", response.text)
        self.assertIn("CPU 8%", response.text)

    def test_invalid_memory_capacity_leaves_percentage_unavailable(self):
        self.data["metrics"] = {"gpu": {"memory_used": 2048, "memory_total": 0}, "ram_total_mib": 8192}
        response = self.client.get("/status", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<meter ", response.text)
        self.assertEqual(response.text.count("용량 확인 불가"), 2)

    def test_controller_timeout_retains_timestamped_metrics_without_controls(self):
        self.data["collected_at"] = "2026-11-26T01:02:03+00:00"
        self.data["metrics"] = {"ram_used_mib": 256, "ram_total_mib": 1024}
        self.client.get("/", headers=self.headers)
        with patch.object(web, "controller_request", side_effect=TimeoutError):
            for _ in range(2):
                web._cache_until = 0
                html = self.client.get("/status", headers=self.headers).text
                self.assertIn("<strong>25%</strong>", html)
                self.assertIn("마지막 수집값", html)
                self.assertIn(self.data["collected_at"], html)
                self.assertNotIn("hx-post=", html)

    def test_large_body_and_forged_confirmation_rejected(self):
        headers = self.csrf_headers()
        self.assertEqual(self.client.post("/services/gpu-smoke/start", headers=headers, content="x" * 2048).status_code, 413)
        self.assertEqual(self.client.post("/services/gpu-smoke/start", headers=headers, data={"confirm": "yes"}).status_code, 400)
        self.assertEqual(self.client.post("/services/gpu-smoke/start", headers=headers, json={"confirm": 1}).status_code, 400)
        self.assertTrue(all(call.args[0]["action"] == "snapshot" for call in self.request.call_args_list))

    def test_confirm_required_if_active_gpu_is_not_exposed_in_portal(self):
        self.data["active_gpu"] = ["unlisted-gpu"]
        response = self.client.get("/", headers=self.headers)
        self.assertIn("hx-confirm=", response.text)
        self.assertIn('name="confirm" value="true"', response.text)

    def test_collected_time_uses_host_timestamp_in_korea_time(self):
        self.data["collected_at"] = "2026-11-26T01:02:03+00:00"
        response = self.client.get("/", headers=self.headers)
        self.assertIn("2026-11-26 10:02:03 KST", response.text)
        self.assertIn('datetime="2026-11-26T01:02:03+00:00"', response.text)

    def test_missing_collection_time_is_not_replaced_by_current_time(self):
        response = self.client.get("/", headers=self.headers)
        self.assertIn("수집 시각 확인 불가", response.text)
        self.assertNotIn("<time ", response.text)

    def test_blocked_switch_has_no_active_form_and_sends_no_control_request(self):
        self.data["active_gpu"] = ["comfyui"]
        self.data["services"]["gpu-smoke"].update(can_start=False, start_reason="ComfyUI의 안전한 종료를 확인할 수 없어 전환할 수 없습니다.")
        headers = self.csrf_headers()
        response = self.client.get("/", headers=self.headers)
        self.assertIn("전환 불가", response.text)
        self.assertNotIn('hx-post="/services/gpu-smoke/start"', response.text)
        response = self.client.post("/services/gpu-smoke/start", headers=headers, json={"confirm": True})
        self.assertEqual(response.status_code, 200)
        self.assertIn("안전한 종료", response.text)
        self.assertTrue(all(call.args[0]["action"] == "snapshot" for call in self.request.call_args_list))

    def test_spinner_renders_only_while_starting_or_stopping(self):
        for state in ("starting", "stopping", "ready", "stopped", "busy"):
            with self.subTest(state=state):
                self.data["services"]["gpu-smoke"]["state"] = state
                web._cache_until = 0
                response = self.client.get("/status", headers=self.headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual('class="status-spinner"' in response.text, state in {"starting", "stopping"})

    def test_dashboard_polling_is_cached_across_requests(self):
        for _ in range(3):
            self.assertEqual(self.client.get("/status", headers=self.headers).status_code, 200)
        self.request.assert_called_once_with({"action": "snapshot"})

    def test_unready_service_link_disabled_and_templates_escape_text(self):
        definition = web.SETTINGS["services"]["gpu-smoke"]
        with patch.dict(definition, {"url": "https://ai.example", "description": "<script>bad()</script>"}):
            response = self.client.get("/", headers=self.headers)
        self.assertNotIn('href="https://ai.example"', response.text)
        self.assertIn("&lt;script&gt;", response.text)
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_loopback_healthcheck_remains_available(self):
        response = self.client.get("http://127.0.0.1:8080/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_host_header_restricted(self):
        self.assertEqual(self.client.get("/", headers={**self.headers, "Host": "evil.example"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
