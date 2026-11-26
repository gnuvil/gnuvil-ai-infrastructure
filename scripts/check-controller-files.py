#!/usr/bin/env python3
"""Refuse privileged execution from an untrusted checkout (stdlib only)."""

import os
from pathlib import Path
import stat
import sys


def check(root):
    root = Path(root).absolute()
    # Includes .env, Dockerfiles, Python imports and Compose includes. Do not chmod
    # someone else's checkout automatically: deployment ownership is an operator step.
    protected = [root, *root.parents]
    for folder in ("scripts", "configs", "services"):
        protected.extend([root / folder, *(root / folder).rglob("*")])
    protected.append(root / "compose.yaml")
    for optional in (".env", ".env.example"):
        if (root / optional).exists():
            protected.append(root / optional)
    for path in protected:
        if ".venv" in path.parts:
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError(f"Controller input must be root-owned, non-symlink and not group/world writable: {path}")
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise RuntimeError(f"Unsupported controller input: {path}")
    # Compose may interpolate these into host paths; keep all host env inputs root-owned.
    if not os.access(root, os.R_OK):
        raise RuntimeError("Cannot read the protected checkout")


if __name__ == "__main__":
    check(sys.argv[1])
