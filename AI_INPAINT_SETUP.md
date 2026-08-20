# AI 视频去水印功能配置说明

## 概述

GreenVideo AI 支持两种视频/图片去水印模式：

| 模式 | 技术方案 | 效果 | 速度 | 资源占用 | 是否需要额外部署 |
|------|----------|------|------|----------|------------------|
| **标准修复** | OpenCV Inpaint | 一般，适合简单水印 | ⚡ 快 | 极低 | ❌ 开箱即用 |
| **AI 修复** | LaMa 深度学习模型 | ✅ 更好，自然无痕 | 🐢 较慢 | 中等 | ✅ 需要部署 IOPaint 服务 |

---

## 标准修复模式（默认）

无需任何配置，开箱即用。使用 OpenCV 的 TELEA/NS 算法进行传统图像修复。

**适用场景**：
- 简单背景上的固定位置水印
- 对修复效果要求不高
- 追求处理速度
- 服务器资源有限

---

## AI 修复模式（推荐）

使用 IOPaint + LaMa 模型，基于深度学习的图像修复技术，效果更自然。

### 架构设计

```
┌─────────────────┐    HTTP API    ┌─────────────────┐
│  FastAPI 后端   │ ─────────────▶ │  IOPaint 服务   │
│  (本项目)       │ ◀───────────── │  (独立部署)     │
└─────────────────┘                └─────────────────┘
         │
         ▼
┌─────────────────┐
│   前端小程序    │
└─────────────────┘
```

### 为什么采用独立服务架构？

1. **轻量**：主项目保持轻量，不强制依赖 PyTorch 等重型库
2. **灵活**：IOPaint 可单独部署在 GPU 服务器上
3. **可扩展**：支持多种模型切换（LaMa、MAT、Stable Diffusion 等）
4. **容错**：AI 服务不可用时自动降级到标准模式

---

## IOPaint 服务部署

### 方式一：Docker 部署（推荐）

```bash
# 拉取镜像
docker pull cwq1990/iopaint:latest

# 启动服务（CPU 模式，LaMa 模型）
docker run -d -p 8080:8080 --name iopaint \
  cwq1990/iopaint:latest \
  start --model=lama --device=cpu --port=8080
```

### 方式二：本地 Python 部署

#### 1. 创建独立虚拟环境（推荐）

```bash
# Windows PowerShell
python -m venv iopaint-env
.\iopaint-env\Scripts\Activate.ps1

# macOS/Linux
python3 -m venv iopaint-env
source iopaint-env/bin/activate
```

#### 2. 安装 IOPaint

```bash
# 基础安装（CPU 模式）
pip install iopaint

# GPU 加速（需要 NVIDIA 显卡）
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
pip install iopaint
```

#### 3. 启动服务

```bash
# 使用 LaMa 轻量模型（推荐，约 200MB）
iopaint start --model=lama --device=cpu --port=8080

# 使用 MAT 模型（约 350MB，质量更好）
iopaint start --model=mat --device=cpu --port=8080

# GPU 加速（如果有 NVIDIA 显卡）
iopaint start --model=lama --device=cuda --port=8080
```

### 方式三：Windows 一键安装包

下载地址：https://www.iopaint.com/install/windows_1click_installer

---

## 项目配置

### 环境变量配置

在 `.env` 文件中添加：

```bash
# 是否启用 AI 修复功能
GREENVIDEO_IOPAINT_ENABLED=true

# IOPaint 服务地址
GREENVIDEO_IOPAINT_BASE_URL=http://127.0.0.1:8080

# 使用的模型（lama / mat / migan）
GREENVIDEO_IOPAINT_MODEL=lama
```

### 模型选择建议

| 模型 | 大小 | 速度 | 质量 | 适用场景 |
|------|------|------|------|----------|
| **migan** | ~50MB | ⚡⚡⚡ 最快 | ⭐⭐ | 极度轻量场景 |
| **lama** | ~200MB | ⚡⚡ 快 | ⭐⭐⭐ | 推荐，平衡选择 |
| **mat** | ~350MB | ⚡ 中等 | ⭐⭐⭐⭐ | 质量优先 |
| zits | ~500MB | 🐢 慢 | ⭐⭐⭐⭐ | 复杂纹理 |

---

## 验证部署

### 1. 检查 IOPaint 服务

```bash
curl http://127.0.0.1:8080/api/health
```

返回 `{"status": "ok"}` 表示服务正常。

### 2. 检查项目集成

启动后端后，调用 API 测试：

```bash
curl -X POST http://127.0.0.1:8000/api/tasks/watermark \
  -H "Content-Type: application/json" \
  -d '{"mode": "ai", "mask_regions": []}'
```

### 3. 前端验证

在小程序「AI 视频去水印」页面：
1. 选择「AI 修复」模式
2. 上传视频/图片
3. 查看处理结果

---

## 性能参考（CPU 模式）

| 任务类型 | 标准修复 | AI 修复（LaMa） |
|----------|----------|-----------------|
| 图片 1080p | < 1 秒 | 3-5 秒 |
| 视频 10秒 1080p | 5-10 秒 | 2-5 分钟 |
| 内存占用 | ~50MB | ~1-2GB |

> **注意**：视频 AI 修复是逐帧处理的，耗时较长。建议在前端提示用户预计等待时间。

---

## 常见问题

### Q: AI 模式和标准模式有什么区别？

A: 标准模式使用传统的 OpenCV 算法，速度快但效果一般；AI 模式使用深度学习模型，修复效果更自然无痕，但速度较慢且需要更多资源。

### Q: 不部署 IOPaint 可以使用 AI 模式吗？

A: 可以选择 AI 模式，但系统会自动检测到 IOPaint 服务不可用，自动降级到标准修复模式。

### Q: 可以部署到 GPU 服务器加速吗？

A: 可以。将 IOPaint 部署到 GPU 服务器，然后修改 `GREENVIDEO_IOPAINT_BASE_URL` 指向 GPU 服务器地址即可。

### Q: 支持其他模型吗？

A: IOPaint 支持多种模型（LaMa、MAT、MIGAN、ZITS、Stable Diffusion 等），修改 `GREENVIDEO_IOPAINT_MODEL` 即可切换。

---

## 生产部署建议

1. **独立部署**：IOPaint 服务与主后端分开部署，便于扩缩容
2. **GPU 加速**：生产环境建议使用 GPU 服务器，处理速度提升 10-20 倍
3. **队列隔离**：AI 修复任务使用独立的 Celery 队列，避免阻塞普通任务
4. **超时设置**：视频 AI 修复耗时较长，需调整请求超时时间
5. **缓存策略**：相同区域的水印修复可以缓存 mask，提升效率
