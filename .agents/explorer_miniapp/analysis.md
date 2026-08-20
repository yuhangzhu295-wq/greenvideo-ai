# Frontend Pages & API Client Analysis Report - apps/miniapp

## Core Findings
The frontend application `apps/miniapp` is built using **Taro + React**. It provides a fully functional UI layout for a video parsing, downloading, and watermark removal tool, but it currently relies heavily on mock data, missing button handlers, and a mock API fallback. The polling flow in the processing page is implemented via `setInterval` but lacks completion/failure handling and redirection logic.

---

## 1. Page-by-Page Analysis

The application has 10 main pages configured under `apps/miniapp/src/pages`:

| Page | Path | Functional Status / Key Role |
| --- | --- | --- |
| **home** | `/pages/home/index` | Main video link submission page. paste from clipboard, call `parseVideo`, save result in Taro storage, and redirect. |
| **result** | `/pages/result/index` | Displays parse results retrieved from Taro storage. Provides download actions and watermark / audio extraction shortcuts. |
| **watermark** | `/pages/watermark/index` | AI Video Watermark removal setup. Mode toggle (standard vs AI), triggers mock watermark task and redirects to processing. |
| **processing** | `/pages/processing/index` | Status page that polls task status using `setInterval` (every 2.8s) and displays a progress ring. |
| **history** | `/pages/history/index` | Renders user's history list retrieved from `/api/history`. Filters and stats cards are currently static. |
| **profile** | `/pages/profile/index` | User dashboard displaying membership info, daily limits, and links to support, invite, and tutorials. |
| **invite** | `/pages/invite/index` | Friend referral page. Allows copying of referral code. Stats and recent invite lists are static. |
| **feedback** | `/pages/feedback/index` | Form for submitting user questions and bug reports. Fully functional validation and submit handler. |
| **faq** | `/pages/faq/index` | Static FAQ list. Collapsible question cards. Search and consult buttons are static. |
| **tutorial** | `/pages/tutorial/index` | Static tutorial page outlining the steps to copy, paste, parse, and clean videos. |

---

## 2. API Helper & Mock Client (`apps/miniapp/src/api`)

### Client Implementation (`client.ts`)
- **Base URL Configuration**: Uses `process.env.TARO_APP_API_BASE_URL` which is injected via Taro's `defineConstants` in webpack build configuration (`apps/miniapp/config/index.ts`).
- **Mock Fallback Mode**: If `TARO_APP_API_BASE_URL` is empty, the client intercepts all API calls and returns mock data from `mock.ts` after a simulated network delay of **220ms**.
- **Request Wrapper**: Uses `Taro.request`. Returns parsed response data if `response.statusCode` is between 200 and 299, otherwise throws `Error(detail)` which is caught and shown to the user in a toast.

### API Endpoints
- `POST /api/parse`: Initiates link parsing (`parseVideo`).
- `POST /api/tasks/download`: Creates a video/audio download task (`createDownloadTask`).
- `POST /api/tasks/watermark`: Creates a watermark removal task (`createWatermarkTask`).
- `GET /api/tasks/${taskId}`: Gets the status of a specific task (`getTask`).
- `GET /api/history`: Retrieves the task history list (`getHistory`).
- `POST /api/feedback`: Submits user feedback (`submitFeedback`).

---

## 3. Data Retrieval & Process Flows

### A. Parsing & Result Flow
1. **Link Submission**: On `home` page, clicking "开始解析" (`startParse`) triggers `parseVideo(url)`.
2. **Local Storage Storage**: Upon success, the `ParseResult` is saved to Taro storage:
   ```typescript
   Taro.setStorageSync("parse_result", result);
   ```
3. **Redirection**: It then navigates to `/pages/result/index` using custom `navigate` helper.
4. **Data Retrieval**: `result` page reads the result inside `useEffect`:
   ```typescript
   const stored = Taro.getStorageSync("parse_result");
   if (stored) setResult(stored);
   ```

### B. Download Flow
1. **Download Trigger**: On `result` page, clicking a format's download button calls `download(formatId)`.
2. **Task Creation**: Calls `createDownloadTask(result.id, formatId)`.
3. **No Polling/Redirect**: It shows a toast `任务已创建 ${task.status}`. It **does not** redirect to the processing page or start polling.

### C. Watermark Flow
1. **Setup**: On `watermark` page, the user selects mode (`standard` or `ai`).
2. **Submission**: Clicking "上传视频 / 图片" or secondary buttons triggers `createTask()`.
3. **Task Creation**: Calls `createWatermarkTask({ source_task_id: "manual_upload_demo", mask_regions: [], mode })`.
4. **Redirection**: Shows toast and navigates to `/pages/processing/index?taskId=${task.task_id}`.

### D. Polling / Processing Flow
1. **Router Param**: On mount, `processing` reads `taskId` from router params (`Taro.getCurrentInstance().router?.params?.taskId`).
2. **Polling Setup**:
   ```typescript
   const timer = setInterval(load, 2800);
   ```
3. **Task Retrieval**: `load()` fetches `getTask(taskId)` and updates the `task` state.
4. **Display**: The page updates the `<ProgressRing progress={task.progress} />` and remaining ETA.
5. **CRITICAL GAP**: There is **no logic** inside the polling function to check if `task.status === "success"` or `"failed"`. The client polls indefinitely, and the user is never redirected to a success/result screen or shown a completed state.

---

## 4. Empty Functions, Mock Code & Fake Actions

Several parts of the application contain fake interactions, hardcoded elements, or missing event handlers:

### 1. Fake Actions (Triggers Toast, No Actual Work)
- **`result` page "复制直链" (Copy Direct Link)**:
  Shows "已复制" toast, but does not copy anything to the clipboard (missing `Taro.setClipboardData` call).
- **`home` page "一键粘贴" (Paste link)**:
  Tries to get data from clipboard. If it fails, it silently sets the URL to the demo link `"https://example.com/demo"`.

### 2. Missing Event Handlers / Empty Functions
- **`faq` page**:
  - `<Input placeholder='搜索问题' />` has no value binding or change handler.
  - "在线咨询" (Online Consult) `<Button>` has no `onClick` handler.
- **`feedback` page**:
  - The quick grid cards ("在线客服", "提交反馈", "问题工单", "使用建议") have no click handlers or navigation.
- **`history` page**:
  - Filter chips ("全部", "下载", "去水印", "成功", "处理中") have no state binding or click handlers to filter the list.
- **`invite` page**:
  - "立即邀请" (Invite Now) `<Button>` has no `onClick` handler or wx `open-type="share"`.
- **`processing` page**:
  - "后台处理" (Run in Background) and "取消任务" (Cancel Task) `<Button>`s have no `onClick` handlers.
- **`profile` page**:
  - Menu rows "我的下载", "我的任务", and "设置" have no event handlers.
- **`tutorial` page**:
  - "常见操作" grid and "更多问题" link have no onClick handlers.
- **`components/index.tsx` (TaskCard Component)**:
  - "重新处理", "取消任务", "查看结果", and "删除" buttons do not have `onClick` handlers attached.

### 3. Hardcoded Mock Data in UI
- **`history` page**: Week statistics (23 tasks, 19 successful, 2 processing, 2.1 GB saved) are hardcoded.
- **`profile` page**: User name ("视频达人"), ID ("88888888"), membership type ("普通会员"), remaining quota ("5次"), and count stats (128 downloads, 36 watermark removals, 8 favorites) are hardcoded.
- **`invite` page**: Reward count stats and the recent invite list are hardcoded.
- **`processing` page**: The steps "模型分析中" and "帧级修复" are statically listed as checkmark and "进行中", regardless of the backend `task.status` or `task.stage`.
