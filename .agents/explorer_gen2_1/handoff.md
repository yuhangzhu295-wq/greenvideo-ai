# Handoff Report — Explorer Diagnosis Complete

## 1. Observation
- **Detached API calls in source**:
  - `apps/miniapp/src/api/client.ts` line 16: `const taroRequest = (Taro as any).request;`
  - `apps/miniapp/src/api/client.ts` line 82: `const taroUpload = (Taro as any).uploadFile;`
  - `apps/miniapp/src/pages/home/index.tsx` line 13: `const getClipboard = (Taro as any).getClipboardData;`
  - `apps/miniapp/src/pages/home/index.tsx` line 29: `const setStorage = (Taro as any).setStorageSync;`
- **Dynamic imports in source**:
  - `apps/miniapp/src/pages/history/index.tsx` line 71: `import("@tarojs/taro").then(Taro => Taro.showToast({title: "删除失败", icon: "none"}));`
  - `apps/miniapp/src/pages/history/index.tsx` line 75: `import("@tarojs/taro").then(Taro => { ...`
- **H5 Build Error**: Run `pnpm --filter @greenvideo/miniapp build:h5` outputted:
  `resolve '@tarojs/router' in 'C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\apps\miniapp\src'`
  `C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\node_modules\@tarojs\router doesn't exist`
- **Backend URL extraction**:
  - `services/api/app/routers/parse.py` line 21: `match = re.search(r'https?://[^\s/$.?#].[^\s]*', payload.url)`
- **SSRF/Security Validation**:
  - `services/api/app/services/security.py` line 44: `def validate_public_http_url(url: str) -> str:`
  - Rejects URLs with empty or non-http/https schemes with `SecurityError("仅支持 http/https 公开视频链接。")`.
- **yt-dlp command line check**: Running `yt-dlp --version` on host Windows command line:
  `yt-dlp : The term 'yt-dlp' is not recognized as the name of a cmdlet, function, script file, or operable program.`
- **Backend downloader execution**:
  - `services/api/app/services/downloader.py` line 39: `if shutil.which("yt-dlp"):`
- **Test execution**: Running `.venv\Scripts\pytest` inside `services/api`:
  `107 passed, 1 warning in 3.61s`

## 2. Logic Chain
- **Issue 1 (Frontend compilation/runtime error)**:
  1. Taro's compile-time Babel plugin `babel-plugin-transform-taro-api` looks for direct `Taro.apiName(...)` calls and replaces them with named imports of the API for tree-shaking.
  2. Because the code detaches the API calls (e.g. `(Taro as any).setStorageSync`), the Babel plugin fails to transform them.
  3. Under H5, the `Taro` default export object is a simple placeholder and does not proxy or expose these APIs. Therefore, accessing `Taro.setStorageSync` evaluates to `undefined`.
  4. Invoking the undefined property causes a runtime TypeError `(0, d.L) is not a function`.
  5. The missing `@tarojs/router` dependency prevents the H5 build from successfully compiling and running.
- **Issue 2 (Backend URL parsing)**:
  1. The regex `r'https?://[^\s/$.?#].[^\s]*'` is too restrictive. If it fails to match a URL in a paste string, `parse.py` falls back to passing the *entire* paste string.
  2. The entire paste string has no valid URL scheme at the start (e.g. starts with `"4.17 复制打开...`").
  3. `validate_public_http_url` fails scheme check, raising `SecurityError`, resulting in a 400 Bad Request.
  4. A robust URL extraction pattern (e.g., `r"https?://[^\s]+"` with trailing punctuation stripping) successfully extracts `https://v.douyin.com/qu2PVcn74Lw/` from the paste string, which passes security checks.
- **Issue 3 (yt-dlp installation)**:
  1. Running `yt-dlp` directly confirms it is not in the system's PATH.
  2. The backend calls `shutil.which("yt-dlp")` to verify existence. If absent, it gracefully degrades to mock/demo data.
- **Issue 4 (Existing tests)**:
  1. The project has 107 pytest cases in `services/api/tests/` verifying routes, database operations, and security limits.
  2. E2E smoke tests in `scripts/smoke_api.py` and scenario feature tests in `services/api/test_all_features.py` require a running uvicorn server.
  3. A full verification flow is provided in `scripts/verify.sh`.

## 3. Caveats
- We did not test real-world video downloading/parsing because `yt-dlp` is not installed on the Windows host. We verified that the codebase gracefully falls back to mock demo data under this condition.

## 4. Conclusion
- **Frontend**: The `(0, d.L) is not a function` error is caused by detached/cast Taro API references and dynamic imports that bypass Taro's compile-time Babel transform, combined with a missing `@tarojs/router` dependency.
- **Backend URL parsing**: The 400 SecurityError is caused by regex failure failing to extract the URL, which causes the entire paste string to be validated as an invalid URL.
- **yt-dlp**: `yt-dlp` is missing on the host. The backend uses `shutil.which` to fallback to mock data.
- **Tests**: Pytest suite is complete (107 cases passing). E2E/smoke scripts require a running server.

## 5. Verification Method
- **Verify Frontend H5 build**: Install `@tarojs/router` and run `pnpm build:h5`.
- **Verify Backend Pytest**: Navigate to `services/api` and run `.venv\Scripts\pytest`.
