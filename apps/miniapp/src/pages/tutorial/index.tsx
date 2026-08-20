import React from "react";
import { Text, View } from "@tarojs/components";
import { BrandHeader, Card, IconBadge, PageShell, SectionTitle, navigate } from "@/components";

export default function TutorialPage() {
  return (
    <PageShell active='tools'>
      <BrandHeader title='使用教程' subtitle='3 分钟快速上手' hero='book' />

      <View className='tutorial-flow'>
        {[
          ["1", "↗", "复制链接", "打开视频平台，点击分享按钮，复制视频链接或完整分享文案"],
          ["2", "▤", "粘贴解析", "回到 GreenVideo AI，粘贴链接并点击“开始解析”"],
          ["3", "☁", "选择下载", "解析完成后，选择清晰度，点击下载按钮创建任务"],
          ["4", "◇", "去水印处理", "上传自有或已授权素材，选择水印位置后开始局部修复"]
        ].map((step) => (
          <Card className='tutorial-step' key={step[0]}>
            <View className='step-index corner'>{step[0]}</View>
            <IconBadge icon={step[1]} />
            <View className='tutorial-text'>
              <View className='page-title small-title'>{step[2]}</View>
              <View className='muted'>{step[3]}</View>
            </View>
            <View className='mini-illustration'>{step[1]}</View>
          </Card>
        ))}
      </View>

      <Card>
        <SectionTitle title='常见操作' aside={<Text className='muted' onClick={() => navigate("/pages/faq/index")}>更多问题 ›</Text>} />
        <View className='operation-row'>
          {[
            ["↗", "如何复制链接", "从分享菜单复制"],
            ["▣", "如何保存到相册", "视频/图片写入相册"],
            ["!", "如何处理失败", "查看错误或提交反馈"]
          ].map((item) => (
            <View className='operation-card' key={item[1]}>
              <IconBadge icon={item[0]} tone={item[0] === "!" ? "orange" : "green"} small />
              <View>
                <View>{item[1]}</View>
                <View className='muted'>{item[2]}</View>
              </View>
            </View>
          ))}
        </View>
      </Card>
    </PageShell>
  );
}
