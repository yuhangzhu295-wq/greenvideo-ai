# GreenVideo AI - Media Processing and Background Task Analysis

This report outlines the exploration findings for the media processing services and the background Celery task setup of the GreenVideo AI project.

---

## 1. Celery Worker Setup (`celery_app.py` & `tasks.py`)

### `celery_app.py`
The Celery application is instantiated and configured in `services/api/app/workers/celery_app.py`.
- **App Name**: `"greenvideo"`
- **Broker URL**: Configured dynamically via `settings.celery_broker_url` (defaults to `redis://localhost:6379/0`).
- **Result Backend**: Configured dynamically via `settings.celery_result_backend` (defaults to `redis://localhost:6379/1`).
- **Configuration Defaults**:
  - `task_serializer = "json"`
  - `result_serializer = "json"`
  - `accept_content = ["json"]`
  - `timezone = "UTC"`
  - `enable_utc = True`

### `tasks.py`
The only background Celery task defined on the worker side is in `services/api/app/workers/tasks.py`:
- **Task Name**: `"greenvideo.demo_process_task"`
- **Implementation**:
  ```python
  @celery_app.task(name="greenvideo.demo_process_task")
  def demo_process_task(task_id: str) -> dict:
      update_task(task_id, status="success", progress=100, stage="处理完成")
      return {"task_id": task_id, "status": "success"}
  ```
- **Observations**:
  - This Celery task is **not** imported or called anywhere in the FastAPI application endpoints or routers. It exists solely as a standalone demo/worker entry point.
  - The task, when run, directly updates the SQLite database task record (using `update_task` helper) to set progress to 100%, stage to "处理完成" (Processing Complete), and status to `"success"`.

---

## 2. Media, Downloader, and Watermark Services (`services/`)

### Downloader Service (`services/downloader.py`)
- **Main Class**: `VideoDownloader`
- **Method `parse_url`**:
  - Validates if the URL is public via `validate_public_http_url(url)`.
  - Checks if the CLI tool `yt-dlp` is available on the host system using `shutil.which("yt-dlp")`.
  - If `yt-dlp` is installed, it runs a subprocess command to parse metadata in JSON format.
  - **Error handling**: Detects specific restricted scenarios (e.g., copyright, login requirements, DRM, private, cookies, CAPTCHA) by scanning the output/error for `PROTECTED_ERROR_MARKERS`. If found, a business-safe `DownloadError` is raised.
  - **Fallback**: If `yt-dlp` is not installed or raises an unexpected error, the service falls back to `_demo_result(url)` which generates a mock `ParseResultDTO` containing preset video formats (1080p, 720p, mp3) and a placeholder thumbnail.

### Media Service (`services/media.py`)
- **Functions**:
  - `ffmpeg_available() -> bool`: Uses `shutil.which("ffmpeg")` to check FFmpeg availability.
  - `probe_video(path) -> dict`: Checks if `ffprobe` is installed via `shutil.which("ffprobe")`. If not, returns a fallback dictionary: `{"duration": 0, "width": 0, "height": 0, "fallback": True}`. If available, runs `ffprobe` using a JSON format output format.
  - `extract_audio(input_path, output_mp3)`: Extracts audio from a video using FFmpeg. If FFmpeg is not available, it writes a placeholder text file containing `"demo mp3 placeholder"` and returns.
  - `make_thumbnail(input_path, output_jpg)`: Generates a thumbnail image from the video frame at 00:00:01. If FFmpeg is not available, it writes a placeholder text file containing `"demo thumbnail placeholder"` and returns.
  - `extract_frames(input_path, frames_dir)`: Extracts all frames from a video as PNG images into a directory. If FFmpeg is not available, writes a `"demo frame placeholder"` text file to `frame_000001.txt` inside the target directory.
  - `assemble_video(frames_dir, audio_path, output_mp4, fps=25)`: Reconstructs a video from PNG frames and optionally mixes in audio. If FFmpeg is not available, writes a `"demo mp4 placeholder"` text file.

### Watermark Processing Service (`services/watermark.py`)
- **Main Class**: `WatermarkProcessor`
- **Dynamic Imports**:
  - OpenCV (`cv2`) and NumPy (`numpy`) are imported dynamically and checked via `_check_cv2()` and `_check_np()` to prevent startup crashes when these libraries are missing.
- **Mask Generation**:
  - `_generate_mask(image_shape, mask_regions)` generates a binary mask where the regions to be removed are filled with 255 (white).
  - If no rectangular `mask_regions` are provided, it defaults to masking the typical Douyin/TikTok watermark area (bottom right corner: 78%-100% height, 72%-100% width).
- **Inpainting Modes**:
  - **Standard Mode (OpenCV)**: Traditional image inpainting using `cv2.inpaint(image, mask, 3, cv2.INPAINT_TELEA)`.
  - **AI Mode (IOPaint)**: Connects to a locally running IOPaint HTTP service (`http://127.0.0.1:8080/api/inpaint`) using `httpx.post`. The image and mask are base64-encoded in the request body. If the IOPaint service is down or fails, it falls back to standard OpenCV inpainting.
- **Video Watermark Removal (`process_video`)**:
  - Checks if both `ffmpeg` (via `shutil.which`) and `cv2`/`numpy` are available. If not, it executes in **Demo Mode**:
    1. Sequentially invokes the progress callback with mock status values (10% -> 62% -> 90% -> 100%).
    2. Copies the original input video directly to the output path via `shutil.copyfile`.
  - If libraries and tools are present:
    1. Uses `cv2.VideoCapture` to read frames, extracting FPS, resolution, and total frames.
    2. Limits processing to 300 frames in AI mode to avoid high latency.
    3. Runs standard or AI inpainting frame-by-frame.
    4. Writes the processed frames using `cv2.VideoWriter` (with `"mp4v"` codec).
    5. Appends the remaining original frames if the processing frame count was capped.

---

## 3. CLI Command Construction and Fallback Mechanisms

### Command Construction
All CLI calls are constructed safely as list of strings, preventing shell injection:
- **yt-dlp**:
  ```python
  ["yt-dlp", "--dump-json", "--no-playlist", "--skip-download", "--no-warnings", url]
  ```
- **ffprobe**:
  ```python
  ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
  ```
- **FFmpeg (Audio extraction)**:
  ```python
  ["ffmpeg", "-y", "-i", str(input_path), "-vn", "-codec:a", "libmp3lame", str(output)]
  ```
- **FFmpeg (Thumbnail extraction)**:
  ```python
  ["ffmpeg", "-y", "-ss", "00:00:01", "-i", str(input_path), "-frames:v", "1", str(output)]
  ```
- **FFmpeg (Frame extraction)**:
  ```python
  ["ffmpeg", "-y", "-i", str(input_path), str(frames / "frame_%06d.png")]
  ```
- **FFmpeg (Video assembly)**:
  ```python
  ["ffmpeg", "-y", "-framerate", str(fps), "-i", str(Path(frames_dir) / "frame_%06d.png")]
  # Conditional: ["-i", str(audio_path), "-shortest"]
  # Final output: ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(output)]
  ```

### CLI Fallback Mechanisms
- **yt-dlp Fallback**: If the `yt-dlp` executable is not found in the system PATH, the application falls back silently to returning a mock result (`_demo_result`) instead of failing.
- **FFmpeg/ffprobe Fallback**: If `ffmpeg` or `ffprobe` is not found, the media functions write placeholder text files (e.g., `"demo mp3 placeholder"`) or return a dictionary indicating a fallback (`{"duration": 0, "fallback": True}`).
- **OpenCV/NumPy Fallback**: If `cv2` or `numpy` are not installed, the watermark processor copies the input file directly to the output path using `shutil.copyfile` and returns.
- **IOPaint AI Fallback**: If AI mode is selected but the IOPaint service is not running or returns a non-200 status code, the system falls back to standard OpenCV-based inpainting (`cv2.inpaint`).

---

## 4. Celery Configuration, Queue Names, and Status Polling

### Celery Configuration
- The app configuration is defined in `celery_app.py` as detailed in Section 1.
- Redis is utilized as the message broker (`redis://localhost:6379/0`) and result database (`redis://localhost:6379/1`).

### Queue Names
- No custom queues are configured or routed in `celery_app.py` or the task decorators. Thus, all tasks run on the default queue (named `"celery"`).
- *Production Note:* `AI_INPAINT_SETUP.md` recommends using a dedicated separate queue for heavy AI restoration tasks to prevent blocking faster, standard download/parsing tasks.

### Task Status Polling
1. When a task is created (via `/api/tasks/download` or `/api/tasks/watermark`), a database record is inserted into the local SQLite `tasks` table.
2. In the current FastAPI backend implementation, **these tasks are not pushed to Celery**. Instead, the endpoints immediately update the SQLite record to `status="processing"`, `progress=62`, and a stage (e.g. `"下载处理中"` or `"AI修复中"`).
3. The frontend client polls the task status via `GET /api/tasks/{task_id}`.
4. The router function `get_task_status` reads the SQLite database record.
5. If the database record indicates a progress >= 100% or status `"success"`, it sets status to `"success"` and returns a local download URL (`/api/files/{task_id}`).
6. Since Celery tasks are never invoked by the endpoint, the progress remains at 62% indefinitely (acting as a mock processing state) unless an external Celery worker or script updates the database record.
