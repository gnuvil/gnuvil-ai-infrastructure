import asyncio
from pathlib import Path

from PIL import Image

from app.core.comfyui_client import comfyui_client, ComfyUIUnavailableError
from app.core.config import OUTPUTS_DIR

POLL_INTERVAL_SECONDS = 1.0
POLL_TIMEOUT_SECONDS = 300


async def run_workflow_and_save(workflow: dict, timeout: float = POLL_TIMEOUT_SECONDS) -> Path:
    client_id = comfyui_client.new_client_id()
    prompt_id = await comfyui_client.queue_prompt(workflow, client_id)

    elapsed = 0.0
    while elapsed < timeout:
        history = await comfyui_client.get_history(prompt_id)
        entry = history.get(prompt_id)
        if entry:
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                messages = status.get("messages", [])
                raise ComfyUIUnavailableError(f"ComfyUI generation failed: {messages}")

            outputs = entry.get("outputs", {})
            for node_output in outputs.values():
                images = node_output.get("images")
                if images:
                    image_info = images[0]
                    image_bytes = await comfyui_client.get_image_bytes(
                        image_info["filename"], image_info["subfolder"], image_info["type"]
                    )
                    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
                    out_path = OUTPUTS_DIR / image_info["filename"]
                    out_path.write_bytes(image_bytes)
                    return out_path

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS

    raise TimeoutError(f"ComfyUI generation timed out after {timeout}s (prompt_id={prompt_id})")


def get_image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as img:
        return img.width, img.height
