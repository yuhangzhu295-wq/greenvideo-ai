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
    # Only completed or successful tasks can hand back output files.
    # Processing, queued, failed, or canceled tasks must never provide downloads.
    if task.status not in {"completed", "success"}:
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
        ext = output_path.suffix.lower()
        is_audio = ext in {".mp3", ".m4a", ".aac", ".wav", ".ogg"}
        is_image = ext in {".jpg", ".jpeg", ".png"}

        if is_image:
            import cv2

            img = cv2.imread(str(output_path))
            if img is None or img.size == 0:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "OUTPUT_NOT_PLAYABLE",
                        "message": "输出图片损坏或无法解码，请重新执行处理任务。",
                    },
                )
        else:
            kind = "audio" if is_audio else "video"
            from app.services.video_repair import media_integrity_issues

            try:
                issues = media_integrity_issues(output_path, kind)
            except Exception:
                issues = ["无法校验输出文件完整性"]
            if issues:
                label = "音轨" if is_audio else "视频"
                raise HTTPException(
                    status_code=409,
                detail={
                    "code": "OUTPUT_NOT_PLAYABLE",
                    "message": (
                        f"输出文件的{label}不完整，请重新执行处理任务："
                        + "；".join(issues)
                    ),
                },
                )
    return FileResponse(output_path, filename=output_path.name)


@router.get("/files/{file_id}/tracking")
def get_tracking_preview_file(file_id: str) -> FileResponse:
    if "/" in file_id or "\\" in file_id or ".." in file_id:
        raise HTTPException(
            status_code=400,
            detail={"code": "UNSAFE_PATH", "message": "非法文件路径。"},
        )
    task = get_task(file_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "TASK_NOT_FOUND", "message": "任务不存在。"},
        )
    if task.status in {"failed", "canceled"}:
        raise HTTPException(
            status_code=409,
            detail={"code": "TASK_NOT_SUCCESSFUL", "message": "任务已失败或取消，不提供追踪预览。"},
        )
    from app.services.storage import storage_path

    preview_path = storage_path("files", f"tracking_{file_id}.mp4")
    if not preview_path.exists() or preview_path.stat().st_size == 0:
        raise HTTPException(
            status_code=404,
            detail={"code": "PREVIEW_NOT_READY", "message": "追踪预览视频尚未生成或不可用。"},
        )
    from app.services.video_repair import media_integrity_issues

    try:
        issues = media_integrity_issues(preview_path, "video")
    except Exception:
        issues = ["无法校验追踪预览视频完整性"]
    if issues:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "OUTPUT_NOT_PLAYABLE",
                "message": "追踪预览视频损坏或不完整：" + "；".join(issues),
            },
        )
    return FileResponse(preview_path, media_type="video/mp4", filename=f"tracking_{file_id}.mp4")


@router.get("/files/{file_id}/source")
def get_source_file(file_id: str) -> FileResponse:
    if "/" in file_id or "\\" in file_id or ".." in file_id:
        raise HTTPException(
            status_code=400,
            detail={"code": "UNSAFE_PATH", "message": "非法文件路径。"},
        )
    task = get_task(file_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "TASK_NOT_FOUND", "message": "任务不存在。"},
        )
    task_input = {}
    if getattr(task, "input_json", None):
        import json

        try:
            task_input = json.loads(task.input_json)
        except Exception:
            task_input = {}
    src_str = task_input.get("source_path")
    if not src_str:
        raise HTTPException(
            status_code=404,
            detail={"code": "SOURCE_NOT_FOUND", "message": "源视频文件不存在。"},
        )
    src_path = Path(src_str)
    if not src_path.exists() or src_path.stat().st_size == 0:
        raise HTTPException(
            status_code=404,
            detail={"code": "SOURCE_NOT_FOUND", "message": "源视频文件不存在。"},
        )
    media_type = "video/mp4" if src_path.suffix.lower() == ".mp4" else "video/quicktime"
    return FileResponse(src_path, media_type=media_type, filename=src_path.name)
