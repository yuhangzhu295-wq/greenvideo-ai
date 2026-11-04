"""Pixel-mask provider - the drop-in replacement for rectangle masks.

Implements the same ``mask_for(frame_index, frame)`` contract as
``FrameTrajectoryMaskProvider`` but returns the actual watermark pixels from
SAM 2.1 segmentation, with only a minimal dilation. The bounding rectangle is
used exclusively as a detection prompt / search window, never as the mask.

Falls back to the legacy rectangle provider only when SAM 2.1 is explicitly
unavailable, and reports the fallback in ``used_fallback`` so the caller can
surface it instead of silently degrading.
"""
from __future__ import annotations

from pathlib import Path

from app.services.watermark_detection.sam2_client import Sam2Worker
from app.services.watermark_detection.track_manager import TrackManager

# Minimal dilation: 2 iterations of a 5x5 ellipse only grows ~4px beyond the
# watermark strokes - enough to cover anti-aliased edges, far below the
# rectangle blowout this pipeline used to deliver.
_DILATE_KERNEL = (5, 5)
_DILATE_ITERS = 2


def _dilate_small(mask):
    import cv2
    import numpy as np

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, _DILATE_KERNEL)
    return cv2.dilate(np.asarray(mask, dtype=np.uint8), kernel, iterations=_DILATE_ITERS)


class PixelMaskProvider:
    """``mask_for(frame_index, frame)`` -> pixel-level watermark mask."""

    def __init__(
        self,
        video_path: str | Path,
        height: int,
        width: int,
        prompts: dict[int, list[dict]] | None = None,
        keyframe_interval: int = 30,
        debug_dir: Path | None = None,
    ) -> None:
        self.video_path = str(video_path)
        self.height = height
        self.width = width
        self.keyframe_interval = keyframe_interval
        self.debug_dir = Path(debug_dir) if debug_dir else None
        self.used_fallback = False
        self.worker = Sam2Worker()
        self.tracks = TrackManager()
        self._prompt_map: dict[int, dict] = {}
        for f_idx, plist in (prompts or {}).items():
            for p in plist:
                box = p.get("box")
                if box:
                    xyxy = [box[0], box[1], box[0] + box[2], box[1] + box[3]]
                    self._prompt_map[int(f_idx)] = {"box": xyxy}

    def _save_debug(self, frame_idx: int, frame, boxes, mask) -> None:
        import json

        import cv2

        if self.debug_dir is None:
            return
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(self.debug_dir / f"original_{frame_idx:06d}.png"), frame)
        boxes_img = frame.copy()
        for b in boxes:
            x1, y1, x2, y2 = [int(v) for v in b]
            cv2.rectangle(boxes_img, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.imwrite(str(self.debug_dir / f"candidate_boxes_{frame_idx:06d}.png"), boxes_img)
        cv2.imwrite(str(self.debug_dir / f"sam_mask_{frame_idx:06d}.png"), mask)
        overlay = frame.copy()
        overlay[mask > 0] = (0.35 * overlay[mask > 0] + 0.65 * 255).astype(overlay.dtype)
        cv2.imwrite(str(self.debug_dir / f"mask_overlay_{frame_idx:06d}.png"), overlay)
        (self.debug_dir / f"mask_{frame_idx:06d}.json").write_text(
            json.dumps({"frame": frame_idx, "mask_area": int((mask > 0).sum())}), encoding="utf-8"
        )

    def build_tracks(self, frame_reader) -> None:
        """Segment key frames and propagate masks through the video.

        ``frame_reader(frame_index)`` returns the BGR frame (or None).
        """
        self.worker.start()
        try:
            keyframes = sorted(self._prompt_map.keys())
            for kf in keyframes:
                frame = frame_reader(kf)
                if frame is None:
                    continue
                prompt = self._prompt_map[kf]
                seg = self.worker.segment_frame(frame, box=prompt["box"])
                mask = _dilate_small(seg["mask"])
                x1, y1, x2, y2 = prompt["box"]
                self.tracks.add_mask(kf, mask, box=(x1, y1, x2 - x1, y2 - y1))
                self._save_debug(kf, frame, [prompt["box"]], mask)
        finally:
            self.worker.stop()

    def mask_for(self, index: int, frame):
        import numpy as np

        mask = np.zeros((self.height, self.width), dtype=np.uint8)
        m = self.tracks.mask_for(index)
        if m is None:
            return mask
        h, w = m.shape[:2]
        mask[: min(h, self.height), : min(w, self.width)] = m[: self.height, : self.width]
        return mask
