"""Persistent STTN video inpainting worker.

STTN (Spatial-Temporal Transformer Networks, researchmm/STTN, MIT code)
repairs a *window* of frames in one forward pass, attending across time —
this is what gives temporal consistency that per-frame LaMa + EMA blending
cannot. Per-frame repair engines stay the fallback for static backgrounds.

Protocol (stdin/stdout JSON lines, same shape as the other AI workers):

    {"op": "load", "weights": "<path>"}   -> {"ok": true, "device": "cuda"}
    {"op": "ping"}                        -> {"ok": true, "loaded": bool}
    {"op": "repair_clip",
     "frames_b64": ["<b64 png>", ...],
     "masks_b64":  ["<b64 png>", ...],
     "chunk": 40, "overlap": 8}
        -> {"ok": true, "frames_b64": [...], "engine": "sttn",
            "chunks": n, "chunk_sizes": [...]}
    {"op": "stop"} / EOF -> exit

Guarantees:
- Non-mask pixels are bit-identical to the input (composited, never resized).
- OOM shrinks the chunk and retries; below the minimum chunk it FAILS the
  request explicitly instead of silently switching engines.
- Model weights must be supplied by the caller. The official STTN release
  weights are CC-BY-NC 4.0 (see docs/THIRD_PARTY_AI_LICENSE_AUDIT.md) and may
  be loaded for benchmarking only — production needs self-trained or other
  permissively licensed weights.

Run with the ai-worker venv interpreter (torch + CUDA live there):
    python services/sttn-worker/sttn_worker.py
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

# The vendored STTN package lives next to this worker.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# STTN is trained and evaluated at this resolution; other sizes still run but
# the pre-trained weights' attention statistics degrade outside it.
STTN_WORK_RES = (432, 240)  # (w, h)
MIN_CHUNK = 4

_IMREAD_COLOR = 1  # cv2.IMREAD_COLOR
_IMREAD_GRAY = 0  # cv2.IMREAD_GRAYSCALE


def _decode_b64_image(data: str, flags: int):
    import cv2
    import numpy as np

    return cv2.imdecode(
        np.frombuffer(base64.b64decode(data), dtype=np.uint8), flags
    )


def _encode_b64_image(img) -> str:
    import cv2

    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("frame encode failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _infer_window(model, frames, masks, device):
    """Run STTN over one window; returns repaired frames (BGR, full res).

    frames: list of BGR uint8 (H, W, 3); masks: list of uint8 (H, W), >0 on
    watermark pixels. Both same length T. Output repaired pixels are composited
    ONLY inside the mask; everything else is returned untouched.
    """
    import cv2
    import numpy as np
    import torch

    h, w = frames[0].shape[:2]
    tw, th = STTN_WORK_RES
    rs_frames = [cv2.resize(f, (tw, th), interpolation=cv2.INTER_AREA) for f in frames]
    rs_masks = [cv2.resize(m, (tw, th), interpolation=cv2.INTER_NEAREST) for m in masks]

    # STTN expects RGB in [-1, 1], shape (1, T, C, H, W); masks (1, T, 1, H, W).
    fr = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2RGB) for f in rs_frames], axis=0)
    fr = fr.astype(np.float32) / 127.5 - 1.0
    mk = np.stack(rs_masks, axis=0).astype(np.float32) / 255.0
    # The model is trained on PRE-MASKED input: the hole region must be zeroed
    # (frames * (1 - mask)), exactly like the official inference pipeline.
    # Feeding unmasked frames leaves the watermark visible and the model
    # hallucinates instead of reconstructing.
    fr = fr * (1.0 - mk[..., None])
    fr_t = torch.from_numpy(fr).permute(0, 3, 1, 2).unsqueeze(0).to(device)
    mk_t = torch.from_numpy(mk).unsqueeze(1).unsqueeze(0).to(device)

    with torch.inference_mode():
        pred = model(fr_t, mk_t)  # (1, T, 3, H, W), tanh output
    pred = pred.squeeze(0).cpu().numpy()  # (T, 3, H, W) in [-1, 1]
    pred = ((pred + 1.0) * 127.5).clip(0, 255).astype(np.uint8)
    pred = pred.transpose(0, 2, 3, 1)  # (T, H, W, 3) RGB

    repaired = []
    for i in range(len(frames)):
        rec_rgb = cv2.resize(pred[i], (w, h), interpolation=cv2.INTER_CUBIC)
        rec_bgr = cv2.cvtColor(rec_rgb, cv2.COLOR_RGB2BGR)
        # Composite: original pixels everywhere except the mask. This holds
        # outside-mask output bit-identical to the input even though the
        # repair ran at reduced resolution.
        m = masks[i] > 0
        out = frames[i].copy()
        out[m] = rec_bgr[m]
        repaired.append(out)
    return repaired


def _repair_clip(model, frames, masks, chunk: int, overlap: int, device):
    """Chunked window repair with linear overlap blending.

    Windows advance by (chunk - overlap) frames; the overlap zone between two
    consecutive windows is blended with a linear ramp so the seam cannot
    flicker. Mask-compositing keeps non-watermark pixels exact. Window
    planning and blending live in chunk_plan (unit-testable without a GPU).
    """
    import torch
    from chunk_plan import blend_zone, plan_windows

    total = len(frames)
    if total == 0:
        raise RuntimeError("empty clip")
    if len(masks) != total:
        raise RuntimeError("frames/masks length mismatch")

    chunk = max(MIN_CHUNK, int(chunk))
    overlap = max(0, min(int(overlap), chunk // 2))
    outputs: list = [None] * total
    chunk_sizes: list[int] = []

    for start, end, blend_count in plan_windows(total, chunk, overlap, MIN_CHUNK):
        try:
            rep = _infer_window(model, frames[start:end], masks[start:end], device)
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            smaller = max(MIN_CHUNK, (end - start) // 2)
            if smaller >= end - start:
                raise
            # Restart the whole clip on the smaller chunk size: mid-clip
            # replanning would mix two overlap layouts and break the ramp.
            return _repair_clip(model, frames, masks, smaller, min(overlap, smaller // 2), device)
        chunk_sizes.append(len(rep))

        for li in range(blend_count, len(rep)):
            outputs[start + li] = rep[li]
        for li in range(blend_count):
            gi = start + li
            prev_out = outputs[gi]
            if prev_out is None:
                outputs[gi] = rep[li]
                continue
            alpha = (li + 1) / (blend_count + 1)
            outputs[gi] = blend_zone(prev_out, rep[li], masks[gi], alpha)
    return outputs, chunk_sizes


def main() -> int:
    import torch
    from sttn.sttn_model import InpaintGenerator

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = None
    print(json.dumps({"ready": True, "device": device, "engine": "sttn"}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            op = req.get("op")
            if op == "ping":
                print(json.dumps({"ok": True, "loaded": model is not None}), flush=True)
                continue
            if op == "load":
                weights = req.get("weights")
                if not weights or not Path(weights).exists():
                    print(json.dumps({"error": f"weights not found: {weights}"}), flush=True)
                    continue
                model = InpaintGenerator().to(device)
                state = torch.load(weights, map_location=device, weights_only=True)
                # Official releases wrap the generator as {"netG": state_dict}.
                if isinstance(state, dict) and "netG" in state and "weight" not in state:
                    state = state["netG"]
                model.load_state_dict(state)
                model.eval()
                print(json.dumps({"ok": True, "device": device}), flush=True)
                continue
            if op == "repair_clip_file":
                if model is None:
                    print(json.dumps({"error": "model not loaded; send op=load first"}), flush=True)
                    continue
                # Full clips are far too large for a base64 JSON line; the
                # caller stages frames/masks in an .npz and hands over paths.
                src_path = req.get("npz")
                out_path = req.get("out_npz")
                if not src_path or not Path(src_path).exists():
                    print(json.dumps({"error": f"npz not found: {src_path}"}), flush=True)
                    continue
                if not out_path:
                    print(json.dumps({"error": "out_npz is required"}), flush=True)
                    continue
                import numpy as np

                blob = np.load(src_path)
                frames = [blob["frames"][i] for i in range(blob["frames"].shape[0])]
                masks = [blob["masks"][i] for i in range(blob["masks"].shape[0])]
                rep, sizes = _repair_clip(
                    model, frames, masks, req.get("chunk", 40), req.get("overlap", 8), device
                )
                np.savez_compressed(
                    out_path,
                    frames=np.stack(rep, axis=0),
                    engine=np.array("sttn"),
                    chunks=np.array(len(sizes)),
                    chunk_sizes=np.array(sizes),
                )
                print(
                    json.dumps({"ok": True, "out_npz": out_path, "chunks": len(sizes)}),
                    flush=True,
                )
                continue
            if op == "repair_clip":
                if model is None:
                    print(json.dumps({"error": "model not loaded; send op=load first"}), flush=True)
                    continue
                frames = []
                for data in req["frames_b64"]:
                    fr = _decode_b64_image(data, _IMREAD_COLOR)
                    if fr is None:
                        raise RuntimeError("frame decode failed")
                    frames.append(fr)
                masks = []
                for data in req["masks_b64"]:
                    mk = _decode_b64_image(data, _IMREAD_GRAY)
                    if mk is None:
                        raise RuntimeError("mask decode failed")
                    masks.append(mk)
                rep, sizes = _repair_clip(
                    model, frames, masks, req.get("chunk", 40), req.get("overlap", 8), device
                )
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "frames_b64": [_encode_b64_image(f) for f in rep],
                            "engine": "sttn",
                            "chunks": len(sizes),
                            "chunk_sizes": sizes,
                        }
                    ),
                    flush=True,
                )
                continue
            if op == "stop":
                break
            print(json.dumps({"error": f"unknown op: {op}"}), flush=True)
        except Exception as exc:  # keep the worker alive across bad requests
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
