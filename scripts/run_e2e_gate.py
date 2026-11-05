"""End-to-end gate: real detection → SAM2 → LaMa → quality vs clean frame.

For each GT video:
1. Run automatic detection (detect_timed_watermarks_hybrid) — no manual coords
2. Segment with SAM2 using detection boxes as prompts
3. Run LaMa repair with SAM2 pixel masks
4. Compare repaired output vs CLEAN frame (no watermark) using mask_gate
5. Temporal gate: repair a consecutive frame run and check flicker/ghosting
   against the clean sequence (goal task 11)
6. Assert: outside-mask PSNR high, outside_changed_ratio low, watermark
   removed, temporal artifacts bounded

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

from app.services.watermark_detect import detect_timed_watermarks_hybrid, frame_prompts_at
from app.services.watermark_detection.mask_gate import evaluate_mask, evaluate_repair_quality
from app.services.watermark_detection.sam2_client import Sam2Worker
from app.services.watermark_detection.temporal_gate import (
    evaluate_temporal_consistency,
    temporal_gate_pass,
)


def run_e2e_gate(dataset_dir: str | Path, sample_frames: int = 2) -> dict:
    dataset_dir = Path(dataset_dir)
    raw_idx = json.loads((dataset_dir / "index.json").read_text(encoding="utf-8"))
    index = raw_idx.get("videos", raw_idx) if isinstance(raw_idx, dict) else raw_idx
    all_results = []
    temporal_results = []

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

            # Step 1: Automatic detection runs ONCE per video (no manual coordinates)
            det = detect_timed_watermarks_hybrid(str(video_path))
            detections = det.detections if det.ok and det.detections else []

            # One LaMa worker per video (loading per frame is pure overhead).
            from app.services.video_repair import LamaWorker

            lama = LamaWorker()
            lama.start()

            # Consecutive-frame run for the temporal gate (goal task 11):
            # repair a contiguous sequence, not isolated samples, so flicker
            # and ghosting are actually observable.
            temporal_span = 8
            temporal_start = sample_idx[0] if sample_idx else 0
            temporal_frames = range(
                temporal_start, min(n, temporal_start + temporal_span)
            )

            try:
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

                    # Step 1b: Frame prompt attribution filtering for this specific frame
                    boxes = frame_prompts_at(detections, f_idx, fps, n, width=w, height=h)
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
                    repaired = lama.inpaint(frame_with_wm, sam2_mask)

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

                    # Removal gain: the repaired inside-mask pixels must be much
                    # closer to the clean ground truth than the source was.
                    # Without this, returning the ORIGINAL watermarked frame
                    # unchanged would pass every outside-mask check.
                    inside = gt > 0
                    has_gt = inside.any()
                    if has_gt:
                        src_mae = float(
                            np.abs(clean.astype(float) - frame_with_wm.astype(float))
                            .mean(axis=2)[inside].mean()
                        )
                        rep_mae = float(
                            np.abs(clean.astype(float) - repaired.astype(float))
                            .mean(axis=2)[inside].mean()
                        )
                        if src_mae < 2.0:
                            # Watermark had near-zero opacity (fade-in phase);
                            # pass as long as repair introduced no artifacts.
                            removal_gain = 1.0 if rep_mae < 4.0 else 0.0
                        else:
                            removal_gain = 1.0 - rep_mae / src_mae

                        pixel_pass = (
                            det_ok
                            and m.precision >= 0.3
                            and m.recall >= 0.3
                            and q["outside_psnr"] >= 30
                            and outside_exact_ratio >= 0.95
                            and removal_gain >= 0.5
                        )
                    else:
                        # Clean frame (watermark 100% transparent or absent):
                        # must not hallucinate a mask onto the background.
                        src_mae = 0.0
                        rep_mae = 0.0
                        removal_gain = 1.0
                        pixel_pass = (
                            sam2_mask.sum() == 0
                            and outside_exact_ratio >= 0.99
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
                            "source_inside_mae": round(src_mae, 2),
                            "repaired_inside_mae": round(rep_mae, 2),
                            "removal_gain": round(removal_gain, 3),
                        },
                        "gate_pass": pixel_pass,
                    })

                # Temporal pass on the consecutive run.
                seq, seq_clean, seq_masks = [], [], []
                for f_idx in temporal_frames:
                    fr = cv2.imread(str(video_dir / "frames" / f"{f_idx:06d}.png"))
                    cl = cv2.imread(str(video_dir / "clean_frames" / f"{f_idx:06d}.png"))
                    if fr is None or cl is None:
                        break
                    boxes = frame_prompts_at(detections, f_idx, fps, n, width=w, height=h)
                    mask = np.zeros((h, w), dtype=np.uint8)
                    for box in boxes:
                        seg = worker.segment_frame(fr, box=box)
                        mask = np.maximum(mask, seg["mask"])
                    rep = lama.inpaint(fr, mask)
                    seq.append(rep)
                    seq_clean.append(cl)
                    seq_masks.append(mask)
                if len(seq) >= 4:
                    t_rep = evaluate_temporal_consistency(seq, seq_clean, seq_masks)
                    t_ok = temporal_gate_pass(t_rep)
                    temporal_results.append({
                        "video": video_dir.name,
                        "frames": len(seq),
                        "flicker_ratio": t_rep.flicker_ratio,
                        "flicker_abs": t_rep.flicker_abs,
                        "inside_temporal_psnr_min": t_rep.inside_temporal_psnr_min,
                        "inside_temporal_psnr_mean": t_rep.inside_temporal_psnr_mean,
                        "color_shift_max": t_rep.color_shift_max,
                        "worst_frame": t_rep.worst_frame,
                        "gate_pass": t_ok,
                    })
            finally:
                lama.stop()
    finally:
        worker.stop()

    total = len(all_results)
    passed = sum(1 for r in all_results if r["gate_pass"])
    temporal_total = len(temporal_results)
    temporal_passed = sum(1 for r in temporal_results if r["gate_pass"])
    return {
        "total_samples": total,
        "e2e_passed": passed,
        "e2e_failed": total - passed,
        "temporal_total": temporal_total,
        "temporal_passed": temporal_passed,
        "temporal_results": temporal_results,
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
    per_frame_ok = report["e2e_passed"] == report["total_samples"]
    temporal_ok = report.get("temporal_total", 0) > 0 and (
        report.get("temporal_passed", 0) == report["temporal_total"]
    )
    if per_frame_ok and temporal_ok:
        print("E2E GATE: PASS (per-frame + temporal)")
    else:
        if not per_frame_ok:
            print(f"E2E GATE: FAIL ({report['e2e_passed']}/{report['total_samples']} per-frame)")
        if not temporal_ok:
            print(
                f"E2E GATE: FAIL (temporal {report.get('temporal_passed', 0)}/"
                f"{report.get('temporal_total', 0)})"
            )
        sys.exit(1)
