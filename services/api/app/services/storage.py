from __future__ import annotations

import re
import secrets
from pathlib import Path

from app.config import settings
from app.db import ensure_storage


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def storage_path(*parts: str) -> Path:
    ensure_storage()
    path = settings.storage_dir.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def create_demo_file(file_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", file_id)[:120] or "demo"
    path = storage_path("files", f"{safe_id}.txt")
    if not path.exists():
        path.write_text(
            "GreenVideo AI demo result file.\n真实生产环境会在这里返回任务生成的签名下载文件。\n",
            encoding="utf-8",
        )
    return path


def safe_upload_name(original_name: str) -> str:
    suffix = Path(original_name).suffix.lower()
    return f"{new_id('upload')}{suffix}"


def output_name(task_id: str, suffix: str) -> str:
    clean_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    return f"{task_id}{clean_suffix.lower()}"
