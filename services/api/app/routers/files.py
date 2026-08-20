from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.db import get_task
from app.services.security import SecurityError, safe_join_storage
from app.services.storage import create_demo_file

router = APIRouter(prefix="/api", tags=["files"])


@router.get("/files/{file_id}")
def get_file(file_id: str) -> FileResponse:
    if "/" in file_id or "\\" in file_id or ".." in file_id:
        raise HTTPException(
            status_code=400,
            detail={"code": "UNSAFE_PATH", "message": "非法文件路径。"},
        )

    task = get_task(file_id)
    if task and task.output_path:
        output_path = Path(task.output_path)
        if output_path.exists() and output_path.is_file():
            return FileResponse(output_path, filename=output_path.name)

    try:
        path = safe_join_storage(f"{file_id}.txt")
    except SecurityError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "UNSAFE_PATH", "message": str(exc)},
        ) from exc
    if not path.exists():
        path = create_demo_file(file_id)
    return FileResponse(path, filename=os.path.basename(path))
