# Anima Prompt Studio Prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dependency-free, clickable HTML prototype that demonstrates the approved Anima Prompt Studio home, text-to-prompt, reference-library, image-analysis, structured-editor, bilingual-output, and local-model-residency flows.

**Architecture:** Create one prototype folder containing a static HTML shell, a focused stylesheet, a small state-driven JavaScript controller, and sample data. Keep user-visible behavior in pure state transition functions so Node's built-in test runner can verify flows without a browser, then use Playwright for `1920×1080` desktop visual verification.

**Tech Stack:** HTML5, CSS, vanilla JavaScript, Node.js built-in `node:test`, Playwright for visual QA.

---

## File Structure

- `prototype/index.html`: semantic page shell, navigation, dialogs, templates, and accessible controls.
- `prototype/styles.css`: restrained visual system for a `1920×1080` desktop workbench.
- `prototype/data.js`: reference entries, structured prompt blocks, model metadata, and bilingual sample results.
- `prototype/app.js`: state model, transitions, rendering, simulated analysis queue, and browser event bindings.
- `prototype/tests/app.test.js`: behavior tests for navigation, reference import, model selection, analysis lifecycle, model release, and bilingual synchronization.
- `prototype/README.md`: how to open the prototype and which flows to try.

### Task 1: Prototype State Contract

**Files:**
- Create: `prototype/tests/app.test.js`
- Create: `prototype/app.js`

- [ ] **Step 1: Write failing state-transition tests**

Create tests using `node:test` for:

```js
test("starts on a blank new-work screen", () => {
  const state = createInitialState();
  assert.equal(state.view, "home");
  assert.equal(state.projectName, "未命名作品");
});

test("imports a reference into prompt expansion without mutating the library", () => {
  const state = createInitialState();
  const next = reduceState(state, { type: "IMPORT_REFERENCE", id: "rain-city" });
  assert.equal(next.view, "text");
  assert.equal(next.textMode, "expand");
  assert.match(next.draftInput, /1girl/);
  assert.equal(state.references[0].title, next.references[0].title);
});

test("runs selected image analyzers in a serial queue", () => {
  const state = createInitialState();
  const selected = reduceState(state, { type: "TOGGLE_ANALYZER", id: "joycaption" });
  const running = reduceState(selected, { type: "START_ANALYSIS" });
  assert.deepEqual(running.analysisQueue, ["wd14", "florence", "joycaption"]);
  assert.equal(running.analyzers.wd14.status, "running");
  assert.equal(running.analyzers.florence.status, "waiting");
});

test("releases every loaded image model", () => {
  const state = createInitialState();
  const loaded = {
    ...state,
    analyzers: Object.fromEntries(
      Object.entries(state.analyzers).map(([id, model]) => [id, { ...model, status: "ready" }])
    ),
  };
  const released = reduceState(loaded, { type: "RELEASE_MODELS" });
  assert.ok(Object.values(released.analyzers).every((model) => model.status === "idle"));
});
```

- [ ] **Step 2: Run tests and verify the expected failure**

Run:

```powershell
node --test prototype/tests/app.test.js
```

Expected: FAIL because `prototype/app.js` does not exist or does not export the required API.

- [ ] **Step 3: Implement the minimal state API**

Add CommonJS-compatible exports for:

```js
function createInitialState() {}
function reduceState(state, action) {}
function getCombinedPrompt(state) {}

if (typeof module !== "undefined") {
  module.exports = { createInitialState, reduceState, getCombinedPrompt };
}
```

Support the exact actions exercised by the tests: `NAVIGATE`, `SET_TEXT_MODE`, `IMPORT_REFERENCE`, `TOGGLE_ANALYZER`, `START_ANALYSIS`, `ADVANCE_ANALYSIS`, and `RELEASE_MODELS`.

- [ ] **Step 4: Run state tests**

Run:

```powershell
node --test prototype/tests/app.test.js
```

Expected: all tests PASS with no warnings.

- [ ] **Step 5: Commit**

```powershell
git add prototype/app.js prototype/tests/app.test.js
git commit -m "test: define prototype interaction state"
```

### Task 2: Sample Content and Bilingual Prompt Behavior

**Files:**
- Create: `prototype/data.js`
- Modify: `prototype/app.js`
- Modify: `prototype/tests/app.test.js`

- [ ] **Step 1: Add failing tests for sample content**

Test that:

```js
test("expands mixed-language input into paired bilingual output", () => {
  let state = createInitialState();
  state = reduceState(state, { type: "SET_DRAFT", value: "1girl, raining，霓虹街道" });
  state = reduceState(state, { type: "EXPAND_PROMPT" });
  assert.match(state.output.positiveEn, /1girl/);
  assert.match(state.output.positiveZh, /少女/);
  assert.ok(state.blocks.length >= 8);
});

test("random generation records a reproducible seed", () => {
  const next = reduceState(createInitialState(), { type: "RANDOMIZE", seed: 3719 });
  assert.equal(next.randomSeed, 3719);
  assert.ok(next.output.positiveEn.length > 40);
  assert.ok(next.output.positiveZh.length > 20);
});
```

- [ ] **Step 2: Verify tests fail**

Run:

```powershell
node --test prototype/tests/app.test.js
```

Expected: FAIL because bilingual expansion and deterministic random samples are not implemented.

- [ ] **Step 3: Add sample data and minimal prompt behavior**

Populate `prototype/data.js` with:

- three reference cards with local generated placeholder artwork expressed as CSS classes, not remote assets;
- ten structured prompt blocks;
- WD14, Florence, JoyCaption, QwenVL, and external API metadata;
- sample raw analyzer outputs and a merged bilingual result;
- character and artist quick-pick entries.

Implement deterministic demo actions that map the approved workflows into these samples. Do not pretend that real inference occurs.

- [ ] **Step 4: Run tests**

Run:

```powershell
node --test prototype/tests/app.test.js
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add prototype/data.js prototype/app.js prototype/tests/app.test.js
git commit -m "feat: add bilingual prototype sample flows"
```

### Task 3: Clickable Workbench Interface

**Files:**
- Create: `prototype/index.html`
- Create: `prototype/styles.css`
- Modify: `prototype/app.js`

- [ ] **Step 1: Add a failing static contract test**

Extend `prototype/tests/app.test.js` to read `prototype/index.html` and assert the presence of:

```js
for (const id of [
  "app",
  "homeView",
  "textView",
  "imageView",
  "referenceLibrary",
  "structuredEditor",
  "bilingualOutput",
  "modelDrawer",
]) {
  assert.match(html, new RegExp(`id=["']${id}["']`));
}
```

- [ ] **Step 2: Verify the test fails**

Run:

```powershell
node --test prototype/tests/app.test.js
```

Expected: FAIL because `prototype/index.html` is missing.

- [ ] **Step 3: Build the semantic HTML shell**

Implement:

- top application bar with new work, library, local-service status, and settings;
- blank “新建作品” home with text and image entry buttons;
- text workspace tabs for random, expansion, and reference library;
- image workspace with upload drop zone, analyzer checkboxes, auto-combine toggle, queue status, and raw-result tabs;
- center structured editor;
- right bilingual positive/negative output;
- model manager drawer with residency segmented control and release button.

Use Lucide-compatible inline icon labels only where a symbol is familiar; do not add a marketing hero.

- [ ] **Step 4: Add rendering and simulated interactions**

Bind controls to `reduceState`, render state into the page, and simulate analyzer progression with short timers. Ensure all demo state is clearly labeled as simulated.

- [ ] **Step 5: Add responsive styling**

Use:

- compact 56px top bar;
- neutral gray workspace surfaces with teal and coral accents;
- maximum 8px card radius;
- stable three-column desktop editor;
- fixed three-column desktop layout optimized for `1920×1080`;
- no gradients, decorative orbs, nested cards, oversized hero typography, or viewport-scaled font sizes.

- [ ] **Step 6: Run tests**

Run:

```powershell
node --test prototype/tests/app.test.js
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```powershell
git add prototype/index.html prototype/styles.css prototype/app.js prototype/tests/app.test.js
git commit -m "feat: build clickable prompt studio prototype"
```

### Task 4: Documentation and Visual Verification

**Files:**
- Create: `prototype/README.md`
- Modify: `prototype/index.html`
- Modify: `prototype/styles.css`
- Modify: `prototype/app.js`

- [ ] **Step 1: Document the prototype**

Explain that `prototype/index.html` opens directly, inference is simulated, and list the four review flows:

1. new work to prompt expansion;
2. reference card to expansion;
3. image upload to multi-model analysis;
4. model residency and release behavior.

- [ ] **Step 2: Run automated tests**

Run:

```powershell
node --test prototype/tests/app.test.js
```

Expected: all tests PASS.

- [ ] **Step 3: Open and verify desktop layout**

Use Playwright at `1920x1080`. Verify:

- first screen is the new-work choice;
- navigation reaches text and image workspaces;
- reference import populates expansion input;
- image analysis visibly advances through the selected queue;
- no text overlaps or horizontal page overflow;
- bilingual output remains visible and readable.

- [ ] **Step 4: Fix any visual defects and rerun tests**

Run:

```powershell
node --test prototype/tests/app.test.js
```

Expected: all tests PASS after visual fixes.

- [ ] **Step 5: Commit**

```powershell
git add prototype docs/superpowers/plans/2026-06-19-anima-prompt-studio-prototype.md
git commit -m "docs: finish prototype walkthrough"
```

### Task 5: Iterative Editing and Reusable Resources

**Files:**
- Modify: `prototype/data.js`
- Modify: `prototype/app.js`
- Modify: `prototype/index.html`
- Modify: `prototype/styles.css`
- Modify: `prototype/tests/app.test.js`
- Modify: `prototype/README.md`

- [ ] **Step 1: Add failing tests**

Cover these behaviors:

- the HTML does not expose a random seed input;
- text generation source can switch between local LLM and external API;
- a reusable resource can be created and added to the user's library;
- editing a prompt block marks it dirty without changing final output;
- applying changes recompiles output and increments the project version;
- discarding changes restores the last applied block snapshot.

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
node --test prototype/tests/app.test.js
```

Expected: FAIL because resource creation and versioned apply behavior do not exist.

- [ ] **Step 3: Implement state transitions**

Add actions:

```text
SET_TEXT_PROVIDER
CREATE_RESOURCE
APPLY_RESOURCE
UPDATE_BLOCK
DISCARD_CHANGES
APPLY_CHANGES
SAVE_BLOCK_AS_RESOURCE
```

Keep working blocks separate from the last applied snapshot. Only `APPLY_CHANGES` updates `PromptResult`.

- [ ] **Step 4: Update the desktop interface**

Add:

- visible per-task text provider selector;
- no visible random seed control;
- “新建资源” dialog and “保存为资源” block actions;
- dirty-state banner;
- “撤销修改” and “应用修改并生成新版本” controls;
- current version indicator and compact version history.

- [ ] **Step 5: Verify**

Run:

```powershell
node --test prototype/tests/app.test.js
```

Then visually verify the edit-to-V2 flow at `1920×1080`.
