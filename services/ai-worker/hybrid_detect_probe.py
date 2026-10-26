"""Hybrid detection probe: cheap CV pre-filter, OCR only on candidate crops.

Measures accuracy against Ground Truth and wall-clock time, to decide whether
OCR-assisted auto detection is viable on CPU.
"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
from paddleocr import PaddleOCR

GT = Path(__file__).resolve().parent.parent.parent / "artifacts" / "watermark-e2e"


def gt_boxes(m):
    m = (m > 127).astype(np.uint8) * 255
    n, _l, st, _c = cv2.connectedComponentsWithStats(m, 8)
    return [
        (int(st[i][0]), int(st[i][1]), int(st[i][2]), int(st[i][3]))
        for i in range(1, n)
        if int(st[i][4]) >= 200
    ]


def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    u = aw * ah + bw * bh - inter
    return inter / u if u else 0.0


def cv_candidates(frame, min_area=400, max_area_ratio=0.15):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)
    gf = gray.astype(np.float32)
    mean = cv2.blur(gf, (21, 21))
    sq = cv2.blur(gf * gf, (21, 21))
    contrast = np.sqrt(np.maximum(sq - mean * mean, 0.0))
    resp = np.clip(grad / 40.0, 0, 4.0) * 0.65 + np.clip(contrast / 45.0, 0, 4.0) * 0.35
    bg = float(np.percentile(resp, 60))
    thr = max(bg * 2.2, float(np.percentile(resp, 90)))
    b = (resp > thr).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    b = cv2.morphologyEx(b, cv2.MORPH_CLOSE, k, iterations=3)
    b = cv2.dilate(b, k, iterations=2)
    h, w = gray.shape
    maxa = int(h * w * max_area_ratio)
    n, _l, st, _c = cv2.connectedComponentsWithStats(b, 8)
    out = []
    for i in range(1, n):
        x, y, bw, bh, a = (int(v) for v in st[i])
        if a < min_area or a > maxa:
            continue
        out.append((x, y, bw, bh))
    return out


def polys_of(result):
    r0 = result[0] if isinstance(result, list) else result
    if not isinstance(r0, dict):
        return None
    for key in ("dt_polys", "dt_boxes"):
        if r0.get(key) is not None:
            return r0[key]
    return None


def main() -> int:
    ocr = PaddleOCR(lang="en", device="cpu", text_det_limit_side_len=736)
    # Warm up on a frame extracted straight from the input video, because
    # before_frames/ only exists after an E2E run.
    warm = GT / "T01_fixed_corner_text" / "input.mp4"
    cap = cv2.VideoCapture(str(warm))
    ok, frame = cap.read()
    cap.release()
    if ok:
        ocr.predict(frame)

    for sc in ("T01_fixed_corner_text", "T03_semitransparent", "T05_multiple",
               "T06_complex_motion"):
        masks = sorted((GT / sc / "mask").glob("mask_*.png"))
        cap = cv2.VideoCapture(str(GT / sc / "input.mp4"))
        frames = []
        i = 0
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            if i % 8 == 0 and len(frames) < 5:
                frames.append((i, fr))
            i += 1
        cap.release()
        t0 = time.time()
        ocr_calls = 0
        found: list[tuple[int, int, int, int]] = []
        for idx, img in frames:
            merged: list[tuple[int, int, int, int]] = []
            for c in cv_candidates(img):
                if any(iou(c, m) > 0.2 for m in merged):
                    continue
                merged.append(c)
            for (x, y, w, h) in merged[:6]:
                pad = 12
                x1, y1 = max(0, x - pad), max(0, y - pad)
                x2 = min(img.shape[1], x + w + pad)
                y2 = min(img.shape[0], y + h + pad)
                crop = img[y1:y2, x1:x2]
                if crop.size == 0 or crop.shape[0] < 48 or crop.shape[1] < 48:
                    continue
                ocr_calls += 1
                polys = polys_of(ocr.predict(crop))
                if polys is None:
                    continue
                for poly in polys:
                    xs = [float(p[0]) for p in poly]
                    ys = [float(p[1]) for p in poly]
                    found.append(
                        (int(min(xs)) + x1, int(min(ys)) + y1,
                         int(max(xs) - min(xs)), int(max(ys) - min(ys)))
                    )
        dt = time.time() - t0

        clusters = []
        for b in found:
            placed = False
            for c in clusters:
                if iou(b, c["box"]) > 0.3:
                    c["members"].append(b)
                    n = len(c["members"])
                    c["box"] = tuple(
                        int(sum(m[i] for m in c["members"]) / n) for i in range(4)
                    )
                    placed = True
                    break
            if not placed:
                clusters.append({"box": b, "members": [b]})
        need = max(2, len(frames) // 2)
        clusters = [c for c in clusters if len(c["members"]) >= need]
        gt = gt_boxes(cv2.imread(str(masks[0]), cv2.IMREAD_GRAYSCALE))
        print(f"--- {sc}: {dt:.1f}s, {ocr_calls} OCR crop calls, "
              f"{len(clusters)} clusters (>={need} frames)")
        for c in clusters:
            iv = max((iou(c["box"], g) for g in gt), default=0.0)
            print(f"    box={c['box']} seen={len(c['members'])}/{len(frames)} "
                  f"iou_to_gt={iv:.3f}")
        if gt and clusters:
            best = max(max((iou(c["box"], g) for g in gt), default=0.0)
                       for c in clusters)
            print(f"    => best IoU {best:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
