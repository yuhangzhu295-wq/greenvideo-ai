"""Unit tests for frame_prompts_at attribution filtering (code-reviewer P0-2)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.watermark_detect import Detection, frame_prompts_at  # noqa: E402


def _det(box, start, end, kind="text", moving=False, score=0.9):
    return Detection(
        box=tuple(box), area=box[2] * box[3], score=score, kind=kind,
        start_s=start, end_s=end, is_moving=moving,
    )


def test_half_open_interval_single_box_on_hop_boundary():
    # Corner hop: TL segment [0.0, 0.5), TR segment [0.5, 1.0). At the
    # boundary frame (t=0.5) only the NEW corner may prompt — the old
    # corner's box joining the OR was the measured P 0.648→0.231 regression.
    dets = [
        _det((18, 60, 310, 34), 0.0, 0.5),
        _det((974, 58, 306, 34), 0.5, 1.0),
    ]
    fps, total = 30.0, 30
    assert frame_prompts_at(dets, 14, fps, total, 1280, 720) == [[18, 60, 328, 94]]
    prompts = frame_prompts_at(dets, 15, fps, total, 1280, 720)
    assert prompts == [[974, 58, 1280, 92]], prompts


def test_truncated_seed_frame_still_finds_segment():
    # int(0.83*30)=24 -> t=0.80 < 0.83: the truncated seed must NOT grab the
    # previous segment, and the caller retries f0+1 which lands inside.
    dets = [
        _det((974, 58, 306, 34), 0.5, 0.83),
        _det((14, 626, 318, 42), 0.83, 1.17),
    ]
    fps, total = 30.0, 60
    # f0=24 -> t=0.80: previous segment only (correct, it owns that time).
    p24 = frame_prompts_at(dets, 24, fps, total, 1280, 720)
    assert p24 == [[974, 58, 1280, 92]]
    # f0=25 -> t=0.833: the BL segment (the retry target in tasks.py).
    p25 = frame_prompts_at(dets, 25, fps, total, 1280, 720)
    assert p25 == [[14, 626, 332, 668]]


def test_out_of_bounds_box_clamped_to_real_frame():
    # hold-grow 1.4x can push a box past the right/bottom edge; the clamp
    # must use the REAL frame size, not the detections' own max extent.
    dets = [_det((1100, 650, 300, 90), 0.0, 2.0)]
    prompts = frame_prompts_at(dets, 0, 30.0, 60, 1280, 720)
    x1, y1, x2, y2 = prompts[0]
    assert x2 <= 1280 and y2 <= 720
    assert (x1, y1) == (1100, 650)


def test_interpolated_box_suppressed_near_direct_evidence():
    # A tracked interpolation overlapping a direct static box is dropped;
    # only frames without direct evidence keep the interpolation.
    dets = [
        _det((100, 100, 200, 50), 0.0, 1.0, kind="text"),
        _det((120, 105, 200, 50), 0.0, 1.0, kind="tracked", moving=True),
    ]
    prompts = frame_prompts_at(dets, 10, 30.0, 60, 1280, 720)
    assert prompts == [[100, 100, 300, 150]]


def test_prompts_capped_by_score():
    dets = [
        _det((10, 10, 50, 40), 0.0, 1.0, score=0.95),
        _det((300, 10, 50, 40), 0.0, 1.0, score=0.93),
        _det((600, 10, 50, 40), 0.0, 1.0, score=0.91),
        _det((900, 10, 50, 40), 0.0, 1.0, score=0.90),
    ]
    prompts = frame_prompts_at(dets, 5, 30.0, 60, 1280, 720)
    assert len(prompts) == 3


def test_seed_frames_ceil_computation_covers_all_segments():
    import math

    dets = [
        _det((18, 60, 310, 34), 0.0, 0.5),
        _det((976, 58, 306, 34), 0.5, 0.83),
        _det((14, 626, 318, 42), 0.83, 1.17),
        _det((974, 626, 306, 42), 1.17, 2.0),
    ]
    fps = 30.0
    seeds = sorted({max(0, math.ceil((d.start_s or 0.0) * fps - 1e-6)) for d in dets})
    assert seeds == [0, 15, 25, 36]
    for idx, f in enumerate(seeds):
        p = frame_prompts_at(dets, f, fps, 60, 1280, 720)
        assert len(p) == 1, f"seed frame {f} should have exactly 1 box"
        expected_x1 = [18, 976, 14, 974][idx]
        assert p[0][0] == expected_x1

