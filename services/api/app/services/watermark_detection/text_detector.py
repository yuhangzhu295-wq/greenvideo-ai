"""Text detector - PaddleOCR polygon discovery only (prompt producer).

Produces OCR polygons + text + confidence as *detection prompts*. These are
NEVER used directly as inpaint masks; they feed SAM 2.1 for pixel-level
segmentation.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_DIR = Path(__file__).resolve().parent
_AI_WORKER_DIR = _DIR.parent.parent.parent.parent.parent / "ai-worker"


@dataclass
class TextPrompt:
    """A detected text region - a SAM prompt, not a repair mask."""

    box: tuple[int, int, int, int]  # x, y, w, h
    polygon: list[tuple[int, int]] = field(default_factory=list)
    text: str = ""
    confidence: float = 0.0
    frame_index: int = 0
    time_s: float = 0.0

    @property
    def box_xyxy(self) -> list[int]:
        return [self.box[0], self.box[1], self.box[0] + self.box[2], self.box[1] + self.box[3]]


def detect_text_frames(
    video_path: str | Path,
    frame_indices: list[int],
    sample_texts: set[str] | None = None,
) -> dict[int, list[TextPrompt]]:
    """Run PaddleOCR on the given frames via the ai-worker CLI.

    Returns {frame_index: [TextPrompt, ...]}. When ``sample_texts`` is given,
    only prompts whose text fuzzy-matches one of the targets survive; this is a
    *detection prior*, never the final mask.
    """
    from app.services.watermark_detect import _fuzzy_text_hit

    cli = _AI_WORKER_DIR / "ocr_detector_cli.py"
    python = _AI_WORKER_DIR / ".venv" / "Scripts" / "python.exe"
    if not (cli.exists() and python.exists()):
        raise FileNotFoundError("ai-worker OCR CLI 不存在")

    results: dict[int, list[TextPrompt]] = {}
    cap_step = 0
    for f_idx in frame_indices:
        cmd = [
            str(python),
            str(cli),
            "--video",
            str(video_path),
            "--start-frame",
            str(f_idx),
            "--end-frame",
            str(f_idx + 1),
            "--raw",
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
        stdout = proc.stdout.decode("utf-8", errors="replace")
        prompts: list[TextPrompt] = []
        for line in stdout.splitlines():
            if not line.startswith("OUTPUT_JSON:"):
                continue
            sightings = json.loads(line.removeprefix("OUTPUT_JSON:"))
            for s in sightings:
                text = str(s.get("text", ""))
                if sample_texts and not _fuzzy_text_hit(text, sample_texts):
                    continue
                bx, by, bw, bh = (int(v) for v in s.get("box", [0, 0, 0, 0]))
                if bw <= 0 or bh <= 0:
                    continue
                prompts.append(
                    TextPrompt(
                        box=(bx, by, bw, bh),
                        text=text,
                        confidence=float(s.get("score", 0.0)),
                        frame_index=f_idx,
                        time_s=float(s.get("time_s", 0.0)),
                    )
                )
        results[f_idx] = prompts
        cap_step += 1
    return results
