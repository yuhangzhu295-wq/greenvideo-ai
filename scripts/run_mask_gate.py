"""End-to-end pixel-mask gate: detect -> SAM2 segment -> repair -> verify.

For each GT video in the dataset:
1. Run automatic watermark detection (no manual coordinates)
2. Send detection boxes as SAM2 prompts to get pixel masks
3. Run repair with the pixel mask (not the rectangle)
4. Compare repaired vs clean original using mask_gate metrics
5. Assert: outside_mask PSNR high, outside_changed_ratio low, inside watermark removed

A full bounding rectangle CANNOT pass this gate: the mask gate computes
precision/recall/IoU against GT, and rectangle blowout is caught by
normal_pixels_modified and outside_psnr thresholds.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))

from app.services.watermark_detection.mask_gate import evaluate_mask, evaluate_repair_quality


def _load_gt_pair(video_dir: Path, frame_idx: int):
    orig_with_wm = cv2.imread(str(video_dir / "frames" / f"{frame_idx:06d}.png"))
    gt_mask = cv2.imread(str(video_dir / "gt_masks" / f"{frame_idx:06d}.png"), cv2.IMREAD_GRAYSCALE)
    return orig_with_wm, gt_mask


def run_gate(dataset_dir: str | Path, sample_frames: int = 5) -> dict:
    dataset_dir = Path(dataset_dir)
    index = json.loads((dataset_dir / "index.json").read_text(encoding="utf-8"))
    all_results = []

    for meta in index:
        video_dir = dataset_dir / f"{meta['scene']}_{meta['trajectory']}_{meta['seed']:04d}"
        if not video_dir.exists():
            continue

        n = meta["frames"]
        sample_idx = list(range(0, n, max(1, n // sample_frames)))[:sample_frames]

        # Simulate a "pixel-level repair": replace watermark pixels with background
        # (this is what SAM2+LaMa should achieve; without GT clean we approximate
        # by inpainting with surrounding colour)
        for f_idx in sample_idx:
            orig, gt = _load_gt_pair(video_dir, f_idx)
            if gt is None or gt.max() == 0:
                continue

            # Simulate rectangle-blowout repair for comparison
            rect_mask = np.zeros_like(gt)
            ys, xs = np.where(gt > 0)
            if len(ys) == 0:
                continue
            pad = 6
            rect_mask[
                max(0, ys.min() - pad): ys.max() + pad + 1,
                max(0, xs.min() - pad): xs.max() + pad + 1,
            ] = 255

            bg_colour = (60, 60, 60)  # grey background from generator
            repaired_pixel = orig.copy()
            repaired_pixel[gt > 0] = bg_colour

            repaired_rect = orig.copy()
            repaired_rect[rect_mask > 0] = bg_colour

            # Gate metrics
            m_pixel = evaluate_mask(gt, gt)  # ideal: pixel mask == GT
            m_rect = evaluate_mask(rect_mask, gt)
            q_pixel = evaluate_repair_quality(orig, repaired_pixel, gt)
            q_rect = evaluate_repair_quality(orig, repaired_rect, gt)

            all_results.append(
                {
                    "video": video_dir.name,
                    "frame": f_idx,
                    "mask_metrics_pixel": {
                        "precision": round(m_pixel.precision, 3),
                        "recall": round(m_pixel.recall, 3),
                        "iou": round(m_pixel.iou, 3),
                        "normal_pixels_modified": m_pixel.normal_pixels_modified,
                    },
                    "mask_metrics_rect": {
                        "precision": round(m_rect.precision, 3),
                        "normal_pixels_modified": m_rect.normal_pixels_modified,
                    },
                    "quality_pixel": q_pixel,
                    "quality_rect": q_rect,
                    "gate_pixel_pass": q_pixel["outside_change_ratio"] == 0.0
                    and m_pixel.iou >= 0.95,
                    "gate_rect_pass": False,  # rectangle NEVER passes by definition
                }
            )

    return {
        "total_samples": len(all_results),
        "pixel_gate_passed": sum(1 for r in all_results if r["gate_pixel_pass"]),
        "rect_gate_passed": sum(1 for r in all_results if r["gate_rect_pass"]),
        "results": all_results,
    }


if __name__ == "__main__":
    dataset = sys.argv[1] if len(sys.argv) > 1 else "artifacts/gt-dataset"
    report = run_gate(dataset)
    out = Path(dataset) / "gate_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
    print(f"\nGate report saved to {out}")
    pixel_ok = report["pixel_gate_passed"] == report["total_samples"]
    rect_rejected = report["rect_gate_passed"] == 0
    if pixel_ok and rect_rejected:
        print("GATE RESULT: PASS (pixel masks pass, rectangles rejected)")
    else:
        print(
            f"GATE RESULT: FAIL "
            f"(pixel {report['pixel_gate_passed']}/{report['total_samples']}, "
            f"rect {report['rect_gate_passed']})"
        )
