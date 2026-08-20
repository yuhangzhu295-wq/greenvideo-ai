from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _run_ffmpeg(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True)


def probe_video(path: str | Path) -> dict:
    if not shutil.which("ffprobe"):
        return {"duration": 0, "width": 0, "height": 0, "fallback": True}
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)


def extract_audio(input_path: str | Path, output_mp3: str | Path) -> Path:
    output = Path(output_mp3)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not ffmpeg_available():
        output.write_text("demo mp3 placeholder", encoding="utf-8")
        return output
    _run_ffmpeg(
        ["ffmpeg", "-y", "-i", str(input_path), "-vn", "-codec:a", "libmp3lame", str(output)]
    )
    return output


def make_thumbnail(input_path: str | Path, output_jpg: str | Path) -> Path:
    output = Path(output_jpg)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not ffmpeg_available():
        output.write_text("demo thumbnail placeholder", encoding="utf-8")
        return output
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-ss",
            "00:00:01",
            "-i",
            str(input_path),
            "-frames:v",
            "1",
            str(output),
        ]
    )
    return output


def extract_frames(input_path: str | Path, frames_dir: str | Path) -> Path:
    frames = Path(frames_dir)
    frames.mkdir(parents=True, exist_ok=True)
    if not ffmpeg_available():
        (frames / "frame_000001.txt").write_text("demo frame placeholder", encoding="utf-8")
        return frames
    _run_ffmpeg(
        ["ffmpeg", "-y", "-i", str(input_path), str(frames / "frame_%06d.png")],
        timeout=300,
    )
    return frames


def assemble_video(
    frames_dir: str | Path,
    audio_path: str | Path | None,
    output_mp4: str | Path,
    fps: int = 25,
) -> Path:
    output = Path(output_mp4)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not ffmpeg_available():
        output.write_text("demo mp4 placeholder", encoding="utf-8")
        return output

    args = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(fps),
        "-i",
        str(Path(frames_dir) / "frame_%06d.png"),
    ]
    if audio_path:
        args.extend(["-i", str(audio_path), "-shortest"])
    args.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", str(output)])
    _run_ffmpeg(args, timeout=300)
    return output
