"""Real GPU benchmark for the STTN worker (weights are CC-BY-NC: benchmark only).

Builds a synthetic clip with a moving overlay on a moving background, repairs
it through the actual worker process, and reports:
- shape/frame-count integrity
- outside-mask pixels bit-identical to source
- PSNR/MAE inside the mask region vs the clean ground truth

Run: services/ai-worker/.venv/Scripts/python.exe -I scripts/sttn_benchmark.py
"""
from __future__ import annotations

import math
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

WORKER = Path("services/sttn-worker/sttn_worker.py").resolve()
WEIGHTS = Path("services/sttn-worker/weights/sttn.pth").resolve()
PY = Path("services/ai-worker/.venv/Scripts/python.exe").resolve()


def build_clip(n: int = 24, w: int = 432, h: int = 240):
    """Moving diagonal-stripe background + moving bright overlay text."""
    frames, masks, cleans = [], [], []
    for i in range(n):
        t = i / n
        bg = np.full((h, w, 3), 90, dtype=np.uint8)
        for k in range(-h, w + h, 40):
            x = k + int(t * 30)
            cv2.line(bg, (x, 0), (x - h, h), (60, 140, 60), 6)
        clean = bg.copy()
        cx = int(w * 0.25 + w * 0.5 * t)
        cy = int(h * 0.3 + h * 0.4 * (0.5 + 0.5 * math.sin(t * 6.283)))
        frame = bg.copy()
        cv2.putText(
            frame, "WM", (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
            (255, 255, 255), 3, cv2.LINE_AA,
        )
        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.putText(
            mask, "WM", (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
            255, 3, cv2.LINE_AA,
        )
        frames.append(frame)
        masks.append(mask)
        cleans.append(clean)
    return frames, masks, cleans


def main() -> int:
    frames, masks, cleans = build_clip()
    stage = Path(tempfile.mkdtemp(prefix="sttn-bench-"))
    src, out = stage / "clip.npz", stage / "repaired.npz"
    np.savez_compressed(src, frames=np.stack(frames), masks=np.stack(masks))

    proc = subprocess.Popen(
        [str(PY), "-I", str(WORKER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        encoding="utf-8", cwd=str(WORKER.parent),
    )
    import json

    def send(req):
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        return json.loads(line.strip() or "{}")

    ready = json.loads(proc.stdout.readline().strip())
    assert ready.get("ready"), ready
    load = send({"op": "load", "weights": str(WEIGHTS.resolve())})
    assert load.get("ok"), load
    rep = send({"op": "repair_clip_file", "npz": str(src.resolve()),
                "out_npz": str(out.resolve()), "chunk": 16, "overlap": 4})
    proc.stdin.close()
    proc.wait(timeout=30)
    if not rep.get("ok"):
        print("WORKER ERROR:", rep)
        return 1

    blob = np.load(out)
    repaired = blob["frames"]
    assert repaired.shape == np.stack(frames).shape, (repaired.shape, np.stack(frames).shape)

    all_mask = np.stack(masks) > 0
    outside = ~all_mask
    src_arr = np.stack(frames).astype(int)
    rep_arr = repaired.astype(int)
    clean_arr = np.stack(cleans).astype(int)

    # Outside mask must be bit-identical (compositing guarantee).
    bit_identical = float((np.abs(src_arr - rep_arr).max(axis=3)[outside] == 0).mean())

    # Inside mask: distance to clean ground truth.
    diff = np.abs(clean_arr - rep_arr).mean(axis=3)
    inside_mae = float(diff[all_mask].mean())
    mse = float(((clean_arr - rep_arr) ** 2).mean(axis=3)[all_mask].mean())
    inside_psnr = 10 * math.log10(255.0**2 / max(mse, 1e-6))

    print(f"frames: {repaired.shape[0]} | chunks: {int(blob['chunks'])}")
    print(f"outside_mask_bit_identical: {bit_identical:.6f}")
    print(f"inside_mask_mae: {inside_mae:.2f} | inside_mask_psnr: {inside_psnr:.2f}")
    ok = bit_identical == 1.0 and inside_psnr > 15.0
    print("STTN BENCHMARK:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
