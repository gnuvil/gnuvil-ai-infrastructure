#!/usr/bin/env python3
"""Initialize LAN proxy secrets locally on the VM; never print credentials."""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess


def initialize(address, gid, directory):
    address = str(ipaddress.IPv4Address(address))
    if os.geteuid() != 0:
        raise RuntimeError("Run on the AI VM as root")
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise RuntimeError("Refusing a symlink secret directory")
    os.chmod(directory, 0o700)
    identity = directory / "portal-access.json"
    if identity.exists():
        raise RuntimeError("Access is already initialized; preserve or explicitly rotate existing credentials")
    os.umask(0o077)
    password = secrets.token_urlsafe(18)
    hashed = subprocess.run(["/usr/bin/openssl", "passwd", "-6", "-stdin"], input=password + "\n",
                            text=True, capture_output=True, check=True).stdout.strip()
    env_file = directory / "portal.env"
    env_lines = env_file.read_text().splitlines() if env_file.exists() else []
    values = dict(line.split("=", 1) for line in env_lines if line and not line.startswith("#") and "=" in line)
    token = values.get("PORTAL_PROXY_TOKEN") or secrets.token_urlsafe(48)
    if len(token) < 32 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in token):
        raise RuntimeError("Existing proxy token must be at least 32 URL-safe characters")
    values.update(PORTAL_PROXY_TOKEN=token, PORTAL_SOCKET_GID=str(gid), PORTAL_BIND_IP=address)
    env_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    os.chmod(env_file, 0o600)
    (directory / "portal.htpasswd").write_text(f"lab:{hashed}\n")
    (directory / "proxy-token.conf").write_text(f'proxy_set_header X-Portal-Token "{token}";\n')
    subprocess.run([
        "/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:3072", "-sha256", "-days", "365", "-noenc",
        "-subj", f"/CN={address}", "-addext", f"subjectAltName=IP:{address}",
        "-addext", "basicConstraints=critical,CA:FALSE",
        "-keyout", str(directory / "portal.key"), "-out", str(directory / "portal.crt"),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for filename in ("portal.htpasswd", "proxy-token.conf", "portal.key", "portal.crt"):
        path = directory / filename
        os.chown(path, 0, gid)
        os.chmod(path, 0o440)
    identity.write_text(json.dumps({"url": f"https://{address}", "username": "lab", "password": password}, indent=2) + "\n")
    os.chmod(identity, 0o600)
    print("Initialized LAN proxy credentials and certificate; retrieve via root-only portal-access.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True)
    parser.add_argument("--gid", type=int, default=10001)
    parser.add_argument("--directory", default="/etc/gnuvil-ai")
    args = parser.parse_args()
    initialize(args.address, args.gid, args.directory)
