"""Persistent LaMa inpainting worker.

Spawning IOPaint's HTTP server per video and calling it per frame costs
hundreds of milliseconds of PNG/base64/HTTP/Python overhead per request while
the GPU forward pass itself takes ~60ms. This worker loads the model once and
serves frames over stdin/stdout line protocol:

    request:  {"image": "<b64 png>", "mask": "<b64 png>"}\n
    response: {"image": "<b64 png>"}\n   or   {"error": "..."}\n

Run with the ai-worker venv interpreter (torch + CUDA live there).
"""
from __future__ import annotations

import base64
import json
import sys


def main() -> int:
    import cv2
    import numpy as np
    import torch
    from iopaint.model.lama import LaMa
    from iopaint.schema import HDStrategy, InpaintRequest

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = LaMa(device)
    config = InpaintRequest(
        hd_strategy=HDStrategy.ORIGINAL,
        sd_keep_unmasked_area=True,
    )
    print(json.dumps({"ready": True, "device": device}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            img = cv2.imdecode(
                np.frombuffer(base64.b64decode(req["image"]), dtype=np.uint8),
                cv2.IMREAD_COLOR,
            )
            mask = cv2.imdecode(
                np.frombuffer(base64.b64decode(req["mask"]), dtype=np.uint8),
                cv2.IMREAD_GRAYSCALE,
            )
            if img is None or mask is None:
                print(json.dumps({"error": "decode failed"}), flush=True)
                continue
            # Inference-only: without this guard torch builds an autograd graph,
            # which doubles memory and slows every forward pass severalfold.
            with torch.inference_mode():
                result = model(img, mask, config)
            ok, buf = cv2.imencode(".png", result)
            if not ok:
                print(json.dumps({"error": "encode failed"}), flush=True)
                continue
            print(
                json.dumps({"image": base64.b64encode(buf.tobytes()).decode("ascii")}),
                flush=True,
            )
        except Exception as exc:  # keep the worker alive across bad requests
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
