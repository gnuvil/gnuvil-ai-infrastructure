.DEFAULT_GOAL := help
SHELL := /bin/bash

PYTHON ?= python3
ANSIBLE_PLAYBOOK ?= $(or $(wildcard /opt/gnuvil-ansible/bin/ansible-playbook),ansible-playbook)
export SERVICE CONFIRM

.PHONY: help bootstrap configure plan validate build switch gpu-off status

help:
	@printf '%s\n' \
		'make validate                      YAML, scripts, Compose, available Ansible checks' \
		'sudo make bootstrap                Install Ansible on the AI VM' \
		'sudo make configure                Configure the AI VM' \
		'sudo make plan                     Preview AI VM changes (--check --diff)' \
		'sudo make build SERVICE=gpu-smoke  Build the CUDA test image' \
		'sudo make switch SERVICE=gpu-smoke Run the CUDA test under the GPU lock' \
		'sudo make gpu-off                  Check that the GPU is idle' \
		'sudo make status                   Inspect container and GPU status'

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
