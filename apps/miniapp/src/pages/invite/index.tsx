import React from "react";
import { Button, Text, View } from "@tarojs/components";
import Taro from "@tarojs/taro";
import { BrandHeader, Card, IconBadge, PageShell, SectionTitle } from "@/components";

export default function InvitePage() {
  function copyCode() {
    Taro.setClipboardData({ data: "AI2026" });
  }

  return (
    <PageShell active='profile'>
      <BrandHeader title='邀请好友' subtitle='一起体验 GreenVideo AI 的高效与智能' hero='users' />

      <Card className='invite-main'>
        <View className='invite-copy'>
          邀请 <Text>1</Text> 位好友，
          <View>
            获得 <Text>3</Text> 次高速解析
          </View>
        </View>
        <View className='invite-code'>
          <View className='muted'>我的邀请码</View>
          <View className='code-text'>
            AI2026 <Text onClick={copyCode}>⧉</Text>
          </View>
          <View className='muted'>好友注册并使用后，你将自动获得奖励</View>
        </View>
        <View className='button-row'>
          <Button className='primary-btn'>立即邀请</Button>
          <Button className='secondary-btn' onClick={copyCode}>
            复制邀请码
          </Button>
        </View>
      </Card>

      <Card>
        <SectionTitle title='奖励说明' />
        <View className='reward-flow'>
          {[
            ["◎", "成功邀请", "好友完成注册"],
            ["↯", "获得奖励", "3 次高速解析"],
            ["✓", "永久有效", "奖励长期有效"]
          ].map((item) => (
            <View className='reward-item' key={item[1]}>
              <IconBadge icon={item[0]} />
              <View>
                <View>{item[1]}</View>
                <View className='muted'>{item[2]}</View>
              </View>
            </View>
          ))}
        </View>
      </Card>

      <Card>
        <SectionTitle title='邀请进度' aside={<Text className='muted'>查看全部 ›</Text>} />
        <View className='progress-stats'>
          {[
            ["已邀请", "2", "人"],
            ["已获得奖励", "6", "次"],
            ["可用奖励", "6", "次"]
          ].map((item) => (
            <View key={item[0]}>
              <View className='muted'>{item[0]}</View>
              <View>
                <Text>{item[1]}</Text> {item[2]}
              </View>
            </View>
          ))}
        </View>
      </Card>

      <Card>
        <SectionTitle title='最近邀请' />
        {[
          ["科技探索者", "2024-05-20 14:32"],
          ["AI 创作者", "2024-05-18 09:15"]
        ].map((item) => (
          <View className='invite-row' key={item[0]}>
            <View className='avatar small-avatar' />
            <View>
              <View>{item[0]}</View>
              <View className='muted'>注册时间：{item[1]}</View>
            </View>
            <View className='reward-pill'>+3 次解析</View>
          </View>
        ))}
      </Card>
    </PageShell>
  );
}
