import React, { useMemo, useState } from "react";
import { Button, Input, Text, View } from "@tarojs/components";
import { BrandHeader, Card, IconBadge, PageShell, SectionTitle, navigate } from "@/components";

const faqs = [
  ["支持哪些平台？", "支持抖音、快手、小红书、视频号、B站等公开视频平台。真实平台若触发登录、反爬或地区限制，会返回合规错误提示。"],
  ["为什么解析失败？", "请检查链接是否正确，或该视频是否已删除、私密、需要登录、需要验证码、受 DRM 保护或受到平台限制。"],
  ["去水印需要多久？", "图片通常很快完成，高清视频或较长视频需要更长时间；处理进度可在任务页和历史页查看。"],
  ["为什么先生成临时文件？", "微信小程序下载文件必须先生成临时路径；视频和图片会继续写入系统相册，音频等其他文件会保存到小程序本地文件，或可复制下载链接到浏览器保存。"],
  ["会默认删除字幕吗？", "不会。字幕区域只有在你主动打开“同时清理字幕区域”时才会进入修复 mask。"]
];

export default function FaqPage() {
  const [open, setOpen] = useState(0);
  const [keyword, setKeyword] = useState("");

  const visibleFaqs = useMemo(
    () => faqs.filter((faq) => `${faq[0]}${faq[1]}`.includes(keyword.trim())),
    [keyword]
  );

  return (
    <PageShell active='tools'>
      <BrandHeader brand subtitle='智能视频提取与处理' hero='video' />

      <Card className='faq-card'>
        <SectionTitle title='常见问题' />
        <View className='search-box'>
          <Text>⌕</Text>
          <Input placeholder='搜索问题' value={keyword} onInput={(event) => setKeyword(String(event.detail.value))} />
        </View>
        <View className='faq-list'>
          {visibleFaqs.map((faq, index) => (
            <View className='faq-item' key={faq[0]} onClick={() => setOpen(open === index ? -1 : index)}>
              <IconBadge icon={index === 0 ? "▦" : index === 1 ? "?" : index === 2 ? "◷" : "↓"} small />
              <View className='faq-text'>
                <View className='faq-question'>{faq[0]}</View>
                {open === index && <View className='muted answer'>{faq[1]}</View>}
              </View>
              <Text className='green-check'>{open === index ? "⌃" : "⌄"}</Text>
            </View>
          ))}
        </View>
      </Card>

      <Card className='support-card'>
        <IconBadge icon='•••' />
        <View>
          <View className='smart-title'>没有找到答案？</View>
          <View className='muted'>联系我们的客服团队为您解答</View>
        </View>
        <Button className='small-primary' onClick={() => navigate("/pages/feedback/index")}>
          在线咨询
        </Button>
      </Card>
    </PageShell>
  );
}
