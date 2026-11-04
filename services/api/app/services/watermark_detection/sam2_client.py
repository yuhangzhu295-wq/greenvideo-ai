"""SAM 2.1 worker client - sends prompts, receives pixel masks.

Talks to the persistent ``services/ai-worker/sam2_worker.py`` process over
stdin/stdout, exactly like ``LamaWorker``. The FastAPI main environment never
imports torch or sam2: segmentation runs in the isolated AI worker.
"""
from __future__ import annotations

import base64
import json
import queue
import subprocess
import threading
from pathlib import Path

from app.services.video_repair import AiServiceError


class Sam2Worker:
    """Persistent SAM 2.1 process: box/polygon prompt -> pixel mask."""

    _worker_script = (
        Path(__file__).resolve().parent.parent.parent.parent.parent
        / "ai-worker" / "sam2_worker.py"
    )
    _worker_python = (
        Path(__file__).resolve().parent.parent.parent.parent.parent
        / "ai-worker" / ".venv" / "Scripts" / "python.exe"
    )

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._queue: queue.Queue[str] | None = None

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, timeout_s: float = 90.0) -> None:
        if not (self._worker_script.exists() and self._worker_python.exists()):
            raise AiServiceError("SAM2 worker 脚本或 ai-worker 解释器不存在")
        self._proc = subprocess.Popen(
            [str(self._worker_python), str(self._worker_script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            cwd=str(self._worker_script.parent),
        )
        self._queue = queue.Queue()

        def _enqueue(pipe, q):
            try:
                for line in iter(pipe.readline, ""):
                    q.put(line)
            except Exception:
                pass
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        t = threading.Thread(target=_enqueue, args=(self._proc.stdout, self._queue), daemon=True)
        t.start()

        try:
            ready_line = self._queue.get(timeout=timeout_s)
        except Exception as exc:
            self.stop()
            raise AiServiceError(f"SAM2 worker 启动超时 ({timeout_s}s)") from exc

        try:
            ready = json.loads(ready_line.strip() or "{}")
        except Exception as exc:
            self.stop()
            raise AiServiceError(f"SAM2 worker 响应格式错误：{ready_line[:120]}") from exc

        if not ready.get("ready"):
            self.stop()
            raise AiServiceError(f"SAM2 worker 启动失败：{ready}")

    def _request(self, payload: dict, timeout_s: float = 300.0) -> dict:
        if not self.is_alive():
            raise AiServiceError("SAM2 worker 进程已终止")
        req = json.dumps(payload)
        try:
            self._proc.stdin.write(req + "\n")
            self._proc.stdin.flush()
        except Exception as exc:
            self.stop()
            raise AiServiceError(f"向 SAM2 worker 发送请求失败：{exc}") from exc

        try:
            line = self._queue.get(timeout=timeout_s)
        except Exception as exc:
            self.stop()
            raise AiServiceError(f"SAM2 worker 推理超时 ({timeout_s}s)") from exc

        if not line:
            self.stop()
            raise AiServiceError("SAM2 worker 已退出")
        resp = json.loads(line.strip() or "{}")
        if "error" in resp:
            raise AiServiceError(f"SAM2 worker 推理失败：{resp['error']}")
        return resp

    def segment_frame(self, frame, box=None, points=None, labels=None) -> dict:
        """Segment one frame. Returns {"mask": uint8 HxW, "score": float, "area": int}."""
        import cv2
        import numpy as np

        ok, buf = cv2.imencode(".png", frame)
        if not ok:
            raise AiServiceError("帧编码失败")
        payload = {
            "type": "segment_image",
            "image_b64": base64.b64encode(buf.tobytes()).decode("ascii"),
        }
        if box is not None:
            payload["box"] = [int(v) for v in box]
        if points is not None:
            payload["points"] = [[int(p[0]), int(p[1])] for p in points]
            payload["labels"] = [int(v) for v in (labels or [1] * len(points))]

        resp = self._request(payload)
        mask_png = base64.b64decode(resp["masks"][0])
        mask = cv2.imdecode(np.frombuffer(mask_png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            raise AiServiceError("SAM2 返回 mask 解码失败")
        return {"mask": mask, "score": float(resp["scores"][0]), "area": int(resp["areas"][0])}

    def propagate_video(self, video_path: str, prompts: dict, frame_indices=None) -> dict:
        """Propagate mask across a video. prompts = {frame_idx: {"box": [...]}}.

        Returns {frame_idx: {"mask": uint8 array, "area": int}}.
        """
        payload = {
            "type": "propagate_video",
            "video_path": str(video_path),
            "prompts": {str(k): v for k, v in prompts.items()},
        }
        if frame_indices is not None:
            payload["frame_indices"] = [int(i) for i in frame_indices]
        resp = self._request(payload, timeout_s=900.0)

        import cv2
        import numpy as np

        out = {}
        for k, item in resp.get("frames", {}).items():
            mask_png = base64.b64decode(item["mask_b64"])
            mask = cv2.imdecode(np.frombuffer(mask_png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if mask is not None:
                out[int(k)] = {"mask": mask, "area": int(item["area"])}
        return out

    def stop(self) -> None:
        if self._proc is not None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                if self._proc.stdout:
                    self._proc.stdout.close()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                    self._proc.wait(timeout=2)
                except Exception:
                    pass
            self._proc = None
