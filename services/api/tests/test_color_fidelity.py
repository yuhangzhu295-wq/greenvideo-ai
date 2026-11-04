"""Regression test verifying LamaWorker color fidelity (RGB/BGR channel order)."""
import cv2
import numpy as np
import pytest

from app.services.video_repair import LamaWorker


def test_lama_worker_preserves_color_channels():
    """LamaWorker must preserve color channels: warm/red tones must not flip to blue."""
    worker = LamaWorker()
    try:
        worker.start()
    except Exception as exc:
        pytest.skip(f"LamaWorker not available on this environment: {exc}")

    try:
        # Create a 200x200 warm-toned image (R=160, G=100, B=40)
        h, w = 200, 200
        canvas = np.zeros((h, w, 3), dtype=np.uint8)
        canvas[:, :] = [40, 100, 160]  # BGR order: B=40, G=100, R=160

        # Draw a synthetic white watermark in the center
        cv2.putText(canvas, "TEST_WM", (20, 110), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 3)

        # Mask only the watermark text
        mask = np.zeros((h, w), dtype=np.uint8)
        mask[80:130, 15:185] = 255

        inpainted = worker.inpaint(canvas, mask)
        assert inpainted is not None
        assert inpainted.shape == canvas.shape

        # Measure BGR channels in the inpainted center region
        center_patch = inpainted[90:120, 30:170]
        mean_b = float(center_patch[:, :, 0].mean())
        mean_r = float(center_patch[:, :, 2].mean())

        # Red channel must be significantly higher than blue channel (preserving warm tone)
        # If BGR was inverted to RGB, B would be ~160 and R would be ~40!
        assert mean_r > mean_b, (
            f"Color channel inverted! Expected R > B, got R={mean_r:.1f}, B={mean_b:.1f}"
        )
        assert abs(mean_r - 160.0) < 45.0, f"R channel drifted too far: {mean_r}"
        assert abs(mean_b - 40.0) < 45.0, f"B channel drifted too far: {mean_b}"
    finally:
        worker.stop()
