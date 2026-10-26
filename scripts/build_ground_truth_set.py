"""Build Ground-Truth watermark test videos for the GreenVideo AI engine.

Every scenario starts from a real, decodable base video that contains no
watermark, then composites a known watermark with FFmpeg. Because the clean
original is kept, the repair quality can be measured against ground truth.

Scenarios
---------
T01 fixed corner text
T02 fixed logo (non-text)
T03 semi-transparent watermark
T04 moving watermark
T05 multiple simultaneous watermarks
T06 complex background + moving subject
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "watermark-e2e"

WIDTH = 640
HEIGHT = 360
FPS = 10
DURATION = 4  # seconds
FRAMES = FPS * DURATION

SCENARIOS = {
    "T01_fixed_corner_text": {
        "title": "固定角落文字水印",
        "kind": "corner_text",
        "mask_boxes": [(18, 18, 250, 62)],
    },
    "T02_fixed_logo": {
        "title": "固定 Logo 水印（非文字）",
        "kind": "logo",
        "mask_boxes": [(516, 276, 104, 64)],
    },
    "T03_semitransparent": {
        "title": "半透明水印",
        "kind": "translucent",
        "mask_boxes": [(174, 118, 300, 76)],
    },
    "T04_moving": {
        "title": "移动水印",
        "kind": "moving",
        "mask_boxes": "dynamic",
    },
    "T05_multiple": {
        "title": "多处水印",
        "kind": "multiple",
        "mask_boxes": [(14, 14, 140, 54), (536, 296, 88, 52), (254, 312, 140, 42)],
    },
    "T06_complex_motion": {
        "title": "复杂背景与运动主体",
        "kind": "complex",
        "mask_boxes": [(18, 14, 110, 48)],
    },
}


def run(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True)


def ffprobe_json(path: Path) -> dict:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ]
    )
    return json.loads(result.stdout)


def make_clean_base(path: Path, motion: bool = False) -> None:
    """A real decodable base clip with no watermark.

    The background is deliberately smooth (gradient + a few anti-aliased
    circles). High-frequency synthetic patterns such as ffmpeg's ``testsrc2``
    carry so much texture that the watermark signal is buried under codec
    noise, which makes any quantitative comparison meaningless.
    """
    import cv2
    import numpy as np

    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    frames_dir = path.parent / "_clean_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    total = FPS * DURATION
    for i in range(total):
        frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        for c in range(3):
            frame[:, :, c] = np.clip(
                xx * 0.35 + yy * 0.2 + i * 5 + c * 40, 0, 255
            ).astype(np.uint8)
        cx = int(WIDTH * 0.5 + 180 * np.sin(i * 0.12))
        cy = int(HEIGHT * 0.5 + 90 * np.cos(i * 0.12))
        cv2.circle(frame, (cx, cy), 70, (240, 240, 240), -1, cv2.LINE_AA)
        cx2 = int(WIDTH * 0.3 + 150 * np.cos(i * 0.09))
        cy2 = int(HEIGHT * 0.6 + 80 * np.sin(i * 0.09))
        cv2.circle(frame, (cx2, cy2), 50, (30, 60, 120), -1, cv2.LINE_AA)
        cv2.imwrite(str(frames_dir / f"f_{i:06d}.png"), frame)

    if motion:
        # A genuinely busier scene for the complex scenario: add moving blocks
        # and a drifting grid so the background is not a copy of the shared one.
        for i in range(total):
            frame = cv2.imread(str(frames_dir / f"f_{i:06d}.png"))
            for k in range(3):
                bx = int((i * 17 + k * 210) % (WIDTH - 60))
                by = int((i * 11 + k * 130) % (HEIGHT - 60))
                color = (40 + k * 60, 200 - k * 50, 120 + k * 40)
                cv2.rectangle(frame, (bx, by), (bx + 60, by + 60), color, -1)
            for gx in range(0, WIDTH, 80):
                cv2.line(frame, (gx, 0), (gx, HEIGHT), (90, 90, 90), 1)
            cv2.imwrite(str(frames_dir / f"f_{i:06d}.png"), frame)

    run(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(FPS),
            "-i",
            str(frames_dir / "f_%06d.png"),
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=44100:duration={DURATION}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "16",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ]
    )


def composite_watermark(clean: Path, out: Path, spec: dict) -> None:
    """Composite the watermark in Python so mask and overlay stay aligned.

    Drawing the watermark and its mask from the same coordinates removes any
    chance of the mask drifting away from the pixels it is supposed to cover.
    """
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(clean))
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    if not frames:
        raise RuntimeError(f"no frames decoded from {clean}")

    kind = spec["kind"]
    frames_dir = out.parent / "_wm_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    for i, frame in enumerate(frames):
        canvas = frame.copy()
        mask = np.zeros(frame.shape[:2], dtype=np.uint8)
        for op in _watermark_ops(kind, i):
            op(canvas, mask)
        cv2.imwrite(str(frames_dir / f"f_{i:06d}.png"), canvas)

    run(
        [
            "ffmpeg", "-y",
            "-framerate", str(FPS),
            "-i", str(frames_dir / "f_%06d.png"),
            "-i", str(clean),
            "-map", "0:v:0",
            "-map", "1:a:0?",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-crf", "18",
            "-c:a", "copy",
            "-shortest",
            str(out),
        ]
    )


def write_mask_frames(scenario_dir: Path, spec: dict) -> Path:
    """Write per-frame masks using the same ops as the watermark composite."""
    import cv2
    import numpy as np

    clean = scenario_dir / "clean_original.mp4"
    cap = cv2.VideoCapture(str(clean))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    frames_dir = scenario_dir / "mask"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for i in range(total):
        mask = np.zeros((h, w), dtype=np.uint8)
        for op in _watermark_ops(spec["kind"], i):
            op(np.zeros((h, w, 3), dtype=np.uint8), mask)
        cv2.imwrite(str(frames_dir / f"mask_{i + 1:03d}.png"), mask)

    mask_video = scenario_dir / "mask_truth.mp4"
    run(
        [
            "ffmpeg", "-y",
            "-framerate", str(FPS),
            "-i", str(frames_dir / "mask_%03d.png"),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(mask_video),
        ]
    )
    return mask_video


def extract_frames(video: Path, out_dir: Path, prefix: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-vsync",
            "0",
            str(out_dir / f"{prefix}_%03d.png"),
        ]
    )
    return sorted(out_dir.glob(f"{prefix}_*.png"))


def build() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "base").mkdir(parents=True, exist_ok=True)

    clean_shared = OUT / "base" / "clean_shared.mp4"
    clean_motion = OUT / "base" / "clean_motion.mp4"
    make_clean_base(clean_shared, motion=False)
    make_clean_base(clean_motion, motion=True)

    manifest: dict[str, dict] = {}

    for name, spec in SCENARIOS.items():
        scenario_dir = OUT / name
        scenario_dir.mkdir(parents=True, exist_ok=True)

        clean = clean_motion if name == "T06_complex_motion" else clean_shared
        local_clean = scenario_dir / "clean_original.mp4"
        shutil.copyfile(clean, local_clean)

        watermarked = scenario_dir / "input.mp4"
        composite_watermark(local_clean, watermarked, spec)

        mask_video = write_mask_frames(scenario_dir, spec)
        (scenario_dir / "mask").mkdir(exist_ok=True)
        mask_frames = extract_frames(mask_video, scenario_dir / "mask", "mask")

        before_dir = scenario_dir / "before_frames"
        extract_frames(watermarked, before_dir, "before")

        probe_in = ffprobe_json(watermarked)
        (scenario_dir / "ffprobe_input.json").write_text(
            json.dumps(probe_in, indent=2), encoding="utf-8"
        )

        manifest[name] = {
            "title": spec["title"],
            "clean_original": str(local_clean.relative_to(ROOT)),
            "input": str(watermarked.relative_to(ROOT)),
            "mask_truth_video": str(mask_video.relative_to(ROOT)),
            "mask_frames": len(mask_frames),
            "mask_boxes": spec["mask_boxes"],
            "duration_s": DURATION,
            "fps": FPS,
            "frames": FRAMES,
            "width": WIDTH,
            "height": HEIGHT,
            "audio_streams": sum(
                1 for s in probe_in.get("streams", []) if s.get("codec_type") == "audio"
            ),
        }
        print(f"built {name}: {spec['title']}")

    (OUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nGround-truth set ready at {OUT}")
    return 0



def _opaque_text(text: str, x: int, y: int, scale: float = 1.0):
    import cv2

    def apply(canvas, mask):
        cv2.putText(
            canvas, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale,
            (255, 255, 255), max(2, int(3 * scale)), cv2.LINE_AA,
        )
        (tw, th), _ = cv2.getTextSize(
            text, cv2.FONT_HERSHEY_SIMPLEX, scale, max(2, int(3 * scale))
        )
        cv2.rectangle(mask, (x - 6, y - th - 8), (x + tw + 6, y + 10), 255, -1)

    return apply


def _translucent_text(text: str, x: int, y: int, alpha: float, scale: float = 1.0):
    import cv2
    import numpy as np

    def apply(canvas, mask):
        overlay = canvas.copy()
        cv2.putText(
            overlay, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale,
            (255, 255, 255), max(2, int(3 * scale)), cv2.LINE_AA,
        )
        cv2.addWeighted(overlay, alpha, canvas, 1 - alpha, 0, dst=canvas)
        (tw, th), _ = cv2.getTextSize(
            text, cv2.FONT_HERSHEY_SIMPLEX, scale, max(2, int(3 * scale))
        )
        cv2.rectangle(mask, (x - 6, y - th - 8), (x + tw + 6, y + 10), 255, -1)

    return apply


def _logo(x: int, y: int, w: int, h: int):
    import cv2

    def apply(canvas, mask):
        cv2.rectangle(canvas, (x, y), (x + w, y + h), (0, 0, 220), -1)
        cv2.rectangle(canvas, (x + w // 4, y + h // 3), (x + 3 * w // 4, y + 2 * h // 3),
                      (255, 255, 255), -1)
        cv2.rectangle(mask, (x - 4, y - 4), (x + w + 4, y + h + 4), 255, -1)

    return apply


def _opaque_text_on_plate(text: str, x: int, y: int, scale: float = 1.0):
    import cv2

    def apply(canvas, mask):
        (tw, th), _ = cv2.getTextSize(
            text, cv2.FONT_HERSHEY_SIMPLEX, scale, max(2, int(3 * scale))
        )
        cv2.rectangle(canvas, (x - 6, y - th - 8), (x + tw + 6, y + 10), (0, 0, 0), -1)
        cv2.putText(
            canvas, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale,
            (255, 255, 255), max(2, int(3 * scale)), cv2.LINE_AA,
        )
        cv2.rectangle(mask, (x - 6, y - th - 8), (x + tw + 6, y + 10), 255, -1)

    return apply


def _translucent_text_on_plate(text: str, x: int, y: int, alpha: float, scale: float = 1.0):
    import cv2
    import numpy as np

    def apply(canvas, mask):
        (tw, th), _ = cv2.getTextSize(
            text, cv2.FONT_HERSHEY_SIMPLEX, scale, max(2, int(3 * scale))
        )
        x1, y1, x2, y2 = x - 6, y - th - 8, x + tw + 6, y + 10
        # A mid-grey plate gives the translucent wash a stable backdrop so its
        # strength does not depend on whatever the background happens to be.
        plate = np.full((y2 - y1, x2 - x1, 3), 110, dtype=np.uint8)
        region = canvas[y1:y2, x1:x2]
        canvas[y1:y2, x1:x2] = cv2.addWeighted(plate, 0.5, region, 0.5, 0)
        cv2.putText(
            canvas, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale,
            (255, 255, 255), max(2, int(3 * scale)), cv2.LINE_AA,
        )
        cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)

    return apply


def _watermark_ops(kind: str, frame_index: int) -> list:
    """Return the draw operations for one frame of the given scenario."""
    if kind == "corner_text":
        return [_opaque_text("WATERMARK", 24, 60, 1.2)]
    if kind == "logo":
        return [_logo(520, 280, 96, 56)]
    if kind == "translucent":
        return [_translucent_text_on_plate("SAMPLE WM", 180, 185, 0.38, 1.4)]
    if kind == "moving":
        seg = min(3, frame_index // 10)
        x, y = [(40, 300), (260, 60), (480, 60), (40, 300)][seg]
        # A dark plate keeps the white text legible against any background so
        # the watermark is genuinely present in every frame.
        return [_opaque_text_on_plate("MOVING", x, y + 30, 1.1)]
    if kind == "multiple":
        return [
            _opaque_text("BRAND", 20, 48, 1.0),
            _logo(540, 300, 80, 44),
            _translucent_text("ID8899", 260, 345, 0.7, 0.8),
        ]
    if kind == "complex":
        # Top-left corner: the drifting blocks in this scenario sweep the lower
        # part of the frame, so a corner watermark stays visible in every frame.
        return [_opaque_text("WM", 24, 46, 1.0)]
    raise ValueError(f"unknown watermark kind: {kind}")


if __name__ == "__main__":
    sys.exit(build())
