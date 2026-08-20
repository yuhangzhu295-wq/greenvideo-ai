import React, { useEffect, useState } from "react";
import { Button, Text, View } from "@tarojs/components";
import Taro from "@tarojs/taro";
import { createDownloadTask, mockParseResult } from "@/api/client";
import type { ParseResult } from "@/api/types";
import { BrandHeader, Card, IconBadge, PageShell, SectionTitle, navigate } from "@/components";

function durationText(seconds: number) {
  const min = String(Math.floor(seconds / 60)).padStart(2, "0");
  const sec = String(seconds % 60).padStart(2, "0");
  return `${min}:${sec}`;
}

export default function ResultPage() {
  const [result, setResult] = useState<ParseResult>(mockParseResult);
  const [loadingFormat, setLoadingFormat] = useState("");
  const [lastTaskId, setLastTaskId] = useState("");

  useEffect(() => {
    const stored = Taro.getStorageSync("parse_result") as ParseResult | "";
    if (stored) setResult(stored);
  }, []);

  async function download(formatId: string) {
    setLoadingFormat(formatId);
    try {
      const task = await createDownloadTask(result.id, formatId);
      setLastTaskId(task.task_id);
      Taro.showToast({ title: "任务已创建", icon: "success" });
      setTimeout(() => navigate(`/pages/processing/index?taskId=${task.task_id}`), 600);
    } catch (err) {
      Taro.showToast({ title: err instanceof Error ? err.message : "创建任务失败", icon: "none", duration: 2600 });
    } finally {
      setLoadingFormat("");
    }
  }

  async function copyDirectLink() {
    if (!lastTaskId) {
      Taro.showToast({ title: "请先创建下载任务并等待生成文件", icon: "none" });
      return;
    }
    await Taro.setClipboardData({ data: `/api/files/${lastTaskId}` });
  }

  return (
    <PageShell active='home'>
      <BrandHeader brand subtitle='智能视频提取与处理' hero='video' />

      <Card className='success-banner'>
        <IconBadge icon='✓' />
        <View>
          <View className='banner-title'>链接解析成功</View>
          <View className='muted'>视频内容已解析完成，可选择下载或进一步处理</View>
        </View>
        <Text className='close-x'>×</Text>
      </Card>

      <Card className='result-card'>
        <View className='result-top'>
          <View className='video-preview soft'>
            <Text className='play-icon'>▶</Text>
            <View className='duration'>{durationText(result.duration)}</View>
          </View>
          <View className='result-info'>
            <View className='page-title'>{result.title || "解析完成"}</View>
            <View className='muted'>来源：{result.source}</View>
            <View className='muted'>发布时间：{result.published_at}</View>
            <View className='tag-row'>
              {result.formats.map((format) => (
                <Text className='format-tag' key={format.format_id}>
                  {format.ext.toUpperCase()}
                </Text>
              ))}
            </View>
          </View>
        </View>

        <SectionTitle title='下载选项' />
        <View className='download-list'>
          {result.formats.map((format) => (
            <View className='download-row' key={format.format_id}>
              <IconBadge icon={format.ext === "mp3" ? "♪" : "▶"} tone={format.ext === "mp3" ? "purple" : "green"} small />
              <View className='download-text'>
                <View>{format.label}</View>
                <View className='muted'>
                  {format.ext.toUpperCase()} 格式 · {format.size_mb} MB
                </View>
              </View>
              <Button
                className='small-primary'
                loading={loadingFormat === format.format_id}
                onClick={() => download(format.format_id)}
              >
                {format.ext === "mp3" ? "下载音频" : "下载视频"}
              </Button>
            </View>
          ))}
          <View className='download-row'>
            <IconBadge icon='↗' tone='green' small />
            <View className='download-text'>
              <View>视频直链（任务生成后可用）</View>
              <View className='muted'>先创建下载任务，完成后可复制本地接口链接</View>
            </View>
            <Button className='outline-btn' onClick={copyDirectLink}>
              复制直链
            </Button>
          </View>
        </View>
      </Card>

      <Card>
        <SectionTitle title='智能处理（推荐）' />
        <View className='smart-actions'>
          <View className='smart-card green-tint' onClick={() => navigate(`/pages/watermark/index?parseId=${result.id}`)}>
            <IconBadge icon='◇' />
            <View>
              <View className='smart-title'>去水印</View>
              <View className='muted'>上传本地授权素材后进行局部修复</View>
            </View>
            <Text>›</Text>
          </View>
          <View className='smart-card blue-tint' onClick={() => download("mp3")}>
            <IconBadge icon='≋' tone='blue' />
            <View>
              <View className='smart-title'>提取音频</View>
              <View className='muted'>创建 MP3 下载任务</View>
            </View>
            <Text>›</Text>
          </View>
        </View>
      </Card>
    </PageShell>
  );
}
