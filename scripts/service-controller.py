#!/usr/bin/env python3
"""Root-owned, bounded JSON-line API over a permission-restricted Unix socket."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import socketserver
import subprocess
import threading
import time

from service_config import load_yaml, portal_spec, validate_portal
from service_runtime import ControlRefused, container_state

ROOT = Path(__file__).resolve().parents[1]
module_spec = importlib.util.spec_from_file_location("gpu_switch", ROOT / "scripts/gpu-switch.py")
gpu = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(gpu)
LOG = logging.getLogger("gnuvil.controller")


def system_metrics(previous_cpu=None):
    result = {"gpu": None, "cpu_percent": None, "ram_used_mib": None, "ram_total_mib": None}
    try:
        rows = list(csv.reader(io.StringIO(gpu.run((
            "nvidia-smi", "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw",
            "--format=csv,noheader,nounits",
        ), timeout=3))))
        if len(rows) != 1 or len(rows[0]) != 6:
            raise ValueError("Expected one GPU")
        row = [cell.strip() for cell in rows[0]]
        def number(value):
            try:
                return float(value)
            except ValueError:
                return None
        result["gpu"] = dict(zip(("name", "utilization", "temperature", "memory_used", "memory_total", "power"),
                                 [row[0], *(number(value) for value in row[1:])]))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    try:
        values = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
        current = (sum(values), values[3] + values[4])
        if previous_cpu and current[0] > previous_cpu[0]:
            result["cpu_percent"] = round(100 * (1 - (current[1] - previous_cpu[1]) / (current[0] - previous_cpu[0])), 1)
        previous_cpu = current
        memory = {line.split(":")[0]: int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines()}
        result["ram_total_mib"] = round(memory["MemTotal"] / 1024)
        result["ram_used_mib"] = round((memory["MemTotal"] - memory["MemAvailable"]) / 1024)
    except (OSError, ValueError, KeyError, IndexError):
        pass
    return result, previous_cpu


class Controller:
    def __init__(self, *, start_sampler=True):
        self.policy = gpu.read_policy()
        self.compose = gpu.compose_config()
        gpu.validate_compose(self.policy, self.compose)
        self.portal = validate_portal(load_yaml(ROOT / "configs/portal.yaml"), self.policy, self.compose)
        self.project = self.compose["name"]
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="service-control")
        self.operation_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.cache_lock = threading.Lock()
        self.operation = None
        self.errors = {}
        self.notice = None
        self.cache = None
        self.cache_time = 0
        self.cpu_sample = None
        self.sampler_stop = threading.Event()
        if start_sampler:
            threading.Thread(target=self.sample, daemon=True, name="host-sampler").start()

    def refresh(self):
        # Slow host commands run in a sampler, never in a web/Unix-socket request.
        states = {}
        for key, service in self.portal["services"].items():
            try:
                states[key] = container_state(gpu.run, gpu.DOCKER, self.project,
                                              portal_spec(service, self.policy), gpu.container_ids)
            except Exception:
                states[key] = {"state": "unknown", "reason": "서비스 상태를 확인할 수 없습니다.", "can_stop": False}
        try:
            active_gpu = [name for name, value in self.policy["services"].items()
                          if gpu.active_containers(self.project, portal_spec({"gpu_service": name}, self.policy)["targets"])]
        except Exception:
            active_gpu = None
        metrics, self.cpu_sample = system_metrics(self.cpu_sample)
        with self.cache_lock:
            self.cache = {"services": states, "metrics": metrics, "active_gpu": active_gpu,
                          "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            self.cache_time = time.monotonic()

    def sample(self):
        while not self.sampler_stop.is_set():
            try:
                self.refresh()
            except Exception:
                LOG.warning("host sampling failed")
            self.sampler_stop.wait(2)

    def start_block_reason(self, key, states, active_gpu):
        service = self.portal["services"][key]
        if "gpu_service" not in service:
            return ""
        if active_gpu is None:
            return "GPU 서비스 상태를 확인할 수 없어 시작할 수 없습니다."
        for other in active_gpu:
            if other == service["gpu_service"]:
                continue
            definition = next((value for value in self.portal["services"].values()
                               if value.get("gpu_service") == other), {})
            label = definition.get("name", other)
            spec = portal_spec({"gpu_service": other}, self.policy)
            if spec["mode"] == "job":
                return f"{label} 작업이 실행 중이어서 전환할 수 없습니다."
            if not spec.get("probe_url"):
                return f"{label}의 안전한 종료를 확인할 수 없어 전환할 수 없습니다."
            other_state = next((states[other_key] for other_key, value in self.portal["services"].items()
                                if value.get("gpu_service") == other), {})
            if not other_state.get("can_stop", False):
                return f"{label}이 작업 중이거나 작업 상태를 확인할 수 없어 전환할 수 없습니다."
        return ""

    def snapshot(self):
        with self.cache_lock:
            fresh = self.cache is not None and time.monotonic() - self.cache_time < 10
            snapshot = dict(self.cache) if fresh else {
                "services": {key: {"state": "unknown", "reason": "호스트 상태를 확인할 수 없습니다.", "can_stop": False}
                             for key in self.portal["services"]},
                "metrics": {"gpu": None, "cpu_percent": None, "ram_used_mib": None, "ram_total_mib": None},
                "collected_at": (self.cache or {}).get("collected_at"),
            }
            snapshot["stale"] = not fresh
        with self.state_lock:
            states = {key: dict(value) for key, value in snapshot["services"].items()}
            for key, message in self.errors.items():
                states[key]["reason"] = message
                states[key]["state"] = "error"
                states[key]["can_stop"] = False
            if self.operation:
                key, action = self.operation
                phase = "stopping" if action == "stop" else "starting"
                if action != "stop" and portal_spec(self.portal["services"][key], self.policy)["mode"] == "job" and states[key]["state"] == "busy":
                    phase = "busy"
                states[key].update(state=phase, can_stop=False,
                                   reason="일회성 작업이 실행 중입니다. 완료될 때까지 기다려 주세요." if phase == "busy" else "요청을 처리하고 있습니다. 잠시 기다려 주세요.")
            for key, state in states.items():
                reason = self.start_block_reason(key, states, snapshot.get("active_gpu"))
                state["start_reason"] = reason
                state["can_start"] = state["state"] in {"stopped", "error"} and not reason and self.operation is None
            snapshot["services"] = states
            snapshot["operation"] = self.operation
            snapshot["notice"] = self.notice[0] if self.notice and time.monotonic() - self.notice[1] < 15 else None
        return snapshot

    def submit(self, key, action, confirm):
        if key not in self.portal["services"] or action not in {"start", "stop", "switch"} or type(confirm) is not bool:
            raise ValueError("등록되지 않은 서비스 또는 허용되지 않은 요청입니다.")
        if not self.operation_lock.acquire(blocking=False):
            with self.state_lock:
                if self.operation == (key, action):
                    return {"ok": True, "message": "이미 같은 요청을 처리하고 있습니다."}
            return {"ok": False, "message": "다른 서비스 제어가 진행 중입니다. 잠시 후 다시 시도하세요."}
        with self.state_lock:
            self.operation = (key, action)
            self.errors.pop(key, None)
            self.notice = None
        self.worker.submit(self.execute, key, action, confirm)
        return {"ok": True, "message": "요청을 접수했습니다. 진행 상태가 자동으로 갱신됩니다."}

    def execute(self, key, action, confirm):
        try:
            service = self.portal["services"][key]
            spec = portal_spec(service, self.policy)
            if "gpu_service" in service and action != "stop":
                gpu.transition(self.project, service["gpu_service"], self.policy, self.compose,
                               safe=True, confirm_switch=confirm)
            else:
                gpu.control_registered_service(self.project, spec, self.policy, self.compose, action,
                                               gpu_service="gpu_service" in service)
        except ControlRefused as error:
            with self.state_lock:
                self.notice = (str(error), time.monotonic())
            LOG.info("operation refused: service=%s action=%s", key, action)
        except Exception as error:
            # Never serialize subprocess stderr, paths or probe bodies to a browser.
            message = str(error) if isinstance(error, RuntimeError) else "제어 작업에 실패했습니다. 운영자가 Controller 로그를 확인해야 합니다."
            with self.state_lock:
                self.errors[key] = message
            LOG.warning("operation failed: service=%s action=%s type=%s", key, action, type(error).__name__)
        finally:
            with self.state_lock:
                self.operation = None
            self.operation_lock.release()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        try:
            payload = self.rfile.readline(4097)
            if len(payload) > 4096 or not payload.endswith(b"\n"):
                raise ValueError("Invalid payload")
            request = json.loads(payload)
            if request == {"action": "snapshot"}:
                result = {"ok": True, "data": self.server.controller.snapshot()}
            elif isinstance(request, dict) and set(request) == {"action", "service", "confirm"}:
                result = self.server.controller.submit(request["service"], request["action"], request["confirm"])
            else:
                raise ValueError("Invalid action")
        except Exception:
            result = {"ok": False, "message": "허용되지 않은 요청이거나 Controller 조회에 실패했습니다."}
        try:
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode() + b"\n")
        except OSError:
            pass


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, controller):
        self.controller = controller
        self.clients = threading.BoundedSemaphore(16)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.clients.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.clients.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.clients.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", default="/run/gnuvil-ai/controller.sock")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Controller must run under the managed systemd unit")
    os.umask(0o007)
    controller = Controller()
    path = Path(args.socket)
    if path.exists():
        if not path.is_socket():
            raise RuntimeError("Refusing to replace a non-socket path")
        path.unlink()
    with Server(str(path), controller) as server:
        os.chmod(path, 0o660)
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
