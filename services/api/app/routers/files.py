from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.db import get_task

router = APIRouter(prefix="/api", tags=["files"])


@router.get("/files/{file_id}")
def get_file(file_id: str) -> FileResponse:
    if "/" in file_id or "\\" in file_id or ".." in file_id:
        raise HTTPException(
            status_code=400,
            detail={"code": "UNSAFE_PATH", "message": "非法文件路径。"},
        )

    task = get_task(file_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "TASK_NOT_FOUND", "message": "任务不存在，无法提供下载文件。"},
        )
    # A failed or canceled task must not hand back a file: the output of a
    # failed repair can be the untouched input, which would look like a
    # successful download to the user.
    if task.status in {"failed", "canceled"}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TASK_NOT_SUCCESSFUL",
                "message": "任务未成功完成，不提供下载。",
            },
        )
    if not task.output_path:
        raise HTTPException(
            status_code=404,
            detail={"code": "OUTPUT_NOT_READY", "message": "任务尚未生成输出文件。"},
        )

    output_path = Path(task.output_path)
    if not output_path.exists() or not output_path.is_file():
        raise HTTPException(
            status_code=404,
            detail={"code": "OUTPUT_MISSING", "message": "输出文件缺失，请重新执行处理任务。"},
        )
    # A completed task must hand back real media. An empty file, or one with no
    # video stream, means the pipeline produced nothing playable — serving it
    # would look like a successful download.
    if output_path.stat().st_size == 0:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "OUTPUT_EMPTY",
                "message": "输出文件为空，未生成可播放的视频，请重新执行处理任务。",
            },
        )
    if task.type in {"watermark", "download"}:
        # Audio-only downloads (mp3) legitimately have no video frames, so the
        # completeness check has to match the kind of file that was produced.
        # Both branches verify decodable data rather than the header alone: a
        # track that only exists in the container index is not a playable file,
        # and a truncated file keeps advertising its original frame count.
        audio_only = output_path.suffix.lower() in {".mp3", ".m4a", ".aac", ".wav", ".ogg"}
        kind = "audio" if audio_only else "video"
        from app.services.video_repair import media_integrity_issues

        try:
            issues = media_integrity_issues(output_path, kind)
        except Exception:
            issues = ["无法校验输出文件完整性"]
        if issues:
            label = "音轨" if audio_only else "视频"
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "OUTPUT_NOT_PLAYABLE",
                    "message": f"输出文件的{label}不完整，请重新执行处理任务："
                    + "；".join(issues),
                },
            )
    return FileResponse(output_path, filename=output_path.name)
