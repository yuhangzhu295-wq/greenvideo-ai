import React, { useState } from "react";
import { Button, Image, Input, Text, View, Video } from "@tarojs/components";
import Taro from "@tarojs/taro";
import { parseVideo, uploadWatermarkFile } from "@/api/client";
import type { WatermarkPayload } from "@/api/types";
import { BrandHeader, Card, IconBadge, PageShell, SectionTitle, navigate } from "@/components";

type Preset = NonNullable<WatermarkPayload["mask_preset"]>;
type RepairMode = NonNullable<WatermarkPayload["repair_mode"]>;

const presetOptions: Array<{ value: Preset; label: string; desc: string }> = [
  { value: "douyin_top_left", label: "左上水印", desc: "Logo、抖音号、搜索文字" },
  { value: "douyin_bottom_right", label: "右下水印", desc: "右下角 Logo 与账号" },
  { value: "douyin_both_corners", label: "左上 + 右下", desc: "适合本次测试素材" },
  { value: "custom", label: "自定义区域", desc: "仅使用手动区域参数" }
];

/**
 * Regions for the chosen preset, expressed as fractions of the real frame so
 * they scale to whatever the user picked. "both corners" yields two regions so
 * neither corner is skipped.
 *
 * The bottom-right band reaches the frame edge on purpose: platform watermarks
 * sit flush against it, and a box that stops short leaves the tail of the ID
 * text in place.
 */
function presetRegions(
  preset: Preset,
  W: number,
  H: number
): Array<{ x: number; y: number; w: number; h: number }> {
  const topLeft = { x: 0, y: 0, w: Math.round(W * 0.42), h: Math.round(H * 0.20) };
  const bottomRight = {
    x: Math.round(W * 0.55),
    y: Math.round(H * 0.76),
    w: W - Math.round(W * 0.55),
    h: H - Math.round(H * 0.76)
  };
  if (preset === "douyin_bottom_right") return [bottomRight];
  if (preset === "douyin_both_corners") return [topLeft, bottomRight];
  return [topLeft];
}

/**
 * Two-phase mask for clips whose watermark moves to the centre at the end. The
 * content phase reuses the selected preset regions; the end-card phase covers
 * the centred block.
 *
 * `splitSeconds` comes from the caller: a fixed constant would leave short
 * clips without any end-card coverage at all.
 */
function buildTimedRegions(
  preset: Preset,
  mediaSize: { width: number; height: number } | null,
  splitSeconds: number
): Array<{
  x: number; y: number; w: number; h: number; start_s: number; end_s?: number;
}> {
  // Without the real frame size the regions would be calibrated for another
  // resolution and silently miss the watermark, so the caller is told to pick
  // the file again rather than defaulting to a guess.
  if (!mediaSize || mediaSize.width <= 0 || mediaSize.height <= 0) {
    throw new Error("无法获取视频尺寸，请重新选择素材后再开启分时处理");
  }
  const W = mediaSize.width;
  const H = mediaSize.height;
  const split = Math.max(0.1, splitSeconds);
  const head = presetRegions(preset, W, H).map((r) => ({ ...r, start_s: 0, end_s: split }));
  const cardY = Math.round(H * 0.82);
  return [
    ...head,
    {
      x: Math.round(W * 0.35),
      y: cardY,
      w: Math.round(W * 0.30),
      h: H - cardY,
      start_s: split
    }
  ];
}

export default function WatermarkPage() {
  const [filePath, setFilePath] = useState("");
  const [fileLabel, setFileLabel] = useState("");
  const [preset, setPreset] = useState<Preset>("douyin_both_corners");
  const [repairMode, setRepairMode] = useState<RepairMode>("opencv");
  const [removeSubtitle, setRemoveSubtitle] = useState(false);
  const [autoDetect, setAutoDetect] = useState(true);
  // Some clips change watermark layout mid-video (corner overlay, then a
  // centred end card). This adds a second region for the tail of the video.
  const [endCardWatermark, setEndCardWatermark] = useState(false);
  const [mediaSize, setMediaSize] = useState<{ width: number; height: number } | null>(null);
  // When the end-card watermark starts. Seeded from the clip duration as a
  // starting guess, but an estimate can be wrong, so the user must confirm it
  // before a two-phase repair is submitted.
  const [splitSeconds, setSplitSeconds] = useState("");
  const [splitConfirmed, setSplitConfirmed] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function chooseFile() {
    try {
      const res = await Taro.chooseMedia({
        count: 1,
        mediaType: ["image", "video"],
        sourceType: ["album", "camera"]
      });
      const file = res.tempFiles[0] as {
        tempFilePath: string; size?: number; fileType?: string;
        width?: number; height?: number; duration?: number;
      };
      setFilePath(file.tempFilePath);
      // Regions are computed from the real frame size: a mask calibrated for one
      // resolution lands outside the frame on another and silently repairs
      // nothing.
      if (file.width && file.height) {
        setMediaSize({ width: file.width, height: file.height });
      } else {
        setMediaSize(null);
      }
      // Seed the end-card split from the clip's own duration so a short video
      // does not keep its whole length in the content phase. The user can still
      // override it.
      if (file.duration && file.duration > 0) {
        setSplitSeconds(String(Math.round(file.duration * 0.8)));
      } else {
        setSplitSeconds("");
      }
      setSplitConfirmed(false);
      const sizeMb = file.size ? `${(file.size / 1024 / 1024).toFixed(2)} MB` : "已选择";
      setFileLabel(`${file.fileType || "素材"} · ${sizeMb}`);
    } catch (err) {
      const msg = String((err as { errMsg?: string }).errMsg || "");
      if (!msg.includes("cancel")) Taro.showToast({ title: "选择素材失败", icon: "none" });
    }
  }

  async function parseClipboardLink() {
    try {
      const clip = await Taro.getClipboardData();
      if (!clip.data) {
        Taro.showToast({ title: "剪贴板为空", icon: "none" });
        return;
      }
      Taro.showLoading({ title: "解析中..." });
      const result = await parseVideo(clip.data);
      Taro.hideLoading();
      Taro.setStorageSync("parse_result", result);
      Taro.navigateTo({ url: "/pages/result/index" });
    } catch (error) {
      Taro.hideLoading();
      Taro.showToast({ title: error instanceof Error ? error.message : "解析失败", icon: "none", duration: 2600 });
    }
  }

  async function submit() {
    if (!filePath) {
      Taro.showToast({ title: "请先选择视频或图片", icon: "none" });
      return;
    }
    if (!confirmed) {
      Taro.showToast({ title: "请先确认素材授权", icon: "none" });
      return;
    }
    if (!autoDetect && endCardWatermark && !splitConfirmed) {
      Taro.showToast({ title: "请确认片尾开始时间", icon: "none" });
      return;
    }
    if (!autoDetect && endCardWatermark && !(Number(splitSeconds) > 0)) {
      Taro.showToast({ title: "请填写有效的片尾开始时间", icon: "none" });
      return;
    }
    setSubmitting(true);
    try {
      Taro.showLoading({ title: "上传中..." });
      const task = await uploadWatermarkFile(filePath, {
        mask_preset: preset,
        repair_mode: repairMode,
        remove_subtitle: removeSubtitle,
        auto_detect: autoDetect,
        confirm_authorized: confirmed,
        timed_mask_regions: (!autoDetect && endCardWatermark)
          ? buildTimedRegions(preset, mediaSize, Number(splitSeconds) || 12)
          : []
      });
      Taro.hideLoading();
      Taro.showToast({ title: "任务已创建", icon: "success" });
      setTimeout(() => navigate(`/pages/processing/index?taskId=${task.task_id}`), 600);
    } catch (error) {
      Taro.hideLoading();
      Taro.showToast({ title: error instanceof Error ? error.message : "上传失败", icon: "none", duration: 2800 });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <PageShell active='tools'>
      <BrandHeader brand subtitle='智能视频提取与处理' hero='video' />

      <Card className='watermark-card'>
        <View className='card-head'>
          <View>
            <View className='page-title'>AI 视频去水印</View>
            <View className='muted'>智能识别 LOGO / 字幕 / 角标</View>
          </View>
          <View className='safe-pill'>AI 智能识别 · 精准去除</View>
        </View>

        {filePath ? (
          <View className='watermark-video-wrapper'>
            {/\.(jpe?g|png|webp)($|\?)/i.test(filePath) || fileLabel.startsWith("image") ? (
              <Image
                src={filePath}
                className='watermark-video-player'
                mode='aspectFit'
              />
            ) : (
              <Video
                src={filePath}
                className='watermark-video-player'
                controls
                autoplay={false}
                showCenterPlayBtn
              />
            )}
            <View className='watermark-track-tag'>AI 逐帧追踪就绪</View>
          </View>
        ) : (
          <View className='watermark-preview' onClick={chooseFile}>
            <Text className='dash-label logo'>智能感知</Text>
            <Text className='dash-label subtitle'>逐帧追踪</Text>
            <Text className='dash-label corner'>角标/二维码</Text>
            <Text className='center-play'>+ 选择素材</Text>
          </View>
        )}

        <SectionTitle title='选择水印位置' />
        <View className='preset-grid'>
          {presetOptions.map((option) => (
            <View
              key={option.value}
              className={`preset-chip ${preset === option.value ? "active" : ""}`}
              onClick={() => setPreset(option.value)}
            >
              <View>{option.label}</View>
              <Text>{option.desc}</Text>
            </View>
          ))}
        </View>

        <View className='mode-selector'>
          <View className='mode-title'>选择修复模式</View>
          <View className='mode-options'>
            <View
              className={`mode-option ${repairMode === "opencv" ? "active" : ""}`}
              onClick={() => setRepairMode("opencv")}
            >
              <View className='mode-icon'>↯</View>
              <View className='mode-name'>标准修复</View>
              <View className='mode-desc'>OpenCV mask inpaint</View>
            </View>
            <View
              className={`mode-option ${repairMode === "ai" ? "active" : ""}`}
              onClick={() => setRepairMode("ai")}
            >
              <View className='mode-icon'>AI</View>
              <View className='mode-name'>AI 修复</View>
              <View className='mode-desc'>IOPaint LaMa 真实推理</View>
            </View>
          </View>
        </View>

        <View className='toggle-row' onClick={() => setAutoDetect(!autoDetect)}>
          <Text className={`checkbox ${autoDetect ? "checked" : ""}`}>{autoDetect ? "✓" : ""}</Text>
          <View>
            <View className='feature-title'>自动检测水印区域与片尾</View>
            <View className='muted'>开启后智能定位水印与平台片尾；关闭后可使用手动预设</View>
          </View>
        </View>

        {!autoDetect && (
          <View className='toggle-row' onClick={() => { setEndCardWatermark(!endCardWatermark); setSplitConfirmed(false); }}>
            <Text className={`checkbox ${endCardWatermark ? "checked" : ""}`}>{endCardWatermark ? "✓" : ""}</Text>
            <View>
              <View className='feature-title'>手动设置片尾分时水印</View>
              <View className='muted'>视频末尾出现居中片尾水印时开启，将按时间段分别处理</View>
            </View>
          </View>
        )}

        {!autoDetect && endCardWatermark && (
          <View className='toggle-row'>
            <View>
              <View className='feature-title'>片尾开始时间（秒）</View>
              <View className='muted'>
                已按视频时长预估 {splitSeconds || "?"} 秒。请核对实际转场时刻并修改，然后勾选确认。
              </View>
              <Input
                className='split-input'
                type='number'
                value={splitSeconds}
                onInput={(e) => { setSplitSeconds(e.detail.value); setSplitConfirmed(false); }}
                placeholder='例如 12'
              />
              <View className='toggle-row' onClick={() => setSplitConfirmed(!splitConfirmed)}>
                <Text className={`checkbox ${splitConfirmed ? "checked" : ""}`}>{splitConfirmed ? "✓" : ""}</Text>
                <View>
                  <View className='feature-title'>确认该时间正确</View>
                  <View className='muted'>预估时间可能偏差；未确认将无法提交，避免漏修水印</View>
                </View>
              </View>
            </View>
          </View>
        )}

        <View className='toggle-row' onClick={() => setRemoveSubtitle(!removeSubtitle)}>
          <Text className={`checkbox ${removeSubtitle ? "checked" : ""}`}>{removeSubtitle ? "✓" : ""}</Text>
          <View>
            <View className='feature-title'>同时清理字幕区域</View>
            <View className='muted'>默认关闭；只有你主动选择时才处理视频中部字幕</View>
          </View>
        </View>

        <View className='upload-zone' onClick={chooseFile}>
          <IconBadge icon='☁' />
          <View>
            <View className='upload-title'>上传视频 / 图片</View>
            <View className='muted'>支持 MP4 / MOV / AVI / JPG / PNG，最大 500MB</View>
            <View className='muted'>{fileLabel || "尚未选择素材"}</View>
          </View>
        </View>

        <View className='button-row three'>
          <Button className='secondary-btn' onClick={chooseFile}>
            ▣ 从相册选择
          </Button>
          <Button className='secondary-btn' onClick={parseClipboardLink}>
            ↗ 粘贴视频链接
          </Button>
          <Button className='secondary-btn' onClick={() => navigate("/pages/history/index?type=watermark")}>
            ◷ 导入历史任务
          </Button>
        </View>

        <View className='auth-box' onClick={() => setConfirmed(!confirmed)}>
          <Text className={`checkbox ${confirmed ? "checked" : ""}`}>{confirmed ? "✓" : ""}</Text>
          <Text>我确认该素材为本人自有或已获得授权，仅用于合法修复与个人学习。</Text>
        </View>

        <Button className='primary-btn full' loading={submitting} onClick={submit}>
          开始去水印处理
        </Button>
      </Card>

      <Card>
        <SectionTitle title='AI 去水印能力' />
        <View className='ability-grid'>
          {[
            ["▧", "局部修复", "智能填充背景，自然无痕", "green"],
            ["T", "字幕保护", "默认不清理正常字幕", "purple"],
            ["HD", "画质增强", "保留原始尺寸与音轨", "blue"]
          ].map((item) => (
            <View className='ability-card' key={item[1]}>
              <IconBadge icon={item[0]} tone={item[3]} />
              <View>
                <View className='ability-title'>{item[1]}</View>
                <View className='muted'>{item[2]}</View>
              </View>
            </View>
          ))}
        </View>
      </Card>
    </PageShell>
  );
}
