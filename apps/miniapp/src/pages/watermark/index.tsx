import React, { useState } from "react";
import { Button, Text, View } from "@tarojs/components";
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

export default function WatermarkPage() {
  const [filePath, setFilePath] = useState("");
  const [fileLabel, setFileLabel] = useState("");
  const [preset, setPreset] = useState<Preset>("douyin_both_corners");
  const [repairMode, setRepairMode] = useState<RepairMode>("opencv");
  const [removeSubtitle, setRemoveSubtitle] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function chooseFile() {
    try {
      const res = await Taro.chooseMedia({
        count: 1,
        mediaType: ["image", "video"],
        sourceType: ["album", "camera"]
      });
      const file = res.tempFiles[0] as { tempFilePath: string; size?: number; fileType?: string };
      setFilePath(file.tempFilePath);
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
    setSubmitting(true);
    try {
      Taro.showLoading({ title: "上传中..." });
      const task = await uploadWatermarkFile(filePath, {
        mask_preset: preset,
        repair_mode: repairMode,
        remove_subtitle: removeSubtitle,
        confirm_authorized: confirmed
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

        <View className='watermark-preview'>
          <Text className='dash-label logo'>LOGO</Text>
          <Text className='dash-label subtitle'>字幕文字</Text>
          <Text className='dash-label corner'>角标</Text>
          <Text className='center-play'>▶</Text>
        </View>

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
              <View className='mode-desc'>IOPaint 可用时启用</View>
            </View>
          </View>
        </View>

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
