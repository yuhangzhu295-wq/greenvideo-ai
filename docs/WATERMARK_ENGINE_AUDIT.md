# GreenVideo AI 水印引擎审计报告

审计时间：2026-10-25
分支：`recovery/development`
环境：Windows 11 / RTX 5060 Laptop 8GB / CUDA Driver 13.1

## 一、根因矩阵

| # | 现象 | 文件 | 根因 | 复现 | 修复 | 验证 |
|---|---|---|---|---|---|---|
| R1 | AI 模式未执行 AI 推理 | `services/api/app/services/watermark.py` `process_video()` | 函数没有 `repair_mode=="ai"` 分支，只读 mask 后逐帧 `cv2.inpaint` | `ai` 与 `opencv` 输出 SHA256 相同 | 增加真实 LaMa 调用分支；引擎不可用时抛 `AiServiceError` 而非静默降级 | AI 与 OpenCV 输出哈希不同，stage 标注 `iopaint-lama` |
| R2 | IOPaint 适配器契约错误 | `watermark.py` `_inpaint_with_iopaint()` | POST `/api/inpaint` 且解析 `response.json()["image"]`；实际是 `/api/v1/inpaint` 返回二进制 PNG | 起服务后旧路径 404 | 改为 `/api/v1/inpaint`，按 `content-type: image/*` 解析二进制 | 单帧修复实测 `outside_diff=0.0`、`mask_diff=23.7` |
| R3 | IOPaint 安装破坏主环境 | `services/api/.venv` | 同环境安装把 starlette 降到 0.32.0、numpy 降到 1.26.4 | `pip check` 报 broken；`import fastapi` 失败 | AI 推理迁到 `services/ai-worker/.venv` 独立环境，以 HTTP 服务方式接入 | 主环境 `pip check` 无冲突，20 个 pytest 通过 |
| R4 | custom 预设产生空 Mask | `watermark.py` `_generate_mask()` | `custom` 只用 `mask_regions`，前端从不发送，Mask 全 0 | 实测 `custom + no regions -> 0 px` | 空 Mask 不再静默通过；AI 模式空 Mask 直接失败 | 见测试 `test_watermark_*` |
| R5 | 前端 custom 无区域输入 | `apps/miniapp/src/pages/watermark/index.tsx` | 无收集 `mask_regions` 的控件，`uploadWatermarkFile` 恒传 `[]` | 页面无区域 state | 增加"自动检测水印区域"开关，走后端检测 | 提交后 DB `input_json.mask_regions` 非空 |
| R6 | 前端 Mock/Demo 回退 | `apps/miniapp/src/api/client.ts` | 上传失败返回 `task_watermark_demo`；`getTask` 返回 `mockTask` | `DEMO_FALLBACK` 分支 | 删除这两处假成功回退，失败一律抛错 | typecheck 通过；后端不可达时报错 |
| R7 | 只能确认固定区域像素变化 | `scripts/verify_local_material.py` | 无 Ground Truth，无 PSNR/SSIM | 旧报告只有 mean_abs_diff | 建立无水印 Ground Truth 集与量化指标 | 见 `before_after_psnr.json` |
| R8 | 遮罩为固定比例大矩形 | `watermark.py` `_preset_regions()` | 左上 42%x18% 等，实测覆盖率 14.6% | mask 统计 | 保留预设作为兜底，新增自动检测与逐帧跟踪 | 见检测章节 |
| R9 | 逐帧独立修复无时序约束 | `watermark.py` `process_video()` | 每帧单独 inpaint | 长视频闪烁 | LaMa 逐帧 + 闪烁指标监控 | `flicker_std` 对比 |
| R10 | 移动水印被静态矩形覆盖 | `watermark.py` | 单一矩形无法表达移动路径，union 遮罩过量 | T04 outside PSNR 仅 20.1 dB | 新增 `MovingMaskTracker` 逐帧紧致遮罩 | T04 outside PSNR 37.9 dB，gain +34.0 dB |
| R11 | 测试素材遮罩与水印错位 | `scripts/build_ground_truth_set.py` | `drawbox` 在 lavfi `color` 源上 `t` 不推进；半透明水印在亮背景上不可见 | mask 与文字 overlap 0% | 改为 Python 合成，mask 与水印同源坐标；加水印底板 | inside/outside diff 比 3.4x~155x |

## 二、引擎选择

| 候选 | 结论 |
|---|---|
| IOPaint / LaMa | **选定为主引擎**。Apache-2.0，单帧修复强，可独立进程运行，RTX 5060 8GB 可承载 |
| Video Subtitle Remover (VSR) | 未集成。需额外打包 STTN 权重与 CLI，超出本次范围；列为后续候选 |
| ProPainter | **不作为默认引擎**。非商业许可，未取得授权不得用于商业项目 |

隔离方案：`services/ai-worker/.venv`（torch 2.11.0+cu128 + iopaint 1.6.0），以 HTTP 服务运行；主项目 `services/api/.venv` 版本不变。

## 三、IOPaint API 契约（已核实）

- 模型查询：`GET /api/v1/model`
- 模型切换：`POST /api/v1/model`，body `{"name": "lama"}`
- 推理：`POST /api/v1/inpaint`，body 为 base64 `image` + base64 `mask`，**响应为二进制图片**（`content-type: image/png`）
- 原代码的 `/api/inpaint` + JSON 响应假设是错的

## 四、水印检测现状

自动检测已实现（`app/services/watermark_detect.py`），基于 Sobel 梯度能量 + 局部对比度 + 跨帧持续性的保守判定。实测结论：

- 在合成测试素材上，`testsrc2` 类高频纹理背景会淹没信号，检测器拒绝出图并返回"检测失败"，**不会伪造遮罩**。
- 在平滑背景测试集上，GT 框的响应排在随机同尺寸窗口的 81~99 百分位，信号真实存在。
- OCR 路线（PaddleOCR 3.7 + paddlepaddle）在本机受阻：`paddlepaddle==3.3.1` 触发 oneDNN/PIR `NotImplementedError`；降级到 `3.0.0` 后未再验证。**自动文字检测仍属未完成项**，已在 `docs/WATERMARK_E2E_FINAL_REPORT.md` 中标为 BLOCKED/UNSUPPORTED。
- 当前可用路径：预设区域、自定义区域、自动检测（保守，失败即报错）。

## 五、本审计未覆盖

- 微信开发者工具内的真实人工操作（标为 NOT TESTED，用可重复客户端集成测试补充）。
- 真实外部水印视频素材（需确认许可，未找到授权清晰的现成素材，标为 BLOCKED）。
- 长视频（>30s）与高分辨率（>1080p）的显存与耗时表现。

## 六、审查代理复核后的修复

独立审查代理（code-reviewer）复核实测证据后提出以下问题，均已处理：

| 级别 | 问题 | 处理 |
|---|---|---|
| P0 | `tasks.py` 无有效素材时生成 demo 文本并报 completed | 改为直接失败，stage="缺少可处理的素材"，不再写 demo 文件 |
| P1 | 并发任务共享 `processor.last_engine`，可能误报 | 每次处理调用后立即捕获本任务的 `engine` / `engine_note`，新增 `_stage_from_engine()` |
| P1 | 图片 `repair_mode="ai"` 失败后静默降级 OpenCV | 与视频路径一致：IOPaint 不可用或推理失败即抛 `AiServiceError`，任务标记 failed |
| P1 | 前端 `createWatermarkTask` 失败仍返回 `task_watermark_demo` | 删除该回退，失败一律抛错 |
| P1 | 验收脚本只评 8/40 帧、阈值仅 1 dB | 改为逐帧全量评分，新增 `worst_frame_gain_db`（最差单帧）检查，阈值提高到 5 dB，误伤帧阈值 -3 dB |
| P2 | `clean_motion.mp4` 与 `clean_shared.mp4` 完全相同 | 复杂场景改为叠加移动色块与网格，两背景 SHA256 已不同 |

补充实测：

- 无素材任务（仅 `video_url`、无上传文件）现返回 `status=failed`，`error="未收到可处理的视频或图片文件，任务未执行。"`
- 图片 AI 路径在 `GREENVIDEO_IOPAINT_ENABLED=false` 下实测抛 `AiServiceError`，不再降级。

### OCR 路线实测补充（2026-10-25）

已定位可用组合：**paddlepaddle 3.2.2 + PaddleOCR 3.7.0**（CPU）。

- `paddlepaddle==3.3.1`：`NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support [pir::ArrayAttribute<pir::DoubleAttribute>]`，即使 `FLAGS_use_mkldnn=0` 仍复现。
- `paddlepaddle==3.0.0`：`ValueError: Type of attribute: strides is not right`。
- `paddlepaddle==3.2.2`：可用，`PP-OCRv6_medium_det/rec` 正常加载。

实测精度（`artifacts/watermark-e2e/ocr_detection_accuracy.json`）：

| 场景 | 检出框 | IoU>=0.3 | 说明 |
|---|---|---|---|
| T01 固定文字 | — | 命中 `WATERMARK` | 文字水印可检出 |
| T02 固定 Logo | 5 | 2 | Logo 无文字，检出的是背景误报 |
| T03 半透明 | 5 | 5 | 检出质量较好（IoU 0.51~0.75） |
| T04 移动 | 5 | 3 | 能跟随部分位置 |
| T05 多处 | 7 | 6 | 多目标可检出 |
| T06 复杂背景 | 5 | 0 | 全为误报，且出现 y=590 等越界框 |

结论：OCR 只能覆盖**文字类**水印，对 Logo / 半透明 / 非文字水印无效；CPU 推理约 3~7 秒/帧，无法用于逐帧视频；复杂背景下误报率高。因此**不将 OCR 作为默认自动检测路径**，仅作为后续 GPU 部署与文字水印场景的候选。当前自动检测仍以保守的梯度/对比度/持续性判定为主，检测不到即报失败。

## 七、业务流审计发现并修复的缺陷（2026-10-25）

对九项业务流逐项实测后修复：

| 问题 | 现象（实测） | 修复 |
|---|---|---|
| 取消不停止后台处理 | 取消后任务仍跑完 40 帧，`status` 被改写为 `completed`，但 `error="用户已取消"`、`history=success`，状态自相矛盾 | `repair_video()` 与 OpenCV 帧循环新增 `cancel_check` 每帧检查，取消即抛 `AiServiceCancelled` |
| 取消/失败残留输出文件 | 取消后 `storage/files/<task>.mp4` 仍存在（107 KB） | 新增 `_discard_output()`，失败与取消路径均删除未完成输出 |
| 异常捕获顺序错误 | `except Exception` 先于 `except AiServiceCancelled`，导致取消被记为 `failed` | 调整为先捕获 `AiServiceCancelled`，任务保持 `canceled` |

修复后实测：取消请求 → `canceled 100%`，无残留输出文件，DB 状态一致。

## 八、自动水印检测算法落地（2026-10-25）

`services/api/app/services/watermark_detect.py` 重写为**时序中值 + 结构能量**算法，替换原先的纯梯度+对比度方案（后者在平滑背景尚可、复杂纹理完全失效）。

### 算法流程

1. 采样最多 20 帧，取**时序中值**图 `median`。
2. **静态度** `staticness = clip(1 - maxdev/40, 0, 1)`，`maxdev` 为各帧与中值的最大偏差。水印不随场景运动，所以它静；场景内容动，所以它不静。
3. **结构能量**取中值图上的 Sobel 梯度与 21x21 局部对比度。水印是画上去的，自带结构；平坦静态背景（如纯色天空）没有结构。
4. `score = staticness*0.5 + (clip(grad/60,0,1)*0.3 + clip(contrast/50,0,1)*0.2)`
5. 93 百分位阈值 → 连通域 → **核心收紧**（保留组件内分数 ≥ 0.72*max 的紧密包围盒）→ 10% 外扩 → **密度条件化的范围生长**（只有当生长后区域平均分不低于原峰值 92% 才接受，避免长进背景）。
6. 面积/宽高比过滤剔除场景结构（跨帧大块、竖长条）；按分数排序后空间去重。

### 校准依据（实测数据）

| 区域类型 | 梯度 | 对比度 | 静态度 |
| --- | --- | --- | --- |
| 水印区（T01-T06） | 57–191 | 26–83 | 0.13–0.99 |
| 平坦静态背景（白圆） | 0–23 | 0–22 | 0.83–0.86 |
| 运动繁忙背景 | 高 | 高 | 低 |

三类必须联合判定：单看静态度会把白圆当水印，单看结构会把繁忙背景当水印。

### 检测精度（box IoU 对真值）

| 场景 | box IoU | 误遮比例 |
| --- | --- | --- |
| T01 固定文字 | 0.587 | 0.37% |
| T02 固定 Logo | 0.630 | 1.79% |
| T03 半透明 | 0.665 | 1.24% |
| T04 移动 | 0.573 | 2.22% |
| T05 多处 | 0.400 | 1.49% |
| T06 复杂背景 | 0.507 | 0.00% |

### OCR 路线结论（未采用为默认）

- 可用组合：`paddlepaddle==3.2.2` + `PaddleOCR 3.7.0`（CPU）。3.3.1 触发 oneDNN/PIR `NotImplementedError`；3.0.0 触发 strides 错误。
- Windows + Python 3.12 无 PaddlePaddle GPU 轮子（已探测 cu118/cu123/cu126/cu128）。
- CPU 稳态 11 秒/帧；加时序聚类后 T01 IoU 0.53、T03 0.73，但只能覆盖文字水印，Logo/半透明无效，复杂背景误报率高（T06 全误报）。
- 结论：不作为默认路径，仅作后续 GPU 部署候选。

---

# 更新（2026-10-25，第 12 轮修复后）

## 五、假成功防护矩阵（第 9-12 轮审查发现）

审查代理 4 轮 REJECTED，逐轮发现下列假成功路径。全部已修复并配回归测试。

| # | 假成功路径 | 文件 | 修复 |
|---|---|---|---|
| F1 | 单帧 `cv2.imwrite` 返回 False 未检查，源 3 帧输出 2 帧仍报 `frames_repaired=3` + completed | `video_repair.py` | 逐帧检查写入返回值与文件非空，失败抛 `AiServiceError` |
| F2 | OpenCV `VideoWriter` 未打开，写出全为 no-op，交付零字节"修复视频"仍 completed + 下载 200 | `watermark.py` | `writer.isOpened()` 检查；写出后校验非空 / 有视频流 / 帧数一致 |
| F3 | 下载任务写 `GreenVideo AI download result` 文本命名 `.mp4`，标记 completed，下载接口返回 `video/mp4` | `tasks.py` | 真实平台下载未实现，任务直接 failed 并说明；删除 `_write_demo_result` 死代码 |
| F4 | 下载接口只检查文件存在 | `files.py` | 空文件 409 `OUTPUT_EMPTY` |
| F5 | 帧数校验信任容器 `nb_frames`；碎片化 MP4 不写该字段，`if x is not None` 直接跳过比较 | `watermark.py` / `video_repair.py` | 新增 `count_packets()` / `count_decoded_frames()` 真实解复用计数；无法计数返回 `None`，调用方一律失败而非放行 |
| F6 | 上述修复自身 bug：`-select_streams video:0` 不被 ffprobe 接受（只认 `v:0`），返回码 1 使每次调用都返回 `None`——**守卫失败开放** | `video_repair.py` | 新增 `_ffprobe_stream_selector()` 映射 `video→v / audio→a / subtitle→s` |
| F7 | AI 路径用输入循环计数 `index` 充当输出帧数，两个硬性帧数比较同时失效 | `video_repair.py` | 从交付文件独立计数包数，与处理帧数、输入真实包数三方比对 |
| F8 | `cap.read()` 首次失败当作正常 EOF，未核对输入声明帧数；可打开但尾部损坏的源解码提前停止仍 completed | `watermark.py` | 解码帧数 vs 输入真实包数比对，不符即抛错 |
| F9 | "有视频流"不等于"有可解码帧"；810 字节、有视频流头但零视频包的 MP4 拿到 HTTP 200 | `files.py` | 改用 `count_decoded_frames()` 校验，`None` 或 ≤0 返回 409 `OUTPUT_NOT_PLAYABLE` |

## 六、计数helper 边界行为（已实测）

| 输入 | `count_packets(video)` | `count_decoded_frames` |
| --- | --- | --- |
| 普通 MP4（3 帧 + 音频） | 3 | 3 |
| 碎片化 MP4（`nb_frames`=N/A） | 3 | 3 |
| 仅音频文件 | None | None |
| 不存在路径 | None | None |
| 零字节文件 | None | None |
| header-only MP4（有视频流、零包） | None | None |

video 与 audio 计数未混淆：普通 MP4 video=3 / audio=13。

## 七、严格守卫未误杀合法输出（已实测）

| 场景 | 结果 |
| --- | --- |
| 无音轨源（OpenCV 路径） | completed，4 包 |
| 碎片化源（`nb_frames`=N/A） | completed，4 包 |
| 碎片化源 + 音轨 | completed，4 视频包 / 18 音频包 |
| 用户真实视频 522 帧（API 全流程） | completed，522 视频包 / 751 音频包 / 522 可解码帧，音频 PCM 与源一致 |
| `frame_stride` 参数 | 无调用方传入，不可用于跳帧少计 |
