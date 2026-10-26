# 审查代理复核状态

生成时间：2026-10-25；最新更新：2026-10-27

## 当前状态：**最新独立审查 REJECTED，尚未修复全部问题**

以下第 11 轮“等待复核”记载的是当时的历史状态，**不能视作当前结论**。最新审查结论、待修事项和实测见本文件末尾及 `docs/RESTART_HANDOFF.md`。

第 11 轮审查代理当时派发失败，原因是网络/供应商问题，非代码问题：

| 尝试的代理 | 结果 |
| --- | --- |
| `code-reviewer`（多轮重试） | `dial tcp ... connectex: 连接尝试失败`（上游网络不可达） |
| `reviewer-glm`（用户指定） | `provider-not-found`：供应商 `account:zai-start-plan/GLM-5.3` 不存在或不可用 |
| `final-gate` | `auth_unavailable`：供应商配额已耗尽（`RESOURCE_EXHAUSTED`，142 小时后重置） |
| `code-reviewer`（再次重试） | 仍为上游网络不可达（多IP均 `connectex` 超时） |

**因此第 11 轮独立复核尚未完成。** 在审查代理恢复前，以下结论均为工作代理自验，不构成独立审查通过。

## 已完成并自验的修复（待独立复核确认）

第 10 轮审查 REJECTED 的 4 项 P0（根因：帧数校验信任 `nb_frames`，碎片化 MP4 缺该字段导致校验被跳过）：

| # | 修复 | 自验证据 |
| --- | --- | --- |
| 1 | 新增 `count_packets()` / `count_decoded_frames()` 真实解复用计数 | 碎片化 MP4（`nb_frames`=N/A）正确返回 3；仅音频/不存在/零字节/header-only 均返回 None |
| 2 | 修复自身 bug：`-select_streams video:0` 不被 ffprobe 接受，每次返回 None（守卫失败开放） | 新增 `_ffprobe_stream_selector()`；碎片化 MP4 现返回 3 而非 None |
| 3 | AI 路径不再用输入循环计数充当输出帧数 | 交付文件独立计数，与处理帧数、输入真实包数三方比对 |
| 4 | OpenCV 路径三重校验 + `files.py` 可解码帧校验 | 构造真零包 header-only MP4（有视频流、零包），下载接口返回 409 `OUTPUT_NOT_PLAYABLE` |

### 攻击复现结果（工作代理自行执行）

| 攻击 | 结果 |
| --- | --- |
| writer 已打开但产出碎片化短视频（无 `nb_frames`） | 拦截：`OpenCV 写出器没有生成任何视频数据` |
| writer 未打开 | 拦截：`无法创建视频写出器（编码器不可用）` |
| 尾部损坏源（faststart 后截断） | 拦截：`未能从输入视频解码任何帧` |
| header-only MP4 走下载接口 | 拦截：409 `OUTPUT_NOT_PLAYABLE` |
| video/audio 计数混淆 | 未发生：普通 MP4 video=3 / audio=13 |
| `frame_stride` 跳帧少计 | 不可达：无调用方传入该参数 |
| 图片路径空 mask（输出与输入逐字节相同） | 诚实：`engine=none` + `degraded=True`，任务层 `engine == "none"` 判 failed |
| 取消路径（第 2 帧取消） | 抛 `AiServiceCancelled`，任务 canceled，输出已 `_discard_output()` 删除 |
| OpenCV 路径 + ffmpeg 不可用 + 源有音轨 | 拦截：`FFmpeg 不可用，无法保留原始音轨；已中止而非输出无音轨视频。` |
| OpenCV 路径 + ffmpeg 不可用 + 源无音轨 | completed（预期，无音轨可丢失），4 包 |

### 严格守卫未误杀合法输出

| 场景 | 结果 |
| --- | --- |
| 无音轨源（OpenCV 路径） | completed，4 包 |
| 碎片化源（`nb_frames`=N/A） | completed，4 包 |
| 碎片化源 + 音轨 | completed，4 视频包 / 18 音频包 |
| 用户真实视频 522 帧（API 全流程） | completed，522 视频包 / 751 音频包 / 522 可解码帧，音频 PCM 与源一致 |

### 回归

- ruff：All checks passed!
- pytest：**29 passed**（含 `test_no_fake_success.py` 8 个假成功回归测试）
- pip check：No broken requirements found
- 前端 lint / typecheck / weapp 编译：全通过
- 两阶段端到端（真实 LaMa）：VERDICT PASS（gain +5.47 dB，最差 +2.53 dB，遮罩外 36.86 dB，条带覆盖率 100%）
- 合成 6 场景 auto_detect：6/6 PASS
- Git：HEAD=da209ee，master 未动，fsck 干净，未推送

## 审查代理恢复后应复核的重点

1. **`_ffprobe_stream_selector` 是否真的修好了失败开放**——这是最关键的一项。上一轮正是因为 helper 每次返回 `None` 而守卫放行。复核方应自行在碎片化 MP4 上调用 `count_packets` 确认返回真实数字而非 `None`。
2. **四项攻击是否真的被拦截**——特别是"writer 已打开但产出短视频"和"尾部损坏源解码提前停止"。
3. **严格守卫是否误杀合法输出**——用户视频 522 帧全流程必须仍然 completed。产物位于 `services/api/storage/files/task_e496bdeb01545b84.mp4`。
4. **是否还有未发现的假成功路径**——尤其是图片修复路径、任务取消路径、无音轨源路径。

## 已知限制（诚实记录，非假成功）

| 项 | 状态 |
| --- | --- |
| `real_portrait` auto_detect 增益 +4.47 dB 未达 5 dB 门禁 | 检测器 IoU 0.695、覆盖真值 76.5%，剩余 23.5% 白色文字像素未遮罩。任务诚实报告真实引擎与覆盖率，独立门禁判 FAIL 是正确结果 |
| 微信开发者工具内人工操作 | NOT TESTED |
| 无授权清晰的外部真实水印视频 | BLOCKED |
| 4 秒/640x360 与 522 帧/720x960 以外的时长与分辨率 | 未系统测试 |
| iOS/Android 真机 | 未验证 |
| Redis + Celery 配置 | 未处理，`.env.example` 仍配 Redis broker 有误导 |
| pnpm 版本解析 | 未处理，`corepack pnpm` 与 `packageManager` 冲突 |
| 真实平台下载 | 该行是历史状态。当前已经接入 yt-dlp，MP4/MP3 正常样例跑通，但存在最新独立审查发现的截断媒体与音频校验缺陷，尚未验收 |

## 最新独立审查（2026-10-27）：REJECTED

此轮审查由 `code-reviewer` 独立运行，不能以工作代理此前的 PASS 替代其结论。审查实测正常 LaMa 双阶段修复 30/30 帧、平均 PSNR 增益 +5.466 dB；正常本地 HTTP 视频和 MP3 下载可成功。但发现以下未解决问题：

1. **P0 音频假成功**：有音频流头、无 `mdat`/无可计数音频包的 `.mp3`，任务仍 `completed`，文件接口仍交付。`watermark.py` `_audio_stream_status`、`tasks.py` 的下载完成校验和 `files.py` 只检查音频流声明，须验证可解码音频包/帧数。
2. **P0 截断下载视频假成功**：faststart MP4 声明 40 帧而实际只有 39 视频包/39 解码帧；下载任务仍 completed 并交付。必须在处理和交付前核对声明帧数、实际包数、可解码帧数及探测错误。
3. **P0 截断源视频假成功**：同样的 40→39 截尾源可被 OpenCV 修复成 39 帧并报完成；当前修复守卫仅对比已经缩短的实际包数与处理帧数，未对比源声明帧数。
4. **P1 解析音频选项被误判为视频**：解析结果真实音频格式 ID（如 `140`）被 `build_selector` 当作视频，下载校验随后因无视频帧失败；纯音频直链的 `best` 也被误标为 MP4。
5. **P1 下载取消竞态**：校验完成到写入 `completed` 之间未再次检查取消状态；可把取消任务覆盖为完成。
6. **P1 下载异常无业务码**：yt-dlp 在发现和启动之间消失、解析超时，分别直接抛 `OSError`/`TimeoutExpired`，缺明确 `DownloadError` 分类。

**哈希记录勘误**：源视频 `services/api/storage/uploads/user_source.mp4`、先前批准的 `user_result7.mp4` 和新结果 `task_e496bdeb01545b84.mp4` 的音频 PCM 实测均为 `6224bb08b425dc2c3544bd6b66a28abde6928eb70ddb480e32e652c4de298af9`。此前多轮审查提示中将中间字节误写成 `...3544bd4b66...`；那个字面字符串与三份文件都不相等。音轨“与源一致”的事实成立，但**不要声称与错写的常量一致**。

本轮审查后曾开始修复这些问题，但**在用户准备重启前尚未完成上述六项修复或重新交审**。当前工作树应被视为中间检查点。审查报告详见本轮会话，恢复步骤见 `docs/RESTART_HANDOFF.md`。
