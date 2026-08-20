# Third Party Notices

This file records the upstream checks performed before repairing the GreenVideo AI codebase. The checks used the official GitHub repositories listed below on 2026-06-29.

| Project | Use in GreenVideo AI | License / status verified | Official source |
| --- | --- | --- | --- |
| Taro | React + TypeScript WeChat miniapp framework for `apps/miniapp` | MIT | https://github.com/NervJS/taro |
| FastAPI | REST API service, validation, routing | MIT | https://github.com/fastapi/fastapi |
| Uvicorn | ASGI server for FastAPI | BSD-style | https://github.com/Kludex/uvicorn |
| Celery | Optional async worker and queue abstraction | BSD-3-Clause | https://github.com/celery/celery |
| Redis | Optional Celery broker/result backend through Docker Compose | Redis 7.x remains BSD-3-Clause; current Redis 8+ upstream is tri-licensed under RSALv2, SSPLv1, or AGPLv3. This project does not copy Redis source and should pin Redis 7.x if BSD terms are required. | https://github.com/redis/redis |
| yt-dlp | Public video metadata parsing and authorized downloads | Unlicense | https://github.com/yt-dlp/yt-dlp |
| FFmpeg | Optional media probing, frame/audio handling, demo media generation through CLI calls | FFmpeg is primarily LGPL-2.1+ by default; GPL components are optional. GreenVideo AI does not use GPL-only filters such as `delogo`. | https://github.com/FFmpeg/FFmpeg |
| OpenCV | Default local mask-based image/video inpainting when installed | Apache-2.0 | https://github.com/opencv/opencv |
| PaddleOCR | Optional OCR-assisted watermark/subtitle region detection; not required by default | Apache-2.0 | https://github.com/PaddlePaddle/PaddleOCR |
| IOPaint | Optional advanced inpainting service; no model weights included | Apache-2.0; repository is archived, so use as optional service only and keep OpenCV fallback enabled. | https://github.com/Sanster/IOPaint |

## Compliance Notes

- yt-dlp is called only with public URL metadata/download commands. The project does not import cookies, use login state, bypass CAPTCHA, bypass DRM, or use proxy pools to evade platform restrictions.
- FFmpeg is invoked with argument lists and `shell=False`. The default watermark repair path is OpenCV mask inpainting, not FFmpeg `delogo`.
- OpenCV, PaddleOCR, and IOPaint are optional runtime capabilities. Missing optional components must not break API health, demo parsing, task creation, or the local fallback verification flow.
- No third-party source code, pretrained model weights, cookies, account data, or private platform credentials are copied into this repository.
