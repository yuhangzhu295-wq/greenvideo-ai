import React from "react";
import { Button, Input, Picker, Text, Textarea, View } from "@tarojs/components";
import Taro from "@tarojs/taro";

export type NavKey = "home" | "tools" | "profile";

const navItems: Array<{ key: NavKey; label: string; icon: string; url: string }> = [
  { key: "home", label: "视频去水印", icon: "▦", url: "/pages/home/index" }
];

export function navigate(url: string) {
  Taro.navigateTo({ url });
}

export function PageShell(props: { active: NavKey; children: React.ReactNode }) {
  return (
    <View className='page-shell'>
      <View className='page-inner'>{props.children}</View>
      <BottomNav active={props.active} />
    </View>
  );
}

export function BrandHeader(props: {
  title?: string;
  subtitle?: string;
  hero?: "video" | "book" | "users" | "profile" | "support";
  brand?: boolean;
}) {
  const hero = props.hero || "video";
  const symbolMap = {
    video: "▶",
    book: "▣",
    users: "◎",
    profile: "◯",
    support: "•••"
  };
  return (
    <View className='brand-header'>
      <View className='circuit circuit-left' />
      <View className='brand-copy'>
        {props.brand ? (
          <View className='brand-title'>
            GreenVideo <Text>AI</Text>
          </View>
        ) : (
          <View className='plain-title'>{props.title}</View>
        )}
        <View className='brand-subtitle'>{props.subtitle}</View>
      </View>
      <View className={`hero-device hero-${hero}`}>
        <View className='hero-symbol'>{symbolMap[hero]}</View>
      </View>
    </View>
  );
}

export function Card(props: { children: React.ReactNode; className?: string; onClick?: () => void }) {
  return (
    <View className={`card ${props.className || ""}`} onClick={props.onClick}>
      {props.children}
    </View>
  );
}

export function SectionTitle(props: { title: string; aside?: React.ReactNode }) {
  return (
    <View className='section-title-row'>
      <View className='section-title'>
        <View className='section-accent' />
        <Text>{props.title}</Text>
      </View>
      {props.aside}
    </View>
  );
}

export function IconBadge(props: { icon: string; tone?: string; small?: boolean }) {
  return (
    <View className={`icon-badge ${props.tone || "green"} ${props.small ? "small" : ""}`}>
      {props.icon}
    </View>
  );
}

export function BottomNav(props: { active: NavKey }) {
  return (
    <View className='bottom-nav'>
      {navItems.map((item) => (
        <View
          key={item.key}
          className={`bottom-nav-item ${props.active === item.key ? "active" : ""}`}
          onClick={() => navigate(item.url)}
        >
          <Text className='bottom-icon'>{item.icon}</Text>
          <Text>{item.label}</Text>
          <View className='nav-underline' />
        </View>
      ))}
    </View>
  );
}

export function StatusBadge(props: { status: string }) {
  const map: Record<string, string> = {
    queued: "排队中",
    processing: "处理中",
    completed: "成功",
    success: "成功",
    failed: "失败",
    canceled: "已取消"
  };
  const css = props.status === "completed" ? "success" : props.status;
  return <View className={`status-badge ${css}`}>{map[props.status] || props.status}</View>;
}

export function ProgressRing(props: { progress: number; label?: string }) {
  const safeProgress = Math.min(100, Math.max(0, props.progress));
  const degree = safeProgress * 3.6;
  return (
    <View
      className='progress-ring'
      style={{ background: `conic-gradient(#10C878 ${degree}deg, rgba(255,255,255,.68) 0deg)` }}
    >
      <View className='progress-core'>
        <Text className='progress-number'>{safeProgress}%</Text>
        <Text>{props.label || "处理中..."}</Text>
      </View>
    </View>
  );
}

export function TaskCard(props: {
  id: string;
  taskId?: string | null;
  title: string;
  kind: string;
  thumbnail: string;
  status: string;
  createdAt: string;
  sizeMb: number;
  onDelete?: (id: string) => void;
  onAction?: (taskId: string, action: string) => void;
}) {
  const action = props.status === "failed" ? "retry" : props.status === "processing" ? "cancel" : "view";
  const actionText = props.status === "failed" ? "重新处理" : props.status === "processing" ? "查看进度" : "查看结果";
  const targetId = props.taskId || props.id;
  return (
    <Card className='task-card'>
      <View className='thumb' style={{ background: props.thumbnail }}>
        <Text>▶</Text>
      </View>
      <View className='task-main'>
        <View className='task-title'>{props.title}</View>
        <View className='task-kind'>{props.kind}</View>
        <View className='task-meta'>
          {props.createdAt} · {props.sizeMb} MB
        </View>
        {props.status === "processing" && (
          <View className='inline-progress'>
            <View style={{ width: "65%" }} />
          </View>
        )}
        <View className='task-actions'>
          <Button size='mini' className='ghost-btn' onClick={() => props.onAction?.(targetId, action)}>
            {actionText}
          </Button>
          {props.status !== "processing" && (
            <Button size='mini' className='danger-btn' onClick={() => props.onDelete?.(props.id)}>
              删除
            </Button>
          )}
        </View>
      </View>
      <StatusBadge status={props.status} />
    </Card>
  );
}

export function FormField(props: {
  label: string;
  placeholder: string;
  value: string;
  type?: "input" | "textarea" | "picker";
  options?: string[];
  onChange: (value: string) => void;
}) {
  if (props.type === "textarea") {
    return (
      <View className='form-field large'>
        <View className='form-label'>{props.label}</View>
        <Textarea
          value={props.value}
          maxlength={500}
          placeholder={props.placeholder}
          onInput={(event) => props.onChange(String(event.detail.value))}
        />
        <Text className='counter'>{props.value.length}/500</Text>
      </View>
    );
  }
  if (props.type === "picker") {
    const options = props.options || [];
    const currentIndex = Math.max(0, options.indexOf(props.value));
    return (
      <Picker
        mode='selector'
        range={options}
        value={currentIndex}
        onChange={(event) => props.onChange(options[Number(event.detail.value)] || options[0])}
      >
        <View className='form-field'>
          <View className='form-label'>{props.label}</View>
          <View className={`picker-value ${props.value ? "" : "muted"}`}>
            {props.value || props.placeholder}
          </View>
          <Text className='chevron'>›</Text>
        </View>
      </Picker>
    );
  }
  return (
    <View className='form-field'>
      <View className='form-label'>{props.label}</View>
      <Input
        value={props.value}
        placeholder={props.placeholder}
        onInput={(event) => props.onChange(String(event.detail.value))}
      />
    </View>
  );
}
