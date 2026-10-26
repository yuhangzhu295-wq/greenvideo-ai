"""Measure repair quality against the clean Ground-Truth original.

For every scenario the report compares the repaired output with the clean
original and with the watermarked input:

* PSNR / SSIM inside the watermark mask  - did the watermark go away?
* PSNR / SSIM outside the watermark mask - was the rest of the picture kept?
* Frame-to-frame flicker                 - temporal stability
* Audio stream presence
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
GT = ROOT / "artifacts" / "watermark-e2e"


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    d = np.abs(a.astype(np.float64) - b.astype(np.float64))
    mse = float((d ** 2).mean())
    if mse == 0:
        return float("inf")
    return round(10.0 * np.log10(255.0 ** 2 / mse), 3)


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    try:
        from skimage.metrics import structural_similarity

        return round(
            float(
                structural_similarity(a, b, channel_axis=2, data_range=255)
            ),
            4,
        )
    except Exception:
        return float("nan")


def read_frames(path: Path, limit: int | None = None) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    out = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        out.append(f)
        if limit and len(out) >= limit:
            break
    cap.release()
    return out


def mask_at(scenario: str, idx: int) -> np.ndarray:
    masks = sorted((GT / scenario / "mask").glob("mask_*.png"))
    m = cv2.imread(str(masks[idx]), cv2.IMREAD_GRAYSCALE)
    return m > 127


def has_audio(path: Path) -> bool:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    return bool(r.stdout.strip())


def flicker(frames: list[np.ndarray]) -> float:
    if len(frames) < 3:
        return 0.0
    diffs = []
    for i in range(1, len(frames)):
        d = np.abs(
            frames[i].astype(np.float32) - frames[i - 1].astype(np.float32)
        ).mean()
        diffs.append(d)
    return round(float(np.std(diffs)), 4)


def measure(scenario: str, stride: int = 4) -> dict:
    clean = read_frames(GT / scenario / "clean_original.mp4")
    water = read_frames(GT / scenario / "input.mp4")
    out = read_frames(GT / scenario / "output.mp4")
    n = min(len(clean), len(water), len(out))
    if n == 0:
        return {"error": "missing frames"}

    idxs = list(range(0, n, max(1, stride)))
    in_psnr, out_psnr, in_ssim, out_ssim = [], [], [], []
    out_out_psnr, out_out_ssim = [], []
    mask_cov = []

    for i in idxs:
        m = mask_at(scenario, i)
        if not m.any():
            continue
        mask_cov.append(100.0 * float(m.mean()))
        for arr_in, arr_out, p_list, s_list in (
            (water[i][m], out[i][m], in_psnr, in_ssim),
            (clean[i][m], out[i][m], out_psnr, out_ssim),
        ):
            # Build 1-pixel-tall images so PSNR/SSIM work on the masked subset.
            a = water[i].copy(); b = out[i].copy()
            if p_list is in_psnr:
                p_list.append(psnr(water[i][m].reshape(1, -1, 3), out[i][m].reshape(1, -1, 3)))
                s_list.append(ssim(water[i], out[i]))
            else:
                p_list.append(psnr(clean[i][m].reshape(1, -1, 3), out[i][m].reshape(1, -1, 3)))
                s_list.append(ssim(clean[i], out[i]))

        outside = ~m
        if outside.any():
            out_out_psnr.append(
                psnr(clean[i][outside].reshape(1, -1, 3), out[i][outside].reshape(1, -1, 3))
            )
            out_out_ssim.append(ssim(clean[i], out[i]))

    def stats(xs):
        xs = [x for x in xs if x == x and x != float("inf")]
        if not xs:
            return {"mean": None, "min": None, "max": None}
        return {
            "mean": round(float(np.mean(xs)), 3),
            "min": round(float(np.min(xs)), 3),
            "max": round(float(np.max(xs)), 3),
        }

    return {
        "frames_measured": len(idxs),
        "mask_coverage_pct": round(float(np.mean(mask_cov)), 3) if mask_cov else 0.0,
        "watermark_region": {
            "input_vs_output_psnr": stats(in_psnr),
            "output_vs_clean_psnr": stats(out_psnr),
            "output_vs_clean_ssim": stats(out_ssim),
        },
        "outside_region": {
            "output_vs_clean_psnr": stats(out_out_psnr),
            "output_vs_clean_ssim": stats(out_out_ssim),
        },
        "flicker_std_input": flicker(water[:n:max(1, stride)]),
        "flicker_std_output": flicker(out[:n:max(1, stride)]),
        "audio_preserved": has_audio(GT / scenario / "output.mp4"),
        "output_frames": len(out),
        "input_frames": len(water),
    }


def main() -> int:
    only = sys.argv[1:] or None
    report = {}
    for scenario in sorted(p.name for p in GT.iterdir() if p.is_dir() and p.name.startswith("T0")):
        if only and scenario not in only:
            continue
        if not (GT / scenario / "output.mp4").exists():
            print(f"{scenario}: no output yet (skipped)")
            continue
        r = measure(scenario)
        report[scenario] = r
        wr = r.get("watermark_region", {})
        orr = r.get("outside_region", {})
        print(
            f"{scenario}: mask_cov={r.get('mask_coverage_pct')}% "
            f"wm_psnr(out vs clean)={wr.get('output_vs_clean_psnr', {}).get('mean')} "
            f"outside_psnr={orr.get('output_vs_clean_psnr', {}).get('mean')} "
            f"flicker_in={r.get('flicker_std_input')} out={r.get('flicker_std_output')} "
            f"audio={r.get('audio_preserved')}"
        )
    (GT / "quality_metrics.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
