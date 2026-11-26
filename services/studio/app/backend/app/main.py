from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import CORS_ORIGINS, FRONTEND_DIST, OUTPUTS_DIR, UPLOADS_DIR
from app.services.db import init_db
from app.services import output_files, queue_service
from app.api import generate, models, img2img, inpaint, queue, controlnet, ipadapter, video, characters, qwen, monitor, gallery

app = FastAPI(title="Local Image AI Studio")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    init_db()
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    output_files.reconcile_outputs()
    queue_service.start_worker()


# StaticFiles checks the folders at import time, so create them before mounting (fresh server volumes).
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")

app.include_router(generate.router, prefix="/api")
app.include_router(models.router, prefix="/api")
app.include_router(img2img.router, prefix="/api")
app.include_router(inpaint.router, prefix="/api")
app.include_router(queue.router, prefix="/api")
app.include_router(controlnet.router, prefix="/api")
app.include_router(ipadapter.router, prefix="/api")
app.include_router(video.router, prefix="/api")
app.include_router(characters.router, prefix="/api")
app.include_router(qwen.router, prefix="/api")
app.include_router(monitor.router, prefix="/api")
app.include_router(gallery.router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# The built frontend is served from the same origin as the API, so the browser needs no CORS.
# Mounted last so /api, /outputs and /uploads keep precedence.
if (FRONTEND_DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
