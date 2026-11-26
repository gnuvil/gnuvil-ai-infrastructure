#!/usr/bin/env python3
"""Check repository YAML, Python syntax and the resolved Compose config without a daemon."""

import ast
from pathlib import Path
import importlib.util
import json
import ipaddress
import shutil
import subprocess

from service_config import UniqueKeyLoader, load_yaml, validate_portal

import yaml




root = Path(__file__).resolve().parents[1]
yaml_paths = [root / "compose.yaml"]
for folder in ("configs", "services", "ansible"):
    yaml_paths.extend(
        path for path in (root / folder).rglob("*.yaml")
        if "collections" not in path.relative_to(root).parts and not path.name.endswith(".local.yaml")
    )
for path in sorted(yaml_paths):
    with path.open() as stream:
        list(yaml.load_all(stream, Loader=UniqueKeyLoader))
python_paths = [path for folder in ("scripts", "services/portal", "services/comfyui/gnuvil_lifecycle", "tests")
                for path in (root / folder).glob("*.py")]
for path in sorted(python_paths):
    ast.parse(path.read_text(), filename=str(path))
print(f"PASS: {len(yaml_paths)} YAML files and Python script syntax")

spec = importlib.util.spec_from_file_location("gpu_switch", root / "scripts/gpu-switch.py")
gpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gpu)
policy = gpu.read_policy()
portal = validate_portal(load_yaml(root / "configs/portal.yaml"), policy)
docker = shutil.which("docker")
if docker:
    compose = (docker, "compose", "--file", str(root / "compose.yaml"))
    config = json.loads(subprocess.run((*compose, "--profile", "*", "config", "--format", "json"),
                                      check=True, text=True, capture_output=True).stdout)
    gpu.validate_compose(policy, config)
    validate_portal(portal, policy, config)
    default_services = subprocess.run((*compose, "config", "--services"),
                                     check=True, text=True, capture_output=True).stdout.split()
    if set(default_services).intersection(policy["services"]):
        raise ValueError("Default Compose configuration activates a GPU service")
    service = config["services"]["portal"]
    if (service.get("privileged") or service.get("gpus") or service.get("runtime") == "nvidia"
            or service.get("user") != "10001:10001" or not service.get("read_only")
            or service.get("network_mode") == "host"
            or service.get("cap_add") or service.get("devices")
            or service.get("pid") == "host" or service.get("ipc") == "host"):
        raise ValueError("Portal must be an unprivileged, isolated CPU-only service")
    if service.get("ports") != [{"mode": "ingress", "host_ip": "127.0.0.1", "target": 8080,
                                "published": "8080", "protocol": "tcp"}]:
        raise ValueError("Portal must only publish 127.0.0.1:8080")
    mounts = service.get("volumes", [])
    if len(mounts) != 2 or any(not mount.get("read_only") for mount in mounts):
        raise ValueError("Portal mounts must be restricted and read-only")
    if {mount["target"] for mount in mounts} != {"/etc/gnuvil-ai/portal.yaml", "/run/gnuvil-ai"}:
        raise ValueError("Portal may not mount Docker or other host paths")
    proxy = config["services"].get("portal-proxy")
    if proxy:
        ports = proxy.get("ports", [])
        if (proxy.get("profiles") != ["portal-access"] or proxy.get("user") != "10001:10001"
                or proxy.get("privileged") or proxy.get("gpus") or proxy.get("devices")
                or proxy.get("cap_add") or not proxy.get("read_only")
                or proxy.get("network_mode") == "host" or "@sha256:" not in proxy.get("image", "")
                or len(ports) != 1 or ports[0].get("target") != 8443 or ports[0].get("published") != "443"):
            raise ValueError("LAN proxy must be isolated, pinned, CPU-only and explicitly profiled")
        address = ipaddress.ip_address(ports[0].get("host_ip", "0.0.0.0"))
        if not address.is_private or address.is_unspecified:
            raise ValueError("LAN proxy must bind an explicit private/loopback address")
        expected_mounts = {"/etc/nginx/nginx.conf", "/etc/nginx/portal-token.conf", "/etc/nginx/portal.htpasswd",
                           "/etc/nginx/portal.crt", "/etc/nginx/portal.key"}
        mounts = proxy.get("volumes", [])
        if {mount["target"] for mount in mounts} != expected_mounts or len(mounts) != 5 or any(not mount.get("read_only") for mount in mounts):
            raise ValueError("LAN proxy may only mount its read-only configuration and credentials")
    print("PASS: Compose include, portal/proxy isolation, default profiles and GPU policy")
else:
    print("SKIP: Docker CLI unavailable; resolved Compose and isolation checks not run")
print("PASS: portal configuration and GPU service allowlist")
