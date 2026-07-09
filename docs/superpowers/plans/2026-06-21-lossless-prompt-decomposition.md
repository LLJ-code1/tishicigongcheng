# Lossless Prompt Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lossless “仅拆解” workflow for complete prompts while leaving the existing expansion workflow unchanged.

**Architecture:** A dedicated prompt-engine function asks the selected OpenAI-compatible provider for item-level bilingual classification. Server-side normalization verifies one-to-one coverage against the exact source fragments before compiling ordinary editor blocks, including an extra unclassified block. A separate API route and frontend action feed the verified result into the existing version/editor state.

**Tech Stack:** Python standard library HTTP server and unittest, OpenAI-compatible chat completions, vanilla JavaScript, Node test runner.

---

### Task 1: Prompt-engine contract

**Files:**
- Modify: `prototype/tests/test_prompt_engine.py`
- Modify: `prototype/prompt_engine.py`
- Create: `prototype/prompts/text/modes/decompose.md`

- [ ] Write failing tests for exact source preservation, bilingual item construction, unclassified output, and rejected missing/duplicate fragments.
- [ ] Run `python -m unittest prototype.tests.test_prompt_engine -v` and confirm failures are caused by missing decomposition functions.
- [ ] Implement source splitting, decomposition prompt assembly, strict normalization, and provider invocation without calling expansion code.
- [ ] Run the prompt-engine tests and confirm they pass.

### Task 2: HTTP API

**Files:**
- Modify: `prototype/tests/test_server.py`
- Modify: `prototype/server.py`

- [ ] Write failing tests for request validation and saved provider configuration.
- [ ] Run `python -m unittest prototype.tests.test_server -v` and confirm the decomposition request processor and route are absent.
- [ ] Add `POST /api/text/decompose` and map errors through the existing JSON error handling.
- [ ] Run the server tests and confirm they pass.

### Task 3: Workbench interaction

**Files:**
- Modify: `prototype/tests/app.test.js`
- Modify: `prototype/index.html`
- Modify: `prototype/app.js`
- Modify: `prototype/styles.css`

- [ ] Write failing tests confirming that “仅拆解” and “拓展并结构化” are separate controls and decomposition has independent loading/reducer actions.
- [ ] Run `node --test prototype/tests/app.test.js` and confirm the new UI contract fails.
- [ ] Add the secondary decomposition button, API call, loading state, and reducer actions while leaving `expandTextPrompt()` unchanged.
- [ ] Run the frontend tests and confirm they pass.

### Task 4: Regression and live verification

**Files:**
- Modify: `docs/development-log.md`

- [ ] Run all Python and Node test suites.
- [ ] Restart Prompt Studio and call `/api/text/decompose` with the local provider using a mixed-language prompt.
- [ ] Verify the exact original fragments are present in the returned blocks and the existing `/api/text/expand` remains operational.
- [ ] Record the feature and verification results in the development log.

