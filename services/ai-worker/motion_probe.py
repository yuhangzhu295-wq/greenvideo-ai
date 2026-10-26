"""Probe: does the "static overlay over moving background" prior localise watermarks?

Builds a temporal median of the video, then scores pixels by staticness (low
deviation from the median) combined with the structural energy baked into the
median image. Reports box-level IoU against the Ground-Truth masks.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

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


def boxes_from(score: np.ndarray, pct: float):
    thr = np.percentile(score, pct)
    b = (score > thr).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    b = cv2.morphologyEx(b, cv2.MORPH_CLOSE, k, iterations=3)
    b = cv2.dilate(b, k, iterations=2)
    h, w = score.shape
    cnt, _l, st, _c = cv2.connectedComponentsWithStats(b, 8)
    out = []
    for i in range(1, cnt):
        x, y, bw, bh, a = (int(v) for v in st[i])
        if a < 400 or a > h * w * 0.2:
            continue
        out.append((x, y, bw, bh))
    return out


def main() -> int:
    for sc in sorted(p.name for p in GT.iterdir()
                     if p.is_dir() and p.name.startswith("T0")):
        cap = cv2.VideoCapture(str(GT / sc / "input.mp4"))
        frames = []
        while True:
            ok, f = cap.read()
            if not ok:
                break
            frames.append(f)
        cap.release()
        n = len(frames)
        if n < 4:
            print(f"{sc}: too few frames")
            continue
        idxs = list(range(0, n, max(1, n // 20)))[:20]
        sample = [frames[i] for i in idxs]

        stack = np.stack(sample, axis=0)
        med = np.median(stack, axis=0).astype(np.uint8)
        devs = [np.abs(f.astype(np.float32) - med.astype(np.float32)).mean(axis=2)
                for f in sample]
        maxdev = np.max(np.stack(devs, axis=0), axis=0)
        staticness = np.clip(1.0 - maxdev / 40.0, 0, 1)

        gray = cv2.cvtColor(med, cv2.COLOR_BGR2GRAY)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        grad = np.sqrt(gx * gx + gy * gy)
        gf = gray.astype(np.float32)
        mean = cv2.blur(gf, (21, 21))
        sq = cv2.blur(gf * gf, (21, 21))
        contrast = np.sqrt(np.maximum(sq - mean * mean, 0.0))

        score = (staticness * 0.5
                 + np.clip(grad / 60.0, 0, 1) * 0.3
                 + np.clip(contrast / 50.0, 0, 1) * 0.2)

        gt = gt_boxes(cv2.imread(
            str(sorted((GT / sc / "mask").glob("mask_*.png"))[0]),
            cv2.IMREAD_GRAYSCALE))
        if not gt:
            print(f"{sc}: no gt")
            continue
        best = (0.0, None)
        for pct in (85, 90, 93, 95, 97):
            boxes = boxes_from(score, pct)
            if not boxes:
                continue
            mi = max(max((iou(pb, g) for g in gt), default=0.0) for pb in boxes)
            if mi > best[0]:
                best = (mi, (pct, boxes[:4]))
        print(f"{sc}: best_box_iou={best[0]:.3f} "
              f"pct={best[1][0] if best[1] else None}")
        if best[1]:
            print(f"    pred={best[1][1]}")
        print(f"    gt  ={gt[:3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
