"""Unit tests for the STTN chunk planner and overlap blending.

These cover the pure array bookkeeping (window coverage, blend weights,
mask-exact blending) that runs before/around the GPU forward pass. Model
quality is validated separately by the GPU benchmark script.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "sttn-worker"))

from chunk_plan import blend_zone, plan_windows  # noqa: E402


def test_plan_windows_covers_every_frame():
    windows = plan_windows(100, chunk=40, overlap=8)
    covered = set()
    for start, end, _blend in windows:
        assert end > start
        covered.update(range(start, end))
    assert covered == set(range(100))


def test_plan_windows_no_gaps():
    for total, chunk, overlap in [(13, 4, 2), (40, 40, 8), (41, 40, 8), (120, 60, 10), (7, 40, 8)]:
        windows = plan_windows(total, chunk, overlap)
        prev_end = 0
        for start, end, _blend in windows:
            # Each window after the first must begin inside the previous
            # window's coverage (no gap) and within it (no unbounded jump).
            assert start <= prev_end, f"gap at {start} (prev_end={prev_end})"
            prev_end = end
        assert prev_end == total


def test_plan_windows_blend_count_bounds():
    windows = plan_windows(100, chunk=40, overlap=8)
    assert windows[0][2] == 0, "first window has nothing to blend"
    for _start, _end, blend in windows[1:]:
        assert 0 < blend <= 8


def test_plan_windows_clamps_impossible_overlap():
    # overlap == chunk would rewind the cursor and loop forever; the planner
    # clamps overlap to chunk//2, so the plan still terminates and covers.
    windows = plan_windows(20, chunk=4, overlap=4)
    covered = set()
    for start, end, blend in windows:
        covered.update(range(start, end))
        assert blend <= 2
    assert covered == set(range(20))


def test_blend_zone_keeps_outside_mask_untouched():
    prev = np.zeros((10, 10, 3), dtype=np.uint8)
    prev[:] = 100
    new = np.zeros((10, 10, 3), dtype=np.uint8)
    new[:] = 200
    mask = np.zeros((10, 10), dtype=np.uint8)
    mask[2:5, 2:5] = 255
    out = blend_zone(prev, new, mask, alpha=0.5)
    # Outside the mask: bit-identical to the previous output (original bytes).
    assert (out[mask == 0] == 100).all()
    # Inside the mask: blended between the two.
    assert np.allclose(out[mask > 0], 150, atol=1)


def test_blend_zone_alpha_endpoints():
    prev = np.full((4, 4, 3), 50, dtype=np.uint8)
    new = np.full((4, 4, 3), 250, dtype=np.uint8)
    mask = np.full((4, 4), 255, dtype=np.uint8)
    assert (blend_zone(prev, new, mask, 0.0) == 50).all()
    assert (blend_zone(prev, new, mask, 1.0) == 250).all()
