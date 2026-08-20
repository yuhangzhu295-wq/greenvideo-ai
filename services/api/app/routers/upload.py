from __future__ import annotations

from fastapi import APIRouter

from app.config import settings

router = APIRouter(prefix="/api", tags=["upload"])


@router.get("/upload/limits")
def upload_limits() -> dict:
    return {
        "formats": ["MP4", "MOV", "AVI", "JPG", "JPEG", "PNG"],
        "max_size_mb": settings.max_upload_mb,
        "max_duration_minutes": settings.max_video_minutes,
        "compliance": "仅处理用户自有或已授权素材；默认不移除正常字幕。",
    }
