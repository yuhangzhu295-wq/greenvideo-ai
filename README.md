# GreenVideo AI

GreenVideo AI 是一个微信小程序风格的视频工具项目，包含 Taro 小程序前端和 FastAPI 后端。当前版本已经把参考图里的主要页面、解析、下载任务、去水印/局部修复、历史记录、教程、邀请、FAQ 和反馈链路接到真实 API，并保留合规失败提示与本地验证素材流程。

## 功能状态

- 首页“粘贴/开始解析”会调用 `/api/parse`，支持从整段分享文案中提取 URL。
- 平台链接无法公开解析时会返回明确原因，例如需要登录、私密、DRM、平台限制或频率限制，不再显示“服务连接失败”这种无定位错误。
- 下载结果页会通过 `/api/tasks/download` 创建任务，处理页按任务 ID 轮询真实状态。
- 工具页“AI 视频去水印”支持本地图片/视频上传，必须确认素材为自有或已授权后才会创建任务。
- 后端提供 Douyin 常见水印位置预设：左上、右下、左右同时处理，并可选择是否处理字幕区域。
- 历史记录、任务删除、取消任务、反馈提交均已接入后端。

## 合规边界

本项目只用于用户自有、已获得授权或可合法处理素材的下载备份、格式转换和局部修复。项目不会实现 cookie 导入、账号登录抓取、代理池、验证码绕过、DRM 破解、付费/私密内容绕过或平台限制绕过。上游平台要求登录、验证码或限制访问时，系统只给出友好提示，并引导用户上传本地授权素材继续处理。

## 项目结构

```text
greenvideo-ai/
  apps/miniapp/          # Taro + React + TypeScript 小程序
  services/api/          # FastAPI + SQLite + 媒体处理服务
  scripts/               # 自动验证、API smoke、链接与素材验证
  artifacts/             # 自动验证产物
  docs/                  # 测试素材与运行说明
```

## 后端运行

Windows PowerShell:

```powershell
cd C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\services\api
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Bash/WSL:

```bash
cd /mnt/c/Users/zyu33/Documents/Codex/2026-06-27/yi/outputs/greenvideo-ai/services/api
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

健康检查：

```bash
curl http://127.0.0.1:8000/api/health
```

## 小程序运行

推荐本地一键启动：

```powershell
cd C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai
pnpm start:local
```

这个命令会自动创建/更新后端虚拟环境，启动 `http://127.0.0.1:8000`，等待 `/api/health` 可访问，并用 `TARO_APP_API_BASE_URL=http://127.0.0.1:8000` 重新构建微信小程序 dist。然后在微信开发者工具中打开 `apps/miniapp`。

停止本地后端：

```powershell
pnpm stop:local
```

需要持续监听源码变化时使用：

```powershell
pnpm start:local:watch
```

手动运行方式：

```powershell
cd C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai
pnpm install --no-frozen-lockfile
pnpm --filter @greenvideo/miniapp dev:weapp
```

在微信开发者工具中打开 `apps/miniapp`。本地调试时建议把 `TARO_APP_API_BASE_URL` 设置为后端地址；真机预览需要换成局域网可访问的后端地址，并在开发者工具中按需关闭域名校验。

```powershell
$env:TARO_APP_API_BASE_URL="http://127.0.0.1:8000"
pnpm --filter @greenvideo/miniapp dev:weapp
```

浏览器 H5 预览：

```powershell
$env:TARO_APP_API_BASE_URL="http://127.0.0.1:8000"
pnpm dev:h5 -- --host 127.0.0.1 --port 10086
```

## 自动校验

完整验证命令：

```bash
bash scripts/verify.sh
```

脚本会自动执行：

- 后端依赖安装、Ruff、Pytest。
- 启动本地 API 并执行 smoke test。
- 用真实/示例分享链接验证解析失败是否被正确归类为合规限制。
- 使用 `C:\Users\zyu33\Desktop\f553635d2972ffa3bca4b94d7ad10b42.mp4` 作为本地素材进行去水印任务验证；如果素材不存在才生成 demo 素材。
- 前端 `pnpm install`、lint、typecheck、`build:weapp`。
- 静态扫描空处理函数、TODO/mock-only 残留。

当前自动生成的关键产物：

- `artifacts/share_links_report.md`
- `artifacts/watermark_validation_report.md`
- `artifacts/watermark_output.mp4`
- `artifacts/watermark_before_001.jpg`
- `artifacts/watermark_after_001.jpg`
- `artifacts/watermark_before_last.jpg`
- `artifacts/watermark_after_last.jpg`

## 第三方组件

已按官方仓库核验 Taro、FastAPI、Uvicorn、Celery、Redis、yt-dlp、FFmpeg、OpenCV、PaddleOCR、IOPaint 的许可证和状态，记录在 `THIRD_PARTY_NOTICES.md`。默认本地修复路径使用 OpenCV inpaint；PaddleOCR 和 IOPaint 仅作为可选能力，不随项目打包模型权重。
