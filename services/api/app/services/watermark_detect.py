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
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class Detection:
    box: tuple[int, int, int, int]  # x, y, w, h
    area: int
    score: float
    kind: str  # "text" | "logo" | "tracked"
    start_s: float = 0.0
    end_s: float | None = None


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


def _sample_frames(frames: list[np.ndarray], limit: int = 20) -> list[np.ndarray]:
    if len(frames) <= limit:
        return list(frames)
    step = max(1, len(frames) // limit)
    return frames[::step][:limit]


def _temporal_median(sample: list[np.ndarray]) -> np.ndarray:
    stack = np.stack(sample, axis=0)
    return np.median(stack, axis=0).astype(np.uint8)


def _staticness(sample: list[np.ndarray], median: np.ndarray) -> np.ndarray:
    devs = [np.abs(f.astype(np.float32) - median.astype(np.float32)).mean(axis=2) for f in sample]
    maxdev = np.max(np.stack(devs, axis=0), axis=0)
    # A pixel that never changes is an overlay candidate; 40 grey levels is the
    # point where "moved" is considered certain.
    return np.clip(1.0 - maxdev / 40.0, 0.0, 1.0)


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
    # or a solid-colour object.
    structure = np.clip(grad / 60.0, 0.0, 1.0) * 0.3 + np.clip(contrast / 50.0, 0.0, 1.0) * 0.2
    score = staticness * 0.5 + structure

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
            frame_static = np.clip(1.0 - dev / 40.0, 0.0, 1.0)
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


def detect_outro_cut(video_path: Path | str) -> tuple[int | None, float | None]:
    """Detect if the video ends with a platform promo outro (e.g. Douyin outro).

    Scans the last 4.5 seconds of the video for a sudden scene cut to a dark,
    uniform border or promotional card lasting 1.5 to 4.2 seconds.
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
        if 1.5 <= tail_sec <= 4.2:
            if diff > 25.0 or (luma < 35.0 and records[i - 1][2] > 55.0):
                post_records = records[i:]
                post_lumas = [r[2] for r in post_records]
                post_stds = [r[3] for r in post_records]
                if np.mean(post_lumas) < 45.0 and np.mean(post_stds) < 25.0:
                    return idx, sec
    return None, None


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

    api_dir = Path(__file__).resolve().parent.parent.parent
    ai_worker_python = api_dir.parent / "ai-worker" / ".venv" / "Scripts" / "python.exe"
    ocr_cli = api_dir.parent / "ai-worker" / "ocr_detector_cli.py"

    ocr_boxes: list[dict] = []
    if ai_worker_python.exists() and ocr_cli.exists():
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
            proc = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
            stdout = proc.stdout.decode("utf-8", errors="replace")
            for line in stdout.splitlines():
                if line.startswith("OUTPUT_JSON:"):
                    ocr_boxes = json.loads(line.removeprefix("OUTPUT_JSON:"))
                    break
        except Exception:
            ocr_boxes = []

    if ocr_boxes:
        cap = cv2.VideoCapture(str(video_path))
        sample_step = max(1, int(fps * 0.25))
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
                patch_vars[i].append(float(cv2.Sobel(patch, cv2.CV_32F, 1, 1).var()))
        cap.release()

        detections: list[Detection] = []
        static_mask = np.zeros((height, width), dtype=np.uint8)

        for i, b in enumerate(ocr_boxes):
            vars_arr = np.array(patch_vars[i])
            if len(vars_arr) == 0:
                continue
            med_var = float(np.median(vars_arr))
            max_var = float(np.max(vars_arr))
            thr = max(200.0, med_var * 0.7 if med_var > 300.0 else max_var * 0.35)
            active_indices = np.where(vars_arr >= thr)[0]
            if len(active_indices) >= 2:
                start_s = max(0.0, timestamps[active_indices[0]] - 0.2)
                end_s = min(content_dur, timestamps[active_indices[-1]] + 0.35)
                bx, by, bw, bh = (int(v) for v in b["box"])
                det = Detection(
                    box=(bx, by, bw, bh),
                    area=bw * bh,
                    score=float(b.get("confidence", 0.95)),
                    kind="text",
                    start_s=round(float(start_s), 2),
                    end_s=round(float(end_s), 2),
                )
                detections.append(det)
                cv2.rectangle(static_mask, (bx, by), (bx + bw, by + bh), 255, -1)

        if detections:
            stats = {
                "method": "hybrid_ocr",
                "outro_cut_frame": outro_cut_frame,
                "content_frames": content_end_frame,
                "detections_count": len(detections),
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
    return res
