#!/bin/sh
# Prepares host folders for the Local Image AI Studio and the extra ComfyUI model folders it uses.
# Creates missing folders only; never changes ownership of existing data recursively.
set -eu
[ "$(id -u)" -eq 0 ] || { echo 'Run with sudo' >&2; exit 1; }
base=/srv/ai-data
[ -d "$base/models/comfyui" ] || { echo "Run services/comfyui/setup-data.sh first" >&2; exit 1; }

# Model folders (read-only for the containers; copy model files in as root)
for d in loras vae controlnet ipadapter clip_vision ultralytics ultralytics/bbox \
         diffusion_models text_encoders frame_interpolation; do
    install -d -m 0755 -o root -g root "$base/models/comfyui/$d"
done

# ComfyUI-writable: DWPose weights downloaded on first use
install -d -m 0750 -o 1000 -g 1000 "$base/state/comfyui/controlnet_aux"

# Studio backend data: SQLite DB, uploads, tag CSV, generated results
install -d -m 0750 -o 1000 -g 1000 "$base/state/studio" "$base/state/studio/uploads" "$base/outputs/studio"

# YOLO detector files that Impact Subpack may load with weights_only=False must be listed here.
whitelist_dir="$base/state/comfyui/user/default/ComfyUI-Impact-Subpack"
install -d -m 0750 -o 1000 -g 1000 "$base/state/comfyui/user/default" "$whitelist_dir"
if [ ! -e "$whitelist_dir/model-whitelist.txt" ]; then
    : > "$whitelist_dir/model-whitelist.txt"
    chown 1000:1000 "$whitelist_dir/model-whitelist.txt"
fi
echo "OK. Copy models into $base/models/comfyui/<folder>/ and list trusted .pt detectors in"
echo "   $whitelist_dir/model-whitelist.txt"
