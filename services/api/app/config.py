from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_name: str = "GreenVideo AI"
    database_url: str = os.getenv("GREENVIDEO_DATABASE_URL", "sqlite:///./storage/greenvideo.db")
    storage_dir: Path = Path(os.getenv("GREENVIDEO_STORAGE_DIR", "./storage"))
    celery_broker_url: str = os.getenv("GREENVIDEO_CELERY_BROKER_URL", "redis://localhost:6379/0")
    celery_result_backend: str = os.getenv(
        "GREENVIDEO_CELERY_RESULT_BACKEND", "redis://localhost:6379/1"
    )
    max_upload_mb: int = 500
    max_video_minutes: int = 30
    iopaint_enabled: bool = os.getenv("GREENVIDEO_IOPAINT_ENABLED", "true").lower() == "true"
    iopaint_base_url: str = os.getenv("GREENVIDEO_IOPAINT_BASE_URL", "http://127.0.0.1:8080")
    iopaint_model: str = os.getenv("GREENVIDEO_IOPAINT_MODEL", "lama")

    @property
    def database_path(self) -> Path:
        if self.database_url.startswith("sqlite:///"):
            return Path(self.database_url.removeprefix("sqlite:///"))
        raise ValueError("Only sqlite:/// URLs are supported by the local demo backend.")


settings = Settings()
