"""Debug: SAM2 segmentation quality with different prompt boxes vs GT.

Run: services/ai-worker/.venv/Scripts/python.exe -I scripts/debug_sam2_prompts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))

from app.services.watermark_detection.mask_gate import evaluate_mask
from app.services.watermark_detection.sam2_client import Sam2Worker

CASES = [
    # (video_dir, frame, prompt_box, label)
    ("building_continuous_drift_0045", 0, [538, 510, 810, 544], "tight OCR box"),
    ("building_continuous_drift_0045", 0, [527, 502, 821, 555], "padded det box"),
    ("building_continuous_drift_0045", 0, [534, 506, 814, 548], "OCR box +4px"),
    ("night_corner_hop_0044", 0, [18, 60, 328, 94], "tight OCR box"),
    ("grass_fixed_tl_0042", 0, [16, 62, 330, 100], "GT box"),
]


def main() -> None:
    worker = Sam2Worker()
    worker.start()
    try:
        for video_dir, f_idx, box, label in CASES:
            base = Path("artifacts/gt-dataset") / video_dir
            frame = cv2.imread(str(base / "frames" / f"{f_idx:06d}.png"))
            gt = cv2.imread(str(base / "gt_masks" / f"{f_idx:06d}.png"), cv2.IMREAD_GRAYSCALE)
            seg = worker.segment_frame(frame, box=box)
            m = evaluate_mask(seg["mask"], gt)
            area = int((seg["mask"] > 0).sum())
            print(
                f"{video_dir[:20]:20s} f={f_idx} {label:16s} box={box} "
                f"mask_px={area:6d} gt_px={int((gt>0).sum()):5d} "
                f"P={m.precision:.3f} R={m.recall:.3f} IoU={m.iou:.3f}"
            )
    finally:
        worker.stop()


if __name__ == "__main__":
    main()
