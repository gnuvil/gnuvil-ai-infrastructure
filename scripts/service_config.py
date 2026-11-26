"""Small, shared configuration contract for the portal and host controller."""

from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml

IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}\Z")
PROBE_SOCKET = re.compile(r"/run/gnuvil-[a-z0-9-]+/lifecycle\.sock\Z")


class UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise ValueError(f"{key_node.start_mark}: duplicate key {key!r}")
            seen.add(key)
        return super().construct_mapping(node, deep)


def load_yaml(path):
    with Path(path).open() as stream:
        value = yaml.load(stream, Loader=UniqueKeyLoader)
    if not isinstance(value, dict):
        raise ValueError(f"{Path(path).name}: expected a mapping")
    return value


def identifier(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def service_spec(name, value):
    """Preserve the original `gpu-smoke: job` policy shorthand."""
    if isinstance(value, str):
        value = {"mode": value}
    fields = {"mode", "companions", "probe_url", "probe_socket", "ready_url", "idle_shutdown_seconds"}
    if not isinstance(value, dict) or set(value) - fields:
        raise ValueError(f"{name}: invalid service policy")
    mode = value.get("mode")
    companions = value.get("companions", [])
    if not isinstance(mode, str) or mode not in {"job", "service"} or not isinstance(companions, list):
        raise ValueError(f"{name}: expected job/service and a companions list")
    targets = [name, *companions]
    if not all(identifier(target) for target in targets) or len(set(targets)) != len(targets):
        raise ValueError(f"{name}: invalid or duplicate Compose targets")
    probe_url = value.get("probe_url")
    if probe_url is not None:
        if not isinstance(probe_url, str):
            raise ValueError(f"{name}: probe_url must be a string")
        parts = urlsplit(probe_url)
        if (parts.scheme != "http" or parts.hostname != "127.0.0.1" or not parts.port
                or parts.username or parts.password or parts.query or parts.fragment
                or parts.path not in {"", "/"}):
            raise ValueError(f"{name}: probe_url must be http://127.0.0.1:<port>")
        probe_url = probe_url.rstrip("/")
    probe_socket = value.get("probe_socket")
    if probe_socket is not None:
        if not isinstance(probe_socket, str) or len(probe_socket) > 103 or not PROBE_SOCKET.fullmatch(probe_socket):
            raise ValueError(f"{name}: probe_socket must use /run/gnuvil-<service>/lifecycle.sock")
        if probe_url:
            raise ValueError(f"{name}: configure only one lifecycle probe")
    ready_url = value.get("ready_url")
    if ready_url is not None:
        if not isinstance(ready_url, str):
            raise ValueError(f"{name}: ready_url must be a string")
        parts = urlsplit(ready_url)
        if (parts.scheme != "http" or parts.hostname != "127.0.0.1" or not parts.port
                or parts.username or parts.password or parts.query or parts.fragment):
            raise ValueError(f"{name}: ready_url must use a loopback HTTP port")
    if mode == "job" and (companions or probe_url or probe_socket or ready_url):
        raise ValueError(f"{name}: jobs cannot have companions or a drain probe")
    idle_shutdown = value.get("idle_shutdown_seconds")
    if "idle_shutdown_seconds" in value:
        if type(idle_shutdown) is not int or idle_shutdown <= 0:
            raise ValueError(f"{name}: idle_shutdown_seconds must be a positive integer")
        if mode != "service" or not (probe_url or probe_socket):
            raise ValueError(f"{name}: idle shutdown requires a long-running service and a drain probe")
    return {"mode": mode, "targets": targets, "probe_url": probe_url, "probe_socket": probe_socket, "ready_url": ready_url,
            "idle_shutdown_seconds": idle_shutdown}


def validate_portal(portal, policy=None, compose=None):
    if set(portal) - {"title", "public_origin", "allowed_hosts", "services"}:
        raise ValueError("Unknown portal setting")
    if not isinstance(portal.get("title"), str) or not portal["title"].strip():
        raise ValueError("Portal title is required")
    origin = urlsplit(portal.get("public_origin", ""))
    if (origin.scheme not in {"http", "https"} or not origin.hostname
            or origin.username or origin.password or origin.path or origin.query or origin.fragment):
        raise ValueError("public_origin must be an HTTP(S) origin without a trailing slash")
    hosts = portal.get("allowed_hosts")
    if (not isinstance(hosts, list) or not hosts
            or not all(isinstance(host, str) and re.fullmatch(r"[a-zA-Z0-9.-]+", host) for host in hosts)
            or origin.hostname not in hosts):
        raise ValueError("allowed_hosts must explicitly include the public_origin host")
    services = portal.get("services")
    if not isinstance(services, dict):
        raise ValueError("Portal services must be a mapping")
    targets_seen = set()
    gpu_seen = set()
    for key, service in services.items():
        if not identifier(key) or not isinstance(service, dict):
            raise ValueError("Invalid portal service")
        if set(service) - {"name", "description", "category", "icon", "url", "gpu_service", "cpu_service"}:
            raise ValueError(f"{key}: unknown portal service field")
        for field in ("name", "description", "category", "icon"):
            if not isinstance(service.get(field), str) or not service[field].strip():
                raise ValueError(f"{key}: {field} is required")
        url = service.get("url")
        if url is not None:
            parts = urlsplit(url)
            if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
                raise ValueError(f"{key}: invalid service URL")
        if ("gpu_service" in service) == ("cpu_service" in service):
            raise ValueError(f"{key}: choose exactly one of gpu_service/cpu_service")
        if "gpu_service" in service:
            name = service["gpu_service"]
            if not identifier(name) or name in gpu_seen:
                raise ValueError(f"{key}: invalid or duplicate GPU mapping")
            gpu_seen.add(name)
            if policy is not None and name not in policy["services"]:
                raise ValueError(f"{key}: GPU service is not in the policy")
            spec = service_spec(name, policy["services"][name]) if policy else None
        else:
            cpu = service["cpu_service"]
            fields = {"service", "companions", "probe_url", "probe_socket", "ready_url", "idle_shutdown_seconds"}
            if not isinstance(cpu, dict) or set(cpu) - fields:
                raise ValueError(f"{key}: invalid CPU service")
            name = cpu.get("service")
            spec = service_spec(name, {"mode": "service", **{k: v for k, v in cpu.items() if k != "service"}})
        if spec:
            if targets_seen.intersection(spec["targets"]) or "portal" in spec["targets"]:
                raise ValueError(f"{key}: shared targets and portal self-control are forbidden")
            targets_seen.update(spec["targets"])
            if compose and any(target not in compose["services"] for target in spec["targets"]):
                raise ValueError(f"{key}: unknown Compose target")
            if "cpu_service" in service and compose:
                for target in spec["targets"]:
                    member = compose["services"][target]
                    if (not member.get("image") or member.get("pull_policy") != "never"
                            or member.get("restart") != "no" or not member.get("profiles")
                            or not member.get("healthcheck", {}).get("test")
                            or member.get("privileged") or member.get("deploy", {}).get("restart_policy")):
                        raise ValueError(f"{target}: controlled CPU services need a profile, local image, healthcheck and restart: no")
            if "cpu_service" in service and policy:
                gpu_targets = {target for gpu_name, value in policy["services"].items()
                               for target in service_spec(gpu_name, value)["targets"]}
                if gpu_targets.intersection(spec["targets"]):
                    raise ValueError(f"{key}: CPU controls cannot reference a GPU service group")
    return portal


def portal_spec(service, policy):
    if "gpu_service" in service:
        return service_spec(service["gpu_service"], policy["services"][service["gpu_service"]])
    cpu = service["cpu_service"]
    return service_spec(cpu["service"], {"mode": "service", **{k: v for k, v in cpu.items() if k != "service"}})
