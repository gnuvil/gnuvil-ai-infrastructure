#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID != 0 ]]; then
    printf 'Run on the AI VM with sudo make bootstrap.\n' >&2
    exit 1
fi
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 || ! -r /etc/os-release ]]; then
    printf 'Bootstrap requires Ubuntu Server 24.04 on amd64.\n' >&2
    exit 1
fi
# shellcheck disable=SC1091
. /etc/os-release
if [[ ${ID:-} != ubuntu || ${VERSION_ID:-} != 24.04 ]]; then
    printf 'Bootstrap requires Ubuntu Server 24.04 on amd64.\n' >&2
    exit 1
fi

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/ansible"
apt-get update
apt-get install -y --no-install-recommends python3-apt python3-venv ca-certificates
venv=/opt/gnuvil-ansible
/usr/bin/python3 -m venv "$venv"
"$venv/bin/python" -m pip install --disable-pip-version-check --requirement bootstrap-requirements.txt
"$venv/bin/ansible-galaxy" collection install     --requirements-file requirements.yaml --collections-path collections
