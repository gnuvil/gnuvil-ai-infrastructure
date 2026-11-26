.DEFAULT_GOAL := help
SHELL := /bin/bash

PYTHON ?= python3
ANSIBLE_PLAYBOOK ?= $(or $(wildcard /opt/gnuvil-ansible/bin/ansible-playbook),ansible-playbook)
export SERVICE CONFIRM

.PHONY: portal-access-pull portal-access-up portal-access-down help bootstrap configure plan validate test build switch gpu-off status portal-build portal-up portal-down portal-logs

help:
	@printf '%s\n' \
		'make validate                      YAML, scripts, Compose, available Ansible checks' \
		'sudo make bootstrap                Install Ansible on the AI VM' \
		'sudo make configure                Configure the AI VM' \
		'sudo make plan                     Preview AI VM changes (--check --diff)' \
		'sudo make build SERVICE=gpu-smoke  Build the CUDA test image' \
		'sudo make switch SERVICE=gpu-smoke Run the CUDA test under the GPU lock' \
		'sudo make gpu-off                  Check that the GPU is idle' \
		'sudo make status                   Inspect container and GPU status' \
		'make test                         Run portal/controller tests without Docker or GPU' \
		'sudo make portal-build             Build the CPU-only portal image' \
		'sudo make portal-up                Start the portal (authenticated proxy required)' \
		'sudo make portal-down              Stop only the portal' \
		'sudo make portal-logs              Read portal logs' \
		'sudo make portal-access-pull        Fetch the pinned LAN auth proxy' \
		'sudo make portal-access-up          Start the optional HTTPS LAN proxy' \
		'sudo make portal-access-down        Stop only the LAN proxy'

bootstrap:
	./bootstrap.sh

configure:
	cd ansible && "$(ANSIBLE_PLAYBOOK)" playbook.yaml

plan:
	cd ansible && "$(ANSIBLE_PLAYBOOK)" playbook.yaml --check --diff

validate:
	bash -n bootstrap.sh
	PYTHONDONTWRITEBYTECODE=1 "$(PYTHON)" scripts/validate.py
	@if command -v "$(ANSIBLE_PLAYBOOK)" >/dev/null; then \
		(cd ansible && "$(ANSIBLE_PLAYBOOK)" playbook.yaml --syntax-check) || exit $$?; \
	else printf '%s\n' 'SKIP: ansible-playbook is unavailable'; fi
	@if command -v ansible-lint >/dev/null; then \
		(cd ansible && ansible-lint --offline playbook.yaml) || exit $$?; \
	else printf '%s\n' 'SKIP: ansible-lint is unavailable'; fi

build:
	"$(PYTHON)" scripts/gpu-switch.py build "$${SERVICE}"

switch:
	@args=(); if [[ "$${CONFIRM:-0}" == 1 ]]; then args+=(--confirm-stop); fi; \
	"$(PYTHON)" scripts/gpu-switch.py switch "$${SERVICE}" "$${args[@]}"

gpu-off:
	@args=(); if [[ "$${CONFIRM:-0}" == 1 ]]; then args+=(--confirm-stop); fi; \
	"$(PYTHON)" scripts/gpu-switch.py off "$${args[@]}"

status:
	"$(PYTHON)" scripts/gpu-switch.py status

# On a development machine: uv sync --project services/portal --locked, then
# make validate test PYTHON=services/portal/.venv/bin/python
# On the VM use the root-only proxy environment; never put the token in Git.
PORTAL_ENV ?= /etc/gnuvil-ai/portal.env

portal-build:
	docker compose --env-file "$(PORTAL_ENV)" build portal

portal-up:
	docker compose --env-file "$(PORTAL_ENV)" up --detach --no-deps --no-build --pull never portal

portal-down:
	docker compose --env-file "$(PORTAL_ENV)" stop portal

portal-logs:
	docker compose --env-file "$(PORTAL_ENV)" logs --tail 100 portal

test:
	PYTHONDONTWRITEBYTECODE=1 "$(PYTHON)" -m unittest discover -s tests -v

portal-access-pull:
	docker compose --env-file "$(PORTAL_ENV)" --profile portal-access pull --policy always portal-proxy

portal-access-up:
	docker compose --env-file "$(PORTAL_ENV)" --profile portal-access up --detach --no-deps --no-build --pull never portal-proxy

portal-access-down:
	docker compose --env-file "$(PORTAL_ENV)" --profile portal-access stop portal-proxy
