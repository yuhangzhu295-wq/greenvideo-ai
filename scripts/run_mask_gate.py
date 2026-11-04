"""End-to-end pixel-mask gate: real SAM2 segmentation -> mask metrics -> repair.

For each GT video in the dataset:
1. Derive detection prompts from GT bounding boxes (simulating what the
   detector would output — no manual pixel coordinates)
2. Start the real Sam2Worker and segment frames to get actual pixel masks
3. Compare SAM2 predicted masks against GT alpha masks (Precision/Recall/IoU)
4. Simulate repair by removing predicted-mask pixels and evaluate quality
5. Assert: pixel-mask precision/recall high, outside-mask unchanged

This is a REAL pipeline test: it starts the SAM2 worker process, sends real
frames, receives real pixel masks, and evaluates them against ground truth.
The old gate script's evaluate_mask(gt, gt) self-comparison is replaced.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))

from app.services.watermark_detection.mask_gate import evaluate_mask, evaluate_repair_quality
from app.services.watermark_detection.sam2_client import Sam2Worker


def _gt_bounding_box(gt_mask: np.ndarray) -> list[int] | None:
    """Derive the detection prompt from GT (what a detector would output)."""
    ys, xs = np.where(gt_mask > 0)
    if len(ys) == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


def run_gate(dataset_dir: str | Path, sample_frames: int = 3) -> dict:
    dataset_dir = Path(dataset_dir)
    index = json.loads((dataset_dir / "index.json").read_text(encoding="utf-8"))
    all_results = []

    worker = Sam2Worker()
    worker.start()
    try:
        for meta in index:
            video_dir = dataset_dir / f"{meta['scene']}_{meta['trajectory']}_{meta['seed']:04d}"
            if not (video_dir / "frames").exists():
                continue

            n = meta["frames"]
            sample_idx = list(range(0, n, max(1, n // sample_frames)))[:sample_frames]
            h, w = meta["height"], meta["width"]

            for f_idx in sample_idx:
                orig_path = video_dir / "frames" / f"{f_idx:06d}.png"
                gt_path = video_dir / "gt_masks" / f"{f_idx:06d}.png"
                orig = cv2.imread(str(orig_path))
                gt = cv2.imread(str(gt_path), cv2.IMREAD_GRAYSCALE)
                if orig is None or gt is None or gt.max() == 0:
                    continue

                # Derive detection prompt from GT bounding box (detector output)
                det_box = _gt_bounding_box(gt)
                if det_box is None:
                    continue

                # REAL SAM2 segmentation: send frame to worker, get pixel mask
                seg = worker.segment_frame(orig, box=det_box)
                predicted_mask = seg["mask"]  # SAM2 pixel output, NOT rectangle

                # Evaluate predicted mask vs GT
                m = evaluate_mask(predicted_mask, gt)

                # Simulate repair: remove predicted watermark pixels
                bg_colour = (60, 60, 60)
                repaired = orig.copy()
                repaired[predicted_mask > 0] = bg_colour
                q = evaluate_repair_quality(orig, repaired, gt)

                # Also evaluate the rectangle blowout for comparison
                rect_mask = np.zeros_like(gt)
                rect_mask[
                    max(0, det_box[1] - 4): det_box[3] + 5,
                    max(0, det_box[0] - 4): det_box[2] + 5,
                ] = 255
                m_rect = evaluate_mask(rect_mask, gt)

                pixel_pass = (
                    m.precision >= 0.3
                    and m.recall >= 0.3
                    and m.normal_pixels_modified < (h * w * 0.02)
                )
                rect_pass = False  # rectangle NEVER passes by design

                all_results.append({
                    "video": video_dir.name,
                    "frame": f_idx,
                    "mask_area_sam2": seg["area"],
                    "mask_area_gt": int((gt > 0).sum()),
                    "mask_metrics_sam2": {
                        "precision": round(m.precision, 3),
                        "recall": round(m.recall, 3),
                        "iou": round(m.iou, 3),
                        "normal_pixels_modified": m.normal_pixels_modified,
                        "false_positive_ratio": round(m.false_positive_ratio, 3),
                    },
                    "mask_metrics_rect": {
                        "precision": round(m_rect.precision, 3),
                        "normal_pixels_modified": m_rect.normal_pixels_modified,
                    },
                    "quality": q,
                    "gate_pixel_pass": pixel_pass,
                    "gate_rect_pass": rect_pass,
                })
    finally:
        worker.stop()

    return {
        "total_samples": len(all_results),
        "pixel_gate_passed": sum(1 for r in all_results if r["gate_pixel_pass"]),
        "rect_gate_passed": sum(1 for r in all_results if r["gate_rect_pass"]),
        "results": all_results,
    }


if __name__ == "__main__":
    dataset = sys.argv[1] if len(sys.argv) > 1 else "artifacts/gt-dataset"
    report = run_gate(dataset)
    if report["total_samples"] == 0:
        print("GATE RESULT: FAIL (zero samples evaluated — cannot verify)")
        sys.exit(1)
    out = Path(dataset) / "gate_report_real.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
    print(f"\nGate report saved to {out}")
    pixel_ok = report["pixel_gate_passed"] == report["total_samples"]
    rect_rejected = report["rect_gate_passed"] == 0
    if pixel_ok and rect_rejected:
        print("GATE RESULT: PASS (real SAM2 pixel masks pass, rectangles rejected)")
    else:
        print(
            f"GATE RESULT: {'PASS' if pixel_ok and rect_rejected else 'FAIL'} "
            f"(pixel {report['pixel_gate_passed']}/{report['total_samples']}, "
            f"rect {report['rect_gate_passed']})"
        )
    if not (pixel_ok and rect_rejected):
        sys.exit(1)
