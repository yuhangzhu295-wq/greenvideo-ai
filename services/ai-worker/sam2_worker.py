"""Persistent SAM 2.1 segmentation worker.

Serves pixel-level watermark masks over stdin/stdout line protocol. The FastAPI
backend sends detection prompts (boxes / polygons from PaddleOCR or overlay
detectors); this worker returns precise pixel masks - never bounding
rectangles. Two operation modes:

1. ``segment_image`` - single-frame SAM2ImagePredictor segmentation. Used for
   key frames, static watermarks, images and re-anchoring after a track jump.
2. ``propagate_video`` - SAM2VideoPredictor mask propagation. Used to carry a
   key-frame mask across a frame window with real per-pixel tracking instead of
   linear interpolation between boxes.

Run with the ai-worker venv interpreter (torch + CUDA + sam2 live there):

    python sam2_worker.py

Protocol
--------
request:  {"type": "segment_image", "image_b64": "...", "box": [x1,y1,x2,y2],
           "polygon": [[x,y],...], "points": [[x,y],...], "labels": [1,...]}\n
          {"type": "propagate_video", "video_path": "...", "frame_indices": [i,...],
           "prompts": {"k": {"box": [...]}}}\n
response: {"ok": true, "masks": ["<b64 png>", ...], "scores": [...],
           "ious": [...], "areas": [...]}\n
          {"error": "..."}\n
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("SAM2_DISABLE_FLASH_ATTENTION", "1")

_DIR = os.path.dirname(os.path.abspath(__file__))
_CHECKPOINT = os.path.join(_DIR, "checkpoints", "sam2.1_hiera_large.pt")
_CONFIG = "configs/sam2.1/sam2.1_hiera_l.yaml"


def _decode_image(b64: str):
    import base64

    import cv2
    import numpy as np

    buf = base64.b64decode(b64)
    return cv2.imdecode(np.frombuffer(buf, dtype=np.uint8), cv2.IMREAD_COLOR)


def _encode_mask(mask) -> str:
    import base64

    import cv2
    import numpy as np

    arr = (np.asarray(mask) > 0).astype(np.uint8) * 255
    ok, buf = cv2.imencode(".png", arr)
    if not ok:
        raise RuntimeError("mask encode failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _run_segment_image(image_predictor, req) -> dict:
    import numpy as np

    img = _decode_image(req["image_b64"])
    # BGR -> RGB; .copy() materialises positive strides for torch.from_numpy.
    rgb = img[:, :, ::-1].copy()
    image_predictor.set_image(rgb)

    kwargs = {"multimask_output": False}
    if req.get("box"):
        kwargs["box"] = np.array(req["box"], dtype=np.float32)
    if req.get("points"):
        kwargs["point_coords"] = np.array(req["points"], dtype=np.float32)
        labels = req.get("labels") or [1] * len(req["points"])
        kwargs["point_labels"] = np.array(labels, dtype=np.int32)

    masks, scores, logit_ious = image_predictor.predict(**kwargs)
    best = int(np.argmax(scores)) if len(scores) > 1 else 0
    mask = masks[best]
    iou = None
    if logit_ious is not None and len(logit_ious) > 0:
        try:
            iou = float(np.asarray(logit_ious).reshape(-1)[best])
        except (IndexError, ValueError, TypeError):
            iou = None
    return {
        "ok": True,
        "masks": [_encode_mask(mask)],
        "scores": [float(scores[best])],
        "ious": [iou],
        "areas": [int((mask > 0).sum())],
    }


def _run_propagate_video(video_model, req) -> dict:
    import contextlib
    import numpy as np
    import torch

    video_path = req["video_path"]
    prompts = req.get("prompts", {})  # {frame_idx: {"box": [...]} }
    frame_filter = req.get("frame_indices")
    wanted = set(int(i) for i in frame_filter) if frame_filter is not None else None

    # Autocast to bfloat16 on CUDA: SAM2 video attention uses bfloat16 internally,
    # and adding prompts on multiple different keyframes creates float32
    # embeddings for unobserved steps. Autocasting unifies the dtypes and
    # prevents "mat1 and mat2 must have the same dtype (BFloat16 and Float)".
    autocast_ctx = (
        torch.autocast(device_type="cuda", dtype=torch.bfloat16)
        if torch.cuda.is_available()
        else contextlib.nullcontext()
    )

    with torch.inference_mode(), autocast_ctx:
        state = video_model.init_state(video_path)
        try:
            # Support multiple objects per frame (multi-watermark): each prompt
            # entry gets its own obj_id so SAM2 tracks them independently.
            obj_id = 0
            for f_idx_raw, plist in prompts.items():
                f_idx = int(f_idx_raw)
                entries = plist if isinstance(plist, list) else [plist]
                for p in entries:
                    if p.get("box"):
                        video_model.add_new_points_or_box(
                            state,
                            frame_idx=f_idx,
                            obj_id=obj_id,
                            box=np.array(p["box"], dtype=np.float32),
                        )
                        obj_id += 1
                    elif p.get("points"):
                        labels = p.get("labels") or [1] * len(p["points"])
                        video_model.add_new_points_or_box(
                            state,
                            frame_idx=f_idx,
                            obj_id=obj_id,
                            points=np.array(p["points"], dtype=np.float32),
                            labels=np.array(labels, dtype=np.int32),
                        )
                        obj_id += 1

            start_f = min(wanted) if wanted else None
            max_f = len(wanted) if wanted else None
            results: dict[str, dict] = {}
            for frame_idx, _obj_ids, mask_logits in video_model.propagate_in_video(
                state, start_frame_idx=start_f, max_frame_num_to_track=max_f
            ):
                f = int(frame_idx)
                if wanted is not None and f not in wanted:
                    continue
                # OR all object masks into one combined watermark mask
                combined = np.zeros_like((mask_logits[0] > 0.0).cpu().numpy())
                for obj_mask_logit in mask_logits:
                    m = (obj_mask_logit > 0.0).cpu().numpy()
                    combined = combined | m
                combined = np.squeeze(combined)  # ensure (H, W) for cv2.imencode
                results[str(f)] = {
                    "mask_b64": _encode_mask(combined),
                    "area": int(combined.sum()),
                }
            return {"ok": True, "frames": results}
        finally:
            video_model.reset_state(state)


def main() -> int:
    import torch
    from sam2.build_sam import build_sam2, build_sam2_video_predictor
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    device = "cuda" if torch.cuda.is_available() else "cpu"
    image_model = build_sam2(_CONFIG, _CHECKPOINT, device=device)
    image_predictor = SAM2ImagePredictor(image_model)
    video_model = build_sam2_video_predictor(_CONFIG, _CHECKPOINT, device=device)

    print(json.dumps({"ready": True, "device": device}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            req_type = req.get("type")
            if req_type == "segment_image":
                resp = _run_segment_image(image_predictor, req)
            elif req_type == "propagate_video":
                resp = _run_propagate_video(video_model, req)
            else:
                resp = {"error": f"unknown type {req_type}"}
            print(json.dumps(resp), flush=True)
        except Exception as exc:  # keep worker alive across bad requests
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
