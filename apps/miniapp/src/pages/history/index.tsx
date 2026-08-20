import React, { useEffect, useMemo, useState } from "react";
import { Text, View } from "@tarojs/components";
import Taro from "@tarojs/taro";
import { deleteHistoryItem, getHistory } from "@/api/client";
import type { HistoryItem } from "@/api/types";
import { BrandHeader, Card, IconBadge, PageShell, TaskCard, navigate } from "@/components";

const filters = [
  { label: "全部", type: "all" },
  { label: "下载", type: "download" },
  { label: "去水印", type: "watermark" },
  { label: "成功", type: "success" },
  { label: "处理中", type: "processing" },
  { label: "失败", type: "failed" }
];

export default function HistoryPage() {
  const routeType = String(Taro.getCurrentInstance().router?.params?.type || "all");
  const [activeType, setActiveType] = useState(routeType);
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(false);

  async function load(type = activeType) {
    setLoading(true);
    try {
      const data = await getHistory({ type });
      setItems(data);
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : "历史加载失败", icon: "none" });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(routeType);
  }, []);

  const summary = useMemo(() => {
    const success = items.filter((item) => item.status === "success").length;
    const processing = items.filter((item) => item.status === "processing").length;
    const totalMb = items.reduce((sum, item) => sum + Number(item.size_mb || 0), 0);
    return { total: items.length, success, processing, totalMb: totalMb.toFixed(1) };
  }, [items]);

  async function changeFilter(type: string) {
    setActiveType(type);
    await load(type);
  }

  async function remove(id: string) {
    try {
      await deleteHistoryItem(id);
      setItems((prev) => prev.filter((item) => item.id !== id));
      Taro.showToast({ title: "已删除", icon: "success" });
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : "删除失败", icon: "none" });
    }
  }

  return (
    <PageShell active='profile'>
      <BrandHeader title='历史记录' subtitle='查看和管理您的视频处理历史' hero='profile' />

      <Card className='history-summary'>
        <View className='card-head'>
          <View>
            <View className='page-title'>历史记录</View>
            <View className='muted'>{loading ? "正在同步任务..." : "所有记录均来自后端 API"}</View>
          </View>
          <View className='outline-pill'>全部时间⌄</View>
        </View>
        <View className='stats-grid'>
          {[
            ["▤", String(summary.total), "本页任务", "个任务", "green"],
            ["✓", String(summary.success), "成功完成", "个任务", "blue"],
            ["◷", String(summary.processing), "处理中", "个任务", "purple"],
            ["▥", `${summary.totalMb} MB`, "已处理大小", "本页累计", "orange"]
          ].map((stat) => (
            <View className='stat-item' key={stat[2]}>
              <IconBadge icon={stat[0]} tone={stat[4]} small />
              <View className='stat-number'>{stat[1]}</View>
              <View>{stat[2]}</View>
              <View className='muted'>{stat[3]}</View>
            </View>
          ))}
        </View>
      </Card>

      <View className='filter-row'>
        {filters.map((chip) => (
          <Text
            className={`filter-chip ${activeType === chip.type ? "active" : ""}`}
            key={chip.type}
            onClick={() => changeFilter(chip.type)}
          >
            {chip.label}
          </Text>
        ))}
        <Text className='filter-link' onClick={() => load()}>
          刷新
        </Text>
      </View>

      <View className='history-list'>
        {items.map((item) => (
          <TaskCard
            key={item.id}
            id={item.id}
            taskId={item.task_id}
            title={item.title}
            kind={item.kind}
            thumbnail={item.thumbnail}
            status={item.status}
            createdAt={item.created_at}
            sizeMb={item.size_mb}
            onDelete={remove}
            onAction={(taskId, action) => {
              if (action === "retry") {
                if (item.kind.includes("去水印")) navigate("/pages/watermark/index");
                else navigate("/pages/home/index");
                return;
              }
              navigate(`/pages/processing/index?taskId=${taskId}`);
            }}
          />
        ))}
      </View>
    </PageShell>
  );
}
