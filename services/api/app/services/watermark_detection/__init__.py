"""Pixel-level watermark detection, segmentation and video tracking.

This package replaces the rectangle-mask pipeline. Detection prompts (OCR
polygons / detector boxes) are segmented into precise pixel masks with SAM 2.1
and propagated across frames with the SAM2 video predictor, so the repair
engine only ever sees the actual watermark pixels - never a full bounding
rectangle.

Modules
-------
- ``text_detector``   PaddleOCR text discovery (prompt producer, not mask)
- ``mask_segmenter``  SAM 2.1 box/polygon prompt -> pixel mask + confidence
- ``video_tracker``   SAM2 video mask propagation with jump-safe segmenting
- ``track_manager``   multi-track lifecycle (fixed / jumping / moving / fade)
- ``pixel_mask_provider``  drop-in ``mask_for(frame_index)`` for repair engine
"""
