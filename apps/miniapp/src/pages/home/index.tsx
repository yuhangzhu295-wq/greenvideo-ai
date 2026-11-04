import React, { useState } from "react";
import { Button, Image, Text, View, Video } from "@tarojs/components";
import Taro from "@tarojs/taro";
import { uploadWatermarkFile } from "@/api/client";
import type { WatermarkPayload } from "@/api/types";
import { BrandHeader, Card, IconBadge, PageShell, SectionTitle, navigate } from "@/components";

type RepairMode = NonNullable<WatermarkPayload["repair_mode"]>;

export default function HomePage() {
  const [filePath, setFilePath] = useState("");
  const [fileLabel, setFileLabel] = useState("");
  const [fileType, setFileType] = useState<"image" | "video">("video");
  const [repairMode, setRepairMode] = useState<RepairMode>("ai");
  const [confirmed, setConfirmed] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  async function chooseFile() {
    try {
      const res = await Taro.chooseMedia({
        count: 1,
        mediaType: ["image", "video"],
        sourceType: ["album", "camera"]
      });
      const file = res.tempFiles[0] as {
        tempFilePath: string;
        size?: number;
        fileType?: string;
      };
      setFilePath(file.tempFilePath);
      const isImg = file.fileType === "image" || /\.(jpe?g|png|webp)($|\?)/i.test(file.tempFilePath);
      setFileType(isImg ? "image" : "video");
      const sizeMb = file.size ? `${(file.size / 1024 / 1024).toFixed(2)} MB` : "已选择";
      setFileLabel(`${isImg ? "图片素材" : "视频素材"} · ${sizeMb}`);
    } catch (err) {
      const msg = String((err as { errMsg?: string }).errMsg || "");
      if (!msg.includes("cancel")) {
        Taro.showToast({ title: "选择素材失败", icon: "none" });
      }
    }
  }

  async function submit() {
    if (!filePath) {
      Taro.showToast({ title: "请先选择需要去水印的视频或图片", icon: "none" });
      return;
    }
    if (!confirmed) {
      Taro.showToast({ title: "请先确认素材授权", icon: "none" });
      return;
    }
    setSubmitting(true);
    try {
      Taro.showLoading({ title: "上传处理中..." });
      const isImg = fileType === "image";
      const task = await uploadWatermarkFile(filePath, {
        mask_preset: isImg ? "douyin_both_corners" : "custom",
        repair_mode: repairMode,
        auto_detect: !isImg,
        confirm_authorized: confirmed,
        timed_mask_regions: []
      });
      Taro.hideLoading();
      Taro.showToast({ title: "任务已创建", icon: "success" });
      setTimeout(() => navigate(`/pages/processing/index?taskId=${task.task_id}`), 500);
    } catch (error) {
      Taro.hideLoading();
      Taro.showToast({
        title: error instanceof Error ? error.message : "提交失败",
        icon: "none",
        duration: 2800
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <PageShell active='home'>
      <BrandHeader brand subtitle='全自动智能识别 · 逐帧追踪消除' hero='video' />

      <Card className='watermark-card'>
        <View className='card-head'>
          <View>
            <View className='page-title'>去水印</View>
            <View className='muted'>全自动定位水印与片尾 · 深度平滑消除</View>
          </View>
          <View className='safe-pill'>AI 自动感知</View>
        </View>

        {filePath ? (
          <View className='watermark-video-wrapper'>
            {fileType === "image" ? (
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
            <View className='watermark-track-tag'>已选素材：{fileLabel}</View>
          </View>
        ) : (
          <View className='upload-zone' onClick={chooseFile} style={{ margin: "16px 0", cursor: "pointer" }}>
            <IconBadge icon='☁' tone='green' />
            <View>
              <View className='upload-title'>点击上传待去水印视频或图片</View>
              <View className='muted'>支持 MP4 / MOV / AVI / JPG / PNG，自动识别消除</View>
            </View>
          </View>
        )}

        {filePath && (
          <View style={{ display: "flex", justifyContent: "flex-end", marginBottom: "12px" }}>
            <Button className='secondary-btn' style={{ fontSize: "12px", height: "30px", lineHeight: "28px" }} onClick={chooseFile}>
              更换素材
            </Button>
          </View>
        )}

        <View className='mode-selector'>
          <View className='mode-title'>选择修复模式</View>
          <View className='mode-options'>
            <View
              className={`mode-option ${repairMode === "ai" ? "active" : ""}`}
              onClick={() => setRepairMode("ai")}
            >
              <View className='mode-icon'>AI</View>
              <View className='mode-name'>AI 智能修复 (推荐)</View>
              <View className='mode-desc'>IOPaint LaMa 深度学习逐帧无损</View>
            </View>
            <View
              className={`mode-option ${repairMode === "opencv" ? "active" : ""}`}
              onClick={() => setRepairMode("opencv")}
            >
              <View className='mode-icon'>↯</View>
              <View className='mode-name'>标准修复</View>
              <View className='mode-desc'>OpenCV 极速修补</View>
            </View>
          </View>
        </View>

        <View className='toggle-row' onClick={() => setConfirmed(!confirmed)} style={{ marginTop: "14px" }}>
          <Text className={`checkbox ${confirmed ? "checked" : ""}`}>{confirmed ? "✓" : ""}</Text>
          <View>
            <View className='feature-title'>确认素材来源合规并授权处理</View>
            <View className='muted'>仅用于自有或已授权视频合法去除水印</View>
          </View>
        </View>

        <View className='button-row' style={{ marginTop: "18px" }}>
          <Button
            className='primary-btn'
            loading={submitting}
            onClick={filePath ? submit : chooseFile}
          >
            {filePath ? "✦ 开始智能去水印" : "☁ 点击选择视频素材"}
          </Button>
        </View>
      </Card>
    </PageShell>
  );
}
