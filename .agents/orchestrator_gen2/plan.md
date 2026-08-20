# Execution Plan — Gen2

This plan details the steps to resolve the frontend compiler bug, robustify URL extraction, verify yt-dlp, and provide clear running instructions.

## Step 1: Initial Exploration & Diagnostics
- **Agent**: `explorer_gen2_1` (`teamwork_preview_explorer`)
- **Directory**: `.agents/explorer_gen2_1`
- **Goal**:
  - Locate why `(0, d.L) is not a function` is triggered in the miniapp.
  - Diagnose backend parsing of full text strings and why it throws `400 SecurityError`.
  - Check `yt-dlp` availability and integration.
  - Document where existing tests are located.
- **Verification**: Review `analysis.md` written by the explorer.

## Step 2: Implementation of Fixes
- **Agent**: `worker_gen2_1` (`teamwork_preview_worker`)
- **Directory**: `.agents/worker_gen2_1`
- **Goal**:
  - Fix frontend compilation issue (e.g. resolve invalid exports/imports or types compile issue).
  - Modify backend URL parser to extract valid HTTP/HTTPS URLs from arbitrary text input before validation.
  - Implement dynamic fallback or robust checks.
- **Verification**: Code compiles without errors; pytest passes locally for parser.

## Step 3: E2E Verification & Audit
- **Agents**: Reviewer, Challenger, and Forensic Auditor
- **Goal**:
  - Run the test suite on the updated codebase.
  - Perform compliance audit to ensure no security or policy violations.
- **Verification**: Forensic auditor returns CLEAN.

## Step 4: Final Synthesis & Documentation
- **Agent**: Orchestrator (Successor/Self)
- **Goal**:
  - Document the exact steps for the user to:
    1. Clear Taro dev cache and restart the `dev:weapp` process (e.g., `pnpm --filter @greenvideo/miniapp dev:weapp --clear-cache` or deleting `.taro-cache` / `.temp`).
    2. Verify and install `yt-dlp` locally (e.g., `pip install yt-dlp`).
  - Summarize automated test results.
