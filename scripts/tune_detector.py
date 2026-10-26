"""Tune the watermark detector on box-level IoU against Ground Truth.

Pixel IoU is not the right target: a watermark's interior (inside letter
strokes) legitimately has no local contrast, so a perfect detector can never
reach 1.0 there. What matters for repair quality is that the predicted boxes
cover the watermark tightly and do not spill onto the picture.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
GT = ROOT / "artifacts" / "watermark-e2e"


def gt_boxes(scenario: str, frame_idx: int = 5) -> list[tuple[int, int, int, int]]:
    masks = sorted((GT / scenario / "mask").glob("mask_*.png"))
    m = cv2.imread(str(masks[frame_idx]), cv2.IMREAD_GRAYSCALE)
    m = (m > 127).astype(np.uint8) * 255
    n, _lab, st, _c = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in st[i])
        if area < 20:
            continue
        out.append((x, y, w, h))
    return out


def pred_boxes(scenario: str, params: dict) -> list[tuple[int, int, int, int]]:
    sys.path.insert(0, str(ROOT / "services" / "api"))
    for mod in [m for m in list(sys.modules) if m.startswith("app.services")]:
        del sys.modules[mod]
    from app.services.watermark_detect import detect_watermarks

    frames_p = sorted((GT / scenario / "before_frames").glob("before_*.png"))[::3][:10]
    frames = [cv2.imread(str(p)) for p in frames_p]
    res = detect_watermarks(frames, **params)
    return [d.box for d in res.detections]


def box_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def match_score(
    gt: list[tuple[int, int, int, int]], pred: list[tuple[int, int, int, int]]
) -> tuple[float, float, float]:
    """Return (best_mean_iou, coverage, spill) for the predicted box set."""
    if not gt:
        return 0.0, 0.0, 0.0
    gt_mask = np.zeros((360, 640), dtype=bool)
    for (x, y, w, h) in gt:
        gt_mask[y : y + h, x : x + w] = True
    pred_mask = np.zeros((360, 640), dtype=bool)
    for (x, y, w, h) in pred:
        pred_mask[y : y + h, x : x + w] = True

    inter = int((gt_mask & pred_mask).sum())
    union = int((gt_mask | pred_mask).sum())
    set_iou = inter / union if union else 0.0
    coverage = inter / int(gt_mask.sum()) if gt_mask.sum() else 0.0
    spill = (
        int((pred_mask & ~gt_mask).sum()) / int((~gt_mask).sum())
        if (~gt_mask).sum()
        else 0.0
    )
    return set_iou, coverage, spill


def main() -> int:
    scenarios = sorted(p.name for p in GT.iterdir() if p.is_dir() and p.name.startswith("T0"))
    grids = []
    for th in (0.5, 0.55, 0.6):
        for min_ratio in (0.0008, 0.002):
            for max_ratio in (0.10, 0.20):
                grids.append(
                    {
                        "response_threshold": th,
                        "min_area_ratio": min_ratio,
                        "max_area_ratio": max_ratio,
                    }
                )

    best = None
    for params in grids:
        rows = {}
        for sc in scenarios:
            gt = gt_boxes(sc)
            pred = pred_boxes(sc, params)
            siou, cov, spill = match_score(gt, pred)
            rows[sc] = {
                "set_iou": round(siou, 4),
                "coverage": round(cov, 4),
                "spill_pct": round(100 * spill, 3),
                "pred_boxes": pred,
                "gt_boxes": gt,
            }
        mean_iou = float(np.mean([r["set_iou"] for r in rows.values()]))
        mean_spill = float(np.mean([r["spill_pct"] for r in rows.values()]))
        objective = mean_iou - mean_spill / 100.0
        if best is None or objective > best["objective"]:
            best = {"objective": objective, "params": params, "rows": rows,
                    "mean_iou": mean_iou, "mean_spill": mean_spill}
        print(f"{params} -> mean_iou={mean_iou:.3f} mean_spill={mean_spill:.2f}%")

    print("\n=== BEST ===")
    print("params:", best["params"])
    print(f"mean set IoU={best['mean_iou']:.3f}  mean spill={best['mean_spill']:.2f}%")
    for sc, r in best["rows"].items():
        print(f"  {sc}: iou={r['set_iou']:.3f} cov={r['coverage']:.3f} spill={r['spill_pct']:.2f}%")

    (GT / "detector_tuning.json").write_text(json.dumps(best, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
