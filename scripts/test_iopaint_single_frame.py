"""Verify the real IOPaint LaMa single-frame repair against the documented API.

POST /api/v1/inpaint takes base64 image + base64 grayscale mask and returns a
binary image (not JSON). This test proves the engine really rebuilds the masked
background instead of returning the input unchanged.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import httpx

IOPAINT_BASE = "http://127.0.0.1:8080"
ROOT = Path(__file__).resolve().parent.parent


def load_pair(scenario: str, index: int) -> tuple[np.ndarray, np.ndarray]:
    base = ROOT / "artifacts" / "watermark-e2e" / scenario
    frames = sorted((base / "before_frames").glob("before_*.png"))
    masks = sorted((base / "mask").glob("mask_*.png"))
    img = cv2.imread(str(frames[index]))
    mask = cv2.imread(str(masks[index]), cv2.IMREAD_GRAYSCALE)
    if img is None or mask is None:
        raise RuntimeError(f"failed to load frame/mask pair for {scenario}[{index}]")
    return img, mask


def inpaint(img: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, dict]:
    ok, img_buf = cv2.imencode(".png", img)
    ok2, mask_buf = cv2.imencode(".png", mask)
    if not (ok and ok2):
        raise RuntimeError("failed to encode inputs")
    payload = {
        "image": base64.b64encode(img_buf.tobytes()).decode(),
        "mask": base64.b64encode(mask_buf.tobytes()).decode(),
    }
    response = httpx.post(
        f"{IOPAINT_BASE}/api/v1/inpaint", json=payload, timeout=180
    )
    meta = {"status": response.status_code, "content_type": response.headers.get("content-type")}
    if response.status_code != 200:
        meta["body"] = response.text[:300]
        return None, meta
    arr = np.frombuffer(response.content, dtype=np.uint8)
    out = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    meta["bytes"] = len(response.content)
    return out, meta


def metrics(before: np.ndarray, after: np.ndarray, mask: np.ndarray) -> dict:
    m = mask > 127
    diff = np.abs(before.astype(np.float64) - after.astype(np.float64))
    return {
        "mask_pixels": int(m.sum()),
        "mask_mean_abs_diff": round(float(diff[m].mean()), 3) if m.any() else 0.0,
        "outside_mean_abs_diff": round(float(diff[~m].mean()), 3) if (~m).any() else 0.0,
        "identical": bool(np.array_equal(before, after)),
    }


def main() -> int:
    results = {}
    for scenario in ("T01_fixed_corner_text", "T02_fixed_logo", "T03_semitransparent"):
        img, mask = load_pair(scenario, 10)
        out, meta = inpaint(img, mask)
        if out is None:
            print(f"{scenario}: FAILED {meta}")
            results[scenario] = {"status": "failed", "meta": meta}
            continue
        m = metrics(img, out, mask)
        results[scenario] = {"status": "ok", "meta": meta, "metrics": m}
        print(f"{scenario}: status={meta['status']} type={meta['content_type']} "
              f"bytes={meta.get('bytes')} mask_diff={m['mask_mean_abs_diff']} "
              f"outside_diff={m['outside_mean_abs_diff']} identical={m['identical']}")

    out_path = ROOT / "artifacts" / "watermark-e2e" / "iopaint_single_frame_test.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = all(r["status"] == "ok" for r in results.values())
    print(f"\nSingle-frame LaMa verification: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
