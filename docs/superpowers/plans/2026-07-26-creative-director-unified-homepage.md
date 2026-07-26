# Creative Director Unified Homepage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the text/image choice homepage with one recoverable text-plus-single-image creative-director flow that proposes directions, asks adaptive questions, produces a confirmable visual brief, and lets the user select a target model before entering the existing workbench.

**Architecture:** Add a focused `creative_director.py` orchestration layer that calls the already configured local or external OpenAI-compatible text provider with a versioned built-in Skill prompt, validates the model proposal, and applies it through the existing server-authoritative creative-intake transition engine. The browser owns presentation and local image bytes only; existing `/api/vision/analyze` supplies visible-image evidence, while project metadata stores only safe image references and canonical creative-intake state. The current workbench and Recipe pipeline remain intact and are entered only after brief confirmation and model selection.

**Tech Stack:** Python standard library, existing OpenAI-compatible transport helpers, `http.server`, vanilla JavaScript/HTML/CSS, Python `unittest`, Node `node:test`.

## Global Constraints

- Main product is `prototype/`; `AnimaDex/` remains read-only.
- Windows desktop target, 1920×1080, Prompt Studio port `57913`.
- Homepage must not ask the user to choose 文生图 or 图生图.
- First delivery supports text plus zero or one reference image; multi-image composition is a separate plan.
- Image bytes remain browser/request-local and are never stored in project metadata, Recipe v1, logs, or prompts sent to a text-only provider.
- Sending an image to an external service requires an explicit visible confirmation; this phase uses existing local vision analysis by default.
- Creative-director state changes must go through `apply_creative_intake_transition`; the model cannot directly author canonical state.
- Confirmed and locked content cannot be silently changed.
- Provider failures preserve the last canonical state and conversation display.
- Persistence remains on `POST /api/workspace/commit`; do not add a second save path.
- Do not change SQLite `user_version = 1`.
- New behavior requires Python and Node tests; tests use mock transports and no real external API.
- Do not declare multi-image UI, model webpage research, LoRA management, or model-adapted thirteen-block preview complete.

---

## Stable Interfaces

```python
# prototype/creative_director.py
class CreativeDirectorError(Exception):
    code: str

def build_creative_director_system_prompt(skill_override: str = "") -> str: ...

def run_creative_director_turn(
    *,
    current: object,
    user_message: object,
    image_evidence: object,
    provider: object,
    settings: dict,
    skill_override: object = "",
    transport=None,
) -> dict: ...
```

Success result:

```json
{
  "message": "我先给你三个方向……",
  "item": {"schemaVersion": 1, "revision": 2, "stage": "intake"},
  "proposedAction": {"type": "set_directions", "directions": []},
  "provider": "local",
  "model": "configured-model"
}
```

HTTP endpoint:

```text
POST /api/creative-intake/director
```

Request fields:

```json
{
  "current": {},
  "message": "红裙，奔跑",
  "imageEvidence": null,
  "provider": "local",
  "skillOverride": ""
}
```

The browser uses `POST /api/creative-intake/transition` for deterministic,
non-model choices such as selecting one of the proposed directions,
confirming a brief, selecting a model, and reopening a brief.

---

### Task 1: Built-in Creative Director Skill and Model Proposal Schema

**Files:**

- Create: `prototype/prompts/creative_director.md`
- Create: `prototype/creative_director.py`
- Create: `prototype/tests/test_creative_director.py`

**Produces:** prompt loader, strict model-proposal normalization, provider call orchestration, and transition application.

- [ ] Write failing tests proving the built-in prompt contains the confirmed rules: one recommended direction plus two alternatives, distinguish user/image/AI sources, adaptive questioning, “交给你决定”, lock/conflict protection, brief-before-prompt behavior, and strict JSON-only output.
- [ ] Run the focused test and observe `ModuleNotFoundError`.
- [ ] Add the prompt file and `build_creative_director_system_prompt()`. User overrides append below the immutable safety/output-contract section; blank overrides use only the built-in prompt.
- [ ] Add failing tests for model proposal parsing. The only accepted proposal keys are `message` and `action`; action must be one of `set_directions`, `set_brief_draft`, or `replace_inputs`. Reject markdown fences, duplicate JSON keys, unknown keys, NaN/Infinity, oversized text, and direct `confirm_brief`/`select_model`.
- [ ] Implement strict parsing using existing `prompt_engine.parse_model_json` only if it preserves duplicate-key rejection; otherwise add a local strict parser without changing the shared parser.
- [ ] Add failing orchestration tests with a fake transport for local/API provider selection, provider configuration errors, invalid model output, and a legal proposal.
- [ ] Implement `run_creative_director_turn()`: normalize current, validate inputs, resolve provider through existing settings, call one chat completion, normalize proposal, pass action to `apply_creative_intake_transition`, and return message plus canonical item.
- [ ] Verify a failed call or invalid proposal never mutates the caller-owned current state.
- [ ] Run `prototype.tests.test_creative_director` and existing prompt-engine tests.
- [ ] Commit:

```powershell
git add -- prototype/prompts/creative_director.md prototype/creative_director.py prototype/tests/test_creative_director.py
git commit -m "feat: add creative director engine"
```

### Task 2: Creative Director HTTP API and Settings Override

**Files:**

- Modify: `prototype/server.py`
- Modify: `prototype/db.py`
- Modify: `prototype/tests/test_server.py`
- Modify: `prototype/tests/test_db.py`

**Consumes:** `run_creative_director_turn`.

- [ ] Write failing server tests for `POST /api/creative-intake/director`: legal local and external provider calls with mocked transport, missing current/message, unknown keys, invalid image evidence, invalid Skill override, provider failure, invalid model JSON, and domain-transition failure.
- [ ] Assert stable error codes and that response failures expose no API key or upstream response body.
- [ ] Add the route after global Host/Origin/Content-Type/body-size validation and reuse `read_json()`.
- [ ] Add a processor function that loads redacted settings, passes the actual stored secret internally, and delegates to the director engine.
- [ ] Add `creativeDirectorSkillOverride` to the settings allowlist with a 100,000-character UTF-8 limit; GET may return the override because it is not a secret. Unknown/sensitive nested keys remain stripped.
- [ ] Test PUT/GET preservation, blank reset to default, and no collision with API key redaction.
- [ ] Run full server and DB tests.
- [ ] Commit:

```powershell
git add -- prototype/server.py prototype/db.py prototype/tests/test_server.py prototype/tests/test_db.py
git commit -m "feat: expose creative director API"
```

### Task 3: Unified Homepage Markup and Layout

**Files:**

- Modify: `prototype/index.html`
- Modify: `prototype/styles.css`
- Modify: `prototype/tests/app.test.js`

**Produces:** one homepage surface with conversation, one-image attachment, provider selector, brief card, and model gate.

- [ ] Add failing static DOM tests proving the homepage no longer contains the two creation-choice buttons or navigation buttons labelled 文生图/图片解析.
- [ ] Add failing tests for these stable IDs: `directorConversation`, `directorMessageInput`, `directorSendBtn`, `directorImageInput`, `directorImagePreview`, `directorProviderControls`, `directorDirections`, `directorBriefCard`, `directorModelGate`, `directorContinueBtn`.
- [ ] Replace the homepage choices with:
  - conversation transcript;
  - one multiline composer;
  - attachment button/drop zone and removable preview;
  - local/API task provider selector;
  - send/stop control;
  - direction choices;
  - source-aware brief card;
  - model selector shown only after brief confirmation.
- [ ] Keep project list and settings access reachable.
- [ ] Hide the existing workbench until the canonical state reaches `model_selected`; do not delete its text/image tools.
- [ ] Implement desktop CSS with responsive containment at 1920×1080, clear focus states, scrollable transcript, source badges, lock badges, error banners, and disabled/busy states.
- [ ] Run static Node tests and `node --check prototype/app.js`.
- [ ] Commit:

```powershell
git add -- prototype/index.html prototype/styles.css prototype/tests/app.test.js
git commit -m "feat: add unified creative director homepage"
```

### Task 4: Frontend Director Conversation and Deterministic Actions

**Files:**

- Modify: `prototype/app.js`
- Modify: `prototype/tests/app.test.js`

**Produces:** reducer state, API calls, stale guards, direction selection, brief confirmation/reopen, model selection, and recovery rendering.

- [ ] Write failing reducer tests for conversation messages, busy/error state, proposed directions, selected direction, draft brief, confirmed brief, model gate, and “return to brief”.
- [ ] Add state fields:

```javascript
directorMessages: [],
directorBusy: false,
directorError: "",
directorImageEvidence: null,
directorImageAnalysisStatus: "idle",
directorRequestRevision: 0
```

- [ ] Ensure project hydration reconstructs a compact transcript from canonical creative-intake data when raw display messages are absent; do not persist arbitrary assistant prose as authoritative state.
- [ ] Add failing request tests for `POST /api/creative-intake/director`; capture workspace session, project revision, and creative-intake revision, and discard stale results with `isCreativeIntakeResponseCurrent`.
- [ ] Implement send flow with current canonical state, message, evidence, task provider, and configured Skill override. On success, append display message and dispatch `CREATIVE_INTAKE_REPLACED`; on failure preserve state and input.
- [ ] Add deterministic helper `transitionCreativeIntake(action)` using the existing transition endpoint and the same stale guard.
- [ ] Wire direction selection, confirm brief, reopen brief, select model, and continue-to-workbench actions.
- [ ] `confirm_brief` stays disabled while open questions or open conflicts exist.
- [ ] Selecting a model uses IDs from existing `/api/model-profiles`; no model webpage research is added.
- [ ] Persist every accepted canonical change through the existing metadata-only workspace commit flow; debounce only display updates, never change operation ID/frozen-body behavior.
- [ ] Run complete Node tests.
- [ ] Commit:

```powershell
git add -- prototype/app.js prototype/tests/app.test.js
git commit -m "feat: wire creative director conversation"
```

### Task 5: Single-Image Evidence Flow

**Files:**

- Modify: `prototype/app.js`
- Modify: `prototype/index.html`
- Modify: `prototype/styles.css`
- Modify: `prototype/tests/app.test.js`
- Modify: `prototype/tests/test_server.py`

**Consumes:** existing `/api/vision/analyze`; produces safe evidence for director turns.

- [ ] Write failing tests that one supported image can be attached, previewed, replaced, and removed; a second image replaces the first after confirmation rather than creating hidden multi-image state.
- [ ] Reuse existing MIME/size validation and browser object-URL cleanup.
- [ ] On attach, create a safe creative-intake image reference with client ID, pure filename, MIME type, status `local_reference_not_embedded`, and initially empty `requestedUses`.
- [ ] Add requested-use chips: 人物、外貌、服装、动作、环境、构图、光影、风格; allow “让 AI 建议”.
- [ ] Write failing tests that analysis calls `/api/vision/analyze`, converts successful analyzer output into bounded text evidence, and never stores image bytes/raw base64 in creative-intake/project metadata.
- [ ] Implement local vision analysis before the next director turn. Evidence shape is:

```json
{
  "imageId": "image-...",
  "requestedUses": ["action"],
  "summary": "bounded merged visible-content analysis",
  "sourceModels": ["wd14"],
  "uncertain": false
}
```

- [ ] Limit summary to 100,000 characters and model IDs to 16 safe strings; server director validation rejects mismatched image IDs.
- [ ] If analysis partially fails, preserve successful evidence and show failed analyzers; if all fail, preserve attachment and allow retry/remove without inventing evidence.
- [ ] External image upload remains disabled in this phase; UI explicitly states local analysis. Do not silently send bytes to the selected external text provider.
- [ ] Run Node and focused server/vision tests.
- [ ] Commit:

```powershell
git add -- prototype/app.js prototype/index.html prototype/styles.css prototype/tests/app.test.js prototype/tests/test_server.py
git commit -m "feat: add single image director evidence"
```

### Task 6: Brief Card, Model Gate, and Workbench Handoff

**Files:**

- Modify: `prototype/app.js`
- Modify: `prototype/index.html`
- Modify: `prototype/styles.css`
- Modify: `prototype/tests/app.test.js`

- [ ] Write failing render tests for the brief summary and grouped rows: 用户明确、图片借用、AI 补全、已锁定、待确认、冲突.
- [ ] Render source/lock metadata from canonical items; never infer source from prose.
- [ ] Provide per-item “要求修改” that sends a director message rather than directly editing canonical state.
- [ ] Confirm button calls `confirm_brief`; success shows model gate and existing model-profile readiness/evidence status.
- [ ] Model selection calls `select_model`; only `model_selected` enables “进入拆解”.
- [ ] Handoff sets workbench visible and preserves canonical brief/model metadata. It must not pretend decomposition exists; the existing manual expansion/decompose controls remain available with a notice that model-adapted preview is a later phase.
- [ ] Reopen brief from workbench calls `reopen_brief`, hides stale downstream work, and returns to the homepage card.
- [ ] Test refresh/reopen at `intake`, `brief_draft`, `brief_confirmed`, and `model_selected`.
- [ ] Run complete Node tests.
- [ ] Commit:

```powershell
git add -- prototype/app.js prototype/index.html prototype/styles.css prototype/tests/app.test.js
git commit -m "feat: add brief and model handoff"
```

### Task 7: Advanced Skill Editor, Documentation, and Full Verification

**Files:**

- Modify: `prototype/index.html`
- Modify: `prototype/styles.css`
- Modify: `prototype/app.js`
- Modify: `prototype/tests/app.test.js`
- Modify: `docs/architecture.md`
- Modify: `docs/data-model.md`
- Modify: `docs/integration-guide.md`
- Modify: `docs/operator-runbook.md`
- Modify: `docs/roadmap.md`

- [ ] Add failing UI tests for an advanced settings editor that loads `creativeDirectorSkillOverride`, saves it through existing settings, and restores default by writing an empty override after explicit confirmation.
- [ ] Add the editor with immutable-default explanation, character count, save/reset, and no API key echo.
- [ ] Document director engine, prompt source, endpoint, provider behavior, local single-image evidence, state/persistence boundaries, failure behavior, and Skill override.
- [ ] Mark unified homepage/single-image director/brief/model gate complete in roadmap; keep multi-image, model research, LoRA, and model-adapted decomposition pending.
- [ ] Run:

```powershell
& 'H:\提示词工程\AnimaDex\.venv\Scripts\python.exe' -m unittest discover -s prototype\tests -p "test_*.py"
& 'C:\Users\13688\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test prototype\tests\app.test.js
& 'H:\提示词工程\AnimaDex\.venv\Scripts\python.exe' -m unittest discover -s scripts\tests -p "test_*.py"
& 'H:\提示词工程\AnimaDex\.venv\Scripts\python.exe' scripts\build_random_wordlists.py --check
git diff --check
```

- [ ] Verify no image bytes, API keys, local paths, DBs, logs, PIDs, model files, or SDD artifacts are tracked.
- [ ] Commit:

```powershell
git add -- prototype/index.html prototype/styles.css prototype/app.js prototype/tests/app.test.js docs/architecture.md docs/data-model.md docs/integration-guide.md docs/operator-runbook.md docs/roadmap.md
git commit -m "docs: complete unified creative director flow"
```

## Acceptance Scenarios

1. Text only: “红裙、奔跑” → three directions → adaptive question → brief card → confirm → model select → workbench.
2. Single image, explicit use: attach image and select “动作” → local analysis → brief marks action as image-sourced and ignores unselected clothing/environment.
3. Single image, AI suggestion: no requested uses → director proposes visible candidate elements for confirmation, without treating them as confirmed.
4. Locked conflict: later request contradicts a confirmed item → stable conflict shown; no silent overwrite.
5. Provider failure: canonical session, composer text, attachment, and previous conversation remain.
6. Refresh/reopen: canonical stage and brief return; image bytes require reattachment while safe reference remains.
7. External provider: only text plus bounded local image evidence is sent; raw image is not sent.
8. Advanced Skill override: save, use in next call, reset to default.

## Subsequent Plans

After this plan passes:

1. Multi-image provenance and per-image element composition.
2. Original-link model research and evidence-graded versioned profiles.
3. Model-adapted thirteen-block preview and approval.
4. LoRA-lite profiles.
5. End-to-end real Anima validation.
