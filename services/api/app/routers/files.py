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
        # playability check has to match the kind of file that was produced.
        audio_only = output_path.suffix.lower() in {".mp3", ".m4a", ".aac", ".wav", ".ogg"}
        if audio_only:
            from app.services.watermark import _audio_stream_status

            if _audio_stream_status(output_path) != "present":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "OUTPUT_NOT_PLAYABLE",
                        "message": "输出文件没有可解码音轨，请重新执行处理任务。",
                    },
                )
        else:
            from app.services.video_repair import count_decoded_frames

            try:
                decoded = count_decoded_frames(output_path)
            except Exception:
                decoded = None
            # A header-only MP4 has a video stream and zero frames: it is not
            # playable, so it must not be handed back as a finished result.
            if decoded is None or decoded <= 0:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "OUTPUT_NOT_PLAYABLE",
                        "message": "输出文件没有可解码的视频帧，请重新执行处理任务。",
                    },
                )
    return FileResponse(output_path, filename=output_path.name)
