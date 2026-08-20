# AUDIT_REPORT

## 当前技术栈

- 前端：Taro 4.2.0 + React 18 + TypeScript，微信小程序输出位于 `apps/miniapp/dist`。
- 后端：FastAPI + SQLModel + SQLite，本地服务位于 `services/api`。
- 任务：存在 Celery 配置和 Redis compose，但当前主要任务实现是 FastAPI 内部 `asyncio.create_task` mock 流程。
- 媒体处理：已有 `yt-dlp` 解析封装、OpenCV/IOPaint 处理服务雏形，但下载任务和去水印任务没有完整调用真实处理管线。
- 存储：SQLite 和本地 `services/api/storage/` 已存在，包含 files/uploads/static/demo 等目录。

## 当前 API 地址配置

- `apps/miniapp/src/api/client.ts` 使用 `process.env.TARO_APP_API_BASE_URL || "http://127.0.0.1:8000"`。
- 微信开发者工具或手机真机中，`127.0.0.1` 指向模拟器/设备自身，不一定能访问电脑上的 FastAPI 服务。
- 当前没有前端配置页或清晰错误引导来提示用户配置局域网 IP、开启“不校验合法域名”、或启动后端。
- 后端健康检查当前为 `GET /health`，文本要求为 `GET /api/health`，前端也缺少 `healthCheck()`。

## 无效功能列表

| 页面 | 按钮/功能 | 当前代码位置 | 问题 | 修复方案 |
|---|---|---|---|---|
| 首页 | 开始解析 | `apps/miniapp/src/pages/home/index.tsx` | 直接调用 `/api/parse`，无 health check；连接失败只显示粗略错误；非 demo 模式不允许空输入，但真实分享文案字段只按 `url` 传 | 增加 `healthCheck()`；`parseVideo(inputText)` 支持完整文案；展示后端未启动/API 地址未配置/平台限制等具体错误 |
| 首页 | 视频下载功能卡 | `home/index.tsx` | 直接跳结果页，若没有解析结果会展示 mock 数据 | 改为聚焦首页解析区域或提示先解析/上传素材 |
| 首页 | 视频去水印功能卡 | `home/index.tsx` | 直接创建 `source_task_id=local_demo` 水印任务，未要求授权确认 | 改为进入 watermark 页，让用户选择素材、mask preset 并确认授权 |
| 解析结果页 | 复制直链 | `result/index.tsx` | 仅 toast“已复制”，没有真实文件链接 | 未生成文件时提示先下载；完成后复制 `/api/files/{task_id}` |
| 解析结果页 | 去水印 | `result/index.tsx` | 仅跳 watermark，没有传 parse_id/task_id | 传入 parse_id 或源任务信息，供后端从已有文件创建水印任务 |
| 去水印页 | 从相册选择/上传 | `watermark/index.tsx` | `createWatermarkTask` 上传字段只有 `mode/mask_regions`，没有 `confirm_authorized`、`mask_preset`、`repair_mode`、`remove_subtitle`；后端也不强制授权 | 增加授权确认、mask preset 选择、repair mode 映射和明确上传错误 |
| 去水印页 | 粘贴视频链接 | `watermark/index.tsx` | 当前复用上传函数，不读取剪贴板、不调用 `/api/parse` | 实现剪贴板解析，平台限制时提示本地上传继续 |
| 去水印页 | AI 修复 | `watermark/index.tsx` | 前端显示 LaMa/AI，但后端只读 `mode=ai`，IOPaint 不可用时未在前端说明 | 调 health 显示 AI 修复是否可用；不可用时禁用或提示 fallback OpenCV |
| 处理中页 | 进度 | `processing/index.tsx` + `components/ProgressRing` | 初始状态来自 `mockTask` 固定 62%；流程文案固定；完成态没有保存/复制/查看历史按钮 | 从 0/queued 开始轮询真实 task；completed/failed/canceled 显示对应动作 |
| 处理中页 | 取消任务 | `processing/index.tsx` | 可调用 cancel，但任务 API 没有 DELETE，取消后固定 navigateBack | 加入取消后状态刷新；补齐 `DELETE /api/tasks/{id}` |
| 历史页 | 筛选 chip | `history/index.tsx` | chip 只是静态文字，不改变 query | 接入 `getHistory(query)` 并按 type/status 过滤 |
| 历史页 | 再次处理/查看 | `history/index.tsx` | 将 history id 当 taskId 跳转，可能查不到任务 | 使用 `task_id` 字段；无 task_id 时给出明确提示 |
| 我的页 | 我的下载/我的任务/设置 | `profile/index.tsx` | 多个菜单项无点击逻辑 | 绑定 history 查询、设置弹窗/API 地址提示 |
| 反馈页 | 提交反馈 | `feedback/index.tsx` | 成功路径可用，但失败没有 catch，可能造成无提示异常 | 增加 try/catch、loading、后端错误展示 |

## 服务连接失败原因

P0 根因：

- 前端默认 `API_BASE_URL=http://127.0.0.1:8000`。在微信开发者工具或移动设备环境中，这通常不能访问电脑后端，导致截图中的“服务连接失败”。
- 前端没有先调用健康检查，也没有提示“请启动 FastAPI 或配置局域网 IP”。
- 后端健康接口路径与文本要求不一致：现有 `/health`，缺少 `/api/health`。
- `/api/parse` 请求模型只定义 `url` 字段，文本要求也支持 `{ "text": "完整分享文案" }`。
- `VideoDownloader.parse_url()` 在 yt-dlp 不存在或真实平台失败时会回退 demo，无法区分 `expected_blocked` 与真实 bug。

## 上传失败原因

P0 根因：

- 前端 `Taro.uploadFile` 只发送 `mode` 与 `mask_regions`，不发送 `confirm_authorized=true`，也没有 `mask_preset/repair_mode/remove_subtitle`。
- 后端 multipart 分支没有解析 `mask_preset/repair_mode/confirm_authorized/remove_subtitle` form 字段，也没有强制授权确认。
- 后端只保存上传文件并启动 mock 任务，没有调用 `WatermarkProcessor.process_image/process_video`，也没有生成真实输出文件。
- 后端任务进度直接从 62% 开始，随后 mock 到成功，不代表真实上传和处理。

## 页面真实状态

- 首页：UI 可用；剪贴板和解析按钮半接通；缺 health check、具体错误和平台限制处理；功能卡仍有壳子行为。
- 解析结果页：可从 storage 读 parse_result；下载任务可创建但后端是 mock；直链复制不真实；去水印未传源任务。
- AI 视频去水印页：可选择媒体并上传；缺授权确认、mask preset、链接粘贴解析、真实处理闭环。
- 处理中页：轮询 task API，但初始 mock 62%，文案固定，缺完成/失败操作区。
- 历史记录页：可读取 API；筛选静态；查看/重试 id 映射不可靠。
- 使用教程页：静态说明页，基本可用。
- 常见问题页：静态说明页，需补合规说明和后端未连接处理指引。
- 我的页：部分菜单可跳转，下载/任务/设置无真实逻辑。
- 邀请好友页：静态说明页，复制/邀请可后续增强。
- 客服反馈页：可提交 API；缺失败捕获和 loading。

## 修复优先级

P0:

- 增加 `/api/health`，修复前端 `healthCheck()`、API 地址配置和连接失败提示。
- 修复 `/api/parse` 支持完整分享文案，提取 URL，并区分 success / expected_blocked / failed_bug。
- 修复 `/api/tasks/watermark` multipart 字段、授权确认、mask preset，并真实生成输出文件或 demo fallback。
- 移除核心流程中的固定 62% 起步和 mock-only 行为。
- 创建 `docs/TEST_MATERIALS.md`、`verify_share_links.py`、`verify_local_material.py`、`scan_no_empty_handlers.py`。

P1:

- 下载任务生成真实文件或明确 demo fallback，完成后写 history。
- 处理页完成态/失败态/取消态真实展示。
- 历史筛选和删除补齐。
- 反馈页失败处理补齐。

P2:

- 设置页/API 地址提示增强。
- H5/小程序开发环境说明补齐。
- 可选 IOPaint/PaddleOCR 能力提示和更细粒度水印区域 UI。
