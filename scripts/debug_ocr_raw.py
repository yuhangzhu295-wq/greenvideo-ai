"""Debug: show raw PaddleOCR predictions on GT frames (no keyword filtering).

Run: services/ai-worker/.venv/Scripts/python.exe scripts/debug_ocr_raw.py <video> <frame> [--clahe]
"""
from __future__ import annotations

import sys

import cv2
import numpy as np
from paddleocr import PaddleOCR

video = sys.argv[1]
f_idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0
use_clahe = "--clahe" in sys.argv

cap = cv2.VideoCapture(video)
cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
ret, frame = cap.read()
cap.release()
assert ret, f"cannot read frame {f_idx}"

ocr_scale = 1.0
if max(frame.shape[:2]) > 640:
    ocr_scale = 640.0 / max(frame.shape[:2])
    frame = cv2.resize(frame, None, fx=ocr_scale, fy=ocr_scale, interpolation=cv2.INTER_AREA)

if use_clahe:
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    lab[..., 0] = clahe.apply(lab[..., 0])
    frame = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

ocr = PaddleOCR(
    lang="ch",
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
)
res = ocr.predict(frame)
total = 0
for r in res:
    texts = list(r.get("rec_texts") or [])
    scores = list(r.get("rec_scores") or [])
    raw_boxes = r.get("rec_boxes")
    boxes = np.asarray(raw_boxes) if raw_boxes is not None else np.zeros((0, 4))
    print(f"frame={f_idx} scale={ocr_scale:.3f} detections={len(texts)}")
    for txt, sc, b in zip(texts, scores, boxes, strict=False):
        inv = 1.0 / ocr_scale
        print(
            f"  text={txt!r} score={float(sc):.3f} "
            f"box_full=({int(b[0]*inv)},{int(b[1]*inv)},{int((b[2]-b[0])*inv)},{int((b[3]-b[1])*inv)})"
        )
    total += len(texts)
if total == 0:
    print("NO TEXT DETECTED AT ALL")
