from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import httpx

from app.config import settings
from app.schemas import MaskPreset, MaskRegion, RepairMode
from app.services.video_repair import AiServiceError


def try_import_paddleocr() -> bool:
    try:
        __import__("paddleocr")
    except Exception:
        return False
    return True


def iopaint_status() -> tuple[bool, str]:
    """Return (available, reason) for the IOPaint service without raising."""
    from app.services.video_repair import iopaint_status as _status

    return _status()


def try_iopaint_service() -> bool:
    return iopaint_status()[0]


def is_video_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in {".mp4", ".mov", ".avi"}


def _audio_stream_status(path: Path) -> str:
    """ "present", "absent" or "unknown" for a file's audio.

    "unknown" must never be treated as "absent": a failed probe says nothing
    about the content, and acting as though the source had no audio is what let
    a muted video be reported as a finished repair.
    """
    try:
        from app.services.video_repair import _ffprobe

        probe = _ffprobe(path)
    except Exception:
        return "unknown"
    streams = probe.get("streams")
    if not isinstance(streams, list):
        return "unknown"
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio_streams:
        return "absent"

    # If the container declares an audio stream, verify that actual audio packets
    # can be demuxed. A header-only file (e.g. mdat stripped) declares a stream
    # but contains no packets, which must not be reported as "present".
    try:
        from app.services.video_repair import count_packets

        pkts = count_packets(path, "audio")
    except Exception:
        return "unknown"
    if pkts is None:
        return "unknown"
    if pkts <= 0:
        return "absent"
    return "present"


def _has_audio_stream(path: Path) -> bool:
    """True only when an audio stream is confirmed present."""
    return _audio_stream_status(path) == "present"


def _probe_streams(path: Path) -> dict:
    """Raw ffprobe result, or an empty dict when the probe itself fails."""
    try:
        from app.services.video_repair import _ffprobe

        return _ffprobe(path)
    except Exception:
        return {}


def _stream_frame_count(path: Path) -> int | None:
    """Real video packet count, or None when it cannot be determined.

    Container metadata is not trusted here: a fragmented MP4 omits ``nb_frames``
    entirely, so a writer that silently dropped frames would look identical to a
    complete one. Counting packets by demuxing is the only statement that
    reflects what is actually in the file.
    """
    try:
        from app.services.video_repair import count_packets

        return count_packets(path, "video")
    except Exception:
        return None


class _OverlayMaskProvider:
    """Combine a base per-frame mask with an always-on overlay mask.

    Used when the caller asks for both time-scoped watermark regions and the
    subtitle band: the two describe different things, so neither replaces the
    other.
    """

    def __init__(self, base, overlay) -> None:
        self.base = base
        self.overlay = overlay

    def mask_for(self, index: int, frame):
        import numpy as np

        base = self.base.mask_for(index, frame)
        if base is None:
            return self.overlay
        if self.overlay is None or not (self.overlay > 0).any():
            return base
        return np.maximum(np.asarray(base, dtype=np.uint8), self.overlay)


class TimedMaskProvider:
    """Build a per-frame mask from time-scoped regions.

    A clip can carry different watermarks at different times - a corner overlay
    during the content and a centred end card at the tail. Each region declares
    the window it applies to, so a frame is masked only where a watermark
    actually is instead of blanketing the whole video with every region.
    """

    def __init__(
        self, regions, height: int, width: int, fps: float, timestamps: list[float] | None = None
    ) -> None:
        self.regions = list(regions)
        self.height = height
        self.width = width
        self.fps = fps or 25.0
        # Real per-frame timestamps when the source is variable frame rate.
        # Deriving time from the frame index assumes a constant rate, which
        # switches the mask early or late on VFR material.
        self.timestamps = timestamps

    def _time_of(self, index: int) -> float:
        if self.timestamps and 0 <= index < len(self.timestamps):
            return float(self.timestamps[index])
        return index / self.fps

    def _active(self, t: float) -> list:
        active = []
        for r in self.regions:
            # A small tolerance keeps a frame that lands exactly on a boundary
            # (frame timestamps are quantised, so 14.4000 vs 14.4 is common) on
            # the earlier region instead of flipping a frame early.
            if t < r.start_s - 1e-6:
                continue
            if r.end_s is not None and t >= r.end_s - 1e-6:
                continue
            active.append(r)
        return active

    def mask_for(self, index: int, frame):
        import numpy as np

        t = self._time_of(index)
        mask = np.zeros((self.height, self.width), dtype=np.uint8)
        for r in self._active(t):
            x1 = max(0, min(self.width, r.x))
            y1 = max(0, min(self.height, r.y))
            x2 = max(0, min(self.width, r.x + r.w))
            y2 = max(0, min(self.height, r.y + r.h))
            if x2 > x1 and y2 > y1:
                mask[y1:y2, x1:x2] = 255
        return mask


class FrameTrajectoryMaskProvider:
    """Build dense per-frame mask directly from optical flow tracking trajectories.

    For every frame, reads the exact active bounding boxes from FrameTrackRecord
    so the mask moves dynamically with the watermark instead of using static boxes.
    """

    def __init__(self, frame_tracks: list, height: int, width: int) -> None:
        self.frame_tracks = list(frame_tracks)
        self.height = height
        self.width = width

    def mask_for(self, index: int, frame):
        import numpy as np

        mask = np.zeros((self.height, self.width), dtype=np.uint8)
        if 0 <= index < len(self.frame_tracks):
            rec = self.frame_tracks[index]
            boxes = getattr(rec, "boxes", []) or []
            for tb in boxes:
                b = getattr(tb, "box", None) or (tb.get("box") if isinstance(tb, dict) else None)
                if not b:
                    continue
                bx, by, bw, bh = b
                x1 = max(0, min(self.width, int(bx)))
                y1 = max(0, min(self.height, int(by)))
                x2 = max(0, min(self.width, int(bx + bw)))
                y2 = max(0, min(self.height, int(by + bh)))
                if x2 > x1 and y2 > y1:
                    mask[y1:y2, x1:x2] = 255
        return mask


class MovingMaskTracker:
    """Produce a tight per-frame mask for a watermark that changes position.

    The static mask covers every requested region on every frame. That is exact
    for a fixed watermark but wrong for a moving one: the union of all positions
    masks far more picture than the watermark actually occupies, and LaMa then
    rebuilds background that never needed touching.

    This tracker looks for the strongest local response inside a padded
    neighbourhood of each static region and keeps only the responding pixels, so
    a moving watermark gets a box that follows it instead of a full-video smear.
    If a frame shows no response in a region, the static region is used as a
    safe fallback rather than dropping the watermark entirely.
    """

    def __init__(
        self,
        input_path: str | Path,
        mask_regions: list[MaskRegion],
        mask_preset: MaskPreset,
        remove_subtitle: bool,
        static_mask,
    ) -> None:
        self.input_path = Path(input_path)
        self.mask_regions = list(mask_regions)
        self.mask_preset = mask_preset
        self.remove_subtitle = remove_subtitle
        self.static_mask = static_mask
        self._processor = WatermarkProcessor()
        self._response_cache: dict[int, object] = {}
        self._tracked_frames = 0
        self._static_frames = 0

    def _response(self, frame):
        import cv2
        import numpy as np

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        grad = np.sqrt(gx * gx + gy * gy)
        gf = gray.astype(np.float32)
        mean = cv2.blur(gf, (21, 21))
        sq = cv2.blur(gf * gf, (21, 21))
        contrast = np.sqrt(np.maximum(sq - mean * mean, 0.0))
        return np.clip(grad / 40.0, 0, 4.0) * 0.65 + np.clip(contrast / 45.0, 0, 4.0) * 0.35

    def mask_for(self, index: int, frame):
        import cv2
        import numpy as np

        if self.static_mask is None:
            return None
        if not self.mask_regions and self.mask_preset == "custom":
            return self.static_mask

        response = self._response(frame)
        height, width = self.static_mask.shape[:2]
        frame_mask = np.zeros((height, width), dtype=np.uint8)

        background = float(np.percentile(response, 60))
        threshold = background * 2.2

        if self.mask_regions:
            regions = self.mask_regions
        else:
            probe = self._processor._generate_mask(
                (height, width), [], self.mask_preset, self.remove_subtitle
            )
            regions = _mask_to_regions(probe)

        tracked_any = False
        for region in regions:
            # A modest pad lets the box follow the watermark without dragging in
            # half the frame.
            pad_x = max(8, int(region.w * 0.25))
            pad_y = max(8, int(region.h * 0.25))
            x = max(0, region.x - pad_x)
            y = max(0, region.y - pad_y)
            x2 = min(width, region.x + region.w + pad_x)
            y2 = min(height, region.y + region.h + pad_y)
            window = response[y:y2, x:x2]
            if window.size == 0:
                cv2.rectangle(
                    frame_mask,
                    (region.x, region.y),
                    (region.x + region.w, region.y + region.h),
                    255,
                    -1,
                )
                continue
            # Threshold against the window's own distribution: the watermark is
            # the strongest structure in its neighbourhood, not merely above the
            # frame-wide background level.
            window_threshold = max(threshold, float(np.percentile(window, 88)))
            local = (window > window_threshold).astype(np.uint8) * 255
            if int((local > 0).sum()) < 40:
                # No watermark response here on this frame: the watermark is not
                # at this position, so nothing is masked for this region.
                continue
            anchor = (region.x + region.w / 2.0 - x, region.y + region.h / 2.0 - y)
            tight = _tight_box(local, anchor)
            if tight is None:
                continue
            bx, by, bw, bh = tight
            cv2.rectangle(frame_mask, (x + bx, y + by), (x + bx + bw, y + by + bh), 255, -1)
            tracked_any = True

        if tracked_any:
            self._tracked_frames += 1
        else:
            self._static_frames += 1
        return frame_mask


def _mask_to_regions(mask):
    import cv2
    import numpy as np

    binary = (mask > 0).astype(np.uint8) * 255
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(binary, connectivity=8)
    regions = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < 40:
            continue
        regions.append(MaskRegion(x=x, y=y, w=w, h=h))
    return regions


def _tight_box(local, anchor: tuple[int, int] | None = None):
    """Return the response blob nearest the anchor, not simply the largest one.

    Picking the largest blob lets background structure win; a watermark sits
    where the caller expected it, so proximity to the anchor is the right test.
    """
    import cv2

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    grown = cv2.morphologyEx(local, cv2.MORPH_CLOSE, kernel, iterations=2)
    grown = cv2.dilate(grown, kernel, iterations=2)
    count, _labels, stats, cent = cv2.connectedComponentsWithStats(grown, connectivity=8)
    best = None
    best_key = None
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < 40:
            continue
        cx, cy = float(cent[i][0]), float(cent[i][1])
        if anchor is not None:
            ax, ay = anchor
            dist = ((cx - ax) ** 2 + (cy - ay) ** 2) ** 0.5
        else:
            dist = 0.0
        key = (dist, -area)
        if best_key is None or key < best_key:
            best_key = key
            best = (x, y, w, h)
    return best


class WatermarkProcessor:
    def __init__(self) -> None:
        self._cv2_available: bool | None = None
        self._np_available: bool | None = None
        # Engine actually used by the most recent process_* call.
        self.last_engine: str = "unknown"
        self.last_engine_note: str = ""
        # Populated when processing could not be performed and the input was copied.
        self.last_degraded: bool = False
        # Detailed report from the AI video repair engine when it ran.
        self.last_repair_report = None

    def _record_engine(self, engine: str, note: str = "", degraded: bool = False) -> None:
        self.last_engine = engine
        self.last_engine_note = note
        self.last_degraded = degraded

    def _check_cv2(self) -> bool:
        if self._cv2_available is None:
            try:
                import cv2  # noqa: F401

                self._cv2_available = True
            except Exception:
                self._cv2_available = False
        return self._cv2_available

    def _check_np(self) -> bool:
        if self._np_available is None:
            try:
                import numpy as np  # noqa: F401

                self._np_available = True
            except Exception:
                self._np_available = False
        return self._np_available

    def _preset_regions(
        self,
        width: int,
        height: int,
        mask_preset: MaskPreset,
        remove_subtitle: bool,
    ) -> list[MaskRegion]:
        regions: list[MaskRegion] = []
        if mask_preset in {"douyin_top_left", "douyin_both_corners"}:
            regions.append(
                MaskRegion(
                    x=0,
                    y=0,
                    w=max(1, int(width * 0.42)),
                    h=max(1, int(height * 0.18)),
                )
            )
        if mask_preset in {"douyin_bottom_right", "douyin_both_corners"}:
            regions.append(
                MaskRegion(
                    x=max(0, int(width * 0.60)),
                    y=max(0, int(height * 0.78)),
                    w=max(1, int(width * 0.40)),
                    h=max(1, int(height * 0.18)),
                )
            )
        if remove_subtitle:
            regions.append(
                MaskRegion(
                    x=max(0, int(width * 0.18)),
                    y=max(0, int(height * 0.56)),
                    w=max(1, int(width * 0.64)),
                    h=max(1, int(height * 0.20)),
                )
            )
        return regions

    def _generate_mask(
        self,
        image_shape: tuple[int, int] | tuple[int, int, int],
        mask_regions: list[MaskRegion],
        mask_preset: MaskPreset,
        remove_subtitle: bool,
    ):
        import numpy as np

        height, width = int(image_shape[0]), int(image_shape[1])
        mask = np.zeros((height, width), dtype=np.uint8)
        regions = list(mask_regions)
        if mask_preset != "custom":
            regions.extend(self._preset_regions(width, height, mask_preset, remove_subtitle))
        elif remove_subtitle:
            regions.extend(self._preset_regions(width, height, "custom", True))

        for region in regions:
            x1 = min(width, max(0, region.x))
            y1 = min(height, max(0, region.y))
            x2 = min(width, x1 + region.w)
            y2 = min(height, y1 + region.h)
            if x2 > x1 and y2 > y1:
                mask[y1:y2, x1:x2] = 255
        return mask

    def _inpaint_with_opencv(self, image, mask):
        import cv2

        return cv2.inpaint(image, mask, 3, cv2.INPAINT_TELEA)

    def _inpaint_with_iopaint(self, image_path: Path, mask, output_path: Path) -> bool:
        """Single-frame repair against the real IOPaint API.

        IOPaint 1.6 exposes ``POST /api/v1/inpaint`` with base64 image + mask
        and answers with a binary PNG - not JSON. The model is selected with
        ``POST /api/v1/model`` beforehand.
        """
        import cv2
        import numpy as np

        from app.services.video_repair import AiServiceError, _ensure_model

        base = settings.iopaint_base_url.rstrip("/")
        try:
            _ensure_model()
            img = cv2.imread(str(image_path))
            if img is None:
                return False
            ok, img_buf = cv2.imencode(".png", img)
            ok2, mask_buf = cv2.imencode(".png", mask)
            if not (ok and ok2):
                return False
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
            response = httpx.post(f"{base}/api/v1/inpaint", json=payload, timeout=300)
            if response.status_code != 200:
                return False
            if "image" not in response.headers.get("content-type", ""):
                return False
            arr = np.frombuffer(response.content, dtype=np.uint8)
            result = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if result is None:
                return False
            cv2.imwrite(str(output_path), result)
            return True
        except (AiServiceError, Exception):
            return False

    def process_image(
        self,
        input_path: str | Path,
        mask_regions: list[MaskRegion],
        output_path: str | Path,
        repair_mode: RepairMode = "opencv",
        mask_preset: MaskPreset = "douyin_both_corners",
        remove_subtitle: bool = False,
    ) -> Path:
        input_path = Path(input_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not self._check_cv2() or not self._check_np():
            # No inpainting engine is available: the file is copied unchanged and the
            # caller must be told the watermark was NOT removed.
            self._record_engine(
                "none",
                "OpenCV/NumPy 不可用，未执行去水印，已原样复制文件。",
                degraded=True,
            )
            shutil.copyfile(input_path, output_path)
            return output_path

        import cv2

        image = cv2.imread(str(input_path))
        if image is None:
            self._record_engine(
                "none",
                "图片解码失败，未执行去水印，已原样复制文件。",
                degraded=True,
            )
            shutil.copyfile(input_path, output_path)
            return output_path

        mask = self._generate_mask(image.shape, mask_regions, mask_preset, remove_subtitle)
        if not (mask > 0).any():
            # An empty mask means nothing was selected, so no repair happened.
            # Reporting a successful engine here would hand back the original
            # file unchanged.
            self._record_engine(
                "none",
                "遮罩为空，未指定任何水印区域，未执行修复。",
                degraded=True,
            )
            shutil.copyfile(input_path, output_path)
            return output_path

        if repair_mode == "ai":
            available, reason = iopaint_status()
            if not available:
                # Same contract as the video path: an AI request that cannot run
                # the AI engine must fail, not quietly degrade.
                raise AiServiceError(f"AI 修复引擎不可用：{reason}")
            if self._inpaint_with_iopaint(input_path, mask, output_path):
                if not output_path.exists() or output_path.stat().st_size == 0:
                    raise AiServiceError("IOPaint 未生成有效的修复结果文件")
                self._record_engine(
                    "iopaint-lama",
                    f"已调用 IOPaint LaMa（模型 {settings.iopaint_model}）执行 AI 修复。",
                )
                return output_path
            raise AiServiceError("IOPaint 推理失败，未生成修复结果。")
        self._record_engine("opencv", "已使用 OpenCV 修复引擎。")

        repaired = self._inpaint_with_opencv(image, mask)
        # A failed write leaves no file, so the task would report success with
        # nothing to download.
        if not cv2.imwrite(str(output_path), repaired) or not output_path.exists():
            raise AiServiceError("修复结果写入磁盘失败")
        return output_path

    def process_video(
        self,
        input_path: str | Path,
        mask_regions: list[MaskRegion],
        output_path: str | Path,
        progress_cb: Callable[[int, str], None] | None = None,
        repair_mode: RepairMode = "opencv",
        mask_preset: MaskPreset = "douyin_both_corners",
        remove_subtitle: bool = False,
        cancel_check: Callable[[], bool] | None = None,
        timed_mask_regions: list | None = None,
        max_frames: int | None = None,
        frame_tracks: list | None = None,
        pixel_mask_prompts: dict[int, list[dict]] | None = None,
        mask_debug_dir: str | Path | None = None,
    ) -> Path:
        input_path = Path(input_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        ffmpeg_available = shutil.which("ffmpeg") is not None
        cv2_available = self._check_cv2() and self._check_np()
        if not cv2_available:
            self._record_engine(
                "none",
                "OpenCV/NumPy 不可用，未执行去水印，已原样复制文件。",
                degraded=True,
            )
            if progress_cb:
                progress_cb(20, "OpenCV 未安装，未执行去水印（已原样复制文件）")
                progress_cb(100, "处理结束（未执行去水印）")
            shutil.copyfile(input_path, output_path)
            return output_path

        import cv2

        # Read the source's own frame timestamps so time-scoped masks switch at
        # the right frame on variable-frame-rate material.
        source_timestamps: list[float] | None = None
        try:
            from app.services.video_repair import _frame_timestamps

            stamps = _frame_timestamps(input_path)
            if len(stamps) >= 2:
                source_timestamps = stamps
        except Exception:
            source_timestamps = None

        cap = cv2.VideoCapture(str(input_path))
        if not cap.isOpened():
            cap.release()
            self._record_engine(
                "none",
                "视频解码失败，未执行去水印，已原样复制文件。",
                degraded=True,
            )
            if progress_cb:
                progress_cb(20, "视频无法解码，未执行去水印（已原样复制文件）")
                progress_cb(100, "处理结束（未执行去水印）")
            shutil.copyfile(input_path, output_path)
            return output_path

        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        total_frames = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if width <= 0 or height <= 0:
            cap.release()
            self._record_engine(
                "none",
                "无法读取视频分辨率，未执行去水印，已原样复制文件。",
                degraded=True,
            )
            if progress_cb:
                progress_cb(20, "无法读取视频分辨率，未执行去水印（已原样复制文件）")
                progress_cb(100, "处理结束（未执行去水印）")
            shutil.copyfile(input_path, output_path)
            return output_path

        # A truncated source keeps its original header, so the container still
        # advertises the full frame count while the tail is gone. Repairing it
        # would produce a short video and report success, so the source is
        # checked against its own header before any work starts.
        from app.services.video_repair import media_integrity_issues

        source_issues = media_integrity_issues(input_path, "video")
        if source_issues:
            cap.release()
            raise AiServiceError("输入视频不完整，未执行修复：" + "；".join(source_issues))

        mask = self._generate_mask((height, width), mask_regions, mask_preset, remove_subtitle)

        if repair_mode == "ai":
            # The persistent CUDA worker path lives inside repair_video and does
            # not need the IOPaint HTTP server; only fall back to gating on
            cap.release()
            if progress_cb:
                progress_cb(12, "AI 引擎已就绪，开始逐帧 LaMa 修复")

            from app.services.video_repair import repair_video

            # Pixel-level path: when SAM2 prompts are available, segment actual
            # watermark pixels instead of writing whole rectangles as 255. The
            # detection boxes serve only as SAM prompts / search windows; the
            # repair engine receives the pixel mask. Falls back to the legacy
            # rectangle providers only when SAM2 worker is unavailable.
            pixel_provider = None
            if pixel_mask_prompts:
                try:
                    from app.services.watermark_detection.pixel_mask_provider import (
                        PixelMaskProvider,
                    )

                    debug = Path(mask_debug_dir) if mask_debug_dir else None
                    pixel_provider = PixelMaskProvider(
                        input_path, height, width,
                        prompts=pixel_mask_prompts, debug_dir=debug,
                    )

                    def _read_frame(f_idx: int):
                        cap_p = cv2.VideoCapture(str(input_path))
                        cap_p.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
                        ret, fr = cap_p.read()
                        cap_p.release()
                        return fr if ret else None

                    pixel_provider.build_tracks(_read_frame)
                    if progress_cb:
                        progress_cb(14, "SAM 2.1 像素级水印分割完成")
                except Exception as exc:
                    if progress_cb:
                        progress_cb(14, f"SAM2 分割不可用（{exc}），回退矩形 mask")
                    pixel_provider = None

            if pixel_provider is not None:
                provider = pixel_provider
                if remove_subtitle:
                    provider = _OverlayMaskProvider(
                        provider,
                        self._generate_mask((height, width), [], "custom", True),
                    )
            elif frame_tracks:
                provider = FrameTrajectoryMaskProvider(frame_tracks, height, width)
                if remove_subtitle:
                    provider = _OverlayMaskProvider(
                        provider,
                        self._generate_mask((height, width), [], "custom", True),
                    )
            elif timed_mask_regions:
                provider = TimedMaskProvider(
                    timed_mask_regions, height, width, fps, source_timestamps
                )
                if remove_subtitle:
                    # The subtitle band is a separate request from the watermark
                    # layout, so it must still be applied on top of the
                    # time-scoped regions rather than being dropped.
                    provider = _OverlayMaskProvider(
                        provider,
                        self._generate_mask((height, width), [], "custom", True),
                    )
            else:
                # A single static rectangle cannot follow a moving watermark, so
                # the per-frame tracker produces a tight mask for every frame.
                # Without it the union box would mask ~5x more picture than
                # necessary.
                tracker = MovingMaskTracker(
                    input_path, mask_regions, mask_preset, remove_subtitle, mask
                )
                provider = tracker

            def mask_provider(idx: int, frame):
                return provider.mask_for(idx, frame)

            report = repair_video(
                input_path,
                output_path,
                mask_provider,
                progress_cb=progress_cb,
                cancel_check=cancel_check,
                max_frames=max_frames,
            )
            # A run that repaired no frame has not removed anything, so it must
            # not be reported as a successful AI repair however the engine was
            # called.
            if report.frames_repaired == 0:
                self._record_engine(
                    "none",
                    "没有任何帧包含水印区域，未执行修复。",
                    degraded=True,
                )
                self.last_repair_report = report
                return output_path
            self._record_engine(
                "iopaint-lama",
                (
                    f"已调用 IOPaint LaMa 执行 AI 修复：修复 {report.frames_repaired} 帧，"
                    f"平均遮罩覆盖率 {report.mask_coverage_pct}%，"
                    f"音频保留 {report.audio_preserved}。"
                ),
            )
            self.last_repair_report = report
            return output_path

        self._record_engine("opencv", "已使用 OpenCV 逐帧修复引擎。")

        if progress_cb:
            progress_cb(12, "已生成水印区域 mask")

        # The OpenCV path must honour the same per-frame mask as the AI path.
        # Using only the static mask here silently ignored time-scoped regions and
        # produced a video with an empty mask, i.e. an unmodified copy reported as
        # a finished repair.
        if frame_tracks:
            frame_mask_provider = FrameTrajectoryMaskProvider(frame_tracks, height, width)
            if remove_subtitle:
                frame_mask_provider = _OverlayMaskProvider(
                    frame_mask_provider,
                    self._generate_mask((height, width), [], "custom", True),
                )
        elif timed_mask_regions:
            frame_mask_provider = TimedMaskProvider(
                timed_mask_regions, height, width, fps, source_timestamps
            )
            if remove_subtitle:
                frame_mask_provider = _OverlayMaskProvider(
                    frame_mask_provider,
                    self._generate_mask((height, width), [], "custom", True),
                )
        else:
            frame_mask_provider = MovingMaskTracker(
                input_path, mask_regions, mask_preset, remove_subtitle, mask
            )

        import numpy as np

        src_packets = _stream_frame_count(input_path)
        if src_packets is None or src_packets <= 0:
            raise AiServiceError("无法确认输入文件的视频帧数，已中止而非输出可能缺帧的视频")

        repaired_frames = 0
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_video = Path(temp_dir) / "video_no_audio.mp4"
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(temp_video), fourcc, fps, (width, height))
            # A writer that failed to open still returns a usable-looking object;
            # every write() call is then a no-op and the resulting file is empty.
            # Without this check the empty file is copied out and served as a
            # completed repair.
            if not writer.isOpened():
                writer.release()
                raise AiServiceError("无法创建视频写出器（编码器不可用），未执行修复。")
            frame_count = 0
            while True:
                if cancel_check is not None and cancel_check():
                    cap.release()
                    writer.release()
                    from app.services.video_repair import AiServiceCancelled

                    raise AiServiceCancelled("任务已被用户取消")
                if max_frames is not None and frame_count >= max_frames:
                    break
                ok, frame = cap.read()
                if not ok:
                    break
                frame_mask = frame_mask_provider.mask_for(frame_count, frame)
                if frame_mask is None:
                    frame_mask = np.zeros((height, width), dtype=np.uint8)
                if int((frame_mask > 0).sum()) > 0:
                    repaired = self._inpaint_with_opencv(frame, frame_mask)
                    repaired_frames += 1
                else:
                    repaired = frame
                writer.write(repaired)
                frame_count += 1
                if progress_cb and frame_count % 15 == 0:
                    progress = 20 + int((frame_count / total_frames) * 65)
                    progress_cb(min(progress, 88), "逐帧修复水印区域")

            cap.release()
            writer.release()

            if frame_count == 0:
                raise AiServiceError("未能从输入视频解码任何帧")

            expected_frames = max_frames if max_frames is not None else src_packets
            if frame_count != expected_frames:
                raise AiServiceError(
                    f"从输入解码 {frame_count} 帧但预期有 {expected_frames} 帧，"
                    "解码可能提前结束，已中止而非输出缺帧视频"
                )

            # The writer must actually have produced a decodable, non-empty video
            # with exactly as many packets as were written. An empty file, a
            # stream-less file, or a short file all mean the encode silently
            # failed, and none of them may be delivered as a finished repair.
            if not temp_video.exists() or temp_video.stat().st_size == 0:
                raise AiServiceError("OpenCV 写出器没有生成任何视频数据，未执行修复。")
            temp_probe = _probe_streams(temp_video)
            temp_video_streams = [
                s for s in temp_probe.get("streams", []) if s.get("codec_type") == "video"
            ]
            if not temp_video_streams:
                raise AiServiceError("OpenCV 写出器生成的视频没有视频流，未执行修复。")
            temp_frames = _stream_frame_count(temp_video)
            if temp_frames is None or temp_frames <= 0:
                raise AiServiceError(
                    "无法确认 OpenCV 写出器生成的视频帧数，已中止而非输出可能缺帧的视频"
                )
            if temp_frames != frame_count:
                raise AiServiceError(
                    f"OpenCV 写出器只生成 {temp_frames} 帧（应为 {frame_count}），"
                    "已中止而非输出缺帧视频"
                )

            if ffmpeg_available:
                # `-shortest` truncates the audio when the video is shorter than
                # the track, so the full source audio is copied instead.
                combined = Path(temp_dir) / "combined.mp4"
                mux_cmd = ["ffmpeg", "-y", "-i", str(temp_video)]
                if max_frames is not None:
                    trimmed_dur = frame_count / fps
                    mux_cmd.extend(["-t", f"{trimmed_dur:.6f}", "-i", str(input_path)])
                else:
                    mux_cmd.extend(["-i", str(input_path)])
                mux_cmd.extend(
                    [
                        "-map",
                        "0:v:0",
                        "-map",
                        "1:a:0?",
                        "-c:v",
                        "copy",
                        "-c:a",
                        "copy",
                        str(combined),
                    ]
                )
                mux = subprocess.run(
                    mux_cmd,
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                    shell=False,
                )
                source_audio = _audio_stream_status(input_path)
                if mux.returncode == 0 and combined.exists() and combined.stat().st_size > 0:
                    output_audio = _audio_stream_status(combined)
                    if output_audio == "absent" and source_audio != "absent":
                        # Falling back to the silent file here would pass off a
                        # muted video as a finished repair.
                        raise AiServiceError("音轨合流失败：输出没有音频流")
                    if output_audio == "unknown" and source_audio != "absent":
                        # The output's audio could not be confirmed, so a muted
                        # file cannot be ruled out. "absent" is the only case
                        # where a silent output is expected and acceptable.
                        raise AiServiceError("无法确认输出是否包含音轨，已中止")
                    shutil.copyfile(combined, output_path)
                elif source_audio == "present":
                    raise AiServiceError(f"音轨合流失败（ffmpeg 返回码 {mux.returncode}）")
                elif source_audio == "unknown":
                    # The source's audio could not be determined, so a silent
                    # result cannot be ruled out and must not be reported as a
                    # completed repair.
                    raise AiServiceError("无法确认源文件是否包含音轨，已中止")
                else:
                    shutil.copyfile(temp_video, output_path)
            elif _audio_stream_status(input_path) != "absent":
                # Without ffmpeg the audio track cannot be carried over, so the
                # result would be a muted video. That is not a finished repair.
                raise AiServiceError("FFmpeg 不可用，无法保留原始音轨；已中止而非输出无音轨视频。")
            else:
                shutil.copyfile(temp_video, output_path)

        # The delivered file must be the one that was just verified. Re-count its
        # packets rather than trusting the intermediate result, because the copy
        # or the mux is the last step that can go wrong.
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise AiServiceError("输出文件为空，未执行修复。")
        out_packets = _stream_frame_count(output_path)
        if out_packets is None or out_packets <= 0:
            raise AiServiceError("无法确认输出文件的视频帧数，已中止而非交付可能缺帧的视频")
        expected_frames = max_frames if max_frames is not None else src_packets
        if out_packets != expected_frames:
            raise AiServiceError(
                f"输出文件只有 {out_packets} 帧（应为 {expected_frames}），已中止而非交付缺帧视频"
            )
        # The delivered file must also decode, and must match whatever its own
        # header claims. A truncated encode otherwise looks complete.
        out_issues = media_integrity_issues(output_path, "video")
        if out_issues:
            raise AiServiceError("输出视频不完整，已中止而非交付：" + "；".join(out_issues))

        if repaired_frames == 0:
            self._record_engine(
                "none",
                "没有任何帧包含水印区域，未执行修复。",
                degraded=True,
            )

        if progress_cb:
            progress_cb(100, "处理完成")
        return output_path
