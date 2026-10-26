# TEST_MATERIALS

## 本地视频素材

优先使用 Windows 本地素材：

```text
C:\Users\zyu33\Desktop\f553635d2972ffa3bca4b94d7ad10b42.mp4
```

如果文件不存在，验证脚本会生成带模拟水印的 10 秒 demo mp4；如果 FFmpeg 不可用，则生成 fallback 文件并在报告里明确标记。

## 去水印位置

- 左上角：适合抖音 Logo、抖音号 `85547992731`、搜索文字区域，默认比例 `x=0% y=0% w=42% h=18%`。
- 右下角：适合右下角抖音 Logo、抖音号区域，默认比例 `x=60% y=78% w=40% h=18%`。
- 双角：同时处理左上和右下，作为本次本地素材验证默认值。
- 字幕区域默认不处理，只有 `remove_subtitle=true` 时才加入 mask。

## 分享链接验证

抖音分享文案：

```text
4.17 复制打开抖音，看看【田宇轩的作品】事实证明是58还是能有爱情的吗？# 158身高 ... https://v.douyin.com/qu2PVcn74Lw/ E@H.vf 02/06 uSL:/ :6pm
```

B 站链接：

```text
【我给Codex做了一个分镜台，Codex秒变视频分镜脚本工作台】https://b23.tv/UlH6LvD
```

快手链接：

```text
https://v.kuaishou.com/KDINf2L9 用最平静的声音说出最绝望的话 “缅甸诈骗电话 ” 毁掉 该作品在快手被播放过1,811万次，点击链接，打开【快手】直接观看！
```

验证判定：

- `success`：平台解析成功。
- `expected_blocked`：返回登录、私密、DRM、平台限制、反爬或频率限制等合规错误。
- `failed_bug`：接口 500、连接失败、未提取 URL、JSON 结构错误或非预期错误。

---

# 更新（2026-10-25）

## 当前实际使用的素材与脚本

本地视频素材仍为首选（路径见上）。以下是本次建立的全部 Ground Truth 素材与验证脚本。

### 合成 Ground Truth（6 场景）

构建脚本 `scripts/build_ground_truth_set.py`。水印与遮罩由 Python 从**同一坐标列表**合成，从结构上排除错位。背景刻意使用平滑渐变 + 抗锯齿圆形——ffmpeg `testsrc2` 之类高频图案会把水印信号埋进编码噪声，任何量化对比都失去意义。

| 场景 | 水印类型 |
| --- | --- |
| T01_fixed_corner_text | 固定左上文字 |
| T02_fixed_logo | 固定右下 Logo |
| T03_semitransparent | 半透明文字（带底板保证可见） |
| T04_moving | 四位置移动水印 |
| T05_multiple | 三处同时水印 |
| T06_complex_motion | 复杂运动背景 |

### 真实素材 Ground Truth（3 场景）

构建脚本 `scripts/build_real_footage_gt.py`。取自本地视频，无水印原片 + Python 合成水印 + 同源坐标遮罩。

| 场景 | 水印类型 |
| --- | --- |
| real_portrait | 白色 `WATERMARK` 文字 |
| real_landscape | 右下红白 Logo |
| real_screen | 右下红白 Logo |

### 两阶段片尾水印

构建与验证脚本 `scripts/eval_endcard_2phase.py`。内容期右下角水印 + 片尾中央水印，切换点由用户显式确认（1.5s）。遮罩区域来自前端 `buildTimedRegions("douyin_both_corners", {width:720,height:960}, 1.5)` 的实际输出。

### 用户真实视频

`services/api/storage/uploads/user_source.mp4`（522 帧 / 720x960 / 751 音频包）。已批准产物 `services/api/storage/test_output/user_result7.mp4`，音频 PCM SHA256 与源一致 `6224bb08b425dc2c3544bd6b66a28abde6928eb70ddb480e32e652c4de298af9`。

### 验证脚本清单

| 脚本 | 用途 |
| --- | --- |
| `scripts/watermark_e2e.py` | 合成 6 场景 API 全流程验收（上传→任务→下载→ffprobe→PSNR/SSIM） |
| `scripts/eval_autodetect_e2e.py` | 合成 6 场景 auto_detect 质量测量 |
| `scripts/eval_real_footage_e2e.py` | 真实素材 3 场景质量测量 |
| `scripts/submit_real_footage_autodetect.py` | 真实素材 auto_detect 任务提交（配对用上传文件 SHA256） |
| `scripts/eval_endcard_2phase.py` | 两阶段片尾水印端到端（真实 LaMa + 独立质量门禁） |
| `scripts/measure_repair_quality.py` | PSNR/SSIM/闪烁/音轨公共度量 |
| `scripts/eval_detector_v2.py` / `eval_detector_real.py` | 检测器 IoU 与误遮评估 |
| `scripts/tune_detector.py` | 检测器参数调优 |

### 质量门禁（独立于实现）

PASS 需同时满足：`gain_mean ≥ 5 dB`、`gain_min ≥ -3 dB`、`outside_mean ≥ 25 dB`、帧数保持、音频保留；片尾场景另需条带 933..949 遮罩覆盖率 ≥99%。

## 素材合规

所有 Ground Truth 水印均为本地合成或取自用户提供素材，不涉及未授权平台视频。分享链接验证仅用于确认解析边界，不用于下载或作为修复验收依据。
