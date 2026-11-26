#!/bin/sh
# Downloads the Qwen-Image (text-to-image) models used by Image AI Studio, pinned to a repository
# revision and checked by SHA-256, into the read-only ComfyUI model folders.
#   sudo sh services/studio/fetch-qwen-image.sh
# Resumes partial downloads. ~21.8 GB in total. Requires services/studio/setup-data.sh first.
set -eu
[ "$(id -u)" -eq 0 ] || { echo 'Run with sudo' >&2; exit 1; }
models=/srv/ai-data/models/comfyui
staging=/srv/ai-data/.model-download/qwen-image
[ -d "$models/diffusion_models" ] || { echo "Run services/studio/setup-data.sh first" >&2; exit 1; }
install -d -m 0750 -o root -g root "$staging"

# folder  file-name  url  sha256
# city96/Qwen-Image-gguf @ e77babc5 (Apache-2.0): Q4_K_S leaves ~4 GB of the V100's 16 GB for activations.
# Comfy-Org/Qwen-Image @ 1f12b17b (Apache-2.0): fp8 text encoder (cast to fp16 on V100) and the VAE.
fetch() {
    folder=$1 name=$2 url=$3 sum=$4
    target="$models/$folder/$name"
    if [ -f "$target" ] && printf '%s  %s\n' "$sum" "$target" | sha256sum -c --status -; then
        echo "OK (already installed) $folder/$name"; return
    fi
    part="$staging/$name.part"
    echo "Downloading $folder/$name"
    if command -v aria2c >/dev/null 2>&1; then
        # 16 parallel connections; resumes a .part left by either downloader
        aria2c -x 16 -s 16 -k 64M -c --file-allocation=none --console-log-level=warn --summary-interval=30 \
            -d "$staging" -o "$name.part" "$url"
    else
        curl -fL --retry 5 --retry-delay 10 -C - -o "$part" "$url"
    fi
    printf '%s  %s\n' "$sum" "$part" | sha256sum -c -
    install -m 0644 -o root -g root "$part" "$target"
    rm -f "$part"
    echo "Installed $folder/$name"
}

fetch diffusion_models qwen-image-Q4_K_S.gguf \
    https://huggingface.co/city96/Qwen-Image-gguf/resolve/e77babc55af111419e1714a7a0a848b9cac25db7/qwen-image-Q4_K_S.gguf \
    118ca698a0c0b77002a2ab00d4b1b7c79e2b4053210748740d0ca1fffc47fab0
fetch text_encoders qwen_2.5_vl_7b_fp8_scaled.safetensors \
    https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/1f12b17be14c89b026c51a91d67c32f84bb047bc/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors \
    cb5636d852a0ea6a9075ab1bef496c0db7aef13c02350571e388aea959c5c0b4
fetch vae qwen_image_vae.safetensors \
    https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/1f12b17be14c89b026c51a91d67c32f84bb047bc/split_files/vae/qwen_image_vae.safetensors \
    a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f
echo "Done. Restart ComfyUI (portal or make switch) so it lists the new files."
