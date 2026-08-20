import React, { useState } from "react";
import { Button, Input, Text, View } from "@tarojs/components";
import Taro from "@tarojs/taro";
import { healthCheck, parseVideo } from "@/api/client";
import { BrandHeader, Card, IconBadge, PageShell, SectionTitle, navigate } from "@/components";

const demoUrl = "https://example.com/demo";

export default function HomePage() {
  const [inputText, setInputText] = useState("");
  const [loading, setLoading] = useState(false);

  async function paste() {
    try {
      const data = await Taro.getClipboardData();
      setInputText(data.data);
      if (!data.data) Taro.showToast({ title: "剪贴板为空", icon: "none" });
    } catch {
      Taro.showToast({ title: "无法读取剪贴板", icon: "none" });
    }
  }

  async function startParse(useDemo = false) {
    const value = useDemo ? demoUrl : inputText.trim();
    if (!value) {
      Taro.showToast({ title: "请粘贴视频分享链接", icon: "none" });
      return;
    }
    setLoading(true);
    try {
      await healthCheck();
      const result = await parseVideo(value);
      Taro.setStorageSync("parse_result", result);
      Taro.showToast({ title: "解析成功", icon: "success" });
      Taro.navigateTo({ url: "/pages/result/index" });
    } catch (error) {
      const message = error instanceof Error ? error.message : "解析失败";
      if (message.includes("平台限制") || message.includes("登录") || message.includes("DRM")) {
        const modal = await Taro.showModal({
          title: "解析受限",
          content: `${message}\n可上传本地授权素材继续处理，或使用演示链接体验流程。`,
          cancelText: "本地上传",
          confirmText: "演示链接"
        });
        if (modal.confirm) startParse(true);
        if (modal.cancel) navigate("/pages/watermark/index");
      } else {
        if (message.includes("后端服务未连接")) {
          Taro.showModal({
            title: "后端未连接",
            content:
              "请在项目根目录运行：pnpm start:local\n\n确认 FastAPI 健康检查 http://127.0.0.1:8000/api/health 可访问后，再回到微信开发者工具重新编译。",
            showCancel: false,
            confirmText: "知道了"
          });
        } else {
          Taro.showToast({
            title: message || "解析失败",
            icon: "none",
            duration: 2800
          });
        }
      }
    } finally {
      setLoading(false);
    }
  }

  const tools = [
    ["☁", "视频下载", "无水印高清保存", "parse", "green"],
    ["◇", "视频去水印", "智能去除水印", "/pages/watermark/index", "blue"],
    ["▣", "使用教程", "新手快速上手", "/pages/tutorial/index", "purple"],
    ["?", "常见问题", "解答你的疑问", "/pages/faq/index", "orange"]
  ];

  return (
    <PageShell active='home'>
      <BrandHeader brand subtitle='智能视频提取与处理' hero='video' />

      <Card className='input-card'>
        <View className='card-head'>
          <SectionTitle title='提取视频链接' />
          <View className='safe-pill'>安全解析 · 隐私保护</View>
        </View>
        <View className='link-input'>
          <Input
            value={inputText}
            placeholder='粘贴视频分享链接或完整分享文案'
            onInput={(event) => setInputText(String(event.detail.value))}
          />
          <Text className='link-mark'>↗</Text>
        </View>
        <View className='button-row'>
          <Button className='secondary-btn' onClick={paste}>
            ▣ 一键粘贴
          </Button>
          <Button className='primary-btn' loading={loading} onClick={() => startParse()}>
            ✦ 开始解析
          </Button>
        </View>
      </Card>

      <Card className='feature-strip'>
        {[
          ["◎", "支持多平台", "1000+ 平台"],
          ["↯", "高速解析", "秒级响应"],
          ["✓", "安全处理", "隐私有保障"],
          ["◷", "历史记录", "随时可查"]
        ].map((item) => (
          <View className='feature-item' key={item[1]}>
            <IconBadge icon={item[0]} small />
            <View>
              <View className='feature-title'>{item[1]}</View>
              <View className='muted'>{item[2]}</View>
            </View>
          </View>
        ))}
      </Card>

      <View className='tool-grid'>
        {tools.map((item) => (
          <Card key={item[1]} className='tool-entry'>
            <View
              onClick={() => {
                if (item[3] === "parse") startParse(inputText.trim().length === 0);
                else navigate(item[3]);
              }}
            >
              <IconBadge icon={item[0]} tone={item[4]} />
              <View className='tool-title'>{item[1]}</View>
              <View className='muted'>{item[2]}</View>
              <View className='tiny-arrow'>›</View>
            </View>
          </Card>
        ))}
      </View>

      <View className='steps-title'>
        <Text className='big-num'>3</Text>
        <Text>步轻松获取视频</Text>
      </View>
      <View className='step-grid'>
        {[
          ["1", "▣", "复制分享链接", "从平台复制视频分享链接"],
          ["2", "▤", "粘贴并开始解析", "支持完整分享文案自动提取 URL"],
          ["3", "☁", "选择清晰度并下载", "视频保存到相册或继续授权修复"]
        ].map((step) => (
          <Card className='step-card' key={step[0]}>
            <View className='step-index'>{step[0]}</View>
            <IconBadge icon={step[1]} />
            <View className='step-name'>{step[2]}</View>
            <View className='muted'>{step[3]}</View>
          </Card>
        ))}
      </View>

      <View className='compliance-note'>
        仅处理用户自有、已授权或公开视频；不支持登录、付费、私密、验证码、DRM 或平台限制绕过。
      </View>
    </PageShell>
  );
}
