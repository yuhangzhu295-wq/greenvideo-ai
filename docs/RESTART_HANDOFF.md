# GreenVideo AI 重启交接（2026-10-27）

> **状态：本地检查点，未达到最终验收。** 本文件供电脑重启后直接恢复上下文。不要把保存成功理解成独立审查 PASS；当前最新独立审查结论是 **REJECTED**。

## 项目位置与保护约束

- 工作目录：`C:\Users\zyu33\Documents\Codex\greenvideo-ai`
- 工作分支：`recovery/development`；原始基线和 `master`：`da209ee95327f7ac5a681371458791ef5f39b721`。本次仅作**本地提交**，不推送远程。
- 不删除/覆盖既存项目、用户视频、数据库或成果；不使用 `git reset --hard`、`git clean -fd`、强制推送；不绕过登录/DRM/付费访问；不以 demo、仅重编码、空文件或任务完成状态冒充实际修复。
- `.env` 为本地配置，**不得提交**；`services/api/storage/` 和 `artifacts/` 在 `.gitignore` 内，依然存于本机磁盘但不会进入 Git 检查点。`services/api/tests/_test_storage/` 为测试产生的临时目录，不纳入提交。

## 最近进度（现存代码）

1. API 后端已实现真实 IOPaint/LaMa 视频逐帧修复，带按时间片遮罩、自动检测、FFmpeg 视频编码及保留源音频。小程序端已增加分时区域和片尾切换时间确认。对失败的单帧落盘、VideoWriter 未打开、音轨丢失、零帧/零包及碎片化 MP4 无 `nb_frames` 的部分假成功路径增加拒绝逻辑。
2. 最近新增 yt-dlp 真实下载：`ParseResult.url`、旧 SQLite 库的 `url` 列补迁移、解析保存 URL；任务实际下载并核验、MP4/MP3 文件接口分别验证。`_find_ytdlp()` 优先使用 API 虚拟环境内的 yt-dlp。格式列表不再虚构分辨率或文件大小。外部站点可能 TLS 重置/限流；此类失败应明确报错。
3. 已有本机测试：最近一次完整 API 测试 **43 passed, 1 warning**、Ruff 通过；本地 HTTP 确定性下载测试 14 项通过。实际公网 CC-BY Sintel 示例曾下载 MP4 **4,372,373 bytes / 1253 可解码帧 / 2435 音频包**，MP3 **1,457,162 bytes** 并经 API 以 `audio/mpeg` 返回。网络不稳定，不能以一次公网成功推断所有平台可用。
4. 最近一次完成的真实 LaMa 两阶段测试（新下载修改之前）PSNR 平均增益 **+5.47 dB**、最差 **+2.53 dB**、遮罩外 **36.86 dB**、片尾底边条带遮罩覆盖 **100%**；合成自动检测 6/6 PASS。用户视频 `task_e496bdeb01545b84.mp4` / 历史批准产物 `user_result7.mp4` 均为 522 视频包、751 音频包、522 可解码帧，与源视频解码音频 PCM **相同**。
5. 本轮最后一个“完整回归 + LaMa + 合成六场景”命令被取消，**不得说它已经在下载新改动之后整套通过**。重启后应重新执行。小程序新加入“大小未知”展示，也需重跑 lint/typecheck/build。

## 最新独立审查结论：REJECTED（仍需修理）

最新 `code-reviewer` 已亲自验证正常 LaMa 修复与正常视频/音频下载，但找出了以下六项未解决问题（`docs/REVIEW_STATUS.md` 有更详细记录）。在准备重启时**尚未针对这六项完成修复**：

| 优先级 | 未解决的问题及复现事实 | 主要位置 |
| --- | --- | --- |
| P0 | 有音频流头、没有 `mdat` 或可解码音频包的假 `.mp3`，下载任务仍 `completed` 且文件接口交付；必须验证**实际可解码音频帧/包数** > 0 | `watermark.py` `_audio_stream_status`；`tasks.py` 音频下载校验；`files.py` 音频响应 |
| P0 | 已截尾 faststart MP4 头声明 **40 帧**，实际仅 **39 视频包/39 解码帧**；下载任务仍 completed 并交付；不可只检查“至少一帧” | `tasks.py` 下载校验；`files.py` |
| P0 | 同一截尾视频作为源被 OpenCV 修复，输出 39 帧并标记成功；源声明 40 帧，与实际 39 的矛盾当前未检查 | `watermark.py` 视频源验证；`video_repair.py` |
| P1 | 解析结果的实际音频格式 ID（如 `140`）被当视频下载；纯音频直链的 `best` 错标 MP4。应根据解析记录的格式类型决定音频提取/显示 | `downloader.py` 格式选择；`tasks.py` |
| P1 | 下载校验和写入 completed 之间有取消竞态，可能覆盖 canceled 状态 | `tasks.py` |
| P1 | yt-dlp 解析超时、可执行文件启动失败等异常未转成带业务码的 `DownloadError` | `downloader.py` |

**数据哈希勘误**：三份音频实际一致的 PCM SHA256 是 `6224bb08b425dc2c3544bd6b66a28abde6928eb70ddb480e32e652c4de298af9`。之前审查 prompt 曾误写成含 `...3544bd4b66...` 的字面量；那一条不匹配源文件，请勿再次把错误值当真。

## 用户素材与可复现证据（仅本机）

- `services/api/storage/uploads/user_source.mp4`：用户来源，约 3 MB。
- `services/api/storage/test_output/user_result7.mp4`：先前经审查认可的产物，约 4.9 MB；不要覆盖。
- `services/api/storage/files/task_e496bdeb01545b84.mp4`：严格帧数守卫下通过 API 处理的结果，约 4.9 MB；不要覆盖。
- `services/api/storage/greenvideo.db`：任务/解析数据。重启前停止写入并备份；不是 Git 文件。
- `artifacts/`：Ground Truth、用户视频证据和测试报告；被 Git 忽略，不能依赖克隆恢复。
- 本机重启备份目标：`C:\Users\zyu33\Documents\Codex\greenvideo-ai-restart-backup-2026-10-27`。其中保存 Git bundle、`artifacts/`、`services/api/storage/`、SQLite 一致性备份、未入库的本地 `.env`；这是同一电脑上的第二份副本，不是云备份。以检查点命令成功输出和 `backup-manifest.json` 为实际完成依据；如果备份失败，以命令结果为准。
- `scripts/fixtures/smoke_watermark.mp4`：可提交的小测试视频。
- LaMa 权重缓存：`C:\Users\zyu33\.cache\torch\hub\checkpoints\big-lama.pt`；虚拟环境本机保存、不在 Git 内。

## 重启后恢复顺序

1. 在本地项目目录执行 `git status --short`、`git log -1 --oneline` 核对检查点；确认上述三个用户/成果视频和 SQLite 库仍存在。不要执行清理命令。
2. 环境分别位于 `services/api/.venv` 与 `services/ai-worker/.venv`；检查 `ffmpeg` / `ffprobe`、GPU、IOPaint 模型状态（`http://127.0.0.1:8080/api/v1/model`）。服务重启后可能停止，**HTTP 进程不由 Git 提交保存**。
3. 修复上表六项，尤其是 P0 截断源与音频假成功。为每项添加失败注入/真实媒体回归；不要改用头部声明替代真实包数，而要核对声明和实际二者矛盾，并妥善处理合法 `nb_frames=N/A` 的分片 MP4。
4. 重跑 API：`cd services/api && .venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m pytest -q`；前端：`cd apps/miniapp && pnpm lint && pnpm typecheck && pnpm build:weapp`。再跑真实 LaMa `scripts/eval_endcard_2phase.py` 与 6 场景质量门禁；不能只看 API status。
5. 独立交审查代理复核；不直接采信此前工作代理的 PASS。完成后更新 `docs/REVIEW_STATUS.md`、`docs/WATERMARK_E2E_FINAL_REPORT.md` 和本文的当前结论。

## 当前 Git 检查点的含义

此检查点保护**当前正在开发的代码和交接记录**，包括仍有已知缺陷的新下载实现。重启后从此继续修复；**不要把本地 commit、通过的单元测试或一次成功下载解释为业务完整验收**。
