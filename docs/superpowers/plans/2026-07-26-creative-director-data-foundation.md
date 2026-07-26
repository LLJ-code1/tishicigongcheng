# Creative Director Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the versioned, validated creative-intake state that preserves user intent, image-derived evidence, AI additions, locks, conflicts, stage gates, and downstream invalidation before the new homepage or model-research UI is added.

**Architecture:** Add a focused `creative_intake.py` domain module with strict normalization and a server-authoritative transition function. Expose that transition through one HTTP endpoint, then persist the returned session inside existing project metadata through the current atomic `POST /api/workspace/commit` path; this avoids a schema migration and keeps old projects readable. Extend the frontend state only far enough to create, restore, mutate, and save the session—visible homepage work belongs to the next plan.

**Tech Stack:** Python 3 standard library, SQLite metadata JSON through the existing database layer, `http.server`, vanilla JavaScript state/reducer code, Python `unittest`, Node.js `node:test`.

## Global Constraints

- Main product: `prototype/` Anima Prompt Studio; `AnimaDex/` remains read-only.
- Runtime: Windows desktop, Prompt Studio port `57913`.
- Do not commit API keys, `.env`, SQLite files, logs, PIDs, virtual environments, or model files.
- Keep project persistence on `POST /api/workspace/commit` with stable `operationId` / `Idempotency-Key` and frozen request bodies.
- Do not change `PRAGMA user_version = 1`; this phase stores creative intake in existing metadata JSON.
- Confirmed content cannot be silently overwritten.
- Changing the confirmed brief invalidates decomposition and recipe data; changing only the selected model invalidates decomposition and recipe data but preserves the brief.
- The model may propose a transition, but the server validates and applies it.
- New behavior requires Python and Node tests; automated tests must not call real external APIs.
- Existing uncommitted files are user work. Inspect `git status` before every task and stage only the files named by that task.

---

## File Structure

### Create

- `prototype/creative_intake.py` — schema constants, canonical normalization, transition validation, lock/conflict enforcement, and downstream invalidation.
- `prototype/tests/test_creative_intake.py` — domain tests for normalization and every legal/illegal transition.

### Modify

- `prototype/server.py` — register `POST /api/creative-intake/transition` and map domain failures to stable JSON errors.
- `prototype/tests/test_server.py` — HTTP contract tests for the transition endpoint and strict input rejection.
- `prototype/app.js` — initial creative-intake state, reducer actions, project metadata serialization, hydration, and stale-response guard integration.
- `prototype/tests/app.test.js` — frontend state, persistence, hydration, lock preservation, and invalidation tests.
- `docs/architecture.md` — current data flow and API entry once implementation passes.
- `docs/data-model.md` — document `project.metadata.creativeIntake`.
- `docs/integration-guide.md` — request/response example for the transition endpoint.
- `docs/roadmap.md` — mark only the data foundation complete; do not mark the unified homepage, model research, or multi-image UI complete.

### Stable Interfaces

```python
# prototype/creative_intake.py
class CreativeIntakeValidationError(ValueError):
    code: str

def empty_creative_intake() -> dict: ...
def normalize_creative_intake(value: object) -> dict: ...
def apply_creative_intake_transition(
    current: object,
    action: object,
) -> dict: ...
```

```javascript
// prototype/app.js exports
function emptyCreativeIntake()
function normalizeCreativeIntake(value)
function isCreativeIntakeResponseCurrent(request, sessionId, state)
```

Canonical top-level shape:

```json
{
  "schemaVersion": 1,
  "revision": 0,
  "stage": "intake",
  "inputs": {"text": "", "images": []},
  "directions": [],
  "selectedDirectionId": null,
  "brief": null,
  "selectedModelProfileId": null,
  "decomposition": null,
  "recipeStatus": "missing",
  "conflicts": []
}
```

Allowed stages are `intake`, `direction_selected`, `brief_draft`, `brief_confirmed`,
`model_selected`, `decomposition_draft`, and `decomposition_confirmed`.

---

### Task 1: Canonical Creative-Intake Schema

**Files:**

- Create: `prototype/creative_intake.py`
- Create: `prototype/tests/test_creative_intake.py`

**Interfaces:**

- Produces: `CreativeIntakeValidationError`, `empty_creative_intake()`, and `normalize_creative_intake(value)`.
- Consumed by: Task 2 transition rules and Task 3 HTTP handler.

- [ ] **Step 1: Write failing tests for the empty canonical state**

```python
def test_empty_creative_intake_is_canonical():
    self.assertEqual(
        creative_intake.empty_creative_intake(),
        {
            "schemaVersion": 1,
            "revision": 0,
            "stage": "intake",
            "inputs": {"text": "", "images": []},
            "directions": [],
            "selectedDirectionId": None,
            "brief": None,
            "selectedModelProfileId": None,
            "decomposition": None,
            "recipeStatus": "missing",
            "conflicts": [],
        },
    )
```

- [ ] **Step 2: Run the new test and verify the module is missing**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_creative_intake.PromptStudioCreativeIntakeTests.test_empty_creative_intake_is_canonical
```

Expected: `ERROR` with `ModuleNotFoundError: No module named 'creative_intake'`.

- [ ] **Step 3: Implement constants, error type, and empty state**

```python
SCHEMA_VERSION = 1
STAGES = (
    "intake",
    "direction_selected",
    "brief_draft",
    "brief_confirmed",
    "model_selected",
    "decomposition_draft",
    "decomposition_confirmed",
)


class CreativeIntakeValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def empty_creative_intake() -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "revision": 0,
        "stage": "intake",
        "inputs": {"text": "", "images": []},
        "directions": [],
        "selectedDirectionId": None,
        "brief": None,
        "selectedModelProfileId": None,
        "decomposition": None,
        "recipeStatus": "missing",
        "conflicts": [],
    }
```

- [ ] **Step 4: Run the empty-state test and verify it passes**

Run the command from Step 2.

Expected: `OK`, one test run.

- [ ] **Step 5: Add failing normalization tests**

Cover these exact cases:

```python
def test_normalize_rejects_unknown_top_level_key(self):
    value = creative_intake.empty_creative_intake()
    value["surprise"] = True
    with self.assertRaisesRegex(
        creative_intake.CreativeIntakeValidationError,
        "unsupported fields",
    ):
        creative_intake.normalize_creative_intake(value)

def test_normalize_preserves_source_and_lock_provenance(self):
    value = creative_intake.empty_creative_intake()
    value["brief"] = {
        "status": "draft",
        "summary": "红裙女孩在雨夜奔跑",
        "items": [{
            "id": "item-action",
            "category": "action",
            "text": "奔跑",
            "source": {"type": "image", "refId": "image-1"},
            "locked": True,
        }],
        "aiAdditions": [],
        "openQuestions": [],
    }
    value["stage"] = "brief_draft"
    normalized = creative_intake.normalize_creative_intake(value)
    self.assertEqual(
        normalized["brief"]["items"][0]["source"],
        {"type": "image", "refId": "image-1"},
    )
    self.assertTrue(normalized["brief"]["items"][0]["locked"])
```

Also reject:

- booleans used as integer revisions;
- text over 200,000 characters;
- more than 8 images, 3 directions, 200 brief items, 200 decomposition blocks, or 100 conflicts;
- duplicate IDs within images, directions, brief items, or decomposition blocks;
- image source references whose `refId` is absent from `inputs.images`;
- a `confirmed` brief in any stage before `brief_confirmed`;
- decomposition data before `model_selected`;
- unknown stages, source types, recipe statuses, or object keys;
- non-finite numbers and invalid Unicode.

- [ ] **Step 6: Run the normalization tests and verify they fail**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_creative_intake -v
```

Expected: failures because `normalize_creative_intake` and nested validators are absent.

- [ ] **Step 7: Implement strict normalization**

Use small private helpers:

```python
def _mapping(value: object, label: str) -> Mapping[str, object]: ...
def _reject_unknown_keys(value: Mapping[str, object], allowed: set[str], label: str) -> None: ...
def _text(value: object, label: str, *, maximum: int, allow_empty: bool = True) -> str: ...
def _identifier(value: object, label: str) -> str: ...
def _array(value: object, label: str, *, maximum: int) -> list: ...
def _source(value: object, image_ids: set[str], label: str) -> dict: ...
def _brief(value: object, image_ids: set[str]) -> dict | None: ...
def _decomposition(value: object) -> dict | None: ...
```

Accepted source forms:

```json
{"type": "user", "refId": null}
{"type": "image", "refId": "image-1"}
{"type": "ai", "refId": null}
{"type": "model_rule", "refId": "anima-1.1-v1"}
```

Canonical brief shape:

```json
{
  "status": "draft",
  "summary": "红裙女孩在雨夜奔跑",
  "items": [],
  "aiAdditions": [],
  "openQuestions": []
}
```

Canonical image shape:

```json
{
  "id": "image-1",
  "name": "reference.png",
  "mimeType": "image/png",
  "status": "local_reference_not_embedded",
  "requestedUses": ["action"]
}
```

Do not accept image bytes, filesystem paths, API keys, or arbitrary metadata in this object.

- [ ] **Step 8: Run the domain test file**

Run the command from Step 6.

Expected: all tests in `test_creative_intake.py` pass.

- [ ] **Step 9: Commit Task 1**

```powershell
git add -- prototype/creative_intake.py prototype/tests/test_creative_intake.py
git commit -m "feat: add creative intake schema"
```

---

### Task 2: Server-Authoritative Stage Transitions

**Files:**

- Modify: `prototype/creative_intake.py`
- Modify: `prototype/tests/test_creative_intake.py`

**Interfaces:**

- Consumes: `normalize_creative_intake(value)`.
- Produces: `apply_creative_intake_transition(current, action) -> dict`.
- Consumed by: Task 3 endpoint.

Supported actions:

```text
replace_inputs
set_directions
select_direction
set_brief_draft
confirm_brief
select_model
set_decomposition_draft
confirm_decomposition
reopen_brief
```

- [ ] **Step 1: Write failing tests for legal progression**

Construct a session through these calls:

```python
state = creative_intake.empty_creative_intake()
state = creative_intake.apply_creative_intake_transition(
    state,
    {"type": "replace_inputs", "text": "红裙，奔跑", "images": []},
)
state = creative_intake.apply_creative_intake_transition(
    state,
    {
        "type": "set_directions",
        "directions": [
            {"id": "main", "label": "主方案", "summary": "雨夜奔跑"},
            {"id": "alt-1", "label": "替代一", "summary": "旷野奔跑"},
            {"id": "alt-2", "label": "替代二", "summary": "车站奔跑"},
        ],
    },
)
state = creative_intake.apply_creative_intake_transition(
    state, {"type": "select_direction", "directionId": "main"}
)
self.assertEqual(state["stage"], "direction_selected")
self.assertEqual(state["revision"], 3)
```

Continue through `set_brief_draft`, `confirm_brief`, `select_model`,
`set_decomposition_draft`, and `confirm_decomposition`.

- [ ] **Step 2: Write failing tests for lock and stale-data rules**

Required assertions:

```python
with self.assertRaisesRegex(
    creative_intake.CreativeIntakeValidationError,
    "locked item",
):
    creative_intake.apply_creative_intake_transition(
        brief_state,
        {
            "type": "set_brief_draft",
            "brief": changed_brief_without_locked_action,
            "approvedLockedItemIds": [],
        },
    )
```

Also verify:

- editing a locked item succeeds only when its ID is in `approvedLockedItemIds`;
- `confirm_brief` fails while `openQuestions` or unresolved conflicts remain;
- `select_model` is rejected before brief confirmation;
- `set_decomposition_draft` is rejected before model selection;
- `confirm_decomposition` is rejected if any block is unapproved;
- `reopen_brief` preserves inputs, directions, selected direction, and brief;
- `reopen_brief` clears selected model and decomposition and sets `recipeStatus` to `stale`;
- selecting another model preserves the confirmed brief but clears decomposition and sets `recipeStatus` to `stale`;
- every successful transition increments `revision` exactly once;
- failed transitions do not mutate the input object.

- [ ] **Step 3: Run transition tests and verify they fail**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_creative_intake -v
```

Expected: failures because the transition function is absent.

- [ ] **Step 4: Implement transition dispatch with copy-on-write**

```python
def apply_creative_intake_transition(current: object, action: object) -> dict:
    state = normalize_creative_intake(current)
    command = _mapping(action, "action")
    action_type = _text(
        command.get("type"), "action.type", maximum=64, allow_empty=False
    )
    handler = _TRANSITIONS.get(action_type)
    if handler is None:
        raise CreativeIntakeValidationError(
            "unsupported_action", f"unsupported creative-intake action: {action_type}"
        )
    updated = handler(copy.deepcopy(state), command)
    updated["revision"] = state["revision"] + 1
    return normalize_creative_intake(updated)
```

Implement one private handler per action. Compare locked items by canonical
`(id, category, text, source)` values. `approvedLockedItemIds` authorizes only the
named changes for that single transition and is not stored.

- [ ] **Step 5: Run domain tests**

Run the command from Step 3.

Expected: all creative-intake tests pass.

- [ ] **Step 6: Run the existing Recipe tests**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_recipe -v
```

Expected: existing Recipe tests pass unchanged; creative-intake data is not added to Recipe v1 in this phase.

- [ ] **Step 7: Commit Task 2**

```powershell
git add -- prototype/creative_intake.py prototype/tests/test_creative_intake.py
git commit -m "feat: enforce creative intake transitions"
```

---

### Task 3: Creative-Intake Transition API

**Files:**

- Modify: `prototype/server.py`
- Modify: `prototype/tests/test_server.py`

**Interfaces:**

- Consumes: `apply_creative_intake_transition(current, action)`.
- Produces: `POST /api/creative-intake/transition`.

Request:

```json
{
  "current": {"schemaVersion": 1, "revision": 0, "stage": "intake"},
  "action": {"type": "select_direction", "directionId": "main"}
}
```

Success:

```json
{"item": {"schemaVersion": 1, "revision": 1, "stage": "direction_selected"}}
```

Failure:

```json
{"error": "说明文字", "code": "invalid_transition"}
```

- [ ] **Step 1: Write a failing HTTP success test**

Follow the existing `PromptStudioServerTests` request helper pattern:

```python
def test_creative_intake_transition_returns_server_normalized_state(self):
    response = self.request_json(
        "POST",
        "/api/creative-intake/transition",
        {
            "current": creative_intake.empty_creative_intake(),
            "action": {
                "type": "replace_inputs",
                "text": "红裙，奔跑",
                "images": [],
            },
        },
    )
    self.assertEqual(response.status, 200)
    self.assertEqual(response.json["item"]["revision"], 1)
    self.assertEqual(response.json["item"]["inputs"]["text"], "红裙，奔跑")
```

- [ ] **Step 2: Write failing HTTP rejection tests**

Test all of:

- missing `current`;
- missing `action`;
- unknown top-level request key;
- unknown action type;
- invalid stage transition;
- locked-item overwrite without approval;
- non-object JSON;
- a request exceeding the existing 1 MiB JSON limit.

Assert HTTP `400` for validation failures and stable domain `code` values.

- [ ] **Step 3: Run the focused server tests and verify 404/failures**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_server.PromptStudioServerTests.test_creative_intake_transition_returns_server_normalized_state -v
```

Expected: failure because the endpoint is not registered.

- [ ] **Step 4: Register the route and handler**

At imports:

```python
from creative_intake import (
    CreativeIntakeValidationError,
    apply_creative_intake_transition,
)
```

In `do_POST`:

```python
if parsed.path == "/api/creative-intake/transition":
    return self.handle_creative_intake_transition()
```

Handler:

```python
def handle_creative_intake_transition(self) -> None:
    payload = self.read_json()
    unknown = set(payload) - {"current", "action"}
    if unknown:
        return self.send_json(
            {"error": f"unsupported fields: {', '.join(sorted(unknown))}",
             "code": "invalid_request"},
            status=400,
        )
    try:
        item = apply_creative_intake_transition(
            payload.get("current"),
            payload.get("action"),
        )
    except CreativeIntakeValidationError as error:
        return self.send_json(
            {"error": str(error), "code": error.code},
            status=400,
        )
    self.send_json({"item": item})
```

`read_json()` is the existing strict object reader at `prototype/server.py:1636`;
reuse it and do not add a second JSON parser.

- [ ] **Step 5: Run all focused server tests**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_server -v
```

Expected: all server tests pass.

- [ ] **Step 6: Commit Task 3**

```powershell
git add -- prototype/server.py prototype/tests/test_server.py
git commit -m "feat: expose creative intake transitions"
```

---

### Task 4: Frontend State, Persistence, and Recovery

**Files:**

- Modify: `prototype/app.js`
- Modify: `prototype/tests/app.test.js`

**Interfaces:**

- Consumes: API `{"item": <canonical session>}` from Task 3.
- Produces: `state.creativeIntake`, project metadata field `creativeIntake`, reducer action `CREATIVE_INTAKE_REPLACED`, and stale-response guard.

- [ ] **Step 1: Write failing initial-state and reducer tests**

```javascript
test("starts with an empty creative intake session", () => {
  const state = createInitialState();
  assert.equal(state.creativeIntake.schemaVersion, 1);
  assert.equal(state.creativeIntake.stage, "intake");
  assert.equal(state.creativeIntake.revision, 0);
});

test("replaces creative intake only with a normalized server item", () => {
  const state = createInitialState();
  const next = reduceState(state, {
    type: "CREATIVE_INTAKE_REPLACED",
    item: {
      ...emptyCreativeIntake(),
      revision: 1,
      inputs: {text: "红裙，奔跑", images: []},
    },
  });
  assert.equal(next.creativeIntake.revision, 1);
  assert.equal(next.creativeIntake.inputs.text, "红裙，奔跑");
  assert.equal(next.hasUnsavedProjectChanges, true);
});
```

- [ ] **Step 2: Write failing persistence and hydration tests**

```javascript
test("persists creative intake in project metadata", () => {
  const state = createInitialState();
  state.creativeIntake = {
    ...emptyCreativeIntake(),
    revision: 4,
    stage: "brief_confirmed",
    brief: confirmedBriefFixture(),
  };
  assert.deepEqual(
    buildProjectPayload(state).metadata.creativeIntake,
    state.creativeIntake,
  );
});

test("hydrates creative intake from current project metadata", () => {
  const original = createInitialState();
  const restored = hydrateProjectState(original, {
    id: "project-one",
    metadata: {
      workspaceBaseVersion: 0,
      creativeIntake: {
        ...emptyCreativeIntake(),
        revision: 4,
        stage: "brief_confirmed",
        brief: confirmedBriefFixture(),
      },
    },
    versions: [],
  });
  assert.equal(restored.creativeIntake.stage, "brief_confirmed");
  assert.equal(restored.creativeIntake.revision, 4);
});
```

Also test:

- absent or malformed historical metadata falls back to `emptyCreativeIntake()`;
- `createNewProjectState` resets creative intake while preserving global settings;
- project save without a new prompt version still persists creative intake;
- no image bytes, local paths, or external API keys enter the metadata;
- `shouldConfirmWorkspaceDiscard` returns true after an intake change;
- normalization returns a defensive clone so later action mutation cannot alter state.

- [ ] **Step 3: Run focused Node tests and verify failures**

Run:

```powershell
node --test --test-name-pattern="creative intake|persists creative|hydrates creative" prototype/tests/app.test.js
```

Expected: failures because the exports and state field do not exist.

- [ ] **Step 4: Implement the frontend canonical helpers**

Add:

```javascript
function emptyCreativeIntake() {
  return {
    schemaVersion: 1,
    revision: 0,
    stage: "intake",
    inputs: {text: "", images: []},
    directions: [],
    selectedDirectionId: null,
    brief: null,
    selectedModelProfileId: null,
    decomposition: null,
    recipeStatus: "missing",
    conflicts: [],
  };
}

function normalizeCreativeIntake(value) {
  const source = safeObject(value);
  if (source.schemaVersion !== 1) return emptyCreativeIntake();
  // Validate the exact allowed stage and required container types, then clone.
  // Invalid historical metadata fails closed to the empty state.
}
```

Mirror the server's top-level types and limits. Do not reimplement transition
authorization in JavaScript; the server remains authoritative.

- [ ] **Step 5: Integrate state, reducer, payload, and hydration**

Required changes:

```javascript
// createInitialState
creativeIntake: emptyCreativeIntake(),

// buildProjectPayload metadata
creativeIntake: normalizeCreativeIntake(state.creativeIntake),

// hydrateProjectState
next.creativeIntake = projectMetadataIsCurrent
  ? normalizeCreativeIntake(metadata.creativeIntake)
  : emptyCreativeIntake();
```

Reducer behavior:

```javascript
case "CREATIVE_INTAKE_REPLACED":
  next.creativeIntake = normalizeCreativeIntake(action.item);
  markProjectChanged(next);
  return next;
```

Export `emptyCreativeIntake`, `normalizeCreativeIntake`, and
`isCreativeIntakeResponseCurrent`.

- [ ] **Step 6: Add stale-response protection tests**

```javascript
test("rejects a creative intake response from an older revision", () => {
  const state = createInitialState();
  state.creativeIntake.revision = 5;
  const request = {
    sessionId: 3,
    projectRevision: state.projectRevision,
    creativeIntakeRevision: 4,
  };
  assert.equal(
    isCreativeIntakeResponseCurrent(request, 3, state),
    false,
  );
});
```

The guard returns true only when workspace session, project revision, and
creative-intake revision all match the captured request.

- [ ] **Step 7: Run the complete Node test file**

Run:

```powershell
node --test prototype/tests/app.test.js
```

Expected: all tests pass.

- [ ] **Step 8: Commit Task 4**

```powershell
git add -- prototype/app.js prototype/tests/app.test.js
git commit -m "feat: persist creative intake state"
```

---

### Task 5: Cross-Layer Recovery and Security Regression

**Files:**

- Modify: `prototype/tests/test_full_flow.py`
- Modify: `prototype/tests/test_db.py`

**Interfaces:**

- Consumes: project metadata persistence, atomic workspace commit, and canonical intake shape.
- Produces: evidence that the new metadata survives real save/reopen flows without schema or secret regressions.

- [ ] **Step 1: Write a failing full-flow recovery test**

Use a temporary database and the existing workspace-commit test helpers:

```python
def test_creative_intake_survives_atomic_commit_and_project_reopen(self):
    session = confirmed_brief_session()
    result = db.commit_workspace(
        {
            "operationId": "save-intake-1",
            "createProject": True,
            "project": {
                "id": "project-intake",
                "name": "雨夜红裙",
                "mode": "text",
                "status": "draft",
                "metadata": {
                    "workspaceBaseVersion": 0,
                    "creativeIntake": session,
                },
            },
            "version": None,
        },
        db_path=self.db_path,
        idempotency_key="save-intake-1",
    )
    reopened = db.get_project(result["project"]["id"], self.db_path)
    self.assertEqual(reopened["metadata"]["creativeIntake"], session)
```

Adapt the call to the exact existing `commit_workspace` signature.

- [ ] **Step 2: Add security and idempotency tests**

Verify:

- replaying the same frozen commit returns the same result;
- reusing the idempotency key with a changed creative session returns the existing idempotency conflict;
- keys named `apiKey`, `authorization`, `token`, and `password` are stripped if injected anywhere below creative-intake metadata;
- `PRAGMA user_version` remains `1`;
- exporting and restoring a logical backup preserves creative-intake metadata because it is project metadata;
- malformed stored intake metadata is returned as sanitized metadata and the frontend—not the database—falls back to an empty intake state.

- [ ] **Step 3: Run the focused tests and verify failures or missing coverage**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_full_flow prototype.tests.test_db -v
```

Expected before completing fixtures: at least the new assertions fail.

- [ ] **Step 4: Make only the minimal fixture or sanitizer changes needed**

Do not add a table or migration. If secret stripping already passes through
`sanitize_metadata`, keep production code unchanged and adjust only the tests.
If recursive metadata sanitization has a demonstrated gap, fix it in
`prototype/db.py` and add the exact regression assertion.

- [ ] **Step 5: Run the focused tests**

Run the command from Step 3.

Expected: all full-flow and database tests pass.

- [ ] **Step 6: Commit Task 5**

Stage only files actually changed:

```powershell
git add -- prototype/tests/test_full_flow.py prototype/tests/test_db.py
git diff --cached --check
git commit -m "test: cover creative intake recovery"
```

If `prototype/db.py` required a real sanitizer fix, include it explicitly in the
same commit.

---

### Task 6: Documentation and Full Verification

**Files:**

- Modify: `docs/architecture.md`
- Modify: `docs/data-model.md`
- Modify: `docs/integration-guide.md`
- Modify: `docs/roadmap.md`

**Interfaces:**

- Documents the exact state and API implemented by Tasks 1–5.

- [ ] **Step 1: Update architecture**

Add the creative-intake domain module and data flow:

```text
统一输入（后续 UI）
  → POST /api/creative-intake/transition
  → creative_intake.py 校验和状态转换
  → 前端接收规范化会话
  → POST /api/workspace/commit
  → projects.metadata_json.creativeIntake
```

State explicitly that this phase does not yet implement the unified homepage,
AI director calls, multi-image interaction, model webpage research, or
model-adapted decomposition UI.

- [ ] **Step 2: Update the data model**

Document:

- `schemaVersion`;
- every top-level field;
- stage enum;
- source enum;
- image-reference rules;
- lock semantics;
- revision semantics;
- invalidation rules;
- why no SQLite migration is required.

- [ ] **Step 3: Add API request and response examples**

Include one legal `replace_inputs` call, one `confirm_brief` call, and one
`locked_item_conflict` error. Examples must use the exact property names from
`creative_intake.py`.

- [ ] **Step 4: Update roadmap conservatively**

Mark only these items complete:

- versioned creative-intake state;
- lock/source/conflict schema;
- server-authoritative transition API;
- atomic project-metadata persistence and recovery.

Leave homepage replacement, director prompting, multi-image UI, model research,
LoRA records, and thirteen-block preview in “下一阶段”.

- [ ] **Step 5: Run generated-content and whitespace checks**

Run:

```powershell
git diff --check
rg -n "T[B]D|T[O]DO|implement l[a]ter|fill in d[e]tails" prototype/creative_intake.py docs/architecture.md docs/data-model.md docs/integration-guide.md docs/roadmap.md
```

Expected: `git diff --check` exits `0`; `rg` returns no placeholder matches.

- [ ] **Step 6: Run the complete verification suite**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s prototype\tests -p "test_*.py"
node --test prototype\tests\app.test.js
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s scripts\tests -p "test_*.py"
& .\AnimaDex\.venv\Scripts\python.exe scripts\build_random_wordlists.py --check
```

Expected: every command exits `0`, with zero failed tests and no generated
wordlist drift.

- [ ] **Step 7: Review scope and committed files**

Run:

```powershell
git status --short
git diff --stat HEAD
git log --oneline -6
```

Confirm:

- no local database, image, model, API key, log, or PID is staged;
- unrelated pre-existing modifications remain untouched;
- no homepage, model research, or model-adaptation behavior is claimed complete.

- [ ] **Step 8: Commit documentation**

```powershell
git add -- docs/architecture.md docs/data-model.md docs/integration-guide.md docs/roadmap.md
git commit -m "docs: document creative intake foundation"
```

---

## Subsequent Plans

After this foundation is implemented and reviewed, create separate detailed plans
in this order:

1. `creative-director-unified-homepage` — unified text/single-image UI and director API orchestration.
2. `creative-director-multi-image-provenance` — per-image element selection and multi-image source tracking.
3. `model-source-research-profiles` — original-link fetch, evidence grading, review, and versioned model profiles.
4. `model-adapted-decomposition-preview` — model selection, semantic/model layers, thirteen-block approval.
5. `lora-lite-profiles` — link, version, trigger, weight, notes, and simple conflicts.
6. `creative-director-release-validation` — full restore, fixed scenarios, and real Anima output acceptance.

Each subsequent plan must consume the stable `creativeIntake` schema and
transition endpoint rather than introducing a second source of truth.

## Spec Coverage Audit

This first implementation plan deliberately covers the shared foundation from
spec sections 6, 9, 10, 11, 12, and the persistence/recovery portions of section
13. The remaining confirmed requirements are assigned without overlap:

- Spec sections 4, 5, and the user-facing parts of 10 → unified-homepage plan.
- Spec section 4.1 multi-image combination → multi-image-provenance plan.
- Spec section 7.1 and 7.2 → model-source-research plan.
- Spec section 8 → model-adapted-decomposition plan.
- Spec section 7.3 → LoRA-lite plan.
- End-to-end quality cases in section 13 → release-validation plan.

No requirement is silently dropped. This plan must not claim those subsequent
features are implemented when only their shared data contract exists.
