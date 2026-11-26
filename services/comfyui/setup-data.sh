#!/bin/sh
set -eu
[ "$(id -u)" -eq 0 ] || { echo 'Run with sudo' >&2; exit 1; }
[ "$#" -eq 1 ] && [ -f "$1" ] || { echo 'Usage: sudo sh setup-data.sh MODEL_FILE' >&2; exit 1; }
expected=ea349eeae87ca8d25ba902c93810f7ca83e5c82f920edf12f273af004ae02819
printf '%s  %s\n' "$expected" "$1" | sha256sum -c -
base=/srv/ai-data
install -d -m 0750 -o root -g root "$base/models/comfyui"
install -d -m 0755 -o root -g root "$base/models/comfyui/checkpoints"
install -d -m 0750 -o 1000 -g 1000 "$base/outputs/comfyui" "$base/state/comfyui" "$base/state/comfyui/input" "$base/state/comfyui/user"
target="$base/models/comfyui/checkpoints/NoobAI-XL-Vpred-v1.0.safetensors"
if [ -e "$target" ]; then
    printf '%s  %s\n' "$expected" "$target" | sha256sum -c -
else
    install -m 0644 -o root -g root "$1" "$target"
    printf '%s  %s\n' "$expected" "$target" | sha256sum -c -
fi
