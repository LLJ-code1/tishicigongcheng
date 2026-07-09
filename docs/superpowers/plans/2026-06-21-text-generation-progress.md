# Text Generation Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a compact, persistent progress panel for prompt decomposition and expansion so the user can distinguish waiting, model work, rendering, completion, and failure.

**Architecture:** Add a serializable `generationProgress` object to the existing frontend state and update it through reducer actions. A browser-side timer refreshes elapsed time while a request is active; the existing synchronous HTTP endpoints remain unchanged, and progress text honestly groups model generation and server validation into one waiting phase.

**Tech Stack:** Vanilla JavaScript state reducer, static HTML/CSS, Node test runner, in-app browser verification.

---

### Task 1: Progress State Contract

**Files:**
- Modify: `prototype/tests/app.test.js`
- Modify: `prototype/app.js`

- [ ] **Step 1: Write failing reducer tests**

Add tests asserting that `START_TEXT_DECOMPOSITION` creates a running progress state, `APPLY_TEXT_DECOMPOSITION` completes it, and failure actions preserve the error message.

- [ ] **Step 2: Run the frontend tests**

Run:

```powershell
& 'C:\Users\13688\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test prototype/tests/app.test.js
```

Expected: the new progress assertions fail because `generationProgress` does not exist.

- [ ] **Step 3: Implement reducer state**

Add `generationProgress` with `status`, `task`, `title`, `detail`, `provider`, `startedAt`, `elapsedSeconds`, and `finishedAt`. Update both decomposition and expansion start, success, and failure actions without changing their API payloads.

- [ ] **Step 4: Re-run the frontend tests**

Expected: the reducer progress tests pass.

### Task 2: Compact Progress Panel

**Files:**
- Modify: `prototype/tests/app.test.js`
- Modify: `prototype/index.html`
- Modify: `prototype/styles.css`
- Modify: `prototype/app.js`

- [ ] **Step 1: Write failing markup tests**

Assert that the page contains `generationProgress`, `generationProgressTitle`, `generationProgressDetail`, and `generationProgressElapsed` below the two prompt action buttons.

- [ ] **Step 2: Run the frontend tests**

Expected: markup assertions fail because the panel is absent.

- [ ] **Step 3: Add markup, rendering, and elapsed timer**

Render idle, running, success, and error styles. Start a one-second timer only while a text task is running, update elapsed time without dispatching new versions, and clear the timer on completion or failure.

- [ ] **Step 4: Bump static asset cache versions**

Update the CSS and JavaScript query versions in `prototype/index.html`.

- [ ] **Step 5: Run the frontend tests**

Expected: all frontend tests pass.

### Task 3: Real Browser Verification

**Files:**
- Modify: `docs/development-log.md`

- [ ] **Step 1: Reload the local page**

Verify the new asset version is loaded and the progress panel is visible beneath the buttons.

- [ ] **Step 2: Run decomposition from the page**

Confirm the panel changes from running to completed, elapsed time updates, V1 appears, and no console error is produced.

- [ ] **Step 3: Run expansion from a clean page**

Confirm the same progress lifecycle and successful structured output.

- [ ] **Step 4: Run all automated tests**

Run all Python and Node suites and record the results in `docs/development-log.md`.

