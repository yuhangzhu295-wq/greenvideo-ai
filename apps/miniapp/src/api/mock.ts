import type { HistoryItem, ParseResult, TaskStatus } from "./types";

export const mockParseResult: ParseResult = {
  id: "parse_demo",
  title: "解析完成",
  source: "演示视频",
  thumbnail: "/static/demo/thumb.svg",
  duration: 225,
  published_at: "2024-05-20 14:23",
  formats: [
    { format_id: "1080p", label: "高清视频 1080P", ext: "mp4", size_mb: 52.36 },
    { format_id: "720p", label: "高清视频 720P", ext: "mp4", size_mb: 23.14 },
    { format_id: "mp3", label: "音频文件 MP3", ext: "mp3", size_mb: 4.85 }
  ]
};

export const mockTask: TaskStatus = {
  task_id: "task_demo",
  type: "watermark",
  status: "processing",
  progress: 62,
  stage: "模型识别",
  eta_seconds: 28,
  download_url: null
};

export const mockHistory: HistoryItem[] = [
  {
    id: "hist_aurora",
    title: "极光之旅的治愈瞬间",
    kind: "视频下载",
    thumbnail: "linear-gradient(135deg,#69B7FF,#0F766E)",
    status: "success",
    size_mb: 128.6,
    created_at: "2024-05-15 14:32:18"
  },
  {
    id: "hist_sunset",
    title: "海边日落延时摄影",
    kind: "视频去水印",
    thumbnail: "linear-gradient(135deg,#FFCC8A,#FF7A59)",
    status: "success",
    size_mb: 96.3,
    created_at: "2024-05-15 10:21:07"
  },
  {
    id: "hist_city",
    title: "城市夜景4K素材",
    kind: "视频下载",
    thumbnail: "linear-gradient(135deg,#172B4D,#3388FF)",
    status: "processing",
    size_mb: 256.8,
    created_at: "2024-05-14 21:45:33"
  }
];
