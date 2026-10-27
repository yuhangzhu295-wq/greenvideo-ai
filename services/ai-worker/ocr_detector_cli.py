"""CLI worker for PaddleOCR watermark detection in short videos."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
import cv2
import numpy as np

PLATFORM_KEYWORDS = (
    "抖音", "快手", "小红书", "视频号", "微博", "bilibili",
    "douyin", "kuaishou", "tiktok", "搜索", "全屋定制",
    "号", "id", "q ", "○"
)

def run_detection(video_path: str, end_frame: int | None = None, sample_sec: float = 1.0) -> list[dict]:
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    max_f = end_frame if end_frame is not None and end_frame > 0 else total
    max_f = min(max_f, total)
    max_dur = max_f / fps
    
    interval = max(1, int(fps * sample_sec))
    sample_indices = list(range(0, max_f, interval))
    
    # Central safety zone: [0.25W..0.75W, 0.20H..0.80H]
    cz_x1, cz_x2 = int(width * 0.25), int(width * 0.75)
    cz_y1, cz_y2 = int(height * 0.20), int(height * 0.80)
    
    def in_center_safety_zone(bx, by, bw, bh):
        cx, cy = bx + bw / 2, by + bh / 2
        return (cz_x1 <= cx <= cz_x2) and (cz_y1 <= cy <= cz_y2)
    
    from paddleocr import PaddleOCR
    # Explicitly disable UVDoc perspective unwarping and doc orientation:
    # in natural video frames, UVDoc distorts coordinates and causes severe
    # box displacement (e.g. cutting off lines of text in the watermark).
    ocr = PaddleOCR(
        lang="ch",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    
    raw_candidates = []
    
    for f_idx in sample_indices:
        t_sec = f_idx / fps
        cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
        ret, frame = cap.read()
        if not ret: break
        
        res = ocr.predict(frame)
        for r in res:
            texts = r.get("rec_texts")
            scores = r.get("rec_scores")
            boxes = r.get("rec_boxes")
            if texts is None or boxes is None or scores is None:
                continue
            for txt, sc, b in zip(texts, scores, boxes):
                if sc < 0.5: continue
                bx1, by1, bx2, by2 = int(b[0]), int(b[1]), int(b[2]), int(b[3])
                bw, bh = bx2 - bx1, by2 - by1
                if bw <= 0 or bh <= 0: continue
                
                # Check center safety zone
                if in_center_safety_zone(bx1, by1, bw, bh):
                    continue
                    
                # Keyword check: short random noise words in the background shouldn't trigger
                # unless they look like platform watermarks
                txt_lower = txt.lower()
                is_platform = any(k in txt_lower for k in PLATFORM_KEYWORDS) or len(txt) >= 4
                if not is_platform and sc < 0.85:
                    continue
                
                # Logo icon is typically to the left of the platform text (e.g. Douyin 'd' note).
                # Expand left only when adjacent to the platform logo header; for normal text lines,
                # use tight padding so dark frame borders (like sky) are preserved for inpainting context.
                is_logo_header = any(k in txt_lower for k in ("抖音", "快手", "小红书", "bilibili")) or len(txt) <= 3
                if is_logo_header:
                    pad_left = max(int(bh * 1.35), int(bw * 0.25))
                else:
                    pad_left = max(6, int(bh * 0.35))
                pad_top = max(6, int(bh * 0.25))
                pad_right = max(8, int(bh * 0.35))
                pad_bot = max(8, int(bh * 0.35))
                
                fx1 = max(0, bx1 - pad_left)
                fy1 = max(0, by1 - pad_top)
                fx2 = min(width, bx2 + pad_right)
                fy2 = min(height, by2 + pad_bot)
                
                raw_candidates.append({
                    "time_s": t_sec,
                    "box": (fx1, fy1, fx2 - fx1, fy2 - fy1),
                    "text": txt,
                    "score": float(sc)
                })
                
    cap.release()
    
    # Cluster candidates spatially
    clusters = []
    for cand in raw_candidates:
        t_sec = cand["time_s"]
        x, y, w, h = cand["box"]
        matched = False
        for c in clusters:
            cx, cy, cw, ch = c["box"]
            inter_x = max(0, min(x + w, cx + cw) - max(x, cx))
            inter_y = max(0, min(y + h, cy + ch) - max(y, cy))
            inter = inter_x * inter_y
            u = w * h + cw * ch - inter
            if (inter / u > 0.2) or (abs(x - cx) < width * 0.12 and abs(y - cy) < height * 0.12):
                c["box"] = (min(x, cx), min(y, cy), max(x + w, cx + cw) - min(x, cx), max(y + h, cy + ch) - min(y, cy))
                c["times"].append(t_sec)
                c["texts"].append(cand["text"])
                c["scores"].append(cand["score"])
                matched = True
                break
        if not matched:
            clusters.append({
                "box": (x, y, w, h),
                "times": [t_sec],
                "texts": [cand["text"]],
                "scores": [cand["score"]]
            })
            
    # Keep clusters with persistent presence (seen in at least 2 sampled frames)
    # and not spanning huge areas
    results = []
    for c in clusters:
        if len(c["times"]) >= 2:
            bx, by, bw, bh = c["box"]
            # Max area sanity check: a watermark should not exceed 8% of the frame
            if (bw * bh) > (width * height * 0.08):
                continue
            start_s = max(0.0, min(c["times"]) - 0.2)
            end_s = min(max_dur, max(c["times"]) + 1.2)
            results.append({
                "box": [bx, by, bw, bh],
                "start_s": round(start_s, 2),
                "end_s": round(end_s, 2),
                "confidence": round(float(np.mean(c["scores"])), 3),
                "texts": list(set(c["texts"]))[:3]
            })
            
    return results

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--end-frame", type=int, default=None)
    parser.add_argument("--sample-sec", type=float, default=1.0)
    args = parser.parse_args()
    
    res = run_detection(args.video, args.end_frame, args.sample_sec)
    print("OUTPUT_JSON:" + json.dumps(res, ensure_ascii=False))

if __name__ == "__main__":
    main()
