#!/bin/sh
set -eu
python3 - <<'PY'
import torch
if not torch.cuda.is_available() or 'sm_70' not in torch.cuda.get_arch_list():
    raise SystemExit('V100 CUDA support unavailable; check driver and PyTorch wheel')
print('CUDA:', torch.version.cuda, 'GPU:', torch.cuda.get_device_name(0), flush=True)
x = torch.ones((64, 64), device='cuda', dtype=torch.float16)
assert (x @ x)[0, 0].item() == 64
PY
exec python3 main.py --listen 0.0.0.0 --port 8188 \
    --disable-all-custom-nodes --disable-dynamic-vram \
    --whitelist-custom-nodes gnuvil_lifecycle ComfyUI-GGUF ComfyUI_IPAdapter_plus \
        ComfyUI-Impact-Pack ComfyUI-Impact-Subpack comfyui_controlnet_aux \
    --output-directory /data/output --input-directory /data/input \
    --user-directory /data/user
