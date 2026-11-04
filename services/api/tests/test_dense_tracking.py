"""Regression tests for the dense-OCR floating-watermark trajectory pipeline.

Covers the defects found in the code-review audit: the negative-dt velocity
explosion, endpoint extrapolation guards, fuzzy text matching, and a synthetic
end-to-end coverage check against known ground truth.
"""
import math

import cv2
import numpy as np

from app.services.watermark import FrameTrajectoryMaskProvider
from app.services.watermark_detect import (
    _fuzzy_text_hit,
    _sightings_to_moving_detections,
)


def test_fuzzy_text_hit_exact_and_substring():
    assert _fuzzy_text_hit("bilibili", {"bilibili"})
    assert _fuzzy_text_hit("bilibili", {"bilibili"}) is True
    assert _fuzzy_text_hit("@tiktok_user", {"tiktok_user"})


def test_fuzzy_text_hit_misreads():
    # OCR misreads on semi-transparent overlays: <=2 edit distance passes.
    assert _fuzzy_text_hit("bilibli", {"bilibili"})
    assert _fuzzy_text_hit("bilibil", {"bilibili"})
    # Multi-target search: failing on the first target must not abort checking subsequent targets.
    assert _fuzzy_text_hit("bilibli", {"unrelated_target", "bilibili"}) is True
    assert _fuzzy_text_hit("bilibli", {"bilibili", "unrelated_target"}) is True
    # Unrelated scene text must not match.
    assert not _fuzzy_text_hit("hello world", {"bilibili"})
    assert not _fuzzy_text_hit("", {"bilibili"})


def test_sightings_negative_dt_guard():
    """A negative dt between sightings must not explode velocity to absurdity.

    Regression: dt = max(1e-3, t1 - t0) clamped -0.5s to 1e-3, producing
    288000 px/s and boxes like (-2764520, 1651424).
    """
    sightings = [
        {"time_s": 10.5, "box": [288, 228, 140, 75], "text": "bilibili"},
        {"time_s": 11.0, "box": [0, 400, 226, 89], "text": "bilibili"},
        {"time_s": 11.5, "box": [0, 760, 225, 85], "text": "bilibili"},
    ]
    dets = _sightings_to_moving_detections(sightings, {"bilibili"}, 60.0, 720)
    assert dets, "trajectory should be built"
    for d in dets:
        x, y, w, h = d.box
        assert -400 <= x <= 800, f"box x out of frame bounds: {d.box}"
        assert -200 <= y <= 1400, f"box y out of frame bounds: {d.box}"


def test_sightings_extrapolation_covers_head_and_tail():
    sightings = [
        {"time_s": 1.0, "box": [100, 200, 140, 60], "text": "wm"},
        {"time_s": 1.5, "box": [140, 240, 140, 60], "text": "wm"},
        {"time_s": 2.0, "box": [180, 280, 140, 60], "text": "wm"},
    ]
    # 60 fps, 3 s video (180 frames): extrapolation reaches ~0.3s beyond the
    # last sighting and an inflated hold bridges the remaining tail.
    dets = _sightings_to_moving_detections(sightings, {"wm"}, 60.0, 180)
    assert dets
    last_end = max(d.end_s for d in dets)
    assert last_end >= 2.85, f"tail not covered to video end: {last_end}"
    first_start = min(d.start_s for d in dets)
    assert first_start <= 0.75, f"head not covered backwards: {first_start}"


def test_sightings_extrapolation_skipped_on_long_gap():
    """A velocity estimated across a >1s gap must not drive extrapolation.

    Both real sightings stay as their own segments; the head/tail残段 are
    covered by holds anchored at the endpoint sighting centres (inflated, but
    never shifted along the unreliable 2s-gap velocity vector).
    """
    sightings = [
        {"time_s": 0.5, "box": [100, 200, 140, 60], "text": "wm"},
        {"time_s": 2.5, "box": [400, 500, 140, 60], "text": "wm"},
    ]
    dets = _sightings_to_moving_detections(sightings, {"wm"}, 60.0, 180)
    assert len(dets) >= 2
    first_start = min(d.start_s for d in dets)
    last_end = max(d.end_s for d in dets)
    assert first_start == 0.0, "head hold should start at video start"
    assert last_end == 3.0, "tail hold should end at video end"

    # The hold segment anchored at the tail sighting must share its centre
    # (no velocity-directed drift), only differ by inflated size.
    tail_holds = [d for d in dets if d.start_s == 2.5]
    assert tail_holds, "missing tail hold"
    th = tail_holds[0]
    assert abs((th.box[0] + th.box[2] / 2.0) - (400 + 70)) <= 2
    assert abs((th.box[1] + th.box[3] / 2.0) - (500 + 30)) <= 2


def test_static_sightings_return_empty():
    """Sightings that never move belong to the static path, not trajectories."""
    sightings = [
        {"time_s": 1.0, "box": [100, 200, 140, 60], "text": "wm"},
        {"time_s": 2.0, "box": [102, 201, 141, 61], "text": "wm"},
        {"time_s": 3.0, "box": [101, 202, 140, 60], "text": "wm"},
    ]
    dets = _sightings_to_moving_detections(sightings, {"wm"}, 60.0, 180)
    assert dets == []


def _ground_truth_text_pos(t_s: float, w: int, h: int) -> tuple[int, int]:
    """Same sinusoid the synthetic bilibili-style fixture was built with."""
    cx = w / 2 + (w * 0.32) * math.sin(t_s / 1.5 * math.pi)
    cy = h / 2 + (h * 0.28) * math.cos(t_s / 1.5 * math.pi)
    return int(cx), int(cy)


def test_end_to_end_trajectory_coverage_against_ground_truth(tmp_path):
    """Synthetic video with a known floating watermark: the detection mask must
    cover >=95% of per-frame ground-truth watermark areas.

    Uses the real production frame scale (720x1280, watermark glyph height
    ~40px): at thumbnail scales OCR simply fails and the test would measure
    OCR recall, not trajectory coverage.
    """
    w, h = 720, 1280
    fps = 30.0
    dur_s = 8.0
    glyph_w, glyph_h = 200, 40
    src_full = tmp_path / "full.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(str(src_full), fourcc, fps, (w, h))
    n = int(fps * dur_s)
    for i in range(n):
        frame = np.full((h, w, 3), 60, dtype=np.uint8)
        t = i / fps
        cx, cy = _ground_truth_text_pos(t, w, h)
        overlay = frame.copy()
        cv2.putText(
            overlay,
            "bilibili",
            (cx - glyph_w // 2, cy + glyph_h // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.4,
            (255, 255, 255),
            3,
            cv2.LINE_AA,
        )
        frame = cv2.addWeighted(overlay, 0.55, frame, 0.45, 0)
        out.write(frame)
    out.release()

    from app.services.watermark_detect import detect_timed_watermarks_hybrid

    det = detect_timed_watermarks_hybrid(src_full)
    assert det.ok, f"detection failed: {det.reason}"
    assert det.frame_tracks, "no frame tracks produced"


    provider = FrameTrajectoryMaskProvider(det.frame_tracks, h, w)
    covered = 0
    total = 0
    misses = []
    for i in range(n):
        t = i / fps
        cx, cy = _ground_truth_text_pos(t, w, h)
        gx = cx - glyph_w // 2
        gy = cy - glyph_h // 2
        mask = provider.mask_for(i, None)
        region = mask[gy: gy + glyph_h, max(0, gx): min(w, gx + glyph_w)]
        gt_area = max(1, region.size)
        inter = float((region > 0).sum())
        total += 1
        if inter / gt_area >= 0.5:
            covered += 1
        else:
            misses.append((i, round(inter / gt_area, 2)))
    ratio = covered / max(1, total)
    assert ratio >= 0.95, (
        f"trajectory covers only {ratio:.1%} of ground-truth frames; worst: {misses[:10]}"
    )
