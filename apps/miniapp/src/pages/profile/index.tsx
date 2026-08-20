import React from "react";
import { Text, View } from "@tarojs/components";
import Taro from "@tarojs/taro";
import { BrandHeader, Card, IconBadge, PageShell, navigate } from "@/components";

export default function ProfilePage() {
  const menu = [
    ["↓", "我的下载", "", () => navigate("/pages/history/index?type=download")],
    ["▤", "我的任务", "3 个任务进行中", () => navigate("/pages/history/index?type=processing")],
    ["◎", "邀请好友", "得会员 · 享特权", () => navigate("/pages/invite/index")],
    ["▣", "使用教程", "", () => navigate("/pages/tutorial/index")],
    ["⚙", "设置", "", () => Taro.showToast({ title: "请在开发环境配置 API_BASE_URL", icon: "none" })],
    ["☎", "联系客服", "", () => navigate("/pages/feedback/index")]
  ] as const;

  return (
    <PageShell active='profile'>
      <BrandHeader title='我的' subtitle='管理账号与个人信息' hero='profile' />

      <Card className='profile-card'>
        <View className='avatar'>人</View>
        <View className='profile-info'>
          <View className='page-title'>
            视频达人 <Text className='edit'>✎</Text>
          </View>
          <View className='muted'>ID：88888888 ⧉</View>
          <View className='member-pill'>✓ 普通会员</View>
        </View>
        <View className='quota-card'>
          <View className='muted'>今日剩余高速解析</View>
          <View>
            <Text className='quota-num'>5</Text> 次
          </View>
        </View>
      </Card>

      <Card className='profile-stats'>
        {[
          ["↓", "128", "下载", "green"],
          ["◇", "36", "去水印", "blue"],
          ["★", "8", "收藏", "purple"]
        ].map((item) => (
          <View className='profile-stat' key={item[2]}>
            <IconBadge icon={item[0]} tone={item[3]} small />
            <View className='stat-number'>{item[1]}</View>
            <View>{item[2]}</View>
          </View>
        ))}
      </Card>

      <Card className='menu-card'>
        {menu.map((item) => (
          <View className='menu-row' key={item[1]} onClick={item[3]}>
            <IconBadge icon={item[0]} small tone={item[1] === "邀请好友" ? "green" : "blue"} />
            <Text>{item[1]}</Text>
            {item[2] && <Text className='menu-hint'>{item[2]}</Text>}
            <Text className='chevron'>›</Text>
          </View>
        ))}
      </Card>

      <Card className='vip-banner' onClick={() => navigate("/pages/invite/index")}>
        <View>
          <View className='smart-title'>开通会员 · 享更多特权</View>
          <View className='muted'>无限高速解析 · 去水印无限制 · 专属客服</View>
        </View>
        <View className='vip-crown'>VIP</View>
        <Text>›</Text>
      </Card>
    </PageShell>
  );
}
