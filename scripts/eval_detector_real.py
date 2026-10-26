"""Evaluate the detector on REAL footage Ground Truth."""
from __future__ import annotations
import json, sys
from pathlib import Path
import cv2, numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "api"))
GT = ROOT / "artifacts" / "real-footage-gt"
from app.services.watermark_detect import detect_watermarks  # noqa: E402


def gt_boxes(m):
    m = (m > 127).astype(np.uint8) * 255
    n, _l, st, _c = cv2.connectedComponentsWithStats(m, 8)
    return [(int(st[i][0]), int(st[i][1]), int(st[i][2]), int(st[i][3]))
            for i in range(1, n) if int(st[i][4]) >= 200]


def iou(a, b):
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax+aw, bx+bw), min(ay+ah, by+bh)
    inter = max(0, x2-x1) * max(0, y2-y1)
    u = aw*ah + bw*bh - inter
    return inter/u if u else 0.0


def read_frames(p):
    c = cv2.VideoCapture(str(p)); o = []
    while True:
        ok, f = c.read()
        if not ok: break
        o.append(f)
    c.release(); return o


def main() -> int:
    report = {}
    for sc in sorted(p.name for p in GT.iterdir() if p.is_dir()):
        frames = read_frames(GT/sc/"input.mp4")
        masks = sorted((GT/sc/"mask").glob("mask_*.png"))
        if not frames or not masks:
            print(f"{sc}: missing frames/masks"); continue
        gt = gt_boxes(cv2.imread(str(masks[0]), cv2.IMREAD_GRAYSCALE))
        res = detect_watermarks(frames)
        pred = [d.box for d in res.detections]
        per_gt = [max((iou(p, g) for p in pred), default=0.0) for g in gt]
        mean_iou = float(np.mean(per_gt)) if per_gt else 0.0
        pm = np.zeros((frames[0].shape[0], frames[0].shape[1]), bool)
        for (x,y,w,h) in pred: pm[y:y+h, x:x+w] = True
        gm = np.zeros_like(pm)
        for (x,y,w,h) in gt: gm[y:y+h, x:x+w] = True
        spill = 100*float((pm & ~gm).sum())/max(1,float((~gm).sum()))
        report[sc] = {"detected": res.ok, "mean_box_iou": round(mean_iou,3),
                      "spill_pct": round(spill,3), "pred": pred, "gt": gt,
                      "stats": res.stats}
        print(f"{sc}: detected={res.ok} box_iou={mean_iou:.3f} spill={spill:.2f}% boxes={len(pred)}")
        print(f"    pred={pred}")
        print(f"    gt  ={gt}")
    (GT/"detector_accuracy.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
