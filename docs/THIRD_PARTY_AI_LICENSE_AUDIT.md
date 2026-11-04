# 第三方 AI 模型许可证审计

更新时间：2026-11-05
审计人：主控代理（依据 architecture-reviewer 要求）

## 结论汇总

| 模型 | 许可证 | 商用 | 生产路径 | 用途 | 状态 |
|------|--------|------|----------|------|------|
| SAM 2.1 (facebookresearch/sam2) | Apache 2.0 | ✅ 允许 | ✅ 生产 | 像素级 watermark segmentation | **已集成** |
| LaMa / big-lama (IOPaint) | Apache 2.0 | ✅ 允许 | ✅ 生产（fallback） | 图像 inpainting / 静态小水印 | **已集成** |
| PaddleOCR (PP-OCRv6) | Apache 2.0 | ✅ 允许 | ✅ 生产 | 文字检测（prompt producer） | **已集成** |
| STTN (researchmm/STTN) | MIT（代码）/ CC-BY-NC 4.0（预训练权重） | ⚠️ 权重 NC | 待定 | 视频时序修复 worker | **待集成**（须用自训或 MIT 替代权重） |
| ProPainter (sczhou/ProPainter) | NTU SLab License（NonCommercial） | ❌ 禁止 | ❌ 禁止 | 仅 Benchmark | **仅 Benchmark** |
| E2FGVI (MCG-NKU/E2FGVI) | CC-BY-NC 4.0 | ❌ 禁止 | ❌ 禁止 | 仅 Benchmark | **仅 Benchmark** |

## 详细分析

### SAM 2.1 (Meta AI)
- **代码**：https://github.com/facebookresearch/sam2 — Apache License 2.0
- **权重**：sam2.1_hiera_large.pt — Apache License 2.0
- **商用**：明确允许，无限制
- **生产集成**：`services/ai-worker/sam2_worker.py`（常驻 GPU，stdin/stdout 协议）
- **风险**：无

### LaMa (IOPaint)
- **代码**：https://github.com/Sanster/IOPaint — Apache License 2.0
- **权重**：big-lama.pt — Apache License 2.0
- **商用**：允许
- **生产集成**：`services/ai-worker/lama_worker.py`（图片/小水印 fallback）
- **风险**：无

### PaddleOCR
- **代码**：https://github.com/PaddlePaddle/PaddleOCR — Apache License 2.0
- **模型**：PP-OCRv6 — Apache License 2.0
- **商用**：允许
- **生产集成**：`services/ai-worker/ocr_detector_cli.py`（文字检测 prompt）
- **风险**：无

### STTN
- **代码**：https://github.com/researchmm/STTN — MIT License（已 vendor 至 `services/sttn-worker/sttn/`，含 LICENSE 副本）
- **预训练权重**：官方发布权重使用 CC-BY-NC 4.0（NonCommercial）
- **商用**：代码 MIT 允许；**NC 权重不允许商用**
- **方案**：可集成 STTN 架构代码 + 自行训练权重（或使用 MIT 替代权重），**不可直接加载官方 NC 权重进生产**
- **状态**（2026-11-05）：Worker 已实现（`services/sttn-worker/sttn_worker.py`，GPU 常驻 + chunk/overlap 融合 + OOM 降档重试 + npz 传输 + mask 外像素逐位保持）。官方权重已下载至 `services/sttn-worker/weights/sttn.pth`（**git 已忽略，仅本机 benchmark**），实测 benchmark（`scripts/sttn_benchmark.py`）：outside_mask_bit_identical=1.0、inside_psnr=15.99（移动背景+移动水印合成片段）。**生产路径仍默认 LaMa**；STTN 仅在显式配置权重时可用，且 NC 权重限定 benchmark 用途，商用上线前必须替换为自训/MIT 权重。

### ProPainter
- **代码 + 权重**：https://github.com/sczhou/ProPainter — NTU SLab License 4.0（NonCommercial）
- **商用**：**明确禁止**
- **用途**：仅用于内部 Benchmark 对比，不得进入生产路径
- **风险**：高（若误入生产则违反许可证）

### E2FGVI
- **代码 + 权重**：https://github.com/MCG-NKU/E2FGVI — CC-BY-NC 4.0
- **商用**：**明确禁止**
- **用途**：仅用于内部 Benchmark 对比
- **风险**：高（同上）

## 执行策略

1. **生产路径**：仅使用 Apache 2.0 模型（SAM 2.1 + LaMa + PaddleOCR）
2. **Benchmark 路径**：ProPainter / E2FGVI 仅在独立 benchmark 脚本中运行，输出仅供质量对比报告，不进入交付管线
3. **STTN**：Worker 架构可先搭建（MIT 代码），权重来源另行解决；在解决前不作为主修复引擎
4. **盲测集**：Ground Truth 生成器仅用本项目自有代码（numpy + OpenCV），无第三方许可依赖
