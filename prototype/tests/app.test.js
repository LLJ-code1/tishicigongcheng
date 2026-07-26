const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  createInitialState,
  createNewProjectState,
  hydrateProjectState,
  buildProjectPayload,
  buildVersionPayload,
  buildWorkspaceCommitPayload,
  createClientId,
  shouldConfirmWorkspaceDiscard,
  reduceState,
  getCombinedPrompt,
  isSupportedImageFile,
  compileBlocks,
  compileBlockFragment,
  composeArtistMix,
  normalizeBlockWeight,
  normalizeResourceWeights,
  canonicalizePromptItem,
  normalizeModelStatus,
  statusLabel,
  projectSettingsMetadata,
  settingsWritePayload,
  enqueueByKey,
  isWorkspaceRequestCurrent,
  emptyCreativeIntake,
  normalizeCreativeIntake,
  isCreativeIntakeResponseCurrent,
} = require("../app.js");
const unicode15 = require("../unicode15-data.js");

function confirmedBriefFixture() {
  return {
    status: "confirmed",
    summary: "A red dress on a runway.",
    items: [
      {
        id: "item-outfit",
        category: "outfit",
        text: "red dress",
        source: { type: "user", refId: null },
        locked: true,
      },
    ],
    aiAdditions: [],
    openQuestions: [],
  };
}

function creativeIntakeStageFixture(stage) {
  const value = {
    ...emptyCreativeIntake(),
    revision: [
      "intake",
      "direction_selected",
      "brief_draft",
      "brief_confirmed",
      "model_selected",
      "decomposition_draft",
      "decomposition_confirmed",
    ].indexOf(stage),
    stage,
  };
  if (stage === "intake") return value;

  value.directions = [
    { id: "direction-one", label: "Runway", summary: "A runway study." },
  ];
  value.selectedDirectionId = "direction-one";
  if (stage === "direction_selected") return value;

  value.brief = {
    ...confirmedBriefFixture(),
    status: "draft",
    items: confirmedBriefFixture().items.map((item) => ({
      ...item,
      locked: false,
    })),
  };
  if (stage === "brief_draft") return value;

  value.brief = confirmedBriefFixture();
  if (stage === "brief_confirmed") return value;

  value.selectedModelProfileId = "anima-1.1-v1";
  value.recipeStatus = "stale";
  if (stage === "model_selected") return value;

  value.decomposition = {
    status: "draft",
    blocks: [
      {
        id: "block-one",
        category: "outfit",
        zh: "红裙",
        en: "red dress",
        source: { type: "user", refId: null },
        locked: false,
        approved: true,
        reason: "From the confirmed brief.",
        risks: [],
      },
    ],
  };
  if (stage === "decomposition_draft") return value;

  value.decomposition.status = "confirmed";
  value.recipeStatus = "ready";
  return value;
}

function createGeneratedState() {
  const blockIds = [
    "quality",
    "artist",
    "subject",
    "appearance",
    "pose",
    "scene",
    "composition",
    "lighting",
    "effects",
    "negative",
  ];
  return reduceState(createInitialState(), {
    type: "APPLY_TEXT_RANDOM",
    item: {
      positiveEn: "masterpiece, best quality, 1girl, standing, rainy city",
      positiveZh: "杰作，最佳质量，一名女性角色，站立，雨中城市",
      negativeEn: "low quality",
      negativeZh: "低质量",
      relationEn: "She stands in the rainy city.",
      relationZh: "她站在雨中的城市里。",
      blocks: blockIds.map((id) => ({
        id,
        label: id,
        en: `${id} en`,
        zh: `${id} zh`,
        locked: id === "quality" || id === "subject",
        source: "external_api",
        confidence: 90,
      })),
      checks: {},
    },
    finishedAt: 2000,
  });
}

function applyServerSave(state, version, extraMetadata = {}) {
  const payload = buildVersionPayload(state);
  return reduceState(state, {
    type: "PROJECT_SAVED",
    savedRevision: state.workingRevision,
    item: {
      id: `version-${version}`,
      projectId: state.projectId || "project-test",
      version,
      ...payload,
      metadata: { ...payload.metadata, ...extraMetadata },
      createdAt: `2026-07-${String(version).padStart(2, "0")}T08:00:00+00:00`,
    },
  });
}

function createPersistedGeneratedState() {
  let state = createGeneratedState();
  state = reduceState(state, {
    type: "PROJECT_CREATED",
    item: { id: "project-test", name: "Rain Study", status: "draft" },
  });
  return applyServerSave(state, 1);
}

test("starts on a blank new-work screen", () => {
  const state = createInitialState();
  assert.equal(state.view, "home");
  assert.equal(state.projectName, "未命名作品");
  assert.equal(state.output.positiveEn, "");
});

test("starts with an empty creative intake session", () => {
  const state = createInitialState();
  assert.equal(state.creativeIntake.schemaVersion, 1);
  assert.equal(state.creativeIntake.stage, "intake");
  assert.equal(state.creativeIntake.revision, 0);
});

test("replaces creative intake only with a normalized server item", () => {
  const state = createInitialState();
  const item = {
    ...emptyCreativeIntake(),
    revision: 1,
    inputs: { text: "red dress, runway", images: [] },
  };
  const next = reduceState(state, {
    type: "CREATIVE_INTAKE_REPLACED",
    item,
  });

  assert.equal(next.creativeIntake.revision, 1);
  assert.equal(next.creativeIntake.inputs.text, "red dress, runway");
  assert.equal(next.hasUnsavedProjectChanges, true);
  item.inputs.text = "mutated after dispatch";
  assert.equal(next.creativeIntake.inputs.text, "red dress, runway");
});

test("creative intake normalization mirrors canonical containers enums and limits", () => {
  const canonical = {
    ...emptyCreativeIntake(),
    revision: 4,
    stage: "brief_confirmed",
    directions: [
      { id: "direction-one", label: "Runway", summary: "A runway study." },
    ],
    selectedDirectionId: "direction-one",
    brief: confirmedBriefFixture(),
    recipeStatus: "stale",
  };
  assert.deepEqual(normalizeCreativeIntake(canonical), canonical);

  for (const malformed of [
    { ...canonical, stage: "unsupported" },
    { ...canonical, directions: "not-an-array" },
    {
      ...canonical,
      directions: [
        ...canonical.directions,
        { id: "two", label: "Two", summary: "" },
        { id: "three", label: "Three", summary: "" },
        { id: "four", label: "Four", summary: "" },
      ],
    },
    {
      ...canonical,
      inputs: { text: "x".repeat(200_001), images: [] },
    },
  ]) {
    assert.deepEqual(normalizeCreativeIntake(malformed), emptyCreativeIntake());
  }
});

test("creative intake fails closed when a confirmed brief precedes brief_confirmed", () => {
  const result = normalizeCreativeIntake({
    ...emptyCreativeIntake(),
    revision: 1,
    stage: "intake",
    brief: confirmedBriefFixture(),
  });

  assert.deepEqual(result, {
    schemaVersion: 1,
    revision: 0,
    stage: "intake",
    inputs: { text: "", images: [] },
    directions: [],
    selectedDirectionId: null,
    brief: null,
    selectedModelProfileId: null,
    decomposition: null,
    recipeStatus: "missing",
    conflicts: [],
  });
});

test("creative intake fails closed when decomposition precedes model_selected", () => {
  const result = normalizeCreativeIntake({
    ...emptyCreativeIntake(),
    revision: 1,
    stage: "brief_confirmed",
    decomposition: { status: "draft", blocks: [] },
  });

  assert.deepEqual(result, {
    schemaVersion: 1,
    revision: 0,
    stage: "intake",
    inputs: { text: "", images: [] },
    directions: [],
    selectedDirectionId: null,
    brief: null,
    selectedModelProfileId: null,
    decomposition: null,
    recipeStatus: "missing",
    conflicts: [],
  });
});

test("creative intake normalization enforces exact cross-field stage invariants", () => {
  const intakeWithSelection = creativeIntakeStageFixture("intake");
  intakeWithSelection.directions = [
    { id: "direction-one", label: "Runway", summary: "A runway study." },
  ];
  intakeWithSelection.selectedDirectionId = "direction-one";

  const directionWithBrief = creativeIntakeStageFixture("direction_selected");
  directionWithBrief.brief = {
    ...confirmedBriefFixture(),
    status: "draft",
  };

  const briefWithoutDraft = creativeIntakeStageFixture("brief_draft");
  briefWithoutDraft.brief = null;

  const confirmedWithDraft = creativeIntakeStageFixture("brief_confirmed");
  confirmedWithDraft.brief.status = "draft";

  const modelWithoutSelection = creativeIntakeStageFixture("model_selected");
  modelWithoutSelection.selectedModelProfileId = null;

  const modelWithDecomposition = creativeIntakeStageFixture("model_selected");
  modelWithDecomposition.decomposition = {
    status: "draft",
    blocks: [],
  };

  const decompositionWithoutDraft =
    creativeIntakeStageFixture("decomposition_draft");
  decompositionWithoutDraft.decomposition = null;

  const confirmedWithDraftDecomposition = creativeIntakeStageFixture(
    "decomposition_confirmed"
  );
  confirmedWithDraftDecomposition.decomposition.status = "draft";

  const confirmedWithStaleRecipe = creativeIntakeStageFixture(
    "decomposition_confirmed"
  );
  confirmedWithStaleRecipe.recipeStatus = "stale";

  const confirmedWithUnlockedItem =
    creativeIntakeStageFixture("brief_confirmed");
  confirmedWithUnlockedItem.brief.items[0].locked = false;

  for (const malformed of [
    intakeWithSelection,
    directionWithBrief,
    briefWithoutDraft,
    confirmedWithDraft,
    modelWithoutSelection,
    modelWithDecomposition,
    decompositionWithoutDraft,
    confirmedWithDraftDecomposition,
    confirmedWithStaleRecipe,
    confirmedWithUnlockedItem,
  ]) {
    assert.deepEqual(normalizeCreativeIntake(malformed), emptyCreativeIntake());
  }
});

test("creative intake normalization rejects incomplete decomposition draft state", () => {
  const malformed = {
    ...emptyCreativeIntake(),
    revision: 9,
    stage: "decomposition_draft",
    directions: [
      { id: "direction-one", label: "Runway", summary: "A runway study." },
    ],
    selectedDirectionId: "direction-one",
    decomposition: creativeIntakeStageFixture("decomposition_draft").decomposition,
  };

  assert.deepEqual(normalizeCreativeIntake(malformed), emptyCreativeIntake());
});

test("persists creative intake in project metadata but not Recipe v1", () => {
  const state = createInitialState();
  state.creativeIntake = {
    ...creativeIntakeStageFixture("brief_confirmed"),
    revision: 4,
  };

  assert.deepEqual(
    buildProjectPayload(state).metadata.creativeIntake,
    state.creativeIntake
  );
  assert.equal(
    Object.hasOwn(buildVersionPayload(state).metadata, "creativeIntake"),
    false
  );
});

test("hydrates creative intake from current project metadata", () => {
  const original = createInitialState();
  const restored = hydrateProjectState(original, {
    id: "project-one",
    metadata: {
      workspaceBaseVersion: 0,
      creativeIntake: {
        ...creativeIntakeStageFixture("brief_confirmed"),
        revision: 4,
      },
    },
    versions: [],
  });

  assert.equal(restored.creativeIntake.stage, "brief_confirmed");
  assert.equal(restored.creativeIntake.revision, 4);
});

test("absent malformed or stale historical creative intake metadata fails closed", () => {
  const malformedCases = [
    {},
    { creativeIntake: null },
    {
      creativeIntake: {
        ...emptyCreativeIntake(),
        inputs: { text: "", images: "not-an-array" },
      },
    },
  ];
  for (const metadata of malformedCases) {
    const restored = hydrateProjectState(createInitialState(), {
      id: "project-one",
      metadata,
      versions: [],
    });
    assert.deepEqual(restored.creativeIntake, emptyCreativeIntake());
  }

  const stale = hydrateProjectState(createInitialState(), {
    id: "project-one",
    metadata: {
      workspaceBaseVersion: 0,
      creativeIntake: {
        ...emptyCreativeIntake(),
        revision: 8,
        inputs: { text: "stale metadata", images: [] },
      },
    },
    versions: [
      {
        id: "version-one",
        version: 1,
        metadata: {},
      },
    ],
  });
  assert.deepEqual(stale.creativeIntake, emptyCreativeIntake());
});

test("creating a new project resets creative intake and preserves global settings", () => {
  const state = createInitialState();
  state.settings.localTextModel = "custom-local-model";
  state.creativeIntake = {
    ...emptyCreativeIntake(),
    revision: 2,
    inputs: { text: "old project", images: [] },
  };

  const fresh = createNewProjectState(state);

  assert.deepEqual(fresh.creativeIntake, emptyCreativeIntake());
  assert.equal(fresh.settings.localTextModel, "custom-local-model");
});

test("metadata-only project save carries creative intake without a prompt version", () => {
  let state = createInitialState();
  state = reduceState(state, {
    type: "CREATIVE_INTAKE_REPLACED",
    item: {
      ...emptyCreativeIntake(),
      revision: 1,
      inputs: { text: "metadata only", images: [] },
    },
  });

  const commit = buildWorkspaceCommitPayload(state, {
    operationId: "save-intake",
    projectId: "project-intake",
  });

  assert.equal(commit.version, null);
  assert.equal(commit.project.metadata.creativeIntake.revision, 1);
  assert.equal(commit.project.metadata.creativeIntake.inputs.text, "metadata only");
});

test("creative intake metadata rejects image bytes paths and external API keys", () => {
  const state = createInitialState();
  state.settings.apiTextKey = "external-secret";
  state.creativeIntake = {
    ...emptyCreativeIntake(),
    revision: 1,
    inputs: {
      text: "",
      images: [
        {
          id: "image-one",
          name: "C:\\private\\reference.png",
          mimeType: "image/png",
          status: "ready",
          requestedUses: [],
          bytes: "base64-image-bytes",
          apiKey: "external-secret",
        },
      ],
    },
  };

  const metadata = buildProjectPayload(state).metadata;

  assert.deepEqual(metadata.creativeIntake, emptyCreativeIntake());
  assert.equal(JSON.stringify(metadata).includes("base64-image-bytes"), false);
  assert.equal(JSON.stringify(metadata).includes("C:\\private"), false);
  assert.equal(JSON.stringify(metadata).includes("external-secret"), false);
});

test("creative intake changes participate in workspace discard confirmation", () => {
  const state = reduceState(createInitialState(), {
    type: "CREATIVE_INTAKE_REPLACED",
    item: {
      ...emptyCreativeIntake(),
      revision: 1,
      inputs: { text: "unsaved intake", images: [] },
    },
  });

  assert.equal(shouldConfirmWorkspaceDiscard(state), true);
});

test("creative intake response guard rejects every stale request dimension", () => {
  const state = createInitialState();
  state.projectRevision = 7;
  state.creativeIntake.revision = 5;
  const request = {
    sessionId: 3,
    projectRevision: 7,
    creativeIntakeRevision: 5,
  };

  assert.equal(isCreativeIntakeResponseCurrent(request, 3, state), true);
  assert.equal(
    isCreativeIntakeResponseCurrent({ ...request, sessionId: 2 }, 3, state),
    false
  );
  assert.equal(
    isCreativeIntakeResponseCurrent(
      { ...request, projectRevision: 6 },
      3,
      state
    ),
    false
  );
  assert.equal(
    isCreativeIntakeResponseCurrent(
      { ...request, creativeIntakeRevision: 4 },
      3,
      state
    ),
    false
  );
});

test("project list state covers loading ready empty and error data", () => {
  let state = createInitialState();
  state = reduceState(state, { type: "PROJECTS_LOADING" });
  assert.equal(state.projectsStatus, "loading");
  assert.equal(state.projectsError, "");

  state = reduceState(state, {
    type: "PROJECTS_LOADED",
    items: [
      {
        id: "project-rain",
        name: "Rain Study",
        latestVersion: 2,
        versionCount: 2,
      },
    ],
  });
  assert.equal(state.projectsStatus, "ready");
  assert.equal(state.projects[0].latestVersion, 2);

  state = reduceState(state, { type: "PROJECTS_LOADED", items: [] });
  assert.equal(state.projectsStatus, "ready");
  assert.deepEqual(state.projects, []);

  state = reduceState(state, {
    type: "PROJECTS_FAILED",
    error: "database unavailable",
  });
  assert.equal(state.projectsStatus, "error");
  assert.equal(state.projectsError, "database unavailable");
});

test("hydrates a server project and its persisted history from the latest version", () => {
  const original = createInitialState();
  original.settings.textProvider = "api";
  original.settings.apiTextKey = "keep-only-in-global-settings";
  const state = hydrateProjectState(original, {
    id: "project-rain",
    name: "Rain Study",
    mode: "text",
    status: "draft",
    metadata: { textMode: "expand", draftInput: "old input" },
    updatedAt: "2026-07-16T09:00:00+00:00",
    versions: [
      {
        id: "version-2",
        version: 2,
        source: "expand",
        positiveEn: "latest prompt",
        positiveZh: "最新提示词",
        negativeEn: "latest negative",
        negativeZh: "最新负面词",
        blocks: [{ id: "scene", en: "latest scene", weight: 110 }],
        metadata: {
          textMode: "random",
          draftInput: "latest input",
          relationEn: "latest relation",
          relationZh: "最新关系",
          randomSeed: 42,
          outputChecks: { bilingualAligned: true },
        },
        createdAt: "2026-07-16T09:00:00+00:00",
      },
      {
        id: "version-1",
        version: 1,
        positiveEn: "first prompt",
        blocks: [{ id: "scene", en: "first scene" }],
        metadata: {},
        createdAt: "2026-07-15T09:00:00+00:00",
      },
    ],
  });

  assert.equal(state.projectId, "project-rain");
  assert.equal(state.projectName, "Rain Study");
  assert.equal(state.version, 2);
  assert.deepEqual(state.versionHistory.map((entry) => entry.version), [1, 2]);
  assert.equal(state.output.positiveEn, "latest prompt");
  assert.equal(state.output.relationEn, "latest relation");
  assert.equal(state.blocks[0].weight, 110);
  assert.equal(state.textMode, "random");
  assert.equal(state.draftInput, "latest input");
  assert.equal(state.randomSeed, 42);
  assert.equal(state.hasUnsavedChanges, false);
  assert.equal(state.settings.apiTextKey, "keep-only-in-global-settings");
});

test("current project metadata can restore draft-only edits without a new version", () => {
  const state = hydrateProjectState(createInitialState(), {
    id: "project-draft",
    name: "Draft only edit",
    mode: "text",
    metadata: {
      workspaceBaseVersion: 1,
      textMode: "expand",
      draftInput: "input saved after V1",
    },
    versions: [
      {
        id: "version-1",
        version: 1,
        source: "image",
        positiveEn: "saved output",
        blocks: [],
        metadata: { textMode: "random", draftInput: "input inside V1" },
      },
    ],
  });

  assert.equal(state.version, 1);
  assert.equal(state.output.positiveEn, "saved output");
  assert.equal(state.view, "text");
  assert.equal(state.analysisComplete, false);
  assert.equal(state.textMode, "expand");
  assert.equal(state.draftInput, "input saved after V1");
});

test("latest persisted version source wins when project header mode is stale", () => {
  const state = hydrateProjectState(createInitialState(), {
    id: "project-stale-mode",
    name: "Stale header",
    mode: "text",
    metadata: {},
    versions: [
      {
        id: "version-image",
        version: 1,
        source: "image",
        positiveEn: "image result",
        blocks: [],
        metadata: {},
      },
    ],
  });

  assert.equal(state.view, "image");
  assert.equal(state.analysisComplete, true);
});

test("project update payload carries the server concurrency token", () => {
  const freshPayload = buildProjectPayload(createInitialState());
  assert.equal("baseUpdatedAt" in freshPayload, false);

  const state = hydrateProjectState(createInitialState(), {
    id: "project-cas",
    name: "CAS project",
    mode: "text",
    status: "draft",
    updatedAt: "2026-07-16T09:00:00.123456+00:00",
    metadata: {},
    versions: [],
  });
  assert.equal(
    buildProjectPayload(state).baseUpdatedAt,
    "2026-07-16T09:00:00.123456+00:00"
  );
  assert.equal(
    buildVersionPayload(state).baseUpdatedAt,
    "2026-07-16T09:00:00.123456+00:00"
  );
  const source = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");
  assert.match(source, /projectResult\?\.item\?\.updatedAt/);
  assert.match(source, /guardedVersionPayload/);
});

test("new project clears project state but preserves global settings and resources", () => {
  let state = createPersistedGeneratedState();
  state.settings.textProvider = "api";
  state.resources.snippets.push({ id: "saved-recipe", name: "Saved Recipe" });
  state.projects = [{ id: "project-test", name: "Rain Study" }];
  state.projectsStatus = "ready";
  state.analyzers.florence.selected = true;
  state.analyzers.florence.available = true;
  state.analyzers.florence.status = "ready";
  state.analyzers.florence.raw = "PROJECT_A_SECRET_RAW";
  state.analyzers.florence.error = "old error";

  const fresh = createNewProjectState(state);

  assert.equal(fresh.view, "home");
  assert.equal(fresh.projectId, null);
  assert.equal(fresh.version, 0);
  assert.deepEqual(fresh.versionHistory, []);
  assert.equal(fresh.output.positiveEn, "");
  assert.equal(fresh.settings.textProvider, "api");
  assert.ok(fresh.resources.snippets.some((item) => item.id === "saved-recipe"));
  assert.equal(fresh.projects[0].id, "project-test");
  assert.equal(fresh.projectsStatus, "ready");
  assert.equal(fresh.analyzers.florence.selected, true);
  assert.equal(fresh.analyzers.florence.available, true);
  assert.equal(fresh.analyzers.florence.status, "idle");
  assert.equal(fresh.analyzers.florence.raw, "");
  assert.equal(fresh.analyzers.florence.error, "");
});

test("opening another project clears prior analyzer results and task loaders", () => {
  let previous = createGeneratedState();
  previous.analyzers.florence.selected = true;
  previous.analyzers.florence.available = true;
  previous.analyzers.florence.status = "ready";
  previous.analyzers.florence.raw = "PROJECT_A_SECRET_RAW";
  previous.analyzers.florence.error = "old error";
  previous = reduceState(previous, {
    type: "START_TEXT_EXPANSION",
    startedAt: 123,
  });
  previous = reduceState(previous, { type: "START_PENDING_TRANSLATION" });
  previous = reduceState(previous, {
    type: "START_BLOCK_VARIANT",
    id: "pose",
  });
  previous.batchRegenerating = true;
  previous.analysisQueue = ["florence"];
  previous.analyzers.florence.status = "running";

  const opened = hydrateProjectState(previous, {
    id: "project-b",
    name: "Project B",
    mode: "text",
    metadata: {},
    versions: [],
  });

  assert.equal(opened.analyzers.florence.selected, true);
  assert.equal(opened.analyzers.florence.available, true);
  assert.equal(opened.analyzers.florence.status, "idle");
  assert.equal(opened.analyzers.florence.raw, "");
  assert.equal(opened.analyzers.florence.error, "");
  assert.deepEqual(opened.analysisQueue, []);
  assert.equal(opened.textGenerating, false);
  assert.equal(opened.textDecomposing, false);
  assert.equal(opened.translatingPending, false);
  assert.equal(opened.regeneratingBlockId, "");
  assert.equal(opened.batchRegenerating, false);
  assert.equal(opened.generationProgress.status, "idle");
});

test("cancelling old workspace requests clears loaders even when opening fails", () => {
  let state = createGeneratedState();
  state = reduceState(state, { type: "START_TEXT_EXPANSION", startedAt: 123 });
  state = reduceState(state, { type: "START_PENDING_TRANSLATION" });
  state = reduceState(state, { type: "START_BLOCK_VARIANT", id: "pose" });
  state.batchRegenerating = true;
  state.analysisQueue = ["florence"];
  state.analyzers.florence.available = true;
  state.analyzers.florence.status = "running";

  state = reduceState(state, { type: "WORKSPACE_REQUESTS_CANCELLED" });

  assert.equal(state.textGenerating, false);
  assert.equal(state.translatingPending, false);
  assert.equal(state.regeneratingBlockId, "");
  assert.equal(state.batchRegenerating, false);
  assert.deepEqual(state.analysisQueue, []);
  assert.equal(state.analyzers.florence.status, "idle");
  assert.equal(state.generationProgress.status, "idle");

  const source = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");
  assert.match(source, /hideTagSuggest\(\);\s*dispatch\(\{ type: "WORKSPACE_REQUESTS_CANCELLED" \}\)/);
});

test("workspace replacement asks before discarding meaningful unsaved work", () => {
  let state = createInitialState();
  assert.equal(shouldConfirmWorkspaceDiscard(state), false);

  state = reduceState(state, { type: "NAVIGATE", view: "text" });
  assert.equal(shouldConfirmWorkspaceDiscard(state), false);

  state = reduceState(state, {
    type: "SET_PROJECT_NAME",
    value: "Named draft",
  });
  assert.equal(shouldConfirmWorkspaceDiscard(state), true);

  state = createGeneratedState();
  assert.equal(shouldConfirmWorkspaceDiscard(state), true);
  state = reduceState(state, {
    type: "UPDATE_BLOCK",
    id: "pose",
    language: "en",
    value: "pending edit",
  });
  assert.equal(shouldConfirmWorkspaceDiscard(state), true);
});

test("version payload uses the persisted base and never serializes pending block edits", () => {
  let state = createPersistedGeneratedState();
  const savedPose = state.appliedBlocks.find((block) => block.id === "pose").en;
  state = reduceState(state, {
    type: "UPDATE_BLOCK",
    id: "pose",
    language: "en",
    value: "pending pose that must not be saved",
  });

  const payload = buildVersionPayload(state);
  assert.equal(payload.baseVersion, 1);
  assert.equal(
    payload.blocks.find((block) => block.id === "pose").en,
    savedPose
  );
  assert.equal("dirtyBlockIds" in payload.metadata, false);
});

test("server save response is authoritative and does not clear newer work", () => {
  let state = createGeneratedState();
  const savedRevision = state.workingRevision;
  const payload = buildVersionPayload(state);
  state = reduceState(state, {
    type: "UPDATE_BLOCK",
    id: "pose",
    language: "en",
    value: "newer in-flight edit",
  });
  state = reduceState(state, { type: "APPLY_CHANGES" });
  state = reduceState(state, {
    type: "PROJECT_SAVED",
    savedRevision,
    item: {
      id: "version-7",
      version: 7,
      ...payload,
      createdAt: "2026-07-16T10:00:00+00:00",
    },
  });

  assert.equal(state.version, 7);
  assert.equal(state.versionHistory.at(-1).version, 7);
  assert.equal(state.hasUnsavedChanges, true);
  assert.match(state.output.positiveEn, /newer in-flight edit/);
});

test("editing project metadata during a save remains visibly unsaved", () => {
  let state = createPersistedGeneratedState();
  const savedProjectRevision = state.projectRevision;
  state = reduceState(state, { type: "PROJECT_SAVE_STARTED" });
  state = reduceState(state, {
    type: "SET_PROJECT_NAME",
    value: "Name typed while saving",
  });
  assert.equal(state.saveStatus, "saving");

  state = reduceState(state, {
    type: "PROJECT_HEADER_SAVED",
    savedProjectRevision,
    updatedAt: "2026-07-16T10:00:00+00:00",
  });
  assert.equal(state.hasUnsavedProjectChanges, true);
  assert.equal(state.saveStatus, "idle");
  assert.match(state.toast, /当前修改仍待保存/);
});

test("late first-create response preserves a newer project name", () => {
  let state = createInitialState();
  state = reduceState(state, { type: "SET_PROJECT_NAME", value: "A" });
  const savedProjectRevision = state.projectRevision;
  state = reduceState(state, { type: "SET_PROJECT_NAME", value: "B" });

  state = reduceState(state, {
    type: "PROJECT_CREATED",
    item: {
      id: "project-new",
      name: "A",
      status: "draft",
      updatedAt: "2026-07-16T09:00:00.000001+00:00",
    },
    savedProjectRevision,
  });

  assert.equal(state.projectId, "project-new");
  assert.equal(state.projectUpdatedAt, "2026-07-16T09:00:00.000001+00:00");
  assert.equal(state.projectName, "B");
  assert.equal(state.hasUnsavedProjectChanges, true);
});

test("project browser exposes four states and guards duplicate or stale requests", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const source = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "..", "styles.css"), "utf8");

  assert.match(html, /id=["']recentProjectList["']/);
  assert.match(html, /id=["']recentProjectStatus["']/);
  assert.match(html, /id=["']projectNameInput["']/);
  assert.match(html, /id=["']projectSaveStatus["']/);
  assert.match(source, /if \(saveInFlight\) return saveInFlight/);
  assert.match(source, /作品正在保存，请等待完成后再切换/);
  assert.match(source, /window\.confirm\("当前作品有未保存内容/);
  assert.match(source, /requestId !== projectOpenRequestId/);
  assert.match(source, /requestId !== projectListRequestId/);
  assert.match(source, /state\.workingRevision !== openingWorkingRevision/);
  assert.match(source, /state\.projectRevision !== openingProjectRevision/);
  assert.match(source, /advanceWorkspaceSession\(\)/);
  assert.match(source, /isCurrentWorkspaceRequest\(request\)/);
  assert.match(source, /addEventListener\("beforeunload"/);
  assert.match(source, /仍有未应用的结构块修改/);
  assert.match(css, /\.recent-project-list/);
  assert.match(css, /\.project-save-panel/);
});

test("imports a reference into prompt expansion without mutating the library", () => {
  const state = createInitialState();
  const originalTitle = state.references[0].title;
  const next = reduceState(state, { type: "IMPORT_REFERENCE", id: "rain-city" });

  assert.equal(next.view, "text");
  assert.equal(next.textMode, "expand");
  assert.match(next.draftInput, /1girl/);
  assert.equal(state.references[0].title, originalTitle);
  assert.equal(next.references[0].title, originalTitle);
});

test("runs selected image analyzers in a serial queue", () => {
  const state = createInitialState();
  const running = reduceState(state, { type: "START_ANALYSIS" });

  assert.deepEqual(running.analysisQueue, ["florence"]);
  assert.equal(running.analyzers.florence.status, "running");
  assert.equal(running.analyzers.wd14.available, false);
});

test("applies installed vision model status from the backend", () => {
  const state = reduceState(createInitialState(), {
    type: "APPLY_VISION_STATUS",
    models: {
      florence: { installed: true, label: "已安装", missing: [] },
      wd14: { installed: true, label: "已安装", missing: [] },
      joycaption: { installed: true, label: "已安装", missing: [] },
      qwenvl: { installed: true, label: "已安装", missing: [] },
      external: {
        installed: false,
        label: "尚未配置",
        missing: ["external vision API configuration"],
      },
    },
  });

  assert.equal(state.analyzers.wd14.available, true);
  assert.equal(state.analyzers.joycaption.available, true);
  assert.equal(state.analyzers.qwenvl.available, true);
  assert.equal(state.analyzers.external.available, false);
});

test("applies real image analysis returned by the backend", () => {
  let state = createInitialState();
  state = reduceState(state, { type: "START_ANALYSIS" });
  state = reduceState(state, {
    type: "APPLY_IMAGE_ANALYSIS",
    item: {
      positiveEn: "multiple girls, pink hair, brown dresses",
      positiveZh: "多名女性角色，粉色头发，棕色连衣裙",
      negativeEn: "",
      negativeZh: "",
      relationEn: "",
      relationZh: "",
      blocks: [
        {
          id: "subject",
          label: "主体与角色",
          en: "multiple girls",
          zh: "多名女性角色",
          source: "Florence + 本地 LLM",
        },
      ],
      rawResults: {
        florence: "multiple girls, pink hair, brown dresses",
      },
      analyzers: [
        {
          id: "florence",
          status: "ready",
          raw: "multiple girls, pink hair, brown dresses",
        },
        {
          id: "joycaption",
          status: "error",
          error: "CUDA 显存不足",
        },
      ],
      checks: {},
    },
  });

  assert.equal(state.analysisComplete, true);
  assert.equal(state.analyzers.florence.status, "ready");
  assert.match(state.analyzers.florence.raw, /multiple girls/);
  assert.match(state.output.positiveEn, /pink hair/);
  assert.equal(state.analyzers.joycaption.status, "error");
  assert.match(state.analyzers.joycaption.error, /显存不足/);
  assert.equal(state.blocks[0].source, "Florence + 本地 LLM");
  assert.equal(state.version, 0);
  assert.equal(state.hasUnsavedChanges, true);
});

test("failed image analysis never applies a fixed demo result", () => {
  let state = createInitialState();
  state = reduceState(state, { type: "START_ANALYSIS" });
  state = reduceState(state, {
    type: "IMAGE_ANALYSIS_FAILED",
    error: "Florence 识图失败",
  });

  assert.equal(state.analysisComplete, false);
  assert.equal(state.version, 0);
  assert.equal(state.output.positiveEn, "");
  assert.match(state.analysisNotice, /Florence 识图失败/);
});

test("releases every loaded image model", () => {
  const state = createInitialState();
  const loaded = {
    ...state,
    analyzers: Object.fromEntries(
      Object.entries(state.analyzers).map(([id, model]) => [
        id,
        { ...model, status: "ready" },
      ])
    ),
  };
  const released = reduceState(loaded, { type: "RELEASE_MODELS" });

  assert.ok(
    Object.values(released.analyzers).every(
      (model) =>
        model.status === (model.available === false ? "unavailable" : "idle")
    )
  );
});

test("accepts only supported local image files", () => {
  assert.equal(
    isSupportedImageFile({ name: "reference.png", type: "image/png" }),
    true
  );
  assert.equal(
    isSupportedImageFile({ name: "reference.webp", type: "" }),
    true
  );
  assert.equal(
    isSupportedImageFile({ name: "notes.txt", type: "text/plain" }),
    false
  );
  assert.equal(
    isSupportedImageFile({ name: "reference.gif", type: "image/gif" }),
    false
  );
});

test("loading an image records file metadata and enables analysis", () => {
  const state = reduceState(createInitialState(), {
    type: "SET_IMAGE",
    name: "reference.webp",
    mimeType: "image/webp",
    size: 313214,
  });

  assert.equal(state.view, "image");
  assert.equal(state.imageLoaded, true);
  assert.equal(state.imageName, "reference.webp");
  assert.equal(state.imageMimeType, "image/webp");
  assert.equal(state.imageSize, 313214);
  assert.match(state.analysisNotice, /图片已载入/);
});

test("applies a real bilingual expansion result from the backend", () => {
  let state = createInitialState();
  state = reduceState(state, {
    type: "SET_DRAFT",
    value: "1girl, raining，霓虹街道",
  });
  state = reduceState(state, { type: "START_TEXT_EXPANSION" });
  state = reduceState(state, {
    type: "APPLY_TEXT_EXPANSION",
    item: {
      positiveEn: "masterpiece, best quality, score_7, 1girl, rain",
      positiveZh: "杰作，最佳质量，一名女性角色，雨景",
      negativeEn: "worst quality, low quality",
      negativeZh: "最差质量，低质量",
      relationEn: "She stands in the foreground beneath the rain.",
      relationZh: "她站在雨中的前景。",
      blocks: [
        {
          id: "quality",
          label: "质量与基础修饰",
          en: "masterpiece, best quality, score_7",
          zh: "杰作，最佳质量，score_7",
          weight: 100,
          locked: false,
          source: "model_profile",
          confidence: 100,
        },
        {
          id: "artist",
          label: "画师与风格",
          en: "",
          zh: "",
          weight: 100,
          locked: false,
          source: "expanded",
          confidence: 100,
        },
        ...["subject", "appearance", "pose", "scene", "composition", "lighting", "effects", "negative"].map((id) => ({
          id,
          label: id,
          en: `${id} en`,
          zh: `${id} zh`,
          weight: 100,
          locked: false,
          source: "expanded",
          confidence: 90,
        })),
      ],
      checks: {
        preservedUserIntent: true,
        bilingualAligned: true,
        conflicts: [],
        assumptions: [],
      },
    },
  });

  assert.match(state.output.positiveEn, /1girl/);
  assert.match(state.output.positiveZh, /女性角色/);
  assert.equal(state.blocks.length, 10);
  assert.equal(state.version, 0);
  assert.equal(state.hasUnsavedChanges, true);
  assert.equal(state.textGenerating, false);
  assert.match(getCombinedPrompt(state), /rain/);
});

test("loading and failure states never create a fake prompt version", () => {
  let state = createInitialState();
  state = reduceState(state, { type: "START_TEXT_EXPANSION" });

  assert.equal(state.textGenerating, true);
  assert.equal(state.version, 0);
  assert.equal(state.output.positiveEn, "");

  state = reduceState(state, {
    type: "TEXT_EXPANSION_FAILED",
    error: "无法连接文本模型",
  });

  assert.equal(state.textGenerating, false);
  assert.equal(state.version, 0);
  assert.equal(state.output.positiveEn, "");
  assert.match(state.toast, /无法连接文本模型/);
});

test("lossless decomposition has independent loading actions", () => {
  let state = createInitialState();
  state = reduceState(state, {
    type: "START_TEXT_DECOMPOSITION",
    startedAt: 1000,
  });

  assert.equal(state.textDecomposing, true);
  assert.equal(state.textGenerating, false);
  assert.equal(state.version, 0);
  assert.equal(state.generationProgress.status, "running");
  assert.equal(state.generationProgress.task, "decompose");
  assert.equal(state.generationProgress.startedAt, 1000);
  assert.match(state.generationProgress.detail, /等待模型生成与服务端校验/);

  state = reduceState(state, {
    type: "TEXT_DECOMPOSITION_FAILED",
    error: "原文覆盖校验失败",
    finishedAt: 5500,
  });

  assert.equal(state.textDecomposing, false);
  assert.equal(state.version, 0);
  assert.match(state.toast, /原文覆盖校验失败/);
  assert.equal(state.generationProgress.status, "error");
  assert.equal(state.generationProgress.elapsedSeconds, 4.5);
  assert.match(state.generationProgress.detail, /原文覆盖校验失败/);
});

test("applies a verified decomposition through the existing editor state", () => {
  const item = {
    positiveEn: "1girl, raining",
    positiveZh: "一名女性角色；下雨",
    negativeEn: "",
    negativeZh: "",
    relationEn: "",
    relationZh: "",
    blocks: [
      {
        id: "subject",
        label: "主体与角色",
        en: "1girl",
        zh: "一名女性角色",
        weight: 100,
        locked: false,
        source: "本地 LLM · 仅拆解",
        confidence: 95,
      },
      {
        id: "scene",
        label: "场景与环境",
        en: "raining",
        zh: "下雨",
        weight: 100,
        locked: false,
        source: "本地 LLM · 仅拆解",
        confidence: 95,
      },
    ],
    checks: {
      preservedUserIntent: true,
      bilingualAligned: true,
      conflicts: [],
      assumptions: [],
      sourceCoverage: 100,
    },
  };

  let state = reduceState(createInitialState(), {
    type: "START_TEXT_DECOMPOSITION",
    startedAt: 1000,
  });
  state = reduceState(state, {
    type: "APPLY_TEXT_DECOMPOSITION",
    item,
    finishedAt: 4200,
  });

  assert.equal(state.textDecomposing, false);
  assert.equal(state.version, 0);
  assert.equal(state.hasUnsavedChanges, true);
  assert.equal(state.blocks.length, 2);
  assert.equal(state.output.positiveEn, "1girl, raining");
  assert.equal(state.generationProgress.status, "success");
  assert.equal(state.generationProgress.elapsedSeconds, 3.2);
  assert.match(state.generationProgress.detail, /结构块与双语提示词已更新/);
});

test("text expansion reports a visible progress lifecycle", () => {
  let state = createInitialState();
  state = reduceState(state, {
    type: "START_TEXT_EXPANSION",
    startedAt: 2000,
  });

  assert.equal(state.generationProgress.status, "running");
  assert.equal(state.generationProgress.task, "expand");
  assert.match(state.generationProgress.title, /拓展并结构化/);

  state = reduceState(state, {
    type: "TEXT_EXPANSION_FAILED",
    error: "模型连接超时",
    finishedAt: 9000,
  });

  assert.equal(state.generationProgress.status, "error");
  assert.equal(state.generationProgress.elapsedSeconds, 7);
  assert.equal(state.generationProgress.detail, "模型连接超时");
});

test("model-driven random generation has an explicit loading lifecycle", () => {
  let state = reduceState(createInitialState(), {
    type: "START_TEXT_RANDOM",
    startedAt: 1000,
  });

  assert.equal(state.textGenerating, true);
  assert.equal(state.textMode, "random");
  assert.equal(state.generationProgress.task, "random");
  assert.match(state.generationProgress.title, /随机/);

  state = reduceState(state, {
    type: "APPLY_TEXT_RANDOM",
    item: {
      positiveEn: "a detailed model-generated random prompt",
      positiveZh: "模型生成的详细随机提示词",
      negativeEn: "low quality",
      negativeZh: "低质量",
      relationEn: "A coherent relation.",
      relationZh: "连贯的关系描述。",
      blocks: [],
      checks: {},
    },
    finishedAt: 3000,
  });

  assert.equal(state.textGenerating, false);
  assert.match(state.output.positiveEn, /model-generated/);
  assert.equal(state.generationProgress.status, "success");
});

test("full random calls the real model endpoint and has no static prompt generator", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

  assert.match(source, /\/api\/text\/random/);
  assert.match(source, /function randomizeTextPrompt/);
  assert.doesNotMatch(source, /function randomOutput/);
});

test("static prototype exposes every major review surface", () => {
  const htmlPath = path.join(__dirname, "..", "index.html");
  const html = fs.readFileSync(htmlPath, "utf8");

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
  const unicodeDataIndex = html.indexOf("unicode15-data.js?v=20260716-unicode15");
  const appScriptIndex = html.indexOf("app.js?v=20260725-ai-local-edit-v1");
  assert.notEqual(unicodeDataIndex, -1);
  assert.notEqual(appScriptIndex, -1);
  assert.ok(unicodeDataIndex < appScriptIndex);
});

test("prototype targets a 1920x1080 desktop workspace without mobile navigation", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "..", "styles.css"), "utf8");

  assert.match(html, /data-target-viewport=["']1920x1080["']/);
  assert.doesNotMatch(html, /class=["'][^"']*mobile-nav/);
  assert.doesNotMatch(css, /@media\s*\(\s*max-width/);
  assert.match(css, /min-width:\s*1280px/);
});

test("random mode keeps its technical seed out of the user interface", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");

  assert.doesNotMatch(html, /id=["']randomSeed["']/);
  assert.doesNotMatch(html, />随机种子</);
});

test("switches the text generation provider for the current task", () => {
  const state = createInitialState();
  const next = reduceState(state, {
    type: "SET_TEXT_PROVIDER",
    value: "api",
  });

  assert.equal(state.settings.textProvider, "local");
  assert.equal(next.settings.textProvider, "api");
});

test("starts with editable local and external text model settings", () => {
  const state = createInitialState();

  assert.equal(state.settings.localTextUrl, "http://127.0.0.1:8080/v1");
  assert.equal(
    state.settings.localTextModel,
    "Huihui-Qwen3-14B-abliterated-v2.Q4_K_M.gguf"
  );
  assert.equal(state.settings.expansionLevel, "balanced");
  assert.equal(state.settings.apiTextUrl, "");
  assert.equal(state.settings.apiTextModel, "");
  assert.equal(state.settings.apiTextKey, "");
});

test("text model settings are exposed without a simulation label", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const textView = html.match(
    /<section id="textView">([\s\S]*?)<section id="imageView">/
  )?.[1] || "";

  for (const id of [
    "localTextUrl",
    "localTextModel",
    "apiTextUrl",
    "apiTextModel",
    "apiTextKey",
  ]) {
    assert.match(html, new RegExp(`id=["']${id}["']`));
  }
  assert.doesNotMatch(textView, /演示模拟/);
});

test("text expansion exposes strict balanced and creative levels", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");

  assert.match(html, /data-expansion-level="strict"/);
  assert.match(html, /data-expansion-level="balanced"/);
  assert.match(html, /data-expansion-level="creative"/);
});

test("complete prompts expose a separate lossless decomposition action", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

  assert.match(html, /id=["']decomposeBtn["']/);
  assert.match(html, />\s*仅拆解\s*</);
  assert.match(html, /id=["']expandBtn["']/);
  assert.match(html, />\s*拓展并结构化\s*</);
  assert.match(app, /\/api\/text\/decompose/);
});

test("text actions expose a persistent generation progress panel", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");

  for (const id of [
    "generationProgress",
    "generationProgressTitle",
    "generationProgressDetail",
    "generationProgressProvider",
    "generationProgressElapsed",
  ]) {
    assert.match(html, new RegExp(`id=["']${id}["']`));
  }
});

test("structured editor labels real model regeneration as random variants", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

  assert.match(html, /\/api\/text\/regenerate-block/);
  assert.match(html, /↻ 随机变体/);
  assert.doesNotMatch(html, /type: "REGENERATE_BLOCK"/);
});

test("browser bootstrap receives the random-variant block configuration", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");
  const browserBootstrap = source.split("(function bootstrapBrowser")[1] || "";

  assert.match(
    browserBootstrap,
    /const RANDOM_VARIANT_BLOCKS = new Set\(app\.randomVariantBlockIds\);/
  );
});

test("model drawer exposes local lifecycle and external API test controls", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");

  for (const action of [
    "start-local-llm",
    "stop-local-llm",
    "test-local-llm",
    "test-external-api",
  ]) {
    assert.match(html, new RegExp(`data-action=["']${action}["']`));
  }
  assert.match(html, /id=["']localLlmStatus["']/);
  assert.match(html, /id=["']externalApiStatus["']/);
});

test("closed model drawer cannot block workbench controls", () => {
  const css = fs.readFileSync(path.join(__dirname, "..", "styles.css"), "utf8");

  assert.match(
    css,
    /\.model-drawer\s*\{[^}]*pointer-events:\s*none;[^}]*\}/
  );
  assert.match(
    css,
    /\.model-drawer\.open\s*\{[^}]*pointer-events:\s*auto;[^}]*\}/
  );
});

test("creates a reusable resource in the personal library", () => {
  const state = createInitialState();
  const next = reduceState(state, {
    type: "CREATE_RESOURCE",
    resource: {
      id: "long-term-coat",
      type: "snippets",
      name: "长期使用的黑色风衣",
      meta: "服装",
      targetBlock: "appearance",
      en: "long black trench coat, layered dark clothing",
      zh: "黑色长风衣、层叠深色服装",
    },
  });

  assert.equal(state.resources.snippets.length, 0);
  assert.equal(next.resources.snippets.length, 1);
  assert.equal(next.resources.snippets[0].name, "长期使用的黑色风衣");
});

test("applies an AnimaDex character to subject and appearance together", () => {
  const state = createInitialState();
  state.resources.characters.push({
    id: "animadex-characters-hatsune-miku",
    type: "characters",
    name: "Hatsune Miku",
    source: "AnimaDex",
    targetBlock: "subject",
    en: "hatsune miku, vocaloid",
    zh: "初音未来",
    blocks: [
      {
        id: "subject",
        en: "hatsune miku, vocaloid",
        zh: "初音未来，VOCALOID",
      },
      {
        id: "appearance",
        en: "aqua hair, twintails, aqua eyes",
        zh: "水蓝色双马尾，水蓝色眼睛",
      },
    ],
  });

  const next = reduceState(state, {
    type: "APPLY_RESOURCE",
    id: "animadex-characters-hatsune-miku",
  });

  assert.equal(
    next.blocks.find((block) => block.id === "subject").en,
    "hatsune miku, vocaloid"
  );
  assert.equal(
    next.blocks.find((block) => block.id === "appearance").en,
    "aqua hair, twintails, aqua eyes"
  );
  assert.deepEqual(next.dirtyBlockIds, ["subject", "appearance"]);
});

test("editing a block stays pending until changes are applied", () => {
  let state = createGeneratedState();
  const originalOutput = state.output.positiveEn;

  state = reduceState(state, {
    type: "UPDATE_BLOCK",
    id: "pose",
    language: "en",
    value: "crouching by the roadside, tying her shoelaces",
  });

  assert.equal(state.output.positiveEn, originalOutput);
  assert.deepEqual(state.dirtyBlockIds, ["pose"]);
  assert.equal(state.version, 0);
});

test("applying block edits recompiles output and marks the next server version pending", () => {
  let state = createGeneratedState();
  state = reduceState(state, {
    type: "UPDATE_BLOCK",
    id: "pose",
    language: "en",
    value: "crouching by the roadside, tying her shoelaces",
  });
  state = reduceState(state, { type: "APPLY_CHANGES" });

  assert.equal(state.version, 0);
  assert.equal(state.dirtyBlockIds.length, 0);
  assert.match(state.output.positiveEn, /tying her shoelaces/);
  assert.doesNotMatch(state.output.positiveEn, /She stands in the rainy city/);
  assert.equal(state.output.relationEn, "");
  assert.equal(state.output.relationZh, "");
  assert.equal(state.versionHistory.length, 0);
  assert.equal(state.hasUnsavedChanges, true);
  assert.match(state.toast, /保存后生成 V1/);
});

test("discarding pending edits restores the last applied blocks", () => {
  let state = createGeneratedState();
  const appliedPose = state.blocks.find((block) => block.id === "pose").en;
  state = reduceState(state, {
    type: "UPDATE_BLOCK",
    id: "pose",
    language: "en",
    value: "temporary pose",
  });
  state = reduceState(state, { type: "DISCARD_CHANGES" });

  assert.equal(
    state.blocks.find((block) => block.id === "pose").en,
    appliedPose
  );
  assert.equal(state.dirtyBlockIds.length, 0);
});

test("applies a model-generated block variant as a pending edit", () => {
  let state = createGeneratedState();
  const originalOutput = state.output.positiveEn;
  const originalScene = state.blocks.find((block) => block.id === "scene").en;
  const originalPose = state.blocks.find((block) => block.id === "pose").en;

  state = reduceState(state, {
    type: "APPLY_BLOCK_VARIANT",
    item: {
      id: "pose",
      en: "walking through shallow rainwater, looking over one shoulder",
      zh: "走过浅浅雨水，回头望向身后",
      source: "外部 API · 随机变体",
      confidence: 88,
    },
  });

  const pose = state.blocks.find((block) => block.id === "pose");
  assert.notEqual(pose.en, originalPose);
  assert.ok(pose.zh.length > 0);
  assert.equal(state.blocks.find((block) => block.id === "scene").en, originalScene);
  assert.equal(state.output.positiveEn, originalOutput);
  assert.deepEqual(state.dirtyBlockIds, ["pose"]);
  assert.equal(state.version, 0);
});

test("does not apply a variant to a locked structured block", () => {
  let state = createGeneratedState();
  const originalSubject = state.blocks.find((block) => block.id === "subject").en;

  state = reduceState(state, {
    type: "APPLY_BLOCK_VARIANT",
    item: {
      id: "subject",
      en: "different subject",
      zh: "不同主体",
    },
  });

  assert.equal(
    state.blocks.find((block) => block.id === "subject").en,
    originalSubject
  );
  assert.equal(state.dirtyBlockIds.length, 0);
});

test("pending translation updates only placeholders without creating a version", () => {
  let state = createGeneratedState();
  const subject = state.blocks.find((block) => block.id === "subject");
  const appliedSubject = state.appliedBlocks.find(
    (block) => block.id === "subject"
  );
  const appearanceZh = state.blocks.find(
    (block) => block.id === "appearance"
  ).zh;
  subject.en = "Hatsune Miku";
  subject.zh = "待本地 LLM 翻译：Hatsune Miku";
  appliedSubject.en = subject.en;
  appliedSubject.zh = subject.zh;
  const originalVersion = state.version;

  state = reduceState(state, { type: "START_PENDING_TRANSLATION" });
  assert.equal(state.translatingPending, true);

  state = reduceState(state, {
    type: "APPLY_PENDING_TRANSLATIONS",
    item: {
      translations: [{ id: "subject", zh: "初音未来" }],
    },
  });

  assert.equal(state.translatingPending, false);
  assert.equal(
    state.blocks.find((block) => block.id === "subject").zh,
    "初音未来"
  );
  assert.equal(
    state.appliedBlocks.find((block) => block.id === "subject").zh,
    "初音未来"
  );
  assert.equal(
    state.blocks.find((block) => block.id === "appearance").zh,
    appearanceZh
  );
  assert.match(state.output.positiveZh, /初音未来/);
  assert.equal(state.version, originalVersion);
  assert.equal(state.versionHistory.length, 0);
  assert.equal(state.hasUnsavedChanges, true);
});

test("pending translation failure preserves the current prompt", () => {
  let state = createGeneratedState();
  state.blocks[0].zh = "待本地 LLM 翻译：masterpiece";
  const before = JSON.stringify({
    blocks: state.blocks,
    appliedBlocks: state.appliedBlocks,
    output: state.output,
    version: state.version,
  });

  state = reduceState(state, { type: "START_PENDING_TRANSLATION" });
  state = reduceState(state, {
    type: "PENDING_TRANSLATION_FAILED",
    error: "本地模型翻译失败",
  });

  assert.equal(state.translatingPending, false);
  assert.equal(
    JSON.stringify({
      blocks: state.blocks,
      appliedBlocks: state.appliedBlocks,
      output: state.output,
      version: state.version,
    }),
    before
  );
  assert.match(state.toast, /本地模型翻译失败/);
});

test("selects only eligible unlocked blocks for joint randomization", () => {
  let state = createGeneratedState();
  state = reduceState(state, {
    type: "TOGGLE_VARIANT_SELECTION",
    id: "scene",
  });
  state = reduceState(state, {
    type: "TOGGLE_VARIANT_SELECTION",
    id: "quality",
  });
  state = reduceState(state, {
    type: "TOGGLE_VARIANT_SELECTION",
    id: "subject",
  });

  assert.deepEqual(state.selectedVariantBlockIds, ["scene"]);
});

test("applies coordinated block variants as pending edits", () => {
  let state = createGeneratedState();
  const originalOutput = state.output.positiveEn;
  state = reduceState(state, {
    type: "TOGGLE_VARIANT_SELECTION",
    id: "scene",
  });
  state = reduceState(state, {
    type: "TOGGLE_VARIANT_SELECTION",
    id: "lighting",
  });
  state = reduceState(state, { type: "START_BLOCKS_VARIANT" });
  state = reduceState(state, {
    type: "APPLY_BLOCKS_VARIANT",
    item: {
      items: [
        {
          id: "scene",
          en: "covered station platform",
          zh: "有顶棚的车站站台",
          source: "本地 LLM · 联合随机",
        },
        {
          id: "lighting",
          en: "warm train windows, cool ambient rain light",
          zh: "暖色车窗灯光，冷色雨天环境光",
          source: "本地 LLM · 联合随机",
        },
      ],
    },
  });

  assert.equal(state.batchRegenerating, false);
  assert.deepEqual(state.selectedVariantBlockIds, []);
  assert.deepEqual(state.dirtyBlockIds.sort(), ["lighting", "scene"]);
  assert.equal(state.output.positiveEn, originalOutput);
  assert.match(state.blocks.find((block) => block.id === "scene").en, /station/);
});

test("structured editor exposes multi-select joint randomization controls", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

  assert.match(html, /id=["']batchVariantBar["']/);
  assert.match(html, /id=["']batchVariantBtn["']/);
  assert.match(app, /data-select-variant-block/);
  assert.match(app, /\/api\/text\/regenerate-blocks/);
  assert.match(app, /联合随机/);
});

test("output panel exposes local LLM pending translation controls", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

  assert.match(html, /data-action=["']translate-pending["']/);
  assert.match(html, /本地 LLM 翻译/);
  assert.match(app, /\/api\/text\/translate-pending/);
  assert.match(app, /START_PENDING_TRANSLATION/);
});

test("image upload creates and renders a local preview", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

  assert.match(html, /id=["']imagePreview["']/);
  assert.match(app, /URL\.createObjectURL/);
  assert.match(app, /URL\.revokeObjectURL/);
  assert.match(app, /function loadImageFile/);
  assert.match(app, /renderImageUpload/);
});

test("image analysis sends the uploaded file to the real vision endpoint", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

  assert.match(html, /真实本地识图/);
  assert.doesNotMatch(html, /演示模拟/);
  assert.match(app, /\/api\/vision\/analyze/);
  assert.match(app, /fileToBase64/);
  assert.match(app, /APPLY_IMAGE_ANALYSIS/);
  assert.doesNotMatch(app, /data\.mergedImageResult/);
  assert.doesNotMatch(app, /startAnalysisSimulation/);
});

test("dynamic HTML rendering escapes model and user provided text", () => {
  const app = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

  // 结构块 textarea 必须转义,防止 LLM 输出破坏 DOM 或注入脚本
  assert.match(app, />\$\{escapeHtml\(block\.en\)\}<\/textarea>/);
  assert.match(app, />\$\{escapeHtml\(block\.zh\)\}<\/textarea>/);
  assert.doesNotMatch(app, />\$\{block\.(?:en|zh)\}<\/textarea>/);

  // 分析器错误文本、参考卡片、结构块标题不允许未转义直插
  assert.doesNotMatch(app, /\$\{model\.error \|\| model\.detail\}/);
  assert.doesNotMatch(app, /<strong>\$\{block\.label\}<\/strong>/);
  assert.doesNotMatch(app, /<p>\$\{item\.promptZh\}<\/p>/);
  assert.match(app, /\$\{escapeHtml\(model\.error \|\| model\.detail\)\}/);
});

test("api helper survives non-JSON error responses", () => {
  const app = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");
  assert.match(app, /服务返回了无法解析的响应/);
  assert.doesNotMatch(app, /const payload = await response\.json\(\);\s*\n\s*if \(!response\.ok\)/);
});

test("long-running requests are cancellable via AbortController", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

  assert.match(html, /id=["']cancelGenerationBtn["']/);
  assert.match(html, /id=["']cancelAnalysisBtn["']/);
  assert.match(app, /new AbortController\(\)/);
  assert.match(app, /const workspaceRequestAborts = new Set\(\)/);
  assert.match(app, /signal: request\.controller\.signal/);
  assert.match(app, /for \(const controller of workspaceRequestAborts\) controller\.abort\(\)/);
  assert.match(app, /isAbortError/);
  assert.match(app, /已取消图片分析/);
});

test("reference library renders an empty state when nothing matches", () => {
  const app = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "..", "styles.css"), "utf8");
  assert.match(app, /reference-empty/);
  assert.match(css, /\.reference-empty/);
});

test("editing a block produces a live preview without touching the version", () => {
  let state = createGeneratedState();
  const originalOutput = state.output.positiveEn;

  state = reduceState(state, {
    type: "UPDATE_BLOCK",
    id: "pose",
    language: "en",
    value: "kneeling in shallow water",
  });

  assert.equal(state.output.positiveEn, originalOutput);
  assert.ok(state.previewOutput);
  assert.match(state.previewOutput.positiveEn, /kneeling in shallow water/);
  assert.equal(state.version, 0);

  state = reduceState(state, { type: "APPLY_CHANGES" });
  assert.equal(state.previewOutput, null);
  assert.match(state.output.positiveEn, /kneeling in shallow water/);
});

test("every pending block mutation advances the workspace revision guard", () => {
  let state = createGeneratedState();
  const initialRevision = state.workingRevision;

  state = reduceState(state, {
    type: "UPDATE_BLOCK",
    id: "pose",
    language: "en",
    value: "first pending edit",
  });
  assert.equal(state.workingRevision, initialRevision + 1);

  state = reduceState(state, {
    type: "UPDATE_BLOCK",
    id: "pose",
    language: "en",
    value: "second pending edit",
  });
  assert.equal(state.workingRevision, initialRevision + 2);
  assert.deepEqual(state.dirtyBlockIds, ["pose"]);
});

test("async workspace results expire after either content revision changes", () => {
  let state = createGeneratedState();
  const request = {
    sessionId: 7,
    workingRevision: state.workingRevision,
    projectRevision: state.projectRevision,
  };
  assert.equal(isWorkspaceRequestCurrent(request, 7, state), true);

  state = reduceState(state, {
    type: "UPDATE_BLOCK",
    id: "pose",
    language: "en",
    value: "user edit after request start",
  });
  assert.equal(isWorkspaceRequestCurrent(request, 7, state), false);

  const draftRequest = {
    sessionId: 7,
    workingRevision: state.workingRevision,
    projectRevision: state.projectRevision,
  };
  state = reduceState(state, { type: "SET_DRAFT", value: "newer draft" });
  assert.equal(isWorkspaceRequestCurrent(draftRequest, 7, state), false);
  assert.equal(isWorkspaceRequestCurrent(draftRequest, 8, state), false);
});

test("combined prompt uses the live preview while edits are pending", () => {
  const { getCombinedPrompt } = require("../app.js");
  let state = createGeneratedState();

  state = reduceState(state, {
    type: "UPDATE_BLOCK",
    id: "pose",
    language: "en",
    value: "balancing on a glass bridge",
  });

  assert.match(getCombinedPrompt(state), /balancing on a glass bridge/);
  assert.doesNotMatch(getCombinedPrompt(state), /pose en/);
});

test("copy buttons read the same live output shown in the panel", () => {
  const app = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

  assert.match(app, /state\.previewOutput \|\| state\.output/);
  assert.doesNotMatch(app, /copyText\(state\.output\[button\.dataset\.copy\]\)/);
});

test("compilation deduplicates repeated items and applies block weight syntax", () => {
  let state = createGeneratedState();
  state = reduceState(state, {
    type: "UPDATE_BLOCK",
    id: "scene",
    language: "en",
    value: "misty harbor, neon lights",
  });
  state = reduceState(state, {
    type: "UPDATE_BLOCK",
    id: "effects",
    language: "en",
    value: "misty harbor, light bloom",
  });
  state = reduceState(state, {
    type: "SET_BLOCK_WEIGHT",
    id: "effects",
    value: 120,
  });
  state = reduceState(state, { type: "APPLY_CHANGES" });

  const occurrences = state.output.positiveEn.match(/misty harbor/g) || [];
  assert.equal(occurrences.length, 1);
  assert.match(state.output.positiveEn, /\(light bloom:1\.2\)/);
});

test("block weights are finite and constrained at every frontend ingress", () => {
  assert.equal(normalizeBlockWeight(-250), 0);
  assert.equal(normalizeBlockWeight(1_000_000), 120);
  assert.equal(normalizeBlockWeight("45"), 45);
  assert.equal(normalizeBlockWeight(Number.NaN), 100);
  assert.equal(normalizeBlockWeight(Number.POSITIVE_INFINITY), 100);
  assert.equal(normalizeBlockWeight("Infinity"), 100);
  assert.equal(normalizeBlockWeight(null), 100);

  const databaseRecipe = normalizeResourceWeights({
    id: "database-recipe",
    type: "snippets",
    weight: -1,
    blocks: [
      { id: "scene", weight: -250 },
      { id: "effects", weight: 1_000_000 },
      { id: "artist", weight: Number.NaN },
      { id: "pose", weight: "Infinity" },
    ],
  });
  assert.equal(databaseRecipe.weight, 0);
  assert.deepEqual(
    databaseRecipe.blocks.map((block) => block.weight),
    [0, 120, 100, 100]
  );

  let state = createGeneratedState();
  state = reduceState(state, {
    type: "SET_BLOCK_WEIGHT",
    id: "effects",
    value: Number.NaN,
  });
  assert.equal(state.blocks.find((block) => block.id === "effects").weight, 100);
  state = reduceState(state, {
    type: "SET_BLOCK_WEIGHT",
    id: "effects",
    value: 1_000_000,
  });
  assert.equal(state.blocks.find((block) => block.id === "effects").weight, 120);
});

test("hostile recipe weights are normalized before storage and application", () => {
  let state = createGeneratedState();
  const recipe = {
    id: "hostile-weight-recipe",
    type: "snippets",
    name: "异常权重配方",
    blocks: [
      { id: "scene", en: "scene", zh: "场景", weight: -250 },
      { id: "effects", en: "effects", zh: "效果", weight: 1_000_000 },
      { id: "artist", en: "artist", zh: "画师", weight: Number.NaN },
      { id: "pose", en: "pose", zh: "姿势", weight: "Infinity" },
    ],
  };

  state = reduceState(state, { type: "ADD_RECIPE", recipe });
  const stored = state.resources.snippets.find(
    (item) => item.id === "hostile-weight-recipe"
  );
  assert.deepEqual(
    stored.blocks.map((block) => block.weight),
    [0, 120, 100, 100]
  );

  state = reduceState(state, {
    type: "APPLY_RESOURCE",
    id: "hostile-weight-recipe",
  });
  assert.equal(state.blocks.find((block) => block.id === "scene").weight, 0);
  assert.equal(state.blocks.find((block) => block.id === "effects").weight, 120);
  assert.equal(state.blocks.find((block) => block.id === "artist").weight, 100);
  assert.equal(state.blocks.find((block) => block.id === "pose").weight, 100);
});

test("compilation cannot emit negative or oversized block weight factors", () => {
  const output = compileBlocks([
    { id: "scene", en: "hidden scene", zh: "隐藏场景", weight: -5 },
    { id: "effects", en: "light bloom", zh: "光晕", weight: 1_000_000 },
    { id: "quality", en: "masterpiece", zh: "杰作", weight: Number.NaN },
  ]);

  assert.doesNotMatch(output.positiveEn, /hidden scene/);
  assert.match(output.positiveEn, /\(light bloom:1\.2\)/);
  assert.match(output.positiveEn, /masterpiece/);
  assert.doesNotMatch(output.positiveEn, /:-|:10000/);
  assert.equal(
    compileBlockFragment({ id: "effects", en: "sparkles", weight: -1 }),
    ""
  );
});

test("artist mixer keeps its independent 10 to 150 percent range", () => {
  const mixed = composeArtistMix([
    { id: "artist-high", tag: "@artist_high", weight: 150 },
    { id: "artist-low", tag: "@artist_low", weight: 10 },
  ]);

  assert.equal(mixed, "(@artist_high:1.5), (@artist_low:0.1)");
});

test("Unicode prompt tags use NFKC, whitespace, and sharp-s deduplication", () => {
  const output = compileBlocks([
    {
      id: "scene",
      en: "Straße, Éclair, red  dress, \ufeffblue sky\ufeff",
      zh: "ＣＡＴ",
      weight: 100,
    },
    {
      id: "effects",
      en: "STRASSE, e\u0301clair, red dress, blue sky",
      zh: "CAT",
      weight: 100,
    },
  ]);

  assert.equal(canonicalizePromptItem("Straße"), "strasse");
  assert.equal(canonicalizePromptItem("Éclair"), canonicalizePromptItem("e\u0301clair"));
  assert.equal(canonicalizePromptItem("  red\t dress "), "red dress");
  assert.equal(canonicalizePromptItem("red\ufeffdress"), "red dress");
  assert.equal(canonicalizePromptItem("red\u0085dress"), "red dress");
  assert.equal(canonicalizePromptItem("red\u200bdress"), "red\u200bdress");
  assert.equal(canonicalizePromptItem("\u001cred\u001c"), "\u001cred\u001c");
  assert.equal(unicode15.unicodeVersion, "15.0.0");
  assert.equal(unicode15.isAssignedCodePoint(0x41), true);
  assert.equal(unicode15.isAssignedCodePoint(0x1c89), false);
  assert.equal(unicode15.lowercase("AΣẞ"), "aσß");
  assert.equal(canonicalizePromptItem("\u1c89"), "\u1c89");
  assert.equal(canonicalizePromptItem("\ua7f1"), "\ua7f1");
  assert.equal(canonicalizePromptItem("A\u0295Σ"), "a\u0295σ");
  assert.equal(canonicalizePromptItem("AΣ\u0295"), "aσ\u0295");
  assert.equal(output.positiveEn, "Straße, Éclair, red  dress, blue sky");
  assert.equal(output.positiveZh, "ＣＡＴ");
});

test("a zero-weight block is excluded from compiled output", () => {
  let state = createGeneratedState();
  state = reduceState(state, {
    type: "SET_BLOCK_WEIGHT",
    id: "effects",
    value: 0,
  });
  state = reduceState(state, { type: "APPLY_CHANGES" });
  assert.doesNotMatch(state.output.positiveEn, /effects en/);
});

test("negative blocks do not expose positive prompt weight controls", () => {
  const app = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

  assert.equal(
    compileBlockFragment({ id: "negative", en: "bad anatomy", weight: 0 }),
    ""
  );
  assert.match(app, /block\.id === "negative"\s*\?\s*""\s*:/);
});

test("unknown model statuses use a safe class and label", () => {
  const hostileStatus = '\"><img src=x onerror="alert(1)">';
  assert.equal(normalizeModelStatus("ready"), "ready");
  assert.equal(statusLabel("ready"), "已就绪");
  assert.equal(normalizeModelStatus(hostileStatus), "unknown");
  assert.equal(statusLabel(hostileStatus), "未知状态");

  let state = createInitialState();
  state = reduceState(state, {
    type: "APPLY_IMAGE_ANALYSIS",
    item: {
      analyzers: [{ id: "florence", status: hostileStatus }],
      blocks: [],
    },
  });
  assert.equal(state.analyzers.florence.status, "unknown");
});

test("project metadata excludes API keys and unrecognized settings", () => {
  const settings = {
    textProvider: "api",
    localTextUrl: "http://127.0.0.1:8080/v1",
    apiTextModel: "model-name",
    autoCombine: true,
    localTextKey: "local-secret",
    apiTextKey: "api-secret",
    futureSecret: "must-not-persist",
  };

  assert.deepEqual(projectSettingsMetadata(settings), {
    textProvider: "api",
    localTextUrl: "http://127.0.0.1:8080/v1",
    apiTextModel: "model-name",
    autoCombine: true,
  });
  assert.equal(settings.apiTextKey, "api-secret");

  const state = createInitialState();
  state.settings = settings;
  const payload = buildProjectPayload(state);
  assert.deepEqual(payload.metadata.settings, {
    textProvider: "api",
    localTextUrl: "http://127.0.0.1:8080/v1",
    apiTextModel: "model-name",
    autoCombine: true,
  });

  const source = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");
  assert.match(source, /settings: projectSettingsMetadata\(state\.settings\)/);
  assert.doesNotMatch(source, /settings: state\.settings/);
});

test("settings writes omit masked or blank keys but keep newly entered keys", () => {
  assert.deepEqual(
    settingsWritePayload({
      textProvider: "api",
      apiTextKey: "",
      apiTextKeyConfigured: true,
      localTextKey: "   ",
      localTextKeyConfigured: true,
    }),
    { textProvider: "api" }
  );
  assert.deepEqual(
    settingsWritePayload({
      textProvider: "api",
      apiTextKey: "replacement-key",
      apiTextKeyConfigured: true,
    }),
    { textProvider: "api", apiTextKey: "replacement-key" }
  );

  const source = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");
  assert.match(source, /已配置；留空保持不变/);
});

test("favorite persistence keeps the server-generated deletion id", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

  assert.match(source, /added\.favoriteId = saved\.item\.favoriteId/);
  assert.match(source, /removed\.favoriteId \|\| removed\.id/);
});

test("favorite mutations for the same resource are serialized", async () => {
  const queue = new Map();
  const events = [];
  let releaseFirst;
  const firstGate = new Promise((resolve) => {
    releaseFirst = resolve;
  });

  const first = enqueueByKey(queue, "artist-1", async () => {
    events.push("first:start");
    await firstGate;
    events.push("first:end");
  });
  const second = enqueueByKey(queue, "artist-1", async () => {
    events.push("second:start");
    events.push("second:end");
  });

  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(events, ["first:start"]);
  releaseFirst();
  await Promise.all([first, second]);
  assert.deepEqual(events, [
    "first:start",
    "first:end",
    "second:start",
    "second:end",
  ]);
  assert.equal(queue.size, 0);
});

test("a block variant can be compared then kept or reverted", () => {
  let state = createGeneratedState();
  const originalPose = state.blocks.find((block) => block.id === "pose").en;

  state = reduceState(state, {
    type: "APPLY_BLOCK_VARIANT",
    item: { id: "pose", en: "new pose", zh: "新姿势", source: "外部 API · 随机变体" },
  });
  let pose = state.blocks.find((block) => block.id === "pose");
  assert.equal(pose.pendingVariant.previousEn, originalPose);

  state = reduceState(state, { type: "REVERT_BLOCK_VARIANT", id: "pose" });
  pose = state.blocks.find((block) => block.id === "pose");
  assert.equal(pose.en, originalPose);
  assert.equal(pose.pendingVariant, undefined);
  assert.deepEqual(state.dirtyBlockIds, []);

  state = reduceState(state, {
    type: "APPLY_BLOCK_VARIANT",
    item: { id: "pose", en: "kept pose", zh: "保留姿势" },
  });
  state = reduceState(state, { type: "KEEP_BLOCK_VARIANT", id: "pose" });
  pose = state.blocks.find((block) => block.id === "pose");
  assert.equal(pose.en, "kept pose");
  assert.equal(pose.pendingVariant, undefined);
  assert.deepEqual(state.dirtyBlockIds, ["pose"]);
});

test("restoring an old version stays pending until the server creates a new version", () => {
  let state = createPersistedGeneratedState();
  const v1Pose = state.blocks.find((block) => block.id === "pose").en;
  state = reduceState(state, {
    type: "UPDATE_BLOCK",
    id: "pose",
    language: "en",
    value: "changed pose",
  });
  state = reduceState(state, { type: "APPLY_CHANGES" });
  assert.equal(state.version, 1);
  assert.equal(state.versionHistory.length, 1);
  state = applyServerSave(state, 2);
  assert.equal(state.version, 2);

  state = reduceState(state, { type: "RESTORE_VERSION", version: 1 });
  assert.equal(state.version, 2);
  assert.equal(state.blocks.find((block) => block.id === "pose").en, v1Pose);
  assert.equal(state.versionHistory.length, 2);
  assert.equal(state.restoreFromVersion, 1);
  assert.equal(state.hasUnsavedChanges, true);
  assert.equal(buildVersionPayload(state).baseVersion, 2);
  assert.equal(buildVersionPayload(state).metadata.restoredFromVersion, 1);

  state = applyServerSave(state, 3);
  assert.equal(state.version, 3);
  assert.equal(state.versionHistory.length, 3);
  assert.equal(state.versionHistory[2].restoredFrom, 1);
  assert.equal(state.restoreFromVersion, null);
  assert.equal(state.hasUnsavedChanges, false);
});

test("restoring history also restores its version metadata without mixing revisions", () => {
  let state = createGeneratedState();
  state.view = "image";
  state.textMode = "expand";
  state.draftInput = "v1 draft";
  state.randomSeed = 111;
  state.outputChecks = { marker: "v1" };
  state.imageName = "v1.png";
  state = reduceState(state, {
    type: "PROJECT_CREATED",
    item: { id: "project-test", name: "History", status: "draft" },
  });
  state = applyServerSave(state, 1);

  state.view = "text";
  state.textMode = "random";
  state.draftInput = "v2 draft";
  state.randomSeed = 222;
  state.outputChecks = { marker: "v2" };
  state.imageName = "v2.png";
  state = reduceState(state, {
    type: "UPDATE_BLOCK",
    id: "pose",
    language: "en",
    value: "v2 pose",
  });
  state = reduceState(state, { type: "APPLY_CHANGES" });
  state = applyServerSave(state, 2);

  state = reduceState(state, { type: "RESTORE_VERSION", version: 1 });
  const payload = buildVersionPayload(state);
  assert.equal(state.draftInput, "v1 draft");
  assert.equal(state.textMode, "expand");
  assert.equal(state.randomSeed, 111);
  assert.deepEqual(state.outputChecks, { marker: "v1" });
  assert.equal(state.imageName, "v1.png");
  assert.equal(state.view, "image");
  assert.equal(state.imageLoaded, false);
  assert.equal(state.analysisComplete, true);
  assert.equal(payload.metadata.draftInput, "v1 draft");
  assert.equal(payload.metadata.randomSeed, 111);
  assert.equal(payload.source, "image");
  assert.equal(payload.metadata.restoredFromVersion, 1);
});

test("history restore requires explicit confirmation before discarding pending edits", () => {
  let state = createPersistedGeneratedState();
  state = reduceState(state, {
    type: "UPDATE_BLOCK",
    id: "pose",
    language: "en",
    value: "do not discard silently",
  });

  const blocked = reduceState(state, { type: "RESTORE_VERSION", version: 1 });
  assert.equal(
    blocked.blocks.find((block) => block.id === "pose").en,
    "do not discard silently"
  );
  assert.match(blocked.toast, /确认放弃/);

  const restored = reduceState(state, {
    type: "RESTORE_VERSION",
    version: 1,
    confirmed: true,
  });
  assert.notEqual(
    restored.blocks.find((block) => block.id === "pose").en,
    "do not discard silently"
  );
});

test("editor exposes version timeline, variant compare and shortcuts", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "..", "styles.css"), "utf8");

  assert.match(html, /id=["']versionTimeline["']/);
  assert.match(app, /data-restore-version/);
  assert.match(app, /data-keep-variant/);
  assert.match(app, /data-revert-variant/);
  assert.match(app, /block-fragment/);
  assert.match(css, /\.version-chip/);
  assert.match(css, /\.variant-compare/);
  assert.match(app, /addEventListener\("keydown"/);
});

test("artist mix composes weighted tags for the artist block", () => {
  const { composeArtistMix } = require("../app.js");

  assert.equal(
    composeArtistMix([
      { tag: "@lack", weight: 80 },
      { tag: "toi8", weight: 100 },
      { tag: "wlop", weight: 120 },
    ]),
    "(@lack:0.8), toi8, (wlop:1.2)"
  );
  assert.equal(composeArtistMix([]), "");
  assert.equal(composeArtistMix([{ tag: "  ", weight: 90 }]), "");
});

test("quick library exposes the artist mixer panel", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

  assert.match(html, /id=["']artistMixer["']/);
  assert.match(app, /data-mix-artist/);
  assert.match(app, /data-mix-weight/);
  assert.match(app, /applyArtistMixBtn/);
  assert.match(app, /function applyArtistMix/);
});

test("a recipe saves all blocks and can be applied back", () => {
  let state = createGeneratedState();
  const recipe = {
    id: "recipe-1",
    type: "snippets",
    name: "测试配方",
    meta: "配方 · 整组结构块",
    blocks: state.blocks.map((block) => ({
      id: block.id,
      en: `${block.id} recipe en`,
      zh: `${block.id} recipe zh`,
    })),
  };
  state = reduceState(state, { type: "ADD_RECIPE", recipe });
  assert.ok(state.resources.snippets.some((item) => item.id === "recipe-1"));

  state = reduceState(state, { type: "APPLY_RESOURCE", id: "recipe-1" });
  assert.equal(
    state.blocks.find((block) => block.id === "scene").en,
    "scene recipe en"
  );
  assert.ok(state.previewOutput);
  assert.ok(state.dirtyBlockIds.length > 0);
});

test("recipes preserve block weights when reapplied", () => {
  let state = createGeneratedState();
  const recipe = {
    id: "weighted-recipe",
    type: "snippets",
    name: "权重配方",
    meta: "配方 · 整组结构块",
    blocks: state.blocks.map((block) => ({
      id: block.id,
      en: `${block.id} weighted en`,
      zh: `${block.id} weighted zh`,
      weight: block.id === "effects" ? 45 : block.weight,
    })),
  };

  state = reduceState(state, { type: "ADD_RECIPE", recipe });
  state = reduceState(state, { type: "APPLY_RESOURCE", id: "weighted-recipe" });

  assert.equal(state.blocks.find((block) => block.id === "effects").weight, 45);
});

test("a variant matrix candidate applies with explicit block ids", () => {
  let state = createGeneratedState();
  state = reduceState(state, {
    type: "APPLY_BLOCKS_VARIANT",
    item: {
      items: [
        { id: "pose", en: "matrix pose", zh: "矩阵姿势" },
        { id: "scene", en: "matrix scene", zh: "矩阵场景" },
      ],
    },
    blockIds: ["pose", "scene"],
  });
  assert.equal(state.blocks.find((block) => block.id === "pose").en, "matrix pose");
  assert.equal(state.blocks.find((block) => block.id === "scene").en, "matrix scene");
});

test("tag suggestions rank prefix matches first", () => {
  const { suggestTags } = require("../app.js");
  const dictionary = ["twintails", "twin braids", "wind lift", "sitting"];
  assert.deepEqual(suggestTags(dictionary, "twin"), ["twintails", "twin braids"]);
  assert.deepEqual(suggestTags(dictionary, "ind"), ["wind lift"]);
  assert.deepEqual(suggestTags(dictionary, "t"), []);
  assert.deepEqual(suggestTags(dictionary, "sitting"), []);
});

test("editor exposes recipe, matrix and tag suggest features", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");
  const dataJs = fs.readFileSync(path.join(__dirname, "..", "data.js"), "utf8");

  assert.match(html, /data-action=["']save-recipe["']/);
  assert.match(html, /id=["']variantMatrixBtn["']/);
  assert.match(html, /id=["']variantMatrix["']/);
  assert.match(app, /BLOCKS_VARIANT_READY/);
  assert.match(app, /data-pick-matrix/);
  assert.match(app, /function updateTagSuggest/);
  assert.match(dataJs, /promptTagDictionary/);
});

test("workspace commit preallocates stable ids and carries the complete recipe inputs", () => {
  const state = createGeneratedState();
  state.projectName = "Atomic save";
  state.generationParameters.steps = 36;
  state.manualParameterKeys = ["steps"];
  const payload = buildWorkspaceCommitPayload(state, {
    operationId: "save-fixed",
    projectId: "project-fixed",
    versionId: "version-fixed",
  });

  assert.equal(payload.operationId, "save-fixed");
  assert.equal(payload.createProject, true);
  assert.equal(payload.project.id, "project-fixed");
  assert.equal(payload.version.id, "version-fixed");
  assert.equal(payload.version.baseVersion, 0);
  assert.equal(
    payload.version.metadata.parameterLayers.manual_override.steps,
    36
  );
  assert.equal(payload.project.metadata.workspaceBaseVersion, 1);
  assert.equal("baseUpdatedAt" in payload.project, false);
});

test("client ids are safe and deterministic with an injected uuid", () => {
  assert.equal(
    createClientId("save", () => "A0B1-C2D3-E4F5"),
    "save-a0b1-c2d3-e4f5"
  );
  assert.match(createClientId("version", () => "unsafe / value"), /^[A-Za-z0-9._~-]+$/);
});

test("generation parameter edits are versioned manual overrides", () => {
  let state = createInitialState();
  state = reduceState(state, {
    type: "SET_GENERATION_PARAMETER",
    key: "cfg",
    value: "6.25",
  });
  const payload = buildVersionPayload(state);

  assert.equal(state.generationParameters.cfg, 6.25);
  assert.equal(state.hasUnsavedChanges, true);
  assert.deepEqual(state.manualParameterKeys, ["cfg"]);
  assert.equal(payload.metadata.parameterLayers.manual_override.cfg, 6.25);
});

test("model profiles apply defaults without overwriting manual parameters", () => {
  let state = createInitialState();
  state.generationParameters.cfg = 6.25;
  state.manualParameterKeys = ["cfg"];
  state = reduceState(state, {
    type: "MODEL_PROFILES_LOADED",
    items: [
      {
        profileId: "anima-1.1-v1",
        displayName: "MiaoMiao Harem Anima_1.1",
        defaultParameters: {
          sampler: "Euler a",
          scheduler: "Normal",
          steps: 32,
          cfg: 5.5,
        },
        candidateResolutionPresets: [
          {
            id: "square-1024x1024",
            width: 1024,
            height: 1024,
            label: "1024 × 1024（候选，待验证）",
          },
        ],
      },
    ],
  });

  assert.equal(state.generationParameters.sampler, "Euler a");
  assert.equal(state.generationParameters.steps, 32);
  assert.equal(state.generationParameters.cfg, 6.25);
  assert.equal(state.modelProfilesStatus, "ready");
  assert.equal(state.modelProfiles.length, 1);
});

test("restoring model defaults clears parameter overrides", () => {
  let state = createInitialState();
  state.modelProfiles = [
    {
      profileId: "anima-1.1-v1",
      defaultParameters: {
        sampler: "Euler",
        scheduler: "Normal",
        steps: 30,
        cfg: 5.5,
      },
    },
  ];
  state.generationParameters.steps = 48;
  state.manualParameterKeys = ["steps", "cfg"];

  state = reduceState(state, { type: "RESTORE_MODEL_DEFAULTS" });

  assert.equal(state.generationParameters.steps, 30);
  assert.deepEqual(state.manualParameterKeys, []);
});

test("AI edit preview state preserves multi-block reasons until confirmation", () => {
  let state = createInitialState();
  const preview = {
    ready: true,
    affectedIds: ["scene", "lighting", "effects"],
    lockedIds: Array.from({ length: 10 }, (_, index) => `locked-${index}`),
    diffs: [
      {
        id: "scene",
        before: { zh: "古老天文台" },
        after: { zh: "暴雨中的破碎城堡废墟" },
        reason: "用户要求替换环境。",
      },
    ],
  };

  state = reduceState(state, { type: "EDIT_PREVIEW_READY", item: preview });

  assert.deepEqual(state.pendingEditPreview, preview);
  assert.equal(state.pendingEditPreview.diffs[0].reason, "用户要求替换环境。");
});

test("deterministic random plans map clothing into the dedicated outfit block", () => {
  let state = createGeneratedState();
  const outfit = {
    id: "outfit",
    label: "服装与配饰",
    en: "old outfit",
    zh: "旧服装",
    locked: false,
    weight: 100,
  };
  state.appliedBlocks.push(outfit);
  state.blocks = structuredClone(state.appliedBlocks);
  state = reduceState(state, {
    type: "APPLY_RANDOM_PLAN",
    item: {
      librarySeed: "00112233445566778899aabbccddeeff",
      catalog: { version: "v1-test" },
      items: [
        {
          text: "armored bodysuit",
          categoryId: "clothing_outfit",
          locked: true,
          binding: { blockId: "appearance" },
        },
      ],
    },
  });
  const appliedOutfit = state.appliedBlocks.find((block) => block.id === "outfit");

  assert.equal(appliedOutfit.en, "armored bodysuit");
  assert.match(appliedOutfit.zh, /^待本地 LLM 翻译：/);
  assert.equal(appliedOutfit.locked, true);
  assert.equal(state.randomSeed, "00112233445566778899aabbccddeeff");
});

test("workbench visibly exposes model presets and AI local edit flow", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const appSource = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

  assert.match(html, /Anima_1\.1/);
  assert.match(html, /RECIPE V1 · 13 BLOCKS/);
  assert.match(html, /data-generation-param="steps"/);
  assert.match(html, /data-action="wordlist-plan"/);
  assert.match(html, /id="recipeModelProfile"/);
  assert.match(html, /id="recipeResolutionPreset"/);
  assert.match(html, /候选，待验证/);
  assert.match(html, /AI 局部修改/);
  assert.match(html, /生成修改预览/);
  assert.match(html, /data-action="preview-edit"/);
  assert.match(html, /data-action="undo-recipe"/);
  assert.doesNotMatch(html, /<select id="targetModel"/);
  assert.match(
    appSource,
    /renderOutput\(\);\s+renderRecipeConsole\(\);\s+renderDrawer\(\);/
  );
  assert.match(appSource, /provider: state\.settings\.textProvider/);
  assert.match(appSource, /item\.reason/);
  assert.match(appSource, /其余.*保持不变/);
});

test("workbench exposes logical backup export and isolated restore controls", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const appSource = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

  assert.match(html, /data-action="export-all"/);
  assert.match(html, /data-action="export-project"/);
  assert.match(html, /id="backupFileInput"/);
  assert.match(html, /校验并隔离恢复/);
  assert.match(appSource, /\/api\/backups\/inspect/);
  assert.match(appSource, /\/api\/backups\/stage-restore\?conflict=rename/);
  assert.match(appSource, /activated/);
});
