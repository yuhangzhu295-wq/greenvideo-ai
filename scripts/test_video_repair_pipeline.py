"""Run the real LaMa video repair on the Ground-Truth scenarios.

Uses the known Ground-Truth mask (not the auto detector) so this test isolates
the repair engine: mask -> LaMa -> mux -> verify.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "api"))

GT = ROOT / "artifacts" / "watermark-e2e"


def mask_for(scenario: str, frame_idx: int) -> np.ndarray:
    masks = sorted((GT / scenario / "mask").glob("mask_*.png"))
    m = cv2.imread(str(masks[frame_idx]), cv2.IMREAD_GRAYSCALE)
    return m


def run(scenario: str) -> dict:
    from app.services.video_repair import repair_video

    src = GT / scenario / "input.mp4"
    out_dir = GT / scenario
    out = out_dir / "output.mp4"

    cap = cv2.VideoCapture(str(src))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()

    def provider(idx: int, frame):
        return mask_for(scenario, idx)

    report = repair_video(src, out, provider)
    return {
        "engine": report.engine,
        "frames_total": report.frames_total,
        "frames_repaired": report.frames_repaired,
        "frames_skipped": report.frames_skipped,
        "mask_coverage_pct": report.mask_coverage_pct,
        "audio_preserved": report.audio_preserved,
        "output": f"{report.output_width}x{report.output_height}",
        "output_frames": report.output_frames,
        "output_duration": report.output_duration,
        "notes": report.notes,
        "input_frames": total,
    }


def main() -> int:
    only = sys.argv[1:] or None
    results = {}
    for scenario in sorted(p.name for p in GT.iterdir() if p.is_dir() and p.name.startswith("T0")):
        if only and scenario not in only:
            continue
        print(f"== {scenario}")
        try:
            info = run(scenario)
            results[scenario] = info
            print(
                f"   engine={info['engine']} repaired={info['frames_repaired']}"
                f"/{info['frames_total']} skipped={info['frames_skipped']}"
                f" audio={info['audio_preserved']} out={info['output']}"
                f" dur={info['output_duration']}"
            )
        except Exception as exc:
            results[scenario] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"   FAILED: {exc}")

    (GT / "repair_engine_report.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    ok = all("error" not in r for r in results.values())
    print(f"\nRepair engine run: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
