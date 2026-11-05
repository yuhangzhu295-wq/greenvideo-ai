"""Unit tests for the temporal consistency gate (goal task 11)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.watermark_detection.temporal_gate import (  # noqa: E402
    evaluate_temporal_consistency,
    temporal_gate_pass,
)


def _sequence(base, jitter, n=10, wm=True):
    frames, masks = [], []
    rng = np.random.RandomState(7)
    for _ in range(n):
        frame = base.copy()
        if jitter:
            frame = np.clip(
                frame.astype(int) + rng.normal(0, jitter, frame.shape), 0, 255
            ).astype(np.uint8)
        mask = np.zeros(frame.shape[:2], dtype=np.uint8)
        if wm:
            mask[20:40, 20:120] = 255
        frames.append(frame)
        masks.append(mask)
    return frames, masks


def test_stable_sequence_passes():
    base = np.full((60, 160, 3), 120, dtype=np.uint8)
    clean, masks = _sequence(base, jitter=0)
    repaired, _ = _sequence(base, jitter=0)
    report = evaluate_temporal_consistency(repaired, clean, masks)
    assert temporal_gate_pass(report)
    assert report.flicker_ratio <= 1.5
    assert report.inside_temporal_psnr_min > 30


def test_flickering_repair_is_detected():
    # Clean: static. Repaired: strong per-frame noise inside the mask only —
    # exactly what a memoryless per-frame inpainter produces.
    base = np.full((60, 160, 3), 120, dtype=np.uint8)
    clean, masks = _sequence(base, jitter=0)
    rng = np.random.RandomState(3)
    repaired = []
    for f in clean:
        f = f.copy()
        f[20:40, 20:120] = np.clip(
            f[20:40, 20:120].astype(int) + rng.normal(0, 40, f[20:40, 20:120].shape),
            0, 255,
        ).astype(np.uint8)
        repaired.append(f)
    report = evaluate_temporal_consistency(repaired, clean, masks)
    assert report.flicker_ratio > 3.0, "flicker must be visible in the ratio"
    assert not temporal_gate_pass(report)


def test_single_ghosted_frame_is_detected():
    # Every frame matches the clean ground truth except one ghosted frame —
    # a per-frame sampling gate at low density would miss it.
    base = np.full((60, 160, 3), 120, dtype=np.uint8)
    clean, masks = _sequence(base, jitter=0)
    repaired = [f.copy() for f in clean]
    ghost = clean[4].copy()
    ghost[20:40, 20:120] = 255
    repaired[4] = ghost
    report = evaluate_temporal_consistency(repaired, clean, masks)
    assert report.worst_frame == 4
    assert report.inside_temporal_psnr_min < 12.0
    assert not temporal_gate_pass(report)
    # The mean stays high: this is why the gate uses the MIN, not the mean.
    assert report.inside_temporal_psnr_mean > 30


def test_frame_to_frame_color_shift_is_detected():
    # Repaired alternates tint inside the mask every frame (color pumping).
    base = np.full((60, 160, 3), 120, dtype=np.uint8)
    clean, masks = _sequence(base, jitter=0)
    repaired = []
    for i, f in enumerate(clean):
        f = f.copy()
        if i % 2 == 1:
            f[20:40, 20:120, 2] = np.clip(
                f[20:40, 20:120, 2].astype(int) + 40, 0, 255
            ).astype(np.uint8)
        repaired.append(f)
    report = evaluate_temporal_consistency(repaired, clean, masks)
    assert report.color_shift_max > 12.0
    assert not temporal_gate_pass(report)


def test_length_mismatch_raises():
    base = np.full((60, 160, 3), 120, dtype=np.uint8)
    clean, masks = _sequence(base, jitter=0, n=5)
    repaired, _ = _sequence(base, jitter=0, n=4)
    try:
        evaluate_temporal_consistency(repaired, clean, masks)
        raised = False
    except ValueError:
        raised = True
    assert raised
