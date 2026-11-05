"""Blind test set generator (goal task 14).

Unlike build_gt_watermark_dataset.py's deterministic modulo cycles, this
generator draws EVERY watermark parameter at random from a seed the
implementer never sees in advance: scene, trajectory, text (including
Chinese and brand-like strings), colour, alpha, font scale, position and
motion phase. The reviewer prints the seed only alongside results; the
implementer receives failure evidence, not the parameter set.

Usage:
    python scripts/build_blind_set.py --output artifacts/blind-set --count 6 [--seed N]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_gt_watermark_dataset import generate_video  # noqa: E402

SCENES = ["grass", "sky", "night", "building", "water", "face", "camera_motion"]
TRAJECTORIES = ["fixed_tl", "fixed_br", "corner_hop", "continuous_drift", "fade_in_out"]

# Deliberately generic strings: platform-like, user-like, Chinese, digits.
TEXTS = [
    "@user_watermark", "VLOG_2026", "小明爱旅行", "No.1088",
    "my_channel", "光影纪实", "@creator.official", "ID 9527",
    "半透明测试", "STM-0425", "@daily_clip", "只此青绿",
]
COLOURS = [
    (255, 255, 255), (0, 0, 0), (0, 200, 255), (180, 100, 50),
    (80, 220, 120), (255, 180, 40), (210, 210, 210),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/blind-set", type=str)
    parser.add_argument("--count", default=6, type=int)
    parser.add_argument("--seed", type=int, default=None,
                        help="Reviewer-chosen seed; random when omitted")
    parser.add_argument("--height", default=720, type=int)
    parser.add_argument("--width", default=1280, type=int)
    parser.add_argument("--fps", default=30.0, type=float)
    parser.add_argument("--duration", default=4.0, type=float)
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else int(np.random.randint(0, 2**31 - 1))
    rng = np.random.RandomState(seed)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Wipe stale dirs so a rerun cannot leave videos from a previous seed.
    for child in output_dir.iterdir():
        if child.is_dir() and child.name != "frames":
            import shutil

            shutil.rmtree(child, ignore_errors=True)

    all_meta = []
    for i in range(args.count):
        scene = SCENES[rng.randint(len(SCENES))]
        traj = TRAJECTORIES[rng.randint(len(TRAJECTORIES))]
        text = TEXTS[rng.randint(len(TEXTS))]
        colour = COLOURS[rng.randint(len(COLOURS))]
        alpha = round(float(rng.uniform(0.25, 0.75)), 2)
        font_scale = round(float(rng.uniform(0.9, 1.6)), 2)
        s = int(seed + i)
        meta = generate_video(
            output_dir, scene, traj, text, colour, alpha,
            args.height, args.width, args.fps, args.duration, s,
            font_scale=font_scale,
        )
        all_meta.append(meta)
        print(f"[blind {i+1}/{args.count}] {scene}/{traj} '{text}' "
              f"colour={colour} alpha={alpha}")

    from build_gt_watermark_dataset import DATASET_VERSION

    (output_dir / "index.json").write_text(
        json.dumps({"dataset_version": DATASET_VERSION, "videos": all_meta}, indent=2),
        encoding="utf-8",
    )
    print(f"\nBlind set: {args.count} videos, SEED={seed} (reviewer keeps this)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
