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

            # Active watermark frames where ground truth exists: sample frames
            # where the watermark is actually visible (not during 100% transparent fade-in)
            gt_masks_dir = video_dir / "gt_masks"
            active_frames = []
            for f_cand in range(n):
                gt_f = gt_masks_dir / f"{f_cand:06d}.png"
                if gt_f.exists():
                    g_img = cv2.imread(str(gt_f), cv2.IMREAD_GRAYSCALE)
                    if g_img is not None and (g_img > 0).any():
                        active_frames.append(f_cand)

            temporal_span = 8
            if active_frames:
                step = max(1, len(active_frames) // (sample_frames + 1))
                sample_idx = [
                    active_frames[min(len(active_frames) - 1, (i + 1) * step)]
                    for i in range(sample_frames)
                ]
                mid = len(active_frames) // 2
                temporal_start = max(0, active_frames[mid] - temporal_span // 2)
                temporal_frames = range(temporal_start, min(n, temporal_start + temporal_span))
            else:
                sample_idx = list(range(0, n, max(1, n // sample_frames)))[:sample_frames]
                temporal_start = sample_idx[0] if sample_idx else 0
                temporal_frames = range(temporal_start, min(n, temporal_start + temporal_span))

            # Step 1: Automatic detection runs ONCE per video (no manual coordinates)
            det = detect_timed_watermarks_hybrid(str(video_path))
            detections = det.detections if det.ok and det.detections else []

            # One LaMa worker per video (loading per frame is pure overhead).
            from app.services.video_repair import LamaWorker, _inpaint_frame

            lama = LamaWorker()
            lama.start()

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

                    # Step 3: Run LaMa repair with SAM2 pixel mask (ROI-cropped)
                    repaired = _inpaint_frame(frame_with_wm, sam2_mask, worker=lama)

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
                    edge_reduction = 0.0
                    changed_inside_ratio = 0.0
                    if has_gt:
                        src_mae = float(
                            np.abs(clean.astype(float) - frame_with_wm.astype(float))
                            .mean(axis=2)[inside].mean()
                        )
                        rep_mae = float(
                            np.abs(clean.astype(float) - repaired.astype(float))
                            .mean(axis=2)[inside].mean()
                        )
                        actual_gain = 1.0 - rep_mae / max(src_mae, 1e-6)
                        removal_gain = round(actual_gain, 3)

                        diff_inside = (
                            np.abs(frame_with_wm.astype(int) - repaired.astype(int))
                            .max(axis=2)[inside]
                        )
                        changed_inside = int((diff_inside > 0).sum())
                        changed_inside_ratio = changed_inside / max(1, inside.sum())

                        # Edge reduction inside watermark: measure text edge attenuation
                        gray_fr = cv2.cvtColor(frame_with_wm, cv2.COLOR_BGR2GRAY)
                        gray_rep = cv2.cvtColor(repaired, cv2.COLOR_BGR2GRAY)
                        sobel_fr = float(
                            np.abs(cv2.Sobel(gray_fr, cv2.CV_32F, 1, 1))[inside].mean()
                        )
                        sobel_rep = float(
                            np.abs(cv2.Sobel(gray_rep, cv2.CV_32F, 1, 1))[inside].mean()
                        )
                        edge_reduction = 1.0 - sobel_rep / max(sobel_fr, 1e-6)

                        # Untouched check: if repair did not modify the watermark area, FAIL
                        if changed_inside_ratio < 0.5:
                            gain_ok = False
                        elif src_mae >= 20.0:
                            gain_ok = actual_gain >= 0.5
                        else:
                            # Low-contrast watermark (contrast comparable to sensor noise):
                            # requires actual inpainting coverage (>=80% pixels rewritten),
                            # acceptable PSNR (>=20dB), and no hallucination blowup.
                            gain_ok = (actual_gain >= 0.2) or (
                                changed_inside_ratio >= 0.8
                                and q["inside_psnr"] >= 20.0
                                and rep_mae <= src_mae + 1.5
                            )

                        pixel_pass = (
                            det_ok
                            and m.precision >= 0.3
                            and m.recall >= 0.3
                            and q["outside_psnr"] >= 30
                            and outside_exact_ratio >= 0.95
                            and gain_ok
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
                            "changed_inside_ratio": round(changed_inside_ratio, 4),
                            "edge_reduction": round(edge_reduction, 4),
                        },
                        "gate_pass": pixel_pass,
                    })

                # Temporal pass on the consecutive run.
                seq, seq_clean, seq_gt_masks = [], [], []
                for f_idx in temporal_frames:
                    fr = cv2.imread(str(video_dir / "frames" / f"{f_idx:06d}.png"))
                    cl = cv2.imread(str(video_dir / "clean_frames" / f"{f_idx:06d}.png"))
                    gt_path = str(video_dir / "gt_masks" / f"{f_idx:06d}.png")
                    gt_m = cv2.imread(gt_path, cv2.IMREAD_GRAYSCALE)
                    if fr is None or cl is None or gt_m is None:
                        break
                    boxes = frame_prompts_at(detections, f_idx, fps, n, width=w, height=h)
                    mask = np.zeros((h, w), dtype=np.uint8)
                    for box in boxes:
                        seg = worker.segment_frame(fr, box=box)
                        mask = np.maximum(mask, seg["mask"])
                    rep = _inpaint_frame(fr, mask, worker=lama)
                    seq.append(rep)
                    seq_clean.append(cl)
                    seq_gt_masks.append(gt_m)
                if len(seq) >= 4 and any((m > 0).any() for m in seq_gt_masks):
                    t_rep = evaluate_temporal_consistency(seq, seq_clean, seq_gt_masks)
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
