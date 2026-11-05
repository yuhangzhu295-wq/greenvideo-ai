"""Temporal consistency gate (goal task 11).

Per-frame quality gates cannot see flicker, ghosting or texture swimming:
a sequence can pass every per-frame check and still strobe across time.
This gate compares the *temporal* behaviour of a repaired sequence against
its ground truth inside the watermark region, where all temporal artifacts
of a repair engine live (outside the mask the output is composited and
bit-identical by construction).

Metrics
-------
- ``flicker_ratio``: mean |Δ_t| of repaired pixels inside the mask divided
  by the same statistic of the clean sequence. A repair engine without
  temporal memory (per-frame inpainting) shows up as ratio >> 1.
- ``inside_temporal_psnr_min``: worst per-frame PSNR between repaired and
  clean inside the mask across the sequence. A single ghosted frame fails
  the gate even when the mean looks fine.
- ``color_shift_max``: largest per-frame mean-color deviation inside the
  mask between repaired and clean (BGR), catching frame-to-frame color
  pumping that per-pixel PSNR averages away.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TemporalReport:
    flicker_ratio: float
    flicker_abs: float
    inside_temporal_psnr_min: float
    inside_temporal_psnr_mean: float
    color_shift_max: float
    worst_frame: int
    frame_psnrs: list[float] = field(default_factory=list)


def _mask_psnr(a, b, m) -> float:
    import numpy as np

    diff = (a.astype(np.float64) - b.astype(np.float64)) ** 2
    mse = float(diff[m].mean()) if m.any() else 0.0
    if mse <= 1e-9:
        return 99.0
    return 10.0 * float(np.log10(255.0**2 / mse))


def evaluate_temporal_consistency(
    repaired: list,
    clean: list,
    masks: list,
    flicker_ratio_max: float = 3.0,
    min_frame_psnr: float = 12.0,
    color_shift_max: float = 12.0,
) -> TemporalReport:
    """Compare a repaired sequence against clean ground truth over time.

    All inputs are same-length lists of frames (H, W, 3 uint8) and masks
    (H, W uint8, >0 on watermark pixels). Returns the temporal metrics and
    the pass/fail thresholds applied.
    """
    import numpy as np

    if not (len(repaired) == len(clean) == len(masks)) or not repaired:
        raise ValueError("repaired/clean/masks must be same non-empty length")

    rep = np.stack(repaired).astype(np.float32)
    cln = np.stack(clean).astype(np.float32)
    mk = np.stack([m > 0 for m in masks])
    if not mk.any():
        raise ValueError("cannot evaluate temporal consistency on all-empty mask sequence")

    # Flicker: temporal deltas inside the mask, repaired vs clean.
    rep_delta = np.abs(np.diff(rep, axis=0)).mean(axis=3)
    cln_delta = np.abs(np.diff(cln, axis=0)).mean(axis=3)
    both_mask = mk[:-1] & mk[1:]
    rep_fl = float(rep_delta[both_mask].mean()) if both_mask.any() else 0.0
    cln_fl = float(cln_delta[both_mask].mean()) if both_mask.any() else 0.0
    # On a static background the clean delta is ~0 and the ratio explodes on
    # invisible levels of wobble; report BOTH the ratio and the absolute
    # per-frame grey-level flicker, and pass when either shows calm motion.
    flicker_ratio = rep_fl / max(cln_fl, 1e-3)

    frame_psnrs = [
        _mask_psnr(repaired[i], clean[i], mk[i]) for i in range(len(repaired))
    ]
    worst = int(np.argmin(frame_psnrs))

    # Frame-to-frame color pumping inside the mask: per-frame mean color
    # deviation between repaired and clean.
    shifts = [
        float(
            np.abs(
                repaired[i][mk[i]].astype(np.float32).mean(axis=0)
                - clean[i][mk[i]].astype(np.float32).mean(axis=0)
            ).mean()
        )
        if mk[i].any()
        else 0.0
        for i in range(len(repaired))
    ]

    return TemporalReport(
        flicker_ratio=round(flicker_ratio, 3),
        flicker_abs=round(rep_fl, 3),
        inside_temporal_psnr_min=round(min(frame_psnrs), 2),
        inside_temporal_psnr_mean=round(float(np.mean(frame_psnrs)), 2),
        color_shift_max=round(max(shifts), 2),
        worst_frame=worst,
        frame_psnrs=[round(p, 2) for p in frame_psnrs],
    )


def temporal_gate_pass(
    report: TemporalReport,
    flicker_ratio_max: float = 3.0,
    flicker_abs_max: float = 5.0,
    min_frame_psnr: float = 12.0,
    color_shift_max: float = 12.0,
) -> bool:
    calm_flicker = (
        report.flicker_ratio <= flicker_ratio_max or report.flicker_abs <= flicker_abs_max
    )
    return (
        calm_flicker
        and report.inside_temporal_psnr_min >= min_frame_psnr
        and report.color_shift_max <= color_shift_max
    )
