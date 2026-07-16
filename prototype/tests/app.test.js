const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  createInitialState,
  reduceState,
  getCombinedPrompt,
  isSupportedImageFile,
} = require("../app.js");

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

test("starts on a blank new-work screen", () => {
  const state = createInitialState();
  assert.equal(state.view, "home");
  assert.equal(state.projectName, "未命名作品");
  assert.equal(state.output.positiveEn, "");
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
  assert.equal(state.version, 1);
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
  assert.equal(state.version, 1);
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
  assert.equal(state.version, 1);
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
  assert.equal(state.version, 1);
});

test("applying block edits recompiles output and creates a new version", () => {
  let state = createGeneratedState();
  state = reduceState(state, {
    type: "UPDATE_BLOCK",
    id: "pose",
    language: "en",
    value: "crouching by the roadside, tying her shoelaces",
  });
  state = reduceState(state, { type: "APPLY_CHANGES" });

  assert.equal(state.version, 2);
  assert.equal(state.dirtyBlockIds.length, 0);
  assert.match(state.output.positiveEn, /tying her shoelaces/);
  assert.equal(state.versionHistory.length, 2);
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
  assert.equal(state.version, 1);
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
  assert.equal(state.versionHistory.at(-1).version, originalVersion);
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
  assert.match(app, /signal: activeTextAbort\.signal/);
  assert.match(app, /signal: activeVisionAbort\.signal/);
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
  assert.equal(state.version, 1);

  state = reduceState(state, { type: "APPLY_CHANGES" });
  assert.equal(state.previewOutput, null);
  assert.match(state.output.positiveEn, /kneeling in shallow water/);
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

test("restoring an old version creates a new version with the old content", () => {
  let state = createGeneratedState();
  const v1Pose = state.blocks.find((block) => block.id === "pose").en;
  state = reduceState(state, {
    type: "UPDATE_BLOCK",
    id: "pose",
    language: "en",
    value: "changed pose",
  });
  state = reduceState(state, { type: "APPLY_CHANGES" });
  assert.equal(state.version, 2);

  state = reduceState(state, { type: "RESTORE_VERSION", version: 1 });
  assert.equal(state.version, 3);
  assert.equal(state.blocks.find((block) => block.id === "pose").en, v1Pose);
  assert.equal(state.versionHistory.length, 3);
  assert.equal(state.versionHistory[2].restoredFrom, 1);
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
