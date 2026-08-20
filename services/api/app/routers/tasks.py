from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import ValidationError

from app.config import settings
from app.db import (
    delete_task,
    display_now,
    get_parse_result,
    get_task,
    insert_history,
    insert_task,
    update_history_by_task,
    update_task,
)
from app.schemas import (
    DownloadTaskRequest,
    MaskRegion,
    TaskCreateResponse,
    TaskStatusDTO,
    WatermarkTaskRequest,
)
from app.services.storage import new_id, output_name, safe_upload_name, storage_path
from app.services.watermark import WatermarkProcessor, is_video_path

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

ALLOWED_UPLOAD_EXT = {".mp4", ".mov", ".avi", ".jpg", ".jpeg", ".png"}
IMAGE_EXT = {".jpg", ".jpeg", ".png"}
VIDEO_EXT = {".mp4", ".mov", ".avi"}
processor = WatermarkProcessor()


def _eta(progress: int) -> int:
    return max(0, int((100 - min(progress, 100)) * 0.74))


def _size_mb(path: Path | None, fallback: float = 0.0) -> float:
    if path and path.exists():
        return round(path.stat().st_size / 1024 / 1024, 2)
    return fallback


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _ensure_authorized(payload: WatermarkTaskRequest) -> None:
    if not payload.confirm_authorized:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "AUTHORIZATION_REQUIRED",
                "message": "请先确认该素材为本人自有或已获得授权，仅用于合法修复与个人学习。",
            },
        )


def _safe_stage(task_id: str, progress: int, stage: str) -> None:
    task = get_task(task_id)
    if task is None or task.status == "canceled":
        return
    update_task(task_id, status="processing", progress=progress, stage=stage)


def _write_demo_result(path: Path, title: str, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"GreenVideo AI {title}\n"
        f"生成时间: {datetime.now(UTC).isoformat()}\n"
        f"任务参数: {json.dumps(payload, ensure_ascii=False)}\n"
        "说明: 当前环境没有执行真实平台下载，已生成 demo/fallback 结果文件。\n",
        encoding="utf-8",
    )


async def _run_download_task(task_id: str, payload: DownloadTaskRequest) -> None:
    try:
        _safe_stage(task_id, 12, "读取解析结果")
        await asyncio.sleep(0.05)
        parsed = get_parse_result(payload.parse_id)
        ext = "mp3" if payload.format_id.lower() == "mp3" else "mp4"
        output = storage_path("files", output_name(task_id, ext))

        _safe_stage(task_id, 45, "生成下载文件")
        await asyncio.sleep(0.05)
        title = parsed.title if parsed else "演示视频"
        _write_demo_result(
            output,
            "download result",
            {
                "parse_id": payload.parse_id,
                "format_id": payload.format_id,
                "title": title,
                "is_demo": True,
            },
        )

        update_task(
            task_id,
            status="completed",
            progress=100,
            stage="处理完成",
            output_path=str(output),
        )
        update_history_by_task(task_id, status="success", size_mb=_size_mb(output, 0.01))
    except Exception as exc:
        update_task(task_id, status="failed", stage="处理失败", error=str(exc))
        update_history_by_task(task_id, status="failed")


@router.post("/download", response_model=TaskCreateResponse)
async def create_download_task(
    payload: DownloadTaskRequest,
    background_tasks: BackgroundTasks,
) -> TaskCreateResponse:
    task_id = new_id("task")
    parsed = get_parse_result(payload.parse_id)
    title = parsed.title if parsed else "高清视频 1080P"
    insert_task(
        {
            "id": task_id,
            "type": "download",
            "status": "queued",
            "progress": 0,
            "stage": "等待下载",
            "input": payload.model_dump(),
        }
    )
    insert_history(
        {
            "id": new_id("hist"),
            "task_id": task_id,
            "title": title,
            "kind": "视频下载",
            "thumbnail": "linear-gradient(135deg,#7cc9ff,#0f7edb)",
            "status": "processing",
            "size_mb": 0.0,
            "created_at": display_now(),
        }
    )
    background_tasks.add_task(_run_download_task, task_id, payload)
    return TaskCreateResponse(task_id=task_id, status="queued")


async def _save_upload(file_obj: Any) -> Path:
    filename = file_obj.filename or "upload.bin"
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_EXT:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "UNSUPPORTED_FILE_TYPE",
                "message": "仅支持 MP4/MOV/AVI/JPG/JPEG/PNG 文件。",
            },
        )

    uploaded_path = storage_path("uploads", safe_upload_name(filename))
    total = 0
    try:
        with uploaded_path.open("wb") as output:
            while True:
                chunk = await file_obj.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > settings.max_upload_mb * 1024 * 1024:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "code": "FILE_TOO_LARGE",
                            "message": f"文件大小不能超过 {settings.max_upload_mb}MB。",
                        },
                    )
                output.write(chunk)
    except Exception:
        uploaded_path.unlink(missing_ok=True)
        raise
    return uploaded_path


def _parse_mask_regions(raw: Any) -> list[MaskRegion]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [MaskRegion.model_validate(item) for item in raw]
    try:
        data = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_MASK_REGIONS", "message": "mask_regions 必须是 JSON 数组。"},
        ) from exc
    return [MaskRegion.model_validate(item) for item in data]


async def _load_watermark_payload(request: Request) -> tuple[WatermarkTaskRequest, Path | None]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
        try:
            return WatermarkTaskRequest.model_validate(data), None
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc

    form = await request.form()
    file_obj = form.get("file")
    uploaded_path = None
    if file_obj is not None and hasattr(file_obj, "read"):
        uploaded_path = await _save_upload(file_obj)

    mode_value = str(form.get("repair_mode") or form.get("mode") or "opencv")
    if mode_value == "standard":
        mode_value = "opencv"
    try:
        payload = WatermarkTaskRequest(
            source_task_id=str(form.get("source_task_id") or "") or None,
            parse_id=str(form.get("parse_id") or "") or None,
            video_url=str(form.get("video_url") or "") or None,
            mask_regions=_parse_mask_regions(form.get("mask_regions")),
            mask_preset=str(form.get("mask_preset") or "douyin_both_corners"),
            repair_mode=mode_value,
            remove_subtitle=_parse_bool(form.get("remove_subtitle")),
            confirm_authorized=_parse_bool(form.get("confirm_authorized")),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    return payload, uploaded_path


def _resolve_source_path(payload: WatermarkTaskRequest, uploaded_path: Path | None) -> Path | None:
    if uploaded_path:
        return uploaded_path
    if payload.source_task_id:
        source_task = get_task(payload.source_task_id)
        if source_task and source_task.output_path:
            source_path = Path(source_task.output_path)
            if source_path.exists():
                return source_path
    return None


async def _run_watermark_task(
    task_id: str,
    payload: WatermarkTaskRequest,
    source_path: Path | None,
) -> None:
    try:
        _safe_stage(task_id, 8, "准备素材")
        await asyncio.sleep(0.05)
        suffix = source_path.suffix.lower() if source_path else ".txt"
        output_suffix = ".mp4" if suffix in VIDEO_EXT else suffix if suffix in IMAGE_EXT else ".txt"
        output = storage_path("files", output_name(task_id, output_suffix))

        if source_path and suffix in IMAGE_EXT:
            _safe_stage(task_id, 20, "OpenCV 图片修复中")
            await asyncio.to_thread(
                processor.process_image,
                source_path,
                payload.mask_regions,
                output,
                payload.repair_mode,
                payload.mask_preset,
                payload.remove_subtitle,
            )
        elif source_path and suffix in VIDEO_EXT and is_video_path(source_path):
            def progress(progress: int, stage: str) -> None:
                _safe_stage(task_id, progress, stage)

            await asyncio.to_thread(
                processor.process_video,
                source_path,
                payload.mask_regions,
                output,
                progress,
                payload.repair_mode,
                payload.mask_preset,
                payload.remove_subtitle,
            )
        else:
            _safe_stage(task_id, 62, "生成演示修复结果")
            _write_demo_result(output, "watermark result", payload.model_dump())

        update_task(
            task_id,
            status="completed",
            progress=100,
            stage="处理完成",
            output_path=str(output),
        )
        update_history_by_task(task_id, status="success", size_mb=_size_mb(output, 0.01))
    except Exception as exc:
        update_task(task_id, status="failed", stage="处理失败", error=str(exc))
        update_history_by_task(task_id, status="failed")


@router.post("/watermark", response_model=TaskCreateResponse)
async def create_watermark_task(
    request: Request,
    background_tasks: BackgroundTasks,
) -> TaskCreateResponse:
    payload, uploaded_path = await _load_watermark_payload(request)
    _ensure_authorized(payload)
    source_path = _resolve_source_path(payload, uploaded_path)

    task_id = new_id("task")
    insert_task(
        {
            "id": task_id,
            "type": "watermark",
            "status": "queued",
            "progress": 0,
            "stage": "等待去水印处理",
            "input": {
                **payload.model_dump(),
                "uploaded_path": str(uploaded_path) if uploaded_path else None,
                "source_path": str(source_path) if source_path else None,
            },
        }
    )
    insert_history(
        {
            "id": new_id("hist"),
            "task_id": task_id,
            "title": "AI 视频去水印",
            "kind": "视频去水印",
            "thumbnail": "linear-gradient(135deg,#dff9f1,#9ddcff)",
            "status": "processing",
            "size_mb": _size_mb(uploaded_path),
            "created_at": display_now(),
        }
    )
    background_tasks.add_task(_run_watermark_task, task_id, payload, source_path)
    return TaskCreateResponse(task_id=task_id, status="queued")


@router.get("/{task_id}", response_model=TaskStatusDTO)
def get_task_status(task_id: str) -> TaskStatusDTO:
    task = get_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "任务不存在。"},
        )

    progress = int(task.progress)
    status = task.status
    if progress >= 100 and status not in {"failed", "canceled"}:
        status = "completed"

    download_url = None
    if status in {"completed", "success"}:
        download_url = f"/api/files/{task_id}"

    return TaskStatusDTO(
        task_id=task.id,
        type=task.type,
        status=status,
        progress=progress,
        stage=task.stage,
        eta_seconds=_eta(progress),
        download_url=download_url,
        error=task.error,
    )


@router.post("/{task_id}/cancel")
def cancel_task(task_id: str) -> dict:
    task = get_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "任务不存在。"},
        )
    if task.status not in {"completed", "success", "failed"}:
        update_task(task_id, status="canceled", error="用户已取消", stage="已取消")
        update_history_by_task(task_id, status="failed")
    return {"ok": True}


@router.delete("/{task_id}")
def remove_task(task_id: str) -> dict:
    if not delete_task(task_id):
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "任务不存在。"},
        )
    return {"ok": True}
