import React, { useState } from "react";
import { Button, Text, View } from "@tarojs/components";
import Taro from "@tarojs/taro";
import { submitFeedback } from "@/api/client";
import { BrandHeader, Card, FormField, IconBadge, PageShell, SectionTitle } from "@/components";

export default function FeedbackPage() {
  const [category, setCategory] = useState("功能异常");
  const [description, setDescription] = useState("");
  const [contact, setContact] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    if (description.trim().length < 10) {
      Taro.showToast({ title: "请至少填写 10 个字", icon: "none" });
      return;
    }
    setSubmitting(true);
    try {
      await submitFeedback({ category, description: description.trim(), contact });
      Taro.showToast({ title: "反馈已提交", icon: "success" });
      setDescription("");
      setContact("");
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : "提交失败", icon: "none", duration: 2600 });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <PageShell active='profile'>
      <BrandHeader title='客服与反馈' subtitle='我们随时为您提供帮助' hero='support' />

      <Card className='greeting-card'>
        <View>
          <View className='page-title'>您好！</View>
          <View className='muted'>有任何问题或建议，我们都乐意为您服务</View>
          <View className='pill-row'>
            <Text>✓ 专业团队</Text>
            <Text>✓ 快速响应</Text>
            <Text>✓ 用心服务</Text>
          </View>
        </View>
        <View className='message-art'>•••</View>
      </Card>

      <View className='quick-grid'>
        {[
          ["☎", "在线客服", "实时咨询解答", "green"],
          ["✎", "提交反馈", "反馈问题与建议", "blue"],
          ["▤", "问题工单", "查看处理进度", "purple"],
          ["!", "使用建议", "分享您的想法", "orange"]
        ].map((item) => (
          <Card className='quick-card' key={item[1]}>
            <IconBadge icon={item[0]} tone={item[3]} />
            <View className='tool-title'>{item[1]}</View>
            <View className='muted'>{item[2]}</View>
          </Card>
        ))}
      </View>

      <Card className='feedback-form'>
        <View className='card-head'>
          <SectionTitle title='提交反馈' />
          <View className='safe-pill'>信息安全保障</View>
        </View>
        <FormField
          type='picker'
          label='问题类型'
          placeholder='请选择问题类型'
          value={category}
          options={["功能异常", "解析失败", "下载问题", "会员咨询", "使用建议"]}
          onChange={setCategory}
        />
        <FormField
          type='textarea'
          label='问题描述'
          placeholder='请详细描述您遇到的问题或建议（不少于10字）'
          value={description}
          onChange={setDescription}
        />
        <FormField
          label='联系方式'
          placeholder='请输入手机号或邮箱（选填）'
          value={contact}
          onChange={setContact}
        />
        <Button className='primary-btn full' loading={submitting} onClick={submit}>
          提交反馈
        </Button>
      </Card>

      <Card className='response-card'>
        <IconBadge icon='✓' />
        <View>
          <View className='smart-title'>
            工作日 <Text className='big-num inline'>10</Text> 分钟内响应
          </View>
          <View className='muted'>我们承诺在工作日 10 分钟内响应您的咨询，尽快为您解决问题。</View>
        </View>
        <View className='timer-art'>10 min</View>
      </Card>
    </PageShell>
  );
}
