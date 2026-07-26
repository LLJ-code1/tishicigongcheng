# Creative Director Multi-Image Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the unified Creative Director from one local reference image to as many as eight independently analyzed images whose selected elements remain traceable through the brief.

**Architecture:** Keep `creativeIntake.inputs.images` as the canonical ordered list and keep browser `File` objects in an in-memory controller keyed by stable image ID. Normalize director evidence to an ordered list of bounded text-only records; every record must bind to one current canonical image before any external provider call. UI mutations continue to replace the complete canonical input list through the server transition API.

**Tech Stack:** Python 3.12 standard library, vanilla JavaScript/HTML/CSS, Node 24 test runner, existing local vision endpoint and workspace commit path.

## Global Constraints

- Support at most eight images, each PNG/JPEG/WebP, at most 20 MiB and 50 million decoded pixels.
- Raw image bytes and local paths never enter canonical metadata, project payloads, logs, or external text-provider requests.
- Each borrowed brief item uses `source: {type: "image", refId: "<stable-image-id>"}`.
- An image contributes only its selected `requestedUses`; an empty list means “AI suggests candidates” and does not silently confirm any element.
- One image failure must not discard successful evidence from other images.
- Existing single-image saved projects and in-flight metadata remain valid.
- All canonical mutations use `/api/creative-intake/transition`; all accepted metadata uses the metadata-only workspace commit.
- Do not modify AnimaDex or depend on a real external provider in tests.

---

### Task 1: Multi-Image Evidence Contract

**Files:**
- Modify: `prototype/creative_director.py`
- Modify: `prototype/tests/test_creative_director.py`
- Modify: `prototype/tests/test_server.py`

**Interfaces:**
- Consumes: canonical `current.inputs.images: list[ImageReference]`, maximum eight.
- Produces: `validate_image_evidence(current, value) -> list[dict]`; provider payload key `imageEvidence` is always a JSON array.

- [ ] **Step 1: Write failing domain tests**

Add cases proving that `imageEvidence` accepts `null`, a legacy single object, or an array of at most eight objects and normalizes them to an array. Assert rejection before provider I/O for duplicate/unknown IDs, more than eight entries, mismatched uses, unsafe IDs, unsupported MIME/status on any current image, and evidence containing bytes/path/key-like fields.

```python
evidence = [
    {"imageId": "image-action", "requestedUses": ["action"], "summary": "running"},
    {"imageId": "image-outfit", "requestedUses": ["outfit"], "summary": "red coat"},
]
result = run_creative_director(current=current, image_evidence=evidence, provider=fake)
self.assertEqual(result.provider_payload["imageEvidence"], evidence)
```

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
& 'H:\提示词工程\AnimaDex\.venv\Scripts\python.exe' -m unittest prototype.tests.test_creative_director prototype.tests.test_server
```

Expected: failures because the current validator rejects more than one image and expects one evidence object.

- [ ] **Step 3: Implement ordered multi-image validation**

Replace the single-image assumptions with:

```python
def validate_image_evidence(current: dict, value: object) -> list[dict]:
    evidence_items = [] if value is None else ([value] if isinstance(value, dict) else value)
    # strict list shape, maximum 8, unique IDs
    # validate every current image domain before provider I/O
    # bind every evidence item to its matching current image
    # return bounded text-only normalized records in canonical image order
```

Keep legacy object input for compatibility, but always send the normalized array to the provider. Preserve the existing secret-fragment echo and strict proposal validation.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the focused Python command from Step 2 and require zero failures.

- [ ] **Step 5: Commit**

```powershell
git add -- prototype/creative_director.py prototype/tests/test_creative_director.py prototype/tests/test_server.py
git commit -m "feat: support multi-image director evidence"
```

### Task 2: Frontend Attachment Controller and Canonical Mutations

**Files:**
- Modify: `prototype/app.js`
- Modify: `prototype/tests/app.test.js`

**Interfaces:**
- Consumes: accepted canonical image arrays from the transition endpoint.
- Produces: `createDirectorImageCollectionController()`, `buildDirectorImagesReplaceAction(references)`, and per-ID file/evidence state.

- [ ] **Step 1: Write failing controller tests**

Cover adding one to eight files, rejecting the ninth, duplicate stable IDs, replacing one card, removing one card, independent object URL cleanup, accepted-canonical binding after metadata persistence failure, and no file binding after a rejected transition.

```javascript
controller.bind("image-a", fileA);
controller.bind("image-b", fileB);
controller.remove("image-a");
assert.equal(controller.get("image-b").file, fileB);
assert.deepEqual(revokedUrls, [urlA]);
```

- [ ] **Step 2: Run Node tests and verify RED**

```powershell
& 'C:\Users\13688\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test prototype\tests\app.test.js
```

Expected: missing collection controller and array mutation helpers.

- [ ] **Step 3: Implement the collection controller**

Use a `Map<imageId, {file, objectUrl, evidence, failures, status}>`. Every `bind`, `replace`, `remove`, and `clear` revokes exactly the object URLs it owns. Build full-list `replace_inputs` actions without touching canonical state locally.

```javascript
function buildDirectorImagesReplaceAction(current, images) {
  return { type: "replace_inputs", text: current.inputs.text, images: clone(images) };
}
```

After an accepted response, reconcile the controller against canonical IDs. When persistence fails after acceptance, retain all newly accepted files and display the save error.

- [ ] **Step 4: Run Node tests and verify GREEN**

Run the Node command from Step 2 and require zero failures.

- [ ] **Step 5: Commit**

```powershell
git add -- prototype/app.js prototype/tests/app.test.js
git commit -m "feat: add multi-image attachment controller"
```

### Task 3: Per-Image Analysis, Partial Failure, and Director Request

**Files:**
- Modify: `prototype/app.js`
- Modify: `prototype/tests/app.test.js`

**Interfaces:**
- Consumes: per-ID controller entries and `/api/vision/analyze`.
- Produces: `resolveDirectorImageEvidenceCollection(...) -> {items, failures}` and an external-safe director request.

- [ ] **Step 1: Write failing orchestration tests**

Test two successes, one success plus one complete failure, retry of only the failed image, image removal while analysis is running, use-chip changes invalidating only that image’s evidence, and stale result rejection after any canonical image revision.

```javascript
const result = await resolveDirectorImageEvidenceCollection({
  references: [actionRef, outfitRef, environmentRef],
  analyze,
});
assert.deepEqual(result.items.map((item) => item.imageId), [
  "action-ref",
  "environment-ref",
]);
assert.deepEqual(result.failures.map((item) => item.imageId), ["outfit-ref"]);
```

Assert that the external request contains only bounded evidence text and never `File`, blob URL, base64, path, or bytes.

- [ ] **Step 2: Run Node tests and verify RED**

Run the complete Node test command and confirm the collection behavior is absent.

- [ ] **Step 3: Implement per-image evidence resolution**

Analyze each attached canonical image independently through the local endpoint. Reuse evidence only when image ID, file identity, requested uses, analyzer selection, session, workspace revision, and project revision still match. Return successful evidence even when peers fail, and expose retry by image ID.

- [ ] **Step 4: Run Node tests and verify GREEN**

Require the full Node suite to pass.

- [ ] **Step 5: Commit**

```powershell
git add -- prototype/app.js prototype/tests/app.test.js
git commit -m "feat: orchestrate multi-image local evidence"
```

### Task 4: Multi-Image Cards, Drag-and-Drop, and Provenance UI

**Files:**
- Modify: `prototype/index.html`
- Modify: `prototype/styles.css`
- Modify: `prototype/app.js`
- Modify: `prototype/tests/app.test.js`

**Interfaces:**
- Consumes: canonical image list and per-ID controller render model.
- Produces: image card DOM keyed by `data-director-image-id` and brief source labels keyed by `source.refId`.

- [ ] **Step 1: Write failing render and interaction tests**

Require `multiple` file selection, a drop zone, an “添加图片” control disabled at eight, one preview/status/use-chip group per image, per-card replace/remove/retry, and brief rows that display the originating image name or stable fallback ID.

- [ ] **Step 2: Run Node tests and verify RED**

Run the complete Node suite and confirm missing multi-card markup/handlers.

- [ ] **Step 3: Implement the multi-card UI**

Render cards from canonical order. Every card owns eight use chips plus “让 AI 建议”. Dropped/selected files are validated together; accepted files are appended until the eight-image limit, and rejected files produce explicit per-file errors. Removing one image must leave all other files, evidence, selection, and previews intact.

For image-sourced brief rows, resolve `source.refId` against canonical images and show `图片：<name>`; never infer provenance from row prose.

- [ ] **Step 4: Browser verification**

At a 1920×1080 desktop viewport, verify empty, one-image, eight-image, per-image failure, brief provenance, and keyboard focus states. Confirm no horizontal overflow inside the director cards.

- [ ] **Step 5: Run Node tests and commit**

```powershell
& 'C:\Users\13688\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test prototype\tests\app.test.js
git add -- prototype/index.html prototype/styles.css prototype/app.js prototype/tests/app.test.js
git commit -m "feat: add multi-image provenance UI"
```

### Task 5: Recovery, Documentation, and Full Verification

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/data-model.md`
- Modify: `docs/integration-guide.md`
- Modify: `docs/operator-runbook.md`
- Modify: `docs/roadmap.md`
- Modify: `prototype/tests/app.test.js`
- Modify: `prototype/tests/test_server.py`

**Interfaces:**
- Consumes: all prior task contracts.
- Produces: documented, recoverable multi-image workflow with full regression evidence.

- [ ] **Step 1: Add recovery and acceptance tests**

Cover reopening a project with up to eight safe references, requiring reattachment only for missing browser files, preserving per-image uses and brief sources, multi-image action/outfit/environment acceptance, and rejecting any persisted image path/base64.

- [ ] **Step 2: Update documentation**

Document the array evidence contract, eight-image boundary, local-only bytes, per-image retry, source `refId`, browser-file recovery boundary, and operator troubleshooting. Mark multi-image provenance complete while leaving model research, model-adapted decomposition, and LoRA pending.

- [ ] **Step 3: Run full verification**

```powershell
& 'H:\提示词工程\AnimaDex\.venv\Scripts\python.exe' -m unittest discover -s prototype\tests -p "test_*.py"
& 'C:\Users\13688\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test prototype\tests\app.test.js
& 'H:\提示词工程\AnimaDex\.venv\Scripts\python.exe' -m unittest discover -s scripts\tests -p "test_*.py"
& 'H:\提示词工程\AnimaDex\.venv\Scripts\python.exe' scripts\build_random_wordlists.py --check
& 'H:\提示词工程\AnimaDex\.venv\Scripts\python.exe' scripts\generate_unicode15_ranges.py --check
git diff --check
```

- [ ] **Step 4: Audit tracked files**

Verify no image bytes, API keys, `.env`, SQLite databases, logs, PIDs, model binaries, blob URLs, base64 payloads, or SDD artifacts are tracked.

- [ ] **Step 5: Commit**

```powershell
git add -- docs/architecture.md docs/data-model.md docs/integration-guide.md docs/operator-runbook.md docs/roadmap.md prototype/tests/app.test.js prototype/tests/test_server.py
git commit -m "docs: complete multi-image provenance"
```
