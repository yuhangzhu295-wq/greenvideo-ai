"""Multimodal Vision LLM (Gemini / VLM) Spatio-Temporal Watermark Grounding Service.

Provides video-level semantic watermark identification, spatio-temporal grounding,
and distinction between removable platform overlays (logos, account IDs, floating
text, outro QR cards) and native video content (scene text, dialogue subtitles).

Seamlessly degrades to local CV when API keys are unconfigured.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any, Literal

import cv2
import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class VLMKeyframeBox(BaseModel):
    """Normalized bounding box coordinate in [0, 1000] scale."""

    t_s: float
    ymin: int = Field(ge=0, le=1000)
    xmin: int = Field(ge=0, le=1000)
    ymax: int = Field(ge=0, le=1000)
    xmax: int = Field(ge=0, le=1000)


class VLMWatermarkTrack(BaseModel):
    """Spatio-temporal watermark trajectory detected by Vision LLM."""

    track_id: str
    kind: Literal[
        "platform_logo",
        "account_id",
        "floating_text",
        "qr_code",
        "outro_card",
        "subtitle",
        "other",
    ]
    should_remove: bool = True
    is_moving: bool = False
    start_s: float = 0.0
    end_s: float = 0.0
    keyframes: list[VLMKeyframeBox] = Field(default_factory=list)


class VLMVideoAnalysisResult(BaseModel):
    """Structured grounding result from Vision LLM."""

    ok: bool = False
    outro_start_s: float | None = None
    tracks: list[VLMWatermarkTrack] = Field(default_factory=list)
    reason: str | None = None


def is_vlm_available() -> bool:
    """Check whether Gemini or compatible VLM API key is configured."""
    return bool(
        os.getenv("GEMINI_API_KEY")
        or os.getenv("VLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )


def sample_keyframes_for_vlm(
    video_path: str | Path,
    max_frames: int = 16,
) -> list[tuple[float, bytes]]:
    """Sample representative keyframes from the video with burned timestamp tags.

    Returns:
        List of (timestamp_s, jpeg_bytes)
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = max(1.0, cap.get(cv2.CAP_PROP_FPS))

    if total_frames <= 0:
        cap.release()
        return []

    step = max(1, total_frames // max_frames)
    frame_indices = list(range(0, total_frames, step))[:max_frames]

    results = []
    for f_idx in frame_indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
        ret, frame = cap.read()
        if not ret:
            break
        t_sec = round(f_idx / fps, 2)

        annotated = frame.copy()
        cv2.putText(
            annotated,
            f"t={t_sec:.2f}s",
            (int(annotated.shape[1] * 0.42), 26),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )

        ok, buf = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if ok:
            results.append((t_sec, buf.tobytes()))

    cap.release()
    return results


VLM_GROUNDING_PROMPT = """
You are a precise video watermark and overlay spatio-temporal detection assistant.
Analyze these timestamped sequential frames from a short video clip.

Tasks:
1. Locate every platform watermark (e.g. Douyin/TikTok, Kuaishou, Xiaohongshu, Bilibili,
   Doubao, CapCut logos and author IDs), floating watermarks, outro cards, and promo QR codes.
2. Distinguish removable external overlays from native video content:
   - should_remove = true: Platform logos, creator ID watermarks, promo cards.
   - should_remove = false: Native actor dialogue subtitles, in-scene shop signs, clothing text.
3. For each watermark track, specify:
   - track_id: unique string (e.g., "track_logo_tl", "track_id_br")
   - kind: "platform_logo" | "account_id" | "floating_text" | "qr_code" | "outro_card" | "subtitle"
   - should_remove: boolean
   - is_moving: boolean
   - start_s: first appearance timestamp (seconds)
   - end_s: last disappearance timestamp (seconds)
   - keyframes: list of bounding boxes [ymin, xmin, ymax, xmax] normalized to 0-1000 scale.
4. If there is a promotional outro card at the end of the video, set outro_start_s, else null.

Output STRICT JSON conforming to this schema:
{
  "outro_start_s": float or null,
  "tracks": [
    {
      "track_id": "str",
      "kind": "str",
      "should_remove": true,
      "is_moving": false,
      "start_s": 0.0,
      "end_s": 5.2,
      "keyframes": [
        {"t_s": 0.0, "ymin": 0, "xmin": 0, "ymax": 82, "xmax": 242}
      ]
    }
  ]
}
"""


def call_gemini_grounding(
    keyframes: list[tuple[float, bytes]],
    api_key: str,
    model_name: str = "gemini-2.0-flash",
    timeout_s: float = 25.0,
) -> VLMVideoAnalysisResult:
    """Call Google Gemini multimodal API for spatio-temporal watermark grounding."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

    parts: list[dict[str, Any]] = [{"text": VLM_GROUNDING_PROMPT}]
    for _, img_bytes in keyframes:
        parts.append(
            {
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(img_bytes).decode("ascii"),
                }
            }
        )

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.1,
        },
    }

    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning("Gemini VLM API error %d: %s", resp.status_code, resp.text[:200])
                return VLMVideoAnalysisResult(
                    ok=False, reason=f"GEMINI_API_ERROR_{resp.status_code}"
                )

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return VLMVideoAnalysisResult(ok=False, reason="NO_CANDIDATES")

            text_content = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            parsed = json.loads(text_content)

            outro_s = parsed.get("outro_start_s")
            raw_tracks = parsed.get("tracks", [])

            tracks: list[VLMWatermarkTrack] = []
            for rt in raw_tracks:
                if not rt.get("should_remove", True):
                    continue
                kfs = [
                    VLMKeyframeBox(
                        t_s=kf.get("t_s", 0.0),
                        ymin=int(kf.get("ymin", 0)),
                        xmin=int(kf.get("xmin", 0)),
                        ymax=int(kf.get("ymax", 1000)),
                        xmax=int(kf.get("xmax", 1000)),
                    )
                    for kf in rt.get("keyframes", [])
                ]
                tracks.append(
                    VLMWatermarkTrack(
                        track_id=str(rt.get("track_id", "track")),
                        kind=rt.get("kind", "other"),
                        should_remove=True,
                        is_moving=bool(rt.get("is_moving", False)),
                        start_s=float(rt.get("start_s", 0.0)),
                        end_s=float(rt.get("end_s", 0.0)),
                        keyframes=kfs,
                    )
                )

            return VLMVideoAnalysisResult(
                ok=True,
                outro_start_s=float(outro_s) if outro_s is not None else None,
                tracks=tracks,
            )

    except Exception as exc:
        logger.warning("Gemini grounding request exception: %s", exc)
        return VLMVideoAnalysisResult(ok=False, reason=f"EXCEPTION_{exc}")


def call_openai_compatible_grounding(
    keyframes: list[tuple[float, bytes]],
    api_key: str,
    base_url: str | None = None,
    model_name: str = "gpt-4o-mini",
    timeout_s: float = 25.0,
) -> VLMVideoAnalysisResult:
    """Call OpenAI-compatible vision chat completion endpoint for watermark grounding."""
    base = (base_url or "https://api.openai.com/v1").rstrip("/")
    url = f"{base}/chat/completions"

    content: list[dict[str, Any]] = [{"type": "text", "text": VLM_GROUNDING_PROMPT}]
    for _, img_bytes in keyframes:
        b64 = base64.b64encode(img_bytes).decode("ascii")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
        })

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }

    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=4.0)) as client:
            resp = client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                logger.warning(
                    "OpenAI VLM API error %d: %s", resp.status_code, resp.text[:200]
                )
                return VLMVideoAnalysisResult(
                    ok=False, reason=f"OPENAI_API_ERROR_{resp.status_code}"
                )

            data = resp.json()
            text_content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = json.loads(text_content)

            outro_s = parsed.get("outro_start_s")
            raw_tracks = parsed.get("tracks", [])

            tracks: list[VLMWatermarkTrack] = []
            for rt in raw_tracks:
                if not rt.get("should_remove", True):
                    continue
                kfs = [
                    VLMKeyframeBox(
                        t_s=kf.get("t_s", 0.0),
                        ymin=int(kf.get("ymin", 0)),
                        xmin=int(kf.get("xmin", 0)),
                        ymax=int(kf.get("ymax", 1000)),
                        xmax=int(kf.get("xmax", 1000)),
                    )
                    for kf in rt.get("keyframes", [])
                ]
                tracks.append(
                    VLMWatermarkTrack(
                        track_id=str(rt.get("track_id", "track")),
                        kind=rt.get("kind", "other"),
                        should_remove=True,
                        is_moving=bool(rt.get("is_moving", False)),
                        start_s=float(rt.get("start_s", 0.0)),
                        end_s=float(rt.get("end_s", 0.0)),
                        keyframes=kfs,
                    )
                )

            return VLMVideoAnalysisResult(
                ok=True,
                outro_start_s=float(outro_s) if outro_s is not None else None,
                tracks=tracks,
            )
    except Exception as exc:
        logger.warning("OpenAI grounding exception: %s", exc)
        return VLMVideoAnalysisResult(ok=False, reason=f"EXCEPTION_{exc}")


def analyze_video_watermarks_vlm(video_path: str | Path) -> VLMVideoAnalysisResult:
    """Public entrypoint: analyze video with Vision LLM or return unconfigured."""
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("VLM_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    if not gemini_key and not openai_key:
        return VLMVideoAnalysisResult(
            ok=False,
            reason="VLM_NOT_CONFIGURED",
            tracks=[],
        )

    keyframes = sample_keyframes_for_vlm(video_path, max_frames=14)
    if not keyframes:
        return VLMVideoAnalysisResult(ok=False, reason="CANNOT_EXTRACT_KEYFRAMES")

    if gemini_key:
        model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        return call_gemini_grounding(keyframes, gemini_key, model_name)
    else:
        base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("VLM_BASE_URL")
        model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        return call_openai_compatible_grounding(keyframes, openai_key, base_url, model_name)


def convert_vlm_tracks_to_pixel_boxes(
    tracks: list[VLMWatermarkTrack],
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    """Convert VLM normalized [0..1000] keyframes into pixel coordinates for downstream tracking."""
    converted = []
    for track in tracks:
        if not track.keyframes:
            continue
        kfs = sorted(track.keyframes, key=lambda k: k.t_s)
        if len(kfs) == 1:
            kf = kfs[0]
            x1 = int(kf.xmin * width / 1000.0)
            y1 = int(kf.ymin * height / 1000.0)
            x2 = int(kf.xmax * width / 1000.0)
            y2 = int(kf.ymax * height / 1000.0)
            w = max(1, x2 - x1)
            h = max(1, y2 - y1)
            converted.append(
                {
                    "track_id": track.track_id,
                    "kind": track.kind,
                    "start_s": track.start_s,
                    "end_s": track.end_s,
                    "box": [x1, y1, w, h],
                    "confidence": 0.95,
                    "is_moving": track.is_moving,
                }
            )
        else:
            for idx in range(len(kfs)):
                kf = kfs[idx]
                s_t = track.start_s if idx == 0 else (kfs[idx - 1].t_s + kf.t_s) / 2.0
                e_t = track.end_s if idx == len(kfs) - 1 else (kf.t_s + kfs[idx + 1].t_s) / 2.0
                x1 = int(kf.xmin * width / 1000.0)
                y1 = int(kf.ymin * height / 1000.0)
                x2 = int(kf.xmax * width / 1000.0)
                y2 = int(kf.ymax * height / 1000.0)
                w = max(1, x2 - x1)
                h = max(1, y2 - y1)
                converted.append(
                    {
                        "track_id": f"{track.track_id}_{idx}",
                        "kind": track.kind,
                        "start_s": round(s_t, 2),
                        "end_s": round(e_t, 2),
                        "box": [x1, y1, w, h],
                        "confidence": 0.95,
                        "is_moving": track.is_moving,
                    }
                )
    return converted
