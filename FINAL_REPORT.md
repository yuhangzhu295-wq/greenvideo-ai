# GreenVideo AI 最终修复报告

## 执行结论

已按 `greenvideo_ai_codex_repair_with_materials_prompt.md` 对项目完成修复。参考截图只用于定位问题，最终以文本要求为准：核心按钮不再停留在静态 UI 或 mock 提示，解析、下载任务、上传去水印、历史记录、反馈和页面跳转均已接入后端，并完成自动校验与自动修理闭环。

## 主要修复

- 首页解析：支持整段分享文案输入，自动提取链接并调用 `/api/parse`。
- 错误归类：对需要登录、私密、DRM、平台限制、频率限制等场景返回结构化中文提示，避免“服务连接失败”误导用户。
- 下载任务：结果页创建真实下载任务，处理页按任务 ID 轮询状态，支持取消和历史记录写入。
- 去水印任务：工具页接入本地图片/视频选择、上传、任务创建、处理进度和结果跳转。
- 水印位置：按用户素材补充 Douyin 左上角与右下角预设，支持 `douyin_top_left`、`douyin_bottom_right`、`douyin_both_corners`。
- 授权确认：去水印/局部修复任务必须勾选“自有或已授权素材”后才能提交。
- 后端安全：补齐 SSRF 防护、路径穿越防护、上传类型/大小校验和文件下载安全路径校验。
- 依赖边界：使用 yt-dlp 做公开视频 metadata/授权下载能力；使用 OpenCV 做默认局部修复；FFmpeg 用于视频帧/音频处理；PaddleOCR、IOPaint 仅作为可选高级能力。

## 素材验证

本次校验使用了用户本机素材：

```text
C:\Users\zyu33\Desktop\f553635d2972ffa3bca4b94d7ad10b42.mp4
```

验证结果：

- 使用 demo fallback：`False`
- 任务状态：`completed`
- 输出视频：`artifacts\watermark_output.mp4`
- 原视频时长：`10.266667`
- 输出视频时长：`10.239002`
- 时长差校验：`<= 1s`
- 首帧/末帧的处理前后截图均已导出。

详细记录见 `artifacts/watermark_validation_report.md`。

## 分享链接验证

自动校验对 Douyin、Bilibili、Kuaishou 示例分享链接执行了解析测试。结果不是“按钮无效”，而是被正确归类为合规限制：

- Douyin：`LOGIN_REQUIRED_NOT_SUPPORTED`
- Bilibili：`PLATFORM_BLOCKED_OR_RATE_LIMITED`
- Kuaishou：`PLATFORM_BLOCKED_OR_RATE_LIMITED`

详细记录见 `artifacts/share_links_report.md`。

## 自动验证结果

最终执行：

```bash
bash scripts/verify.sh
```

通过项目：

- 后端 Ruff：通过
- 后端 Pytest：`20 passed, 1 warning`
- API smoke：通过
- 分享链接归类校验：通过
- 本地素材去水印校验：通过
- 前端 lint：通过
- 前端 typecheck：通过
- 前端 `build:weapp`：通过
- 静态空处理函数扫描：通过

## 剩余边界

真实平台可能因为登录、验证码、地区、频率、平台策略或 DRM 限制拒绝公开视频解析。本项目不会绕过这些限制；用户可上传本地自有或授权素材继续处理。真机调试时需要把 `TARO_APP_API_BASE_URL` 改为手机可访问的局域网后端地址。

## 2026-06-30 运行态修复

用户在微信开发者工具中再次验证时，截图显示小程序已编译但本地 FastAPI 未运行，导致按钮请求 `http://127.0.0.1:8000` 失败。已补充：

- `pnpm start:local`：自动安装后端依赖、启动 FastAPI、健康检查通过后重建微信小程序。
- `pnpm start:local:watch`：启动后端并进入 Taro watch 构建。
- `pnpm stop:local`：停止脚本启动的本地 FastAPI。
- 前端离线提示改为弹窗，明确提示运行 `pnpm start:local`，避免只显示短 toast。
