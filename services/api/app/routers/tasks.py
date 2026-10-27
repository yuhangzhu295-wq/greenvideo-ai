from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
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
    TimedMaskRegion,
    WatermarkTaskRequest,
)
from app.services.storage import new_id, output_name, safe_upload_name, storage_path
from app.services.video_repair import AiServiceCancelled
from app.services.watermark import WatermarkProcessor, is_video_path

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

ALLOWED_UPLOAD_EXT = {".mp4", ".mov", ".avi", ".jpg", ".jpeg", ".png"}
IMAGE_EXT = {".jpg", ".jpeg", ".png"}
VIDEO_EXT = {".mp4", ".mov", ".avi"}
# A watermark is a small overlay. Anything covering more than this share of the
# frame is scene structure the detector mistook for one.
AUTO_DETECT_MAX_COVERAGE_PCT = 15.0
# Hand-picked regions get a looser ceiling than auto-detection (a user may
# legitimately select a wide band), but still bounded so a mistake cannot mask
# the whole frame.
MANUAL_MAX_COVERAGE_PCT = 60.0


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


def _stage_from_engine(engine: str, note: str, fallback: str) -> str:
    """Report the engine that actually ran, never implying an engine that did not."""
    note = note or ""
    if engine == "none":
        return f"未执行去水印：{note}" if note else "未执行去水印"
    if engine == "opencv" and "降级" in note:
        return note
    if engine == "iopaint-lama":
        return note or f"已使用 IOPaint LaMa 执行 AI 修复（引擎：{engine}）"
    return f"{fallback}（引擎：{engine}）{('；' + note) if note and note not in fallback else ''}"


def _timed_region_peak_coverage_pct(regions, width: int, height: int) -> float:
    """Largest share of the frame covered at any single point in time.

    Time-scoped regions apply to different windows, so the union of all of them
    would overstate the exposure. The peak matters because that is what a frame
    is actually repaired against.
    """
    if not regions or width <= 0 or height <= 0:
        return 0.0
    boundaries = sorted({r.start_s for r in regions}
                        | {r.end_s for r in regions if r.end_s is not None})
    peak = 0.0
    for t in boundaries:
        active = [
            r for r in regions
            if r.start_s <= t and (r.end_s is None or t < r.end_s)
        ]
        if not active:
            continue
        canvas = np.zeros((height, width), dtype=bool)
        for r in active:
            x1 = max(0, min(width, r.x))
            y1 = max(0, min(height, r.y))
            x2 = max(0, min(width, r.x + r.w))
            y2 = max(0, min(height, r.y + r.h))
            if x2 > x1 and y2 > y1:
                canvas[y1:y2, x1:x2] = True
        peak = max(peak, float(canvas.mean()))
    return 100.0 * peak

def _probe_frame_size(path: Path) -> tuple[int, int]:
    """(width, height) of a video's first stream, or (0, 0) when unknown."""
    try:
        import cv2

        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            cap.release()
            return 0, 0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        cap.release()
        return w, h
    except Exception:
        return 0, 0

def _region_coverage_pct(regions: list[MaskRegion], width: int = 0, height: int = 0) -> float:
    """Share of the frame covered by the union of the given regions."""
    if not regions:
        return 0.0
    if width <= 0 or height <= 0:
        max_x = max(r.x + r.w for r in regions)
        max_y = max(r.y + r.h for r in regions)
        width = max(width, max_x)
        height = max(height, max_y)
    if width <= 0 or height <= 0:
        return 0.0
    canvas = np.zeros((height, width), dtype=bool)
    for r in regions:
        x1 = max(0, min(width, r.x))
        y1 = max(0, min(height, r.y))
        x2 = max(0, min(width, r.x + r.w))
        y2 = max(0, min(height, r.y + r.h))
        if x2 > x1 and y2 > y1:
            canvas[y1:y2, x1:x2] = True
    return 100.0 * float(canvas.mean())

def _discard_output(output: Path | None) -> None:
    """Remove an unfinished output so a failed task never leaves a file behind."""
    if output is None:
        return
    try:
        Path(output).unlink(missing_ok=True)
    except OSError:
        pass


def _detect_source_watermark(source_path: Path):
    """Sample frames from the upload and locate persistent watermark regions."""
    import cv2

    from app.services.watermark_detect import detect_watermarks

    cap = cv2.VideoCapture(str(source_path))
    if not cap.isOpened():
        cap.release()
        from app.services.watermark_detect import DetectionResult

        return DetectionResult(False, "无法解码上传视频", [], None, None, 0, {})

    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    frames: list = []
    if total > 40:
        step = max(1, total // 30)
        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % step == 0 and len(frames) < 30:
                frames.append(frame)
            idx += 1
    else:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frames.append(frame)
    cap.release()

    if not frames:
        from app.services.watermark_detect import DetectionResult

        return DetectionResult(False, "视频中没有可解码帧", [], None, None, 0, {})
    return detect_watermarks(frames)


async def _run_download_task(task_id: str, payload: DownloadTaskRequest) -> None:
    """Download a public video/audio the user asked for, with yt-dlp.

    The result is only reported as completed when a real, decodable file was
    produced. Every refusal (login/DRM/rate limit/no yt-dlp) is surfaced as a
    business error, never as a demo file dressed up as a finished download.
    """
    from app.services.downloader import DownloadError, VideoDownloader

    output_dir = storage_path("files", f"dl_{task_id}")
    try:
        parsed = get_parse_result(payload.parse_id)
        if parsed is None:
            raise DownloadError(
                "PARSE_RESULT_EXPIRED", "解析结果不存在或已过期，请重新解析后再下载。"
            )

        _safe_stage(task_id, 20, "准备下载")
        downloader = VideoDownloader()

        def progress(pct: int, stage: str) -> None:
            _safe_stage(task_id, pct, stage)

        def canceled() -> bool:
            task = get_task(task_id)
            return bool(task and task.status == "canceled")

        produced = await asyncio.to_thread(
            downloader.download,
            parsed.url,
            payload.format_id,
            output_dir,
            progress,
            canceled,
        )

        # The produced file must be complete media, not a zero-byte, header-only
        # or truncated artifact that merely exists on disk. Truncation matters
        # here: a cut MP4 keeps its original header, so the advertised frame
        # count is compared against the packets actually present.
        from app.services.video_repair import media_integrity_issues

        ext = produced.suffix.lower()
        audio_only = ext in {".mp3", ".m4a", ".aac", ".wav", ".ogg"}
        issues = media_integrity_issues(produced, "audio" if audio_only else "video")
        if issues:
            label = "音频" if audio_only else "视频"
            raise DownloadError(
                "DOWNLOAD_INCOMPLETE",
                f"下载得到的{label}文件不完整，未提供下载结果：" + "；".join(issues),
            )

        # Re-read the task before publishing: a cancel that arrived while the
        # file was being verified must not be overwritten with "completed".
        current = get_task(task_id)
        if current is not None and current.status == "canceled":
            shutil.rmtree(output_dir, ignore_errors=True)
            return

        final = storage_path("files", output_name(task_id, ext.lstrip(".") or "mp4"))
        shutil.move(str(produced), str(final))
        shutil.rmtree(output_dir, ignore_errors=True)

        # Second check after the move: the window between the first check and
        # the status write is small but non-zero.
        current = get_task(task_id)
        if current is not None and current.status == "canceled":
            _discard_output(final)
            return

        update_task(
            task_id,
            status="completed",
            progress=100,
            stage="下载完成",
            output_path=str(final),
        )
        update_history_by_task(
            task_id, status="success", size_mb=_size_mb(final, 0.01)
        )
    except DownloadError as exc:
        update_task(
            task_id,
            status="failed",
            progress=100,
            stage="下载失败",
            error=f"[{exc.code}] {exc.message}",
        )
        update_history_by_task(task_id, status="failed")
        shutil.rmtree(output_dir, ignore_errors=True)
    except Exception as exc:
        update_task(task_id, status="failed", progress=100, stage="下载失败", error=str(exc))
        update_history_by_task(task_id, status="failed")
        shutil.rmtree(output_dir, ignore_errors=True)


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


def _parse_timed_mask_regions(raw: Any) -> list[TimedMaskRegion]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [TimedMaskRegion.model_validate(item) for item in raw]
    try:
        data = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_TIMED_MASK_REGIONS",
                    "message": "timed_mask_regions 必须是 JSON 数组。"},
        ) from exc
    return [TimedMaskRegion.model_validate(item) for item in data]


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
            timed_mask_regions=_parse_timed_mask_regions(form.get("timed_mask_regions")),
            mask_preset=str(form.get("mask_preset") or "douyin_both_corners"),
            repair_mode=mode_value,
            remove_subtitle=_parse_bool(form.get("remove_subtitle")),
            confirm_authorized=_parse_bool(form.get("confirm_authorized")),
            auto_detect=_parse_bool(form.get("auto_detect")),
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
    # A processor per task: the engine result is instance state, so sharing one
    # object between concurrent tasks let one task's failure be overwritten by
    # another's success before it was read.
    processor = WatermarkProcessor()
    try:
        _safe_stage(task_id, 8, "准备素材")
        await asyncio.sleep(0.05)
        suffix = source_path.suffix.lower() if source_path else ".txt"
        output_suffix = ".mp4" if suffix in VIDEO_EXT else suffix if suffix in IMAGE_EXT else ".txt"
        output = storage_path("files", output_name(task_id, output_suffix))

        # Auto-detection: locate the watermark before repairing. If the detector
        # cannot find a confident region the task fails instead of masking the
        # whole picture.
        mask_regions = list(payload.mask_regions)
        mask_preset = payload.mask_preset
        frame_width = frame_height = 0
        # Auto-detection and hand-picked time-scoped regions describe the same
        # thing (where to repair) in two different ways. Allowing both lets the
        # detector's result be gated while the submitted regions - which the
        # processor prefers - bypass that gate entirely.
        if payload.auto_detect and payload.timed_mask_regions:
            update_task(
                task_id,
                status="failed",
                progress=100,
                stage="参数冲突",
                error="自动检测与手动分时区域不能同时使用，请只选择其中一种。",
            )
            update_history_by_task(task_id, status="failed")
            return
        if payload.auto_detect and source_path and suffix in VIDEO_EXT:
            _safe_stage(task_id, 12, "正在检测水印区域")
            frame_width, frame_height = _probe_frame_size(source_path)
            detection = await asyncio.to_thread(
                _detect_source_watermark, source_path
            )
            if not detection.ok:
                update_task(
                    task_id,
                    status="failed",
                    progress=100,
                    stage="水印检测失败",
                    error=f"{detection.reason}（stats: {detection.stats}）",
                )
                update_history_by_task(task_id, status="failed")
                return
            mask_preset = "custom"
            mask_regions = [
                MaskRegion(x=det.box[0], y=det.box[1], w=det.box[2], h=det.box[3])
                for det in detection.detections
            ]
            if not mask_regions:
                update_task(
                    task_id,
                    status="failed",
                    progress=100,
                    stage="水印检测失败",
                    error=f"{detection.reason}（stats: {detection.stats}）",
                )
                update_history_by_task(task_id, status="failed")
                return

            # A detector that reports most of the frame has found scene structure,
            # not a watermark. Repairing that would destroy the picture, so the
            # task fails and asks for a manual region instead of proceeding.
            coverage_pct = _region_coverage_pct(mask_regions, frame_width, frame_height)
            if coverage_pct > AUTO_DETECT_MAX_COVERAGE_PCT:
                update_task(
                    task_id,
                    status="failed",
                    progress=100,
                    stage="水印检测结果不可信",
                    error=(
                        f"自动检测到的区域覆盖 {coverage_pct:.1f}% 画面，超过 "
                        f"{AUTO_DETECT_MAX_COVERAGE_PCT}% 可信上限，疑似误检。"
                        "请手动选择水印区域后重试。"
                    ),
                )
                update_history_by_task(task_id, status="failed")
                return

            _safe_stage(
                task_id,
                16,
                f"已自动检测到 {len(mask_regions)} 个水印区域"
                f"（覆盖 {coverage_pct:.1f}% 画面），开始修复",
            )
            # Record what the detector actually produced so the run can be
            # audited later instead of only showing the pre-detection payload.
            update_task(
                task_id,
                input={
                    **payload.model_dump(),
                    "auto_detect_regions": [r.model_dump() for r in mask_regions],
                    "source_path": str(source_path) if source_path else None,
                },
            )

        # The same sanity gate applies to hand-submitted time-scoped regions: a
        # region set covering most of the frame is a mistake, not a watermark,
        # and repairing it would destroy the picture.
        if payload.timed_mask_regions and source_path and suffix in VIDEO_EXT:
            if frame_width <= 0 or frame_height <= 0:
                frame_width, frame_height = _probe_frame_size(source_path)
            timed_coverage = _timed_region_peak_coverage_pct(
                payload.timed_mask_regions, frame_width, frame_height
            )
            if timed_coverage > MANUAL_MAX_COVERAGE_PCT:
                update_task(
                    task_id,
                    status="failed",
                    progress=100,
                    stage="遮罩区域过大",
                    error=(
                        f"分时遮罩最大覆盖 {timed_coverage:.1f}% 画面，超过 "
                        f"{MANUAL_MAX_COVERAGE_PCT}% 上限。水印通常只占画面很小一部分，"
                        "请确认区域是否选得过大。"
                    ),
                )
                update_history_by_task(task_id, status="failed")
                return

        # The processor object is shared between concurrent tasks, so the engine
        # state must be captured immediately after this task's own call instead
        # of being read later.
        engine = "unknown"
        engine_note = ""

        def is_canceled() -> bool:
            task = get_task(task_id)
            return task is not None and task.status == "canceled"

        if source_path and suffix in IMAGE_EXT:
            _safe_stage(task_id, 20, "图片修复中")
            await asyncio.to_thread(
                processor.process_image,
                source_path,
                mask_regions,
                output,
                payload.repair_mode,
                mask_preset,
                payload.remove_subtitle,
            )
            engine = processor.last_engine
            engine_note = processor.last_engine_note
        elif source_path and suffix in VIDEO_EXT and is_video_path(source_path):
            def progress(progress: int, stage: str) -> None:
                _safe_stage(task_id, progress, stage)

            await asyncio.to_thread(
                processor.process_video,
                source_path,
                mask_regions,
                output,
                progress,
                payload.repair_mode,
                mask_preset,
                payload.remove_subtitle,
                is_canceled,
                payload.timed_mask_regions,
            )
            engine = processor.last_engine
            engine_note = processor.last_engine_note
        else:
            # No usable media was supplied. Writing a demo text file and calling
            # the task "completed" would be a fake success, so the task fails.
            update_task(
                task_id,
                status="failed",
                progress=100,
                stage="缺少可处理的素材",
                error="未收到可处理的视频或图片文件，任务未执行。",
            )
            update_history_by_task(task_id, status="failed")
            return

        # Never report success when no repair engine actually ran.
        if engine == "none":
            update_task(
                task_id,
                status="failed",
                progress=100,
                stage="未执行去水印",
                error=engine_note or "去水印引擎不可用，未修改原始文件。",
                output_path=str(output),
            )
            update_history_by_task(task_id, status="failed")
            return

        update_task(
            task_id,
            status="completed",
            progress=100,
            stage=_stage_from_engine(engine, engine_note, "处理完成"),
            output_path=str(output),
        )
        update_history_by_task(task_id, status="success", size_mb=_size_mb(output, 0.01))
    except AiServiceCancelled:
        # Must precede `except Exception`: the user canceled the task, so it
        # stays canceled and the partial output is dropped rather than reported
        # as a finished file.
        update_task(
            task_id,
            status="canceled",
            progress=100,
            stage="已取消",
            error="用户已取消",
            output_path=None,
        )
        update_history_by_task(task_id, status="failed")
        _discard_output(output)
    except Exception as exc:
        update_task(task_id, status="failed", stage="处理失败", error=str(exc))
        update_history_by_task(task_id, status="failed")
        _discard_output(output)


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
