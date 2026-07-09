# Multi-Block Random Variant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users select multiple structured prompt blocks and generate one coordinated random variant for all selected blocks.

**Architecture:** Add a dedicated multi-block engine and `/api/text/regenerate-blocks` endpoint. The model generates all selected blocks in one response, then a second model pass reviews cross-block consistency against immutable blocks. The frontend stores selected block IDs, renders checkboxes and a batch action bar, and applies returned blocks as pending edits.

**Tech Stack:** Python prompt engine and HTTP server, vanilla JavaScript reducer/UI, Python unittest, Node test runner.

---

### Task 1: Multi-Block Prompt Engine

**Files:**
- Modify: `prototype/tests/test_prompt_engine.py`
- Modify: `prototype/prompt_engine.py`

- [ ] Add failing tests for coordinated multi-block output, unsupported/locked selection rejection, exact selected-ID coverage, and unchanged candidates.
- [ ] Run prompt-engine tests and confirm failures are caused by the missing multi-block function.
- [ ] Implement `regenerate_prompt_blocks` with generation and whole-result review passes.
- [ ] Re-run prompt-engine tests.

### Task 2: HTTP Endpoint

**Files:**
- Modify: `prototype/tests/test_server.py`
- Modify: `prototype/server.py`

- [ ] Add failing tests for saved provider settings, selected IDs, and route declaration.
- [ ] Implement `process_text_regenerate_blocks_request` and `POST /api/text/regenerate-blocks`.
- [ ] Re-run server tests.

### Task 3: Structured Editor Multi-Select

**Files:**
- Modify: `prototype/tests/app.test.js`
- Modify: `prototype/app.js`
- Modify: `prototype/index.html`
- Modify: `prototype/styles.css`

- [ ] Add failing reducer and markup tests for selection, locked-block exclusion, batch loading, and pending multi-block application.
- [ ] Add `selectedVariantBlockIds` and `batchRegenerating` state/actions.
- [ ] Add eligible-block checkboxes and a batch action bar with selected count, clear action, and “联合随机”.
- [ ] Call the new endpoint and apply all returned blocks as pending edits.
- [ ] Bump static asset cache versions and run frontend tests.

### Task 4: Verification

**Files:**
- Modify: `docs/development-log.md`

- [ ] Run all Python and Node tests.
- [ ] From the live page, select at least scene, composition, and lighting, run joint randomization, and verify all three change while unselected blocks remain unchanged.
- [ ] Verify the result remains pending until “应用修改并生成新版本”.
- [ ] Record results in the development log.

