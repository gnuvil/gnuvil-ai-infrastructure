import asyncio
import subprocess
from dataclasses import dataclass

from app.core.comfyui_client import comfyui_client

# Serializes this app's own diffusion jobs. GPU sharing with other server services (llama.cpp, ...)
# is not handled here: the server's GPU controller runs one GPU service at a time, and ComfyUI's
# gnuvil_lifecycle extension blocks new prompts while it is draining for a switch.
gpu_lock = asyncio.Lock()

VRAM_RELEASE_TARGET_MB = 3000
VRAM_RELEASE_TIMEOUT_SECONDS = 10.0


@dataclass
class VramStatus:
    used_mb: float
    total_mb: float
    name: str = ""
    utilization_percent: float | None = None
    temperature_c: float | None = None

    @property
    def free_mb(self) -> float:
        return self.total_mb - self.used_mb


def _read_nvidia_smi() -> VramStatus | None:
    """Only works where the backend itself sees the GPU (single-PC setup)."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        name, used, total, util, temp = [x.strip() for x in result.stdout.strip().splitlines()[0].split(",")]
        return VramStatus(float(used), float(total), name, float(util), float(temp))
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, IndexError):
        return None


async def read_vram() -> VramStatus | None:
    """GPU memory as ComfyUI sees it (works when ComfyUI runs in another container); nvidia-smi as fallback."""
    stats = await comfyui_client.get_system_stats()
    devices = (stats or {}).get("devices") or []
    gpu = next((d for d in devices if d.get("type") == "cuda"), devices[0] if devices else None)
    if gpu and gpu.get("vram_total"):
        total = gpu["vram_total"] / (1024 * 1024)
        free = gpu.get("vram_free", 0) / (1024 * 1024)
        return VramStatus(used_mb=total - free, total_mb=total, name=str(gpu.get("name", "")))
    return await asyncio.to_thread(_read_nvidia_smi)


async def release_diffusion_vram() -> VramStatus | None:
    """Asks ComfyUI to unload its models and waits until VRAM actually drops."""
    await comfyui_client.free_memory()
    waited = 0.0
    status = await read_vram()
    while status and status.used_mb > VRAM_RELEASE_TARGET_MB and waited < VRAM_RELEASE_TIMEOUT_SECONDS:
        await asyncio.sleep(0.5)
        waited += 0.5
        status = await read_vram()
    return status
