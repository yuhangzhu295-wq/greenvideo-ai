from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import httpx

from app.config import settings
from app.schemas import MaskPreset, MaskRegion, RepairMode


def try_import_paddleocr() -> bool:
    try:
        __import__("paddleocr")
    except Exception:
        return False
    return True


def try_iopaint_service() -> bool:
    if not settings.iopaint_enabled:
        return False
    try:
        response = httpx.get(f"{settings.iopaint_base_url}/api/health", timeout=2)
        return response.status_code == 200
    except Exception:
        return False


def is_video_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in {".mp4", ".mov", ".avi"}


class WatermarkProcessor:
    def __init__(self) -> None:
        self._cv2_available: bool | None = None
        self._np_available: bool | None = None

    def _check_cv2(self) -> bool:
        if self._cv2_available is None:
            try:
                import cv2  # noqa: F401

                self._cv2_available = True
            except Exception:
                self._cv2_available = False
        return self._cv2_available

    def _check_np(self) -> bool:
        if self._np_available is None:
            try:
                import numpy as np  # noqa: F401

                self._np_available = True
            except Exception:
                self._np_available = False
        return self._np_available

    def _preset_regions(
        self,
        width: int,
        height: int,
        mask_preset: MaskPreset,
        remove_subtitle: bool,
    ) -> list[MaskRegion]:
        regions: list[MaskRegion] = []
        if mask_preset in {"douyin_top_left", "douyin_both_corners"}:
            regions.append(
                MaskRegion(
                    x=0,
                    y=0,
                    w=max(1, int(width * 0.42)),
                    h=max(1, int(height * 0.18)),
                )
            )
        if mask_preset in {"douyin_bottom_right", "douyin_both_corners"}:
            regions.append(
                MaskRegion(
                    x=max(0, int(width * 0.60)),
                    y=max(0, int(height * 0.78)),
                    w=max(1, int(width * 0.40)),
                    h=max(1, int(height * 0.18)),
                )
            )
        if remove_subtitle:
            regions.append(
                MaskRegion(
                    x=max(0, int(width * 0.18)),
                    y=max(0, int(height * 0.56)),
                    w=max(1, int(width * 0.64)),
                    h=max(1, int(height * 0.20)),
                )
            )
        return regions

    def _generate_mask(
        self,
        image_shape: tuple[int, int] | tuple[int, int, int],
        mask_regions: list[MaskRegion],
        mask_preset: MaskPreset,
        remove_subtitle: bool,
    ):
        import numpy as np

        height, width = int(image_shape[0]), int(image_shape[1])
        mask = np.zeros((height, width), dtype=np.uint8)
        regions = list(mask_regions)
        if mask_preset != "custom":
            regions.extend(self._preset_regions(width, height, mask_preset, remove_subtitle))
        elif remove_subtitle:
            regions.extend(self._preset_regions(width, height, "custom", True))

        for region in regions:
            x1 = min(width, max(0, region.x))
            y1 = min(height, max(0, region.y))
            x2 = min(width, x1 + region.w)
            y2 = min(height, y1 + region.h)
            if x2 > x1 and y2 > y1:
                mask[y1:y2, x1:x2] = 255
        return mask

    def _inpaint_with_opencv(self, image, mask):
        import cv2

        return cv2.inpaint(image, mask, 3, cv2.INPAINT_TELEA)

    def _inpaint_with_iopaint(self, image_path: Path, mask, output_path: Path) -> bool:
        import cv2
        import numpy as np

        try:
            img = cv2.imread(str(image_path))
            if img is None:
                return False

            _, img_encoded = cv2.imencode(".png", img)
            img_b64 = base64.b64encode(img_encoded.tobytes()).decode("utf-8")

            _, mask_encoded = cv2.imencode(".png", mask)
            mask_b64 = base64.b64encode(mask_encoded.tobytes()).decode("utf-8")

            response = httpx.post(
                f"{settings.iopaint_base_url}/api/inpaint",
                json={"image": img_b64, "mask": mask_b64, "model": settings.iopaint_model},
                timeout=120,
            )

            if response.status_code != 200:
                return False
            result = response.json()
            if "image" not in result:
                return False

            result_bytes = base64.b64decode(result["image"])
            result_np = np.frombuffer(result_bytes, dtype=np.uint8)
            result_img = cv2.imdecode(result_np, cv2.IMREAD_COLOR)
            if result_img is None:
                return False
            cv2.imwrite(str(output_path), result_img)
            return True
        except Exception:
            return False

    def process_image(
        self,
        input_path: str | Path,
        mask_regions: list[MaskRegion],
        output_path: str | Path,
        repair_mode: RepairMode = "opencv",
        mask_preset: MaskPreset = "douyin_both_corners",
        remove_subtitle: bool = False,
    ) -> Path:
        input_path = Path(input_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not self._check_cv2() or not self._check_np():
            shutil.copyfile(input_path, output_path)
            return output_path

        import cv2

        image = cv2.imread(str(input_path))
        if image is None:
            shutil.copyfile(input_path, output_path)
            return output_path

        mask = self._generate_mask(image.shape, mask_regions, mask_preset, remove_subtitle)

        if repair_mode == "ai" and try_iopaint_service():
            if self._inpaint_with_iopaint(input_path, mask, output_path):
                return output_path

        repaired = self._inpaint_with_opencv(image, mask)
        cv2.imwrite(str(output_path), repaired)
        return output_path

    def process_video(
        self,
        input_path: str | Path,
        mask_regions: list[MaskRegion],
        output_path: str | Path,
        progress_cb: Callable[[int, str], None] | None = None,
        repair_mode: RepairMode = "opencv",
        mask_preset: MaskPreset = "douyin_both_corners",
        remove_subtitle: bool = False,
    ) -> Path:
        input_path = Path(input_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        ffmpeg_available = shutil.which("ffmpeg") is not None
        cv2_available = self._check_cv2() and self._check_np()
        if not cv2_available:
            if progress_cb:
                progress_cb(20, "OpenCV 未安装，使用文件复制 fallback")
                progress_cb(100, "处理完成")
            shutil.copyfile(input_path, output_path)
            return output_path

        import cv2

        cap = cv2.VideoCapture(str(input_path))
        if not cap.isOpened():
            cap.release()
            if progress_cb:
                progress_cb(20, "视频无法解码，使用文件复制 fallback")
                progress_cb(100, "处理完成")
            shutil.copyfile(input_path, output_path)
            return output_path

        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        total_frames = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if width <= 0 or height <= 0:
            cap.release()
            shutil.copyfile(input_path, output_path)
            return output_path

        mask = self._generate_mask((height, width), mask_regions, mask_preset, remove_subtitle)
        if progress_cb:
            progress_cb(12, "已生成水印区域 mask")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_video = Path(temp_dir) / "video_no_audio.mp4"
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(temp_video), fourcc, fps, (width, height))
            frame_count = 0
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                repaired = self._inpaint_with_opencv(frame, mask)
                writer.write(repaired)
                frame_count += 1
                if progress_cb and frame_count % 15 == 0:
                    progress = 20 + int((frame_count / total_frames) * 65)
                    progress_cb(min(progress, 88), "逐帧修复水印区域")

            cap.release()
            writer.release()

            if ffmpeg_available:
                combined = Path(temp_dir) / "combined.mp4"
                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(temp_video),
                        "-i",
                        str(input_path),
                        "-map",
                        "0:v:0",
                        "-map",
                        "1:a?",
                        "-c:v",
                        "copy",
                        "-c:a",
                        "copy",
                        "-shortest",
                        str(combined),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                    shell=False,
                )
                if combined.exists() and combined.stat().st_size > 0:
                    shutil.copyfile(combined, output_path)
                else:
                    shutil.copyfile(temp_video, output_path)
            else:
                shutil.copyfile(temp_video, output_path)

        if progress_cb:
            progress_cb(100, "处理完成")
        return output_path
