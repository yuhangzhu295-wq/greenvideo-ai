"""Measure watermark-detection accuracy against the Ground-Truth masks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.services.watermark_detect import detect_watermarks  # noqa: E402

GT = ROOT / "artifacts" / "watermark-e2e"


def load_frames(scenario: str, limit: int = 20) -> list[np.ndarray]:
    d = GT / scenario / "before_frames"
    paths = sorted(d.glob("before_*.png"))[:limit]
    return [cv2.imread(str(p)) for p in paths if cv2.imread(str(p)) is not None]


def gt_mask(scenario: str) -> np.ndarray:
    d = GT / scenario / "mask"
    p = sorted(d.glob("mask_*.png"))[10]
    m = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
    return (m > 127).astype(np.uint8)


def score(pred: np.ndarray, truth: np.ndarray) -> dict:
    p = pred > 0
    t = truth > 0
    inter = int((p & t).sum())
    union = int((p | t).sum())
    iou = inter / union if union else 0.0
    recall = inter / int(t.sum()) if t.sum() else 0.0
    precision = inter / int(p.sum()) if p.sum() else 0.0
    # Damage to the real picture: predicted pixels outside the watermark.
    collateral = round(100.0 * float((p & ~t).sum()) / float((~t).sum()), 3)
    return {
        "iou": round(iou, 4),
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "collateral_damage_pct": collateral,
        "gt_pixels": int(t.sum()),
        "pred_pixels": int(p.sum()),
    }


def main() -> int:
    report = {}
    for scenario in sorted(p.name for p in GT.iterdir() if p.is_dir() and p.name.startswith("T0")):
        frames = load_frames(scenario)
        if not frames:
            continue
        result = detect_watermarks(frames)
        truth = gt_mask(scenario)
        pred = result.static_mask if result.static_mask is not None else np.zeros_like(truth)
        entry = {
            "title": scenario,
            "detected": result.ok,
            "reason": result.reason,
            "stats": result.stats,
            "boxes": [d.box for d in result.detections],
            "metrics": score(pred, truth),
        }
        report[scenario] = entry
        print(
            f"{scenario}: detected={result.ok} IoU={entry['metrics']['iou']:.3f} "
            f"recall={entry['metrics']['recall']:.3f} "
            f"collateral={entry['metrics']['collateral_damage_pct']:.3f}% "
            f"boxes={len(result.detections)}"
        )

    out = GT / "detection_accuracy.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nDetection report -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
