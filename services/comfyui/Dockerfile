FROM nvidia/cuda:12.9.1-base-ubuntu24.04@sha256:5d2e53778e2180e01676aa8bac1aada242e95230ec97e21ecfb33de4e27cd1df

ENV DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_PROJECT_ENVIRONMENT=/opt/comfy-venv UV_LINK_MODE=copy \
    PATH=/opt/comfy-venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git python3 python3-venv python3-pip libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/uv-bootstrap && /opt/uv-bootstrap/bin/pip install uv==0.10.9
WORKDIR /opt/gnuvil-comfyui
COPY pyproject.toml uv.lock ./
RUN /opt/uv-bootstrap/bin/uv sync --locked --no-install-project --no-dev \
    && rm -rf /root/.cache/uv
RUN git init /opt/ComfyUI && git -C /opt/ComfyUI remote add origin https://github.com/Comfy-Org/ComfyUI.git \
    && git -C /opt/ComfyUI fetch --depth 1 origin 0d0e105153c02261519a6a552c52ec1cd5d9855e \
    && git -C /opt/ComfyUI checkout --detach FETCH_HEAD \
    && rm -rf /opt/ComfyUI/.git
# Custom nodes for the Local Image AI Studio, pinned like ComfyUI itself. Their Python
# dependencies live in pyproject.toml/uv.lock; their own install.py scripts are not run.
RUN set -eu; cd /opt/ComfyUI/custom_nodes; \
    for spec in \
        city96/ComfyUI-GGUF@6ea2651e7df66d7585f6ffee804b20e92fb38b8a \
        cubiq/ComfyUI_IPAdapter_plus@a0f451a5113cf9becb0847b92884cb10cbdec0ef \
        ltdrdata/ComfyUI-Impact-Pack@c4539f99df15cc94d772aeb933ae599c8117e20f \
        ltdrdata/ComfyUI-Impact-Subpack@b4bf952cde06df01eb3deff27d6dc6a9fc9d464a \
        Fannovel16/comfyui_controlnet_aux@c2655d2970d810d8a3d71deed204cdf416b66132; do \
        repo=${spec%@*}; sha=${spec#*@}; name=${repo#*/}; \
        git init -q "$name"; git -C "$name" remote add origin "https://github.com/$repo.git"; \
        git -C "$name" fetch -q --depth 1 origin "$sha"; git -C "$name" checkout -q --detach FETCH_HEAD; \
        rm -rf "$name/.git"; \
    done; \
    chown -R 1000:1000 /opt/ComfyUI
COPY entrypoint.sh /usr/local/bin/gnuvil-comfyui
USER 1000:1000
WORKDIR /opt/ComfyUI
EXPOSE 8188
ENTRYPOINT ["/bin/sh", "/usr/local/bin/gnuvil-comfyui"]
