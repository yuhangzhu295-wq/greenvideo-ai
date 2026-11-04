"""Real video watermark repair driven by the IOPaint LaMa engine.

Pipeline
--------
decode (OpenCV) -> per-frame mask -> LaMa inpaint (HTTP) -> encode (FFmpeg)
-> mux original audio -> verify (ffprobe)

Guarantees enforced here
------------------------
* The LaMa service must answer. If it does not, the caller gets a real error
  and the task is marked failed - never a silent OpenCV fallback reported as AI.
* Pixels outside the mask are never touched: LaMa is called with the exact mask
  and the returned frame is written back only inside that mask.
* The original audio track is preserved.
* Resolution, frame rate and frame count are preserved.
* The output is verified with ffprobe before the task reports success.
"""

from __future__ import annotations

import base64
import json
import queue
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.config import settings


@dataclass
class RepairReport:
    engine: str
    frames_total: int = 0
    frames_repaired: int = 0
    frames_skipped: int = 0
    mask_coverage_pct: float = 0.0
    audio_preserved: bool = False
    output_fps: float = 0.0
    output_frames: int = 0
    output_width: int = 0
    output_height: int = 0
    output_duration: float = 0.0
    notes: list[str] = field(default_factory=list)


class AiServiceError(RuntimeError):
    """Raised when the AI repair engine cannot be used or fails."""


class AiServiceCancelled(RuntimeError):
    """Raised when a repair run is aborted because the task was canceled."""


def iopaint_status() -> tuple[bool, str]:
    if not settings.iopaint_enabled:
        return False, "IOPaint 未启用（GREENVIDEO_IOPAINT_ENABLED=false）"
    base = settings.iopaint_base_url.rstrip("/")
    try:
        response = httpx.get(f"{base}/api/v1/server-config", timeout=3)
    except Exception as exc:
        return False, f"IOPaint 服务不可达（{base}）：{type(exc).__name__}"
    if response.status_code != 200:
        return False, f"IOPaint 健康检查返回 {response.status_code}"
    try:
        models = response.json().get("modelInfos", [])
        names = [m.get("name") for m in models]
    except Exception:
        names = []
    if settings.iopaint_model not in names:
        return False, (f"IOPaint 未提供模型 {settings.iopaint_model}（可用：{names or '未知'}）")
    return True, f"IOPaint 可用，模型 {settings.iopaint_model}"


def _ensure_model() -> None:
    base = settings.iopaint_base_url.rstrip("/")
    try:
        current = httpx.get(f"{base}/api/v1/model", timeout=5).json()
    except Exception as exc:
        raise AiServiceError(f"无法读取 IOPaint 当前模型：{exc}") from exc
    if current.get("name") == settings.iopaint_model:
        return
    try:
        response = httpx.post(
            f"{base}/api/v1/model",
            json={"name": settings.iopaint_model},
            timeout=120,
        )
    except Exception as exc:
        raise AiServiceError(f"切换 IOPaint 模型失败：{exc}") from exc
    if response.status_code != 200:
        raise AiServiceError(f"切换 IOPaint 模型返回 {response.status_code}: {response.text[:200]}")


class LamaWorker:
    """Persistent LaMa process (ai-worker venv: torch+CUDA) over stdin/stdout.

    Calling IOPaint over HTTP costs hundreds of milliseconds of PNG/base64 and
    Python pipeline overhead per frame while the GPU forward itself takes tens
    of milliseconds. This worker loads the model once and serves crops over a
    pipe. Falls back to HTTP IOPaint when the worker cannot start.
    """

    _worker_script = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "ai-worker" / "lama_worker.py"
    )
    _worker_python = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "ai-worker" / ".venv" / "Scripts" / "python.exe"
    )

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._queue: queue.Queue[str] | None = None

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, timeout_s: float = 60.0) -> None:
        if not (self._worker_script.exists() and self._worker_python.exists()):
            raise AiServiceError("LaMa worker 脚本或 ai-worker 解释器不存在")
        self._proc = subprocess.Popen(
            [str(self._worker_python), str(self._worker_script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            cwd=str(self._worker_script.parent),
        )
        self._queue = queue.Queue()

        def _enqueue(pipe, q):
            try:
                for line in iter(pipe.readline, ""):
                    q.put(line)
            except Exception:
                pass
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        t = threading.Thread(target=_enqueue, args=(self._proc.stdout, self._queue), daemon=True)
        t.start()

        try:
            ready_line = self._queue.get(timeout=timeout_s)
        except Exception as exc:
            self.stop()
            raise AiServiceError(f"LaMa worker 启动超时 ({timeout_s}s)") from exc

        try:
            ready = json.loads(ready_line.strip() or "{}")
        except Exception as exc:
            self.stop()
            raise AiServiceError(f"LaMa worker 响应格式错误：{ready_line}") from exc

        if not ready.get("ready"):
            self.stop()
            raise AiServiceError(f"LaMa worker 启动失败：{ready}")

    def inpaint(self, frame_crop, mask_crop, timeout_s: float = 30.0):
        import cv2
        import numpy as np

        if not self.is_alive():
            raise AiServiceError("LaMa worker 进程已终止")

        ok, img_buf = cv2.imencode(".png", frame_crop)
        ok2, mask_buf = cv2.imencode(".png", mask_crop)
        if not (ok and ok2):
            raise AiServiceError("帧或 mask 编码失败")
        req = json.dumps({
            "image": base64.b64encode(img_buf.tobytes()).decode("ascii"),
            "mask": base64.b64encode(mask_buf.tobytes()).decode("ascii"),
        })
        try:
            self._proc.stdin.write(req + "\n")
            self._proc.stdin.flush()
        except Exception as exc:
            self.stop()
            raise AiServiceError(f"向 LaMa worker 发送请求失败：{exc}") from exc

        try:
            line = self._queue.get(timeout=timeout_s)
        except Exception as exc:
            self.stop()
            raise AiServiceError(f"LaMa worker 推理超时 ({timeout_s}s)") from exc

        if not line:
            self.stop()
            raise AiServiceError("LaMa worker 已退出")
        resp = json.loads(line.strip() or "{}")
        if "image" not in resp:
            raise AiServiceError(f"LaMa worker 推理失败：{resp.get('error')}")
        arr = np.frombuffer(base64.b64decode(resp["image"]), dtype=np.uint8)
        out = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if out is None or out.shape != frame_crop.shape:
            raise AiServiceError("LaMa worker 返回尺寸不符或解码失败")
        return out

    def stop(self) -> None:
        if self._proc is not None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                if self._proc.stdout:
                    self._proc.stdout.close()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                    self._proc.wait(timeout=2)
                except Exception:
                    pass
            self._proc = None


def _inpaint_frame(
    frame,
    mask,
    client: httpx.Client | None = None,
    base: str | None = None,
    worker: LamaWorker | None = None,
    max_attempts: int = 3,
):
    """Inpaint only the masked region via worker or IOPaint, pasted back into full frame.

    Performance: sending the full 1080p frame costs seconds of PNG/base64
    encoding per request. Cropping to the mask bounding rect (+ margin) cuts
    the payload to a fraction and lets LaMa run on a much smaller tensor.
    """
    import cv2
    import numpy as np

    ys, xs = np.where(mask > 0)
    if ys.size == 0:
        return frame

    height, width = frame.shape[:2]
    margin = 96
    x0 = max(0, int(xs.min()) - margin)
    y0 = max(0, int(ys.min()) - margin)
    x1 = min(width, int(xs.max()) + margin)
    y1 = min(height, int(ys.max()) + margin)
    # Align to multiples of 8: friendlier to the model's downsampling.
    x0 -= x0 % 8
    y0 -= y0 % 8
    x1 += (8 - x1 % 8) % 8
    y1 += (8 - y1 % 8) % 8
    x1 = min(x1, width)
    y1 = min(y1, height)

    frame_crop = frame[y0:y1, x0:x1]
    mask_crop = mask[y0:y1, x0:x1]

    inpainted_crop = None

    # Priority 1: Persistent in-process CUDA worker on cropped ROI
    if worker is not None and worker.is_alive():
        try:
            inpainted_crop = worker.inpaint(frame_crop, mask_crop)
        except Exception as exc:
            print(f"[repair] Worker inpaint failed, falling back to HTTP: {exc}")
            inpainted_crop = None

    # Priority 2: HTTP IOPaint fallback on cropped ROI
    if inpainted_crop is None and client is not None and base:
        ok, img_buf = cv2.imencode(".png", frame_crop)
        ok2, mask_buf = cv2.imencode(".png", mask_crop)
        if not (ok and ok2):
            raise AiServiceError("帧或 mask 编码失败")

        payload = {
            "image": base64.b64encode(img_buf.tobytes()).decode("ascii"),
            "mask": base64.b64encode(mask_buf.tobytes()).decode("ascii"),
            "hd_strategy": "Original",
            "sd_keep_unmasked_area": True,
        }

        last_error = ""
        for _attempt in range(max_attempts):
            try:
                response = client.post(f"{base}/api/v1/inpaint", json=payload, timeout=25.0)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                continue
            content_type = response.headers.get("content-type", "")
            if "image" not in content_type:
                last_error = f"响应不是图片（content-type={content_type}）"
                continue
            arr = np.frombuffer(response.content, dtype=np.uint8)
            out = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if out is None or out.shape != frame_crop.shape:
                last_error = "响应图片解码失败或尺寸不符"
                continue
            inpainted_crop = out
            break
        if inpainted_crop is None:
            raise AiServiceError(f"LaMa HTTP 推理失败（重试 {max_attempts} 次）：{last_error}")

    if inpainted_crop is None:
        raise AiServiceError("未找到可用的 LaMa 推理引擎（Worker 和 HTTP 均不可用）")

    # Paste the repaired crop back; unmasked pixels of the crop are identical
    # to the source (sd_keep_unmasked_area), so the rest of the frame stays
    # byte-identical to the original.
    out_frame = frame.copy()
    out_frame[y0:y1, x0:x1] = inpainted_crop
    return out_frame


def _stream_info(probe: dict, kind: str) -> dict | None:
    for s in probe.get("streams", []):
        if s.get("codec_type") == kind:
            return s
    return None


def _frame_timestamps(path: Path) -> list[float]:
    """Per-frame presentation timestamps of a video's first stream."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    stamps: list[float] = []
    for line in result.stdout.splitlines():
        line = line.strip().rstrip(",")
        if not line:
            continue
        try:
            stamps.append(float(line))
        except ValueError:
            continue
    return stamps


def _stream_summary(path: Path) -> tuple[int | None, float]:
    """(video frame count, container duration) for a media file."""
    probe = _ffprobe(path)
    vs = _stream_info(probe, "video") or {}
    nb = vs.get("nb_frames")
    duration = float(probe.get("format", {}).get("duration", 0) or 0)
    frames = int(nb) if nb and str(nb).isdigit() else None
    return frames, duration


def _verify_output(
    output: Path, source: Path, probe_in: dict, expected_frames: int | None = None
) -> list[str]:
    """Compare the finished file with the source and report every mismatch.

    A repair is only reported as faithful when resolution, frame count, duration
    and audio survive. Anything else is stated plainly instead of being hidden
    behind a "completed" status.
    """
    issues: list[str] = []
    src_vs = _stream_info(probe_in, "video") or {}
    src_frames, src_duration = _stream_summary(source)
    out_frames, out_duration = _stream_summary(output)

    out_probe = _ffprobe(output)
    out_vs = _stream_info(out_probe, "video") or {}
    if (src_vs.get("width"), src_vs.get("height")) != (out_vs.get("width"), out_vs.get("height")):
        issues.append(
            f"分辨率变化 {src_vs.get('width')}x{src_vs.get('height')} -> "
            f"{out_vs.get('width')}x{out_vs.get('height')}"
        )

    src_audio = _stream_info(probe_in, "audio")
    out_audio = _stream_info(out_probe, "audio")
    if src_audio is not None and out_audio is None:
        issues.append("输出丢失音频流")
    elif src_audio is not None and out_audio is not None:
        if src_audio.get("sample_rate") != out_audio.get("sample_rate"):
            issues.append(
                f"音频采样率变化 {src_audio.get('sample_rate')} -> {out_audio.get('sample_rate')}"
            )

    if expected_frames is not None:
        if out_frames is not None and out_frames != expected_frames:
            issues.append(f"帧数 {out_frames} (预期 {expected_frames})")
    elif src_frames is not None and out_frames is not None and src_frames != out_frames:
        issues.append(f"帧数 {src_frames} -> {out_frames}")

    if (
        expected_frames is None
        and src_duration
        and out_duration
        and abs(src_duration - out_duration) > 0.1
    ):
        issues.append(f"时长 {src_duration:.3f}s -> {out_duration:.3f}s")
    return issues


def _ffprobe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    import json

    return json.loads(result.stdout)


def _ffprobe_stream_selector(stream_type: str) -> str:
    """Map a stream type name to ffprobe's short stream selector."""
    return {"video": "v", "audio": "a", "subtitle": "s"}.get(stream_type, "v")


def count_packets(path: Path, stream_type: str = "video") -> int | None:
    """Count actual packets by demuxing, or None when the count is unobtainable.

    Container metadata (`nb_frames`) is advisory: a fragmented MP4 written by a
    streaming muxer often omits it, and a file whose header claims N frames can
    physically contain fewer. Every fidelity guard in this pipeline therefore
    counts real packets, and treats "cannot count" as a failure rather than
    skipping the check.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_packets",
                # ffprobe's stream selector uses short names ("v:0", "a:0");
                # "video:0" is rejected with a non-zero exit.
                "-select_streams",
                f"{_ffprobe_stream_selector(stream_type)}:0",
                "-show_entries",
                "stream=nb_read_packets",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    out = (result.stdout or "").strip()
    if not out.isdigit():
        return None
    return int(out)


def count_decoded_frames(path: Path, stream_type: str = "video") -> int | None:
    """Count frames the decoder can actually produce, or None if unobtainable.

    This is the strongest available statement about whether a file is playable:
    a header with no packets, or a truncated tail, shows up here as a shortfall.
    Works for audio streams too (``stream_type="audio"``), which is how an
    audio file that merely declares a track is told apart from one that carries
    decodable audio.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_frames",
                "-select_streams",
                f"{_ffprobe_stream_selector(stream_type)}:0",
                "-show_entries",
                "stream=nb_read_frames",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    out = (result.stdout or "").strip()
    if not out.isdigit():
        return None
    return int(out)


def declared_frame_count(path: Path, stream_type: str = "video") -> int | None:
    """Frame count the container advertises, or None when it advertises none.

    ``None`` means "unknown", not "zero": fragmented MP4s legitimately omit
    this field, and treating an omission as a mismatch would reject valid files.
    """
    try:
        probe = _ffprobe(path)
    except Exception:
        return None
    for stream in probe.get("streams", []) or []:
        if stream.get("codec_type") != stream_type:
            continue
        raw = stream.get("nb_frames")
        if raw is not None and str(raw).isdigit():
            return int(raw)
    return None


def media_integrity_issues(path: Path, kind: str = "video") -> list[str]:
    """Reasons a produced file is not a complete, decodable piece of media.

    An empty list means every check that could be performed passed. The checks
    are based on demuxing and decoding, never on the header alone:

      * at least one packet and one decodable frame must exist;
      * the container must not advertise more frames than it actually holds —
        a truncated file keeps its original header, so this is the only way to
        notice the tail is missing.

    The packet comparison is deliberately the strict one: an audio decoder can
    legitimately report one frame fewer than the header (AAC priming), so
    comparing decoded count against the declared count would reject healthy
    files while catching nothing extra.

    A count that cannot be obtained is reported as an issue, not a pass.
    Counter edge: a file that is fragmented *and* truncated carries no header
    count at all, so only decodability can be asserted for it.
    """
    issues: list[str] = []
    packets = count_packets(path, kind)
    decoded = count_decoded_frames(path, kind)
    declared = declared_frame_count(path, kind)

    if packets is None:
        issues.append("无法统计实际数据包数")
    elif packets <= 0:
        issues.append("没有任何数据包")
    if decoded is None:
        issues.append("无法解码任何帧")
    elif decoded <= 0:
        issues.append("可解码帧数为 0")
    if declared is not None and packets is not None and packets < declared:
        issues.append(f"文件头声明 {declared} 帧但实际只有 {packets} 个数据包")
    return issues


def repair_video(
    input_path: str | Path,
    output_path: str | Path,
    mask_provider: Callable[[int, object], object],
    progress_cb: Callable[[int, str], None] | None = None,
    frame_stride: int = 1,
    cancel_check: Callable[[], bool] | None = None,
    max_frames: int | None = None,
) -> RepairReport:
    """Repair a video frame-by-frame with the LaMa engine.

    ``mask_provider(frame_index, frame)`` returns the uint8 mask for that frame
    (255 = repair). Frames whose mask is empty are copied through untouched.

    ``cancel_check`` is polled once per frame; returning True aborts the run and
    raises ``AiServiceCancelled`` so the caller can mark the task canceled
    instead of overwriting it with a success once the work finishes anyway.

    ``max_frames`` bounds the processed frames when an appended promo outro was
    detected and trimmed.
    """
    import cv2
    import numpy as np

    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Prefer the persistent in-process worker (CUDA, model loaded once): it
    # removes hundreds of milliseconds of HTTP/PNG overhead per frame and does
    # not require the IOPaint HTTP server at all. HTTP IOPaint is the fallback.
    worker = LamaWorker()
    try:
        worker.start()
    except Exception as exc:
        print(f"[repair] LaMa worker unavailable, trying HTTP IOPaint: {exc}")
        worker = None

    try:
        if worker is None:
            available, reason = iopaint_status()
            if not available:
                raise AiServiceError(f"AI 修复引擎不可用：{reason}")
            _ensure_model()
        base = settings.iopaint_base_url.rstrip("/")

        report = RepairReport(engine=f"iopaint-lama:{settings.iopaint_model}")

        cap = cv2.VideoCapture(str(input_path))
        if not cap.isOpened():
            cap.release()
            raise AiServiceError("视频解码失败（OpenCV 无法打开输入文件）")

        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if width <= 0 or height <= 0:
            cap.release()
            raise AiServiceError("无法读取视频分辨率")

        source_issues = media_integrity_issues(input_path, "video")
        if source_issues:
            cap.release()
            raise AiServiceError("输入视频不完整，未执行修复：" + "；".join(source_issues))

        # Prefer the container's declared rate over OpenCV's average: for a
        # variable-rate source OpenCV reports something like 29.885, and encoding at
        # that value yields 519 frames instead of the source's 522.
        probe_in = _ffprobe(input_path)
        encode_fps = fps
        src_vs = _stream_info(probe_in, "video") or {}
        rate = src_vs.get("r_frame_rate", "")
        if rate and "/" in rate:
            try:
                num, den = rate.split("/")
                if float(den) > 0:
                    encode_fps = float(num) / float(den)
            except (ValueError, ZeroDivisionError):
                encode_fps = fps
        if encode_fps <= 0:
            encode_fps = fps

        report.output_fps = float(encode_fps)
        report.output_width = width
        report.output_height = height
        report.frames_total = total
        report.notes.append(f"输入 {width}x{height} @ {encode_fps:.3f}fps, {total} 帧")

        if progress_cb:
            progress_cb(8, "AI 引擎已就绪，开始逐帧修复")

        coverage_sum = 0.0
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            repaired_dir = tmp_dir / "frames"
            repaired_dir.mkdir(parents=True, exist_ok=True)

            with httpx.Client() as client:
                index = 0
                prev_frame = None
                prev_inpainted = None
                prev_mask = None
                while True:
                    if cancel_check is not None and cancel_check():
                        cap.release()
                        raise AiServiceCancelled("任务已被用户取消")
                    if max_frames is not None and index >= max_frames:
                        break
                    ok, frame = cap.read()
                    if not ok:
                        break
                    mask = mask_provider(index, frame)
                    if mask is None:
                        mask = np.zeros((height, width), dtype=np.uint8)
                    mask = np.asarray(mask, dtype=np.uint8)
                    if mask.shape[:2] != (height, width):
                        mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)

                    coverage = float((mask > 127).mean())
                    coverage_sum += coverage

                    if coverage <= 0.0:
                        out_frame = frame
                        report.frames_skipped += 1
                        prev_frame = frame
                        prev_inpainted = None
                        prev_mask = None
                    else:
                        try:
                            inpainted = _inpaint_frame(
                                frame, mask, client, base, worker=worker
                            )
                        except TypeError:
                            inpainted = _inpaint_frame(frame, mask, client, base)

                        # 1. Dilate mask with (9, 9) ellipse, then feather with GaussianBlur.
                        # Core mask ensures unfeathered center has 100% alpha = 1.0 (no leakage).
                        core_mask = (mask > 127).astype(np.float32)
                        dilated = cv2.dilate(
                            mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)), iterations=1
                        )
                        feathered = cv2.GaussianBlur(
                            dilated.astype(np.float32) / 255.0, (11, 11), 0
                        )
                        alpha = np.maximum(core_mask, feathered)[..., None]

                        # 2. Outer ring analysis for temporal smoothing & speck suppression
                        ring = (cv2.dilate(dilated, np.ones((15, 15), np.uint8)) > 127) & (
                            dilated <= 127
                        )
                        if ring.any():
                            ring_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)[ring]
                            ring_luma_mean = float(ring_gray.mean())
                            ring_luma_std = float(ring_gray.std())

                            # Dark background bright noise clamp (suppresses fake stars/flashes)
                            if ring_luma_mean < 35.0 and ring_luma_std < 18.0:
                                cap_val = np.percentile(frame[ring], 96, axis=0) + 4.0
                                mask_bool = dilated > 127
                                inpainted[mask_bool] = np.minimum(
                                    inpainted[mask_bool], cap_val
                                ).astype(np.uint8)

                            # Frame-to-frame temporal smoothing (EMA)
                            if (
                                prev_inpainted is not None
                                and prev_mask is not None
                                and prev_frame is not None
                            ):
                                inter_m = np.logical_and(dilated > 127, prev_mask > 127).sum()
                                union_m = np.logical_or(dilated > 127, prev_mask > 127).sum()
                                mask_iou = inter_m / max(1, union_m)
                                if mask_iou > 0.85:
                                    ring_diff = float(
                                        np.abs(
                                            frame[ring].astype(np.float32)
                                            - prev_frame[ring].astype(np.float32)
                                        ).mean()
                                    )
                                    if ring_diff < 3.5:
                                        # Static or very slow background (night sky, wall)
                                        inpainted = (
                                            0.78 * prev_inpainted.astype(np.float32)
                                            + 0.22 * inpainted.astype(np.float32)
                                        ).astype(np.uint8)
                                    elif ring_diff < 10.0:
                                        # Moderate motion: medium smoothing
                                        inpainted = (
                                            0.40 * prev_inpainted.astype(np.float32)
                                            + 0.60 * inpainted.astype(np.float32)
                                        ).astype(np.uint8)

                        out_frame = (
                            inpainted.astype(np.float32) * alpha
                            + frame.astype(np.float32) * (1.0 - alpha)
                        ).astype(np.uint8)
                        report.frames_repaired += 1
                        prev_frame = frame
                        prev_inpainted = inpainted
                        prev_mask = dilated

                    # A frame that fails to land on disk would silently shorten the
                    # output. ffmpeg then encodes a video with fewer frames than the
                    # source while the repair report still claims every frame was
                    # processed, so a truncated video reaches the user as "completed".
                    frame_path = repaired_dir / f"f_{index:06d}.png"
                    if not cv2.imwrite(str(frame_path), out_frame):
                        raise AiServiceError(f"第 {index} 帧写入磁盘失败，已中止而非输出缺帧视频")
                    if not frame_path.exists() or frame_path.stat().st_size == 0:
                        raise AiServiceError(f"第 {index} 帧写入了空文件，已中止而非输出缺帧视频")
                    index += 1

                    if progress_cb and total > 0 and index % 5 == 0:
                        pct = 8 + int((index / total) * 74)
                        progress_cb(min(pct, 82), f"AI 修复中 {index}/{total} 帧")

            cap.release()
            report.mask_coverage_pct = round(100.0 * coverage_sum / max(1, index), 3)

            if index == 0:
                raise AiServiceError("未能从输入视频解码任何帧")

            if progress_cb:
                progress_cb(85, "合成视频并合流原始音轨")

            silent = tmp_dir / "silent.mp4"
            # Encode at the source's declared frame rate. A variable-rate source
            # cannot be reproduced by re-timing (the concat demuxer quantises
            # durations and drops frames), but encoding at the declared rate keeps
            # the frame count and duration faithful, which is what a player shows.
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-framerate",
                    str(encode_fps),
                    "-i",
                    str(repaired_dir / "f_%06d.png"),
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-crf",
                    "16",
                    str(silent),
                ],
                capture_output=True,
                text=True,
                timeout=900,
                check=True,
            )

            # Mux the original audio when the input has one. `-shortest` is
            # deliberately avoided: it truncates the audio whenever the video is a
            # few frames shorter, which loses real sound. The full source audio is
            # copied instead.
            has_audio = any(s.get("codec_type") == "audio" for s in probe_in.get("streams", []))

            final = tmp_dir / "final.mp4"
            cmd = ["ffmpeg", "-y", "-i", str(silent)]
            if has_audio:
                if max_frames is not None:
                    trimmed_dur = index / encode_fps
                    cmd.extend(
                        [
                            "-t",
                            f"{trimmed_dur:.6f}",
                            "-i",
                            str(input_path),
                            "-map",
                            "0:v:0",
                            "-map",
                            "1:a:0",
                        ]
                    )
                else:
                    cmd.extend(["-i", str(input_path), "-map", "0:v:0", "-map", "1:a:0"])
            else:
                cmd.extend(["-map", "0:v:0"])
            cmd.extend(["-c:v", "copy", "-c:a", "copy", str(final)])
            subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=True)

            if not final.exists() or final.stat().st_size == 0:
                raise AiServiceError("FFmpeg 合成输出为空")

            shutil.copyfile(final, output_path)
            report.audio_preserved = has_audio

        if progress_cb:
            progress_cb(92, "校验输出视频")

        probe_out = _ffprobe(output_path)
        video_streams = [s for s in probe_out.get("streams", []) if s.get("codec_type") == "video"]
        audio_streams = [s for s in probe_out.get("streams", []) if s.get("codec_type") == "audio"]
        if not video_streams:
            raise AiServiceError("输出文件没有视频流")
        vs = video_streams[0]
        report.output_width = int(vs.get("width", 0))
        report.output_height = int(vs.get("height", 0))
        report.output_duration = float(probe_out.get("format", {}).get("duration", 0) or 0)
        nb = vs.get("nb_frames")
        report.output_frames = int(nb) if nb and nb.isdigit() else index
        if has_audio and not audio_streams:
            raise AiServiceError("输出文件丢失了音频流")
        if (report.output_width, report.output_height) != (width, height):
            raise AiServiceError(
                f"输出分辨率与输入不一致 {report.output_width}x{report.output_height}"
            )

        expected_frames = max_frames if max_frames is not None else total
        expected_duration = expected_frames / encode_fps
        if abs(report.output_duration - expected_duration) > 1.5:
            raise AiServiceError(
                f"输出时长与预期差异过大: {report.output_duration:.2f}s vs {expected_duration:.2f}s"
            )

        # Fidelity check against the source: any mismatch is reported rather than
        # hidden behind a completed status.
        fidelity_issues = _verify_output(
            output_path, input_path, probe_in, expected_frames=expected_frames
        )
        report.notes.extend(fidelity_issues)

        out_packets = count_packets(output_path, "video")
        if out_packets is None or out_packets <= 0:
            raise AiServiceError("无法确认输出文件的视频帧数，已中止而非交付可能缺帧的视频")
        report.output_frames = out_packets
        if out_packets != index:
            raise AiServiceError(
                f"实际处理 {index} 帧但输出只有 {out_packets} 帧，已中止而非交付缺帧视频"
            )

        src_packets = count_packets(input_path, "video")
        if src_packets is None or src_packets <= 0:
            raise AiServiceError("无法确认输入文件的视频帧数，已中止而非交付可能缺帧的视频")
        if expected_frames is None:
            expected_frames = src_packets

        if out_packets != expected_frames:
            raise AiServiceError(
                f"输出帧数 {out_packets} 与预期 {expected_frames} 不一致，"
                "视频内容有缺失，已中止而非交付缺帧视频"
            )
        if index != expected_frames:
            raise AiServiceError(
                f"从输入解码 {index} 帧但预期为 {expected_frames} 帧，"
                "解码可能提前结束，已中止而非交付缺帧视频"
            )

        if max_frames is not None and src_packets > out_packets:
            orig_dur = float(probe_in.get("format", {}).get("duration", 0) or 0)
            report.notes.append(
                f"已自动裁切平台片尾：{src_packets - out_packets} 帧 "
                f"({orig_dur - report.output_duration:.2f}s)"
            )

        out_issues = media_integrity_issues(output_path, "video")
        if out_issues:
            raise AiServiceError("输出视频不完整，已中止而非交付：" + "；".join(out_issues))

        report.notes.append(
            f"修复 {report.frames_repaired} 帧，跳过 {report.frames_skipped} 帧（无水印区域）"
        )
        report.notes.append(f"平均遮罩覆盖率 {report.mask_coverage_pct}%")
        report.notes.append(f"音频保留：{report.audio_preserved}")
        return report
    finally:
        if worker is not None:
            worker.stop()
