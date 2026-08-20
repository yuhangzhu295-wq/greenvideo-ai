# GreenVideo AI Diagnostic Report

This report presents findings from our investigation of the greenvideo-ai codebase regarding frontend compilation/runtime errors, backend URL parsing, yt-dlp installation status, and the existing test suite.

---

## 1. Frontend compilation/runtime error: `(0, d.L) is not a function`

### 1.1 Direct Observations & Evidence
- **Detached API References**: In `apps/miniapp/src/api/client.ts` and `apps/miniapp/src/pages/home/index.tsx`, we observe `Taro` methods being detached and cast to `any` before invocation:
  - `client.ts:16`: `const taroRequest = (Taro as any).request;`
  - `client.ts:82`: `const taroUpload = (Taro as any).uploadFile;`
  - `home/index.tsx:13`: `const getClipboard = (Taro as any).getClipboardData;`
  - `home/index.tsx:29`: `const setStorage = (Taro as any).setStorageSync;`
- **Dynamic Imports**: In `apps/miniapp/src/pages/history/index.tsx`, dynamic imports are used for `Taro`:
  - `history/index.tsx:71`: `import("@tarojs/taro").then(Taro => Taro.showToast({title: "删除失败", icon: "none"}));`
  - `history/index.tsx:75`: `import("@tarojs/taro").then(Taro => { Taro.navigateTo({...}) });`
- **Missing Router Dependency**: In `apps/miniapp/package.json`, `@tarojs/router` is completely missing from the dependencies. Consequently, running H5 build (`pnpm build:h5`) fails with a module resolution error:
  `resolve '@tarojs/router' in '.../apps/miniapp/src' - C:\...\@tarojs\router doesn't exist`
- **Missing Node Types**: In `apps/miniapp/package.json` devDependencies, `@types/node` is missing, causing TypeScript typecheck errors regarding `process`, `__dirname`, and `path`.

### 1.2 Diagnostic Logic Chain
1. **Babel API Transformation**: Taro uses `babel-plugin-transform-taro-api` to replace direct API calls (e.g. `Taro.request(...)`) with named imports (e.g., `import { request } from "@tarojs/taro"`) to enable tree-shaking.
2. **Bypassing the Transform**: By using indirect assignments like `const setStorage = (Taro as any).setStorageSync`, the Babel plugin does not match the direct call pattern. Thus, the raw property access `Taro.setStorageSync` remains in the compiled code.
3. **Difference between Weapp and H5**:
   - In Weapp (Mini Program), the `Taro` default export is a global proxy that forwards requests to `wx`, so `Taro.setStorageSync` is defined at runtime.
   - In H5, the `Taro` default export is a simple object and does not dynamically proxy all mini-program APIs. The actual APIs are exported as named exports. Since they are not bound to the default `Taro` object in H5, accessing `Taro.setStorageSync` returns `undefined`.
4. **Resulting Error**: When the application tries to invoke the detached/unbound function via Webpack's compiled namespace reference (e.g., `(0, d.L)`), it throws a `TypeError: (0, d.L) is not a function` because the property resolved to `undefined`.
5. **Dynamic Imports Failure**: In `history/index.tsx`, `import("@tarojs/taro")` resolves to the ESM namespace object. In H5, calling `.showToast()` directly on the imported namespace object fails if the runtime has not registered it or if Webpack's interop helper (`_interopRequireWildcard`) resolves it to an object structure that does not match standard expectations.

### 1.3 Recommended Fixes
1. Add `@tarojs/router` and `@types/node` to `apps/miniapp/package.json` dependencies / devDependencies.
2. Replace all detached API references with direct calls on `Taro` or import the APIs as named exports:
   - *Example in `client.ts`:* Use `Taro.request(...)` instead of `const taroRequest = (Taro as any).request; taroRequest(...)`.
   - *Alternative:* Use `import { request, uploadFile } from "@tarojs/taro";` directly.
3. Refactor dynamic imports of `@tarojs/taro` in `pages/history/index.tsx` to static imports at the top of the file:
   - Remove `import("@tarojs/taro").then(...)` and use `Taro.showToast(...)` and `Taro.navigateTo(...)` directly, importing `Taro` statically at line 3.

---

## 2. Backend URL parsing & Security Validation

### 2.1 Direct Observations & Evidence
- **URL Extraction in backend**: Located in `services/api/app/routers/parse.py` (lines 20-22):
  ```python
  # 自动从包含文案的字符串中提取真实 URL
  match = re.search(r'https?://[^\s/$.?#].[^\s]*', payload.url)
  clean_url = match.group(0) if match else payload.url
  ```
- **SSRF Validation**: Located in `services/api/app/services/security.py` (lines 44-65):
  - Checks if URL has `http` or `https` scheme.
  - Rejects localhost, `.local` domains, private IP addresses (`_is_blocked_ip`), and query parameters containing login tokens/cookies (`LOGIN_STATE_KEYS`).
- **Failure Cause**: If the extraction regex `r'https?://[^\s/$.?#].[^\s]*'` fails to match a URL (or if URL extraction was missing in earlier revisions), the *entire* user-submitted paste string is sent as the `clean_url` to `validate_public_http_url(url)`.
  - Since the paste string starts with `"4.17 复制打开抖音..."` rather than `"http"`, `urlparse` returns an empty `parsed.scheme`.
  - This fails the validation check `parsed.scheme not in {"http", "https"}` and raises a `SecurityError("仅支持 http/https 公开视频链接。")`, which bubble up as an HTTP 400 Bad Request error.
- **Regex Limitations**: The pattern `r'https?://[^\s/$.?#].[^\s]*'` expects a non-slash/non-dot character immediately after `https://`, followed by a dot or other character. This is brittle and can fail on certain subdomains or short URLs (e.g. `https://v` or `https://a`).

### 2.2 Recommended Fixes
Replace the regex pattern with a much simpler, standard URL extraction pattern, and then strip trailing punctuation from the match:
```python
# app/routers/parse.py
match = re.search(r"https?://[^\s]+", payload.url)
if match:
    # Extract the match and strip common trailing punctuation characters
    clean_url = match.group(0).rstrip('.,;!?()[]{}<>"\'-')
else:
    clean_url = payload.url
```

---

## 3. yt-dlp installation check

### 3.1 Direct Observations & Evidence
- **Host Check**: Attempting to execute `yt-dlp --version` on the Windows host results in `CommandNotFoundException`. Thus, **yt-dlp is NOT installed** in the host Windows environment.
- **Backend Calling Mechanism**:
  - Located in `services/api/app/services/downloader.py` (lines 39-46):
    ```python
    if shutil.which("yt-dlp"):
        try:
            return self._parse_with_ytdlp(public_url)
        except DownloadError:
            raise
        except Exception:
            return self._demo_result(public_url)
    return self._demo_result(public_url)
    ```
  - The backend uses `shutil.which("yt-dlp")` to determine if `yt-dlp` is available in the system PATH.
  - If installed, it executes a subprocess call: `subprocess.run(["yt-dlp", "--dump-json", "--no-playlist", "--skip-download", "--no-warnings", url], ...)` to parse metadata.
  - If not installed, it gracefully degrades to calling `self._demo_result(public_url)` to return stub data.

---

## 4. Existing tests & execution instructions

### 4.1 Test Catalog
1. **Pytest Suite**: Located in `services/api/tests/`.
   - `conftest.py`: Defines mock fixtures for subprocesses (`yt-dlp`, `ffmpeg`), and `httpx` mock clients for external inpainting services (`IOPaint`).
   - `test_api.py`: Validates FastAPI route responses.
   - `test_security.py`: Verifies path traversal protections and SSRF URL blocks.
   - `test_integration.py`: Contains 107 test cases across Tier 1 (Feature Coverage) and Tier 2 (Boundary & Corner Cases).
2. **E2E Smoke Script**: Located in `scripts/smoke_api.py`.
   - Runs a lightweight end-to-end integration check against a live FastAPI server, validating all 8 backend features.
3. **All Features Scenario Validation**: Located in `services/api/test_all_features.py`.
   - Simulates end-to-end task pipelines (health -> parse -> download -> poll -> watermark).
4. **Verification Script**: Located in `scripts/verify.sh`.
   - Orchestrates dependency installation, frontend linting/typechecking, backend virtual environment creation, backend pytest execution, and running the API smoke tests.

### 4.2 How to Execute Tests
- **Backend Pytest**:
  1. Open PowerShell or Command Prompt.
  2. Navigate to `services/api`: `cd services/api`
  3. Run the virtual environment pytest: `.venv\Scripts\pytest`
- **E2E Smoke Tests**:
  1. Start the FastAPI development server:
     - Navigate to `services/api`: `cd services/api`
     - Run: `.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8000`
  2. In another terminal, from the project root directory, run:
     - `python scripts/smoke_api.py http://127.0.0.1:8000`
- **Verification Pipeline**:
  - Run `bash scripts/verify.sh` from the root directory (requires Git Bash or WSL on Windows).
