# AI Local Edit and Model Preset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an extensible image-model selector with profile-backed parameter defaults and replace the deterministic Chinese edit path with fail-closed AI semantic edit previews.

**Architecture:** Keep model-profile resolution in the existing profile/recipe layer. Add a focused AI edit module that validates model proposals, builds signed immutable previews, and applies only signed affected blocks; reuse the existing OpenAI-compatible provider configuration and transport conventions. The frontend loads profiles, applies defaults until a field is manually overridden, and renders Chinese per-block AI diffs before the existing atomic save flow.

**Tech Stack:** Python 3 standard library HTTP service, OpenAI-compatible JSON chat API, vanilla JavaScript, HTML/CSS, `unittest`, Node test runner.

## Global Constraints

- First release exposes only `anima-1.1-v1`, while the selector is populated from `GET /api/model-profiles`.
- Candidate resolutions remain explicitly unverified and are never auto-selected or described as optimal.
- AI may change the smallest necessary related block set; every changed block needs English, Chinese, and a Chinese reason.
- Invalid or unavailable AI output fails closed and never falls back to the deterministic dictionary.
- Confirmation creates a new Recipe and continues through the existing atomic workspace commit path.
- Existing unrelated dirty worktree changes must be preserved.
- Automated tests use fake transports and never require a real external API.

---

### Task 1: AI proposal contract and validation

**Files:**
- Create: `prototype/ai_edit_engine.py`
- Create: `prototype/prompts/text_edit.md`
- Test: `prototype/tests/test_ai_edit_engine.py`

**Interfaces:**
- Consumes: `recipe.normalize_recipe`, `recipe.recipe_hash`, `recipe.BLOCK_IDS`; provider settings compatible with `prompt_engine.resolve_text_provider`.
- Produces: `propose_ai_edit(instruction, recipe, settings, provider="local", target_model="anima", transport=None, timeout=120) -> dict`.
- Produces: `AIEditError(message, code, status)`.

- [ ] **Step 1: Write failing validation tests**

Add tests proving that a fake model response with `scene`, `lighting`, and `effects` returns all three complete proposals and preserves “破碎城堡” in `zh`; also prove rejection of unknown fields, unknown/duplicate block IDs, empty `en`/`zh`/`reason`, unchanged blocks, malformed JSON, and missing provider configuration.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_ai_edit_engine -v
```

Expected: import failure because `ai_edit_engine` does not exist.

- [ ] **Step 3: Implement the strict model proposal**

Create a prompt that sends the normalized thirteen blocks and requires exactly:

```json
{"changes":[{"blockId":"scene","en":"...","zh":"...","reason":"..."}]}
```

Use the existing provider resolver/request option helpers and transport signature. Reject Markdown, extra top-level/change fields, non-unique IDs, IDs outside the current recipe, blank strings, more than thirteen changes, and proposals identical to the current block.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: all AI proposal contract tests pass.

- [ ] **Step 5: Commit the isolated contract**

```powershell
git add prototype/ai_edit_engine.py prototype/prompts/text_edit.md prototype/tests/test_ai_edit_engine.py
git commit -m "feat: add strict AI prompt edit proposals"
```

### Task 2: Signed preview and apply lifecycle

**Files:**
- Modify: `prototype/ai_edit_engine.py`
- Test: `prototype/tests/test_ai_edit_engine.py`

**Interfaces:**
- Consumes: `propose_ai_edit(...)`.
- Produces: `create_ai_edit_preview(instruction, recipe, base_hash, settings, provider, target_model, transport=None) -> dict`.
- Produces: `apply_ai_edit_preview(recipe, preview, parent_version, new_version=None) -> dict`.

- [ ] **Step 1: Write failing preview lifecycle tests**

Test that the preview contains `affectedIds`, all remaining `lockedIds`, Chinese before/after values, `reason`, `resultHash`, `previewHash`, and `ready=true`. Test that apply merges only signed affected blocks and rejects a changed `after`, changed `reason`, added block, removed block, stale Recipe hash, malformed signature, and non-ready preview.

- [ ] **Step 2: Run focused tests and verify RED**

Expected: lifecycle functions are missing.

- [ ] **Step 3: Implement immutable signed previews**

Generate a process-local HMAC-SHA256 key with `secrets.token_bytes(32)`. Sign the canonical JSON security view containing the instruction, base/result hashes, affected/locked IDs, diffs, and ready state. At apply time verify HMAC, current Recipe hash, exact block membership, and merge only each signed diff’s complete `after` block. Append a change record with instruction, affected IDs, reasons, and hashes.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 1 test command. Expected: proposal and lifecycle tests pass.

- [ ] **Step 5: Commit the preview lifecycle**

```powershell
git add prototype/ai_edit_engine.py prototype/tests/test_ai_edit_engine.py
git commit -m "feat: sign and apply AI edit previews"
```

### Task 3: HTTP routing without deterministic fallback

**Files:**
- Modify: `prototype/server.py`
- Test: `prototype/tests/test_server.py`

**Interfaces:**
- Consumes: `create_ai_edit_preview`, `apply_ai_edit_preview`.
- Produces: existing `POST /api/text/edit-preview` accepting `instruction`, `recipe`, `baseRecipeHash`, `provider`, `targetModel`.
- Produces: existing `POST /api/text/edit-apply` returning `recipe`, `recipeHash`, `workbenchBlocks`, and change metadata.

- [ ] **Step 1: Write failing request and route tests**

Assert provider and target model are forwarded, settings are loaded server-side, AI preview errors preserve their codes/statuses, apply returns projected workbench blocks, and the edit-preview route never calls deterministic `preview_edit`.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_server -v
```

Expected: request rejects new fields or still invokes the deterministic engine.

- [ ] **Step 3: Replace the route adapters**

Change only the edit-preview/edit-apply adapters and imports. Keep the URLs stable. Load provider settings through the existing settings boundary, forward only allowed fields, normalize errors through the current JSON error handler, and retain the current Recipe-to-workbench projection.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: all server tests pass.

- [ ] **Step 5: Commit the HTTP integration**

```powershell
git add prototype/server.py prototype/tests/test_server.py
git commit -m "feat: route prompt edits through AI previews"
```

### Task 4: Profile-backed model selector and parameter provenance

**Files:**
- Modify: `prototype/index.html`
- Modify: `prototype/app.js`
- Modify: `prototype/styles.css`
- Test: `prototype/tests/app.test.js`

**Interfaces:**
- Consumes: `GET /api/model-profiles` items with `profileId`, `displayName`, `defaultParameters`, `candidateResolutionPresets`, and validation metadata.
- Produces: frontend state `modelProfiles`, `modelProfilesStatus`, and selected `modelProfileId`.
- Produces: UI controls `#recipeModelProfile` and `#recipeResolutionPreset`.

- [ ] **Step 1: Write failing reducer and markup tests**

Add Node tests showing profiles load from API-shaped data, the selected profile applies sampler/scheduler/steps/CFG defaults only to non-manual keys, selecting the same profile can restore defaults after explicit user action, candidate resolutions are labeled “候选，待验证” and remain unselected, and the selector remains visible with one option.

- [ ] **Step 2: Run Node tests and verify RED**

Run:

```powershell
node --test prototype\tests\app.test.js
```

Expected: missing state/actions and missing selectors.

- [ ] **Step 3: Implement profile loading and controls**

Load `/api/model-profiles` during app bootstrap, render options from returned data, dispatch profile selection through the reducer, apply `defaultParameters` with source `model_default`, preserve manual overrides unless the user explicitly selects “恢复模型推荐”, and expose candidate resolutions as opt-in values with verification labels.

- [ ] **Step 4: Run Node tests and verify GREEN**

Run the command from Step 2. Expected: all app tests pass.

- [ ] **Step 5: Commit the model selector**

```powershell
git add prototype/index.html prototype/app.js prototype/styles.css prototype/tests/app.test.js
git commit -m "feat: add profile-backed model parameter selector"
```

### Task 5: AI local-edit frontend workflow

**Files:**
- Modify: `prototype/index.html`
- Modify: `prototype/app.js`
- Modify: `prototype/styles.css`
- Test: `prototype/tests/app.test.js`

**Interfaces:**
- Consumes: AI edit preview diffs shaped as `{id, label, before, after, reason}`.
- Produces: request payload with `provider` and `targetModel`.
- Produces: preview UI with changed/unchanged counts, Chinese before/after, reason, loading/error states, and guarded confirmation.

- [ ] **Step 1: Write failing interaction/render tests**

Assert the UI says “AI 局部修改” and “生成修改预览”; request payload contains the configured text provider; three affected blocks render three reasons and Chinese diffs; unchanged count equals thirteen minus affected count; invalid/model-unavailable previews keep confirmation disabled; and no frontend path invokes a dictionary fallback.

- [ ] **Step 2: Run Node tests and verify RED**

Run the Task 4 Node command. Expected: old copy, old payload, and old preview rendering fail assertions.

- [ ] **Step 3: Implement the AI preview UI**

Update copy and placeholder, include provider/target model in preview requests, render escaped Chinese values and reasons, show affected/unchanged counts, keep request revision guards, and map `provider_not_configured`/connection failures to a message directing the user to “模型与接口”.

- [ ] **Step 4: Run Node tests and verify GREEN**

Run the Task 4 Node command. Expected: all app tests pass.

- [ ] **Step 5: Commit the frontend workflow**

```powershell
git add prototype/index.html prototype/app.js prototype/styles.css prototype/tests/app.test.js
git commit -m "feat: add AI local prompt edit workflow"
```

### Task 6: Documentation, full regression, and browser verification

**Files:**
- Modify: `prototype/README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/roadmap.md`
- Modify: `docs/integration-guide.md`

**Interfaces:**
- Documents: AI-only edit behavior, provider requirement, signed preview/apply lifecycle, model selector semantics, and unverified resolution policy.

- [ ] **Step 1: Update current documentation**

Replace deterministic-edit product claims with the AI edit contract; document the expanded edit-preview request fields and errors; describe that one visible profile is available and candidate dimensions are opt-in only.

- [ ] **Step 2: Run all automated verification**

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s prototype\tests -p "test_*.py"
node --test prototype\tests\app.test.js
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s scripts\tests -p "test_*.py"
```

Expected: all suites pass with no external API dependency.

- [ ] **Step 3: Verify the live localhost workflow**

Reload `http://127.0.0.1:57913/` and verify:

1. The one-option model selector is visible.
2. Model defaults and parameter sources render correctly.
3. Candidate resolutions are visibly unverified and not auto-selected.
4. “环境改成破碎城堡，下着大雨” produces a multi-block AI preview when the configured model is available.
5. With the text model unavailable, the page reports that AI editing is unavailable and leaves all blocks unchanged.
6. Confirming a valid preview creates a new unsaved Recipe version ready for the existing atomic save action.

- [ ] **Step 4: Review the final diff for unrelated changes**

Use `git diff --check`, inspect only task-owned hunks, and confirm pre-existing wordlist-polish changes remain intact.

- [ ] **Step 5: Commit documentation**

```powershell
git add prototype/README.md docs/architecture.md docs/roadmap.md docs/integration-guide.md
git commit -m "docs: document AI prompt editing workflow"
```
