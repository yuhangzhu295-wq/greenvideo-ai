# 重启前进度与状态归档说明 (2026-10-29)

## 1. 当前 Git 状态
- 分支：`recovery/development`
- 遵循严苛负面约束：**未执行任何破坏性 reset/clean 操作，未擅自推送远程**。
- 所有代码均完好保存在本地工作区 `C:\Users\zyu33\Documents\Codex\greenvideo-ai\`。

## 2. 本次核心改造与已落地成果
1. **逐帧时空密集追踪与动态可视化**：
   - `services/api/app/services/watermark_detect.py`：实现 `track_watermarks_dense`（角标锚定、Sobel 梯度前景色特征点提取）与 `render_tracking_preview_video`（半透明翠绿遮罩 + 边框 L 标 + 黄色特征点）；
   - 支持漂移水印的密集 OCR 插值与端点扩展；
   - 实现后验残留校验 `verify_watermark_removal`。
2. **多模态视觉大模型（Gemini / VLM）感知服务**：
   - `services/api/app/services/vlm_grounding.py`：支持原生 Gemini 与 OpenAI 兼容视觉网关，无 Key 时自动平滑降级至本地 OCR + 光流。
3. **动态掩码提供器**：
   - `services/api/app/services/watermark.py`：实现 `FrameTrajectoryMaskProvider`，LaMa 修复与追踪轨迹逐帧严密对齐。
4. **极简前端 UI 与同源托管（彻底根治跨域上传报错）**：
   - `apps/miniapp/src/pages/home/index.tsx`：精简为纯粹的“视频去水印”入口，移除所有杂项；
   - `apps/miniapp/src/components/index.tsx`：移除“我的”等无关导航；
   - `services/api/app/main.py`：根路径同源挂载 H5 构建产物，彻底解决浏览器跨域与凭据冲突问题。
5. **测试全绿**：
   - `pytest tests/` 61 项测试全部通过（包含新增的 H5 serving、VLM、dense tracking、tracking api 测试）。
   - 小程序 `tsc --noEmit` 0 错误。

## 3. 重启后快速启动指令
重启电脑后，如需继续实测，只需在命令行执行以下命令即可启动全栈同源服务：
```powershell
# 1. 进入 API 目录并启动后端（同时同源托管极简前端）
cd C:\Users\zyu33\Documents\Codex\greenvideo-ai\services\api
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 2. （如果需要真实 AI 修复）启动 IOPaint 服务
iopaint start --model=lama --device=cpu --port=8080
```
启动后直接在浏览器中访问实测入口：
**http://127.0.0.1:8000/#/pages/home/index**
