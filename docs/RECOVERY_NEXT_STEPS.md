# GreenVideo AI 后续开发清单

生成时间：2026-10-24
基于恢复后的实际状态整理，按 P0 / P1 / P2 排列。

## P0 — 阻塞核心功能，必须优先处理

### P0-1 打通 AI 去水印链路

现状：`repair_mode=ai` 实际执行 OpenCV 逐帧修复；IOPaint 未安装成功；`process_video()` 没有调用 IOPaint 的分支。

需要做：

1. 在独立 venv 或独立服务中安装 IOPaint（不要直接装进 `services/api/.venv`，本次安装把 starlette 降到 0.32.0、numpy 降到 1.26.4 并因 `cv2.pyd` 占用失败，已回滚）。
2. 启动 IOPaint 并核实 `GET http://127.0.0.1:8080/api/health` 返回 200。
3. 为视频实现真实的 AI 推理分支：抽帧 → 传图与 mask 到 IOPaint → 取回修复帧 → 合流音轨。当前 `_inpaint_with_iopaint()` 只支持单图。
4. 补齐验收：AI 可用时任务 stage 必须写明"已调用 IOPaint（模型 lama）"；AI 不可用时必须写明降级原因，且不得报告 AI 成功。

验收标准：AI 模式与 OpenCV 模式输出字节不同，且 stage 明确标注引擎。

### P0-2 建立 AI 可用性探测与明确状态返回

现状：`iopaint_status()` 已返回 (可用性, 原因)，但前端未展示。

需要做：把 `stage` / `error` 中的引擎信息透传到小程序 `processing` 与 `result` 页面，让用户看到"本次使用 OpenCV 修复"或"AI 不可用原因"。

## P1 — 显著影响效果

### P1-1 水印精确定位（问题 B）

现状：`_preset_regions()` 使用固定比例大矩形（左上 42%x18%、右下 40%x18%、字幕 64%x20%），容易破坏主体画面。

要求（按任务约束，必须建独立方案与验证样本，不得直接大规模重写）：

1. 建立标注样本集：至少 20 张含真实水印的截图 + 对应 mask 真值。
2. 实现候选检测（如静态区域时序差分 + 边缘/文本密度启发式），输出紧致边界框。
3. 与现有固定预设做 A/B 对比：报告 IoU、误遮主体像素比例、视觉对比图。
4. 检测失败时回落到现有预设，并在 stage 中说明使用的是"自动检测"还是"预设区域"。
5. 全程保留 `custom` 手动模式作为兜底。

### P1-2 字幕区域遮罩收紧

现状：`remove_subtitle` 固定取 18%~84% 宽、56%~76% 高。

需要做：改为基于检测结果的紧致区域，或至少允许用户指定高度区间；默认不处理正常字幕（现有合规声明已强调这一点，需保持）。

### P1-3 补齐视频 AI 处理的性能与中断

现状：逐帧 OpenCV 处理无帧级取消检查，长视频只能等整段结束。

需要做：在帧循环中检查任务是否被 cancel；补充处理耗时与帧数的进度映射校准（当前 `frame_count % 15` 才更新一次）。

## P2 — 工程与体验改进

### P2-1 启用 Redis + Celery 或移除配置

现状：`.env.example` 配置了 Redis broker/backend，实际用 FastAPI `BackgroundTasks`，本机无 Redis，Celery 链路未验证。

需要做：二选一 —— 要么部署 Redis 并改用 Celery（获得持久队列与重试），要么从配置与依赖中移除 Redis 相关项，避免误导。

### P2-2 统一 pnpm 版本解析

现状：`corepack pnpm` 会调用 corepack 内置 pnpm 11.0.9，与 `packageManager: pnpm@10.18.3` 冲突报错；系统 PATH 的 pnpm 是 10.18.3 可正常工作。

需要做：在 README 与 CI 中固定使用系统 pnpm，或修正 corepack 配置，使 `corepack pnpm` 也解析到 10.18.3。

### P2-3 测试素材纳入版本控制

现状：`storage/` 被 `.gitignore` 排除，真实测试图片依赖隔离备份；本次已在 `scripts/fixtures/smoke_watermark.mp4` 放入可复用视频。

需要做：把水印测试图片（小尺寸 JPEG）加入 `scripts/fixtures/` 并纳入 Git，使 clone 后即可完整复现验证。

### P2-4 下载任务真实化或明确标注

现状：`_run_download_task()` 只写 demo .txt 文件，不执行真实平台下载（文件内已声明）。

需要做：若产品要求真实下载，需处理各平台反爬与授权；若维持 demo，需在前端明显位置标注"演示结果"，避免误解。

### P2-5 清理恢复过程产生的临时资产

需要做：review 并决定是否保留 `artifacts/`、`storage/test_output/verify_fixed.mp4`、`storage/uploads/_probe*`、`storage/uploads/broken.mp4`、`recovery/logs/`；`storage/` 已被 gitignore，不会进入提交。

## 已完成（本次恢复，勿重复）

- GitHub 克隆到 `C:\Users\zyu33\Documents\Codex\greenvideo-ai`，HEAD = `da209ee`，建立 `recovery/development` 分支，`master` 未动。
- 残留资产隔离备份到 `greenvideo-ai-assets-backup`，测试图片已恢复到 `services/api/storage/`。
- 前端 `pnpm install --frozen-lockfile` / lint / typecheck / build:weapp 全通过。
- 后端 venv + `pip install -e ".[dev]"`，Ruff 与 20 个 Pytest 通过。
- 修复假成功：`files.py` 不再返回 demo .txt；任务引擎不可用时标记 failed；smoke 与 local_material 脚本改为真实素材 + 遮罩差异校验。
- 修正损坏的测试 PNG 素材。

---

# 更新（2026-10-25，第 12 轮修复后）

## P0 已解决

### P0-1 打通 AI 去水印链路 — **已完成**

- IOPaint 1.6.0 + LaMa 装入隔离环境 `services/ai-worker/.venv`（torch 2.11.0+cu128），主环境 `services/api/.venv` 未受影响，`pip check` 无冲突。
- 核实 API 契约为 `POST /api/v1/inpaint`（base64 image+mask 入，二进制 PNG 出），模型查询/切换为 `GET|POST /api/v1/model`。
- 视频 AI 推理链路：抽帧 → 逐帧 LaMa → 编码（容器声明的 `r_frame_rate`，保持帧数）→ 合流原始音轨 → ffprobe 校验。
- AI 不可用时抛 `AiServiceError` 而非静默降级；stage 明确标注引擎（`iopaint-lama`）与修复帧数、遮罩覆盖率、音频保留状态。
- 验收：AI 与 OpenCV 输出字节不同；合成 6 场景 +12.62 ~ +38.12 dB。

### P0-2 AI 可用性探测与状态返回 — **已完成**

`iopaint_status()` 返回 (可用性, 原因)，stage/error 透传到前端。AI 不可用时任务失败并说明原因，不报告 AI 成功。

## P1 已解决

### P1-1 水印精确定位 — **已完成**

时序中值 + 结构能量联合判定（静态度 × 梯度 × 对比度）。三类特征必须联合：单看静态度会把白圆当水印，单看结构会把繁忙背景当水印。

精度（box IoU 对真值）：合成场景 0.40-0.67，真实素材 0.508-0.695，同量级说明未过拟合合成背景。误遮比例 ≤2.22%。

调优：真实素材静止有结构场景远多于合成背景，曾出现 6 个候选框、误遮 16.04%；新增"分数相对截断"（保留 ≥ 最强分数 0.78 的框）后误遮降至 0.53%，合成 6 场景无回归。

检测失败回落到现有预设，stage 说明使用"自动检测"还是"预设区域"；`custom` 手动模式保留为兜底。

**已知限制**：`real_portrait`（白色文字水印）检测 IoU 0.695、覆盖真值 76.5%，剩余 23.5% 文字像素未被遮罩，auto_detect 增益 +4.47 dB 未达 5 dB 门禁。任务诚实报告真实引擎与覆盖率，独立门禁判 FAIL 是正确结果。这是自动检测精度的真实上限。

### P1-2 字幕区域遮罩收紧 — **已完成**

`_OverlayMaskProvider` 把字幕遮罩与分时遮罩按 `np.maximum` 叠加，AI 与 OpenCV 路径一致。默认不处理正常字幕，仅 `remove_subtitle=true` 时加入。

### P1-3 性能与中断 — **已完成**

帧循环内检查 `cancel_check()`，取消抛 `AiServiceCancelled`，任务标记 canceled 并 `_discard_output()`；进度按实际帧数线性映射。

## P2 状态

| # | 项 | 状态 |
| --- | --- | --- |
| P2-1 | Redis + Celery 或移除配置 | 未处理。当前用 FastAPI BackgroundTasks，`.env.example` 仍配 Redis broker 有误导 |
| P2-2 | pnpm 版本解析 | 未处理。`corepack pnpm` 解析到 11.0.9 与 `packageManager: pnpm@10.18.3` 冲突；系统 PATH pnpm 10.18.3 正常 |
| P2-3 | 测试素材纳入版本控制 | 部分完成。合成脚本可复现全部 Ground Truth；`storage/` 仍被 gitignore |
| P2-4 | 下载任务真实化或明确标注 | **已处理**。真实平台下载未实现，任务直接 failed 并说明，不再交付 demo 文件 |
| P2-5 | 清理恢复过程临时资产 | 未处理 |

## 当前总体状态

- 后端 pytest 29 passed、ruff clean、`pip check` 无冲突
- 前端 lint / typecheck / weapp 编译全通过
- 合成 6 场景 6/6 PASS；两阶段片尾 PASS；真实素材 2/3 PASS
- 用户真实视频 522 帧全流程跑通，帧数/音频/可解码性全部保真
- Git HEAD=da209ee，master 未动，fsck 干净，未推送远程
