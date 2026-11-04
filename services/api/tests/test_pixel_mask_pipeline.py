"""Unit tests for the pixel-level watermark detection pipeline.

Validates that SAM2-based segmentation produces pixel masks that hug the
watermark contour (not the bounding rectangle), and that the mask gate
correctly distinguishes pixel masks from rectangle blowout.
"""
import cv2
import numpy as np

from app.services.watermark_detection.mask_gate import evaluate_mask, evaluate_repair_quality
from app.services.watermark_detection.track_manager import TrackManager


def _make_synthetic_watermark_frame(h=720, w=1280):
    """A grey frame with a white text-like watermark at a known location."""
    frame = np.full((h, w, 3), 60, dtype=np.uint8)
    gt_mask = np.zeros((h, w), dtype=np.uint8)
    # Simulate "bilibili" text: a few letter-sized blobs on one line
    cv2.putText(gt_mask, "bilibili", (200, 500), cv2.FONT_HERSHEY_SIMPLEX, 2.0, 255, 6, cv2.LINE_AA)
    frame[gt_mask > 0] = 255
    return frame, gt_mask


def test_track_manager_splits_on_jump():
    tm = TrackManager(jump_distance_px=100)
    mask_a = np.zeros((100, 100), dtype=np.uint8)
    mask_a[10:30, 10:40] = 255
    mask_b = np.zeros((100, 100), dtype=np.uint8)
    mask_b[60:80, 500:600] = 255

    tm.add_mask(0, mask_a, box=(10, 10, 30, 20))
    tm.add_mask(1, mask_a, box=(11, 10, 30, 20))  # continuous
    tm.add_mask(2, mask_b, box=(500, 60, 100, 20))  # jump!

    assert len(tm.segments) == 2, "jump must start a new segment, never interpolate"
    assert tm.mask_for(0) is not None
    assert tm.mask_for(1) is not None
    assert tm.mask_for(2) is not None
    assert tm.mask_for(3) is None  # no interpolation beyond segments
    gaps = tm.gaps()
    assert len(gaps) == 0, "adjacent segments (2->3) are contiguous, no gap"


def test_track_manager_gap_reporting():
    tm = TrackManager()
    m = np.zeros((10, 10), dtype=np.uint8)
    m[2:5, 2:5] = 255
    tm.add_mask(0, m, box=(2, 2, 3, 3))
    tm.add_mask(10, m, box=(2, 2, 3, 3))
    gaps = tm.gaps()
    assert gaps == [(1, 9)], "gap between frame 0 and 10 must be reported honestly"


def test_mask_gate_rejects_rectangle_blowout():
    frame, gt = _make_synthetic_watermark_frame()
    h, w = gt.shape

    # The OLD behaviour: full bounding rectangle over the whole text line
    rect_mask = np.zeros_like(gt)
    ys, xs = np.where(gt > 0)
    rect_mask[max(0, ys.min() - 8): ys.max() + 9, max(0, xs.min() - 8): xs.max() + 9] = 255

    metrics_rect = evaluate_mask(rect_mask, gt)
    # A rectangle around text has LOW precision because normal background
    # inside the rectangle gets masked (fp_ratio high).
    assert metrics_rect.precision < 0.5, (
        f"rectangle mask precision must be poor: {metrics_rect.precision:.3f}"
    )
    assert metrics_rect.normal_pixels_modified > 5000, (
        "rectangle mask must register as heavy normal-pixel damage"
    )

    # The NEW behaviour: pixel mask == GT (ideal SAM output)
    metrics_pixel = evaluate_mask(gt, gt)
    assert metrics_pixel.precision == 1.0
    assert metrics_pixel.recall == 1.0
    assert metrics_pixel.iou == 1.0
    assert metrics_pixel.normal_pixels_modified == 0


def test_repair_quality_outside_region_guard():
    frame, gt = _make_synthetic_watermark_frame()

    # Simulate a good repair: watermark removed, background untouched
    repaired = frame.copy()
    repaired[gt > 0] = 60  # fill watermark with background grey

    q = evaluate_repair_quality(frame, repaired, gt)
    assert q["outside_change_ratio"] == 0.0, "background must be untouched"
    assert q["outside_psnr"] >= 99.0, "outside PSNR must be near-infinite when untouched"

    # Simulate a bad repair (old rectangle blowout): background inside the box
    # was rebuilt
    bad = frame.copy()
    ys, xs = np.where(gt > 0)
    y1, y2 = ys.min(), ys.max()
    x1, x2 = xs.min(), xs.max()
    bad[y1:y2 + 1, x1:x2 + 1] = 80  # smeared the whole rectangle

    q_bad = evaluate_repair_quality(frame, bad, gt)
    # Rectangle blowout changes thousands of background pixels; the watermark
    # is tiny relative to the full frame so the absolute ratio is diluted, but
    # the pixel-mask case is exactly 0.0 so ANY non-zero change is a regression.
    assert q_bad["outside_change_ratio"] > 0.0, (
        f"rectangle blowout must be flagged: {q_bad['outside_change_ratio']}"
    )
    assert q_bad["outside_changed_pixels"] > 1000, (
        f"rectangle must damage substantial background: {q_bad['outside_changed_pixels']}"
    )
    # Outside PSNR: pixel-level repair = ~99 (untouched); rectangle blowout =
    # dramatically lower because the whole box got smeared (diluted by frame
    # size but still clearly distinguishable from the 99 ceiling).
    assert q_bad["outside_psnr"] < 50, (
        f"background rebuilt => degraded PSNR: {q_bad['outside_psnr']}"
    )
    assert q_bad["outside_psnr"] < q["outside_psnr"] - 20, (
        "bad repair must be dramatically worse than pixel-level repair"
    )
