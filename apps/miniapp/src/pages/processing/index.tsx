import React, { useEffect, useState } from "react";
import { Button, Text, View } from "@tarojs/components";
import Taro from "@tarojs/taro";
import { cancelTask, getTask, toAbsoluteUrl } from "@/api/client";
import type { TaskStatus } from "@/api/types";
import { BrandHeader, Card, IconBadge, PageShell, ProgressRing, SectionTitle, navigate } from "@/components";

const initialTask: TaskStatus = {
  task_id: "",
  type: "watermark",
  status: "queued",
  progress: 0,
  stage: "等待处理",
  eta_seconds: 0,
  download_url: null
};

function isDone(task: TaskStatus) {
  return task.status === "completed" || task.status === "success" || task.progress >= 100;
}

async function ensureAlbumPermission(): Promise<boolean> {
  try {
    const setting = await Taro.getSetting();
    const authorized = setting.authSetting?.["scope.writePhotosAlbum"];
    if (authorized === true) return true;

    if (authorized === false) {
      const modal = await Taro.showModal({
        title: "需要相册权限",
        content: "保存视频或图片到系统相册需要授权写入相册。请在设置中开启相册权限后重试。",
        cancelText: "暂不",
        confirmText: "去设置"
      });
      if (!modal.confirm) return false;
      const opened = await Taro.openSetting();
      return opened.authSetting?.["scope.writePhotosAlbum"] === true;
    }

    await Taro.authorize({ scope: "scope.writePhotosAlbum" });
    return true;
  } catch {
    return false;
  }
}

async function saveToAlbumOrLocal(tempFilePath: string, url: string) {
  const canWriteAlbum = await ensureAlbumPermission();

  if (canWriteAlbum) {
    try {
      await Taro.saveVideoToPhotosAlbum({ filePath: tempFilePath });
      Taro.showToast({ title: "已保存到系统相册", icon: "success" });
      return;
    } catch {
      // Not every generated file is a video. Fall through to image/local save.
    }

    try {
      await Taro.saveImageToPhotosAlbum({ filePath: tempFilePath });
      Taro.showToast({ title: "已保存到系统相册", icon: "success" });
      return;
    } catch {
      // Non-media files such as MP3 can only be kept in the miniapp sandbox.
    }
  }

  try {
    const saved = await Taro.saveFile({ tempFilePath });
    const savedFilePath = "savedFilePath" in saved ? saved.savedFilePath : "";
    await Taro.showModal({
      title: "已保存到小程序文件",
      content: `该文件未能写入系统相册，已保存到小程序本地文件。你也可以复制下载链接到浏览器保存。\n\n${savedFilePath}`,
      confirmText: "知道了",
      showCancel: false
    });
  } catch {
    await Taro.setClipboardData({ data: url });
    Taro.showToast({ title: "保存失败，已复制链接", icon: "none" });
  }
}

export default function ProcessingPage() {
  const [task, setTask] = useState<TaskStatus>(initialTask);
  const [error, setError] = useState("");

  useEffect(() => {
    const taskId = String(Taro.getCurrentInstance().router?.params?.taskId || "");
    if (!taskId) {
      setError("缺少任务 ID");
      return undefined;
    }
    let mounted = true;
    let timer: ReturnType<typeof setInterval> | undefined;
    const load = async () => {
      try {
        const data = await getTask(taskId);
        if (!mounted) return;
        setTask(data);
        setError(data.error || "");
        if (isDone(data) || data.status === "failed" || data.status === "canceled") {
          if (timer) clearInterval(timer);
        }
      } catch (err) {
        if (!mounted) return;
        setError(err instanceof Error ? err.message : "获取任务失败");
        if (timer) clearInterval(timer);
      }
    };
    load();
    timer = setInterval(load, 2000);
    return () => {
      mounted = false;
      if (timer) clearInterval(timer);
    };
  }, []);

  async function saveFile() {
    if (!task.download_url) {
      Taro.showToast({ title: "文件尚未生成", icon: "none" });
      return;
    }
    const url = toAbsoluteUrl(task.download_url);
    try {
      const result = await Taro.downloadFile({ url });
      if (result.statusCode && (result.statusCode < 200 || result.statusCode >= 300)) {
        throw new Error(`下载失败：${result.statusCode}`);
      }
      if (!result.tempFilePath) {
        throw new Error("下载失败：未生成临时文件");
      }
      await saveToAlbumOrLocal(result.tempFilePath, url);
    } catch {
      Taro.setClipboardData({ data: url });
      Taro.showToast({ title: "下载失败，已复制链接", icon: "none" });
    }
  }

  async function copyLink() {
    if (!task.download_url) {
      Taro.showToast({ title: "文件尚未生成", icon: "none" });
      return;
    }
    await Taro.setClipboardData({ data: toAbsoluteUrl(task.download_url) });
  }

  async function cancel() {
    if (!task.task_id) return;
    await cancelTask(task.task_id);
    Taro.showToast({ title: "任务已取消", icon: "none" });
    navigate("/pages/history/index?type=processing");
  }

  const progressLabel = task.status === "failed" ? "失败" : isDone(task) ? "已完成" : "处理中...";

  return (
    <PageShell active='tools'>
      <BrandHeader brand subtitle='智能视频提取与处理' hero='video' />

      <Card className='processing-card'>
        <SectionTitle title={task.type === "download" ? "正在生成下载文件" : "正在智能去水印"} />
        <View className='processing-preview'>
          <View className='quality-pill'>{task.type === "download" ? "DL" : "1080P"}</View>
          <ProgressRing progress={task.progress} label={progressLabel} />
          <View className='video-time'>▶ {task.stage}</View>
        </View>

        <View className='process-list'>
          <View className='process-line'>
            <IconBadge icon='✓' small />
            <View>
              <View>任务状态</View>
              <View className='muted'>{task.status}</View>
            </View>
            <Text className='ok-mark'>{isDone(task) ? "完成" : "进行中"}</Text>
          </View>
          <View className='process-line'>
            <IconBadge icon='↯' small />
            <View>
              <View>当前阶段</View>
              <View className='muted'>{task.stage}</View>
            </View>
            <Text className='running-mark'>{task.progress}%</Text>
          </View>
          <View className='process-line'>
            <IconBadge icon='◷' small tone='gray' />
            <View>预计剩余</View>
            <Text className='eta'>00:{String(task.eta_seconds).padStart(2, "0")}</Text>
          </View>
        </View>

        {error && (
          <View className='error-box'>
            <Text>{error}</Text>
          </View>
        )}

        <View className='status-steps'>
          {["上传/创建", "模型识别", "生成结果"].map((step, index) => (
            <View className={`status-step ${task.progress >= index * 45 ? "active" : ""}`} key={step}>
              <Text>{task.progress >= 100 && index === 2 ? "✓" : index + 1}</Text>
              <View>{step}</View>
            </View>
          ))}
        </View>
      </Card>

      {isDone(task) ? (
        <View className='button-row'>
          <Button className='primary-btn' onClick={saveFile}>
            保存到相册
          </Button>
          <Button className='secondary-btn' onClick={copyLink}>
            复制下载链接
          </Button>
          <Button className='secondary-btn' onClick={() => navigate("/pages/history/index")}>
            查看历史
          </Button>
        </View>
      ) : task.status === "failed" ? (
        <View className='button-row'>
          <Button className='secondary-btn' onClick={() => navigate("/pages/watermark/index")}>
            重新处理
          </Button>
          <Button className='secondary-btn' onClick={() => navigate("/pages/feedback/index")}>
            联系客服
          </Button>
        </View>
      ) : (
        <View className='button-row'>
          <Button className='secondary-btn' onClick={() => navigate("/pages/history/index?type=processing")}>
            后台处理
          </Button>
          <Button className='danger-soft' onClick={cancel}>
            取消任务
          </Button>
        </View>
      )}

      <Card className='info-strip'>
        <IconBadge icon='A' />
        <View>
          <View className='smart-title'>轻量化 AI 模型 + FFmpeg 管线处理中</View>
          <View className='muted'>系统会保留任务进度并写入历史记录；完成后可从历史页继续下载。</View>
        </View>
      </Card>
    </PageShell>
  );
}
