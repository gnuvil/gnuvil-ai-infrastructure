#!/usr/bin/env python3
"""Check repository YAML, Python syntax and the resolved Compose config without a daemon."""

import ast
from pathlib import Path
import runpy

import yaml


class UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise ValueError(f"{key_node.start_mark}: duplicate key {key!r}")
            seen.add(key)
        return super().construct_mapping(node, deep)


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
for path in sorted((root / "scripts").glob("*.py")):
    ast.parse(path.read_text(), filename=str(path))
print(f"PASS: {len(yaml_paths)} YAML files and Python script syntax")

gpu = runpy.run_path(str(root / "scripts/gpu-switch.py"))
policy = gpu["read_policy"]()
gpu["validate_compose"](policy, gpu["compose_config"]())
default_services = gpu["run"]((*gpu["COMPOSE"], "config", "--services")).split()
if set(default_services).intersection(policy["services"]):
    raise ValueError("Default Compose configuration activates a GPU service")
print("PASS: Compose include, default profiles and GPU policy")
