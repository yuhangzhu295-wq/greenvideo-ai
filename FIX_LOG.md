# GreenVideo AI 修复记录

## 审计与素材确认

- 阅读并执行 `greenvideo_ai_codex_repair_with_materials_prompt.md`，以文本要求为准，截图仅用于定位无效功能和水印区域。
- 生成 `AUDIT_REPORT.md`，列出原项目中的静态 UI、mock 数据、无效按钮、上传失败、解析失败、异步任务不可靠、安全边界缺失等问题。
- 使用官方 GitHub 仓库核验第三方组件许可证与状态，生成 `THIRD_PARTY_NOTICES.md`。
- 记录本地测试素材与分享链接校验方法，生成 `docs/TEST_MATERIALS.md`。

## 后端修复

- 重写 schema、数据库访问、存储与安全工具，统一任务、历史、解析、上传和文件下载的数据结构。
- `/api/parse` 支持从整段分享文案中提取 URL，并对 SSRF、内网地址、登录态参数和非公开访问做拦截。
- yt-dlp 仅以安全参数执行公开视频 metadata/授权下载流程，所有命令使用参数数组和 `shell=False`。
- 平台失败被归类为 `LOGIN_REQUIRED_NOT_SUPPORTED`、`DRM_NOT_SUPPORTED`、`PRIVATE_VIDEO_NOT_SUPPORTED`、`PLATFORM_BLOCKED_OR_RATE_LIMITED` 或 `PARSE_FAILED`。
- `/api/tasks/download` 创建下载任务、写入历史并提供本地开发下载文件。
- `/api/tasks/watermark` 支持 JSON 与 multipart 上传，要求 `confirm_authorized=true`，支持 `mask_preset`、`repair_mode`、`remove_subtitle`。
- 水印修复默认使用 OpenCV inpaint；视频任务在可用时结合 FFmpeg 保留音频。
- 新增 `/api/health`、任务查询/取消/删除、历史筛选/删除、反馈提交、文件下载路径保护。

## 前端修复

- 重写 `apps/miniapp/src/api/client.ts`，接入真实 Taro request/uploadFile，只有显式 `TARO_APP_DEMO_MODE=true` 时才启用 demo fallback。
- 首页完成粘贴、解析、健康检查、平台限制弹窗和跳转。
- 结果页完成下载任务创建、音频提取入口和直链复制逻辑。
- 处理页按任务 ID 轮询真实状态，展示 queued/processing/completed/failed/cancelled。
- 去水印页完成媒体选择、预设区域、修复模式、字幕开关、授权确认、上传和任务创建。
- 历史、我的、邀请、教程、FAQ、反馈页面均修复为可点击、可提交、可跳转。
- 修复 Taro/TypeScript 类型问题，补齐样式类，确保 `build:weapp` 可通过。

## 自动修理记录

- 修复测试中后台任务未执行的问题，改为 FastAPI `BackgroundTasks` 以兼容 TestClient 与真实服务。
- 修复 WSL/Windows 混合环境下 Python venv 与前端 `node_modules` 互相污染的问题，验证脚本优先使用 Windows venv 和 PowerShell 执行前端命令。
- 修复 pnpm v11 构建脚本批准配置，改用 workspace 级 `allowBuilds`。
- 修复 Bilibili 等平台被反爬/限频时误判为失败的问题，改为合规的 `expected_blocked`。
- 修复 `verify.sh` 中环境工具缺失时的噪音输出，使可选依赖缺失只显示提示。

## 最终验证

- `bash scripts/verify.sh` 最终通过。
- 后端 Ruff 通过。
- 后端 Pytest：`20 passed, 1 warning`。
- API smoke 通过。
- 分享链接归类校验通过，报告位于 `artifacts/share_links_report.md`。
- 本地素材去水印校验通过，报告位于 `artifacts/watermark_validation_report.md`。
- 前端 lint、typecheck、`build:weapp` 通过。
- 静态空处理函数扫描通过。

## 2026-06-30 运行态修复

- 根据微信开发者工具截图复查，当前功能无效的直接原因是本地 FastAPI 服务没有启动，`127.0.0.1:8000` 无进程监听。
- 新增 `scripts/start-local.ps1`，自动创建后端 venv、安装依赖、启动 FastAPI、等待 `/api/health`、设置 `TARO_APP_API_BASE_URL` 并重建小程序。
- 新增 `scripts/stop-local.ps1`，用于停止脚本启动的 FastAPI 进程。
- 根目录新增 `pnpm start:local`、`pnpm start:local:watch`、`pnpm stop:local`。
- 首页对“后端未连接”改为明确弹窗，引导运行 `pnpm start:local`，避免短 toast 被误认为按钮无效。

## 2026-06-30 保存逻辑修复

- 原处理页只调用 `Taro.downloadFile`，该 API 按微信机制只会返回临时文件路径，因此提示“已保存到临时文件”。
- 已改为下载完成后优先调用 `Taro.saveVideoToPhotosAlbum`，失败时尝试 `Taro.saveImageToPhotosAlbum`。
- 非视频/图片文件会回退到 `Taro.saveFile`，提示“已保存到小程序文件”，并建议复制下载链接到浏览器保存。
- 处理页按钮从“保存到本地”改为“保存到相册”，FAQ、教程和首页步骤文案同步修正。


## 2026-06-29T17:28:37Z

Starting automated verification.

## 2026-06-29T17:30:07Z

Automated verification completed successfully.

## 2026-06-30T05:41:58Z

Starting automated verification.

## 2026-06-30T05:43:23Z

Automated verification completed successfully.
