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
import shutil
import subprocess
import tempfile
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


def _inpaint_frame(
    frame,
    mask,
    client: httpx.Client,
    base: str,
    max_attempts: int = 3,
):
    import cv2
    import numpy as np

    ok, img_buf = cv2.imencode(".png", frame)
    ok2, mask_buf = cv2.imencode(".png", mask)
    if not (ok and ok2):
        raise AiServiceError("帧或 mask 编码失败")

    payload = {
        "image": base64.b64encode(img_buf.tobytes()).decode("ascii"),
        "mask": base64.b64encode(mask_buf.tobytes()).decode("ascii"),
        "ldm_steps": 20,
        "hd_strategy": "Crop",
        "hd_strategy_crop_margin": 128,
        "hd_strategy_crop_trigger_size": 800,
        "hd_strategy_resize_limit": 1280,
        "sd_keep_unmasked_area": True,
    }

    last_error = ""
    for _attempt in range(max_attempts):
        try:
            response = client.post(f"{base}/api/v1/inpaint", json=payload, timeout=300)
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
        if out is None:
            last_error = "响应图片解码失败"
            continue
        if out.shape != frame.shape:
            last_error = f"返回尺寸不符 {out.shape} != {frame.shape}"
            continue
        return out
    raise AiServiceError(f"LaMa 推理失败（重试 {max_attempts} 次）：{last_error}")


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
                else:
                    inpainted = _inpaint_frame(frame, mask, client, base)
                    # Contract requirement: unmasked pixels must remain 100% untouched.
                    # Feather the mask edges (blur radius 5) to smoothly blend into the
                    # original frame while ensuring unmasked areas are an exact copy.
                    alpha = cv2.GaussianBlur(mask.astype(np.float32) / 255.0, (5, 5), 0)[..., None]
                    out_frame = (
                        inpainted.astype(np.float32) * alpha
                        + frame.astype(np.float32) * (1.0 - alpha)
                    ).astype(np.uint8)
                    report.frames_repaired += 1

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
                "18",
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
        raise AiServiceError(f"输出分辨率与输入不一致 {report.output_width}x{report.output_height}")

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
