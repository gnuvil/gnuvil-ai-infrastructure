import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _path(env: str, default: Path) -> Path:
    value = os.environ.get(env)
    return Path(value) if value else default


# Defaults keep the old single-PC layout; the server image overrides them with environment variables.
COMFYUI_HOST = os.environ.get("COMFYUI_HOST", "127.0.0.1")
COMFYUI_PORT = int(os.environ.get("COMFYUI_PORT", "8188"))
COMFYUI_BASE_URL = f"http://{COMFYUI_HOST}:{COMFYUI_PORT}"
COMFYUI_WS_URL = f"ws://{COMFYUI_HOST}:{COMFYUI_PORT}/ws"

MODELS_DIR = _path("MODELS_DIR", PROJECT_ROOT / "models")
LORAS_DIR = _path("LORAS_DIR", MODELS_DIR / "loras")

WORKFLOWS_DIR = _path("WORKFLOWS_DIR", PROJECT_ROOT / "workflows")
OUTPUTS_DIR = _path("OUTPUTS_DIR", PROJECT_ROOT / "outputs")
# ComfyUI's own output folder, only used to recover result files missing from OUTPUTS_DIR.
COMFYUI_OUTPUT_DIR = _path("COMFYUI_OUTPUT_DIR", PROJECT_ROOT / "ComfyUI" / "output")
UPLOADS_DIR = _path("UPLOADS_DIR", PROJECT_ROOT / "uploads")
DATABASE_PATH = _path("DATABASE_PATH", PROJECT_ROOT / "database" / "app.db")
TAGS_CSV = _path("TAGS_CSV", PROJECT_ROOT / "data" / "danbooru_tags.csv")
# Built frontend (vite build output). Served at "/" when the folder exists, so one origin serves UI and API.
FRONTEND_DIST = _path("FRONTEND_DIST", PROJECT_ROOT / "frontend" / "dist")

CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if o.strip()
]
