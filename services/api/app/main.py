from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import ensure_storage, init_db
from app.routers import feedback, files, history, parse, tasks, upload


def _ensure_demo_thumb(static_dir: Path) -> None:
    demo_thumb = static_dir / "demo" / "thumb.svg"
    if demo_thumb.exists():
        return
    demo_thumb.write_text(
        """<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"
viewBox="0 0 640 360">
<defs><linearGradient id="g" x1="0" x2="1"><stop stop-color="#ccfff0"/>
<stop offset="1" stop-color="#7cc9ff"/></linearGradient></defs>
<rect width="640" height="360" rx="36" fill="url(#g)"/>
<path d="M258 104l150 76-150 76z" fill="white" opacity=".92"/>
<text x="42" y="316" font-size="34" fill="#0f766e" font-family="Arial">GreenVideo AI Demo</text>
</svg>""",
        encoding="utf-8",
    )


def _opencv_available() -> bool:
    try:
        import cv2  # noqa: F401

        return True
    except Exception:
        return False


def _storage_ok() -> bool:
    try:
        ensure_storage()
        probe = settings.storage_dir / ".healthcheck"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def health_payload() -> dict:
    exe_dir = str(Path(sys.executable).parent)
    return {
        "ok": True,
        "service": "greenvideo-api",
        "demo_mode": os.getenv("GREENVIDEO_DEMO_MODE", "false").lower() == "true",
        "yt_dlp_available": bool(shutil.which("yt-dlp") or shutil.which("yt-dlp", path=exe_dir)),
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
        "opencv_available": _opencv_available(),
        "storage_ok": _storage_ok(),
    }


def create_app() -> FastAPI:
    ensure_storage()
    init_db()
    static_dir = settings.storage_dir / "static"
    _ensure_demo_thumb(static_dir)

    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.mount("/static", StaticFiles(directory=Path(static_dir)), name="static")
    app.include_router(parse.router)
    app.include_router(tasks.router)
    app.include_router(upload.router)
    app.include_router(history.router)
    app.include_router(feedback.router)
    app.include_router(files.router)

    @app.get("/api/health")
    def api_health() -> dict:
        return health_payload()

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", **health_payload()}

    # Serve the built H5 app same-origin. MUST be registered last: a "/" mount
    # matches every path and would shadow any route registered after it.
    dist_h5 = Path(__file__).resolve().parent.parent.parent.parent / "apps" / "miniapp" / "dist"
    if dist_h5.is_dir():
        app.mount("/", StaticFiles(directory=dist_h5, html=True), name="h5")

    return app


app = create_app()
