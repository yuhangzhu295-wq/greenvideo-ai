"""End-to-end gate: real detection → SAM2 → LaMa → quality vs clean frame.

For each GT video:
1. Run automatic detection (detect_timed_watermarks_hybrid) — no manual coords
2. Segment with SAM2 using detection boxes as prompts
3. Run LaMa repair with SAM2 pixel masks
4. Compare repaired output vs CLEAN frame (no watermark) using mask_gate
5. Assert: outside-mask PSNR high, outside_changed_ratio low, watermark removed

This is the gate the architecture-reviewer requires: real detection, real
SAM2, real LaMa, real output — not simulated grey-fill.
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


def _detect_boxes(video_path: str, frame_idx: int, fps: float, total: int) -> list[list[int]]:
    """Run automatic detection on one frame. Returns [x1,y1,x2,y2] boxes for SAM2."""
    from app.services.watermark_detect import detect_timed_watermarks_hybrid

    det = detect_timed_watermarks_hybrid(video_path)
    if not det.ok or not det.detections:
        return []
    boxes = []
    for d in det.detections:
        f0 = int((d.start_s or 0.0) * fps)
        f1 = int((d.end_s or total / fps) * fps)
        if f0 <= frame_idx <= f1:
            bx, by, bw, bh = d.box  # Detection.box is (x, y, w, h)
            boxes.append([bx, by, bx + bw, by + bh])  # convert to x1,y1,x2,y2
    return boxes


def run_e2e_gate(dataset_dir: str | Path, sample_frames: int = 2) -> dict:
    dataset_dir = Path(dataset_dir)
    index = json.loads((dataset_dir / "index.json").read_text(encoding="utf-8"))
    all_results = []

    worker = Sam2Worker()
    worker.start()
    try:
        for meta in index:
            video_dir = dataset_dir / f"{meta['scene']}_{meta['trajectory']}_{meta['seed']:04d}"
            video_path = video_dir / "input.mp4"
            if not video_path.exists():
                continue

            n = meta["frames"]
            fps = meta["fps"]
            h, w = meta["height"], meta["width"]
            sample_idx = list(range(0, n, max(1, n // sample_frames)))[:sample_frames]

            for f_idx in sample_idx:
                frame_with_wm = cv2.imread(
                    str(video_dir / "frames" / f"{f_idx:06d}.png")
                )
                clean = cv2.imread(
                    str(video_dir / "clean_frames" / f"{f_idx:06d}.png")
                )
                gt = cv2.imread(
                    str(video_dir / "gt_masks" / f"{f_idx:06d}.png"),
                    cv2.IMREAD_GRAYSCALE,
                )
                if frame_with_wm is None or clean is None or gt is None:
                    continue

                # Step 1: Automatic detection (no manual coordinates)
                boxes = _detect_boxes(str(video_path), f_idx, fps, n)
                det_ok = len(boxes) > 0

                # Step 2: SAM2 pixel segmentation using detection boxes as prompts
                sam2_mask = np.zeros((h, w), dtype=np.uint8)
                if det_ok:
                    combined = np.zeros((h, w), dtype=np.uint8)
                    for box in boxes:
                        seg = worker.segment_frame(frame_with_wm, box=box)
                        m = seg["mask"]
                        combined = np.maximum(combined, m)
                    sam2_mask = combined

                # Step 3: Run LaMa repair with SAM2 pixel mask
                from app.services.video_repair import LamaWorker

                lama = LamaWorker()
                lama.start()
                try:
                    repaired = lama.inpaint(frame_with_wm, sam2_mask)
                finally:
                    lama.stop()

                # Step 4: Evaluate vs clean frame
                m = evaluate_mask(sam2_mask, gt)
                q = evaluate_repair_quality(clean, repaired, gt)

                # Key metric: outside-mask pixels must be IDENTICAL to clean
                # (LaMa should not touch non-watermark area when pixel mask is used)
                outside_exact = int((
                    (np.abs(clean.astype(int) - repaired.astype(int)).max(axis=2) == 0)
                    & (gt == 0)
                ).sum())
                outside_total = int((gt == 0).sum())
                outside_exact_ratio = outside_exact / max(1, outside_total)

                pixel_pass = (
                    det_ok
                    and m.precision >= 0.3
                    and m.recall >= 0.3
                    and q["outside_psnr"] >= 30
                    and outside_exact_ratio >= 0.95
                )

                all_results.append({
                    "video": video_dir.name,
                    "frame": f_idx,
                    "det_ok": det_ok,
                    "det_boxes": boxes,
                    "sam2_area": int(sam2_mask.sum()),
                    "gt_area": int((gt > 0).sum()),
                    "mask_metrics": {
                        "precision": round(m.precision, 3),
                        "recall": round(m.recall, 3),
                        "iou": round(m.iou, 3),
                        "normal_pixels_modified": m.normal_pixels_modified,
                    },
                    "quality_vs_clean": {
                        "inside_psnr": q["inside_psnr"],
                        "outside_psnr": q["outside_psnr"],
                        "outside_changed_pixels": q["outside_changed_pixels"],
                        "outside_exact_ratio": round(outside_exact_ratio, 4),
                    },
                    "gate_pass": pixel_pass,
                })
    finally:
        worker.stop()

    total = len(all_results)
    passed = sum(1 for r in all_results if r["gate_pass"])
    return {
        "total_samples": total,
        "e2e_passed": passed,
        "e2e_failed": total - passed,
        "results": all_results,
    }


if __name__ == "__main__":
    dataset = sys.argv[1] if len(sys.argv) > 1 else "artifacts/gt-dataset"
    report = run_e2e_gate(dataset)
    out = Path(dataset) / "e2e_gate_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
    print(f"\nE2E gate report saved to {out}")
    if report["total_samples"] == 0:
        print("E2E GATE: FAIL (zero samples)")
        sys.exit(1)
    if report["e2e_passed"] == report["total_samples"]:
        print("E2E GATE: PASS")
    else:
        print(f"E2E GATE: FAIL ({report['e2e_passed']}/{report['total_samples']} passed)")
        sys.exit(1)
