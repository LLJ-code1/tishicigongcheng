# Local LLM Retranslate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a right-output-panel button that uses the configured local LLM to replace only pending Chinese translation placeholders.

**Architecture:** Add a focused translation function in `prompt_engine.py`, expose it through one server endpoint, and add reducer actions plus one browser action in `app.js`. The server validates complete ID coverage before the browser atomically updates blocks, applied blocks, current output, and the current version snapshot.

**Tech Stack:** Python standard library HTTP server, OpenAI-compatible local LLM API, vanilla JavaScript state reducer, Node test runner, Python unittest.

---

### Task 1: Translation Engine

**Files:**
- Modify: `prototype/prompt_engine.py`
- Test: `prototype/tests/test_prompt_engine.py`

- [ ] **Step 1: Write failing tests**

Add tests proving `translate_pending_items()` calls the local provider, returns complete ID-to-Chinese translations, and rejects missing, duplicate, empty, or non-Chinese results.

- [ ] **Step 2: Run the focused tests**

Run:

```powershell
& 'C:\Users\13688\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest prototype.tests.test_prompt_engine.PendingTranslationTests
```

Expected: FAIL because `translate_pending_items` does not exist.

- [ ] **Step 3: Implement the engine**

Add `translate_pending_items(items, settings, transport=None, timeout=90)`. It must always resolve the `local` provider, request strict JSON shaped as `{"translations":[{"id":"subject","zh":"初音未来"}]}`, and reject any response whose IDs do not exactly match the request or whose `zh` is empty or contains no CJK characters.

- [ ] **Step 4: Run the focused tests**

Expected: all `PendingTranslationTests` pass.

### Task 2: Server Endpoint

**Files:**
- Modify: `prototype/server.py`
- Test: `prototype/tests/test_server.py`

- [ ] **Step 1: Write failing tests**

Add tests proving `process_text_translate_pending_request()` rejects an empty item list, always uses saved local settings, and the handler exposes `POST /api/text/translate-pending`.

- [ ] **Step 2: Run the focused tests**

Expected: FAIL because the processor and route do not exist.

- [ ] **Step 3: Implement the endpoint**

Import `translate_pending_items`, add the request processor, route, and handler using the existing `PromptEngineError` response pattern.

- [ ] **Step 4: Run server tests**

Expected: server tests pass.

### Task 3: Browser State and Button

**Files:**
- Modify: `prototype/index.html`
- Modify: `prototype/app.js`
- Modify: `prototype/styles.css`
- Test: `prototype/tests/app.test.js`

- [ ] **Step 1: Write failing tests**

Add reducer tests proving translation loading is tracked, success replaces only pending `zh` fields while preserving existing Chinese, recompiles output without increasing the version, and failure leaves data unchanged. Add static tests for the button and endpoint.

- [ ] **Step 2: Run Node tests**

Expected: FAIL because the actions, button, and endpoint call do not exist.

- [ ] **Step 3: Implement state and UI**

Add `translatingPending` state, `START_PENDING_TRANSLATION`, `APPLY_PENDING_TRANSLATIONS`, and `PENDING_TRANSLATION_FAILED`. Place `data-action="translate-pending"` in the output header, render its disabled/loading state, collect only blocks whose `zh` starts with `待本地 LLM 翻译：`, call the new endpoint, and show a no-op toast when nothing needs translation.

- [ ] **Step 4: Run Node tests**

Expected: all Node tests pass.

### Task 4: Verification and Documentation

**Files:**
- Modify: `docs/development-log.md`

- [ ] **Step 1: Run all automated tests**

Run Python unittest discovery and the complete Node test file. Expected: zero failures.

- [ ] **Step 2: Restart Prompt Studio**

Restart the server on port `57913` and verify local LLM health returns HTTP 200.

- [ ] **Step 3: Verify in the real page**

Apply an AnimaDex resource that produces pending placeholders, click “本地 LLM 翻译”, and verify the placeholder disappears from both the structure block and right-side Chinese output while existing translated blocks remain unchanged.

- [ ] **Step 4: Update the development log**

Record the endpoint, atomic update behavior, automated test totals, and real local LLM result.
