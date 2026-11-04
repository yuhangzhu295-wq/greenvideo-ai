"""CLI worker for PaddleOCR watermark detection in short videos.

Design (P0 refactor): PaddleOCR is a *prompt producer*. Every text it reads is
a candidate; classification into scene text vs watermark uses temporal
persistence and positional trajectory, never platform keyword matching.
Platform keywords are a confidence prior only. A single-sighting candidate
that fails both tests is reported on stderr (``UNCONFIRMED_JSON``) so the
caller can route it to a user-confirmation queue instead of silently
dropping it.
"""
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

# Platform identifiers act ONLY as a prior (confidence boost + single-sighting
# allowance). They never gate whether a text candidate is collected.
STRONG_PLATFORM_KEYWORDS = (
    "tiktok", "douyin", "kuaishou", "bilibili", "小红书",
    "抖音", "快手", "视频号", "微博", "豆包", "剪映",
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


def _same_text(a: str, b: str) -> bool:
    """Fuzzy same-text test: OCR misreads semi-transparent overlays.

    Exact/substring hits pass straight through; otherwise the Levenshtein
    budget scales with string length (len/4, min 1) so short strings stay
    strict while long misreads still merge.
    """
    a = (a or "").lower().strip()
    b = (b or "").lower().strip()
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    budget = max(1, min(len(a), len(b)) // 4)
    if abs(len(a) - len(b)) > budget:
        return False
    return _levenshtein(a, b) <= budget


def _is_platform_text(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in STRONG_PLATFORM_KEYWORDS)


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

    # Central safety zone: [0.25W..0.75W, 0.20H..0.80H]. Center content is the
    # camera's subject (actors, props, titles), so auto-removal there carries a
    # far higher cost than a miss; candidates inside need a platform prior or a
    # higher score to qualify.
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

                    txt_lower = txt.lower()
                    platform = _is_platform_text(txt_lower)
                    # Center check: scene content in the center (actor, clothes,
                    # props) is protected. A platform prior or a high score is
                    # required to touch it; the tight OCR box is preserved for
                    # the caller either way.
                    if in_center_safety_zone(bx1, by1, bw, bh) and not platform and sc < 0.92:
                        continue

                    # Tight box = what OCR actually read (used as the SAM prompt
                    # and for trajectory geometry). Padded box = search context.
                    tight = (bx1, by1, bw, bh)

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
                        "tight_box": tight,
                        "text": txt,
                        "score": float(sc),
                        "platform": platform,
                    })

        # First pass on the original frame. The CLAHE-enhanced second pass costs
        # one extra full OCR inference per frame; it is only worth running when
        # the original pass found nothing usable (dark cave / night-sky
        # backgrounds push semi-transparent text below the contrast floor).
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
                "tight_box": [int(v) for v in c["tight_box"]],
                "text": c["text"],
                "score": round(float(c["score"]), 3),
            }
            for c in raw_candidates
        ]

    unconfirmed = _cluster_and_classify(
        raw_candidates, width, height, max_dur
    )
    if unconfirmed:
        # Never silently drop a candidate: report it so the caller can ask the
        # user to confirm the region (goal: auto-detect with human fallback).
        print(
            "UNCONFIRMED_JSON:" + json.dumps(unconfirmed, ensure_ascii=False),
            file=sys.stderr,
        )
    return unconfirmed["confirmed"]


def _cluster_and_classify(
    candidates: list[dict], width: int, height: int, max_dur: float
) -> dict:
    """Group OCR sightings into text entities, then classify each entity.

    Entity = one watermark instance. Sightings merge by *text content* across
    positions (a corner-hopping watermark reads the same string in four
    corners), then split spatially into tight sub-clusters so a hop never
    interpolates a trajectory through the screen center.

    Returns {"confirmed": [...], "unconfirmed": [...]}.
    """
    # 1. Text-entity merge.
    entities: list[dict] = []
    for cand in candidates:
        matched = False
        for ent in entities:
            if _same_text(cand["text"], ent["representative_text"]):
                ent["sightings"].append(cand)
                matched = True
                break
        if not matched:
            entities.append({
                "representative_text": cand["text"],
                "sightings": [cand],
            })

    # 2. Per-entity dedup: the CLAHE retry pass can emit a near-identical box
    # for the same frame; persistence must count distinct time windows only.
    confirmed: list[dict] = []
    unconfirmed: list[dict] = []
    for ent in entities:
        sightings = sorted(ent["sightings"], key=lambda c: c["time_s"])
        deduped: list[dict] = []
        for c in sightings:
            if deduped and abs(c["time_s"] - deduped[-1]["time_s"]) < 0.5:
                continue
            deduped.append(c)
        sightings = deduped
        if not sightings:
            continue

        best_score = max(c["score"] for c in sightings)
        best_text = max((c["text"] for c in sightings), key=len)
        platform = any(c["platform"] for c in sightings)
        persistent = len(sightings) >= 2

        if not persistent and not platform:
            # Single sighting, no prior: never auto-removal, never silent drop.
            c = sightings[0]
            tb = c["tight_box"]
            unconfirmed.append({
                "box": [int(v) for v in c["box"]],
                "tight_box": [int(v) for v in tb],
                "text": c["text"],
                "score": round(float(c["score"]), 3),
                "time_s": round(float(c["time_s"]), 2),
                "reason": "single_sighting",
            })
            continue

        # 3. Spatial sub-cluster split: consecutive sightings that jump far in
        # a short time are distinct resting positions (corner hop), not one
        # trajectory. A sub-cluster is a run of sightings that stay within a
        # small spread; hops between sub-clusters split the entity.
        sub_clusters: list[list[dict]] = [[sightings[0]]]
        for c in sightings[1:]:
            prev_list = sub_clusters[-1]
            spread = _cluster_spread(prev_list + [c])
            if spread > 60.0:
                sub_clusters.append([c])
            else:
                prev_list.append(c)

        multi_sub = len(sub_clusters) > 1
        if multi_sub and platform:
            # A drifting platform watermark with sparse sightings still moves;
            # keep the entity whole and let the dense tracker reconstruct it.
            is_moving = _entity_is_moving(sub_clusters, width, height)
        else:
            is_moving = False

        if multi_sub and not is_moving:
            # One static detection per resting position, time windows split at
            # the midpoint between neighbouring sub-clusters. No interpolation
            # across a hop.
            for s_idx, sc_list in enumerate(sub_clusters):
                prev_end = (
                    sub_clusters[s_idx - 1][-1]["time_s"]
                    if s_idx > 0
                    else None
                )
                next_start = (
                    sub_clusters[s_idx + 1][0]["time_s"]
                    if s_idx < len(sub_clusters) - 1
                    else None
                )
                start_s = (
                    0.0 if prev_end is None else round((prev_end + sc_list[0]["time_s"]) / 2.0, 2)
                )
                end_s = (
                    max_dur if next_start is None else round((sc_list[-1]["time_s"] + next_start) / 2.0, 2)
                )
                det = _entity_result(
                    sc_list, start_s, end_s, is_moving=False,
                    width=width, height=height, max_dur=max_dur,
                    best_score=best_score, best_text=best_text,
                    platform=platform, persistent=persistent,
                )
                if det is not None:
                    confirmed.append(det)
        else:
            # Single resting position, or a moving entity handed to the dense
            # tracker (seed = first sighting).
            start_s = max(0.0, sightings[0]["time_s"] - 0.2)
            end_s = min(max_dur, sightings[-1]["time_s"] + 1.2)
            det = _entity_result(
                sightings, start_s, end_s, is_moving=is_moving,
                width=width, height=height, max_dur=max_dur,
                best_score=best_score, best_text=best_text,
                platform=platform, persistent=persistent,
            )
            if det is not None:
                confirmed.append(det)

    return {"confirmed": confirmed, "unconfirmed": unconfirmed}


def _cluster_spread(items: list[dict]) -> float:
    xs = [c["tight_box"][0] + c["tight_box"][2] / 2.0 for c in items]
    ys = [c["tight_box"][1] + c["tight_box"][3] / 2.0 for c in items]
    return float(max(max(xs) - min(xs), max(ys) - min(ys))) if xs else 0.0


def _entity_is_moving(sub_clusters: list[list[dict]], width: int, height: int) -> bool:
    """A drifting watermark's centroid shifts between sub-clusters."""
    cxs: list[float] = []
    cys: list[float] = []
    for sc_list in sub_clusters:
        cxs.append(
            sum(c["tight_box"][0] + c["tight_box"][2] / 2.0 for c in sc_list) / len(sc_list)
        )
        cys.append(
            sum(c["tight_box"][1] + c["tight_box"][3] / 2.0 for c in sc_list) / len(sc_list)
        )
    spread_x = max(cxs) - min(cxs) if cxs else 0.0
    spread_y = max(cys) - min(cys) if cys else 0.0
    return spread_x > max(60.0, width * 0.06) or spread_y > max(60.0, height * 0.06)


def _entity_result(
    sc_list: list[dict],
    start_s: float,
    end_s: float,
    is_moving: bool,
    width: int,
    height: int,
    max_dur: float,
    best_score: float,
    best_text: str,
    platform: bool,
    persistent: bool,
) -> dict:
    """Build one confirmed detection dict from a group of sightings."""
    # Tight envelope over the group's tight boxes: the multi-line watermark
    # case bounds all stacked lines in one corner cluster.
    xs = [c["tight_box"][0] for c in sc_list]
    ys = [c["tight_box"][1] for c in sc_list]
    x2s = [c["tight_box"][0] + c["tight_box"][2] for c in sc_list]
    y2s = [c["tight_box"][1] + c["tight_box"][3] for c in sc_list]
    bx, by = int(min(xs)), int(min(ys))
    bx2, by2 = int(max(x2s)), int(max(y2s))

    if bx < 12:
        bx = 0
    if width - bx2 < 12:
        bx2 = width
    if by < 10:
        by = 0
    if height - by2 < 10:
        by2 = height
    bw, bh = bx2 - bx, by2 - by

    if not is_moving and (bw * bh) > (width * height * 0.08):
        # Oversized static envelope: scene structure, not a watermark plate.
        return None

    # Mild context padding for the search window; the SAM prompt layer uses
    # tight_box when it needs a precise prompt.
    pad_x = max(6, int(bh * 0.35))
    pad_y = max(6, int(bh * 0.3))
    px1 = max(0, bx - pad_x)
    py1 = max(0, by - pad_y)
    px2 = min(width, bx2 + pad_x)
    py2 = min(height, by2 + pad_y)

    seed = min(sc_list, key=lambda c: c["time_s"])
    stb = seed["tight_box"]
    return {
        "box": [px1, py1, px2 - px1, py2 - py1],
        "tight_box": [bx, by, bw, bh],
        "start_s": round(start_s, 2),
        "end_s": round(end_s, 2),
        "times": [round(float(t), 2) for t in sorted({c["time_s"] for c in sc_list})],
        "confidence": round(
            float(best_score) + (0.1 if (platform and persistent) else 0.0), 3
        ),
        "texts": list({c["text"] for c in sc_list})[:3],
        "is_moving": is_moving,
        "seed_box": [int(v) for v in stb],
        "seed_time_s": round(float(seed["time_s"]), 2),
        "platform_prior": platform,
    }


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
