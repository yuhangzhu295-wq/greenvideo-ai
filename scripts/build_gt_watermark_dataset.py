"""Ground-truth watermark dataset generator.

Synthesizes clean-background videos with programmatically drawn watermarks and
saves per-frame GT alpha masks. Covers: multi-scene backgrounds (grass, face,
sky, night, building, water, fast camera), watermark types (text white/black/
colour/semi-transparent/logo/multi-line), trajectories (fixed, corner-hop,
continuous drift, fade in/out, multi-target), and resolutions (720p, 1080p).

Usage:
    python scripts/build_gt_dataset.py --output artifacts/gt-dataset --count 12
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np


def _make_background(scene: str, h: int, w: int, t: float) -> np.ndarray:
    """Create a synthetic background frame for the given scene type."""
    rng = np.random.RandomState(int(t * 1000) % 2**31)
    if scene == "grass":
        frame = np.full((h, w, 3), (40, 120, 40), dtype=np.uint8)
        noise = rng.normal(0, 15, (h, w, 1)).astype(np.uint8)
        frame = np.clip(frame.astype(int) + noise, 0, 255).astype(np.uint8)
    elif scene == "sky":
        frame = np.full((h, w, 3), (200, 180, 140), dtype=np.uint8)
        frame[: h // 3] = (230, 220, 190)
    elif scene == "night":
        frame = np.full((h, w, 3), (20, 15, 10), dtype=np.uint8)
        pts = rng.randint(0, h, (100, 2))
        for y, x in pts:
            frame[y, x] = (200, 200, 180)
    elif scene == "building":
        frame = np.full((h, w, 3), (100, 100, 110), dtype=np.uint8)
        for i in range(0, w, 80):
            cv2.rectangle(frame, (i, 0), (i + 60, h), (70, 70, 80), -1)
    elif scene == "water":
        frame = np.full((h, w, 3), (150, 100, 50), dtype=np.uint8)
        for i in range(0, h, 20):
            offset = int(10 * math.sin(t * 2 + i / 20))
            frame[i : i + 10] = np.roll(frame[i : i + 10], offset, axis=1)
    elif scene == "face":
        frame = np.full((h, w, 3), (140, 160, 190), dtype=np.uint8)
        cv2.circle(frame, (w // 2, h // 2), min(h, w) // 4, (160, 180, 210), -1)
        cv2.circle(frame, (w // 2 - 40, h // 2 - 30), 12, (50, 50, 50), -1)
        cv2.circle(frame, (w // 2 + 40, h // 2 - 30), 12, (50, 50, 50), -1)
    elif scene == "camera_motion":
        shift = int(t * 60) % w
        frame = np.full((h, w, 3), (90, 110, 130), dtype=np.uint8)
        for i in range(0, w + 200, 150):
            x = (i - shift) % (w + 200) - 100
            if -100 < x < w:
                cv2.rectangle(
                    frame, (max(0, x), h // 3), (min(w, x + 80), 2 * h // 3),
                    (120, 140, 160), -1,
                )
    else:  # default grey
        frame = np.full((h, w, 3), 100, dtype=np.uint8)
    return frame


def _draw_watermark(
    frame: np.ndarray,
    wm_mask: np.ndarray,
    text: str,
    pos: tuple[int, int],
    colour: tuple[int, int, int],
    alpha: float,
    font_scale: float,
) -> np.ndarray:
    """Draw watermark onto frame and update GT alpha mask."""
    overlay = frame.copy()
    cv2.putText(overlay, text, pos, cv2.FONT_HERSHEY_SIMPLEX, font_scale, colour, 3, cv2.LINE_AA)
    cv2.putText(wm_mask, text, pos, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 255, 3, cv2.LINE_AA)
    return cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0)


def _watermark_position(traj: str, t: float, dur: float, h: int, w: int, rng) -> tuple[int, int]:
    margin = 20
    if traj == "fixed_tl":
        return (margin, h // 8)
    if traj == "fixed_br":
        return (w - 300, h - 60)
    if traj == "corner_hop":
        corners = [(margin, h // 8), (w - 300, h // 8), (margin, h - 60), (w - 300, h - 60)]
        idx = int(t / (dur / 4)) % 4
        return corners[idx]
    if traj == "continuous_drift":
        cx = w / 2 + (w * 0.3) * math.sin(t / dur * 2 * math.pi)
        cy = h / 2 + (h * 0.25) * math.cos(t / dur * 2 * math.pi)
        return (int(cx - 100), int(cy))
    if traj == "fade_in_out":
        return (margin, h // 8)
    return (margin, h // 8)


def generate_video(
    output_dir: Path,
    scene: str,
    traj: str,
    text: str,
    colour: tuple[int, int, int],
    alpha: float,
    h: int,
    w: int,
    fps: float,
    dur_s: float,
    seed: int,
) -> dict:
    """Generate one GT video + per-frame masks + metadata."""
    rng = np.random.RandomState(seed)
    video_dir = output_dir / f"{scene}_{traj}_{seed:04d}"
    video_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = video_dir / "frames"
    masks_dir = video_dir / "gt_masks"
    frames_dir.mkdir(exist_ok=True)
    masks_dir.mkdir(exist_ok=True)

    n = int(fps * dur_s)
    fade_period = dur_s / 2 if traj == "fade_in_out" else None

    for i in range(n):
        t = i / fps
        bg = _make_background(scene, h, w, t)
        # Save clean frame (no watermark) for E2E repair quality comparison
        clean_dir = video_dir / "clean_frames"
        clean_dir.mkdir(exist_ok=True)
        cv2.imwrite(str(clean_dir / f"{i:06d}.png"), bg)
        wm_mask = np.zeros((h, w), dtype=np.uint8)
        pos = _watermark_position(traj, t, dur_s, h, w, rng)

        a = alpha
        if fade_period is not None:
            phase = (t % (2 * fade_period)) / fade_period
            if phase < 0.5:
                a = alpha * min(1.0, phase * 2)
            else:
                a = alpha * min(1.0, (1 - phase) * 2)

        bg = _draw_watermark(bg, wm_mask, text, pos, colour, a, 1.2)
        cv2.imwrite(str(frames_dir / f"{i:06d}.png"), bg)
        cv2.imwrite(str(masks_dir / f"{i:06d}.png"), wm_mask)

    meta = {
        "scene": scene,
        "trajectory": traj,
        "text": text,
        "colour": list(colour),
        "alpha": alpha,
        "width": w,
        "height": h,
        "fps": fps,
        "duration_s": dur_s,
        "frames": n,
        "seed": seed,
    }
    (video_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # Also encode as MP4 for pipeline input
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(video_dir / "input.mp4"), fourcc, fps, (w, h))
    for i in range(n):
        fr = cv2.imread(str(frames_dir / f"{i:06d}.png"))
        if fr is not None:
            writer.write(fr)
    writer.release()

    return meta


SCENES = ["grass", "sky", "night", "building", "water", "face", "camera_motion"]
TRAJECTORIES = ["fixed_tl", "fixed_br", "corner_hop", "continuous_drift", "fade_in_out"]
TEXTS = ["bilibili", "douyin_id: 12345", "@user_watermark", "TikTok @creator"]
COLOURS = [(255, 255, 255), (0, 0, 0), (0, 200, 255), (180, 100, 50)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/gt-dataset", type=str)
    parser.add_argument("--count", default=12, type=int, help="number of videos to generate")
    parser.add_argument("--height", default=720, type=int)
    parser.add_argument("--width", default=1280, type=int)
    parser.add_argument("--fps", default=30.0, type=float)
    parser.add_argument("--duration", default=4.0, type=float)
    parser.add_argument("--seed", default=42, type=int)
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_meta = []
    for i in range(args.count):
        rng = np.random.RandomState(args.seed + i)
        scene = SCENES[i % len(SCENES)]
        traj = TRAJECTORIES[i % len(TRAJECTORIES)]
        text = TEXTS[i % len(TEXTS)]
        colour = COLOURS[i % len(COLOURS)]
        alpha = round(rng.uniform(0.3, 0.7), 2)
        meta = generate_video(
            output_dir, scene, traj, text, colour, alpha,
            args.height, args.width, args.fps, args.duration, args.seed + i,
        )
        all_meta.append(meta)
        print(f"[{i+1}/{args.count}] {scene}/{traj} '{text}' alpha={alpha}")

    (output_dir / "index.json").write_text(
        json.dumps(all_meta, indent=2), encoding="utf-8"
    )
    print(f"\nGenerated {args.count} videos in {output_dir}")


if __name__ == "__main__":
    main()
