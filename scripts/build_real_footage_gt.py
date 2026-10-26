"""Build Ground-Truth tests from REAL local footage.

A clean real clip is trimmed, a known watermark is composited in Python, and the
mask is generated from the same coordinates. This exercises the detector on real
camera texture and motion instead of a synthetic background.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "real-footage-gt"
FPS = 10
DUR = 4
FRAMES = FPS * DUR
W, H = 640, 360

SOURCES = [
    ("real_portrait", Path.home() / "Desktop" / "视频" / "1024.mp4", 3.0),
    ("real_landscape", Path.home() / "Downloads" / "1.mp4", 2.0),
    ("real_screen", Path.home() / "Videos" / "屏幕录制" / "屏幕录制 2026-09-19 121228.mp4", 4.0),
]


def run(args, timeout=300):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True)


def trim_clean(src: Path, dst: Path, start: float) -> None:
    run(["ffmpeg", "-y", "-ss", str(start), "-i", str(src), "-t", str(DUR),
         "-vf", f"scale={W}:{H}", "-r", str(FPS),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16",
         "-c:a", "aac", str(dst)])


def composite(clean: Path, out: Path, kind: str) -> None:
    cap = cv2.VideoCapture(str(clean))
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    cap.release()
    d = out.parent / "_frames"
    d.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(frames):
        canvas = f.copy()
        mask = np.zeros(f.shape[:2], np.uint8)
        if kind == "text":
            cv2.putText(canvas, "WATERMARK", (24, 60), cv2.FONT_HERSHEY_SIMPLEX,
                        1.2, (255, 255, 255), 3, cv2.LINE_AA)
            (tw, th), _ = cv2.getTextSize("WATERMARK", cv2.FONT_HERSHEY_SIMPLEX, 1.2, 3)
            cv2.rectangle(mask, (18, 20), (24 + tw + 6, 70), 255, -1)
        elif kind == "logo":
            cv2.rectangle(canvas, (520, 280), (616, 336), (0, 0, 220), -1)
            cv2.rectangle(canvas, (544, 296), (592, 320), (255, 255, 255), -1)
            cv2.rectangle(mask, (516, 276), (620, 340), 255, -1)
        cv2.imwrite(str(d / f"f_{i:06d}.png"), canvas)
        cv2.imwrite(str(out.parent / "mask" / f"mask_{i+1:03d}.png"), mask) \
            if (out.parent / "mask").mkdir(parents=True, exist_ok=True) is None else None
    run(["ffmpeg", "-y", "-framerate", str(FPS), "-i", str(d / "f_%06d.png"),
         "-i", str(clean), "-map", "0:v:0", "-map", "1:a:0?",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-c:a", "copy",
         "-shortest", str(out)])


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for name, src, start in SOURCES:
        if not src.exists():
            print(f"skip {name}: source missing ({src})")
            continue
        sd = OUT / name
        sd.mkdir(parents=True)
        clean = sd / "clean_original.mp4"
        trim_clean(src, clean, start)
        composite(clean, sd / "input.mp4", "text" if "portrait" in name else "logo")
        print(f"built {name} from {src.name}")
    print(f"\nreal-footage GT at {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
