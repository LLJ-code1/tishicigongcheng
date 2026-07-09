# Basic Text-to-Image Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the simulated prompt expansion with a modular Anima-oriented bilingual prompt pipeline that can call a local or external OpenAI-compatible LLM.

**Architecture:** Four Markdown modules are assembled by a new backend prompt engine: visual expansion rules, hybrid Tags/natural-language mode, Anima model profile, and bilingual JSON schema. The backend calls an OpenAI-compatible chat endpoint, validates and normalizes the JSON response, and the frontend applies the real result to the existing structured editor.

**Tech Stack:** Python standard library HTTP server and `urllib`, Markdown prompt modules, browser JavaScript, Node test runner, Python `unittest`.

---

### Task 1: Prompt module assembly

**Files:**
- Create: `prototype/prompt_engine.py`
- Create: `prototype/prompts/text/core/visual_expansion.md`
- Create: `prototype/prompts/text/modes/expand_hybrid.md`
- Create: `prototype/prompts/text/models/anima.md`
- Create: `prototype/prompts/text/schemas/bilingual_structured_output.md`
- Test: `prototype/tests/test_prompt_engine.py`

- [ ] **Step 1: Write failing assembly tests**

Test that `build_text_expand_system_prompt("anima")` includes the four module headings, excludes unresolved placeholders, requires bilingual output, and defaults to unweighted hybrid Anima output.

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_prompt_engine -v
```

Expected: import failure because `prompt_engine.py` does not exist.

- [ ] **Step 3: Implement the four prompt modules and assembler**

The assembler reads only whitelisted module paths and joins them in this order:

```text
core -> mode -> model -> schema
```

- [ ] **Step 4: Run the test and verify GREEN**

Expected: module assembly tests pass.

### Task 2: OpenAI-compatible model call and response validation

**Files:**
- Modify: `prototype/prompt_engine.py`
- Test: `prototype/tests/test_prompt_engine.py`

- [ ] **Step 1: Write failing request and normalization tests**

Cover:

```python
settings = {
    "localTextUrl": "http://127.0.0.1:8080/v1",
    "localTextModel": "local-model",
}
```

Verify the request targets `/chat/completions`, sends the selected model, passes user input separately from system rules, parses fenced JSON, requires all structure blocks, and normalizes labels/source/weight fields.

- [ ] **Step 2: Run the test and verify RED**

Expected: missing call and normalization functions.

- [ ] **Step 3: Implement minimal provider config, HTTP call, JSON extraction, one format-repair retry, and normalization**

Errors must distinguish missing configuration, unreachable model endpoint, upstream HTTP failure, and invalid model JSON.

- [ ] **Step 4: Run the test and verify GREEN**

Expected: all prompt-engine tests pass without network access by using an injected fake transport.

### Task 3: Backend expansion API and editable modules

**Files:**
- Modify: `prototype/server.py`
- Modify: `prototype/tests/test_server.py`

- [ ] **Step 1: Write failing server tests**

Verify:

- `PROMPT_TEMPLATES` exposes the four text modules.
- `POST /api/text/expand` routes through the prompt engine.
- Blank input and unsupported providers return validation errors.

- [ ] **Step 2: Run the test and verify RED**

Expected: missing templates and endpoint.

- [ ] **Step 3: Add module template metadata and expansion handler**

The handler loads saved settings, accepts `input`, `provider`, `targetModel`, and optional `context`, then returns:

```json
{
  "item": {
    "positiveEn": "",
    "positiveZh": "",
    "negativeEn": "",
    "negativeZh": "",
    "relationEn": "",
    "relationZh": "",
    "blocks": [],
    "checks": {}
  }
}
```

- [ ] **Step 4: Run the test and verify GREEN**

Expected: server and prompt-engine tests pass.

### Task 4: Frontend real expansion flow and model settings

**Files:**
- Modify: `prototype/app.js`
- Modify: `prototype/index.html`
- Modify: `prototype/styles.css`
- Modify: `prototype/tests/app.test.js`

- [ ] **Step 1: Write failing reducer and static UI tests**

Verify:

- Real model results initialize output and blocks.
- Loading and failure states do not create fake prompt versions.
- Settings UI contains local/external base URL and model fields.
- The “演示模拟” text is removed.

- [ ] **Step 2: Run the Node test and verify RED**

Run:

```powershell
& 'C:\Users\13688\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test prototype\tests\app.test.js
```

Expected: missing actions and settings fields.

- [ ] **Step 3: Implement asynchronous `/api/text/expand` call**

The button:

1. validates non-empty input;
2. dispatches loading;
3. posts the selected provider and input;
4. applies returned blocks and bilingual output;
5. displays backend errors without simulated fallback.

- [ ] **Step 4: Add compact provider configuration fields**

Local defaults:

```text
URL: http://127.0.0.1:8080/v1
Model: local-model
```

External fields include URL, model, and optional API key. Changes save through the existing settings API.

- [ ] **Step 5: Run Node tests and verify GREEN**

Expected: all frontend tests pass.

### Task 5: Documentation and full verification

**Files:**
- Modify: `prototype/README.md`
- Modify: `docs/development-log.md`

- [ ] **Step 1: Document setup**

Document llama-server/OpenAI-compatible requirements, settings fields, endpoint behavior, and the fact that the first version targets Anima without text weights.

- [ ] **Step 2: Run all verification**

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s prototype\tests -p 'test_*.py' -v
& 'C:\Users\13688\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test prototype\tests\app.test.js
.\AnimaDex\.venv\Scripts\python.exe -m py_compile prototype\prompt_engine.py prototype\server.py prototype\db.py
& 'C:\Users\13688\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --check prototype\app.js
```

Expected: zero failures and zero syntax errors.

- [ ] **Step 3: Start the local server and smoke-test**

Verify:

- `GET /api/prompt-templates` lists the four modules.
- blank `POST /api/text/expand` returns HTTP 400;
- configured but unavailable local endpoint returns a clear HTTP 503 error;
- the page loads at `http://127.0.0.1:57913/`.
