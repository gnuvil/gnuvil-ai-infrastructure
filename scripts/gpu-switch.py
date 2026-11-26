#!/usr/bin/env python3
"""Operate the configured GPU jobs on the local AI VM."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCKER = ("docker", "--host=unix:///var/run/docker.sock")
COMPOSE = (*DOCKER, "compose", "--file", str(ROOT / "compose.yaml"))
LOCK_PATH = Path("/run/gnuvil-ai-gpu.lock")
GPU_FIELDS = "--query-gpu=name,compute_cap,driver_version,memory.used,memory.total"


def run(command, *, capture=True, timeout=30):
    return subprocess.run(
        command, cwd=ROOT, text=True, check=True, capture_output=capture, timeout=timeout
    ).stdout


def read_policy():
    policy = yaml.safe_load((ROOT / "configs/gpu-policy.yaml").read_text())
    for key in ("stop_timeout_seconds", "idle_timeout_seconds", "job_timeout_seconds"):
        if type(policy.get(key)) is not int or policy[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    if type(policy.get("idle_memory_mib")) is not int or policy["idle_memory_mib"] < 0:
        raise ValueError("idle_memory_mib must be a non-negative integer")
    if not isinstance(policy.get("services"), dict) or not policy["services"]:
        raise ValueError("GPU policy must list at least one service")
    for service, mode in policy["services"].items():
        if not isinstance(service, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", service):
            raise ValueError(f"Invalid GPU service name: {service!r}")
        if mode not in ("job", "service"):
            raise ValueError(f"{service}: GPU service type must be job or service")
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
    for name in gpu_services:
        service = services[name]
        if service.get("profiles") != [name] or service.get("restart") != "no":
            raise ValueError(f"{name}: set its own profile and restart: 'no'")
        if service.get("privileged") or service.get("deploy", {}).get("restart_policy"):
            raise ValueError(f"{name}: privileged mode and deploy restart policies are forbidden")
        if not service.get("image") or service.get("pull_policy") != "never":
            raise ValueError(f"{name}: use a local image and pull_policy: never")
        if service.get("stop_signal", "SIGTERM") not in ("SIGTERM", "SIGINT"):
            raise ValueError(f"{name}: stop_signal must be SIGTERM or SIGINT")


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


def stop_managed_services(project, policy, confirm_stop, config):
    active_by_service = {
        name: container_ids(project, name, running=True)
        for name in policy["services"]
    }
    active = any(active_by_service.values())
    if active and not confirm_stop:
        raise RuntimeError(
            "A managed GPU container is active. Check status, drain its work, "
            "then rerun with CONFIRM=1."
        )
    if active:
        for name, containers in active_by_service.items():
            if containers:
                stop_signal = config["services"][name].get("stop_signal", "SIGTERM")
                run((*DOCKER, "kill", f"--signal={stop_signal}", *containers))
        wait_until(
            lambda: not active_containers(project, policy["services"]),
            policy["stop_timeout_seconds"],
            "A managed container did not exit after its stop signal. Inspect it before retrying.",
        )
    wait_until(
        lambda: gpu_idle(policy["idle_memory_mib"]),
        policy["idle_timeout_seconds"],
        "GPU is still busy or above the idle VRAM threshold. Inspect nvidia-smi before retrying.",
    )


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


def run_service(project, service):
    run((*COMPOSE, "up", "--detach", "--no-deps", "--no-build", "--pull", "never",
         "--force-recreate", service), capture=False)
    containers = container_ids(project, service, running=False)
    if len(containers) != 1:
        raise RuntimeError(f"Expected exactly one container for {service}, found {len(containers)}")
    container = containers[0]

    def ready():
        status = run((*DOCKER, "inspect", "--format", "{{.State.Status}}", container)).strip()
        if status != "running":
            raise RuntimeError(f"{service} exited during startup; inspect its logs")
        try:
            run((*DOCKER, "exec", container, "python3", "-c",
                 "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8188/system_stats', timeout=2).read()"))
            return True
        except subprocess.CalledProcessError:
            return False

    wait_until(ready, 180, f"{service} did not become ready; inspect its logs before retrying")
    print(f"{service} is ready. Drain its queue before switching GPU services.")


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

    with LOCK_PATH.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another GPU transition is in progress") from None
        if args.service:
            image = config["services"][args.service]["image"]
            try:
                run((*DOCKER, "image", "inspect", image))
            except subprocess.CalledProcessError:
                raise RuntimeError(f"Cannot inspect {image}. Run sudo make build SERVICE={args.service} first.") from None
        stop_managed_services(project, policy, args.confirm_stop, config)
        if args.service:
            if policy["services"][args.service] == "job":
                run_job(project, args.service, policy)
            else:
                run_service(project, args.service)
        else:
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
