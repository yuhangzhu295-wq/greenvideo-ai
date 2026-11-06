import Taro from "@tarojs/taro";
import { mockHistory, mockParseResult, mockTask } from "./mock";
import type {
  FeedbackPayload,
  HealthStatus,
  HistoryItem,
  ParseResult,
  TaskConfirmPayload,
  TaskStatus,
  WatermarkPayload
} from "./types";

// H5 is served by the FastAPI backend itself, so use same-origin relative URLs.
// Mini programs have no origin and need an absolute address.
const ENV_BASE = process.env.TARO_APP_API_BASE_URL || "";
export const API_BASE_URL =
  process.env.TARO_ENV === "h5" ? ENV_BASE : ENV_BASE || "http://127.0.0.1:8000";
const IS_DEMO_MODE = process.env.TARO_APP_DEMO_MODE === "true";

type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  data?: unknown;
  header?: Record<string, string>;
};

type ErrorDetail = string | { code?: string; message?: string } | Array<{ msg?: string }>;

function detailToMessage(detail: ErrorDetail | undefined): string {
  if (!detail) return "请求失败，请稍后重试";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg || "参数错误").join("；");
  return detail.message || detail.code || "请求失败，请稍后重试";
}

function networkMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  const errMsg = (error as { errMsg?: string } | null)?.errMsg;
  const target = API_BASE_URL || "同源地址";
  return `请求失败${errMsg ? `（${errMsg}）` : ""}。请确认后端已启动（scripts/start-local.ps1）。当前地址：${target}`;
}

export function toAbsoluteUrl(path: string | null | undefined): string {
  if (!path) return "";
  if (/^https?:\/\//.test(path)) return path;
  if (!API_BASE_URL) return path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  try {
    const response = await Taro.request({
      url: toAbsoluteUrl(path),
      method: options.method || "GET",
      data: options.data,
      header: {
        "Content-Type": "application/json",
        ...(options.header || {})
      }
    });

    if (response.statusCode < 200 || response.statusCode >= 300) {
      const payload = response.data as { detail?: ErrorDetail };
      throw new Error(detailToMessage(payload?.detail));
    }
    return response.data as T;
  } catch (error) {
    if (IS_DEMO_MODE) throw new Error("DEMO_FALLBACK");
    throw new Error(networkMessage(error));
  }
}

function delay<T>(value: T, ms = 180): Promise<T> {
  return new Promise((resolve) => {
    setTimeout(() => resolve(value), ms);
  });
}

export async function healthCheck(): Promise<HealthStatus> {
  return request<HealthStatus>("/api/health");
}

export async function parseVideo(inputText: string): Promise<ParseResult> {
  try {
    return await request<ParseResult>("/api/parse", {
      method: "POST",
      data: { text: inputText }
    });
  } catch (err) {
    if (IS_DEMO_MODE && err instanceof Error && err.message === "DEMO_FALLBACK") {
      return delay({ ...mockParseResult, id: inputText ? "parse_local_demo" : mockParseResult.id });
    }
    throw err;
  }
}

export async function createDownloadTask(
  parseId: string,
  formatId: string
): Promise<{ task_id: string; status: string }> {
  try {
    return await request<{ task_id: string; status: string }>("/api/tasks/download", {
      method: "POST",
      data: { parse_id: parseId, format_id: formatId }
    });
  } catch (err) {
    if (IS_DEMO_MODE && err instanceof Error && err.message === "DEMO_FALLBACK") {
      return delay({ task_id: "task_download_demo", status: "queued" });
    }
    throw err;
  }
}

export async function uploadWatermarkFile(
  filePath: string,
  options: WatermarkPayload
): Promise<{ task_id: string; status: string }> {
  try {
    const uploadParams: any = {
      url: toAbsoluteUrl("/api/tasks/watermark"),
      filePath,
      name: "file",
      withCredentials: false,
      formData: {
        mask_regions: JSON.stringify(options.mask_regions || []),
        timed_mask_regions: JSON.stringify(options.timed_mask_regions || []),
        mask_preset: options.mask_preset || "douyin_both_corners",
        repair_mode: options.repair_mode || "opencv",
        remove_subtitle: String(Boolean(options.remove_subtitle)),
        confirm_authorized: String(Boolean(options.confirm_authorized)),
        auto_detect: String(Boolean(options.auto_detect)),
        parse_id: options.parse_id || "",
        source_task_id: options.source_task_id || "",
        video_url: options.video_url || ""
      }
    };
    const response = await Taro.uploadFile(uploadParams);
    if (response.statusCode < 200 || response.statusCode >= 300) {
      let message = "上传失败";
      try {
        const parsed = JSON.parse(String(response.data)) as { detail?: ErrorDetail };
        message = detailToMessage(parsed.detail);
      } catch {
        message = String(response.data || message);
      }
      throw new Error(message);
    }
    return JSON.parse(String(response.data)) as { task_id: string; status: string };
  } catch (err) {
    // A failed upload must surface as an error. Returning a demo task id here
    // would show the user a fake task that never runs a real engine.
    throw new Error(networkMessage(err));
  }
}

export async function createWatermarkTask(
  payload: WatermarkPayload = {}
): Promise<{ task_id: string; status: string }> {
  // A failed create must surface as an error; returning a demo task id would
  // show the user a task that never runs.
  return request<{ task_id: string; status: string }>("/api/tasks/watermark", {
    method: "POST",
    data: payload
  });
}

export async function getTask(taskId: string): Promise<TaskStatus> {
  // Polling must report the real backend state; a mock task would let the UI
  // claim progress for a task that never ran.
  return request<TaskStatus>(`/api/tasks/${taskId}`);
}

export async function cancelTask(taskId: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/tasks/${taskId}/cancel`, { method: "POST" });
}

export async function confirmTask(taskId: string, payload: TaskConfirmPayload = {}): Promise<TaskStatus> {
  return request<TaskStatus>(`/api/tasks/${taskId}/confirm`, {
    method: "POST",
    data: payload
  });
}

export async function deleteTask(taskId: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/tasks/${taskId}`, { method: "DELETE" });
}

export async function getHistory(query: { type?: string; status?: string } | string = {}): Promise<HistoryItem[]> {
  try {
    const normalized = typeof query === "string" ? { type: query } : query;
    const params = [
      normalized.type ? `type=${encodeURIComponent(normalized.type)}` : "",
      normalized.status ? `status=${encodeURIComponent(normalized.status)}` : ""
    ].filter(Boolean).join("&");
    const response = await request<{ items: HistoryItem[] }>(`/api/history${params ? `?${params}` : ""}`);
    return response.items;
  } catch (err) {
    if (IS_DEMO_MODE && err instanceof Error && err.message === "DEMO_FALLBACK") return delay(mockHistory);
    throw err;
  }
}

export async function deleteHistoryItem(id: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/history/${id}`, { method: "DELETE" });
}

export async function submitFeedback(payload: FeedbackPayload): Promise<{ ok: boolean; id: string }> {
  try {
    return await request<{ ok: boolean; id: string }>("/api/feedback", {
      method: "POST",
      data: payload
    });
  } catch (err) {
    if (IS_DEMO_MODE && err instanceof Error && err.message === "DEMO_FALLBACK") {
      return delay({ ok: true, id: "fb_demo" });
    }
    throw err;
  }
}

export { mockHistory, mockParseResult, mockTask };
