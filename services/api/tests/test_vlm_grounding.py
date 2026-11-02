import pytest
from pathlib import Path
from app.services.vlm_grounding import (
    VLMKeyframeBox,
    VLMWatermarkTrack,
    VLMVideoAnalysisResult,
    is_vlm_available,
    convert_vlm_tracks_to_pixel_boxes,
    analyze_video_watermarks_vlm,
)


def test_vlm_grounding_models():
    box = VLMKeyframeBox(t_s=1.5, ymin=10, xmin=20, ymax=100, xmax=200)
    assert box.t_s == 1.5
    assert box.ymin == 10
    assert box.xmax == 200

    track = VLMWatermarkTrack(
        track_id="tr_0",
        kind="platform_logo",
        should_remove=True,
        is_moving=False,
        start_s=0.0,
        end_s=5.0,
        keyframes=[box],
    )
    assert track.track_id == "tr_0"
    assert len(track.keyframes) == 1

    res = VLMVideoAnalysisResult(ok=True, tracks=[track], outro_start_s=12.5)
    assert res.ok is True
    assert res.outro_start_s == 12.5


def test_convert_vlm_tracks_to_pixel_boxes():
    track = VLMWatermarkTrack(
        track_id="test_tr",
        kind="account_id",
        should_remove=True,
        is_moving=True,
        start_s=1.0,
        end_s=4.0,
        keyframes=[
            VLMKeyframeBox(t_s=1.0, ymin=0, xmin=0, ymax=100, xmax=200),
            VLMKeyframeBox(t_s=3.0, ymin=900, xmin=800, ymax=1000, xmax=1000),
        ],
    )
    # 720 x 1280 (width=720, height=1280)
    pixel_boxes = convert_vlm_tracks_to_pixel_boxes([track], width=720, height=1280)
    assert len(pixel_boxes) == 2

    b1 = pixel_boxes[0]
    assert b1["track_id"] == "test_tr_0"
    assert b1["kind"] == "account_id"
    assert b1["box"] == [0, 0, 144, 128]
    assert b1["start_s"] == 1.0
    assert b1["end_s"] == 2.0

    b2 = pixel_boxes[1]
    assert b2["track_id"] == "test_tr_1"
    assert b2["start_s"] == 2.0
    assert b2["end_s"] == 4.0
    assert b2["box"] == [576, 1152, 144, 128]


def test_vlm_fallback_when_unconfigured(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("VLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert is_vlm_available() is False

    dummy_video = tmp_path / "dummy.mp4"
    dummy_video.write_bytes(b"dummy")

    res = analyze_video_watermarks_vlm(dummy_video)
    assert res.ok is False
    assert res.reason == "VLM_NOT_CONFIGURED"
    assert res.tracks == []


def test_detect_timed_watermarks_hybrid_with_vlm(monkeypatch, tmp_path):
    import cv2
    import numpy as np
    from app.services.watermark_detect import detect_timed_watermarks_hybrid

    # Create dummy video
    vid_path = tmp_path / "vlm_dummy.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(str(vid_path), fourcc, 25.0, (100, 100))
    for _ in range(10):
        out.write(np.zeros((100, 100, 3), dtype=np.uint8))
    out.release()

    monkeypatch.setenv("GEMINI_API_KEY", "test_key")

    mock_track = VLMWatermarkTrack(
        track_id="vlm_0",
        kind="platform_logo",
        should_remove=True,
        is_moving=False,
        start_s=0.0,
        end_s=0.4,
        keyframes=[VLMKeyframeBox(t_s=0.0, ymin=0, xmin=0, ymax=200, xmax=200)],
    )

    def mock_analyze(path):
        return VLMVideoAnalysisResult(ok=True, tracks=[mock_track], outro_start_s=None)

    monkeypatch.setattr("app.services.watermark_detect.analyze_video_watermarks_vlm", mock_analyze)

    det_res = detect_timed_watermarks_hybrid(vid_path)
    assert det_res.ok is True
    assert det_res.stats["method"] == "hybrid_vlm"
    assert len(det_res.detections) == 1
    assert det_res.detections[0].box == (0, 0, 20, 20)
    assert det_res.frame_tracks is not None

