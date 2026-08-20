# Handoff Report — explorer_miniapp

## 1. Observation

- **API Fallback Logic**:
  In `apps/miniapp/src/api/client.ts`, lines 5-6 and 37-39:
  ```typescript
  const API_BASE_URL = process.env.TARO_APP_API_BASE_URL || "";
  ...
  export async function parseVideo(url: string): Promise<ParseResult> {
    if (!API_BASE_URL) {
      return delay({ ...mockParseResult, id: url ? "parse_local_demo" : mockParseResult.id });
    }
  ```
  If `TARO_APP_API_BASE_URL` is empty, all API operations mock responses locally.

- **Polling Loop without Exit Condition**:
  In `apps/miniapp/src/pages/processing/index.tsx`, lines 11-26:
  ```typescript
  useEffect(() => {
    const taskId = Taro.getCurrentInstance().router?.params?.taskId || "task_demo";
    let mounted = true;
    const load = async () => {
      const data = await getTask(String(taskId));
      if (mounted) {
        setTask(data);
      }
    };
    load();
    const timer = setInterval(load, 2800);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);
  ```
  The task status is updated in the state, but there is no conditional check (e.g., `if (data.status === 'success')`) to navigate the user to the result or history page, causing the page to poll indefinitely.

- **Fake Clipboard Actions**:
  In `apps/miniapp/src/pages/result/index.tsx`, lines 82-84:
  ```typescript
  <Button className='outline-btn' onClick={() => Taro.showToast({ title: "已复制", icon: "success" })}>
    复制直链
  </Button>
  ```
  The button triggers a "Copied" toast but does not call any system clipboard API like `Taro.setClipboardData`.

- **Static Elements / Missing Handlers**:
  - `apps/miniapp/src/pages/history/index.tsx`, lines 45-52:
    ```typescript
    <View className='filter-row'>
      {["全部", "下载", "去水印", "成功", "处理中"].map((chip, index) => (
        <Text className={`filter-chip ${index === 0 ? "active" : ""}`} key={chip}>
          {chip}
        </Text>
      ))}
      <Text className='filter-link'>筛选</Text>
    </View>
    ```
    The filter chips are strictly presentational with no `onClick` handlers or filtering state.
  - In `apps/miniapp/src/components/index.tsx`, lines 149-158, the action buttons inside `TaskCard` ("重新处理", "取消任务", "查看结果", "删除") have no `onClick` handlers.

- **Watermark Quick Setup Mock**:
  In `apps/miniapp/src/pages/watermark/index.tsx`, lines 10-18:
  ```typescript
  async function createTask() {
    const task = await createWatermarkTask({
      source_task_id: "manual_upload_demo",
      mask_regions: [],
      mode: mode
    });
    Taro.showToast({ title: "任务已创建", icon: "success" });
    navigate(`/pages/processing/index?taskId=${task.task_id}`);
  }
  ```
  Both the upload area and "From Album" / "Paste Link" buttons trigger the exact same mock task (`source_task_id: "manual_upload_demo"`) and redirect directly, bypassing any actual file selection or link input.

- **Workspace Node Modules**:
  Running `npm run typecheck` inside `apps/miniapp` fails with `MODULE_NOT_FOUND` because `node_modules` is not populated. The project uses a `pnpm` monorepo structure.

---

## 2. Logic Chain

1. **API Client Strategy**: The client (`client.ts`) switches to mock mode when the base URL environment variable is unset. This allows running the application in a standalone simulated environment, but hides structural connection issues with the real backend.
2. **Infinite Processing Loop**: Because the processing page (`processing/index.tsx`) does not inspect the status field of the returned `TaskStatus` object, it keeps polling the backend at `2800ms` intervals forever, failing to transition the user when a task completes.
3. **Presentational Gaps**: Elements such as the clipboard copy action in the results page, filter chips in the history page, and buttons on the profile/tutorial/FAQ pages are mock artifacts to complete the visual theme but require proper bindings to work in a real-world context.
4. **Watermark Flow Shortcut**: Instead of prompting the user for an image/video upload or URL inputs, the watermark page goes straight to task creation using a mock ID, skipping interactive file selection.

---

## 3. Caveats

- We only examined the frontend code in `apps/miniapp`. We assumed the backend endpoints (`/api/parse`, `/api/tasks/download`, `/api/tasks/watermark`, etc.) exist and are defined properly on the backend service, but did not verify backend code.
- We did not deploy the application to a real WeChat Mini Program container/simulator to test device-specific behaviors (e.g. how `Taro.getClipboardData` behaves on various mobile OS).
- Monorepo dependencies (`node_modules`) are not installed/restored in the workspace, so typescript/build validations cannot run without executing package installation first.

---

## 4. Conclusion

The `apps/miniapp` project is structured cleanly and has standard page navigation, styling, and basic page state management. However, it is currently in a mock-heavy state:
1. It relies on mock responses from `client.ts` unless `TARO_APP_API_BASE_URL` is set.
2. The processing page needs an exit/redirection logic when task polling finishes (success or failure).
3. The result page needs real clipboard copying, and download actions need redirection/polling links.
4. Missing click handlers must be implemented for filters, task actions (delete, retry), FAQ search, and profile page sub-menus.

---

## 5. Verification Method

- **Inspect Key Files**:
  Verify the observed code snippets by viewing files:
  - `apps/miniapp/src/api/client.ts`
  - `apps/miniapp/src/pages/processing/index.tsx`
  - `apps/miniapp/src/pages/result/index.tsx`
- **Monorepo Setup & Compile**:
  To verify compilation, first run `pnpm install` at the workspace root, and then execute:
  ```powershell
  pnpm --filter @greenvideo/miniapp run typecheck
  ```
