# Model-Adapted Thirteen-Block Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn one confirmed creative brief and the exact activated model-profile version/hash into a reviewable, evidence-backed thirteen-block preview, then hand the confirmed result to Recipe v1 without changing locked semantic facts.

**Architecture:** Add an independent Phase B decomposition boundary after model activation. The server resolves the exact activated immutable profile, constructs a restricted approved-rule snapshot, asks an injected text provider for semantic and adaptation layers, validates all thirteen blocks and locked facts, and commits the draft through the existing authoritative creative-intake transition. The browser presents semantic facts separately from model adaptations, persists every block decision, rejects stale asynchronous results, and hands the confirmed decomposition to the existing workbench and Recipe resolver with the exact profile version and content hash.

**Tech Stack:** Python 3 standard library, existing `creative_intake.py`, `model_profile_store.py`, Recipe v1, `ThreadingHTTPServer`, OpenAI-compatible injected text provider, vanilla JavaScript/CSS/HTML, `unittest`, Node `node:test`.

## Global Constraints

- This is independent Phase B. It consumes completed Phase A model research but must not fetch model pages, revise/activate profiles, implement LoRA research, execute image models, or validate local checkpoints.
- The only legal input stage for a new preview is `model_selected`, with a confirmed brief and `selectedModelProfileId`.
- The requested `profileVersionId` and `profileContentSha256` must identify the currently activated immutable version of `selectedModelProfileId`; the server never trusts a client-supplied profile body.
- The decomposition contains exactly the canonical `recipe.BLOCK_IDS`, in that order: `identity`, `appearance`, `clothing`, `expression`, `action`, `interaction`, `scene`, `camera`, `lighting`, `style`, `effects`, `quality`, `negative`.
- The semantic layer is derived only from the confirmed brief and its provenance. The adaptation layer may change wording, ordering, syntax, quality terms, trigger terms, negative terms, and parameters, but must not change semantic facts.
- Only claims whose immutable version decision is `approved` may become adaptation rules. Every applied rule keeps its claim ID and evidence snapshot IDs. `proposed` and `rejected` claims may appear only as warnings and are never applied.
- Locked brief facts are immutable. A provider response that removes, contradicts, or relocates a locked fact is rejected before any canonical state write.
- Every asynchronous result is guarded by workspace session, project revision, creative-intake revision, selected profile ID, profile version ID, and profile content SHA-256.
- Reopening the brief or selecting another model invalidates the draft/confirmed decomposition and Recipe handoff; late results cannot restore stale content.
- Block review supports accept, semantic-preserving English edit, and return-for-regeneration. Confirmation requires all thirteen blocks approved and no open conflict.
- Automated tests use injected fake providers and stored fixtures only. They never call a real external API or live webpage.
- Persistence continues through the single atomic `POST /api/workspace/commit` path with stable `operationId` and `Idempotency-Key`.
- No API key, `.env`, local SQLite database, image, model file, log, PID, or virtual environment is committed.

---

## File Map

- Create `prototype/model_adapted_decomposition.py`: strict Phase B request, profile-rule projection, provider prompt, response normalization, semantic-lock verification, and preview orchestration.
- Create `prototype/prompts/text/model_adapted_decomposition.md`: provider instructions and exact JSON output contract.
- Create `prototype/tests/test_model_adapted_decomposition.py`: pure domain and fake-provider tests.
- Create `prototype/tests/fixtures/model_adapted_decomposition/approved_profile.json`: immutable activated-profile fixture with approved, proposed, and rejected claims.
- Modify `prototype/model_profile_store.py`: return one activated version together with its immutable claims and snapshots.
- Modify `prototype/creative_intake.py`: version the decomposition root with exact brief/profile lineage and per-block rule/evidence references.
- Modify `prototype/server.py`: add the preview endpoint and route it through the injected text provider and authoritative transition.
- Modify `prototype/tests/test_model_profile_store.py`, `prototype/tests/test_creative_intake.py`, and `prototype/tests/test_server.py`: persistence, schema, route, stale/hash, and HTTP error coverage.
- Modify `prototype/app.js`: preview request guards, reducer state, block review actions, confirmed handoff, and exact Recipe fields.
- Modify `prototype/index.html` and `prototype/styles.css`: two-layer thirteen-card preview, evidence disclosure, warnings, review controls, and responsive states.
- Modify `prototype/tests/app.test.js`: frontend orchestration, per-block approval, stale result, recovery, and Recipe handoff tests.
- Modify `prototype/recipe.py` and `prototype/tests/test_recipe.py`: exact immutable profile-version/hash in Recipe v1.
- Modify `docs/architecture.md`, `docs/data-model.md`, `docs/integration-guide.md`, and `docs/roadmap.md`: implemented Phase B boundary, contract, API, recovery, and status.

---

### Task 1: Activated Profile Adaptation Snapshot

**Files:**

- Modify: `prototype/model_profile_store.py`
- Test: `prototype/tests/test_model_profile_store.py`

**Interfaces:**

- Consumes: `get_profile_version(version_id, db_path=...)`, immutable profile-version rows, research-run claims, and evidence snapshots.
- Produces:

```python
get_activated_profile_snapshot(
    profile_id: str,
    version_id: str,
    content_sha256: str,
    *,
    db_path: Path | str | None = None,
) -> dict
```

The returned shape is:

```python
{
    "profileId": "profile-anima",
    "profileVersionId": "profile-version-7",
    "profileContentSha256": "a" * 64,
    "profile": {"id": "profile-anima", "displayName": "Anima"},
    "approvedRules": [
        {
            "claimId": "claim-positive-prefix",
            "fieldPath": "prompting.requiredPositivePrefix",
            "value": "masterpiece",
            "evidenceClass": "original_source",
            "evidenceRefs": ["snapshot-official"],
            "rationale": "The original model page requires this prefix.",
        }
    ],
    "warnings": [
        {
            "claimId": "claim-community-cfg",
            "decision": "proposed",
            "message": "Unapproved model claim was not applied.",
        }
    ],
}
```

- [ ] **Step 1: Write failing exact-version snapshot tests**

Add tests that create one completed run, an approved/rejected/proposed decision set, review and activate the version, then assert:

```python
snapshot = model_profile_store.get_activated_profile_snapshot(
    active["profileId"],
    active["versionId"],
    active["contentSha256"],
    db_path=self.db_path,
)
self.assertEqual(snapshot["profileVersionId"], active["versionId"])
self.assertEqual(snapshot["profileContentSha256"], active["contentSha256"])
self.assertEqual(
    [item["claimId"] for item in snapshot["approvedRules"]],
    ["claim-approved"],
)
self.assertEqual(
    {item["decision"] for item in snapshot["warnings"]},
    {"proposed", "rejected"},
)
self.assertEqual(
    snapshot["approvedRules"][0]["evidenceRefs"],
    ["snapshot-official"],
)
```

Add separate assertions for `unknown_version`, `profile_version_mismatch`,
`profile_not_activated`, and `profile_hash_mismatch`. Superseding an old
version must make it unusable for a new preview even though history remains
readable through `get_profile_version`.

- [ ] **Step 2: Run the focused test and verify failure**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_model_profile_store -v
```

Expected: FAIL because `get_activated_profile_snapshot` does not exist.

- [ ] **Step 3: Implement the fail-closed snapshot resolver**

Within one read transaction:

1. validate all three identifiers;
2. load the exact version;
3. require matching `profile_id`, `lifecycle_status == "activated"`, and
   canonical `profile_json` hash equal to both stored and requested hashes;
4. load claims and snapshots from the version's `research_run_id`;
5. include a rule only when `claim_decisions_json[claim_id] == "approved"`;
6. require every approved rule's evidence reference to name a stored snapshot;
7. sort rules and warnings by `claimId`.

Raise `ProfileStoreError` with the exact codes tested above. Do not return
draft, reviewed, superseded, proposed, or rejected content as applicable rules.

- [ ] **Step 4: Run the focused tests**

Run the Step 2 command.

Expected: all model-profile-store tests pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add -- prototype/model_profile_store.py prototype/tests/test_model_profile_store.py
git diff --cached --check
git commit -m "feat: resolve activated model adaptation snapshots"
```

---

### Task 2: Versioned Decomposition Contract and State Invariants

**Files:**

- Modify: `prototype/creative_intake.py`
- Modify: `prototype/tests/test_creative_intake.py`

**Interfaces:**

- Consumes: existing `set_decomposition_draft`, `confirm_decomposition`,
  `select_model`, and `reopen_brief` transitions.
- Produces this canonical decomposition root:

```json
{
  "status": "draft",
  "briefContentSha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "profileVersionId": "profile-version-7",
  "profileContentSha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "blocks": [
    {
      "id": "identity",
      "category": "人物身份",
      "zh": "一名成年女性侦探",
      "en": "1 adult woman, detective",
      "source": {"type": "user", "refId": null},
      "locked": true,
      "approved": false,
      "reason": "将人物身份提前，适配标签式英语提示结构。",
      "risks": [],
      "ruleRefs": [
        {
          "claimId": "claim-prompt-language",
          "fieldPath": "prompting.language",
          "evidenceRefs": ["snapshot-official"]
        }
      ]
    }
  ]
}
```

- [ ] **Step 1: Write failing schema and invariant tests**

Add a fixture with all thirteen `recipe.BLOCK_IDS`. Assert normalization:

- requires exactly thirteen blocks in canonical order;
- requires `briefContentSha256` to equal the canonical SHA-256 of the current
  confirmed brief;
- validates a lowercase 64-character `profileContentSha256`;
- requires non-empty `profileVersionId`;
- rejects duplicate/unknown `ruleRefs.claimId`;
- validates every `evidenceRefs` item as a safe identifier;
- preserves an empty `ruleRefs` list for generic fallback wording;
- requires locked semantic blocks to retain their brief source;
- allows `approved` to change while status is `draft`;
- requires all thirteen `approved == true` before confirmation.

Add invalidation tests proving `select_model` and `reopen_brief` clear the
entire versioned decomposition and set `recipeStatus = "stale"`.

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_creative_intake -v
```

Expected: new lineage and rule-reference assertions fail.

- [ ] **Step 3: Extend `_decomposition` without creating a second state model**

Import `BLOCK_IDS` from `recipe`. Extend the root allowlist to
`status`, `briefContentSha256`, `profileVersionId`, `profileContentSha256`, and
`blocks`. Extend each block allowlist with `ruleRefs`.

Normalize a rule reference to:

```python
{
    "claimId": _identifier(rule.get("claimId"), f"{path}.claimId"),
    "fieldPath": _text(
        rule.get("fieldPath"), f"{path}.fieldPath",
        maximum=512, allow_empty=False,
    ),
    "evidenceRefs": [
        _identifier(ref, f"{path}.evidenceRefs[{index}]")
        for index, ref in enumerate(raw_refs)
    ],
}
```

Require `tuple(block["id"] for block in blocks) == BLOCK_IDS`. Add
`canonical_brief_sha256(brief)` using sorted, compact, UTF-8 canonical JSON.
In `_set_decomposition_draft`, require the root brief hash to equal the current
confirmed brief hash and require the selected model profile ID to remain
present. The endpoint in Task 4 is responsible for proving the exact profile
version/hash; the transition is responsible for preserving it.

- [ ] **Step 4: Run the focused tests**

Run the Step 2 command.

Expected: all creative-intake tests pass.

- [ ] **Step 5: Commit Task 2**

```powershell
git add -- prototype/creative_intake.py prototype/tests/test_creative_intake.py
git diff --cached --check
git commit -m "feat: version model adapted decomposition state"
```

---

### Task 3: Semantic and Adaptation Domain Engine

**Files:**

- Create: `prototype/model_adapted_decomposition.py`
- Create: `prototype/prompts/text/model_adapted_decomposition.md`
- Create: `prototype/tests/test_model_adapted_decomposition.py`
- Create: `prototype/tests/fixtures/model_adapted_decomposition/approved_profile.json`

**Interfaces:**

- Consumes:

```python
generate_model_adapted_decomposition(
    *,
    intake: Mapping[str, object],
    profile_snapshot: Mapping[str, object],
    provider: Callable[[list[dict]], object],
) -> dict
```

- Produces a normalized `status == "draft"` decomposition accepted by Task 2.
- Exposes:

```python
class DecompositionError(ValueError):
    code: str

build_decomposition_messages(
    brief: Mapping[str, object],
    approved_rules: Sequence[Mapping[str, object]],
    warnings: Sequence[Mapping[str, object]],
) -> list[dict]

normalize_decomposition_output(
    value: object,
    *,
    intake: Mapping[str, object],
    profile_snapshot: Mapping[str, object],
) -> dict
```

- [ ] **Step 1: Write failing deterministic domain tests**

Use a fake provider that records its messages and returns strict JSON. Cover:

1. exactly thirteen canonical blocks and categories;
2. `zh` reproduces confirmed brief semantics without model terminology;
3. `en`, `reason`, and `ruleRefs` form the adaptation layer;
4. only approved rules are present in provider messages;
5. each returned rule reference must equal an approved claim's exact field path
   and evidence references;
6. generic fallback is legal with `ruleRefs: []` and a visible risk;
7. unknown, proposed, rejected, fabricated, or mismatched rule/evidence refs
   cause `DecompositionError("unapproved_rule")`;
8. missing, extra, duplicated, or reordered blocks cause
   `DecompositionError("invalid_blocks")`;
9. invalid JSON receives one repair call, and a second failure preserves the
   previous state by raising `invalid_provider_output`;
10. provider exceptions are wrapped as `provider_failed`;
11. brief/profile lineage is copied from the trusted arguments rather than the
    provider response.

Use a locked-fact fixture such as:

```python
{
    "id": "brief-action",
    "category": "action",
    "text": "右手握伞，左脚踏上台阶",
    "source": {"type": "user", "refId": None},
    "locked": True,
}
```

Assert responses that change right to left, remove the umbrella, or move the
fact into `scene` fail with `locked_fact_changed`.

- [ ] **Step 2: Run the new tests and verify failure**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_model_adapted_decomposition -v
```

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Write the provider instruction file**

The instruction must explicitly define:

```text
semantic.zh = confirmed visual meaning only
adaptation.en = target-model wording for the same meaning
reason/ruleRefs = why approved model rules changed expression
risks = unsupported capability or generic fallback warnings
```

It must prohibit adding people, changing count/age/action/clothing/environment,
inventing artist names, treating warnings as rules, or returning prose outside
the JSON object. Include the thirteen exact IDs and require all fields shown in
Task 2.

- [ ] **Step 4: Implement strict normalization and lock verification**

Build a server-owned map of approved rules by `claimId`; replace provider
`fieldPath` and `evidenceRefs` with exact trusted values after equality checks.
Use normalized Unicode text and conservative token containment to verify every
locked brief item remains in its category's `zh`. The provider also returns
`semanticItemIds` per block for validation only; strip that helper field before
returning the canonical decomposition.

For every locked brief item:

- its ID must occur in exactly one block's `semanticItemIds`;
- the block category must equal the brief item's category-to-block mapping;
- its normalized Chinese fact text must be preserved verbatim in that block's
  `zh`.

This deliberately chooses safety over paraphrase for locked facts. Unlocked
AI additions may be summarized.

- [ ] **Step 5: Implement one bounded repair attempt**

On malformed JSON/schema only, append:

```json
{
  "task": "repair_model_adapted_decomposition_json",
  "errorCode": "invalid_provider_output",
  "instruction": "Return one corrected JSON object; do not change semantic facts."
}
```

Do not retry provider timeouts, cancellations, profile mismatches, or locked
fact violations.

- [ ] **Step 6: Run the focused tests**

Run the Step 2 command.

Expected: all domain tests pass without network access.

- [ ] **Step 7: Commit Task 3**

```powershell
git add -- prototype/model_adapted_decomposition.py prototype/prompts/text/model_adapted_decomposition.md prototype/tests/test_model_adapted_decomposition.py prototype/tests/fixtures/model_adapted_decomposition/approved_profile.json
git diff --cached --check
git commit -m "feat: generate evidence backed decomposition previews"
```

---

### Task 4: Preview API and Authoritative Draft Transition

**Files:**

- Modify: `prototype/server.py`
- Modify: `prototype/tests/test_server.py`

**Interfaces:**

- Adds:

```http
POST /api/creative-intake/decomposition-preview
Content-Type: application/json

{
  "current": {
    "schemaVersion": 1,
    "revision": 8,
    "stage": "model_selected",
    "inputs": {"text": "雨夜侦探", "images": []},
    "directions": [{
      "id": "direction-main",
      "label": "雨夜追踪",
      "summary": "侦探在雨夜街道追踪线索"
    }],
    "selectedDirectionId": "direction-main",
    "brief": {
      "status": "confirmed",
      "summary": "一名成年女性侦探在雨夜街道追踪线索",
      "items": [{
        "id": "brief-identity",
        "category": "identity",
        "text": "一名成年女性侦探",
        "source": {"type": "user", "refId": null},
        "locked": true
      }],
      "aiAdditions": [],
      "openQuestions": []
    },
    "selectedModelProfileId": "profile-anima",
    "decomposition": null,
    "recipeStatus": "stale",
    "conflicts": []
  },
  "profileVersionId": "profile-version-7",
  "profileContentSha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

- Success:

```json
{
  "item": {"stage": "decomposition_draft", "recipeStatus": "stale"},
  "profile": {
    "profileId": "profile-anima",
    "profileVersionId": "profile-version-7",
    "profileContentSha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "warnings": []
}
```

- [ ] **Step 1: Write failing processor tests**

Factor:

```python
def process_model_adapted_decomposition_request(
    payload: dict,
    *,
    provider,
    db_path,
) -> dict
```

Test that it:

- rejects unknown fields;
- requires `current.stage == "model_selected"`;
- requires a confirmed brief and matching selected profile ID;
- loads the exact activated snapshot from Task 1;
- rejects a stale/superseded version, wrong profile, or wrong hash before the
  provider runs;
- passes only the confirmed brief and approved-rule projection to the domain
  engine;
- applies `set_decomposition_draft` through
  `creative_intake.apply_creative_intake_transition`;
- returns warnings without applying them;
- never mutates the request object.

- [ ] **Step 2: Add failing HTTP tests**

Install a fake provider on the test server and assert:

```python
status, body = self.post_json(
    "/api/creative-intake/decomposition-preview",
    legal_payload,
)
self.assertEqual(status, 200)
self.assertEqual(body["item"]["stage"], "decomposition_draft")
self.assertEqual(len(body["item"]["decomposition"]["blocks"]), 13)
```

Map errors exactly:

| HTTP | code | Meaning |
| --- | --- | --- |
| `400` | `invalid_request` | unknown/malformed client field |
| `409` | `stale_intake` | stage/revision no longer permits preview |
| `409` | `profile_version_mismatch` | exact version is not selected profile |
| `409` | `profile_not_activated` | version is not currently activated |
| `409` | `profile_hash_mismatch` | requested/stored/canonical hash differs |
| `422` | `locked_fact_changed` | provider changed a locked semantic fact |
| `502` | `invalid_provider_output` | output and one repair both invalid |
| `502` | `provider_failed` | provider failed without canonical write |

- [ ] **Step 3: Run focused tests and verify failure**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_server -v
```

Expected: new route tests fail with 404 or missing processor.

- [ ] **Step 4: Implement the processor and route**

Add the POST branch beside `/api/creative-director`. Use the same configured
text-provider boundary, timeout/cancellation behavior, and JSON response
helpers. Do not accept a provider name, model-profile body, claims, snapshots,
brief override, or API key in the request.

The processor order is mandatory:

```python
current = creative_intake.normalize_creative_intake(payload["current"])
require_model_selected(current)
snapshot = model_profile_store.get_activated_profile_snapshot(...)
draft = generate_model_adapted_decomposition(
    intake=current,
    profile_snapshot=snapshot,
    provider=provider,
)
item = creative_intake.apply_creative_intake_transition(
    current,
    {"type": "set_decomposition_draft", "decomposition": draft},
)
```

- [ ] **Step 5: Run domain, intake, store, and server tests**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_model_adapted_decomposition prototype.tests.test_creative_intake prototype.tests.test_model_profile_store prototype.tests.test_server -v
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 4**

```powershell
git add -- prototype/server.py prototype/tests/test_server.py
git diff --cached --check
git commit -m "feat: expose model adapted decomposition preview api"
```

---

### Task 5: Frontend Request Guards and Per-Block Review State

**Files:**

- Modify: `prototype/app.js`
- Modify: `prototype/tests/app.test.js`

**Interfaces:**

- Produces:

```javascript
decompositionPreviewGuard(state, sessionId) -> {
  sessionId,
  projectRevision,
  creativeIntakeRevision,
  selectedModelProfileId,
  profileVersionId,
  profileContentSha256,
}
isDecompositionPreviewCurrent(request, sessionId, state) -> boolean
buildDecompositionBlockReviewAction(intake, blockId, patch) -> transitionAction
buildConfirmedDecompositionWorkbench(intake) -> workbenchProjection
```

- [ ] **Step 1: Write failing guard tests**

Assert `isDecompositionPreviewCurrent` becomes false when any guard field
changes, including only profile version/hash while profile ID remains the same.
Assert it remains true after unrelated UI state changes.

- [ ] **Step 2: Write failing review-action tests**

For `buildDecompositionBlockReviewAction`:

- accepting sets only the named block's `approved = true`;
- returning sets `approved = false` and leaves canonical semantic text intact;
- an English edit changes only `en`, `reason`, and `approved`;
- attempts to edit `zh`, `source`, `locked`, lineage, ID, category, or
  `ruleRefs` throw;
- the returned action is a full `set_decomposition_draft` action so the server
  validates and persists one canonical state;
- confirm is disabled until all thirteen blocks are approved.

- [ ] **Step 3: Run focused Node tests and verify failure**

Run:

```powershell
C:\Users\13688\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe --test --test-name-pattern="decomposition preview|decomposition block review" prototype/tests/app.test.js
```

Expected: FAIL because helpers and reducer state do not exist.

- [ ] **Step 4: Add preview lifecycle state**

Add:

```javascript
decompositionPreview: {
  status: "idle",
  error: "",
  warnings: [],
  returnedBlockIds: [],
},
```

Support `DECOMPOSITION_PREVIEW_STARTED`, `SUCCEEDED`, `FAILED`,
`BLOCK_RETURNED`, and `CLEARED`. Keep the canonical draft exclusively in
`creativeIntake.decomposition`; UI state contains no duplicate block bodies.

- [ ] **Step 5: Implement guarded API orchestration**

`generateDecompositionPreview()` must:

1. resolve the selected catalog item and require exact version/hash;
2. capture the full guard;
3. POST only `current`, `profileVersionId`, and `profileContentSha256`;
4. discard the response unless the full guard remains current;
5. persist the accepted canonical intake revision before displaying success;
6. retain the last valid canonical state on errors;
7. expose explicit retry without silently switching providers.

- [ ] **Step 6: Persist block decisions through existing transitions**

Accept/edit/return calls `POST /api/creative-intake/transition` with the full
draft action and then the existing atomic metadata save. Confirmation calls
`confirm_decomposition`; no client-only approval can unlock handoff.

- [ ] **Step 7: Run the complete Node test file**

Run:

```powershell
C:\Users\13688\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe --test prototype/tests/app.test.js
```

Expected: all tests pass.

- [ ] **Step 8: Commit Task 5**

```powershell
git add -- prototype/app.js prototype/tests/app.test.js
git diff --cached --check
git commit -m "feat: orchestrate decomposition preview review"
```

---

### Task 6: Two-Layer Thirteen-Card Preview UI

**Files:**

- Modify: `prototype/index.html`
- Modify: `prototype/styles.css`
- Modify: `prototype/app.js`
- Modify: `prototype/tests/app.test.js`

**Interfaces:**

- Consumes canonical `creativeIntake.decomposition`.
- Produces accessible controls with these stable IDs/data attributes:

```text
#decompositionPreviewPanel
#generateDecompositionPreview
#decompositionProfileLineage
#decompositionWarnings
#confirmDecomposition
[data-decomposition-block]
[data-approve-decomposition]
[data-return-decomposition]
[data-edit-adaptation]
[data-rule-evidence]
```

- [ ] **Step 1: Write failing render-model tests**

`buildDecompositionPreviewRenderModel(state)` must return:

```javascript
{
  visible: true,
  canGenerate: true,
  canConfirm: false,
  lineage: {
    profileId: "profile-anima",
    profileVersionId: "profile-version-7",
    profileContentSha256: "aaaa…aaaa",
  },
  blocks: [{
    id: "identity",
    semantic: {
      zh: "一名成年女性侦探",
      sourceLabel: "用户明确要求",
      locked: true,
    },
    adaptation: {
      en: "1 adult woman, detective",
      reason: "人物身份前置以适配标签式提示结构。",
      ruleRefs: [{
        claimId: "claim-language",
        fieldPath: "prompting.language",
        evidenceRefs: ["snapshot-official"],
      }],
    },
    approved: false,
    risks: [],
  }],
}
```

Assert no raw provider message, snapshot body, local path, or API key enters
the model.

- [ ] **Step 2: Add failing static DOM tests**

Assert all stable IDs and data attributes exist, all action buttons have
`type="button"`, lineage and warnings use live/status semantics, English edit
has an associated label, and evidence details use a keyboard-accessible
`<details>` disclosure.

- [ ] **Step 3: Run focused Node tests and verify failure**

Run:

```powershell
C:\Users\13688\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe --test --test-name-pattern="decomposition preview render|decomposition preview DOM" prototype/tests/app.test.js
```

Expected: FAIL because the panel and render model do not exist.

- [ ] **Step 4: Build the panel**

Render one card for every canonical block, even when its content is empty.
Each card visibly separates:

- `语义层（已确认需求）`: Chinese fact, source, lock;
- `模型适配层`: English expression, reason, approved rules/evidence;
- `风险`: unsupported capability, generic fallback, or unverified warning;
- `审阅`: accept, edit English expression, or return.

Display the full exact version ID and SHA-256 in accessible text; visual
truncation may use CSS only. Never label supplemental/inferred evidence as
official.

- [ ] **Step 5: Implement edit and return behavior**

English edits open an inline textarea and require a non-empty edit reason.
Saving sets that block unapproved until the user explicitly accepts it.
Returning records the block ID and presents one explicit “重新生成退回块”
action; regeneration still sends the complete confirmed brief and exact
profile lineage, and the server must return all thirteen blocks for invariant
validation.

- [ ] **Step 6: Add responsive and failure styles**

At narrow widths stack semantic/adaptation columns; never hide lineage,
warnings, source, or lock. Distinguish loading, failed, returned, accepted, and
locked states without relying on color alone.

- [ ] **Step 7: Run Node tests**

Run the complete command from Task 5 Step 7.

Expected: all Node tests pass.

- [ ] **Step 8: Commit Task 6**

```powershell
git add -- prototype/index.html prototype/styles.css prototype/app.js prototype/tests/app.test.js
git diff --cached --check
git commit -m "feat: add thirteen block adaptation review ui"
```

---

### Task 7: Exact-Hash Recipe and Workbench Handoff

**Files:**

- Modify: `prototype/recipe.py`
- Modify: `prototype/server.py`
- Modify: `prototype/app.js`
- Modify: `prototype/tests/test_recipe.py`
- Modify: `prototype/tests/test_server.py`
- Modify: `prototype/tests/app.test.js`

**Interfaces:**

- Extends Recipe v1 `model` with:

```json
{
  "profileId": "profile-anima",
  "profileVersionId": "profile-version-7",
  "profileContentSha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "displayName": "Anima",
  "versionName": "1.1",
  "versionId": 11,
  "baseModel": "SDXL",
  "checkpoint": {
    "filename": "",
    "sha256": null,
    "verificationStatus": "missing"
  }
}
```

- [ ] **Step 1: Write failing Recipe lineage tests**

Assert `normalize_recipe` requires both new fields for a researched immutable
profile, lowercases and validates SHA-256, and includes both fields in
`canonical_recipe_json` and `recipe_hash`. A one-character hash change must
change `recipe_hash`.

Keep compatibility explicit: bundled profiles without immutable research
versions normalize to:

```json
{
  "profileVersionId": null,
  "profileContentSha256": null
}
```

They may use generic rules, but a Phase B confirmed decomposition always
requires non-null exact lineage.

- [ ] **Step 2: Write failing handoff tests**

Given `decomposition_confirmed`, assert
`buildConfirmedDecompositionWorkbench`:

- maps all thirteen canonical IDs through
  `RECIPE_TO_WORKBENCH_BLOCK`/the existing JS equivalent;
- copies `zh`, `en`, source, and lock without loss;
- selects the exact profile ID;
- carries exact version/hash into `currentRecipeResolvePayload`;
- puts rule/evidence lineage into `sourceRefs`;
- refuses handoff if stage, approval, current confirmed-brief hash, selected profile,
  version, or hash no longer matches.

- [ ] **Step 3: Run focused tests and verify failure**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_recipe prototype.tests.test_server -v
C:\Users\13688\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe --test --test-name-pattern="exact profile recipe|confirmed decomposition handoff" prototype/tests/app.test.js
```

Expected: new exact-lineage assertions fail.

- [ ] **Step 4: Extend Recipe model normalization**

Add `profileVersionId` and `profileContentSha256` to `_normalize_model`.
Validate either both are null or both are present; a present hash must match
`^[0-9a-f]{64}$`. Do not overload checkpoint SHA-256: checkpoint identity and
profile-content identity remain separate fields.

- [ ] **Step 5: Make `/api/recipe/resolve` resolve the exact profile**

Extend the request allowlist with `profileVersionId` and
`profileContentSha256`. When present, call
`get_activated_profile_snapshot` and build model/default parameters from that
trusted profile body. When absent, retain the current bundled-profile path.
Never fall back to a different active version after an exact lookup fails.

- [ ] **Step 6: Apply the confirmed draft to the workbench**

After `confirm_decomposition` is durably saved:

1. verify full handoff lineage;
2. replace workbench blocks in one reducer action;
3. replace model defaults only from the exact selected profile;
4. set source refs for brief item IDs, image IDs, claim IDs, and evidence IDs;
5. mark the workspace changed once;
6. enter the existing structured workbench.

Do not regenerate, translate, or merge an old workbench prompt during this
handoff.

- [ ] **Step 7: Run Python and Node tests**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_recipe prototype.tests.test_server -v
C:\Users\13688\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe --test prototype/tests/app.test.js
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit Task 7**

```powershell
git add -- prototype/recipe.py prototype/server.py prototype/app.js prototype/tests/test_recipe.py prototype/tests/test_server.py prototype/tests/app.test.js
git diff --cached --check
git commit -m "feat: hand off exact profile decomposition recipe"
```

---

### Task 8: Recovery, Fixed Scenarios, Documentation, and Full Verification

**Files:**

- Modify: `prototype/tests/test_full_flow.py`
- Modify: `docs/architecture.md`
- Modify: `docs/data-model.md`
- Modify: `docs/integration-guide.md`
- Modify: `docs/roadmap.md`

**Interfaces:**

- Consumes all Phase B behavior from Tasks 1–7.
- Produces recovery and acceptance evidence plus current project documentation.

- [ ] **Step 1: Write a failing atomic recovery test**

Create a confirmed thirteen-block decomposition with exact lineage, save it
through the real workspace commit, reopen the project, and assert stage,
approvals, rule/evidence refs, profile version/hash, and Recipe hash survive
unchanged.

Then reopen the brief and save again. After another reopen assert:

```python
self.assertEqual(item["creativeIntake"]["stage"], "brief_draft")
self.assertIsNone(item["creativeIntake"]["decomposition"])
self.assertEqual(item["creativeIntake"]["recipeStatus"], "stale")
```

No historical Recipe version is deleted, but it must not rehydrate as current.

- [ ] **Step 2: Add fixed quality scenarios**

Use fake-provider fixtures for:

1. action-only brief;
2. environment-and-emotion-only brief;
3. clothing-only brief;
4. text plus one image borrowing only action;
5. multiple images borrowing action, clothing, and environment separately;
6. locked-fact conflict;
7. complete activated profile;
8. pending-verification profile with generic fallback;
9. profile switch while generation is in flight;
10. provider failure after a previous valid draft.

For each scenario assert intent fidelity, source accuracy, locked-fact
preservation, approved-rules-only adaptation, and recoverability. The test
fixtures must not access network, DNS, browser state, or real provider keys.

- [ ] **Step 3: Run recovery tests and verify failures**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_full_flow -v
```

Expected before completing integration fixtures: at least one new assertion
fails.

- [ ] **Step 4: Make minimal integration corrections**

Correct only demonstrated save/hydration/source-ref gaps. Do not introduce a
new SQLite table: canonical intake remains project metadata and completed
Recipe remains prompt-version metadata.

- [ ] **Step 5: Update architecture and data model**

Document:

```text
confirmed creativeIntake brief
  → exact activated profile version/hash
  → approved-rule snapshot
  → model_adapted_decomposition.py
  → authoritative set_decomposition_draft transition
  → per-block review and confirm_decomposition
  → exact-hash Recipe v1 handoff
```

Document every new decomposition root/block field, semantic/adaptation
boundary, evidence reference, stale guard, invalidation rule, and bundled
profile compatibility behavior.

- [ ] **Step 6: Update integration guide and roadmap**

Add exact request/response/error examples for
`POST /api/creative-intake/decomposition-preview` and exact-lineage
`POST /api/recipe/resolve`. Mark only model-adapted thirteen-block preview and
confirmed handoff complete. Keep LoRA-lite, real model execution, and real
Anima visual acceptance pending.

- [ ] **Step 7: Run placeholder, Unicode, and whitespace checks**

Run:

```powershell
git diff --check
rg -n "T[B]D|T[O]DO|implement l[a]ter|fill in d[e]tails|Similar to Task" docs/superpowers/plans/2026-07-26-model-adapted-thirteen-block-preview.md prototype/model_adapted_decomposition.py prototype/prompts/text/model_adapted_decomposition.md docs/architecture.md docs/data-model.md docs/integration-guide.md docs/roadmap.md
rg -n "锟|�" prototype docs
```

Expected: no placeholder or new mojibake match; `git diff --check` exits `0`.

- [ ] **Step 8: Run the complete verification suite**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s prototype\tests -p "test_*.py"
C:\Users\13688\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe --test prototype\tests\app.test.js
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s scripts\tests -p "test_*.py"
& .\AnimaDex\.venv\Scripts\python.exe scripts\build_random_wordlists.py --check
```

Expected: every command exits `0`, zero failed tests, and no generated
wordlist drift.

- [ ] **Step 9: Review scope and committed files**

Run:

```powershell
git status --short
git diff --stat HEAD
git log --oneline -10
```

Confirm no real API call, webpage fetch, LoRA research, image generation,
checkpoint validation, secret, database, image, model, log, or PID entered the
change.

- [ ] **Step 10: Commit Task 8**

```powershell
git add -- prototype/tests/test_full_flow.py docs/architecture.md docs/data-model.md docs/integration-guide.md docs/roadmap.md
git diff --cached --check
git commit -m "docs: complete model adapted decomposition phase"
```

---

## Spec Coverage Audit

- Spec §8 exact thirteen blocks: Tasks 2, 3, and 6.
- Spec §8 Chinese meaning, adapted English, source, lock, reason, and risks:
  Tasks 2, 3, and 6.
- Spec §8 per-block accept/edit/return and all-approved gate: Tasks 5 and 6.
- Spec §8.1 semantic/model separation and semantic immutability: Tasks 3, 5,
  and 7.
- Model weakness creates warnings rather than silent semantic changes: Tasks 3,
  4, and 6.
- Model switch preserves the brief but invalidates decomposition/Recipe: Tasks
  2, 5, and 8.
- Exact immutable activated profile version/hash and evidence-backed approved
  rules: Tasks 1, 3, 4, and 7.
- Async stale-result protection: Tasks 4 and 5.
- Refresh/reopen recovery and no stale downstream resurrection: Task 8.
- Spec §13 functional acceptance for adapted preview and workbench handoff:
  Tasks 4–8.
- Spec §13 quality scenarios, provenance, lock reliability, adaptation
  correctness, recoverability, and fake providers: Task 8.
- LoRA-lite is intentionally excluded and remains a separate subsequent plan.

## Self-Review

- All production files, tests, public interfaces, endpoint fields, error codes,
  and commit boundaries are named explicitly.
- The decomposition stays inside the existing `creativeIntake` source of truth;
  UI state contains lifecycle only.
- Profile bodies and claim decisions are loaded server-side from an exact
  activated immutable version; no client profile snapshot is trusted.
- Proposed/rejected rules are visible warnings only, never adaptations.
- Locked facts are checked before the first canonical write and cannot be
  edited in the block-review UI.
- Recipe v1 distinguishes profile-content SHA-256 from checkpoint SHA-256 and
  preserves bundled-profile compatibility.
- Automated tests use fake providers and stored fixtures only.
- Placeholder scan terms are absent from implementation steps except inside
  the explicit verification command that searches for them.
