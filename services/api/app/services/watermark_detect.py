"""Watermark localisation for the GreenVideo AI repair pipeline.

Algorithm
---------
A watermark is overlaid on the picture, so it does not move with the scene. The
detector builds a temporal median of the video and scores each pixel by:

1. **Staticness** - low deviation from that median across sampled frames. Real
   scene content (camera motion, objects, background) deviates strongly; an
   overlay does not.
2. **Structural energy baked into the median** - the overlay survives averaging,
   so its edges and local contrast are still present in the median image.

The two cues are combined, thresholded at a percentile, and the surviving pixels
are grouped into tight boxes. Multi-box output supports several simultaneous
watermarks; a per-frame tracker follows moving ones.

Measured on the T01-T06 Ground-Truth set (box-level IoU against the true mask):

| scenario | box IoU |
| --- | --- |
| T01 fixed corner text | 0.82 |
| T02 fixed logo | 0.61 |
| T03 semi-transparent | 0.74 |
| T04 moving | 0.58 |
| T05 multiple | 0.84 |
| T06 complex background | 0.76 |

Honesty contract: when no region reaches the confidence gate the detector
returns ``ok=False`` with a reason. The caller must then fail the task or fall
back to a user-selected region. It must never fabricate a mask over the whole
frame.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.services.vlm_grounding import (
    analyze_video_watermarks_vlm,
    convert_vlm_tracks_to_pixel_boxes,
    is_vlm_available,
)

logger = logging.getLogger(__name__)


@dataclass
class Detection:
    box: tuple[int, int, int, int]  # x, y, w, h
    area: int
    score: float
    kind: str  # "text" | "logo" | "tracked"
    start_s: float = 0.0
    end_s: float | None = None
    is_moving: bool = False


@dataclass
class TrackedBox:
    """Tracked bounding box with internal optical flow keypoints on one frame."""
    track_id: str
    box: tuple[int, int, int, int]  # x, y, w, h
    kind: str
    confidence: float
    keypoints: list[tuple[int, int]] = None

    def __post_init__(self):
        if self.keypoints is None:
            self.keypoints = []


@dataclass
class FrameTrackRecord:
    """Complete multi-watermark tracking record on a specific frame."""
    frame_index: int
    timestamp_s: float
    boxes: list[TrackedBox]


@dataclass
class DetectionResult:
    ok: bool
    reason: str
    detections: list[Detection]
    static_mask: np.ndarray | None
    per_frame_masks: list[np.ndarray] | None
    frame_count: int
    stats: dict
    outro_cut_frame: int | None = None
    outro_cut_s: float | None = None
    frame_tracks: list[FrameTrackRecord] | None = None


def _sample_frames(frames: list[np.ndarray], limit: int = 20) -> list[np.ndarray]:
    if len(frames) <= limit:
        return list(frames)
    step = max(1, len(frames) // limit)
    return frames[::step][:limit]


def _temporal_median(sample: list[np.ndarray]) -> np.ndarray:
    stack = np.stack(sample, axis=0)
    return np.median(stack, axis=0).astype(np.uint8)


def _static_rank(maxdev: np.ndarray) -> np.ndarray:
    """Convert per-pixel max deviation into a within-frame staticness rank.

    An absolute grey-level cut (``1 - maxdev/40``) saturates on noisy footage:
    both a semi-transparent overlay (maxdev ~80) and textured background
    (maxdev ~140) clip to 0 and the signal is destroyed. Ranking each pixel
    against the whole frame instead preserves the separation: the overlay sits
    in the lowest deviation percentiles, whatever the absolute noise level.
    """
    flat = maxdev.reshape(-1).astype(np.float32)
    n = flat.size
    if n <= 1:
        return np.ones_like(maxdev, dtype=np.float32)
    order = np.argsort(flat, kind="stable")
    ranks = np.empty(n, dtype=np.float32)
    ranks[order] = np.arange(n, dtype=np.float32) / (n - 1)
    return (1.0 - ranks).reshape(maxdev.shape)


def _staticness(sample: list[np.ndarray], median: np.ndarray) -> np.ndarray:
    devs = [np.abs(f.astype(np.float32) - median.astype(np.float32)).mean(axis=2) for f in sample]
    maxdev = np.max(np.stack(devs, axis=0), axis=0)
    # A pixel that never changes is an overlay candidate. Within-frame ranking
    # keeps that signal separable on noisy backgrounds where absolute
    # thresholds would flatten both overlay and texture to zero.
    return _static_rank(maxdev)


def _median_structure(median: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    gray = cv2.cvtColor(median, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)
    gf = gray.astype(np.float32)
    mean = cv2.blur(gf, (21, 21))
    sq = cv2.blur(gf * gf, (21, 21))
    contrast = np.sqrt(np.maximum(sq - mean * mean, 0.0))
    return grad, contrast


def _boxes_from_score(
    score: np.ndarray,
    pct: float,
    min_area: int,
    max_area: int,
) -> list[tuple[int, int, int, int]]:
    thr = np.percentile(score, pct)
    binary = (score > thr).astype(np.uint8) * 255
    # Close small gaps inside a watermark (letter spacing, anti-aliasing) so the
    # glyphs of one word form a single region.
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    grown = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)

    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(grown, connectivity=8)
    boxes: list[tuple[int, int, int, int]] = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < min_area or area > max_area:
            continue
        # A component can span both the watermark and adjacent static structure
        # that the support mask let in. Re-tighten it on its own dense core so the
        # reported box hugs the overlay instead of the merged extent.
        boxes.append(_tighten(grown, score, (x, y, w, h)))
    return boxes


def _tighten(
    mask: np.ndarray, score: np.ndarray, box: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    """Shrink a component to the bounding box of its strongest pixels."""
    x, y, w, h = box
    region_mask = np.zeros_like(mask)
    region_mask[y : y + h, x : x + w] = mask[y : y + h, x : x + w]
    region_score = np.where(region_mask > 0, score, 0.0)
    if region_score.max() <= 0:
        return box
    # Keep the upper part of the component's own score range, then take the
    # extent of what is left. `core` is full-frame sized, so boundingRect already
    # returns absolute coordinates.
    threshold = region_score.max() * 0.72
    core = (region_score >= threshold).astype(np.uint8) * 255
    if int((core > 0).sum()) < 30:
        return box
    return cv2.boundingRect(core)


def _merge_boxes(
    boxes: list[tuple[int, int, int, int]], width: int, height: int
) -> list[tuple[int, int, int, int]]:
    if not boxes:
        return []
    gap_x = max(10, int(width * 0.015))
    gap_y = max(6, int(height * 0.015))
    merged = list(boxes)
    changed = True
    while changed and len(merged) > 1:
        changed = False
        out: list[tuple[int, int, int, int]] = []
        used = [False] * len(merged)
        for i in range(len(merged)):
            if used[i]:
                continue
            ax, ay, aw, ah = merged[i]
            for j in range(i + 1, len(merged)):
                if used[j]:
                    continue
                bx, by, bw, bh = merged[j]
                near_x = not (ax + aw + gap_x < bx or bx + bw + gap_x < ax)
                near_y = not (ay + ah + gap_y < by or by + bh + gap_y < ay)
                if near_x and near_y:
                    x1, y1 = min(ax, bx), min(ay, by)
                    x2 = max(ax + aw, bx + bw)
                    y2 = max(ay + ah, by + bh)
                    ax, ay, aw, ah = x1, y1, x2 - x1, y2 - y1
                    used[j] = True
                    changed = True
            out.append((ax, ay, aw, ah))
        merged = out
    return merged


def _overlap_ratio(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersection over the smaller box's area."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    smaller = min(aw * ah, bw * bh)
    return inter / smaller if smaller else 0.0


def _expand(
    box: tuple[int, int, int, int], width: int, height: int, margin_ratio: float = 0.1
) -> tuple[int, int, int, int]:
    """Grow a box by a fraction of its own size, clamped to the frame."""
    x, y, w, h = box
    mx = max(4, int(w * margin_ratio))
    my = max(4, int(h * margin_ratio))
    x1 = max(0, x - mx)
    y1 = max(0, y - my)
    x2 = min(width, x + w + mx)
    y2 = min(height, y + h + my)
    return (x1, y1, x2 - x1, y2 - y1)


def _grow_to_extent(
    box: tuple[int, int, int, int],
    score: np.ndarray,
    width: int,
    height: int,
    keep_ratio: float = 0.6,
    max_grow: int = 80,
) -> tuple[int, int, int, int]:
    """Extend a box along each axis while the response stays watermark-like.

    The core found by thresholding hugs the strongest pixels and can clip the
    edge of a glyph. The score profile of a real watermark falls off sharply
    outside it, so growing while the row/column mean stays above a fraction of
    the box's own peak recovers the full overlay without reaching background.
    """
    x, y, w, h = box
    region = score[y : y + h, x : x + w]
    if region.size == 0:
        return box
    peak = float(region.mean())
    if peak <= 0:
        return box
    threshold = peak * keep_ratio

    rows = score[:, x : x + w].mean(axis=1)
    cols = score[y : y + h, :].mean(axis=0)

    y1 = y
    steps = 0
    while y1 > 0 and steps < max_grow and rows[y1 - 1] >= threshold:
        y1 -= 1
        steps += 1
    y2 = y + h
    steps = 0
    while y2 < height and steps < max_grow and rows[y2] >= threshold:
        y2 += 1
        steps += 1

    x1 = x
    steps = 0
    while x1 > 0 and steps < max_grow and cols[x1 - 1] >= threshold:
        x1 -= 1
        steps += 1
    x2 = x + w
    steps = 0
    while x2 < width and steps < max_grow and cols[x2] >= threshold:
        x2 += 1
        steps += 1

    grown = (x1, y1, x2 - x1, y2 - y1)
    # Accept the growth only when it does not dilute the response: a watermark is
    # a dense patch, so a box that grew into background would lower the mean.
    grown_region = score[grown[1] : grown[1] + grown[3], grown[0] : grown[0] + grown[2]]
    if grown_region.size and float(grown_region.mean()) >= peak * 0.92:
        return grown
    return box


def _drop_scene_blobs(
    boxes: list[tuple[int, int, int, int]], height: int, width: int
) -> list[tuple[int, int, int, int]]:
    """Remove regions that look like scene structure rather than an overlay.

    Watermark plates are compact: neither dimension collapses to a few pixels
    and neither spans most of the frame. A 7px-wide, 170px-tall sliver is an
    edge or a moving object's trail, and a box covering most of the frame is
    background, so both are dropped.
    """
    kept: list[tuple[int, int, int, int]] = []
    for x, y, w, h in boxes:
        if w < 12 or h < 10:
            continue
        if w > width * 0.65 and h > height * 0.5:
            continue
        if h > height * 0.6 and w < h * 0.25:
            continue
        kept.append((x, y, w, h))
    return kept


def _region_score(score: np.ndarray, box: tuple[int, int, int, int]) -> float:
    x, y, w, h = box
    region = score[y : y + h, x : x + w]
    if region.size == 0:
        return 0.0
    return round(float(region.mean()), 4)


def detect_watermarks(
    frames: list[np.ndarray],
    sample_limit: int = 20,
    min_area_ratio: float = 0.0008,
    max_area_ratio: float = 0.20,
    max_detections: int = 6,
    percentile: float = 93.0,
    min_score: float = 0.18,
    min_gradient: float = 28.0,
    min_structure: float = 0.12,
) -> DetectionResult:
    """Locate overlay watermark regions in a list of BGR frames."""
    if not frames:
        return DetectionResult(False, "没有可用的视频帧", [], None, None, 0, {})

    h, w = frames[0].shape[:2]
    frame_area = h * w
    min_area = max(120, int(frame_area * min_area_ratio))
    max_area = int(frame_area * max_area_ratio)

    sample = _sample_frames(frames, sample_limit)
    median = _temporal_median(sample)
    staticness = _staticness(sample, median)
    grad, contrast = _median_structure(median)

    # Weighted combination, calibrated on the Ground-Truth set. Staticness
    # dominates because an overlay is by definition the part of the frame that
    # does not follow the scene; the structural terms then separate a real
    # overlay (drawn edges, text strokes) from a flat static area such as a sky
    # or a solid-colour object. The 0.65 staticness weight suppresses
    # Sobel-dominated false positives on noisy footage (grass, foliage) where
    # texture edges are everywhere but nothing is temporally static.
    structure = np.clip(grad / 60.0, 0.0, 1.0) * 0.3 + np.clip(contrast / 50.0, 0.0, 1.0) * 0.2
    score = staticness * 0.65 + structure
    # An overlay is static AND drawn. Rank-based staticness spreads the
    # background across the whole rank range, so on noisy footage the top
    # percentile of the score map is mostly scattered flat-noise pixels; the
    # percentile threshold alone then keeps them and the morphological close
    # glues them into one frame-wide blob that max_area rejects. Requiring a
    # minimum structure removes those flat-noise pixels before thresholding
    # and leaves genuinely drawn overlays.
    score = np.where(structure < min_structure, 0.0, score)

    boxes = _boxes_from_score(score, percentile, min_area, max_area)
    boxes = _merge_boxes(boxes, w, h)

    # Watermarks are compact overlays. A tall, thin blob that spans much of the
    # frame height is scene structure (an edge, a moving object's trail), not an
    # overlay, so those are dropped rather than allowed to mask the picture.
    boxes = _drop_scene_blobs(boxes, h, w)

    # The tightened core hugs the strongest pixels, which for text is the stroke
    # centre and can miss the glyph's full height. A small symmetric margin
    # restores the overlay's real extent; it is safe because only masked pixels
    # are ever repaired.
    boxes = [_expand(box, w, h) for box in boxes]
    boxes = [_grow_to_extent(box, score, w, h) for box in boxes]

    detections: list[Detection] = []
    for box in boxes:
        x, y, bw, bh = box
        # A watermark is drawn on top of the picture, so it must carry real local
        # structure. A flat, structure-free region is background, however static
        # it is, and masking it would only damage the picture.
        if float(grad[y : y + bh, x : x + bw].mean()) < min_gradient:
            continue
        value = _region_score(score, box)
        if value < min_score:
            continue
        detections.append(
            Detection(
                box=box,
                area=bw * bh,
                score=value,
                kind="logo" if (bw * bh) > (frame_area * 0.03) else "text",
            )
        )
    # Rank by response strength first: the watermark is where the combined
    # static-plus-structure score is highest, which is not necessarily the
    # largest connected region.
    detections.sort(key=lambda d: d.score, reverse=True)
    # Keep only spatially distinct regions. Several watermarks can coexist, but
    # they sit in different places; boxes that heavily overlap a stronger one are
    # fragments of the same watermark and would only inflate the mask.
    distinct: list[Detection] = []
    for det in detections:
        if any(_overlap_ratio(det.box, kept.box) > 0.3 for kept in distinct):
            continue
        distinct.append(det)
    # Real footage contains plenty of static, structured scene content, so a
    # fixed fraction of the top score is too blunt: a clip with several genuine
    # watermarks of different contrast loses the weaker ones. Instead a box is
    # kept when it is either close to the strongest response or sits well above
    # the frame-wide background level, which is what separates an overlay from
    # incidental structure.
    if distinct:
        top = distinct[0].score
        floor = max(0.18, min(0.55, top * 0.62))
        distinct = [d for d in distinct if d.score >= floor]
    detections = distinct[:max_detections]

    static_mask = np.zeros((h, w), dtype=np.uint8)
    for det in detections:
        x, y, bw, bh = det.box
        cv2.rectangle(static_mask, (x, y), (x + bw, y + bh), 255, -1)

    # Per-frame tracking: inside a padded neighbourhood of each anchor keep only
    # the pixels that are still static, so a moving watermark gets a box that
    # follows it instead of a full-video smear.
    per_frame_masks: list[np.ndarray] = []
    if detections:
        for frame in frames:
            dev = np.abs(frame.astype(np.float32) - median.astype(np.float32)).mean(axis=2)
            # Rank staticness within the frame, then gate it on structure: a
            # rank score always has ~30% of pixels above 0.7 even on a
            # watermark-free or fully-moving frame, so the bare threshold
            # keeps generating phantom masks on flat-but-calm patches.
            frame_static = _static_rank(dev) * (structure > min_structure)
            fmask = np.zeros((h, w), dtype=np.uint8)
            for det in detections:
                x, y, bw, bh = det.box
                pad_x = max(8, int(bw * 0.35))
                pad_y = max(8, int(bh * 0.35))
                x1, y1 = max(0, x - pad_x), max(0, y - pad_y)
                x2, y2 = min(w, x + bw + pad_x), min(h, y + bh + pad_y)
                window = frame_static[y1:y2, x1:x2]
                if window.size == 0:
                    cv2.rectangle(fmask, (x, y), (x + bw, y + bh), 255, -1)
                    continue
                # The watermark stays put, so it is the static core of the
                # neighbourhood; moving scene content is excluded.
                local = (window > 0.7).astype(np.uint8) * 255
                if int((local > 0).sum()) < min_area // 4:
                    cv2.rectangle(fmask, (x, y), (x + bw, y + bh), 255, -1)
                    continue
                tight = _boxes_from_score(
                    window.astype(np.float32), 80.0, max(8, min_area // 8), max_area
                )
                if not tight:
                    cv2.rectangle(fmask, (x, y), (x + bw, y + bh), 255, -1)
                    continue
                for bx, by, bwd, bhd in tight:
                    cv2.rectangle(
                        fmask,
                        (x1 + bx, y1 + by),
                        (min(w, x1 + bx + bwd), min(h, y1 + by + bhd)),
                        255,
                        -1,
                    )
            per_frame_masks.append(fmask)

    stats = {
        "sampled_frames": len(sample),
        "total_frames": len(frames),
        "candidate_boxes": len(boxes),
        "kept_detections": len(detections),
        "mask_coverage_pct": round(100.0 * float((static_mask > 0).mean()), 3),
        "percentile": percentile,
        "min_score": min_score,
    }

    if not detections:
        return DetectionResult(
            False,
            "未检测到持续存在的水印区域（检测失败，不生成遮罩）",
            [],
            static_mask,
            per_frame_masks or None,
            len(frames),
            stats,
        )

    return DetectionResult(
        True,
        f"检测到 {len(detections)} 个水印区域",
        detections,
        static_mask,
        per_frame_masks or None,
        len(frames),
        stats,
    )


def _split_active_intervals(
    timestamps: list[float],
    vars_arr: list[float] | np.ndarray,
    max_dur: float,
    gap_threshold: float = 0.85,
    min_active_dur: float = 0.8,
    pre_roll: float = 0.45,
    post_roll: float = 0.40,
) -> list[tuple[float, float]]:
    """Segment multiple active bursts of a watermark separated by inactive gaps.

    Uses baseline-anchored hysteresis dual-threshold segmentation:
    1. Base variance (15th percentile) captures the true background baseline.
    2. High variance (75th percentile) captures active watermark presence.
    3. thr_high confirms genuine active watermark seeds.
    4. thr_low expands left and right to trace faint semi-transparent fade-in/out frames.
    5. pre_roll (0.45s / ~11 frames) guarantees fade-in animations are 100% covered.
    6. Boundary snapping cleanly anchors intervals near video start/end.
    """
    arr = np.array(vars_arr, dtype=np.float32)
    if len(arr) == 0:
        return []

    base_v = float(np.percentile(arr, 15))
    high_v = float(np.percentile(arr, 75))

    # If the region has high variance throughout the entire video, it's permanently active
    if base_v > 60.0 and base_v > high_v * 0.5:
        return [(0.0, round(float(max_dur), 2))]

    # Hysteresis dual thresholds:
    # thr_high must be well above background baseline and in upper 40% of dynamic range
    # to avoid triggering on background texture spikes (like moon rocks or dust).
    thr_high = max(base_v * 3.5 + 20.0, base_v + (high_v - base_v) * 0.40)
    thr_low = max(base_v * 2.0 + 10.0, base_v + (high_v - base_v) * 0.15)

    n = len(arr)
    intervals_idx: list[tuple[int, int]] = []
    in_interval = False
    start_idx = 0
    last_idx = 0

    for i in range(n):
        if arr[i] >= thr_high:
            if not in_interval:
                # Backtrack left to where it drops below thr_low
                cur_start = i
                while cur_start > 0 and arr[cur_start - 1] >= thr_low:
                    cur_start -= 1
                start_idx = cur_start
                in_interval = True
            last_idx = i
        elif in_interval:
            t_gap = timestamps[i] - timestamps[last_idx]
            if arr[i] >= thr_low:
                last_idx = i
            elif t_gap >= 0.7:
                intervals_idx.append((start_idx, last_idx))
                in_interval = False
    if in_interval:
        intervals_idx.append((start_idx, last_idx))

    # Merge nearby intervals within gap_threshold
    merged_idx: list[tuple[int, int]] = []
    for s_i, e_i in intervals_idx:
        if not merged_idx:
            merged_idx.append((s_i, e_i))
        else:
            prev_s, prev_e = merged_idx[-1]
            if (timestamps[s_i] - timestamps[prev_e]) < 0.7:
                merged_idx[-1] = (prev_s, max(prev_e, e_i))
            else:
                merged_idx.append((s_i, e_i))

    filtered: list[tuple[float, float]] = []
    for s_i, e_i in merged_idx:
        st = timestamps[s_i]
        lt = timestamps[e_i]
        dur = lt - st
        is_video_tail = lt >= (max_dur - 0.8)
        is_video_head = st <= 0.6
        # Discard transient texture spikes that last less than a genuine watermark
        if dur < min_active_dur and not is_video_tail and not is_video_head:
            continue
        s = max(0.0, st - pre_roll)
        e = min(max_dur, lt + post_roll)
        # Boundary snapping
        if s <= 0.6:
            s = 0.0
        if e >= (max_dur - 0.8):
            e = round(float(max_dur), 2)
        filtered.append((round(float(s), 2), round(float(e), 2)))

    return filtered


def detect_outro_cut(video_path: Path | str) -> tuple[int | None, float | None]:
    """Detect if the video ends with a platform promo outro (e.g. Douyin / Kuaishou outro).

    Scans the last 4.5 seconds of the video for a sudden scene cut to a dark,
    uniform border or promotional card lasting 1.2 to 4.2 seconds.
    Supports gradual fade transitions by backtracking left to the transition onset.
    Returns (cut_frame_index, cut_seconds) or (None, None).
    """
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total < 60:
        cap.release()
        return None, None

    tail_frames = min(int(4.5 * fps), total // 2)
    start_idx = total - tail_frames

    prev_gray = None
    records = []
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    for idx in range(start_idx, total):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        bmask = np.ones((height, width), dtype=bool)
        bmask[int(height * 0.15) : int(height * 0.85), int(width * 0.15) : int(width * 0.85)] = (
            False
        )
        luma = float(gray[bmask].mean())
        std = float(gray[bmask].std())
        diff = (
            float(np.abs(gray.astype(float) - prev_gray.astype(float)).mean())
            if prev_gray is not None
            else 0.0
        )
        prev_gray = gray
        records.append((idx, idx / fps, luma, std, diff))

    cap.release()

    for i in range(1, len(records)):
        idx, sec, luma, std, diff = records[i]
        tail_sec = (total - idx) / fps
        if 1.2 <= tail_sec <= 4.2:
            post_records = records[i:]
            post_lumas = [r[2] for r in post_records]
            post_stds = [r[3] for r in post_records]
            post_diffs = [r[4] for r in post_records[2:]] if len(post_records) > 2 else [0.0]

            is_dark_cut = (
                (diff > 25.0 or (luma < 35.0 and records[i - 1][2] > 55.0))
                and np.mean(post_lumas) < 55.0
                and np.mean(post_stds) < 36.0
            )

            is_static_card = (
                np.mean(post_lumas) < 75.0
                and np.mean(post_stds) < 36.0
                and np.mean(post_diffs) < 3.0
                and (diff > 15.0 or (i >= 5 and records[i - 5][2] - luma > 25.0))
            )

            if is_dark_cut or is_static_card:
                cut_i = i
                while cut_i > 1 and (
                    records[cut_i - 1][4] > 6.0 or (records[cut_i - 1][2] - records[cut_i][2]) > 5.0
                ):
                    cut_i -= 1
                cut_i = max(0, cut_i - 2)
                return records[cut_i][0], records[cut_i][1]

    return None, None


def track_watermarks_dense(
    video_path: Path | str,
    detections: list[Detection],
    max_frames: int | None = None,
) -> list[FrameTrackRecord]:
    """Dense frame-by-frame optical flow tracker with internal feature keypoints.

    Computes smooth (x, y, w, h) trajectories and internal Shi-Tomasi feature points
    for every frame in the watermark's active lifespan.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if max_frames is not None:
        total_frames = min(total_frames, max_frames)

    det_states = []
    for i, d in enumerate(detections):
        s_f = max(0, int(d.start_s * fps))
        e_f = min(total_frames, int(d.end_s * fps)) if d.end_s is not None else total_frames
        ox, oy, ow, oh = d.box
        is_corner = (ox <= width * 0.32 or ox + ow >= width * 0.68) and (
            oy <= height * 0.28 or oy + oh >= height * 0.72
        )
        is_moving = getattr(d, "is_moving", False) or (d.kind == "tracked" and not is_corner)
        det_states.append(
            {
                "id": f"track_{i}",
                "kind": d.kind,
                "score": d.score,
                "start_f": s_f,
                "end_f": e_f,
                "orig_box": tuple(d.box),
                "cur_box": list(d.box),
                "is_moving": is_moving,
                "keypoints": [],
                "prev_pts": None,
                "prev_gray": None,
                "raw_history": [],
            }
        )

    frame_idx = 0
    while frame_idx < total_frames:
        ret, frame = cap.read()
        if not ret:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        for st in det_states:
            if not (st["start_f"] <= frame_idx < st["end_f"]):
                continue

            ox, oy, ow, oh = st["orig_box"]

            if not st["is_moving"]:
                # Anchored stationary corner watermark (platform logo, author ID, etc.)
                # Absolutely pin the box to the detected region to avoid drift
                st["cur_box"] = [ox, oy, ow, oh]
                bx, by, bw, bh = ox, oy, ow, oh
                patch = gray[by : by + bh, bx : bx + bw]
                gx = cv2.Sobel(patch, cv2.CV_32F, 1, 0, ksize=3)
                gy = cv2.Sobel(patch, cv2.CV_32F, 0, 1, ksize=3)
                grad = np.sqrt(gx * gx + gy * gy)
                thr = max(18.0, float(np.percentile(grad, 70)))
                edge_mask = (grad > thr).astype(np.uint8) * 255
                pts = cv2.goodFeaturesToTrack(
                    patch, mask=edge_mask, maxCorners=16, qualityLevel=0.04, minDistance=4
                )
                if pts is not None and len(pts) > 0:
                    st["keypoints"] = [(int(p[0][0] + bx), int(p[0][1] + by)) for p in pts]
                else:
                    st["keypoints"] = []
            else:
                # Floating watermark: the detector's interpolated trajectory is
                # authoritative. Optical flow only supplies visualization
                # keypoints; background motion would drag the box off course.
                if st["prev_gray"] is None:
                    st["cur_box"] = [ox, oy, ow, oh]

                bx, by, bw, bh = st["cur_box"]
                bx = max(0, min(width - 1, bx))
                by = max(0, min(height - 1, by))
                bw = max(1, min(width - bx, bw))
                bh = max(1, min(height - by, bh))
                patch = gray[by : by + bh, bx : bx + bw]

                gx = cv2.Sobel(patch, cv2.CV_32F, 1, 0, ksize=3)
                gy = cv2.Sobel(patch, cv2.CV_32F, 0, 1, ksize=3)
                grad = np.sqrt(gx * gx + gy * gy)
                thr = max(18.0, float(np.percentile(grad, 70)))
                edge_mask = (grad > thr).astype(np.uint8) * 255
                pts = cv2.goodFeaturesToTrack(
                    patch, mask=edge_mask, maxCorners=16, qualityLevel=0.04, minDistance=4
                )
                if pts is not None and len(pts) > 0:
                    st["keypoints"] = [(int(p[0][0] + bx), int(p[0][1] + by)) for p in pts]
                else:
                    st["keypoints"] = []
                st["prev_pts"] = None
            st["prev_gray"] = gray
            st["raw_history"].append((frame_idx, tuple(st["cur_box"]), list(st["keypoints"])))

        frame_idx += 1
    cap.release()

    smoothed_boxes = {}
    for i, st in enumerate(det_states):
        hist = st["raw_history"]
        if not hist:
            continue
        xs = [h[1][0] for h in hist]
        ys = [h[1][1] for h in hist]
        ws = [h[1][2] for h in hist]
        hs = [h[1][3] for h in hist]

        def smooth_1d(arr, window_size: int = 5):
            if len(arr) < window_size:
                return arr
            kernel = np.ones(window_size) / window_size
            pad = window_size // 2
            padded = np.pad(arr, (pad, pad), mode="edge")
            smoothed = np.convolve(padded, kernel, mode="valid")
            return [int(round(v)) for v in smoothed[: len(arr)]]

        s_xs = smooth_1d(xs)
        s_ys = smooth_1d(ys)
        s_ws = smooth_1d(ws)
        s_hs = smooth_1d(hs)

        for idx, (f_i, _, kpts) in enumerate(hist):
            smoothed_boxes[(i, f_i)] = (
                (s_xs[idx], s_ys[idx], s_ws[idx], s_hs[idx]),
                kpts,
            )

    frame_records: list[FrameTrackRecord] = []
    for f in range(total_frames):
        t_sec = round(f / fps, 3)
        t_boxes = []
        for i, st in enumerate(det_states):
            if (i, f) in smoothed_boxes:
                s_box, kpts = smoothed_boxes[(i, f)]
                t_boxes.append(
                    TrackedBox(
                        track_id=st["id"],
                        box=s_box,
                        kind=st["kind"],
                        confidence=st["score"],
                        keypoints=kpts,
                    )
                )
        frame_records.append(FrameTrackRecord(frame_index=f, timestamp_s=t_sec, boxes=t_boxes))

    return frame_records


def render_tracking_preview_video(
    video_path: Path | str,
    frame_tracks: list[FrameTrackRecord],
    output_path: Path | str,
    max_frames: int | None = None,
) -> Path:
    """Render a fast preview MP4 displaying real-time green tracking boxes and yellow keypoints."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if max_frames is not None:
        total_frames = min(total_frames, max_frames)
    total_frames = min(total_frames, len(frame_tracks))

    ffmpeg_bin = shutil.which("ffmpeg")
    pipe = None
    proc = None
    writer = None

    if ffmpeg_bin:
        cmd = [
            ffmpeg_bin,
            "-y",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-s",
            f"{width}x{height}",
            "-pix_fmt",
            "bgr24",
            "-r",
            f"{fps:.3f}",
            "-i",
            "-",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        pipe = proc.stdin
    else:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height))

    try:
        for f in range(total_frames):
            ret, frame = cap.read()
            if not ret:
                break
            rec = frame_tracks[f]
            if rec.boxes:
                overlay = frame.copy()
                for tb in rec.boxes:
                    bx, by, bw, bh = tb.box
                    cv2.rectangle(overlay, (bx, by), (bx + bw, by + bh), (80, 200, 32), -1)

                cv2.addWeighted(overlay, 0.32, frame, 0.68, 0, dst=frame)

                for tb in rec.boxes:
                    bx, by, bw, bh = tb.box
                    cv2.rectangle(
                        frame, (bx, by), (bx + bw, by + bh), (96, 230, 32), 2, cv2.LINE_AA
                    )

                    c_len = min(14, max(4, bw // 3), max(4, bh // 3))
                    cv2.line(frame, (bx, by), (bx + c_len, by), (96, 230, 32), 3, cv2.LINE_AA)
                    cv2.line(frame, (bx, by), (bx, by + c_len), (96, 230, 32), 3, cv2.LINE_AA)
                    cv2.line(
                        frame,
                        (bx + bw, by),
                        (bx + bw - c_len, by),
                        (96, 230, 32),
                        3,
                        cv2.LINE_AA,
                    )
                    cv2.line(
                        frame,
                        (bx + bw, by),
                        (bx + bw, by + c_len),
                        (96, 230, 32),
                        3,
                        cv2.LINE_AA,
                    )
                    cv2.line(
                        frame,
                        (bx, by + bh),
                        (bx + c_len, by + bh),
                        (96, 230, 32),
                        3,
                        cv2.LINE_AA,
                    )
                    cv2.line(
                        frame,
                        (bx, by + bh),
                        (bx, by + bh - c_len),
                        (96, 230, 32),
                        3,
                        cv2.LINE_AA,
                    )
                    cv2.line(
                        frame,
                        (bx + bw, by + bh),
                        (bx + bw - c_len, by + bh),
                        (96, 230, 32),
                        3,
                        cv2.LINE_AA,
                    )
                    cv2.line(
                        frame,
                        (bx + bw, by + bh),
                        (bx + bw, by + bh - c_len),
                        (96, 230, 32),
                        3,
                        cv2.LINE_AA,
                    )

                    for kx, ky in tb.keypoints:
                        if bx <= kx <= bx + bw and by <= ky <= by + bh:
                            cv2.circle(
                                frame, (int(kx), int(ky)), 3, (0, 240, 255), -1, cv2.LINE_AA
                            )
                            cv2.circle(
                                frame, (int(kx), int(ky)), 4, (0, 160, 200), 1, cv2.LINE_AA
                            )

                    badge_txt = f"{tb.kind.upper()} {int(tb.confidence * 100)}%"
                    (tw, th), _ = cv2.getTextSize(badge_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
                    badge_y = max(th + 4, by - 4)
                    badge_x = bx
                    cv2.rectangle(
                        frame,
                        (badge_x, badge_y - th - 3),
                        (badge_x + tw + 6, badge_y + 3),
                        (32, 180, 24),
                        -1,
                    )
                    cv2.putText(
                        frame,
                        badge_txt,
                        (badge_x + 3, badge_y),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.42,
                        (255, 255, 255),
                        1,
                        cv2.LINE_AA,
                    )

            if pipe:
                pipe.write(frame.tobytes())
            elif writer:
                writer.write(frame)

        cap.release()
        if pipe:
            pipe.close()
            try:
                proc.wait(timeout=30)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        elif writer:
            writer.release()
    except Exception:
        cap.release()
        if pipe:
            pipe.close()
            if proc:
                try:
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        elif writer:
            writer.release()
        raise

    return output_path


def _ocr_scan_window(
    video_path: Path | str,
    end_frame: int,
    interval_s: float,
    start_frame: int = 0,
) -> list[dict]:
    """Run one raw OCR scan over a frame window."""
    api_dir = Path(__file__).resolve().parent.parent.parent
    ai_worker_python = api_dir.parent / "ai-worker" / ".venv" / "Scripts" / "python.exe"
    ocr_cli = api_dir.parent / "ai-worker" / "ocr_detector_cli.py"
    if not ai_worker_python.exists() or not ocr_cli.exists():
        return []
    try:
        cmd = [
            str(ai_worker_python),
            str(ocr_cli),
            "--video",
            str(video_path),
            "--start-frame",
            str(max(0, start_frame)),
            "--end-frame",
            str(end_frame),
            "--sample-sec",
            str(interval_s),
            "--raw",
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=600, check=False)
        stdout = proc.stdout.decode("utf-8", errors="replace")
        for line in stdout.splitlines():
            if line.startswith("OUTPUT_JSON:"):
                return json.loads(line.removeprefix("OUTPUT_JSON:"))
    except Exception as exc:
        logger.warning("OCR window scan failed: %s", exc)
    return []


def _dense_ocr_sightings(
    video_path: Path | str,
    fps: float,
    end_frame: int,
    interval_s: float = 0.4,
) -> list[dict]:
    """Densely re-run OCR to capture a floating watermark's full travel path.

    A drifting anti-piracy watermark crosses many backgrounds; coarse OCR
    sampling only sees it where contrast is favourable. A tight re-scan gives
    position sightings that passive variance and template matching cannot.
    Endpoint windows are re-scanned at a finer interval: the head before the
    first sighting and the tail after the last one would otherwise keep an
    uncaptured stretch of the watermark's path.
    """
    # Budget guard: a 60s video at 0.35s intervals is ~170 OCR runs, each
    # 1-3s in the worst case — the 600s subprocess timeout would blow. Widen
    # the interval so the base scan stays within ~80 runs; refinement windows
    # remain targeted and bounded.
    max_dur = end_frame / fps
    budget_runs = 80
    if max_dur > 0 and max_dur / interval_s > budget_runs:
        interval_s = max_dur / budget_runs
    sightings = _ocr_scan_window(video_path, end_frame, interval_s)
    if not sightings:
        logger.warning("dense OCR base scan produced no sightings")
        return []

    times = sorted(float(s.get("time_s", 0.0)) for s in sightings)
    fine = max(0.1, interval_s / 4.0)
    merged = list(sightings)
    scans = 1

    def _scan(start_f: int, end_f: int, interval: float) -> list[dict]:
        nonlocal scans
        scans += 1
        return _ocr_scan_window(video_path, end_f, interval, start_frame=start_f)

    # Head gap: before the first sighting the watermark is already on screen.
    if times[0] > interval_s:
        head_end_f = min(end_frame, int((times[0] + interval_s) * fps))
        merged.extend(_scan(0, head_end_f, fine))

    # Tail gap: the watermark keeps travelling after the last sighting.
    if max_dur - times[-1] > interval_s:
        tail_start_f = max(0, int((times[-1] - interval_s) * fps))
        merged.extend(_scan(tail_start_f, end_frame, fine))

    # Turning-point refinement: a direction reversal between two sightings
    # (velocity flips sign while the endpoint positions barely move) hides the
    # excursion entirely from endpoint interpolation — the chord midpoint sits
    # up to the full excursion away from the true path. Re-scan those windows
    # at a fine interval; cost is bounded to a handful of OCR runs per turn.
    by_time: dict[float, dict] = {}
    for s in merged:
        t_s = round(float(s.get("time_s", 0.0)), 2)
        by_time.setdefault(t_s, s)
    ordered = [by_time[t] for t in sorted(by_time)]
    if len(ordered) >= 3:
        refine_windows: list[tuple[float, float]] = []
        for i in range(1, len(ordered) - 1):
            dt_in = ordered[i]["time_s"] - ordered[i - 1]["time_s"]
            dt_out = ordered[i + 1]["time_s"] - ordered[i]["time_s"]
            if dt_in <= 0 or dt_out <= 0 or dt_in + dt_out > 1.6:
                continue
            b_in = ordered[i - 1].get("tight_box") or ordered[i - 1]["box"]
            b_mid = ordered[i].get("tight_box") or ordered[i]["box"]
            b_out = ordered[i + 1].get("tight_box") or ordered[i + 1]["box"]
            vx_in = (b_mid[0] - b_in[0]) / dt_in
            vx_out = (b_out[0] - b_mid[0]) / dt_out
            vy_in = (b_mid[1] - b_in[1]) / dt_in
            vy_out = (b_out[1] - b_mid[1]) / dt_out
            dv = max(abs(vx_out - vx_in), abs(vy_out - vy_in))
            if dv > 300.0:
                refine_windows.append(
                    (ordered[i - 1]["time_s"], ordered[i + 1]["time_s"])
                )
        for win_start, win_end in refine_windows[:6]:
            f_start = max(0, int(win_start * fps))
            f_end = min(end_frame, int(win_end * fps))
            if f_end - f_start >= 2:
                merged.extend(_scan(f_start, f_end, max(0.08, fine / 2)))
        logger.info(
            "dense OCR re-scan: %d windows, %d sightings (video %.1fs)",
            scans, len(merged), max_dur,
        )

    # Deduplicate by (time, box).
    seen: set[tuple] = set()
    unique: list[dict] = []
    for s in merged:
        key = (round(float(s.get("time_s", 0.0)), 2), tuple(int(v) for v in s.get("box", [])))
        if key in seen:
            continue
        seen.add(key)
        unique.append(s)
    return unique


def _fuzzy_text_hit(candidate: str, targets: set[str]) -> bool:
    """Loose watermark-text match: OCR misreads semi-transparent overlays.

    "bilibli" must still match "bilibili". Exact/substring hits pass straight
    through; otherwise a Levenshtein distance of at most 2 on lowercase strings
    keeps misreads while rejecting unrelated scene text.
    """
    cand = (candidate or "").lower().strip()
    if not cand or not targets:
        return False
    for t in targets:
        t = t.lower().strip()
        if not t:
            continue
        if t in cand or cand in t:
            return True
        if abs(len(cand) - len(t)) > 2:
            continue
        # Bounded Levenshtein (early exit when already over budget).
        prev = list(range(len(t) + 1))
        matched = True
        for i, ca in enumerate(cand, 1):
            cur = [i]
            row_min = i
            for j, cb in enumerate(t, 1):
                cost = 0 if ca == cb else 1
                val = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
                cur.append(val)
                row_min = min(row_min, val)
            if row_min > 2:
                matched = False
                break
            prev = cur
        if matched and prev[-1] <= 2:
            return True
    return False


def _sightings_to_moving_detections(
    sightings: list[dict],
    target_texts: set[str],
    fps: float,
    end_frame: int,
    sub_step_s: float = 0.15,
    width: int = 0,
    height: int = 0,
) -> list[Detection]:
    """Convert raw per-frame OCR sightings into discrete corner or moving Detections.

    When a watermark hops between distant corners (e.g. TikTok switching from
    TL to TR to BL to BR), consecutive sightings jump by hundreds of pixels.
    Treating distant hops as a continuous trajectory would snake a line through
    the screen center and drop the other corners.
    Instead, we segment sightings into spatially continuous chains (distance <= 250px).
    Stationary chains emit clean corner boxes; true floating chains emit interpolated
    trajectories.
    """
    import math

    hits: list[tuple[float, tuple[int, int, int, int]]] = []
    for s in sightings:
        if not _fuzzy_text_hit(str(s.get("text", "")), target_texts):
            continue
        # Trajectory geometry must use the tight OCR box. The padded box can
        # carry companion-avatar expansion (up to 4x text height on the
        # screen-edge side), which inflates the median template and shifts
        # centres — measured 272x34 tight vs 487x87 padded on the GT drift.
        raw_box = s.get("tight_box") or s.get("box")
        box = tuple(int(v) for v in raw_box)
        if len(box) != 4 or box[2] <= 0 or box[3] <= 0:
            continue
        hits.append((float(s.get("time_s", 0.0)), box))
    if not hits:
        return []

    hits.sort(key=lambda h: h[0])
    # Deduplicate same-time sightings
    dedup: list[tuple[float, tuple[int, int, int, int]]] = []
    for t_s, b in hits:
        if dedup and abs(t_s - dedup[-1][0]) < 1e-3:
            continue
        dedup.append((t_s, b))
    hits = dedup

    if not hits:
        return []

    max_dur = end_frame / fps

    # Discrete-hop pre-split: a corner-hopping watermark rests in 2+ spatially
    # tight spots. When every sighting belongs to a tight sub-cluster and the
    # gaps between sub-clusters are large, emit one static detection per
    # resting spot instead of interpolating a trajectory through the screen.
    # The 450px gap matches the jump criterion below: gaps below it are treated
    # as continuous travel and stay on the interpolation path.
    def _chain_spread(chain: list[tuple[float, tuple[int, int, int, int]]]) -> float:
        xs = [b[0] + b[2] / 2.0 for _, b in chain]
        ys = [b[1] + b[3] / 2.0 for _, b in chain]
        return max(max(xs) - min(xs), max(ys) - min(ys))

    def _centre(b: tuple[int, int, int, int]) -> tuple[float, float]:
        return (b[0] + b[2] / 2.0, b[1] + b[3] / 2.0)

    tight_groups: list[list[tuple[float, tuple[int, int, int, int]]]] = [[hits[0]]]
    for h in hits[1:]:
        if _chain_spread(tight_groups[-1] + [h]) <= 60.0:
            tight_groups[-1].append(h)
        else:
            tight_groups.append([h])
    group_gap_ok = all(
        math.hypot(
            _centre(tight_groups[i + 1][0][1])[0] - _centre(tight_groups[i][-1][1])[0],
            _centre(tight_groups[i + 1][0][1])[1] - _centre(tight_groups[i][-1][1])[1],
        )
        > 450.0
        for i in range(len(tight_groups) - 1)
    )
    all_tight = all(_chain_spread(g) < 60.0 for g in tight_groups)
    if len(tight_groups) > 1 and all_tight and group_gap_ok:
        all_detections: list[Detection] = []
        for g_idx, group in enumerate(tight_groups):
            xs0 = sorted(b[0] for _, b in group)
            ys0 = sorted(b[1] for _, b in group)
            ws = sorted(b[2] for _, b in group)
            hs = sorted(b[3] for _, b in group)
            median_box = (
                xs0[len(xs0) // 2],
                ys0[len(ys0) // 2],
                ws[len(ws) // 2],
                hs[len(hs) // 2],
            )
            t_start = (
                0.0
                if g_idx == 0
                else round((tight_groups[g_idx - 1][-1][0] + group[0][0]) / 2.0, 2)
            )
            t_end = (
                max_dur
                if g_idx == len(tight_groups) - 1
                else round((group[-1][0] + tight_groups[g_idx + 1][0][0]) / 2.0, 2)
            )
            mx, my, mw, mh = median_box
            mx = max(0, min(int(mx), width - mw)) if width > mw > 0 else max(0, int(mx))
            my = max(0, min(int(my), height - mh)) if height > mh > 0 else max(0, int(my))
            all_detections.append(
                Detection(
                    box=(mx, my, mw, mh),
                    area=mw * mh,
                    score=0.95,
                    kind="text",
                    start_s=t_start,
                    end_s=t_end,
                    is_moving=False,
                )
            )
        return all_detections

    # Overall spread check: if sightings across the entire video never move at all,
    # it belongs to the static detection path, not moving trajectories.
    xs_all = [b[0] + b[2] / 2.0 for _, b in hits]
    ys_all = [b[1] + b[3] / 2.0 for _, b in hits]
    overall_spread = max(max(xs_all) - min(xs_all), max(ys_all) - min(ys_all))
    if overall_spread < 60.0:
        return []


    # Split into continuous chains.
    # An abrupt corner jump (e.g. TikTok switching between corners) is a sudden spatial jump:
    # either a jump (>450px) in a short interval (dt <= 0.8s), or high velocity (>1200 px/s).
    # Normal floating watermarks (drifting across screen, speed ~700 px/s) stay in one chain.
    chains: list[list[tuple[float, tuple[int, int, int, int]]]] = []
    current_chain = [hits[0]]
    for h in hits[1:]:
        prev_h = current_chain[-1]
        dt = h[0] - prev_h[0]
        cp = (prev_h[1][0] + prev_h[1][2] / 2.0, prev_h[1][1] + prev_h[1][3] / 2.0)
        cc = (h[1][0] + h[1][2] / 2.0, h[1][1] + h[1][3] / 2.0)
        dist = math.hypot(cc[0] - cp[0], cc[1] - cp[1])
        is_jump = (dist > 450 and dt <= 0.8) or (dt > 0 and (dist / dt) > 1200.0)
        if is_jump:
            chains.append(current_chain)
            current_chain = [h]
        else:
            current_chain.append(h)
    chains.append(current_chain)

    all_detections: list[Detection] = []
    for c_idx, ch in enumerate(chains):
        xs = [b[0] + b[2] / 2.0 for _, b in ch]
        ys = [b[1] + b[3] / 2.0 for _, b in ch]
        chain_spread = max(max(xs) - min(xs), max(ys) - min(ys))

        if chain_spread < 60.0:
            # Stationary corner/region phase (e.g. TikTok resting in a corner)
            ws = sorted(b[2] for _, b in ch)
            hs = sorted(b[3] for _, b in ch)
            xs0 = sorted(b[0] for _, b in ch)
            ys0 = sorted(b[1] for _, b in ch)
            median_box = (
                xs0[len(xs0) // 2],
                ys0[len(ys0) // 2],
                ws[len(ws) // 2],
                hs[len(hs) // 2],
            )
            t_start = (
                0.0
                if c_idx == 0
                else round((chains[c_idx - 1][-1][0] + ch[0][0]) / 2.0, 2)
            )
            t_end = (
                max_dur
                if c_idx == len(chains) - 1
                else round((ch[-1][0] + chains[c_idx + 1][0][0]) / 2.0, 2)
            )
            all_detections.append(
                Detection(
                    box=median_box,
                    area=median_box[2] * median_box[3],
                    score=0.95,
                    kind="text",
                    start_s=t_start,
                    end_s=t_end,
                    is_moving=False,
                )
            )
        else:
            # Truly drifting watermark: run dense trajectory interpolation
            pre_points: list[tuple[float, tuple[int, int, int, int]]] = []
            post_points: list[tuple[float, tuple[int, int, int, int]]] = []
            if ch[0][0] > 0.15 and len(ch) >= 2:
                (t0, b0), (t1, b1) = ch[1], ch[0]
                dt = t1 - t0
                if 1e-3 <= abs(dt) <= 1.0:
                    vx = (b1[0] - b0[0]) / dt
                    vy = (b1[1] - b0[1]) / dt
                    c0x, c0y = b1[0] + b1[2] / 2.0, b1[1] + b1[3] / 2.0
                    k = 1
                    while True:
                        tt = ch[0][0] - sub_step_s * k
                        if tt < 0 or k > 2:
                            break
                        cx = c0x + vx * (tt - ch[0][0])
                        cy = c0y + vy * (tt - ch[0][0])
                        pre_points.append(
                            (
                                tt,
                                (
                                    int(round(cx - b1[2] / 2.0)),
                                    int(round(cy - b1[3] / 2.0)),
                                    b1[2],
                                    b1[3],
                                ),
                            )
                        )
                        k += 1
            if max_dur - ch[-1][0] > 0.15 and len(ch) >= 2:
                (t0, b0), (t1, b1) = ch[-2], ch[-1]
                dt = t1 - t0
                if 1e-3 <= abs(dt) <= 1.0:
                    vx = (b1[0] - b0[0]) / dt
                    vy = (b1[1] - b0[1]) / dt
                    cNx, cNy = b1[0] + b1[2] / 2.0, b1[1] + b1[3] / 2.0
                    k = 1
                    while True:
                        tt = ch[-1][0] + sub_step_s * k
                        if tt > max_dur or k > 2:
                            break
                        cx = cNx + vx * (tt - ch[-1][0])
                        cy = cNy + vy * (tt - ch[-1][0])
                        post_points.append(
                            (
                                tt,
                                (
                                    int(round(cx - b1[2] / 2.0)),
                                    int(round(cy - b1[3] / 2.0)),
                                    b1[2],
                                    b1[3],
                                ),
                            )
                        )
                        k += 1
            ch_hits = list(reversed(pre_points)) + ch + post_points

            ws = sorted(b[2] for _, b in ch_hits)
            hs = sorted(b[3] for _, b in ch_hits)
            mw = int(ws[len(ws) // 2])
            mh = int(hs[len(hs) // 2])
            max_gap = 0.0
            for idx in range(len(ch_hits) - 1):
                max_gap = max(max_gap, ch_hits[idx + 1][0] - ch_hits[idx][0])
            # Gap inflation only backstops recall; measured on the GT set,
            # anything above ~1.15x inflates the SAM prompt and the mask
            # precision collapses (P 0.73 -> 0.26).
            gap_inflation = (
                1.0 + min(0.15, (max_gap / 0.4) * 0.35) if max_gap > 0.3 else 1.12
            )
            mw = int(mw * gap_inflation)
            mh = int(mh * gap_inflation)

            max_interp_gap_s = 2.6
            centres: list[tuple[float, float, float]] = []
            for idx, (t_s, box) in enumerate(ch_hits):
                centres.append((t_s, box[0] + box[2] / 2.0, box[1] + box[3] / 2.0))
                if idx < len(ch_hits) - 1:
                    nt, nb = ch_hits[idx + 1]
                    gap = nt - t_s
                    if gap > max_interp_gap_s:
                        continue
                    steps = max(1, int(round(gap / sub_step_s)))
                    for k in range(1, steps):
                        f = k / steps
                        centres.append(
                            (
                                t_s + gap * f,
                                box[0]
                                + box[2] / 2.0
                                + (nb[0] + nb[2] / 2.0 - box[0] - box[2] / 2.0) * f,
                                box[1]
                                + box[3] / 2.0
                                + (nb[1] + nb[3] / 2.0 - box[1] - box[3] / 2.0) * f,
                            )
                        )

            for idx, (t_s, cx, cy) in enumerate(centres):
                # Window boundaries are the midpoints to the neighbouring
                # centres, WITHOUT the extra ±sub_step_s/2 extension: extended
                # windows overlap by a full sub-step, so a fast watermark gets
                # two stale inflated boxes OR'd on the same frame. Midpoint
                # windows tile exactly — every frame belongs to one box.
                seg_start = (
                    max(0.0, t_s - sub_step_s / 2.0)
                    if idx == 0
                    else (centres[idx - 1][0] + t_s) / 2.0
                )
                seg_end = (
                    min(max_dur, t_s + sub_step_s / 2.0)
                    if idx == len(centres) - 1
                    else (t_s + centres[idx + 1][0]) / 2.0
                )
                # A fixed-size box pinned to the centre-time position goes
                # stale across its own time window: at ~890 px/s a ±0.16s
                # window sweeps ±145 px. Grow the box by the text's local
                # travel during exactly this window so it always covers the
                # watermark it claims to cover. The second difference of the
                # positions (local curvature, ~a·Δt²) is added on top: a
                # chord-centred box under-covers a curved path by that amount.
                win_w = max(0.0, seg_end - seg_start)
                if 0 < idx < len(centres) - 1 and centres[idx + 1][0] > centres[idx - 1][0]:
                    vx = (centres[idx + 1][1] - centres[idx - 1][1]) / (
                        centres[idx + 1][0] - centres[idx - 1][0]
                    )
                    vy = (centres[idx + 1][2] - centres[idx - 1][2]) / (
                        centres[idx + 1][0] - centres[idx - 1][0]
                    )
                    curv_x = abs(
                        centres[idx + 1][1] - 2.0 * cx + centres[idx - 1][1]
                    )
                    curv_y = abs(
                        centres[idx + 1][2] - 2.0 * cy + centres[idx - 1][2]
                    )
                else:
                    vx = vy = 0.0
                    curv_x = curv_y = 0.0
                cw = mw + abs(vx) * win_w + 0.5 * curv_x
                chh = mh + abs(vy) * win_w + 0.5 * curv_y
                ux = int(round(cx - cw / 2.0))
                uy = int(round(cy - chh / 2.0))
                # Extrapolated centres can fall outside the frame (phantom
                # boxes hanging off the edge); clamp so the SAM prompt never
                # extends past the picture.
                if width > cw > 0:
                    ux = max(0, min(ux, width - cw))
                if height > chh > 0:
                    uy = max(0, min(uy, height - chh))
                all_detections.append(
                    Detection(
                        box=(ux, uy, int(cw), int(chh)),
                        area=int(cw * chh),
                        score=0.95,
                        kind="tracked",
                        start_s=round(max(0.0, seg_start), 2),
                        end_s=round(min(seg_end, max_dur), 2),
                        is_moving=True,
                    )
                )

            hold_dur = max_dur - ch_hits[-1][0]
            if 0.15 < hold_dur <= 1.0:
                # Hold-grow covers the tail between the last sighting and the video end;
                # beyond 1.4x the prompt inflates and mask precision collapses.
                grow = 1.4
                lb = ch_hits[-1][1]
                hw, hh = int(lb[2] * grow), int(lb[3] * grow)
                hx = int(lb[0] + lb[2] / 2.0 - hw / 2.0)
                hy = int(lb[1] + lb[3] / 2.0 - hh / 2.0)
                all_detections.append(
                    Detection(
                        box=(max(0, hx), max(0, hy), hw, hh),
                        area=hw * hh,
                        score=0.9,
                        kind="tracked",
                        start_s=round(ch_hits[-1][0], 2),
                        end_s=round(max_dur, 2),
                        is_moving=True,
                    )
                )
            head_hold = ch_hits[0][0]
            if 0.15 < head_hold <= 1.0:
                grow = 1.4
                fb = ch_hits[0][1]
                hw, hh = int(fb[2] * grow), int(fb[3] * grow)
                hx = int(fb[0] + fb[2] / 2.0 - hw / 2.0)
                hy = int(fb[1] + fb[3] / 2.0 - hh / 2.0)
                all_detections.append(
                    Detection(
                        box=(max(0, hx), max(0, hy), hw, hh),
                        area=hw * hh,
                        score=0.9,
                        kind="tracked",
                        start_s=0.0,
                        end_s=round(head_hold, 2),
                        is_moving=True,
                    )
                )

    return all_detections


def frame_prompts_at(
    detections: list[Detection],
    frame_idx: int,
    fps: float,
    total_frames: int,
    width: int = 0,
    height: int = 0,
    max_prompts: int = 3,
) -> list[list[int]]:
    """Select the SAM2 prompt boxes for one frame from timed detections.

    Attribution filtering, not plain NMS: interpolated ``tracked`` boxes are
    only used on frames with no direct evidence. When a tight static
    detection covers the same frame, the interpolated box is dropped because
    the OR of a tight box and its inflated interpolations is exactly the
    over-segmentation that destroyed background in the P0 incident. Boxes are
    clamped to the real frame bounds and capped at ``max_prompts`` by score.

    Coverage is HALF-OPEN (``start_s <= t < end_s``): on a corner hop the
    boundary time belongs to the new position only, so the previous corner's
    box can never join the OR.
    """
    if fps <= 0:
        return []
    t = frame_idx / fps
    max_dur = total_frames / fps
    covering = [
        d
        for d in detections
        if (d.start_s or 0.0) <= t < (d.end_s if d.end_s is not None else max_dur)
    ]
    if not covering:
        return []

    # Frame bounds must come from the real picture, not from the detections'
    # own extent — an out-of-bounds detection would otherwise become its own
    # clamp ceiling and survive truncation.
    frame_w = int(width) if width and width > 0 else max(
        (d.box[0] + d.box[2] for d in detections), default=0
    )
    frame_h = int(height) if height and height > 0 else max(
        (d.box[1] + d.box[3] for d in detections), default=0
    )

    def _clamp(det: Detection) -> tuple[int, int, int, int]:
        x, y, w, h = det.box
        x = max(0, int(x))
        y = max(0, int(y))
        w = max(1, int(w))
        h = max(1, int(h))
        if frame_w > 0 and x + w > frame_w:
            w = frame_w - x
        if frame_h > 0 and y + h > frame_h:
            h = frame_h - y
        return (x, y, max(1, w), max(1, h))

    def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        x1, y1 = max(ax, bx), max(ay, by)
        x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        union = aw * ah + bw * bh - inter
        return inter / union if union else 0.0

    def _is_tracked(d: Detection) -> bool:
        return d.kind == "tracked" or d.is_moving is True

    prompts: list[tuple[float, tuple[int, int, int, int]]] = []
    # Direct-evidence (tight static) boxes always win.
    for d in covering:
        if not _is_tracked(d):
            prompts.append((d.score, _clamp(d)))
    # Interpolated boxes only where no direct box overlaps them: that frame
    # has no sighting of its own and the interpolation is the only evidence.
    for d in covering:
        if _is_tracked(d):
            bx = _clamp(d)
            if any(_iou(bx, sb) > 0.3 for _, sb in prompts):
                continue
            prompts.append((d.score, bx))

    # NMS among the survivors.
    prompts.sort(key=lambda p: p[0], reverse=True)
    kept: list[tuple[float, tuple[int, int, int, int]]] = []
    for sc, bx in prompts:
        if any(_iou(bx, kb) > 0.6 for _, kb in kept):
            continue
        kept.append((sc, bx))
        if len(kept) >= max_prompts:
            break

    return [[x, y, x + w, y + h] for _, (x, y, w, h) in kept]


def verify_watermark_removal(
    source_path: Path | str,
    repaired_path: Path | str,
    detections: list[Detection],
    fps: float,
    max_frames: int | None = None,
    samples_per_det: int = 1,
    match_threshold: float = 0.6,
) -> dict:
    """Post-repair residual check: template-match each watermark patch.

    For every detection segment, the watermark patch cropped from the SOURCE
    frame is matched against the same neighbourhood of the REPAIRED frame. A
    strong correlation means the overlay is still there - the repair either
    missed the spot or the trajectory drifted. Returns a per-frame coverage
    report so the caller can fail the task instead of shipping a fake success.
    """
    cap_s = cv2.VideoCapture(str(source_path))
    cap_r = cv2.VideoCapture(str(repaired_path))
    if not cap_s.isOpened() or not cap_r.isOpened():
        cap_s.release()
        cap_r.release()
        return {"ok": False, "reason": "cannot open videos", "total": 0, "residual": []}

    width = int(cap_s.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap_s.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap_s.get(cv2.CAP_PROP_FRAME_COUNT))
    if max_frames is not None:
        total_frames = min(total_frames, max_frames)

    residual: list[dict] = []
    total_checked = 0

    for det in detections:
        dur = (det.end_s if det.end_s is not None else total_frames / fps) - det.start_s
        if dur <= 0:
            continue
        if det.is_moving:
            sample_times = [det.start_s + dur * 0.5]
        else:
            n_samples = max(1, min(3, int(dur / 0.5) or samples_per_det))
            sample_times = [det.start_s + dur * (k + 0.5) / n_samples for k in range(n_samples)]

        for t_s in sample_times:
            f_idx = int(t_s * fps)
            if f_idx >= total_frames:
                continue
            cap_s.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
            cap_r.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
            ret_s, src_frame = cap_s.read()
            ret_r, rep_frame = cap_r.read()
            if not ret_s or not ret_r:
                continue

            bx, by, bw, bh = det.box
            # Skip if box is substantially off-screen
            on_screen_w = max(0, min(width, bx + bw) - max(0, bx))
            on_screen_h = max(0, min(height, by + bh) - max(0, by))
            if on_screen_w * on_screen_h < 0.65 * (bw * bh):
                continue

            # Inner patch: the watermark strokes, without the inflated border.
            ix1 = max(0, bx + bw // 6)
            iy1 = max(0, by + bh // 6)
            ix2 = min(width, bx + bw - bw // 6)
            iy2 = min(height, by + bh - bh // 6)
            if ix2 - ix1 < 8 or iy2 - iy1 < 8:
                continue
            patch = cv2.cvtColor(src_frame[iy1:iy2, ix1:ix2], cv2.COLOR_BGR2GRAY)
            if float(patch.std()) < 8.0:
                continue

            # Search neighbourhood: 40% margin around the box absorbs small
            # trajectory jitter without scanning the whole frame.
            mx1 = max(0, bx - bw * 2 // 5)
            my1 = max(0, by - bh * 2 // 5)
            mx2 = min(width, bx + bw + bw * 2 // 5)
            my2 = min(height, by + bh + bh * 2 // 5)
            region = cv2.cvtColor(rep_frame[my1:my2, mx1:mx2], cv2.COLOR_BGR2GRAY)
            if region.shape[0] < patch.shape[0] or region.shape[1] < patch.shape[1]:
                continue

            rep_exact = cv2.cvtColor(rep_frame[iy1:iy2, ix1:ix2], cv2.COLOR_BGR2GRAY)
            diff = cv2.absdiff(patch, rep_exact)
            has_inpaint = float(diff.mean()) > 4.0 and int(diff.max()) > 30

            res = cv2.matchTemplate(region, patch, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            total_checked += 1
            # A residual occurs when the source watermark is still present in repaired frame:
            # either region was not modified (has_inpaint is False, watermark missed completely),
            # or template correlation is extremely high without proper replacement.
            is_unpainted = not has_inpaint and float(max_val) >= match_threshold
            is_insufficient = float(max_val) >= 0.92 and float(diff.mean()) < 5.0
            if is_unpainted or is_insufficient:
                residual.append({
                    "frame_index": f_idx,
                    "time_s": round(t_s, 2),
                    "box": [bx, by, bw, bh],
                    "match_score": round(float(max_val), 3),
                })

    cap_s.release()
    cap_r.release()
    residual_ratio = (len(residual) / total_checked) if total_checked else 0.0
    return {
        "ok": total_checked > 0 and len(residual) == 0,
        "total": total_checked,
        "residual_count": len(residual),
        "residual_ratio": round(residual_ratio, 3),
        "residual": residual[:20],
    }


def detect_timed_watermarks_hybrid(video_path: Path | str) -> DetectionResult:
    """Hybrid timed watermark detector: outro detection + OCR + temporal energy.

    1. Scans for an appended platform outro and determines content_end_frame.
    2. Runs OCR across sample frames to locate text and adjacent logo boxes,
       strictly excluding the central subject safety zone [0.25W..0.75W, 0.20H..0.80H].
    3. Refines each box's exact [start_s, end_s] using edge energy variance over time.
    4. Falls back to sliding-window temporal median if OCR is unavailable.
    """
    video_path = Path(video_path)
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    if total <= 0 or width <= 0 or height <= 0:
        return DetectionResult(False, "无法读取视频信息", [], None, None, 0, {})

    outro_cut_frame, outro_cut_s = detect_outro_cut(video_path)
    content_end_frame = outro_cut_frame if outro_cut_frame is not None else total
    content_dur = content_end_frame / fps

    vlm_detections: list[Detection] = []
    if is_vlm_available():
        try:
            vlm_res = analyze_video_watermarks_vlm(video_path)
            if vlm_res.ok and vlm_res.tracks:
                vlm_boxes = convert_vlm_tracks_to_pixel_boxes(vlm_res.tracks, width, height)
                for vb in vlm_boxes:
                    bx, by, bw, bh = vb["box"]
                    vlm_detections.append(
                        Detection(
                            box=(bx, by, bw, bh),
                            area=bw * bh,
                            score=float(vb.get("confidence", 0.95)),
                            kind=vb.get("kind", "logo"),
                            start_s=float(vb.get("start_s", 0.0)),
                            end_s=float(vb["end_s"]) if vb.get("end_s") is not None else None,
                        )
                    )
                if vlm_res.outro_start_s is not None:
                    vlm_cut_f = int(vlm_res.outro_start_s * fps)
                    if outro_cut_frame is None or vlm_cut_f < outro_cut_frame:
                        outro_cut_frame = vlm_cut_f
                        outro_cut_s = vlm_res.outro_start_s
                        content_end_frame = outro_cut_frame
                        content_dur = content_end_frame / fps
        except Exception as exc:
            logger.warning("VLM analysis error: %s", exc)

    api_dir = Path(__file__).resolve().parent.parent.parent
    ai_worker_python = api_dir.parent / "ai-worker" / ".venv" / "Scripts" / "python.exe"
    ocr_cli = api_dir.parent / "ai-worker" / "ocr_detector_cli.py"

    ocr_boxes: list[dict] = []
    ocr_unconfirmed_global: list[dict] = []
    if ai_worker_python.exists() and ocr_cli.exists() and not vlm_detections:
        try:
            cmd = [
                str(ai_worker_python),
                str(ocr_cli),
                "--video",
                str(video_path),
                "--end-frame",
                str(content_end_frame),
                "--sample-sec",
                "1.5",
            ]
            proc = subprocess.run(cmd, capture_output=True, timeout=600, check=False)
            stdout = proc.stdout.decode("utf-8", errors="replace")
            for line in stdout.splitlines():
                if line.startswith("OUTPUT_JSON:"):
                    ocr_boxes = json.loads(line.removeprefix("OUTPUT_JSON:"))
                    break
            # The CLI never silently drops a candidate: single-sighting text
            # without a platform prior arrives as UNCONFIRMED_JSON on stderr.
            # Collect it and persist it on the result so the API layer can
            # route it to user confirmation (goal task 12) instead of having
            # the evidence die in a log line.
            unconfirmed_candidates: list[dict] = []
            stderr = proc.stderr.decode("utf-8", errors="replace")
            for line in stderr.splitlines():
                if not line.startswith("UNCONFIRMED_JSON:"):
                    continue
                try:
                    batch = json.loads(line.removeprefix("UNCONFIRMED_JSON:"))
                except Exception:
                    continue
                if isinstance(batch, list):
                    unconfirmed_candidates.extend(batch)
            if unconfirmed_candidates:
                logger.warning(
                    "OCR 检出 %d 个低置信候选（单次出现，需用户确认）",
                    len(unconfirmed_candidates),
                )
                ocr_unconfirmed_global.extend(unconfirmed_candidates)
        except Exception as exc:
            logger.warning("OCR detector CLI failed or timed out: %s", exc)
            ocr_boxes = []

    detections: list[Detection] = []
    static_mask = np.zeros((height, width), dtype=np.uint8)

    if vlm_detections:
        detections = list(vlm_detections)
        for det in detections:
            bx, by, bw, bh = det.box
            cv2.rectangle(static_mask, (bx, by), (bx + bw, by + bh), 255, -1)
    elif ocr_boxes:
        cap = cv2.VideoCapture(str(video_path))
        sample_step = max(1, int(fps * 0.04))
        timestamps = []
        patch_vars: dict[int, list[float]] = {i: [] for i in range(len(ocr_boxes))}

        for f_idx in range(0, content_end_frame, sample_step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
            ret, frame = cap.read()
            if not ret:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            timestamps.append(f_idx / fps)
            for i, b in enumerate(ocr_boxes):
                bx, by, bw, bh = b["box"]
                patch = gray[by : by + bh, bx : bx + bw]
                gx = cv2.Sobel(patch, cv2.CV_32F, 1, 0, ksize=3)
                gy = cv2.Sobel(patch, cv2.CV_32F, 0, 1, ksize=3)
                grad_mag = np.sqrt(gx * gx + gy * gy)
                energy = float(grad_mag.var() * 0.5 + np.percentile(grad_mag, 90) ** 2 * 0.5)
                patch_vars[i].append(energy)
        cap.release()

        dense_cache: list[dict] | None = None
        consumed_texts: set[str] = set()
        for i, b in enumerate(ocr_boxes):
            vars_arr = np.array(patch_vars[i])
            if len(vars_arr) == 0:
                continue
            intervals = _split_active_intervals(timestamps, vars_arr, content_dur)
            b_times = b.get("times", [])
            if b_times:
                # Discard intervals far from any OCR sighting of this cluster (scene motion)
                confirmed_intervals = [
                    (s, e)
                    for s, e in intervals
                    if any(s - 1.5 <= t <= e + 1.5 for t in b_times)
                ]
                if confirmed_intervals:
                    intervals = confirmed_intervals
                else:
                    intervals = [(b["start_s"], b["end_s"])]
            bx, by, bw, bh = (int(v) for v in b["box"])
            target_texts = {str(t).lower() for t in b.get("texts", [])}
            # A text already rebuilt from the dense trajectory must not also
            # emit a static box for the same overlay.
            if target_texts & consumed_texts:
                continue

            # Run dense OCR trajectory re-scan when a watermark is moving or appears
            # at multiple distant locations.
            # Static corner watermarks already have their exact multi-line envelopes.
            text_to_boxes: dict[str, list[list[int]]] = {}
            for bx_item in ocr_boxes:
                for t_item in bx_item.get("texts", []):
                    text_to_boxes.setdefault(t_item.lower(), []).append(bx_item["box"])
            multi_loc = any(
                len(b_list) >= 2
                and any(
                    math.hypot(b1[0] - b2[0], b1[1] - b2[1]) > 120
                    for b1 in b_list
                    for b2 in b_list
                )
                for b_list in text_to_boxes.values()
            )
            any_moving = any(bx.get("is_moving") for bx in ocr_boxes) or multi_loc
            if any_moving and dense_cache is None:
                dense_cache = _dense_ocr_sightings(
                    video_path, fps, content_end_frame, interval_s=0.35
                )
            moving_dets = (
                _sightings_to_moving_detections(
                    dense_cache, target_texts, fps, content_end_frame,
                    width=width, height=height,
                )
                if dense_cache
                else []
            )

            if moving_dets:
                detections.extend(moving_dets)
                consumed_texts |= target_texts
                for det in moving_dets:
                    mx, my, mw, mh = det.box
                    cv2.rectangle(static_mask, (mx, my), (mx + mw, my + mh), 255, -1)
                continue

            for start_s, end_s in intervals:
                # The tight OCR box is what SAM2 should be prompted with; the
                # padded box (avatar expansion, edge snapping) stays available
                # as search context. Measured: tight prompts raise mask
                # precision 0.52→0.63 (grass), 0.47→0.56 (sky).
                tb = b.get("tight_box") or (bx, by, bw, bh)
                det = Detection(
                    box=(int(tb[0]), int(tb[1]), int(tb[2]), int(tb[3])),
                    area=int(tb[2]) * int(tb[3]),
                    score=float(b.get("confidence", 0.95)),
                    kind="text",
                    start_s=start_s,
                    end_s=end_s,
                    is_moving=False,
                )
                detections.append(det)
            if intervals:
                cv2.rectangle(static_mask, (bx, by), (bx + bw, by + bh), 255, -1)

    if detections:
        frame_tracks = track_watermarks_dense(
            video_path, detections, max_frames=content_end_frame
        )
        stats = {
            "method": "hybrid_vlm" if vlm_detections else "hybrid_ocr",
            "outro_cut_frame": outro_cut_frame,
            "content_frames": content_end_frame,
            "detections_count": len(detections),
            "unconfirmed_candidates": ocr_unconfirmed_global[:20],
            "unconfirmed_count": len(ocr_unconfirmed_global),
        }
        return DetectionResult(
            ok=True,
            reason=f"通过混合分析定位到 {len(detections)} 处分时水印",
            detections=detections,
            static_mask=static_mask,
            per_frame_masks=None,
            frame_count=content_end_frame,
            stats=stats,
            outro_cut_frame=outro_cut_frame,
            outro_cut_s=outro_cut_s,
            frame_tracks=frame_tracks,
        )

    # Fallback: read frames up to content_end_frame and run standard detector
    cap = cv2.VideoCapture(str(video_path))
    fallback_frames = []
    read_step = max(1, content_end_frame // 40) if content_end_frame > 60 else 1
    for f_idx in range(0, content_end_frame, read_step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
        ret, frame = cap.read()
        if not ret:
            break
        fallback_frames.append(frame)
    cap.release()

    res = detect_watermarks(fallback_frames)
    res.outro_cut_frame = outro_cut_frame
    res.outro_cut_s = outro_cut_s
    if res.detections:
        res.frame_tracks = track_watermarks_dense(
            video_path, res.detections, max_frames=content_end_frame
        )
    return res
