export interface HealthStatus {
  ok: boolean;
  service: string;
  demo_mode: boolean;
  yt_dlp_available: boolean;
  ffmpeg_available: boolean;
  opencv_available: boolean;
  storage_ok: boolean;
}

export interface FormatOption {
  format_id: string;
  label: string;
  ext: string;
  size_mb: number;
}

export interface ParseResult {
  id: string;
  title: string;
  source: string;
  thumbnail: string;
  duration: number;
  published_at: string;
  formats: FormatOption[];
}

export interface WatermarkPayload {
  source_task_id?: string;
  parse_id?: string;
  video_url?: string;
  mask_regions?: Array<{ x: number; y: number; w: number; h: number }>;
  /** Time-scoped regions for clips whose watermark changes layout mid-video. */
  timed_mask_regions?: Array<{
    x: number; y: number; w: number; h: number;
    start_s: number; end_s?: number;
  }>;
  mask_preset?: "douyin_top_left" | "douyin_bottom_right" | "douyin_both_corners" | "custom";
  repair_mode?: "opencv" | "ai";
  remove_subtitle?: boolean;
  confirm_authorized?: boolean;
  auto_detect?: boolean;
}

export interface TaskStatus {
  task_id: string;
  type: string;
  status: "queued" | "processing" | "completed" | "success" | "failed" | "canceled" | string;
  progress: number;
  stage: string;
  eta_seconds: number;
  download_url: string | null;
  error?: string | null;
}

export interface HistoryItem {
  id: string;
  task_id?: string | null;
  title: string;
  kind: string;
  thumbnail: string;
  status: "success" | "processing" | "failed" | string;
  size_mb: number;
  created_at: string;
}

export interface FeedbackPayload {
  category: string;
  description: string;
  contact: string;
}
