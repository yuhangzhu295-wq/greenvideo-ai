"""Ground-truth evaluation for pixel-level watermark masks.

Measures Mask Precision / Recall / IoU / false-positive ratio against a known
alpha mask. A full bounding rectangle cannot pass these gates unless it truly
matches the watermark shape - the whole point of the pixel-mask refactor.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MaskMetrics:
    precision: float
    recall: float
    iou: float
    false_positive_ratio: float  # predicted-but-not-GT pixels / total predicted
    false_negative_ratio: float  # GT-but-not-predicted pixels / total GT
    normal_pixels_modified: int  # pixels outside GT that got masked
    total_normal_pixels: int


def evaluate_mask(predicted, gt) -> MaskMetrics:
    """Compare a predicted binary mask against ground truth.

    ``normal_pixels_modified`` counts pixels masked outside the GT watermark -
    the "collateral damage" metric the reviewer prioritises.
    """
    import numpy as np

    pred = (np.asarray(predicted) > 0)
    gt_b = (np.asarray(gt) > 0)

    tp = int((pred & gt_b).sum())
    fp = int((pred & ~gt_b).sum())
    fn = int((~pred & gt_b).sum())
    tn = int((~pred & ~gt_b).sum())

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    iou = tp / (tp + fp + fn) if (tp + fp + fn) > 0 else 0.0
    fp_ratio = fp / (tp + fp) if (tp + fp) > 0 else 0.0
    fn_ratio = fn / (tp + fn) if (tp + fn) > 0 else 0.0

    return MaskMetrics(
        precision=precision,
        recall=recall,
        iou=iou,
        false_positive_ratio=fp_ratio,
        false_negative_ratio=fn_ratio,
        normal_pixels_modified=fp,
        total_normal_pixels=fp + tn,
    )


def evaluate_repair_quality(original, repaired, gt_mask):
    """Compute PSNR/SSIM inside and outside the watermark region.

    Outside-mask PSNR must be extremely high (ideally identical pixels) - the
    gate that the old rectangle pipeline failed.
    """
    import cv2
    import numpy as np

    orig = np.asarray(original, dtype=np.float32)
    rep = np.asarray(repaired, dtype=np.float32)
    inside = np.asarray(gt_mask) > 0
    outside = ~inside

    def _psnr_region(diff_sq: np.ndarray) -> float:
        mse = float(diff_sq.mean()) if diff_sq.size else 0.0
        if mse <= 0:
            return 99.0
        return float(10.0 * np.log10(255.0 * 255.0 / mse))

    inside_diff = (orig - rep) ** 2
    outside_diff = (orig - rep) ** 2

    inside_psnr = _psnr_region(inside_diff[inside]) if inside.any() else 99.0
    outside_psnr = _psnr_region(outside_diff[outside]) if outside.any() else 99.0

    outside_changed = int((np.abs(orig - rep).max(axis=2)[outside] > 2).sum())
    outside_total = int(outside.sum())

    # SSIM per region (simplified: global SSIM computed on masked crops).
    try:
        from skimage.metrics import structural_similarity

        gray_o = cv2.cvtColor(np.asarray(original, dtype=np.uint8), cv2.COLOR_BGR2GRAY)
        gray_r = cv2.cvtColor(np.asarray(repaired, dtype=np.uint8), cv2.COLOR_BGR2GRAY)
        ys, xs = np.where(inside)
        if len(ys) > 0:
            y1, y2 = max(0, ys.min() - 4), min(gray_o.shape[0], ys.max() + 5)
            x1, x2 = max(0, xs.min() - 4), min(gray_o.shape[1], xs.max() + 5)
            inside_ssim = structural_similarity(
                gray_o[y1:y2, x1:x2], gray_r[y1:y2, x1:x2], data_range=255
            )
        else:
            inside_ssim = 1.0
    except ImportError:
        inside_ssim = None

    return {
        "inside_psnr": round(inside_psnr, 2),
        "outside_psnr": round(outside_psnr, 2),
        "inside_ssim": inside_ssim,
        "outside_changed_pixels": outside_changed,
        "outside_total_pixels": outside_total,
        "outside_change_ratio": round(outside_changed / max(1, outside_total), 6),
    }
