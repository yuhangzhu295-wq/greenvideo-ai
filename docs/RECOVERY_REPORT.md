# GreenVideo AI 恢复报告

生成时间：2026-10-24
恢复模式：全自动执行（GitHub 唯一可信恢复源）

## 一、恢复路径

| 项目 | 值 |
| --- | --- |
| 恢复目录 | `C:\Users\zyu33\Documents\Codex\greenvideo-ai` |
| GitHub 仓库 | https://github.com/yuhangzhu295-wq/greenvideo-ai.git |
| 默认分支 | `master` |
| 恢复 Commit | `da209ee95327f7ac5a681371458791ef5f39b721` |
| 远程 HEAD 核对 | 与已知基线一致，无新提交 |
| 当前开发分支 | `recovery/development`（由基线创建，`master` 未改动） |
| 跟踪文件数 | 234 |
| `git fsck --full` | 干净，无损坏对象 |

隔离备份目录（未改动原始残留文件）：
`C:\Users\zyu33\Documents\Codex\greenvideo-ai-assets-backup`

## 二、本机资产扫描结论

历史路径 `C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai` 已存在但**不可作为恢复源**：

- `.git` 目录被短名化为 `GIT~1`，`git status` 无法识别该仓库。
- `services/api/app` 下无任何 `.py` 源文件。
- 缺少根 `package.json`。
- 大量文件名被 8.3 短名化（例如 `INDEX~1.JSO`）。

该目录内仍有价值的真实资产已复制到隔离备份目录：

- `test_input/test_watermark_1.jpg`、`test_input/test_watermark_2.jpg`
- `test_output/watermark_standard_result.jpg`
- `storage/greenvideo.db`（API 库与根库各一份）
- `artifacts/watermark_before_001.jpg`、`watermark_after_001.jpg` 等对比素材
- `docs/LOCAL_FIRST_REFACTOR_REPORT.md`、`docs/TEST_MATERIALS.md`

其中测试图片与 demo 缩略图已恢复到 `services/api/storage/`（`storage/` 被 `.gitignore` 排除，不会随克隆恢复）。

## 三、运行环境版本

| 工具 | 版本 |
| --- | --- |
| OS | Windows 11（win32 10.0.26200） |
| Git | 2.53.0.windows.1 |
| Node.js | v24.14.0 |
| pnpm | 10.18.3（与 `packageManager` 字段一致） |
| Python（venv） | 3.12.10 |
| FFmpeg / ffprobe | 8.1.1 |
| OpenCV | 5.0.0（opencv-python-headless 5.0.0.93） |
| NumPy | 2.5.4 |
| FastAPI / Starlette | 0.162.0 / 1.9.0 |
| Uvicorn | 0.54.0 |
| SQLModel / Pydantic | 0.0.48 / 2.14.1 |
| Celery | 5.6.3 |
| Pytest / Ruff | 9.1.1 / 0.17.2 |

依赖安装方式：后端 `services/api/.venv` + `pip install -e ".[dev]"`；前端 `pnpm install --frozen-lockfile`（1110 包，36.8s）。

注意：`corepack pnpm` 在本机会调用 corepack 内置 pnpm 11.0.9 并与 `packageManager: pnpm@10.18.3` 冲突报错；系统 PATH 中的 pnpm 已是 10.18.3，因此实际使用系统 `pnpm` 执行（版本正确，锁文件未改动）。

## 四、前端构建结果

| 检查 | 结果 |
| --- | --- |
| `pnpm lint`（tsc --noEmit） | PASS |
| `pnpm typecheck` | PASS |
| `pnpm build:weapp` | PASS（Compiled successfully in 16.48s） |
| 产物 | `apps/miniapp/dist`，45 个文件 |
| 页面数 | 10（home / result / watermark / processing / history / tutorial / faq / profile / invite / feedback） |
| 编译内置 API 地址 | `http://127.0.0.1:8000` |

## 五、后端启动结果

服务命令：`services/api/.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`

`GET /api/health` 实际返回：

```json
{"ok":true,"service":"greenvideo-api","demo_mode":false,"yt_dlp_available":true,
 "ffmpeg_available":true,"opencv_available":true,"storage_ok":true}
```

- SQLite：`PRAGMA integrity_check` = ok；表 `users / parse_results / tasks / history_items / feedback` 齐全。
- 本地存储：`storage/` 写入探针通过。
- OpenCV：图片解码（598x500 JPG）与视频解码（40 帧）正常，`cv2.inpaint` 可执行。
- FFmpeg：可生成含 h264 视频流 + aac 音频流的 MP4 并 remux。
- 端口：8000 与 8080 均空闲，未占用其他项目服务。

## 六、测试通过与失败项目

| 测试 | 结果 |
| --- | --- |
| Ruff（app + tests） | PASS |
| Pytest | PASS（20 passed） |
| `scripts/smoke_api.py` | PASS（Health / Parse / Download / Watermark / Feedback / History 全 OK） |
| `scripts/verify_local_material.py` | PASS（含遮罩区域差异校验） |
| `scripts/verify_share_links.py` | PASS（douyin 需登录被明确拒绝、bilibili 成功、kuaishou 平台限制被明确拒绝） |
| `scripts/scan_no_empty_handlers.py` | PASS |
| 微信小程序生产编译 | PASS |
| 前端 lint / typecheck | PASS |
| 真实视频上传→处理→ffprobe 验证 | PASS |
| 历史记录 / 下载 / 删除 | PASS |
| 取消流程 | PASS |
| 错误提示（未授权 / 损坏文件） | PASS |

无失败项。Pytest 仅有 1 条 StarletteDeprecationWarning（来自 fastapi.testclient 内部，非本项目代码）。

## 七、视频去水印的真实处理引擎

**当前实际引擎：OpenCV 逐帧修复（`cv2.inpaint` + `INPAINT_TELEA`）。**

实证证据（同一输入视频，`repair_mode=opencv` 与 `repair_mode=ai` 各跑一次）：

| 对比项 | 数值 |
| --- | --- |
| opencv 模式输出 SHA256 | `7e471ce787ded845041fcc7ffabee6a03cbefad440ff5b8bd40da95a868ca480` |
| ai 模式输出 SHA256 | `7e471ce787ded845041fcc7ffabee6a03cbefad440ff5b8bd40da95a868ca480` |
| 是否字节相同 | 是（证明 AI 模式实际执行的就是 OpenCV） |
| 源文件 SHA256 | `46c7b3359f5c66cae70b586afbf9cc3fc10d5a03df7a88b06bb1a94f6dcc50fd`（不同，排除"复制原文件"） |

遮罩区域像素差异（证明修复真实发生，非空操作）：

| 帧 | 左上遮罩 mean_abs_diff | 右下遮罩 mean_abs_diff | 全帧 mean_abs_diff |
| --- | --- | --- | --- |
| 第 0 帧 | 41.904 | 1.136 | 4.905 |
| 第 39 帧 | 13.181 | 0.747 | 3.243 |
| `verify_local_material` 首帧 | 34.234 | 0.605 | 4.634 |
| `verify_local_material` 末帧 | 19.508 | 0.478 | 3.321 |

输出完整性（ffprobe 实测）：

```text
stream 0: mpeg4 video 640x360 r_frame_rate=10/1 nb_frames=40
stream 1: aac   audio sample_rate=44100 channels=1 nb_frames=174
format duration=4.000000 size=85438
```

## 八、AI 模式是否实际可用

**不可用。当前环境未部署 IOPaint，AI 模式不会调用 AI 推理。**

- `GREENVIDEO_IOPAINT_BASE_URL` 默认 `http://127.0.0.1:8080`，本机 8080 端口无监听。
- `try_iopaint_service()` / `iopaint_status()` 探测返回不可达（ConnectTimeout）。
- 代码路径确认：`process_video()` 根本没有调用 IOPaint 的分支，始终走 OpenCV 逐帧修复；只有 `process_image()` 在 `repair_mode == "ai"` 且服务可用时才会调 `_inpaint_with_iopaint()`。
- 尝试 `pip install iopaint` 以验证 AI 链路：安装过程将 starlette 降级到 0.32.0、numpy 降级到 1.26.4，并因 `cv2.pyd` 拒绝访问中途失败。已通过 `--force-reinstall --no-deps` 恢复 fastapi 0.162.0 / starlette 1.9.0 / numpy 2.5.4，`pip check` 恢复 "No broken requirements found"，20 个测试复跑通过。IOPaint 最终未安装成功，因此 AI 推理链路**未获得真实运行证据**。

修复后 AI 模式的行为（已实测）：任务状态为 `completed`，但 `stage` 字段明确写明
"AI 修复不可用（IOPaint 服务不可达（http://127.0.0.1:8080）：ConnectTimeout），已降级使用 OpenCV 逐帧修复引擎。"
即：不再谎称 AI 成功，使用者能看到真实引擎。

## 九、已完成的修复及文件差异

仅修复"阻止业务流程正确执行"和"假成功"类缺陷，共 6 个文件，`+260 / -43`：

| 文件 | 修复内容 |
| --- | --- |
| `services/api/app/routers/files.py` | **问题 C**：删除"任务不存在或输出缺失时生成 demo .txt 并返回 200"的假成功路径。现在分别返回 404 `TASK_NOT_FOUND` / `OUTPUT_NOT_READY` / `OUTPUT_MISSING`。路径穿越防护保留（400 `UNSAFE_PATH`）。 |
| `services/api/app/routers/tasks.py` | **问题 A**：新增 `_engine_stage()`，把真实使用的引擎写入任务 `stage`。当 `last_engine == "none"`（引擎不可用 / 解码失败）时任务标记 `failed` 并附明确 `error`，不再报 completed。 |
| `services/api/app/services/watermark.py` | **问题 A/C**：新增 `iopaint_status()` 返回 (可用性, 原因)；`WatermarkProcessor` 记录 `last_engine` / `last_engine_note` / `last_degraded`；`process_image` / `process_video` 的所有复制原文件分支都如实记录"未执行去水印"，stage 文案从"处理完成"改为"处理结束（未执行去水印）"。 |
| `services/api/tests/test_api.py` | 修正损坏的测试素材：原内联 PNG 是 65 字节的损坏数据（真实 1x1 PNG 为 67 字节），OpenCV 无法解码，导致该测试实际验证的是"复制文件并假报成功"。改为有效的 120x80 PNG（base64）。 |
| `scripts/smoke_api.py` | **问题 C**：原 smoke 把 download 任务生成的 demo .txt 当视频源做去水印，属于假成功验证。改为 multipart 上传真实可解码视频 `scripts/fixtures/smoke_watermark.mp4`，并断言输出字节与源不同。 |
| `scripts/verify_local_material.py` | **问题 B/C**：原脚本引用不存在的 Desktop 素材且只校验时长与帧提取（不校验水印是否真被去除）。改为优先使用仓库内置素材，并新增 `mask_region_delta()` 计算遮罩区域 mean_abs_diff，`repair_happened` 为假时直接失败退出。 |

未改动：业务逻辑、API 契约（仅新增错误码与 stage 文案）、前端路由、`master` 分支。

## 十、未解决的问题及风险

1. **AI 去水印链路未打通（P0）**：IOPaint 未安装成功，`process_video` 也没有实现调用 IOPaint 的分支。当前 AI 模式只是"OpenCV + 诚实说明"。
2. **遮罩为固定比例矩形（问题 B 未根治，P1）**：`_preset_regions()` 仍是左上 42%x18% / 右下 40%x18% 大面积矩形，会破坏主体画面。实证中右下区域 mean_abs_diff 仅 0.5~1.1 说明该区域多数像素未被真正改动，但遮罩本身仍然过大。需要水印自动定位，属算法重构，未混入本次恢复。
3. **字幕遮罩偏大（P1）**：`remove_subtitle` 使用 18%~84% 宽度、56%~76% 高度的固定矩形。
4. **Redis/Celery 未启用（P2）**：`.env.example` 配置了 Redis broker，但实际任务由 FastAPI `BackgroundTasks` 执行；本机无 Redis，未验证 Celery 链路。
5. **`_run_download_task` 仍是 demo 输出（P2）**：下载任务不执行真实平台下载，只写 .txt demo 文件。这是原有设计且在文件中明确声明，本次未改。
6. **corepack 版本冲突（P2）**：`corepack pnpm` 会触发 pnpm 11.0.9 与 `packageManager` 冲突。当前用系统 pnpm 10.18.3 规避，建议在 CI/文档中统一。
7. **测试素材不在版本控制内（P2）**：`storage/` 被 gitignore，真实测试图片来自隔离备份。本次已在 `scripts/fixtures/` 放入可复用的视频 fixture。

## 十一、验收结论

**部分恢复（开发环境已可用，AI 去水印不可用）。**

满足的条件：

- Git 源码完整（HEAD = 基线，fsck 干净，master 未动）。
- 开发环境依赖可用（`pip check` 无冲突，pnpm 1110 包装齐）。
- 前后端能够启动（API health 全绿，前端编译成功）。
- 微信小程序构建成功（45 个产物文件，10 页面）。
- 核心 API 测试通过（20 pytest + smoke 全 OK）。
- 至少一组真实视频处理测试通过（含 ffprobe 与遮罩差异实证）。
- 未发生其他项目数据损坏（未删除/覆盖任何原有文件；IOPaint 造成的依赖回退已修复并复测）。
- 恢复日志和报告完整（`recovery/logs/`）。

不满足的条件：AI 去水印引擎未实际可用（已在第八节明确标注）。
