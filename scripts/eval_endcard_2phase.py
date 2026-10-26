"""End-to-end two-phase (end-card) watermark repair using the ACTUAL frontend parameters.

Verifies the two fixes the review agent flagged in round 8:

  P1-C  the end-card box reaches the frame bottom edge (y = H), so the strip
        at y=933..949 that the old `h = H*0.75` box left behind is covered;
  P0-2  the split point is an explicit user confirmation (1.5s here), not an
        estimate derived from video duration.

Both the watermarked input and its ground-truth mask are composited in Python
from the SAME coordinate list, so there is no possibility of misalignment, and
the clean reference is generated with the same code path. The repair then runs
through the real backend `WatermarkProcessor.process_video` with real LaMa.

Usage:  python scripts/eval_endcard_2phase.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "services" / "api"
GT = ROOT / "artifacts" / "ground-truth" / "e2e_2phase"
WIDTH, HEIGHT, FPS, DURATION = 720, 960, 10, 3
TOTAL = FPS * DURATION
SPLIT_S = 1.5

# Exact regions emitted by apps/miniapp/src/pages/watermark/index.tsx
# buildTimedRegions("douyin_both_corners", {width:720,height:960}, 1.5)
# (verified with node against the built bundle in the previous round)
REGIONS = [
    {"x": 0, "y": 0, "w": 302, "h": 192, "start_s": 0, "end_s": SPLIT_S},
    {"x": 396, "y": 730, "w": 324, "h": 230, "start_s": 0, "end_s": SPLIT_S},
    {"x": 137, "y": 202, "w": 446, "h": 758, "start_s": SPLIT_S},
]

# The strip the review agent proved was left unrepaired by the old box:
# y = 933..949 sits inside the end-card watermark but below the old y=922 edge.
PROBE_STRIP = (y for y in range(933, 950))


def clean_frame(i: int) -> np.ndarray:
    """Smooth background with real texture in every region the watermark covers.

    The gradient is deliberately kept below saturation: a region that is already
    pure white makes a white watermark invisible, so "repair" has nothing to
    restore and the measurement says nothing.
    """
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    for c in range(3):
        frame[:, :, c] = np.clip(
            xx * 0.12 + yy * 0.07 + i * 4 + c * 30 + 30, 0, 200
        ).astype(np.uint8)
    cx = int(WIDTH * 0.5 + 180 * np.sin(i * 0.12))
    cy = int(HEIGHT * 0.5 + 90 * np.cos(i * 0.12))
    cv2.circle(frame, (cx, cy), 70, (200, 200, 200), -1, cv2.LINE_AA)
    cv2.circle(frame, (cx, cy), 34, (60, 90, 150), -1, cv2.LINE_AA)
    cx2 = int(WIDTH * 0.3 + 150 * np.cos(i * 0.09))
    cy2 = int(HEIGHT * 0.6 + 80 * np.sin(i * 0.09))
    cv2.circle(frame, (cx2, cy2), 50, (30, 60, 120), -1, cv2.LINE_AA)
    cv2.circle(frame, (cx2, cy2), 22, (210, 180, 120), -1, cv2.LINE_AA)
    # A bottom-right blob so the BR watermark sits on structured content.
    cv2.circle(frame, (WIDTH - 120, HEIGHT - 150), 110, (90, 130, 190), -1, cv2.LINE_AA)
    return frame


def active_regions(t: float) -> list[dict]:
    return [r for r in REGIONS if r["start_s"] - 1e-6 <= t <= r.get("end_s", 1e9)]


def encode(frames_dir: Path, out: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(FPS), "-i", str(frames_dir / "f_%06d.png"),
         "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={DURATION}",
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-crf", "16", "-c:a", "aac", "-shortest", str(out)],
        capture_output=True, text=True, check=True, timeout=600,
    )


def build_inputs() -> tuple[Path, Path]:
    """Composite watermark AND mask from the same coordinate list."""
    in_dir = GT / "_in_frames"
    mask_dir = GT / "mask"
    in_dir.mkdir(parents=True, exist_ok=True)
    mask_dir.mkdir(parents=True, exist_ok=True)
    for i in range(TOTAL):
        t = (i + 0.5) / FPS
        frame = clean_frame(i).copy()
        mask = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
        for r in active_regions(t):
            x, y, w, h = r["x"], r["y"], r["w"], r["h"]
            # Opaque white plate + text, so the watermark is unambiguous.
            cv2.rectangle(frame, (x, y), (x + w, y + h), (250, 250, 250), -1)
            cv2.putText(frame, "WM", (x + 12, y + h // 2), cv2.FONT_HERSHEY_SIMPLEX,
                        min(w, h) / 90.0, (10, 10, 10), max(2, int(min(w, h) / 60)), cv2.LINE_AA)
            mask[y:y + h, x:x + w] = 255
        cv2.imwrite(str(in_dir / f"f_{i:06d}.png"), frame)
        cv2.imwrite(str(mask_dir / f"f_{i:06d}.png"), mask)

    watermarked = GT / "input.mp4"
    encode(in_dir, watermarked)

    clean = GT / "clean.mp4"
    if not clean.exists():
        clean_dir = GT / "_clean_frames"
        clean_dir.mkdir(parents=True, exist_ok=True)
        for i in range(TOTAL):
            cv2.imwrite(str(clean_dir / f"f_{i:06d}.png"), clean_frame(i))
        encode(clean_dir, clean)
    return watermarked, mask_dir


def frame_count(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True, timeout=120,
    )
    return int(out.stdout.strip())


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    mse = float(((a - b) ** 2).mean())
    return 99.0 if mse == 0 else float(10 * np.log10(255.0 ** 2 / mse))


def main() -> int:
    watermarked, mask_dir = build_inputs()

    from app.schemas import TimedMaskRegion
    from app.services.watermark import WatermarkProcessor

    regions = [TimedMaskRegion.model_validate(r) for r in REGIONS]
    out_path = API / "storage" / "test_output" / "e2e_2phase_out.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    proc = WatermarkProcessor()
    result = proc.process_video(
        input_path=str(watermarked),
        mask_regions=[],
        output_path=str(out_path),
        repair_mode="ai",
        mask_preset="douyin_both_corners",
        remove_subtitle=False,
        timed_mask_regions=regions,
    )
    out = Path(result)
    rep = proc.last_repair_report

    print(json.dumps({
        "engine": proc.last_engine,
        "engine_note": proc.last_engine_note,
        "degraded": proc.last_degraded,
        "frames_total": rep.frames_total,
        "frames_repaired": rep.frames_repaired,
        "frames_skipped": rep.frames_skipped,
        "mask_coverage_pct": rep.mask_coverage_pct,
        "audio_preserved": rep.audio_preserved,
        "output_fps": rep.output_fps,
        "output_frames": rep.output_frames,
        "output_size": out.stat().st_size,
    }, ensure_ascii=False, indent=2))
    for n in rep.notes:
        print(f"  note: {n}")

    n = frame_count(out)
    c_clean = cv2.VideoCapture(str(GT / "clean.mp4"))
    c_out = cv2.VideoCapture(str(out))
    c_in = cv2.VideoCapture(str(watermarked))
    idx = 0
    rows, strip_rows = [], []
    while True:
        ok1, f1 = c_clean.read()
        ok2, f2 = c_out.read()
        ok3, f3 = c_in.read()
        if not (ok1 and ok2 and ok3):
            break
        idx += 1
        g1 = cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY)
        g2 = cv2.cvtColor(f2, cv2.COLOR_BGR2GRAY)
        g3 = cv2.cvtColor(f3, cv2.COLOR_BGR2GRAY)
        m = cv2.imread(str(mask_dir / f"f_{idx - 1:06d}.png"), cv2.IMREAD_GRAYSCALE)
        inside = m > 0
        outside = ~inside
        rows.append({
            "i": idx,
            "in_before": psnr(g1[inside], g3[inside]),
            "in_after": psnr(g1[inside], g2[inside]),
            "out_after": psnr(g1[outside], g2[outside]),
        })
        # Frames where the end-card is on screen: measure the 933..949 strip that
        # the old h = H*0.75 box missed entirely. Restricted to the card's own
        # x-extent, because the mask must not cover pixels outside the watermark.
        if any(r["start_s"] == SPLIT_S for r in active_regions((idx - 0.5) / FPS)):
            card = next(r for r in active_regions((idx - 0.5) / FPS) if r["start_s"] == SPLIT_S)
            sl = slice(933, 950)
            xsl = slice(card["x"], card["x"] + card["w"])
            strip_rows.append({
                "i": idx,
                "strip_before": psnr(g1[sl, xsl], g3[sl, xsl]),
                "strip_after": psnr(g1[sl, xsl], g2[sl, xsl]),
                "strip_mask_pct": float((m[sl, xsl] > 0).mean()) * 100,
            })
    c_clean.release()
    c_out.release()
    c_in.release()

    gains = [r["in_after"] - r["in_before"] for r in rows]
    quality = {
        "frames": len(rows),
        "gain_mean": float(np.mean(gains)),
        "gain_min": float(np.min(gains)),
        "in_before_mean": float(np.mean([r["in_before"] for r in rows])),
        "in_after_mean": float(np.mean([r["in_after"] for r in rows])),
        "outside_mean": float(np.mean([r["out_after"] for r in rows])),
        "outside_min": float(np.min([r["out_after"] for r in rows])),
        "worst_frame": int(rows[int(np.argmin(gains))]["i"]),
        "strip": {
            "frames": len(strip_rows),
            "mask_pct": float(np.mean([r["strip_mask_pct"] for r in strip_rows])) if strip_rows else 0.0,
            "before_mean": float(np.mean([r["strip_before"] for r in strip_rows])) if strip_rows else 0.0,
            "after_mean": float(np.mean([r["strip_after"] for r in strip_rows])) if strip_rows else 0.0,
        },
    }
    print(json.dumps(quality, ensure_ascii=False, indent=2))

    ok_gain = quality["gain_mean"] >= 5.0
    ok_worst = quality["gain_min"] >= -3.0
    ok_out = quality["outside_mean"] >= 25.0
    ok_frames = quality["frames"] == frame_count(watermarked)
    ok_strip_mask = quality["strip"]["mask_pct"] >= 99.0
    print(f"\ngain_mean      = {quality['gain_mean']:+6.2f}  (> 5)   {'PASS' if ok_gain else 'FAIL'}")
    print(f"gain_min       = {quality['gain_min']:+6.2f}  (> -3)  {'PASS' if ok_worst else 'FAIL'}")
    print(f"outside_mean   = {quality['outside_mean']:6.2f}  (> 25)  {'PASS' if ok_out else 'FAIL'}")
    print(f"frames         = {quality['frames']}/{frame_count(watermarked)}          {'PASS' if ok_frames else 'FAIL'}")
    print(f"strip 933..949 mask coverage = {quality['strip']['mask_pct']:5.1f}% (>=99) {'PASS' if ok_strip_mask else 'FAIL'}")
    print(f"strip 933..949 PSNR          = {quality['strip']['after_mean']:6.2f} dB (was {quality['strip']['before_mean']:.2f})")

    verdict = "PASS" if all([ok_gain, ok_worst, ok_out, ok_frames, ok_strip_mask]) else "FAIL"
    (GT / "result.json").write_text(
        json.dumps({"report": {
            "engine": proc.last_engine, "engine_note": proc.last_engine_note,
            "degraded": proc.last_degraded, "frames_repaired": rep.frames_repaired,
            "mask_coverage_pct": rep.mask_coverage_pct,
            "audio_preserved": rep.audio_preserved,
        }, "quality": quality, "verdict": verdict}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    print(f"\nVERDICT: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
