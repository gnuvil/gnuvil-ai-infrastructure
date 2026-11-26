#!/usr/bin/env python3
"""Operate the configured GPU jobs on the local AI VM."""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import yaml

from service_config import load_yaml, service_spec
from service_runtime import ControlRefused, container_state, drain_services

ROOT = Path(__file__).resolve().parents[1]
DOCKER = ("/usr/bin/docker", "--host=unix:///var/run/docker.sock")
COMPOSE = (*DOCKER, "compose", "--file", str(ROOT / "compose.yaml"))
LOCK_PATH = Path("/run/gnuvil-ai-gpu.lock")
GPU_FIELDS = "--query-gpu=name,compute_cap,driver_version,memory.used,memory.total"


def run(command, *, capture=True, timeout=30):
    return subprocess.run(
        command, cwd=ROOT, text=True, check=True, capture_output=capture, timeout=timeout
    ).stdout


def read_policy():
    policy = load_yaml(ROOT / "configs/gpu-policy.yaml")
    for key in ("stop_timeout_seconds", "idle_timeout_seconds", "job_timeout_seconds", "ready_timeout_seconds"):
        if key == "ready_timeout_seconds" and key not in policy:
            continue
        if type(policy.get(key)) is not int or policy[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    if type(policy.get("idle_memory_mib")) is not int or policy["idle_memory_mib"] < 0:
        raise ValueError("idle_memory_mib must be a non-negative integer")
    if not isinstance(policy.get("services"), dict) or not policy["services"]:
        raise ValueError("GPU policy must list at least one service")
    for service, mode in policy["services"].items():
        if not isinstance(service, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", service):
            raise ValueError(f"Invalid GPU service name: {service!r}")
        service_spec(service, mode)
    groups = [target for name, value in policy["services"].items()
              for target in service_spec(name, value)["targets"]]
    if len(groups) != len(set(groups)) or "portal" in groups:
        raise ValueError("GPU groups must be disjoint and cannot control the portal")
    return policy


def compose_config():
    return json.loads(run((*COMPOSE, "--profile", "*", "config", "--format", "json")))


def validate_compose(policy, config):
    services = config["services"]
    gpu_services = {
        name for name, service in services.items()
        if service.get("gpus")
        or service.get("runtime") == "nvidia"
        or any(
            device.get("driver") == "nvidia" or "gpu" in device.get("capabilities", [])
            for device in service.get("deploy", {}).get("resources", {})
            .get("reservations", {}).get("devices", [])
        )
    }
    if gpu_services != set(policy["services"]):
        raise ValueError("GPU Compose services must match configs/gpu-policy.yaml")
    for name, value in policy["services"].items():
        spec = service_spec(name, value)
        for target in spec["targets"]:
            if target not in services:
                raise ValueError(f"{name}: unknown companion {target}")
            member = services[target]
            if member.get("stop_signal", "SIGTERM") not in {"SIGTERM", "SIGINT"}:
                raise ValueError(f"{target}: stop_signal must be SIGTERM or SIGINT")
            if member.get("restart") != "no" or member.get("profiles") != [name]:
                raise ValueError(f"{target}: GPU group members need the group profile and restart: no")
            if not member.get("image") or member.get("pull_policy") != "never":
                raise ValueError(f"{target}: use a prebuilt local image and pull_policy: never")
            if spec["mode"] == "service" and not member.get("healthcheck", {}).get("test"):
                raise ValueError(f"{target}: long-running services require a healthcheck")
        service = services[name]
        if service.get("profiles") != [name] or service.get("restart") != "no":
            raise ValueError(f"{name}: set its own profile and restart: 'no'")
        if service.get("privileged") or service.get("deploy", {}).get("restart_policy"):
            raise ValueError(f"{name}: privileged mode and deploy restart policies are forbidden")
        if not service.get("image") or service.get("pull_policy") != "never":
            raise ValueError(f"{name}: use a local image and pull_policy: never")


def container_ids(project, service, *, running):
    command = [
        *DOCKER, "ps", "--quiet", "--no-trunc",
        "--filter", f"label=com.docker.compose.project={project}",
        "--filter", f"label=com.docker.compose.service={service}",
    ]
    if not running:
        command.extend(("--all", "--filter", "label=com.docker.compose.oneoff=False"))
    return run(command).split()


def active_containers(project, services):
    return [container for service in services for container in container_ids(project, service, running=True)]


def wait_until(predicate, seconds, message):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise RuntimeError(message)
        time.sleep(1)


def gpu_idle(memory_limit):
    processes = run((
        "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
    )).strip()
    memory = run((
        "nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits",
    )).splitlines()
    if len(memory) != 1 or not memory[0].strip().isdecimal():
        raise RuntimeError("Expected one NVIDIA GPU reporting numeric VRAM usage")
    return not processes and int(memory[0]) <= memory_limit


def managed_specs(policy):
    return [service_spec(name, value) for name, value in policy["services"].items()]


def stop_targets(project, specs, policy, config=None):
    targets = [target for spec in specs for target in spec["targets"]]
    active = active_containers(project, targets)
    if active:
        config = config if config is not None else compose_config()
        for target in targets:
            containers = container_ids(project, target, running=True)
            if containers:
                signal = config["services"][target].get("stop_signal", "SIGTERM")
                if signal not in {"SIGTERM", "SIGINT"}:
                    raise RuntimeError("Unsupported stop signal")
                run((*DOCKER, "kill", f"--signal={signal}", *containers))
        wait_until(
            lambda: not active_containers(project, targets),
            policy["stop_timeout_seconds"],
            "종료 시간 제한을 초과했습니다. 운영자가 상태를 확인해야 합니다.",
        )


def stop_managed_services(project, policy, confirm_stop=False, *, safe=False):
    specs = [spec for spec in managed_specs(policy) if active_containers(project, spec["targets"])]
    if specs:
        if safe:
            safe_stop(project, specs, policy, require_gpu_idle=True)
            return
        elif not confirm_stop:
            raise RuntimeError(
                "A managed GPU container is active. Check status, drain its work, "
                "then rerun with CONFIRM=1."
            )
        stop_targets(project, specs, policy)
    wait_until(
        lambda: gpu_idle(policy["idle_memory_mib"]),
        policy["idle_timeout_seconds"],
        "GPU 프로세스가 남아 있거나 VRAM이 회수되지 않았습니다. 전환을 중단했습니다.",
    )


def check_images(config, spec):
    for target in spec["targets"]:
        try:
            run((*DOCKER, "image", "inspect", config["services"][target]["image"]))
        except subprocess.CalledProcessError:
            raise RuntimeError(f"{target}: 로컬 이미지가 없습니다. 운영자가 먼저 빌드해야 합니다.") from None


def start_service(project, spec, policy):
    run((*COMPOSE, "up", "--detach", "--no-deps", "--no-build", "--pull", "never", *spec["targets"]))
    def ready():
        state = container_state(run, DOCKER, project, spec, container_ids)
        if state["state"] == "error":
            raise RuntimeError(state["reason"])
        return state["state"] in {"ready", "busy"}
    wait_until(ready, policy.get("ready_timeout_seconds", 120), "서비스 준비 시간 제한을 초과했습니다.")


@contextmanager
def gpu_lock():
    with LOCK_PATH.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ControlRefused("다른 서비스 전환이 진행 중입니다. 잠시 후 다시 시도하세요.") from None
        yield


def safe_stop(project, specs, policy, *, require_gpu_idle):
    if any(spec["mode"] == "job" for spec in specs):
        raise ControlRefused("실행 중인 일회성 작업은 웹에서 종료할 수 없습니다.")
    for spec in specs:
        for target in spec["targets"]:
            active = set(container_ids(project, target, running=True))
            regular = set(container_ids(project, target, running=False))
            if active - regular:
                raise ControlRefused("일회성 Compose 컨테이너가 실행 중이어서 웹 종료를 제한합니다.")
    drain_services(specs)
    stop_targets(project, specs, policy)
    if require_gpu_idle:
        wait_until(lambda: gpu_idle(policy["idle_memory_mib"]), policy["idle_timeout_seconds"],
                   "GPU 프로세스가 남아 있거나 VRAM이 회수되지 않았습니다. 전환을 중단했습니다.")


def control_registered_service(project, spec, policy, config, action, *, gpu_service):
    """Targeted web stop/CPU start, using the same host lock and shutdown rules."""
    with gpu_lock():
        running = active_containers(project, spec["targets"])
        if action == "stop":
            if running:
                safe_stop(project, [spec], policy, require_gpu_idle=gpu_service)
        elif running:
            if not all(container_ids(project, target, running=True) for target in spec["targets"]):
                raise RuntimeError("서비스 일부만 실행 중입니다. 운영자가 확인해야 합니다.")
        else:
            check_images(config, spec)
            start_service(project, spec, policy)


def transition(project, service, policy, config, *, confirm_stop=False, safe=False, confirm_switch=False):
    """One lock and lifecycle implementation for both the CLI and web controller."""
    with gpu_lock():
        if service:
            spec = service_spec(service, policy["services"][service])
            active_others = [other for other in policy["services"] if other != service
                             and active_containers(project, service_spec(other, policy["services"][other])["targets"])]
            target_running = active_containers(project, spec["targets"])
            if target_running:
                if active_others:
                    raise RuntimeError("여러 GPU 서비스가 동시에 실행 중입니다. 운영자가 확인해야 합니다.")
                if all(container_ids(project, target, running=True) for target in spec["targets"]):
                    return  # Idempotent start; never restart an existing job/service.
                raise RuntimeError("서비스 일부만 실행 중입니다. 운영자가 확인해야 합니다.")
            if safe and active_others and not confirm_switch:
                raise ControlRefused("다른 GPU 서비스를 종료하는 전환입니다. 상태를 새로 확인하고 전환에 동의하세요.")
            check_images(config, spec)
        stop_managed_services(project, policy, confirm_stop, safe=safe)
        if service:
            if spec["mode"] == "job":
                run_job(project, service, policy)
            else:
                start_service(project, spec, policy)


def run_job(project, service, policy):
    run((
        *COMPOSE, "up", "--detach", "--no-deps", "--no-build", "--pull", "never",
        "--force-recreate", service,
    ), capture=False)
    containers = container_ids(project, service, running=False)
    if len(containers) != 1:
        raise RuntimeError(f"Expected exactly one container for {service}, found {len(containers)}")
    container = containers[0]
    status = run((*DOCKER, "wait", container), timeout=policy["job_timeout_seconds"]).strip()
    run((*DOCKER, "logs", container), capture=False)
    if status != "0":
        raise RuntimeError(f"{service} exited with status {status}")
    wait_until(
        lambda: gpu_idle(policy["idle_memory_mib"]),
        policy["idle_timeout_seconds"],
        "The job exited, but the GPU has not returned to its idle state.",
    )
    print(f"{service} completed successfully. The GPU is idle.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "switch", "off", "status"))
    parser.add_argument("service", nargs="?")
    parser.add_argument("--confirm-stop", action="store_true")
    args = parser.parse_args()
    if (args.action in {"switch", "build"}) != bool(args.service):
        parser.error("build and switch require exactly one GPU service")
    if args.confirm_stop and args.action not in {"switch", "off"}:
        parser.error("--confirm-stop is for switch and off")
    if os.geteuid() != 0:
        raise RuntimeError("Run this command on the AI VM with sudo")

    policy = read_policy()
    if args.service and args.service not in policy["services"]:
        raise ValueError(f"Unknown GPU service {args.service!r}. Available: {', '.join(policy['services'])}")
    config = compose_config()
    validate_compose(policy, config)
    project = config["name"]

    if args.action == "status":
        run((*COMPOSE, "--profile", "*", "ps", "--all"), capture=False)
        run(("nvidia-smi", GPU_FIELDS, "--format=csv"), capture=False)
        run(("nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv"), capture=False)
        return
    if args.action == "build":
        run((*COMPOSE, "build", args.service), capture=False, timeout=None)
        return

    transition(project, args.service, policy, config, confirm_stop=args.confirm_stop)
    if not args.service:
        print("All managed GPU services are stopped and the GPU is idle.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.TimeoutExpired as error:
        print(f"Timed out running {error.cmd[0]} after {error.timeout}s. Inspect status before retrying.", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as error:
        print((error.stderr or str(error)).strip(), file=sys.stderr)
        sys.exit(1)
    except (OSError, RuntimeError, ValueError, yaml.YAMLError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
