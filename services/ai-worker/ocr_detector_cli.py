"""CLI worker for PaddleOCR watermark detection in short videos."""
from __future__ import annotations

import os

os.environ["OMP_NUM_THREADS"] = "4"
os.environ["MKL_NUM_THREADS"] = "4"

import argparse
import json
import sys
from pathlib import Path
import cv2
import numpy as np

PLATFORM_KEYWORDS = (
    "抖音", "快手", "小红书", "视频号", "微博", "bilibili",
    "douyin", "kuaishou", "tiktok", "搜索", "全屋定制",
    "豆包", "剪映", "ai生成", "生成", "水印",
    "号", "id", "q ", "○", "作者", "布丁"
)

# Unambiguous platform identifiers: single-sighting persistence is ONLY allowed
# for these strong keywords when placed near the frame border, never for short
# generic strings like "id" or "号" that could match scene text.
STRONG_PLATFORM_KEYWORDS = (
    "tiktok", "douyin", "kuaishou", "bilibili", "小红书",
    "抖音", "快手", "视频号", "微博", "豆包", "剪映"
)

def _levenshtein(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for c1 in s1:
        curr = [prev[0] + 1]
        for j, c2 in enumerate(s2):
            insert = prev[j + 1] + 1
            delete = curr[j] + 1
            sub = prev[j] + (0 if c1 == c2 else 1)
            curr.append(min(insert, delete, sub))
        prev = curr
    return prev[-1]


def run_detection(video_path: str, end_frame: int | None = None, sample_sec: float = 1.0, raw: bool = False, start_frame: int = 0) -> list[dict]:
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    max_f = end_frame if end_frame is not None and end_frame > 0 else total
    max_f = min(max_f, total)
    min_f = max(0, min(start_frame, max_f - 1))
    max_dur = max_f / fps

    interval = max(1, int(fps * sample_sec))
    sample_indices = list(range(min_f, max_f, interval))
    
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

        # OCR accuracy on watermark text holds at long-edge ~960, and PaddleOCR
        # cost scales with pixel count: downscaling large frames cuts the dense
        # scan (the dominant detection cost) roughly in half. Boxes are mapped
        # back to full-frame coordinates below via ocr_scale.
        ocr_scale = 1.0
        if max(frame.shape[:2]) > 640:
            ocr_scale = 640.0 / max(frame.shape[:2])
            frame = cv2.resize(
                frame, None, fx=ocr_scale, fy=ocr_scale, interpolation=cv2.INTER_AREA
            )

        def _collect(img, out_list):
            """Run one OCR pass and append surviving candidates to out_list."""
            res = ocr.predict(img)
            inv = 1.0 / ocr_scale
            for r in res:
                texts = r.get("rec_texts")
                scores = r.get("rec_scores")
                boxes = r.get("rec_boxes")
                if texts is None or boxes is None or scores is None:
                    continue
                for txt, sc, b in zip(texts, scores, boxes):
                    if sc < 0.5: continue
                    # Map boxes from the downscaled OCR frame back to full-frame
                    # pixels; all padding/safety math below runs in full-frame
                    # coordinates.
                    bx1 = int(b[0] * inv)
                    by1 = int(b[1] * inv)
                    bx2 = int(b[2] * inv)
                    by2 = int(b[3] * inv)
                    bw, bh = bx2 - bx1, by2 - by1
                    if bw <= 0 or bh <= 0: continue

                    # Center check: scene content in the center (actor, clothes, props) should be protected
                    # unless it matches platform keywords (e.g. Douyin/Kuaishou search card or promo ID)
                    txt_lower = txt.lower()
                    is_platform = any(k in txt_lower for k in PLATFORM_KEYWORDS) or any(
                        _levenshtein(txt_lower, target) <= 2
                        for target in ("bilibili", "tiktok", "kuaishou", "douyin")
                    )
                    if in_center_safety_zone(bx1, by1, bw, bh) and not is_platform:
                        continue
                    if not is_platform and sc < 0.85:
                        continue

                    # Logo icon is typically to the left of the platform text (e.g. Douyin 'd' note, Kuaishou camera).
                    # Expand left only when adjacent to the platform logo header; for normal text lines,
                    # use tight padding so dark frame borders (like sky) are preserved for inpainting context.
                    is_logo_header = any(k in txt_lower for k in ("抖音", "快手", "小红书", "bilibili", "douyin", "kuaishou", "豆包", "剪映", "视频号")) or len(txt) <= 3
                    if is_logo_header:
                        pad_left = min(int(bh * 1.45), int(width * 0.09))
                        pad_right = max(16, int(bh * 0.55))
                        pad_top = max(10, int(bh * 0.35))
                        pad_bot = max(10, int(bh * 0.35))
                    else:
                        pad_left = max(6, min(int(bh * 0.35), 12))
                        pad_right = max(8, min(int(bh * 0.35), 14))
                        pad_top = max(6, min(int(bh * 0.25), 8))
                        pad_bot = max(8, min(int(bh * 0.35), 14))

                    fx1 = max(0, bx1 - pad_left)
                    fy1 = max(0, by1 - pad_top)
                    fx2 = min(width, bx2 + pad_right)
                    fy2 = min(height, by2 + pad_bot)

                    # Companion avatar/icon expansion on left side (e.g. Xiaohongshu avatar at left of nickname)
                    if int(width * 0.03) <= bx1 <= int(width * 0.30):
                        fx1 = max(0, bx1 - int(bh * 4.0))
                        if fx1 <= int(width * 0.08):
                            fx1 = 0
                        fy1 = max(0, by1 - max(pad_top, int(bh * 0.50)))
                        fy2 = min(height, by2 + max(pad_bot, int(bh * 0.50)))
                    # Companion avatar/icon expansion on right side (e.g. Xiaohongshu avatar at right of nickname)
                    if int(width * 0.70) <= bx2 <= int(width * 0.97):
                        fx2 = min(width, bx2 + int(bh * 4.0))
                        if fx2 >= int(width * 0.92):
                            fx2 = width
                        fy1 = max(0, by1 - max(pad_top, int(bh * 0.50)))
                        fy2 = min(height, by2 + max(pad_bot, int(bh * 0.50)))

                    out_list.append({
                        "time_s": t_sec,
                        "box": (fx1, fy1, fx2 - fx1, fy2 - fy1),
                        "text": txt,
                        "score": float(sc)
                    })

        # First pass on the original frame. The CLAHE-enhanced second pass costs
        # one extra full OCR inference per frame; it is only worth running when
        # the original pass found nothing usable (dark cave / night-sky
        # backgrounds push semi-transparent text below the contrast floor).
        frame_hits = 0
        before = len(raw_candidates)
        _collect(frame, raw_candidates)
        frame_hits = len(raw_candidates) - before
        if frame_hits == 0:
            lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
            clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
            lab[..., 0] = clahe.apply(lab[..., 0])
            _collect(cv2.cvtColor(lab, cv2.COLOR_LAB2BGR), raw_candidates)
            frame_hits = len(raw_candidates) - before

        # Third pass: Blue-channel intensity thresholding in raw dense trajectory mode.
        # Catches semi-transparent white text fading into green/yellow foliage.
        if raw and frame_hits == 0:
            b_ch, _, _ = cv2.split(frame)
            _, b_thresh = cv2.threshold(b_ch, 100, 255, cv2.THRESH_BINARY)
            b_3ch = cv2.cvtColor(b_thresh, cv2.COLOR_GRAY2BGR)
            _collect(b_3ch, raw_candidates)
                
    cap.release()

    # Raw mode: return per-frame sightings without spatial clustering so the
    # caller can reconstruct a floating watermark's full travel path.
    if raw:
        return [
            {
                "time_s": round(float(c["time_s"]), 2),
                "box": [int(v) for v in c["box"]],
                "text": c["text"],
                "score": round(float(c["score"]), 3),
            }
            for c in raw_candidates
        ]

    def get_quadrant(bx, by, bw, bh):
        cx, cy = bx + bw / 2, by + bh / 2
        qx = 0 if cx < width * 0.4 else (1 if cx > width * 0.6 else -1)
        qy = 0 if cy < height * 0.35 else (1 if cy > height * 0.65 else -1)
        return (qx, qy) if qx != -1 and qy != -1 else None

    # Cluster candidates spatially
    clusters = []
    for cand in raw_candidates:
        t_sec = cand["time_s"]
        x, y, w, h = cand["box"]
        q_cand = get_quadrant(x, y, w, h)
        matched = False
        for c in clusters:
            cx, cy, cw, ch = c["box"]
            q_c = get_quadrant(cx, cy, cw, ch)
            same_quad = (q_cand is not None) and (q_cand == q_c)
            inter_x = max(0, min(x + w, cx + cw) - max(x, cx))
            inter_y = max(0, min(y + h, cy + ch) - max(y, cy))
            inter = inter_x * inter_y
            u = w * h + cw * ch - inter
            horiz_overlap = (inter_x / min(w, cw) > 0.3) if min(w, cw) > 0 else False
            vert_near = abs(y - cy) < height * 0.08
            
            if same_quad or (inter / u > 0.15) or (horiz_overlap and vert_near):
                c["member_boxes"].append((x, y, w, h))
                c["times"].append(t_sec)
                c["texts"].append(cand["text"])
                c["scores"].append(cand["score"])
                matched = True
                break
        if not matched:
            clusters.append({
                "box": (x, y, w, h),
                "member_boxes": [(x, y, w, h)],
                "times": [t_sec],
                "texts": [cand["text"]],
                "scores": [cand["score"]]
            })
            
    # Keep clusters with persistent presence and not spanning huge areas.
    # Single-sighting persistence is ONLY granted to strong, unambiguous
    # platform names with score>=0.9 (e.g. "tiktok", "douyin", "kuaishou") to
    # catch corner watermarks that happened to coincide with only one sampling frame.
    results = []
    for c in clusters:
        best_score = max(c["scores"]) if c["scores"] else 0.0
        best_text = max(c["texts"], key=len) if c["texts"] else ""
        is_strong_platform = any(k in best_text.lower() for k in STRONG_PLATFORM_KEYWORDS)
        persistent = len(c["times"]) >= 2 or (best_score >= 0.9 and is_strong_platform)
        if persistent:
            # Detect a moving/floating watermark: compute the centroid of member boxes
            # at each distinct sampling timestamp. If the watermark is truly travelling
            # across the frame over time, its centroid coordinates will shift significantly.
            # Multi-line stacked text in a static corner stays stationary at every timestamp.
            t_to_boxes = {}
            for t_val, b in zip(c["times"], c["member_boxes"]):
                t_to_boxes.setdefault(t_val, []).append(b)
            centroids_x = [
                sum(b[0] + b[2] / 2.0 for b in b_list) / len(b_list)
                for b_list in t_to_boxes.values()
            ]
            centroids_y = [
                sum(b[1] + b[3] / 2.0 for b in b_list) / len(b_list)
                for b_list in t_to_boxes.values()
            ]
            spread_x = float(np.max(centroids_x) - np.min(centroids_x)) if centroids_x else 0.0
            spread_y = float(np.max(centroids_y) - np.min(centroids_y)) if centroids_y else 0.0
            is_moving = spread_x > max(60.0, width * 0.06) or spread_y > max(60.0, height * 0.06)

            # A watermark block in a corner frequently contains multiple vertically
            # stacked text lines (e.g. logo line + ID line + search line). The
            # overall envelope must bound all lines in that corner cluster.
            m_xs = [b[0] for b in c["member_boxes"]]
            m_ys = [b[1] for b in c["member_boxes"]]
            m_x2s = [b[0] + b[2] for b in c["member_boxes"]]
            m_y2s = [b[1] + b[3] for b in c["member_boxes"]]
            bx = int(np.min(m_xs))
            by = int(np.min(m_ys))
            bx2 = int(np.max(m_x2s))
            by2 = int(np.max(m_y2s))

            # Subtle edge snap: if naturally close to edge (< 12px), snap cleanly
            # to prevent leftover avatar/pill border slivers that cause inpainting smudges
            if bx < 12:
                bx = 0
            if width - bx2 < 12:
                bx2 = width
            if by < 10:
                by = 0
            if height - by2 < 10:
                by2 = height

            bw = bx2 - bx
            bh = by2 - by

            # Max area sanity check: a watermark should not exceed 8% of the frame.
            # A genuinely moving watermark's full travel envelope is exempt: the
            # caller uses the template of the first sighting to track it densely,
            # the envelope is only a hint of where it travels.
            if not is_moving and (bw * bh) > (width * height * 0.08):
                continue
            start_s = max(0.0, min(c["times"]) - 0.2)
            end_s = min(max_dur, max(c["times"]) + 1.2)
            seed_idx = list(c["times"]).index(min(c["times"]))
            sb = c["member_boxes"][seed_idx]
            results.append({
                "box": [bx, by, bw, bh],
                "start_s": round(start_s, 2),
                "end_s": round(end_s, 2),
                "times": [round(float(t), 2) for t in sorted(set(c["times"]))],
                "confidence": round(float(np.mean(c["scores"])), 3),
                "texts": list(set(c["texts"]))[:3],
                "is_moving": is_moving,
                # First sighting (smallest timestamp) with its tight box: the
                # template used for dense tracking of a floating watermark.
                "seed_box": [int(sb[0]), int(sb[1]), int(sb[2]), int(sb[3])],
                "seed_time_s": round(float(min(c["times"])), 2),
            })

    return results
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--end-frame", type=int, default=None)
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--sample-sec", type=float, default=1.0)
    parser.add_argument("--raw", action="store_true",
                        help="Return per-frame sightings without spatial clustering")
    args = parser.parse_args()

    res = run_detection(args.video, args.end_frame, args.sample_sec, raw=args.raw,
                        start_frame=args.start_frame)
    print("OUTPUT_JSON:" + json.dumps(res, ensure_ascii=False))

if __name__ == "__main__":
    main()
