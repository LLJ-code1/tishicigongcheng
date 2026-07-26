(function (root) {
  const data =
    root.PROMPT_STUDIO_DATA ||
    (typeof require !== "undefined" ? require("./data.js") : {});
  const unicode15 =
    root.PROMPT_STUDIO_UNICODE15 ||
    (typeof require !== "undefined" ? require("./unicode15-data.js") : null);
  if (
    !unicode15 ||
    typeof unicode15.isAssignedCodePoint !== "function" ||
    typeof unicode15.lowercase !== "function"
  ) {
    throw new Error("Unicode 15 canonicalization data is unavailable");
  }
  const RANDOM_VARIANT_BLOCKS = new Set([
    "subject",
    "appearance",
    "outfit",
    "expression",
    "pose",
    "interaction",
    "scene",
    "composition",
    "lighting",
    "effects",
  ]);
  const DEFAULT_BLOCK_WEIGHT = 100;
  const MIN_BLOCK_WEIGHT = 0;
  const MAX_BLOCK_WEIGHT = 120;
  const MAX_DIRECTOR_IMAGE_BYTES = 20 * 1024 * 1024;
  const MAX_DIRECTOR_EVIDENCE_CHARACTERS = 100_000;
  const MAX_DIRECTOR_EVIDENCE_MODELS = 16;
  const DIRECTOR_IMAGE_REQUESTED_USES = Object.freeze([
    Object.freeze({ id: "character", label: "人物" }),
    Object.freeze({ id: "appearance", label: "外貌" }),
    Object.freeze({ id: "outfit", label: "服装" }),
    Object.freeze({ id: "action", label: "动作" }),
    Object.freeze({ id: "environment", label: "环境" }),
    Object.freeze({ id: "composition", label: "构图" }),
    Object.freeze({ id: "lighting", label: "光影" }),
    Object.freeze({ id: "style", label: "风格" }),
  ]);
  const DIRECTOR_IMAGE_USE_IDS = new Set(
    DIRECTOR_IMAGE_REQUESTED_USES.map((item) => item.id)
  );
  const SAFE_DIRECTOR_IMAGE_ID = /^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$/u;
  const SAFE_EVIDENCE_MODEL_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
  const PROMPT_WHITESPACE_PATTERN =
    /[\u0009-\u000d\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+/g;
  const PROMPT_EDGE_WHITESPACE_PATTERN =
    /^[\u0009-\u000d\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+|[\u0009-\u000d\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+$/g;
  const MODEL_STATUS_LABELS = Object.freeze({
    idle: "未加载",
    waiting: "等待中",
    running: "运行中",
    ready: "已就绪",
    releasing: "释放中",
    error: "错误",
    unavailable: "未安装",
    unknown: "未知状态",
  });
  const PROJECT_SETTING_KEYS = Object.freeze([
    "textProvider",
    "localTextUrl",
    "localTextModel",
    "apiTextUrl",
    "apiTextModel",
    "expansionLevel",
    "visionProvider",
    "residency",
    "autoCombine",
  ]);

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function finiteWeight(value) {
    if (typeof value === "number") return value;
    if (typeof value === "string" && value.trim()) return Number(value);
    return Number.NaN;
  }

  function normalizeBlockWeight(value, fallback = DEFAULT_BLOCK_WEIGHT) {
    const candidate = finiteWeight(value);
    const fallbackValue = finiteWeight(fallback);
    const weight = Number.isFinite(candidate)
      ? candidate
      : Number.isFinite(fallbackValue)
        ? fallbackValue
        : DEFAULT_BLOCK_WEIGHT;
    return Math.min(MAX_BLOCK_WEIGHT, Math.max(MIN_BLOCK_WEIGHT, weight));
  }

  function normalizeBlocks(blocks) {
    if (!Array.isArray(blocks)) return [];
    return blocks
      .filter((block) => block && typeof block === "object" && !Array.isArray(block))
      .map((block) => ({
        ...clone(block),
        weight: normalizeBlockWeight(block.weight),
      }));
  }

  function normalizeResourceWeights(resource) {
    if (!resource || typeof resource !== "object" || Array.isArray(resource)) {
      return {};
    }
    const normalized = clone(resource);
    if (Array.isArray(resource.blocks)) {
      normalized.blocks = normalizeBlocks(resource.blocks);
    }
    if (Object.prototype.hasOwnProperty.call(resource, "weight")) {
      normalized.weight = normalizeBlockWeight(resource.weight);
    }
    return normalized;
  }

  function normalizeUnicode15AssignedRuns(value) {
    const output = [];
    let assignedRun = "";
    const flush = () => {
      if (!assignedRun) return;
      output.push(
        unicode15
          .lowercase(assignedRun.normalize("NFKC"))
          .replace(/\u00df/g, "ss")
      );
      assignedRun = "";
    };
    for (const character of String(value ?? "")) {
      if (unicode15.isAssignedCodePoint(character.codePointAt(0))) {
        assignedRun += character;
      } else {
        flush();
        output.push(character);
      }
    }
    flush();
    return output.join("");
  }

  // Keep the backend rule in sync: normalize only Unicode-15 assigned runs,
  // apply the generated Unicode-15 lowercase table, collapse White_Space plus
  // copied BOMs, then fold sharp-s to "ss" inside normalized runs.
  function canonicalizePromptItem(value) {
    return normalizeUnicode15AssignedRuns(value)
      .replace(PROMPT_WHITESPACE_PATTERN, " ")
      .replace(/^ +| +$/g, "");
  }

  function normalizeModelStatus(status) {
    const value = String(status || "");
    return Object.prototype.hasOwnProperty.call(MODEL_STATUS_LABELS, value)
      ? value
      : "unknown";
  }

  function statusLabel(status) {
    return MODEL_STATUS_LABELS[normalizeModelStatus(status)];
  }

  function projectSettingsMetadata(settings) {
    const source = settings && typeof settings === "object" ? settings : {};
    return Object.fromEntries(
      PROJECT_SETTING_KEYS.filter((key) =>
        Object.prototype.hasOwnProperty.call(source, key) &&
        source[key] !== undefined
      ).map((key) => [key, clone(source[key])])
    );
  }

  function settingsWritePayload(settings) {
    const source = settings && typeof settings === "object" ? settings : {};
    const payload = clone(source);
    for (const key of ["apiTextKey", "localTextKey"]) {
      if (typeof payload[key] !== "string" || !payload[key].trim()) {
        delete payload[key];
      }
      delete payload[`${key}Configured`];
    }
    return payload;
  }

  function normalizeCreativeDirectorSkillOverride(value) {
    if (typeof value !== "string") return "";
    const length = Array.from(value).length;
    if (length > 100_000) {
      throw new TypeError("creative director Skill override exceeds 100000 characters");
    }
    return value.trim() ? value : "";
  }

  function enqueueByKey(queue, key, task) {
    const previous = queue.get(key) || Promise.resolve();
    const current = previous.catch(() => undefined).then(task);
    queue.set(key, current);
    return current.finally(() => {
      if (queue.get(key) === current) queue.delete(key);
    });
  }

  function isSupportedImageFile(file) {
    if (!file) return false;
    const mimeType = String(file.type || "").toLowerCase();
    const filename = String(file.name || "").toLowerCase();
    return (
      ["image/png", "image/jpeg", "image/webp"].includes(mimeType) ||
      (!mimeType && /\.(png|jpe?g|webp)$/.test(filename))
    );
  }

  function validateDirectorImageFile(file) {
    if (!isSupportedImageFile(file)) {
      return { ok: false, error: "请选择 PNG、JPG 或 WEBP 图片" };
    }
    const size = Number(file?.size);
    if (!Number.isSafeInteger(size) || size <= 0) {
      return { ok: false, error: "图片文件为空或大小无效" };
    }
    if (size > MAX_DIRECTOR_IMAGE_BYTES) {
      return { ok: false, error: "图片不能超过 20 MB" };
    }
    return { ok: true, error: "" };
  }

  function validateDirectorImageFileBatch(
    files,
    currentReferences = [],
    options = {}
  ) {
    const accepted = [];
    const errors = [];
    const availableSlots = Math.min(
      Number.isSafeInteger(options.maxAccepted)
        ? Math.max(0, options.maxAccepted)
        : 8,
      Math.max(
        0,
        8 -
          (Array.isArray(currentReferences)
            ? currentReferences.length
            : 0)
      )
    );
    for (const file of Array.from(files || [])) {
      const validation = validateDirectorImageFile(file);
      if (!validation.ok) {
        errors.push({
          name: pureImageFilename(file?.name || "未命名文件"),
          error: validation.error,
        });
      } else if (accepted.length >= availableSlots) {
        errors.push({
          name: pureImageFilename(file.name),
          error:
            options.maxAccepted === 1
              ? "本次只能选择 1 张图片"
              : "最多只能添加 8 张参考图",
        });
      } else {
        accepted.push(file);
      }
    }
    return { accepted, errors };
  }

  function directorImageMimeType(file) {
    const declared = String(file?.type || "").toLowerCase();
    if (["image/png", "image/jpeg", "image/webp"].includes(declared)) {
      return declared;
    }
    const name = String(file?.name || "").toLowerCase();
    if (name.endsWith(".png")) return "image/png";
    if (name.endsWith(".webp")) return "image/webp";
    return "image/jpeg";
  }

  function pureImageFilename(value) {
    const parts = String(value || "")
      .split(/[\\/:]+/u)
      .filter(Boolean);
    const candidate = parts.at(-1) || "";
    if (!candidate || candidate === "." || candidate === "..") {
      throw new TypeError("director image filename is invalid");
    }
    return Array.from(candidate).slice(0, 512).join("");
  }

  function createDirectorImageReference(file, createId) {
    const validation = validateDirectorImageFile(file);
    if (!validation.ok) throw new TypeError(validation.error);
    if (typeof createId !== "function") {
      throw new TypeError("director image reference requires an ID factory");
    }
    const id = String(createId() || "");
    if (!SAFE_DIRECTOR_IMAGE_ID.test(id)) {
      throw new TypeError("director image reference ID is invalid");
    }
    return {
      id,
      name: pureImageFilename(file.name),
      mimeType: directorImageMimeType(file),
      status: "local_reference_not_embedded",
      requestedUses: [],
    };
  }

  function setDirectorImageRequestedUses(reference, requestedUses) {
    if (
      !reference ||
      typeof reference !== "object" ||
      Array.isArray(reference) ||
      !Array.isArray(requestedUses)
    ) {
      throw new TypeError("requested image uses require one image reference");
    }
    const normalized = [];
    for (const value of requestedUses) {
      if (typeof value !== "string" || !DIRECTOR_IMAGE_USE_IDS.has(value)) {
        throw new TypeError("requested image use is not supported");
      }
      if (!normalized.includes(value)) normalized.push(value);
    }
    return { ...clone(reference), requestedUses: normalized };
  }

  function buildDirectorImageUiState(imageCount, busy) {
    const atLimit = Number(imageCount) >= 8;
    const isBusy = Boolean(busy);
    return {
      addDisabled: atLimit || isBusy,
      dropDisabled: atLimit || isBusy,
      inputDisabled: isBusy,
      canAttach: !isBusy,
    };
  }

  function buildDirectorImageCards(
    value,
    getAttachment,
    { analyzingIds = [] } = {}
  ) {
    const intake = normalizeCreativeIntake(value);
    const lookup =
      typeof getAttachment === "function" ? getAttachment : () => null;
    const analyzing = new Set(
      Array.isArray(analyzingIds) ? analyzingIds : []
    );
    return intake.inputs.images.map((reference) => {
      const attachment = lookup(reference.id);
      const failures = Array.isArray(attachment?.failures)
        ? attachment.failures
            .map((failure) => String(failure?.error || "").trim())
            .filter(Boolean)
        : [];
      const status = analyzing.has(reference.id)
        ? "analyzing"
        : String(
            attachment?.status ||
              (attachment ? "idle" : "missing")
          );
      return {
        id: reference.id,
        name: reference.name,
        previewUrl: String(attachment?.objectUrl || ""),
        requestedUses: clone(reference.requestedUses),
        aiSuggest: reference.requestedUses.length === 0,
        status,
        failures,
        canRetry: status === "error" || status === "partial",
        needsReattachment: !attachment,
      };
    });
  }

  function buildDirectorImageReplaceAction(intake, reference = null) {
    return buildDirectorImagesReplaceAction(
      intake,
      reference ? [reference] : []
    );
  }

  function buildDirectorImagesReplaceAction(intake, references) {
    const current = normalizeCreativeIntake(intake);
    if (!Array.isArray(references)) {
      throw new TypeError("director image replacement requires an image list");
    }
    return {
      type: "replace_inputs",
      text: current.inputs.text,
      images: clone(references),
    };
  }

  function createDirectorImageCollectionController({
    createObjectURL,
    revokeObjectURL,
  }) {
    if (
      typeof createObjectURL !== "function" ||
      typeof revokeObjectURL !== "function"
    ) {
      throw new TypeError(
        "multi-image attachments require object URL functions"
      );
    }
    const entries = new Map();

    function safeEntry(entry) {
      if (!entry) return null;
      return {
        ...entry,
        evidence: entry.evidence == null ? null : clone(entry.evidence),
        failures: clone(entry.failures),
      };
    }

    function validateBinding(imageId, file) {
      if (!SAFE_DIRECTOR_IMAGE_ID.test(String(imageId || ""))) {
        return { ok: false, error: "director image reference ID is invalid" };
      }
      return validateDirectorImageFile(file);
    }

    function release(entry) {
      if (entry?.objectUrl) revokeObjectURL(entry.objectUrl);
    }

    function makeEntry(file, options = {}) {
      return {
        file,
        objectUrl: createObjectURL(file),
        evidence:
          options.evidence == null ? null : clone(options.evidence),
        failures: Array.isArray(options.failures)
          ? clone(options.failures)
          : [],
        status: String(options.status || "idle"),
      };
    }

    return {
      bind(imageId, file, options = {}) {
        const validation = validateBinding(imageId, file);
        if (!validation.ok) {
          return { accepted: false, error: validation.error };
        }
        if (entries.has(imageId)) {
          return {
            accepted: false,
            error: "director image reference ID is already bound",
          };
        }
        if (entries.size >= 8) {
          return {
            accepted: false,
            error: "director images cannot exceed eight attachments",
          };
        }
        const entry = makeEntry(file, options);
        entries.set(imageId, entry);
        return { accepted: true, entry: safeEntry(entry) };
      },
      replace(imageId, file, options = {}) {
        const validation = validateBinding(imageId, file);
        if (!validation.ok) {
          return { accepted: false, error: validation.error };
        }
        const previous = entries.get(imageId);
        if (!previous) {
          return {
            accepted: false,
            error: "director image reference is not bound",
          };
        }
        const entry = makeEntry(file, options);
        entries.set(imageId, entry);
        release(previous);
        return { accepted: true, entry: safeEntry(entry) };
      },
      remove(imageId) {
        const previous = entries.get(imageId);
        if (!previous) return null;
        entries.delete(imageId);
        release(previous);
        return safeEntry(previous);
      },
      clear() {
        const previous = Array.from(entries.values());
        entries.clear();
        previous.forEach(release);
      },
      reconcile(references) {
        if (!Array.isArray(references)) {
          throw new TypeError(
            "director image reconciliation requires an image list"
          );
        }
        const canonicalIds = new Set(
          references.map((reference) => String(reference?.id || ""))
        );
        for (const imageId of Array.from(entries.keys())) {
          if (!canonicalIds.has(imageId)) this.remove(imageId);
        }
      },
      get(imageId) {
        return safeEntry(entries.get(imageId));
      },
      setAnalysis(imageId, {
        evidence = null,
        failures = [],
        status = "idle",
        evidenceContext = null,
      } = {}) {
        const entry = entries.get(imageId);
        if (!entry) return false;
        entry.evidence = evidence == null ? null : clone(evidence);
        entry.failures = Array.isArray(failures) ? clone(failures) : [];
        entry.status = String(status || "idle");
        entry.evidenceContext = evidenceContext;
        return true;
      },
      size() {
        return entries.size;
      },
    };
  }

  function shouldBindDirectorImageFile({
    transitionOutcome,
    reference,
    current,
  }) {
    if (!transitionOutcome?.accepted) return false;
    if (!reference || typeof reference !== "object") return false;
    let canonical;
    try {
      canonical = normalizeCreativeIntake(current);
    } catch {
      return false;
    }
    const image = canonical.inputs.images.find(
      (candidate) => candidate.id === reference.id
    );
    return Boolean(
      image &&
      image.name === reference.name &&
      image.mimeType === reference.mimeType &&
      image.status === reference.status &&
      JSON.stringify(image.requestedUses) ===
        JSON.stringify(reference.requestedUses)
    );
  }

  function createSingleImagePreviewController({
    createObjectURL,
    revokeObjectURL,
  }) {
    if (
      typeof createObjectURL !== "function" ||
      typeof revokeObjectURL !== "function"
    ) {
      throw new TypeError("single-image preview requires object URL functions");
    }
    let attachment = null;
    return {
      attach(file, { confirmReplace, createId } = {}) {
        const validation = validateDirectorImageFile(file);
        if (!validation.ok) {
          return { accepted: false, error: validation.error };
        }
        if (
          attachment &&
          (typeof confirmReplace !== "function" || !confirmReplace())
        ) {
          return { accepted: false, error: "已保留当前参考图" };
        }
        const reference = createDirectorImageReference(file, createId);
        const previewUrl = createObjectURL(file);
        const previous = attachment;
        attachment = { file, reference, previewUrl };
        if (previous?.previewUrl) revokeObjectURL(previous.previewUrl);
        return { accepted: true, attachment: { ...attachment } };
      },
      remove() {
        const previous = attachment;
        attachment = null;
        if (previous?.previewUrl) revokeObjectURL(previous.previewUrl);
        return previous ? { ...previous } : null;
      },
      current() {
        return attachment ? { ...attachment } : null;
      },
    };
  }

  function boundedText(value, maximum) {
    const source = String(value || "");
    let safe = "";
    for (let index = 0; index < source.length; index += 1) {
      const unit = source.charCodeAt(index);
      if (unit >= 0xd800 && unit <= 0xdbff) {
        const trailing = source.charCodeAt(index + 1);
        if (trailing >= 0xdc00 && trailing <= 0xdfff) {
          safe += source[index] + source[index + 1];
          index += 1;
        } else {
          safe += "\ufffd";
        }
      } else if (unit >= 0xdc00 && unit <= 0xdfff) {
        safe += "\ufffd";
      } else {
        safe += source[index];
      }
    }
    return Array.from(safe).slice(0, maximum).join("");
  }

  function buildDirectorImageEvidence(imageReference, visionItem) {
    const reference = setDirectorImageRequestedUses(
      imageReference,
      imageReference?.requestedUses || []
    );
    const item =
      visionItem && typeof visionItem === "object" && !Array.isArray(visionItem)
        ? visionItem
        : {};
    const rawResults =
      item.rawResults &&
      typeof item.rawResults === "object" &&
      !Array.isArray(item.rawResults)
        ? item.rawResults
        : {};
    const analyzerStates = Array.isArray(item.analyzers)
      ? item.analyzers
      : [];
    const failedAnalyzers = analyzerStates
      .filter((entry) => entry?.status === "error")
      .slice(0, MAX_DIRECTOR_EVIDENCE_MODELS)
      .map((entry) => ({
        id: SAFE_EVIDENCE_MODEL_ID.test(String(entry.id || ""))
          ? String(entry.id)
          : "unknown",
        error: boundedText(entry.error || "分析失败", 2_000),
      }));
    const readyIds = [];
    for (const entry of analyzerStates) {
      const id = String(entry?.id || "");
      if (
        entry?.status === "ready" &&
        SAFE_EVIDENCE_MODEL_ID.test(id) &&
        !readyIds.includes(id)
      ) {
        readyIds.push(id);
      }
    }
    if (!readyIds.length) {
      for (const id of Object.keys(rawResults)) {
        if (
          SAFE_EVIDENCE_MODEL_ID.test(id) &&
          !readyIds.includes(id)
        ) {
          readyIds.push(id);
        }
      }
    }
    const sourceModels = readyIds.slice(
      0,
      MAX_DIRECTOR_EVIDENCE_MODELS
    );
    const summaries = sourceModels
      .map((id) => {
        const analyzer = analyzerStates.find(
          (entry) => entry?.id === id && entry?.status === "ready"
        );
        return String(rawResults[id] ?? analyzer?.raw ?? "").trim();
      })
      .filter(Boolean);
    const summary = boundedText(
      summaries.join("\n\n"),
      MAX_DIRECTOR_EVIDENCE_CHARACTERS
    );
    const hasEvidence = sourceModels.length > 0 && Boolean(summary);
    return {
      evidence: hasEvidence
        ? {
            imageId: reference.id,
            requestedUses: clone(reference.requestedUses),
            summary,
            sourceModels,
            uncertain:
              failedAnalyzers.length > 0 ||
              reference.requestedUses.length === 0,
          }
        : null,
      failedAnalyzers,
      allFailed: !hasEvidence,
    };
  }

  function directorImageBinding(reference) {
    if (!reference) return "";
    return JSON.stringify([
      reference.id || "",
      Array.isArray(reference.requestedUses)
        ? reference.requestedUses
        : [],
    ]);
  }

  function shouldReuseDirectorImageEvidence(
    reference,
    evidence,
    force = false
  ) {
    return Boolean(
      !force &&
        reference &&
        evidence &&
        evidence.imageId === reference.id &&
        JSON.stringify(evidence.requestedUses || []) ===
          JSON.stringify(reference.requestedUses || [])
    );
  }

  function createDirectorImageFlowGuard() {
    let revision = 0;
    return {
      beginMutation(abort) {
        revision += 1;
        if (typeof abort === "function") abort();
        return revision;
      },
      capture(reference) {
        return {
          revision,
          binding: directorImageBinding(reference),
        };
      },
      isCurrent(snapshot, reference) {
        return Boolean(
          snapshot &&
            snapshot.revision === revision &&
            snapshot.binding === directorImageBinding(reference)
        );
      },
    };
  }

  async function resolveDirectorImageEvidence({
    getReference,
    currentEvidence,
    force = false,
    guard,
    analyze,
  }) {
    if (
      typeof getReference !== "function" ||
      !guard ||
      typeof guard.capture !== "function" ||
      typeof guard.isCurrent !== "function" ||
      typeof analyze !== "function"
    ) {
      throw new TypeError("director image evidence resolver is invalid");
    }
    const reference = getReference();
    if (!reference) {
      return { accepted: true, reused: false, result: null };
    }
    const snapshot = guard.capture(reference);
    if (
      shouldReuseDirectorImageEvidence(
        reference,
        currentEvidence,
        force
      )
    ) {
      return {
        accepted: guard.isCurrent(snapshot, getReference()),
        reused: true,
        result: { evidence: clone(currentEvidence) },
      };
    }
    const result = await analyze(reference);
    if (!guard.isCurrent(snapshot, getReference())) {
      return { accepted: false, reused: false, result: null };
    }
    return { accepted: true, reused: false, result };
  }

  function directorEvidenceContextMatches({
    reference,
    entry,
    analyzerIds,
    sessionId,
    workspaceRevision,
    projectRevision,
  }) {
    const context = entry?.evidenceContext;
    return Boolean(
      entry?.evidence &&
        context &&
        context.file === entry.file &&
        JSON.stringify(context.analyzerIds || []) ===
          JSON.stringify(analyzerIds || []) &&
        context.sessionId === sessionId &&
        context.workspaceRevision === workspaceRevision &&
        context.projectRevision === projectRevision &&
        shouldReuseDirectorImageEvidence(reference, entry.evidence)
    );
  }

  function safeDirectorEvidenceItem(reference, value) {
    if (!value || value.imageId !== reference.id) return null;
    const sourceModels = Array.isArray(value.sourceModels)
      ? value.sourceModels
          .map((item) => String(item || ""))
          .filter((item) => SAFE_EVIDENCE_MODEL_ID.test(item))
          .slice(0, MAX_DIRECTOR_EVIDENCE_MODELS)
      : [];
    const summary = boundedText(value.summary, MAX_DIRECTOR_EVIDENCE_CHARACTERS);
    if (!summary || !sourceModels.length) return null;
    return {
      imageId: reference.id,
      requestedUses: clone(reference.requestedUses || []),
      summary,
      sourceModels,
      uncertain: Boolean(value.uncertain),
    };
  }

  function directorCollectionSignature(references) {
    return JSON.stringify(
      (Array.isArray(references) ? references : []).map((reference) => [
        reference?.id || "",
        Array.isArray(reference?.requestedUses)
          ? reference.requestedUses
          : [],
      ])
    );
  }

  async function resolveDirectorImageEvidenceCollection({
    references,
    getEntry,
    analyze,
    analyzerIds = [],
    sessionId,
    workspaceRevision,
    projectRevision,
    getCurrentContext,
    retryImageIds = [],
  }) {
    if (
      !Array.isArray(references) ||
      references.length > 8 ||
      typeof getEntry !== "function" ||
      typeof analyze !== "function" ||
      typeof getCurrentContext !== "function"
    ) {
      throw new TypeError("director image evidence collection resolver is invalid");
    }
    const retryIds = new Set(
      Array.isArray(retryImageIds) ? retryImageIds.map(String) : []
    );
    const initialSignature = directorCollectionSignature(references);
    const snapshots = new Map();
    const outcomes = await Promise.all(
      references.map(async (reference) => {
        const entry = getEntry(reference.id);
        snapshots.set(reference.id, entry?.file);
        const reusable = directorEvidenceContextMatches({
          reference,
          entry,
          analyzerIds,
          sessionId,
          workspaceRevision,
          projectRevision,
        });
        if (reusable && !retryIds.has(reference.id)) {
          return {
            reference,
            evidence: safeDirectorEvidenceItem(reference, entry.evidence),
            failures: [],
          };
        }
        if (
          retryIds.size &&
          !retryIds.has(reference.id) &&
          !reusable
        ) {
          return {
            reference,
            evidence: null,
            failures: Array.isArray(entry?.failures)
              ? clone(entry.failures)
              : [],
          };
        }
        if (!entry?.file) {
          return {
            reference,
            evidence: null,
            failures: [{ id: "attachment", error: "reference file is missing" }],
          };
        }
        let result;
        try {
          result = await analyze(reference, entry);
        } catch (error) {
          return {
            reference,
            evidence: null,
            failures: [
              {
                id: "analysis",
                error: boundedText(
                  error?.message || "all local image analyzers failed",
                  2_000
                ),
              },
            ],
          };
        }
        return {
          reference,
          evidence: safeDirectorEvidenceItem(reference, result?.evidence),
          failures: Array.isArray(result?.failedAnalyzers)
            ? clone(result.failedAnalyzers)
            : [],
        };
      })
    );
    const current = getCurrentContext() || {};
    const globallyStale =
      directorCollectionSignature(current.references) !== initialSignature ||
      JSON.stringify(current.analyzerIds || []) !==
        JSON.stringify(analyzerIds || []) ||
      current.sessionId !== sessionId ||
      current.workspaceRevision !== workspaceRevision ||
      current.projectRevision !== projectRevision;
    const fileStale = references.some(
      (reference) =>
        getEntry(reference.id)?.file !== snapshots.get(reference.id)
    );
    if (globallyStale || fileStale) {
      return { items: [], failures: [], stale: true };
    }
    const items = [];
    const failures = [];
    for (const outcome of outcomes) {
      if (outcome.evidence) {
        items.push(outcome.evidence);
      }
      if (!outcome.evidence || outcome.failures.length) {
        failures.push({
          imageId: outcome.reference.id,
          failures: outcome.failures,
        });
      }
    }
    return { items, failures, stale: false };
  }

  async function performDirectorImageAnalysis({
    file,
    imageReference,
    analyzerIds,
    readBase64,
    apiCall,
    signal,
  }) {
    if (
      typeof readBase64 !== "function" ||
      typeof apiCall !== "function"
    ) {
      throw new TypeError("director image analysis requires IO functions");
    }
    const validation = validateDirectorImageFile(file);
    if (!validation.ok) throw new TypeError(validation.error);
    try {
      const dataBase64 = await readBase64(file);
      const response = await apiCall("/api/vision/analyze", {
        method: "POST",
        ...(signal ? { signal } : {}),
        body: JSON.stringify({
          filename: pureImageFilename(file.name),
          mimeType: directorImageMimeType(file),
          dataBase64,
          analyzerIds: Array.isArray(analyzerIds)
            ? analyzerIds.slice(0, MAX_DIRECTOR_EVIDENCE_MODELS)
            : [],
        }),
      });
      return buildDirectorImageEvidence(
        imageReference,
        response?.item || response
      );
    } catch (error) {
      return {
        evidence: null,
        failedAnalyzers: [
          {
            id: "analysis",
            error: boundedText(
              error?.message || "所有识图模型均执行失败",
              2_000
            ),
          },
        ],
        allFailed: true,
      };
    }
  }

  function idleGenerationProgress() {
    return {
      status: "idle",
      task: "",
      title: "等待任务",
      detail: "点击“仅拆解”或“拓展并结构化”后，这里会显示当前进度。",
      provider: "",
      startedAt: 0,
      elapsedSeconds: 0,
      finishedAt: 0,
    };
  }

  function emptyCreativeIntake() {
    return {
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
    };
  }

  function createInitialState() {
    return {
      view: "home",
      projectId: null,
      projectName: "未命名作品",
      projectStatus: "draft",
      projects: [],
      projectsStatus: "idle",
      projectsError: "",
      openingProjectId: "",
      openError: "",
      saveStatus: "idle",
      saveError: "",
      lastSavedAt: "",
      projectUpdatedAt: "",
      hasUnsavedChanges: false,
      hasUnsavedProjectChanges: false,
      restoreFromVersion: null,
      workingRevision: 0,
      projectRevision: 0,
      creativeIntake: emptyCreativeIntake(),
      directorMessages: [],
      directorBusy: false,
      directorError: "",
      directorImageEvidence: null,
      directorImageAnalysisStatus: "idle",
      directorImageAnalysisFailures: [],
      directorRequestRevision: 0,
      textMode: "expand",
      draftInput: "",
      randomSeed: null,
      modelProfileId: "anima-1.1-v1",
      modelProfiles: [],
      modelProfilesStatus: "idle",
      generationParameters: {
        sampler: "Euler",
        scheduler: "Normal",
        steps: 30,
        cfg: 5.5,
        resolution: { width: 1024, height: 1024 },
        generationSeed: 0,
        denoiseStrength: null,
      },
      manualParameterKeys: [],
      randomPlan: null,
      randomCatalog: null,
      randomCatalogStatus: "idle",
      instructionHistory: [],
      pendingEditPreview: null,
      pendingChange: null,
      recipeHash: "",
      references: clone(data.referenceEntries || []),
      resources: clone(data.quickPicks || {
        characters: [],
        artists: [],
        snippets: [],
      }),
      blocks: [],
      appliedBlocks: [],
      dirtyBlockIds: [],
      version: 0,
      versionHistory: [],
      previewOutput: null,
      analyzers: clone(data.analyzers || {}),
      analysisQueue: [],
      analysisComplete: false,
      activeRawResult: "merged",
      rawMergedResult: "",
      output: {
        positiveEn: "",
        positiveZh: "",
        negativeEn: "",
        negativeZh: "",
        relationEn: "",
        relationZh: "",
      },
      outputChecks: {
        preservedUserIntent: true,
        bilingualAligned: true,
        conflicts: [],
        assumptions: [],
      },
      textGenerating: false,
      textDecomposing: false,
      translatingPending: false,
      selectedVariantBlockIds: [],
      batchRegenerating: false,
      generationProgress: idleGenerationProgress(),
      regeneratingBlockId: "",
      settings: {
        textProvider: "local",
        localTextUrl: "http://127.0.0.1:8080/v1",
        localTextModel: "Huihui-Qwen3-14B-abliterated-v2.Q4_K_M.gguf",
        localTextKey: "",
        apiTextUrl: "",
        apiTextModel: "",
        apiTextKey: "",
        creativeDirectorSkillOverride: "",
        expansionLevel: "balanced",
        visionProvider: "local",
        residency: "smart",
        autoCombine: true,
      },
      drawerOpen: false,
      resourceDialogOpen: false,
      toast: "",
      imageName: "",
      imageMimeType: "",
      imageSize: 0,
      imageLoaded: false,
      analysisNotice: "尚未开始分析",
    };
  }

  function emptyOutput() {
    return {
      positiveEn: "",
      positiveZh: "",
      negativeEn: "",
      negativeZh: "",
      relationEn: "",
      relationZh: "",
    };
  }

  function applyCanonicalModelToWorkspace(state) {
    const profileId = state.creativeIntake?.selectedModelProfileId;
    const profile = state.modelProfiles.find(
      (item) => item.profileId === profileId
    );
    if (!profile) return false;
    state.modelProfileId = profile.profileId;
    for (const [key, value] of Object.entries(
      safeObject(profile.defaultParameters)
    )) {
      if (
        key in state.generationParameters &&
        !state.manualParameterKeys.includes(key)
      ) {
        state.generationParameters[key] = clone(value);
      }
    }
    return true;
  }

  function clearCreativeDownstreamWorkspace(state) {
    state.draftInput = "";
    state.blocks = [];
    state.appliedBlocks = [];
    state.dirtyBlockIds = [];
    state.previewOutput = null;
    state.output = emptyOutput();
    state.outputChecks = {
      preservedUserIntent: true,
      bilingualAligned: true,
      conflicts: [],
      assumptions: [],
    };
    state.pendingEditPreview = null;
    state.pendingChange = null;
    state.recipeHash = "";
    state.randomPlan = null;
    state.instructionHistory = [];
    state.selectedVariantBlockIds = [];
    state.hasUnsavedChanges = false;
  }

  function resetAnalyzerWorkspaceResults(analyzers) {
    const next = clone(analyzers || {});
    Object.values(next).forEach((model) => {
      model.status = model.available === false ? "unavailable" : "idle";
      model.raw = "";
      model.error = "";
    });
    return next;
  }

  function safeObject(value) {
    return value && typeof value === "object" && !Array.isArray(value)
      ? value
      : {};
  }

  const CREATIVE_INTAKE_STAGE_ORDER = [
    "intake",
    "direction_selected",
    "brief_draft",
    "brief_confirmed",
    "model_selected",
    "decomposition_draft",
    "decomposition_confirmed",
  ];
  const CREATIVE_INTAKE_STAGES = new Set(CREATIVE_INTAKE_STAGE_ORDER);
  const CREATIVE_INTAKE_RECIPE_STATUSES = new Set([
    "missing",
    "stale",
    "ready",
  ]);
  const CREATIVE_INTAKE_SOURCE_TYPES = new Set([
    "user",
    "image",
    "ai",
    "model_rule",
  ]);

  function creativeDirectorStageView(stage) {
    const index = CREATIVE_INTAKE_STAGE_ORDER.indexOf(stage);
    const safeIndex = index < 0 ? 0 : index;
    return {
      showDirections: true,
      showBrief:
        safeIndex >= CREATIVE_INTAKE_STAGE_ORDER.indexOf("brief_draft"),
      showModelGate:
        safeIndex >= CREATIVE_INTAKE_STAGE_ORDER.indexOf("brief_confirmed"),
      canContinue:
        safeIndex >= CREATIVE_INTAKE_STAGE_ORDER.indexOf("model_selected"),
      showWorkbench:
        safeIndex >= CREATIVE_INTAKE_STAGE_ORDER.indexOf("model_selected"),
    };
  }

  function canConfirmCreativeBrief(value) {
    const intake = normalizeCreativeIntake(value);
    return Boolean(
      intake.stage === "brief_draft" &&
        intake.brief &&
        intake.brief.status === "draft" &&
        !intake.brief.openQuestions.length &&
        !intake.conflicts.some((conflict) => conflict.status === "open")
    );
  }

  const CREATIVE_BRIEF_GROUPS = [
    { id: "user", label: "用户明确" },
    { id: "image", label: "图片借用" },
    { id: "ai", label: "AI 补全" },
    { id: "locked", label: "已锁定" },
    { id: "questions", label: "待确认" },
    { id: "conflicts", label: "冲突" },
  ];

  function resolveCreativeBriefSourceLabel(source, images = []) {
    if (source?.type !== "image") return "";
    const refId = String(source.refId || "");
    const reference = Array.isArray(images)
      ? images.find((image) => image?.id === refId)
      : null;
    return `图片：${reference?.name || refId}`;
  }

  function creativeBriefItemRow(item, images) {
    return {
      id: item.id,
      category: item.category,
      text: item.text,
      source: clone(item.source),
      sourceLabel: resolveCreativeBriefSourceLabel(item.source, images),
      locked: item.locked,
      revisable: true,
    };
  }

  function buildCreativeBriefGroups(value) {
    const intake = normalizeCreativeIntake(value);
    const brief = intake.brief;
    const groups = Object.fromEntries(
      CREATIVE_BRIEF_GROUPS.map((group) => [group.id, []])
    );
    if (brief) {
      for (const item of brief.items) {
        const row = creativeBriefItemRow(item, intake.inputs.images);
        if (item.source.type === "user") groups.user.push(row);
        else if (item.source.type === "image") groups.image.push(row);
        else groups.ai.push(row);
        if (item.locked) groups.locked.push(row);
      }
      for (const [index, text] of brief.aiAdditions.entries()) {
        groups.ai.push({
          id: `ai-addition-${index}`,
          category: "ai_addition",
          text,
          source: { type: "ai", refId: null },
          locked: false,
          revisable: false,
        });
      }
      for (const [index, text] of brief.openQuestions.entries()) {
        groups.questions.push({
          id: `question-${index}`,
          category: "open_question",
          text,
          source: null,
          locked: false,
          revisable: false,
        });
      }
    }
    for (const conflict of intake.conflicts) {
      if (conflict.status !== "open") continue;
      groups.conflicts.push({
        id: conflict.id,
        category: conflict.code,
        text: conflict.message,
        source: null,
        locked: false,
        revisable: false,
      });
    }
    return CREATIVE_BRIEF_GROUPS.map((group) => ({
      ...group,
      rows: groups[group.id],
    }));
  }

  function buildBriefRevisionMessage(item) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      throw new TypeError("brief item is invalid");
    }
    const id = typeof item.id === "string" ? item.id.trim() : "";
    const category =
      typeof item.category === "string" ? item.category.trim() : "";
    const text = typeof item.text === "string" ? item.text.trim() : "";
    if (
      !id ||
      !category ||
      !text ||
      Array.from(text).length > 20_000 ||
      Array.from(category).length > 128
    ) {
      throw new TypeError("brief item is invalid");
    }
    return `我想修改简报中的「${text}」（${category}），请先问我需要怎么改。`;
  }

  function reconstructDirectorMessages(value) {
    const intake = normalizeCreativeIntake(value);
    const messages = [];
    if (intake.inputs.text.trim()) {
      messages.push({ role: "user", text: intake.inputs.text.trim() });
    }
    if (intake.directions.length) {
      messages.push({
        role: "assistant",
        text: `创作方向：${intake.directions
          .map((direction) =>
            `${direction.label}${direction.summary ? ` — ${direction.summary}` : ""}`
          )
          .join("；")}`,
      });
    }
    const selected = intake.directions.find(
      (direction) => direction.id === intake.selectedDirectionId
    );
    if (selected) {
      messages.push({
        role: "system",
        text: `已选择方向：${selected.label}`,
      });
    }
    if (intake.brief) {
      messages.push({
        role: "assistant",
        text: `创作简报：${intake.brief.summary}`,
      });
      if (intake.brief.status === "confirmed") {
        messages.push({
          role: "system",
          text: "创作简报已确认并锁定。",
        });
      }
    }
    if (intake.selectedModelProfileId) {
      messages.push({
        role: "system",
        text: `已选择目标模型：${intake.selectedModelProfileId}`,
      });
    }
    return messages;
  }

  function creativeIntakeRequestGuard(state, sessionId) {
    return {
      sessionId,
      projectRevision: state.projectRevision,
      creativeIntakeRevision: state.creativeIntake?.revision,
    };
  }

  function buildCreativeDirectorRequest(state, sessionId, message) {
    const normalizedMessage = String(message ?? "").trim();
    if (!normalizedMessage) {
      throw new TypeError("creative director message must not be empty");
    }
    const evidence = state.directorImageEvidence
      ? clone(state.directorImageEvidence)
      : null;
    return {
      path: "/api/creative-intake/director",
      guard: creativeIntakeRequestGuard(state, sessionId),
      body: {
        current: normalizeCreativeIntake(state.creativeIntake),
        message: normalizedMessage,
        imageEvidence: evidence,
        provider: state.settings?.textProvider || "local",
        skillOverride:
          typeof state.settings?.creativeDirectorSkillOverride === "string"
            ? state.settings.creativeDirectorSkillOverride
            : "",
      },
    };
  }

  function buildDirectorMessageInputAction(value, message) {
    const intake = normalizeCreativeIntake(value);
    const normalizedMessage = String(message ?? "").trim();
    if (!normalizedMessage) {
      throw new TypeError("creative director message must not be empty");
    }
    const currentText = intake.inputs.text.trim();
    if (
      currentText === normalizedMessage ||
      currentText.endsWith(`\n${normalizedMessage}`)
    ) {
      return null;
    }
    const text = currentText
      ? `${currentText}\n${normalizedMessage}`
      : normalizedMessage;
    if (Array.from(text).length > 20_000) {
      throw new TypeError(
        "creative director canonical input exceeds 20000 characters"
      );
    }
    return {
      type: "replace_inputs",
      text,
      images: clone(intake.inputs.images),
    };
  }

  function buildCreativeIntakeTransitionRequest(
    state,
    sessionId,
    action
  ) {
    if (!action || typeof action !== "object" || Array.isArray(action)) {
      throw new TypeError("creative intake action must be an object");
    }
    if (action.type === "select_model") {
      const profileId = action.modelProfileId;
      if (
        !state.modelProfiles?.some(
          (profile) => profile.profileId === profileId
        )
      ) {
        throw new TypeError("select_model requires a loaded model profile");
      }
    }
    return {
      path: "/api/creative-intake/transition",
      guard: creativeIntakeRequestGuard(state, sessionId),
      body: {
        current: normalizeCreativeIntake(state.creativeIntake),
        action: clone(action),
      },
    };
  }

  function sortedJsonValue(value) {
    if (Array.isArray(value)) return value.map(sortedJsonValue);
    if (value && typeof value === "object") {
      return Object.fromEntries(
        Object.keys(value)
          .sort()
          .map((key) => [key, sortedJsonValue(value[key])])
      );
    }
    return value;
  }

  function requireCanonicalCreativeIntake(value) {
    const normalized = normalizeCreativeIntake(value);
    if (
      !value ||
      typeof value !== "object" ||
      Array.isArray(value) ||
      JSON.stringify(sortedJsonValue(value)) !==
        JSON.stringify(sortedJsonValue(normalized))
    ) {
      throw new TypeError(
        "creative director response requires a canonical creative intake item"
      );
    }
    return normalized;
  }

  function requireDirectorMessage(value, required) {
    if (value === undefined && !required) return "";
    if (typeof value !== "string") {
      throw new TypeError("director message must be text");
    }
    let length = 0;
    for (let index = 0; index < value.length; index += 1) {
      const unit = value.charCodeAt(index);
      if (unit >= 0xd800 && unit <= 0xdbff) {
        const trailing = value.charCodeAt(index + 1);
        if (!(trailing >= 0xdc00 && trailing <= 0xdfff)) {
          throw new TypeError("director message must contain valid UTF-8 text");
        }
        index += 1;
      } else if (unit >= 0xdc00 && unit <= 0xdfff) {
        throw new TypeError("director message must contain valid UTF-8 text");
      }
      length += 1;
      if (length > 20_000) {
        throw new TypeError("director message exceeds 20000 characters");
      }
    }
    const normalized = value.trim();
    if (required && !normalized) {
      throw new TypeError("director message must not be empty");
    }
    return normalized;
  }

  function acceptCreativeIntakeResponse(
    state,
    request,
    sessionId,
    response,
    userMessage = ""
  ) {
    if (!isCreativeIntakeResponseCurrent(request, sessionId, state)) {
      return { accepted: false, state };
    }
    if (!response || typeof response !== "object" || Array.isArray(response)) {
      throw new TypeError("creative intake response must be an object");
    }
    const canonicalItem = requireCanonicalCreativeIntake(response.item);
    const normalizedUserMessage = String(userMessage ?? "").trim();
    const assistantMessage = requireDirectorMessage(
      response.message,
      Boolean(normalizedUserMessage)
    );
    if (
      canonicalItem.revision !==
      Number(request.creativeIntakeRevision) + 1
    ) {
      throw new TypeError(
        "creative intake response must advance exactly one revision"
      );
    }
    let next = state;
    if (normalizedUserMessage) {
      next = reduceState(next, {
        type: "DIRECTOR_MESSAGE_ADDED",
        message: { role: "user", text: normalizedUserMessage },
      });
    }
    if (assistantMessage) {
      next = reduceState(next, {
        type: "DIRECTOR_MESSAGE_ADDED",
        message: { role: "assistant", text: assistantMessage },
      });
    }
    next = reduceState(next, {
      type: "CREATIVE_INTAKE_REPLACED",
      item: canonicalItem,
    });
    return { accepted: true, state: next };
  }

  function buildDirectorRenderModel(state) {
    const intake = normalizeCreativeIntake(state.creativeIntake);
    const stageView = creativeDirectorStageView(intake.stage);
    const canonicalModelReady =
      state.modelProfilesStatus === "ready" &&
      Boolean(
        state.modelProfiles.find(
          (profile) =>
            profile?.profileId === intake.selectedModelProfileId
        )
      );
    return {
      stage: intake.stage,
      messages:
        Array.isArray(state.directorMessages) &&
        state.directorMessages.length
          ? clone(state.directorMessages)
          : reconstructDirectorMessages(intake),
      busy: Boolean(state.directorBusy),
      error:
        typeof state.directorError === "string"
          ? state.directorError
          : "",
      imageEvidence: state.directorImageEvidence
        ? clone(state.directorImageEvidence)
        : null,
      imageAnalysisStatus:
        state.directorImageAnalysisStatus || "idle",
      imageAnalysisFailures: Array.isArray(
        state.directorImageAnalysisFailures
      )
        ? clone(state.directorImageAnalysisFailures)
        : [],
      directions: intake.directions.map((direction) => ({
        ...clone(direction),
        selected: direction.id === intake.selectedDirectionId,
      })),
      brief: intake.brief ? clone(intake.brief) : null,
      briefGroups: buildCreativeBriefGroups(intake),
      conflicts: clone(intake.conflicts),
      canConfirmBrief: canConfirmCreativeBrief(intake),
      showModelGate: stageView.showModelGate,
      canContinue: stageView.canContinue && canonicalModelReady,
      showWorkbench:
        stageView.showWorkbench &&
        canonicalModelReady &&
        state.view !== "home",
      selectedModelProfileId: intake.selectedModelProfileId,
      modelProfiles: (Array.isArray(state.modelProfiles)
        ? state.modelProfiles
        : []
      )
        .filter(
          (profile) =>
            profile && typeof profile.profileId === "string"
        )
        .map((profile) => ({
          id: profile.profileId,
          label:
            typeof profile.displayName === "string" &&
            profile.displayName.trim()
              ? profile.displayName
              : profile.profileId,
          readiness:
            typeof profile.readiness === "string" &&
            profile.readiness.trim()
              ? profile.readiness
              : typeof profile.validationStatus === "string"
                ? profile.validationStatus
                : "",
          generationReady: Boolean(profile.generationReady),
          evidence: (Array.isArray(profile.evidence)
            ? profile.evidence
            : []
          )
            .slice(0, 16)
            .map((item) => ({
              id:
                typeof item?.id === "string"
                  ? Array.from(item.id).slice(0, 256).join("")
                  : "",
              title:
                typeof item?.title === "string"
                  ? Array.from(item.title).slice(0, 1_000).join("")
                  : "",
              verificationStatus:
                typeof item?.verificationStatus === "string"
                  ? Array.from(item.verificationStatus)
                      .slice(0, 256)
                      .join("")
                  : "",
            })),
          selected:
            profile.profileId === intake.selectedModelProfileId,
        })),
    };
  }

  async function performCreativeIntakeRequest({
    getState,
    sessionId,
    request,
    userMessage = "",
    apiCall,
    signal,
  }) {
    if (typeof getState !== "function" || typeof apiCall !== "function") {
      throw new TypeError(
        "creative intake request requires state and API functions"
      );
    }
    const response = await apiCall(request.path, {
      method: "POST",
      body: JSON.stringify(request.body),
      ...(signal ? { signal } : {}),
    });
    return acceptCreativeIntakeResponse(
      getState(),
      request.guard,
      sessionId,
      response,
      userMessage
    );
  }

  async function persistCreativeIntakeRevision({
    getState,
    targetProjectRevision,
    waitedForFrozenSave = false,
    waitForFrozenSave,
    saveMetadata,
  }) {
    if (
      typeof getState !== "function" ||
      typeof saveMetadata !== "function" ||
      (waitedForFrozenSave && typeof waitForFrozenSave !== "function")
    ) {
      throw new TypeError(
        "creative intake persistence requires state and metadata save functions"
      );
    }
    let result = null;
    if (waitedForFrozenSave) {
      result = await waitForFrozenSave();
      if (!result) return result;
    }
    const current = getState();
    if (
      current.hasUnsavedProjectChanges &&
      Number(current.projectRevision) >= Number(targetProjectRevision)
    ) {
      result = await saveMetadata();
    }
    return result;
  }

  function normalizeCreativeIntake(value) {
    function fail() {
      throw new TypeError("invalid creative intake");
    }

    function object(source, allowedKeys) {
      if (!source || typeof source !== "object" || Array.isArray(source)) fail();
      if (Object.keys(source).some((key) => !allowedKeys.has(key))) fail();
      return source;
    }

    function array(source, maximum) {
      if (!Array.isArray(source) || source.length > maximum) fail();
      return source;
    }

    function text(source, maximum, allowEmpty = true) {
      if (typeof source !== "string" || (!allowEmpty && !source)) fail();
      let length = 0;
      for (let index = 0; index < source.length; index += 1) {
        const unit = source.charCodeAt(index);
        if (unit >= 0xd800 && unit <= 0xdbff) {
          const next = source.charCodeAt(index + 1);
          if (!(next >= 0xdc00 && next <= 0xdfff)) fail();
          index += 1;
        } else if (unit >= 0xdc00 && unit <= 0xdfff) {
          fail();
        }
        length += 1;
        if (length > maximum) fail();
      }
      return source;
    }

    function identifier(source) {
      const result = text(source, 128, false);
      if (/\s/u.test(result)) fail();
      return result;
    }

    function unique(items, selector = (item) => item) {
      const values = items.map(selector);
      if (new Set(values).size !== values.length) fail();
      return items;
    }

    function textArray(source, maximum) {
      return unique(
        array(source, maximum).map((entry) => text(entry, 4_096, false))
      );
    }

    function sourceItem(source, imageIds) {
      const item = object(source, new Set(["type", "refId"]));
      const type = text(item.type, 64, false);
      if (!CREATIVE_INTAKE_SOURCE_TYPES.has(type)) fail();
      let refId = item.refId ?? null;
      if (type === "user" || type === "ai") {
        if (refId !== null) fail();
      } else {
        refId = identifier(refId);
        if (type === "image" && !imageIds.has(refId)) fail();
      }
      return { type, refId };
    }

    function image(source) {
      const item = object(
        source,
        new Set(["id", "name", "mimeType", "status", "requestedUses"])
      );
      const name = text(item.name, 512, false);
      if (
        name === "." ||
        name === ".." ||
        name.includes("/") ||
        name.includes("\\") ||
        name.includes(":")
      ) {
        fail();
      }
      return {
        id: identifier(item.id),
        name,
        mimeType: text(item.mimeType, 128, false),
        status: text(item.status, 128, false),
        requestedUses: textArray(item.requestedUses, 32),
      };
    }

    function briefItem(source, imageIds) {
      const item = object(
        source,
        new Set(["id", "category", "text", "source", "locked"])
      );
      if (typeof item.locked !== "boolean") fail();
      return {
        id: identifier(item.id),
        category: text(item.category, 128, false),
        text: text(item.text, 200_000),
        source: sourceItem(item.source, imageIds),
        locked: item.locked,
      };
    }

    function brief(source, imageIds) {
      if (source === null) return null;
      const item = object(
        source,
        new Set([
          "status",
          "summary",
          "items",
          "aiAdditions",
          "openQuestions",
        ])
      );
      const status = text(item.status, 32, false);
      if (!new Set(["draft", "confirmed"]).has(status)) fail();
      const items = unique(
        array(item.items, 200).map((entry) => briefItem(entry, imageIds)),
        (entry) => entry.id
      );
      if (status === "confirmed") {
        items.forEach((entry) => {
          entry.locked = true;
        });
      }
      return {
        status,
        summary: text(item.summary, 200_000),
        items,
        aiAdditions: textArray(item.aiAdditions, 200),
        openQuestions: textArray(item.openQuestions, 200),
      };
    }

    function decompositionBlock(source, imageIds) {
      const item = object(
        source,
        new Set([
          "id",
          "category",
          "zh",
          "en",
          "source",
          "locked",
          "approved",
          "reason",
          "risks",
        ])
      );
      if (
        typeof item.locked !== "boolean" ||
        typeof item.approved !== "boolean"
      ) {
        fail();
      }
      return {
        id: identifier(item.id),
        category: text(item.category, 128, false),
        zh: text(item.zh, 200_000),
        en: text(item.en, 200_000),
        source: sourceItem(item.source, imageIds),
        locked: item.locked,
        approved: item.approved,
        reason: text(item.reason, 200_000),
        risks: textArray(item.risks, 100),
      };
    }

    function decomposition(source, imageIds) {
      if (source === null) return null;
      const item = object(source, new Set(["status", "blocks"]));
      const status = text(item.status, 32, false);
      if (!new Set(["draft", "confirmed"]).has(status)) fail();
      return {
        status,
        blocks: unique(
          array(item.blocks, 200).map((entry) =>
            decompositionBlock(entry, imageIds)
          ),
          (entry) => entry.id
        ),
      };
    }

    function conflict(source) {
      const item = object(
        source,
        new Set(["id", "code", "message", "status", "itemIds"])
      );
      const status = text(item.status, 32, false);
      if (!new Set(["open", "resolved"]).has(status)) fail();
      return {
        id: identifier(item.id),
        code: text(item.code, 128, false),
        message: text(item.message, 200_000),
        status,
        itemIds: unique(
          array(item.itemIds, 200).map((entry) => identifier(entry))
        ),
      };
    }

    try {
      const intake = object(
        value,
        new Set([
          "schemaVersion",
          "revision",
          "stage",
          "inputs",
          "directions",
          "selectedDirectionId",
          "brief",
          "selectedModelProfileId",
          "decomposition",
          "recipeStatus",
          "conflicts",
        ])
      );
      if (intake.schemaVersion !== 1) fail();
      if (!Number.isSafeInteger(intake.revision) || intake.revision < 0) fail();
      const stage = text(intake.stage, 64, false);
      if (!CREATIVE_INTAKE_STAGES.has(stage)) fail();
      const inputs = object(intake.inputs, new Set(["text", "images"]));
      const images = unique(array(inputs.images, 8).map(image), (item) => item.id);
      const imageIds = new Set(images.map((item) => item.id));
      const directions = unique(
        array(intake.directions, 3).map((source) => {
          const item = object(source, new Set(["id", "label", "summary"]));
          return {
            id: identifier(item.id),
            label: text(item.label, 512, false),
            summary: text(item.summary, 200_000),
          };
        }),
        (item) => item.id
      );
      let selectedDirectionId = intake.selectedDirectionId;
      if (selectedDirectionId !== null) {
        selectedDirectionId = identifier(selectedDirectionId);
        if (!directions.some((item) => item.id === selectedDirectionId)) fail();
      }
      let selectedModelProfileId = intake.selectedModelProfileId;
      if (selectedModelProfileId !== null) {
        selectedModelProfileId = identifier(selectedModelProfileId);
      }
      const normalizedBrief = brief(intake.brief, imageIds);
      const normalizedDecomposition = decomposition(
        intake.decomposition,
        imageIds
      );
      const stageIndex = CREATIVE_INTAKE_STAGE_ORDER.indexOf(stage);
      const recipeStatus = text(intake.recipeStatus, 32, false);
      if (!CREATIVE_INTAKE_RECIPE_STATUSES.has(recipeStatus)) fail();
      const conflicts = unique(
        array(intake.conflicts, 100).map(conflict),
        (item) => item.id
      );
      const directionRequired =
        stageIndex >= CREATIVE_INTAKE_STAGE_ORDER.indexOf("direction_selected");
      if ((selectedDirectionId !== null) !== directionRequired) fail();

      const expectedBriefStatus =
        stageIndex < CREATIVE_INTAKE_STAGE_ORDER.indexOf("brief_draft")
          ? null
          : stage === "brief_draft"
            ? "draft"
            : "confirmed";
      if (expectedBriefStatus === null) {
        if (normalizedBrief !== null) fail();
      } else {
        if (
          normalizedBrief === null ||
          normalizedBrief.status !== expectedBriefStatus
        ) {
          fail();
        }
        if (
          expectedBriefStatus === "confirmed" &&
          normalizedBrief.items.some((item) => !item.locked)
        ) {
          fail();
        }
        if (
          expectedBriefStatus === "confirmed" &&
          (normalizedBrief.openQuestions.length > 0 ||
            conflicts.some((item) => item.status === "open"))
        ) {
          fail();
        }
      }

      const modelRequired =
        stageIndex >= CREATIVE_INTAKE_STAGE_ORDER.indexOf("model_selected");
      if ((selectedModelProfileId !== null) !== modelRequired) fail();

      const expectedDecompositionStatus =
        stageIndex <
        CREATIVE_INTAKE_STAGE_ORDER.indexOf("decomposition_draft")
          ? null
          : stage === "decomposition_draft"
            ? "draft"
            : "confirmed";
      if (expectedDecompositionStatus === null) {
        if (normalizedDecomposition !== null) fail();
      } else if (
        normalizedDecomposition === null ||
        normalizedDecomposition.status !== expectedDecompositionStatus
      ) {
        fail();
      }
      if (
        expectedDecompositionStatus === "confirmed" &&
        normalizedDecomposition.blocks.some((block) => !block.approved)
      ) {
        fail();
      }

      const recipeStatusAllowed =
        stageIndex <=
        CREATIVE_INTAKE_STAGE_ORDER.indexOf("direction_selected")
          ? recipeStatus === "missing"
          : stageIndex <=
              CREATIVE_INTAKE_STAGE_ORDER.indexOf("brief_confirmed")
            ? new Set(["missing", "stale"]).has(recipeStatus)
            : stageIndex <=
                CREATIVE_INTAKE_STAGE_ORDER.indexOf("decomposition_draft")
              ? recipeStatus === "stale"
              : recipeStatus === "ready";
      if (!recipeStatusAllowed) fail();
      return {
        schemaVersion: 1,
        revision: intake.revision,
        stage,
        inputs: {
          text: text(inputs.text, 200_000),
          images,
        },
        directions,
        selectedDirectionId,
        brief: normalizedBrief,
        selectedModelProfileId,
        decomposition: normalizedDecomposition,
        recipeStatus,
        conflicts,
      };
    } catch {
      return emptyCreativeIntake();
    }
  }

  function persistedVersionSnapshot(item) {
    const metadata = safeObject(item?.metadata);
    const version = Number(item?.version);
    if (!Number.isSafeInteger(version) || version < 1) return null;
    return {
      id: typeof item.id === "string" ? item.id : "",
      version,
      source: typeof item.source === "string" ? item.source : "manual",
      output: {
        positiveEn: typeof item.positiveEn === "string" ? item.positiveEn : "",
        positiveZh: typeof item.positiveZh === "string" ? item.positiveZh : "",
        negativeEn: typeof item.negativeEn === "string" ? item.negativeEn : "",
        negativeZh: typeof item.negativeZh === "string" ? item.negativeZh : "",
        relationEn:
          typeof metadata.relationEn === "string" ? metadata.relationEn : "",
        relationZh:
          typeof metadata.relationZh === "string" ? metadata.relationZh : "",
      },
      blocks: normalizeBlocks(Array.isArray(item.blocks) ? item.blocks : []),
      metadata: clone(metadata),
      restoredFrom:
        Number.isSafeInteger(Number(metadata.restoredFromVersion)) &&
        Number(metadata.restoredFromVersion) > 0
          ? Number(metadata.restoredFromVersion)
          : null,
      createdAt: typeof item.createdAt === "string" ? item.createdAt : "",
    };
  }

  function hydrateProjectState(state, project) {
    const next = clone(state);
    const workspaceDefaultModelProfileId = next.modelProfileId;
    const workspaceDefaultGenerationParameters = clone(
      next.generationParameters
    );
    next.analyzers = resetAnalyzerWorkspaceResults(next.analyzers);
    const metadata = safeObject(project?.metadata);
    const history = (Array.isArray(project?.versions) ? project.versions : [])
      .map(persistedVersionSnapshot)
      .filter(Boolean)
      .sort((left, right) => left.version - right.version);
    const latest = history.at(-1) || null;
    const latestMetadata = safeObject(latest?.metadata);
    const latestRecipe = safeObject(latestMetadata.recipe);
    const latestParameters = safeObject(latestRecipe.parameters);
    const projectWorkspaceVersion = Number(metadata.workspaceBaseVersion);
    const projectMetadataIsCurrent =
      !latest ||
      (Number.isSafeInteger(projectWorkspaceVersion) &&
        projectWorkspaceVersion === latest.version);

    next.creativeIntake = projectMetadataIsCurrent
      ? normalizeCreativeIntake(metadata.creativeIntake)
      : emptyCreativeIntake();
    next.directorMessages = reconstructDirectorMessages(next.creativeIntake);
    next.directorBusy = false;
    next.directorError = "";
    next.directorImageEvidence = null;
    next.directorImageAnalysisStatus = "idle";
    next.directorImageAnalysisFailures = [];
    next.directorRequestRevision = 0;
    next.projectId = typeof project?.id === "string" ? project.id : null;
    next.projectName =
      typeof project?.name === "string" && project.name.trim()
        ? project.name
        : "未命名作品";
    next.projectStatus =
      typeof project?.status === "string" ? project.status : "draft";
    next.projectUpdatedAt =
      typeof project?.updatedAt === "string" ? project.updatedAt : "";
    next.view = projectMetadataIsCurrent
      ? project?.mode === "image"
        ? "image"
        : "text"
      : latest?.source === "image"
        ? "image"
        : "text";
    next.textMode =
      projectMetadataIsCurrent && typeof metadata.textMode === "string"
        ? metadata.textMode
        : typeof latestMetadata.textMode === "string"
          ? latestMetadata.textMode
          : "expand";
    next.draftInput =
      projectMetadataIsCurrent && typeof metadata.draftInput === "string"
        ? metadata.draftInput
        : typeof latestMetadata.draftInput === "string"
          ? latestMetadata.draftInput
          : "";
    next.randomSeed = latestMetadata.randomSeed ?? null;
    next.modelProfileId =
      typeof latestRecipe.model?.profileId === "string"
        ? latestRecipe.model.profileId
        : "anima-1.1-v1";
    for (const key of Object.keys(next.generationParameters)) {
      if (latestParameters[key] && "value" in latestParameters[key]) {
        next.generationParameters[key] = clone(latestParameters[key].value);
      }
    }
    next.manualParameterKeys = Object.entries(latestParameters)
      .filter(([, item]) => item?.source === "manual_override")
      .map(([key]) => key);
    next.randomPlan = latestRecipe.randomPlan
      ? clone(latestRecipe.randomPlan)
      : null;
    next.instructionHistory = Array.isArray(latestRecipe.instructionHistory)
      ? clone(latestRecipe.instructionHistory)
      : [];
    next.recipeHash =
      typeof latestMetadata.recipeHash === "string"
        ? latestMetadata.recipeHash
        : "";
    next.pendingEditPreview = null;
    next.pendingChange = null;
    next.imageName =
      projectMetadataIsCurrent && typeof metadata.imageName === "string"
        ? metadata.imageName
        : typeof latestMetadata.imageName === "string"
          ? latestMetadata.imageName
          : "";
    next.imageMimeType = "";
    next.imageSize = 0;
    next.imageLoaded = false;
    next.versionHistory = history;
    next.version = latest?.version || 0;
    next.output = latest ? clone(latest.output) : emptyOutput();
    next.blocks = latest ? clone(latest.blocks) : [];
    next.appliedBlocks = latest ? clone(latest.blocks) : [];
    next.outputChecks = clone(safeObject(latestMetadata.outputChecks));
    next.dirtyBlockIds = [];
    next.previewOutput = null;
    next.selectedVariantBlockIds = [];
    next.batchRegenerating = false;
    next.textGenerating = false;
    next.textDecomposing = false;
    next.translatingPending = false;
    next.regeneratingBlockId = "";
    next.generationProgress = idleGenerationProgress();
    next.restoreFromVersion = null;
    next.hasUnsavedChanges = false;
    next.hasUnsavedProjectChanges = false;
    next.workingRevision = 0;
    next.projectRevision = 0;
    next.saveStatus = "idle";
    next.saveError = "";
    next.lastSavedAt = latest?.createdAt || project?.updatedAt || "";
    next.openingProjectId = "";
    next.openError = "";
    next.rawMergedResult = latest?.output.positiveEn || "";
    next.activeRawResult = "merged";
    next.analysisQueue = [];
    next.analysisComplete = Boolean(
      next.view === "image" && latest?.source === "image"
    );
    next.analysisNotice = latest
      ? next.analysisComplete
        ? "已恢复图片解析结果；原始图片需重新载入"
        : "已恢复项目最新版本"
      : "项目尚未保存提示词版本";
    const hasCanonicalCreativeIntake =
      projectMetadataIsCurrent &&
      metadata.creativeIntake &&
      typeof metadata.creativeIntake === "object" &&
      !Array.isArray(metadata.creativeIntake);
    if (
      hasCanonicalCreativeIntake &&
      !creativeDirectorStageView(next.creativeIntake.stage).canContinue
    ) {
      clearCreativeDownstreamWorkspace(next);
      next.modelProfileId = workspaceDefaultModelProfileId;
      next.generationParameters = workspaceDefaultGenerationParameters;
      next.manualParameterKeys = [];
      next.view = "home";
      next.rawMergedResult = "";
      next.analysisComplete = false;
    } else if (
      hasCanonicalCreativeIntake &&
      creativeDirectorStageView(next.creativeIntake.stage).canContinue &&
      !applyCanonicalModelToWorkspace(next)
    ) {
      next.view = "home";
    }
    next.toast = `已打开作品：${next.projectName}`;
    return next;
  }

  function createNewProjectState(state) {
    const next = createInitialState();
    for (const key of [
      "settings",
      "references",
      "resources",
      "projects",
    ]) {
      next[key] = clone(state[key]);
    }
    next.analyzers = resetAnalyzerWorkspaceResults(state.analyzers);
    next.projectsStatus = state.projectsStatus;
    next.projectsError = state.projectsError;
    return next;
  }

  function buildProjectPayload(state) {
    const payload = {
      name: String(state.projectName || "").trim() || "未命名作品",
      mode: state.view === "image" ? "image" : "text",
      status: state.projectStatus || "draft",
      metadata: {
        textMode: state.textMode,
        draftInput: state.draftInput,
        settings: projectSettingsMetadata(state.settings),
        imageName: state.imageName,
        creativeIntake: normalizeCreativeIntake(state.creativeIntake),
        workspaceBaseVersion: Number.isSafeInteger(state.version)
          ? state.version
          : 0,
      },
    };
    if (
      state.projectId &&
      typeof state.projectUpdatedAt === "string" &&
      state.projectUpdatedAt
    ) {
      payload.baseUpdatedAt = state.projectUpdatedAt;
    }
    return payload;
  }

  function buildVersionPayload(state) {
    const manualOverride = {};
    for (const key of Array.isArray(state.manualParameterKeys)
      ? state.manualParameterKeys
      : []) {
      if (key in safeObject(state.generationParameters)) {
        manualOverride[key] = clone(state.generationParameters[key]);
      }
    }
    const metadata = {
      textMode: state.textMode,
      draftInput: state.draftInput,
      relationEn: state.output.relationEn || "",
      relationZh: state.output.relationZh || "",
      randomSeed: state.randomSeed,
      outputChecks: safeObject(state.outputChecks),
      imageName: state.imageName || "",
      modelProfileId: state.modelProfileId || "anima-1.1-v1",
      parameterLayers: {
        model_default: {},
        lora_requirement: {},
        task_preset: {
          resolution: clone(
            state.generationParameters?.resolution || {
              width: 1024,
              height: 1024,
            }
          ),
          generationSeed: Number.isSafeInteger(
            Number(state.generationParameters?.generationSeed)
          )
            ? Number(state.generationParameters.generationSeed)
            : 0,
        },
        manual_override: manualOverride,
      },
      randomPlan: state.randomPlan ? clone(state.randomPlan) : null,
      instructionHistory: Array.isArray(state.instructionHistory)
        ? clone(state.instructionHistory)
        : [],
      change: state.pendingChange ? clone(state.pendingChange) : null,
      parentVersion: state.pendingChange
        ? Number.isSafeInteger(state.version)
          ? state.version
          : 0
        : null,
      imageRefs: state.imageName
        ? [{ name: state.imageName, status: "local_reference_not_embedded" }]
        : [],
    };
    if (Number.isSafeInteger(state.restoreFromVersion)) {
      metadata.restoredFromVersion = state.restoreFromVersion;
    }
    const payload = {
      baseVersion: Number.isSafeInteger(state.version) ? state.version : 0,
      source: state.view === "image" ? "image" : state.textMode || "manual",
      positiveEn: state.output.positiveEn || "",
      positiveZh: state.output.positiveZh || "",
      negativeEn: state.output.negativeEn || "",
      negativeZh: state.output.negativeZh || "",
      blocks: normalizeBlocks(state.appliedBlocks),
      metadata,
    };
    if (
      state.projectId &&
      typeof state.projectUpdatedAt === "string" &&
      state.projectUpdatedAt
    ) {
      payload.baseUpdatedAt = state.projectUpdatedAt;
    }
    return payload;
  }

  function createClientId(prefix, randomUuid) {
    const source =
      typeof randomUuid === "function"
        ? randomUuid()
        : typeof globalThis !== "undefined" &&
            globalThis.crypto &&
            typeof globalThis.crypto.randomUUID === "function"
          ? globalThis.crypto.randomUUID()
          : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    const safe = String(source || "")
      .toLowerCase()
      .replace(/[^a-z0-9._~-]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 96);
    return `${prefix}-${safe || "local"}`.slice(0, 128);
  }

  function buildWorkspaceCommitPayload(state, ids = {}) {
    const operationId = ids.operationId || createClientId("save");
    const projectId = state.projectId || ids.projectId || createClientId("project");
    const creatingProject = !state.projectId;
    const project = {
      ...buildProjectPayload(state),
      id: projectId,
    };
    if (creatingProject) delete project.baseUpdatedAt;
    const shouldCreateVersion = Boolean(state.hasUnsavedChanges);
    const nextVersion = shouldCreateVersion
      ? (Number.isSafeInteger(state.version) ? state.version : 0) + 1
      : Number.isSafeInteger(state.version)
        ? state.version
        : 0;
    project.metadata = {
      ...safeObject(project.metadata),
      workspaceBaseVersion: nextVersion,
    };
    let version = null;
    if (shouldCreateVersion) {
      version = {
        ...buildVersionPayload(state),
        id: ids.versionId || createClientId("version"),
      };
      delete version.baseUpdatedAt;
    }
    return {
      operationId,
      createProject: creatingProject,
      project,
      version,
    };
  }

  function buildCreativeIntakeWorkspaceCommitPayload(state, ids = {}) {
    const operationId = ids.operationId || createClientId("save");
    const projectId =
      state.projectId || ids.projectId || createClientId("project");
    const creatingProject = !state.projectId;
    const projectPayload = buildProjectPayload(state);
    const project = {
      ...projectPayload,
      id: projectId,
      metadata: {
        ...projectPayload.metadata,
        workspaceBaseVersion: Number.isSafeInteger(state.version)
          ? state.version
          : 0,
      },
    };
    if (creatingProject) delete project.baseUpdatedAt;
    return {
      operationId,
      createProject: creatingProject,
      project,
      version: null,
    };
  }

  function createCreativeIntakeSaveJournal(state, options = {}) {
    const operationId =
      options.operationId || createClientId("save");
    const projectId =
      state.projectId ||
      options.projectId ||
      createClientId("project");
    const request = buildCreativeIntakeWorkspaceCommitPayload(state, {
      operationId,
      projectId,
    });
    return {
      schema: 1,
      kind: "creative_intake_metadata",
      status: "pending",
      operationId,
      projectId,
      originalProjectId: state.projectId || "",
      savedRevision: state.workingRevision,
      savedProjectRevision: state.projectRevision,
      originSessionId: options.originSessionId ?? 0,
      requestBody: JSON.stringify(request),
      committedResult: null,
      createdAt: options.createdAt || new Date().toISOString(),
    };
  }

  function validateCreativeIntakeMetadataCommitResult(journal, result) {
    if (result?.version !== null) {
      throw new Error(
        "Creative intake metadata commit must not contain a Recipe version"
      );
    }
    const updatedAt = result?.project?.updatedAt;
    if (
      typeof updatedAt !== "string" ||
      !updatedAt.trim() ||
      updatedAt.length > 128 ||
      Number.isNaN(Date.parse(updatedAt))
    ) {
      throw new Error(
        "Creative intake metadata commit requires a valid project.updatedAt"
      );
    }
    return result;
  }

  function rollbackCreativeIntakeAfterPersistenceFailure(
    durableState,
    failedState,
    error
  ) {
    const restoredBase = clone(failedState);
    for (const key of [
      "creativeIntake",
      "directorMessages",
      "directorImageEvidence",
      "directorImageAnalysisStatus",
      "directorImageAnalysisFailures",
      "directorRequestRevision",
      "view",
      "draftInput",
      "blocks",
      "appliedBlocks",
      "dirtyBlockIds",
      "previewOutput",
      "output",
      "outputChecks",
      "pendingEditPreview",
      "pendingChange",
      "recipeHash",
      "randomPlan",
      "instructionHistory",
      "selectedVariantBlockIds",
      "hasUnsavedChanges",
      "hasUnsavedProjectChanges",
      "projectRevision",
    ]) {
      restoredBase[key] = clone(durableState[key]);
    }
    const restored = reduceState(restoredBase, {
      type: "DIRECTOR_REQUEST_FAILED",
      error,
    });
    return restored;
  }

  function shouldConfirmWorkspaceDiscard(state) {
    const namedOrStartedProject = Boolean(
      state.projectId ||
        String(state.projectName || "").trim() !== "未命名作品" ||
        String(state.draftInput || "").trim() ||
        state.imageName ||
        Number(state.creativeIntake?.revision) > 0
    );
    return Boolean(
      state.dirtyBlockIds?.length ||
        state.hasUnsavedChanges ||
        (state.hasUnsavedProjectChanges && namedOrStartedProject)
    );
  }

  function markWorkspaceChanged(next) {
    next.hasUnsavedChanges = true;
    next.workingRevision = Number(next.workingRevision || 0) + 1;
    if (next.saveStatus !== "saving") next.saveStatus = "idle";
    next.saveError = "";
  }

  function markProjectChanged(next) {
    next.hasUnsavedProjectChanges = true;
    next.projectRevision = Number(next.projectRevision || 0) + 1;
    if (next.saveStatus !== "saving") next.saveStatus = "idle";
    next.saveError = "";
  }

  function selectedAnalyzerIds(analyzers) {
    return Object.values(analyzers)
      .filter((model) => model.selected && model.available !== false)
      .map((model) => model.id);
  }

  function resetAnalyzerStatuses(analyzers, queue) {
    const next = clone(analyzers);
    Object.values(next).forEach((model) => {
      model.status =
        model.available === false
          ? "unavailable"
          : queue.includes(model.id)
            ? "waiting"
            : "idle";
    });
    if (queue.length) next[queue[0]].status = "running";
    return next;
  }

  function promptFromReference(reference) {
    return {
      positiveEn: reference.promptEn,
      positiveZh: reference.promptZh,
      negativeEn: reference.negativeEn,
      negativeZh: reference.negativeZh,
    };
  }

  function splitPromptItems(value) {
    return String(value || "")
      .split(/[,，;；\n]+/)
      .map((item) => item.replace(PROMPT_EDGE_WHITESPACE_PATTERN, ""))
      .filter(Boolean);
  }

  function formatBlockWeight(weight) {
    const value = normalizeBlockWeight(weight);
    if (value === DEFAULT_BLOCK_WEIGHT) return "";
    return (value / 100).toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
  }

  function formatArtistWeight(weight) {
    const candidate = finiteWeight(weight);
    const value = Number.isFinite(candidate)
      ? Math.min(150, Math.max(10, candidate))
      : DEFAULT_BLOCK_WEIGHT;
    if (value === DEFAULT_BLOCK_WEIGHT) return "";
    return (value / 100).toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
  }

  function compileBlockFragment(block) {
    if (block.id === "negative") return "";
    const weight = normalizeBlockWeight(block.weight);
    if (weight === 0) return "";
    const factor = formatBlockWeight(weight);
    return splitPromptItems(block.en)
      .map((item) => (factor ? `(${item}:${factor})` : item))
      .join(", ");
  }

  function compileBlocks(blocks, relationEn = "", relationZh = "") {
    const negative = blocks.find((block) => block.id === "negative");
    const positive = blocks.filter((block) => block.id !== "negative");
    const seenEn = new Set();
    const enItems = [];
    const seenZh = new Set();
    const zhItems = [];
    for (const block of positive) {
      const weight = normalizeBlockWeight(block.weight);
      if (weight === 0) continue;
      const factor = formatBlockWeight(weight);
      for (const item of splitPromptItems(block.en)) {
        const key = canonicalizePromptItem(item);
        if (seenEn.has(key)) continue;
        seenEn.add(key);
        enItems.push(factor ? `(${item}:${factor})` : item);
      }
      for (const item of splitPromptItems(block.zh)) {
        const key = canonicalizePromptItem(item);
        if (seenZh.has(key)) continue;
        seenZh.add(key);
        zhItems.push(item);
      }
    }
    const positiveEn = enItems.join(", ");
    const positiveZh = zhItems.join("；");
    return {
      positiveEn: [positiveEn, relationEn.trim()].filter(Boolean).join(". "),
      positiveZh: [positiveZh, relationZh.trim()].filter(Boolean).join("。"),
      negativeEn: negative?.en.trim() || "",
      negativeZh: negative?.zh.trim() || "",
      relationEn: relationEn.trim(),
      relationZh: relationZh.trim(),
    };
  }

  function suggestTags(dictionary, token, limit = 8) {
    const query = canonicalizePromptItem(String(token || "").trim());
    if (query.length < 2) return [];
    const source = Array.isArray(dictionary) ? dictionary : [];
    const seen = new Set();
    const starts = [];
    const contains = [];
    for (const raw of source) {
      const tag = String(raw || "").trim();
      const key = canonicalizePromptItem(tag);
      if (!tag || seen.has(key) || key === query) continue;
      seen.add(key);
      if (key.startsWith(query)) starts.push(tag);
      else if (key.includes(query)) contains.push(tag);
    }
    return [...starts, ...contains].slice(0, limit);
  }

  function composeArtistMix(entries) {
    return (entries || [])
      .map((entry) => {
        const tag = String(entry.tag || "").trim();
        if (!tag) return "";
        const factor = formatArtistWeight(entry.weight);
        return factor ? `(${tag}:${factor})` : tag;
      })
      .filter(Boolean)
      .join(", ");
  }

  function refreshPreview(next) {
    next.previewOutput = next.dirtyBlockIds.length
      // A relation narrative was generated from the previous block values. Once
      // any block is edited it is stale and can contradict the structured source
      // of truth, so previews are compiled from the edited blocks only.
      ? compileBlocks(next.blocks)
      : null;
  }

  function initializeVersion(next, output, blocks) {
    const normalizedBlocks = normalizeBlocks(blocks);
    next.output = clone(output);
    next.blocks = clone(normalizedBlocks);
    next.appliedBlocks = clone(normalizedBlocks);
    next.dirtyBlockIds = [];
    next.selectedVariantBlockIds = [];
    next.batchRegenerating = false;
    next.previewOutput = null;
    next.restoreFromVersion = null;
    markWorkspaceChanged(next);
  }

  function markDirty(next, blockId) {
    if (!next.dirtyBlockIds.includes(blockId)) {
      next.dirtyBlockIds.push(blockId);
    }
    // Pending block edits are not persisted until APPLY_CHANGES, but they still
    // invalidate any project-open request that captured an older workspace.
    next.workingRevision = Number(next.workingRevision || 0) + 1;
  }

  function findResource(resources, id) {
    for (const group of Object.values(resources)) {
      const resource = group.find((item) => item.id === id);
      if (resource) return resource;
    }
    return null;
  }

  function blockText(blocks, id) {
    const block = blocks.find((item) => item.id === id);
    return `${block?.en || ""} ${block?.zh || ""}`.toLowerCase();
  }

  function logicalBlockVariants(blockId, blocks) {
    const scene = blockText(blocks, "scene");
    const subject = blockText(blocks, "subject");
    const isRain = /rain|雨|umbrella|伞/.test(scene);
    const isLibrary = /library|book|图书馆|书架/.test(scene);
    const isRooftop = /rooftop|屋顶|高楼/.test(scene);

    const variants = {
      quality: [
        {
          en: "masterpiece, best quality, intricate details, refined finish, contemporary anime aesthetic",
          zh: "杰作、最佳质量、细节繁复、完成度精致、当代动漫审美。",
        },
        {
          en: "award-winning illustration, ultra detailed, clean rendering, polished visual storytelling",
          zh: "获奖级插画、超高细节、干净渲染、成熟的视觉叙事。",
        },
        {
          en: "high quality anime illustration, crisp details, balanced rendering, professional finish",
          zh: "高质量动漫插画、细节清晰、渲染均衡、专业完成度。",
        },
      ],
      artist: [
        {
          en: "@lack, detailed fantasy character illustration, delicate color transitions",
          zh: "@lack，细腻幻想角色插画，色彩过渡柔和。",
        },
        {
          en: "@toi8, clean expressive linework, soft atmospheric colors",
          zh: "@toi8，清爽而富有表现力的线稿，柔和氛围配色。",
        },
        {
          en: "polished anime concept art, graphic color design, confident linework",
          zh: "精致动漫概念插画、平面化色彩设计、利落线条。",
        },
      ],
      subject: [
        {
          en: "1woman, adult woman, solo, long silver hair, composed expression",
          zh: "一位成年女性，单人，银色长发，神情沉着。",
        },
        {
          en: "1woman, adult traveler, solo, short dark hair, observant expression",
          zh: "一位成年女性旅人，单人，深色短发，神情敏锐。",
        },
        {
          en: `1woman, adult ${/pilot|驾驶员/.test(subject) ? "pilot" : "urban explorer"}, solo, confident expression`,
          zh: /pilot|驾驶员/.test(subject)
            ? "一位成年女性驾驶员，单人，神情自信。"
            : "一位成年女性城市探索者，单人，神情自信。",
        },
      ],
      appearance: isRain
        ? [
            {
              en: "long waterproof coat, layered knitwear, clear umbrella, rain-darkened fabric",
              zh: "长款防水外套、层叠针织内搭、透明雨伞、被雨水浸深的布料。",
            },
            {
              en: "cropped rain jacket, pleated skirt, reflective boots, wet loose hair strands",
              zh: "短款雨衣、百褶裙、反光雨靴、被雨打湿的散落发丝。",
            },
            {
              en: "dark trench coat, high collar, leather gloves, silver hair pinned behind one ear",
              zh: "深色长风衣、高领、皮手套、银发别在一侧耳后。",
            },
          ]
        : [
            {
              en: "layered contemporary outfit, textured fabric, restrained accessories",
              zh: "层叠现代服装、富有纹理的面料、克制的配饰。",
            },
            {
              en: "tailored jacket, soft knit inner layer, practical leather shoes",
              zh: "剪裁利落的外套、柔软针织内搭、实用皮鞋。",
            },
          ],
      pose: isRain
        ? [
            {
              en: "walking carefully through shallow puddles, one hand steadying the umbrella",
              zh: "小心走过浅水洼，一只手扶稳雨伞。",
            },
            {
              en: "pausing beneath a shop awning, brushing rain from her sleeve",
              zh: "停在商店雨棚下，轻轻拂去袖口雨水。",
            },
            {
              en: "crouching beside a reflective puddle, adjusting one boot, looking sideways",
              zh: "蹲在倒映灯光的水洼旁整理一只靴子，侧目望向街道。",
            },
          ]
        : isLibrary
          ? [
              {
                en: "reaching toward an upper bookshelf, eyes following the book spines",
                zh: "伸手取高处书架上的书，视线沿着书脊移动。",
              },
              {
                en: "sitting at a wooden desk, turning an archival page with care",
                zh: "坐在木桌前，小心翻动档案页面。",
              },
            ]
          : [
              {
                en: "taking a purposeful step forward, coat moving with the motion",
                zh: "坚定地向前迈步，外套随动作摆动。",
              },
              {
                en: "turning over one shoulder, relaxed posture, attentive gaze",
                zh: "回身越过肩头望去，姿态放松，目光专注。",
              },
            ],
      scene: [
        {
          en: "A narrow neon market street after rainfall, layered signs and distant pedestrians reflected on the pavement.",
          zh: "雨后的狭窄霓虹市场街道，层叠灯牌与远处行人倒映在路面。",
        },
        {
          en: "A glass-roof transit hall at blue hour, quiet platforms opening toward a luminous city skyline.",
          zh: "蓝调时刻的玻璃穹顶交通大厅，安静站台通向明亮城市天际线。",
        },
        {
          en: "A sheltered rooftop garden above the city, wet foliage moving in the evening wind.",
          zh: "城市上方有遮蔽的屋顶花园，湿润植物在晚风中摇动。",
        },
      ],
      effects: isRain
        ? [
            {
              en: "diagonal rain streaks, rippling puddles, fine spray, neon reflections, subtle lens bloom",
              zh: "斜向雨丝、泛起涟漪的水洼、细密水雾、霓虹倒影、轻微镜头辉光。",
            },
            {
              en: "raindrops on the umbrella, drifting mist, tire spray, scattered reflected light",
              zh: "伞面雨滴、漂浮薄雾、车轮水花、散落反射光。",
            },
          ]
        : [
            {
              en: "floating dust, gentle fabric movement, subtle atmospheric particles",
              zh: "漂浮尘埃、轻微布料摆动、克制的氛围粒子。",
            },
            {
              en: "soft haze, drifting leaves, delicate light bloom",
              zh: "柔和薄雾、飘动树叶、细腻光晕。",
            },
          ],
      composition: isRooftop
        ? [
            {
              en: "low-angle full body shot, diagonal skyline, strong wind-driven leading lines",
              zh: "低机位全身镜头、倾斜天际线、由风势形成的强引导线。",
            },
            {
              en: "wide cinematic shot, subject on the lower third, expansive city depth",
              zh: "电影感广角镜头，人物位于下方三分线，城市纵深开阔。",
            },
          ]
        : [
            {
              en: "three-quarter full body view, eye-level camera, layered foreground and background",
              zh: "四分之三全身视角、平视机位、前后景层次分明。",
            },
            {
              en: "medium-wide cinematic framing, asymmetrical balance, clear environmental context",
              zh: "中广景电影感构图、不对称平衡、环境信息清晰。",
            },
            {
              en: "full body portrait, slight low angle, leading lines converging behind the subject",
              zh: "全身人物构图、轻微仰拍、引导线在人物身后汇聚。",
            },
          ],
      lighting: /night|夜|neon|霓虹/.test(scene)
        ? [
            {
              en: "cyan storefront light, warm amber rim light, soft reflected fill from wet pavement",
              zh: "青色店面光、暖琥珀色轮廓光、湿润路面形成的柔和反射补光。",
            },
            {
              en: "magenta and teal neon contrast, cool ambient shadows, luminous rain haze",
              zh: "品红与青绿色霓虹对比、冷色环境阴影、发光雨雾。",
            },
          ]
        : [
            {
              en: "soft window light, warm highlights, cool quiet shadows, gentle atmospheric depth",
              zh: "柔和窗光、暖色高光、安静冷调阴影、轻柔空气透视。",
            },
            {
              en: "diffused afternoon sunlight, subtle rim light, balanced natural colors",
              zh: "漫射午后阳光、轻微轮廓光、均衡自然色彩。",
            },
          ],
      negative: [
        {
          en: "worst quality, low quality, blurry, bad anatomy, malformed hands, extra digits, text, watermark",
          zh: "最差质量、低质量、模糊、错误人体结构、手部畸形、多余手指、文字、水印。",
        },
        {
          en: "low resolution, flat lighting, stiff pose, distorted face, fused fingers, logo, signature",
          zh: "低分辨率、平淡光线、僵硬姿势、脸部变形、手指粘连、标志、签名。",
        },
      ],
    };

    return variants[blockId] || [];
  }

  function regenerateBlock(next, blockId, seed) {
    const block = next.blocks.find((item) => item.id === blockId);
    if (!block) return;
    if (block.locked) {
      next.toast = `“${block.label}”已锁定，请先解锁再重生成`;
      return;
    }

    const variants = logicalBlockVariants(blockId, next.blocks);
    if (!variants.length) return;
    let index = Math.abs(Math.trunc(Number(seed) || Date.now())) % variants.length;
    if (variants[index].en === block.en && variants.length > 1) {
      index = (index + 1) % variants.length;
    }
    const candidate = variants[index];
    block.en = candidate.en;
    block.zh = candidate.zh;
    block.source =
      next.settings.textProvider === "local"
        ? "本地 LLM · 单项重生成"
        : "外部 API · 单项重生成";
    block.confidence = 82 + (index % 3) * 3;
    markDirty(next, block.id);
    next.toast = `已重生成“${block.label}”，等待应用`;
  }

  function reduceState(state, action) {
    const next = clone(state);
    next.blocks = normalizeBlocks(next.blocks);
    next.appliedBlocks = normalizeBlocks(next.appliedBlocks);

    switch (action.type) {
      case "NEW_PROJECT":
        return createNewProjectState(next);
      case "PROJECTS_LOADING":
        next.projectsStatus = "loading";
        next.projectsError = "";
        return next;
      case "PROJECTS_LOADED":
        next.projects = Array.isArray(action.items) ? clone(action.items) : [];
        next.projectsStatus = "ready";
        next.projectsError = "";
        return next;
      case "PROJECTS_FAILED":
        next.projectsStatus = "error";
        next.projectsError = action.error || "项目列表加载失败";
        return next;
      case "PROJECT_OPENING":
        next.openingProjectId = action.id || "";
        next.openError = "";
        return next;
      case "PROJECT_OPENED":
        return hydrateProjectState(next, action.item || {});
      case "PROJECT_OPEN_FAILED":
        next.openingProjectId = "";
        next.openError = action.error || "项目打开失败";
        next.toast = next.openError;
        return next;
      case "WORKSPACE_REQUESTS_CANCELLED":
        next.directorBusy = false;
        next.textGenerating = false;
        next.textDecomposing = false;
        next.translatingPending = false;
        next.regeneratingBlockId = "";
        next.batchRegenerating = false;
        next.analysisQueue = [];
        next.generationProgress = idleGenerationProgress();
        Object.values(next.analyzers).forEach((model) => {
          model.status = model.available === false
            ? "unavailable"
            : model.raw
              ? "ready"
              : "idle";
        });
        return next;
      case "PROJECT_CREATED":
        next.projectId = action.item?.id || next.projectId;
        next.projectUpdatedAt =
          action.item?.updatedAt || next.projectUpdatedAt;
        if (action.savedProjectRevision === next.projectRevision) {
          next.projectName = action.item?.name || next.projectName;
          next.projectStatus = action.item?.status || next.projectStatus;
          next.hasUnsavedProjectChanges = false;
        }
        return next;
      case "PROJECT_SAVE_STARTED":
        next.saveStatus = "saving";
        next.saveError = "";
        return next;
      case "PROJECT_HEADER_SAVED":
        next.projectUpdatedAt = action.updatedAt || next.projectUpdatedAt;
        if (action.savedProjectRevision === next.projectRevision) {
          next.hasUnsavedProjectChanges = false;
        }
        next.saveStatus =
          next.hasUnsavedProjectChanges || next.hasUnsavedChanges
            ? "idle"
            : "saved";
        next.saveError = "";
        next.lastSavedAt = action.updatedAt || next.lastSavedAt;
        next.toast = next.hasUnsavedProjectChanges || next.hasUnsavedChanges
          ? "较早的项目信息已保存，当前修改仍待保存"
          : action.version
            ? `作品已保存为 V${action.version}`
            : `作品信息已保存：${next.projectName || "未命名作品"}`;
        return next;
      case "PROJECT_SAVED": {
        const snapshot = persistedVersionSnapshot(action.item);
        if (!snapshot) {
          next.saveStatus = "error";
          next.saveError = "服务端没有返回有效版本";
          next.toast = next.saveError;
          return next;
        }
        next.versionHistory = next.versionHistory
          .filter((entry) => entry.version !== snapshot.version)
          .concat(snapshot)
          .sort((left, right) => left.version - right.version);
        next.version = snapshot.version;
        next.projectUpdatedAt =
          action.item?.projectUpdatedAt || next.projectUpdatedAt;
        next.lastSavedAt = snapshot.createdAt || action.savedAt || "";
        next.saveStatus = "saved";
        next.saveError = "";
        if (action.savedRevision === next.workingRevision) {
          next.hasUnsavedChanges = false;
          next.restoreFromVersion = null;
          next.pendingChange = null;
        }
        if (action.savedProjectRevision === next.projectRevision) {
          next.hasUnsavedProjectChanges = false;
        }
        if (next.hasUnsavedChanges || next.hasUnsavedProjectChanges) {
          next.saveStatus = "idle";
        }
        next.toast = next.hasUnsavedChanges || next.hasUnsavedProjectChanges
          ? `V${snapshot.version} 已保存，当前还有更新待保存`
          : `作品已保存为 V${snapshot.version}`;
        return next;
      }
      case "PROJECT_SAVE_FAILED":
        next.saveStatus = "error";
        next.saveError = action.error || "作品保存失败";
        next.toast = next.saveError;
        return next;
      case "NAVIGATE":
        if (action.view !== "home" && action.view !== next.view) {
          markProjectChanged(next);
        }
        next.view = action.view;
        next.toast = "";
        return next;
      case "SET_PROJECT_NAME":
        next.projectName = action.value;
        markProjectChanged(next);
        return next;
      case "SET_TEXT_MODE":
        next.view = "text";
        next.textMode = action.mode;
        markProjectChanged(next);
        return next;
      case "SET_DRAFT":
        next.draftInput = action.value;
        markProjectChanged(next);
        return next;
      case "DIRECTOR_MESSAGE_ADDED": {
        const role = action.message?.role;
        const text =
          typeof action.message?.text === "string"
            ? action.message.text.trim()
            : "";
        if (!["user", "assistant", "system"].includes(role) || !text) {
          return next;
        }
        next.directorMessages = [
          ...(Array.isArray(next.directorMessages)
            ? next.directorMessages
            : []),
          { role, text: Array.from(text).slice(0, 20_000).join("") },
        ].slice(-100);
        return next;
      }
      case "DIRECTOR_REQUEST_STARTED":
        next.directorBusy = true;
        next.directorError = "";
        next.directorRequestRevision =
          Number(next.directorRequestRevision || 0) + 1;
        return next;
      case "DIRECTOR_REQUEST_SUCCEEDED":
        next.directorBusy = false;
        next.directorError = "";
        return next;
      case "DIRECTOR_REQUEST_FAILED":
        next.directorBusy = false;
        next.directorError =
          action.error || "创意导演请求失败，请稍后重试";
        return next;
      case "DIRECTOR_IMAGE_EVIDENCE_SET":
        next.directorImageEvidence = action.item
          ? clone(action.item)
          : null;
        next.directorImageAnalysisFailures = Array.isArray(action.failures)
          ? clone(action.failures)
          : [];
        return next;
      case "DIRECTOR_IMAGE_ANALYSIS_STATUS_CHANGED":
        next.directorImageAnalysisStatus =
          typeof action.status === "string" ? action.status : "idle";
        return next;
      case "ENTER_CREATIVE_WORKBENCH":
        if (
          creativeDirectorStageView(next.creativeIntake?.stage).canContinue
        ) {
          if (!applyCanonicalModelToWorkspace(next)) {
            next.toast = "目标模型档案尚未加载完成，请稍后再试";
            return next;
          }
          next.view = "text";
          if (
            !String(next.draftInput || "").trim() &&
            next.creativeIntake.brief?.summary
          ) {
            next.draftInput = next.creativeIntake.brief.summary;
            markProjectChanged(next);
          }
          next.toast =
            "已进入现有工作台；模型适配拆解尚未生成，可使用现有拆解与扩写工具继续。";
        }
        return next;
      case "CREATIVE_INTAKE_REPLACED":
        {
          const hadDownstream =
            creativeDirectorStageView(next.creativeIntake?.stage).canContinue;
        next.creativeIntake = normalizeCreativeIntake(action.item);
        if (
          !creativeDirectorStageView(next.creativeIntake.stage).canContinue
        ) {
          next.view = "home";
          if (hadDownstream) {
            clearCreativeDownstreamWorkspace(next);
          }
        }
        markProjectChanged(next);
        return next;
        }
      case "SET_GENERATION_PARAMETER": {
        const key = action.key;
        if (!(key in safeObject(next.generationParameters))) return next;
        let value = action.value;
        if (["steps", "cfg", "generationSeed", "denoiseStrength"].includes(key)) {
          value = value === null || value === "" ? null : Number(value);
          if (value !== null && !Number.isFinite(value)) return next;
          if (key === "steps") value = Math.max(1, Math.min(1000, Math.round(value)));
          if (key === "cfg") value = Math.max(0, Math.min(100, value));
          if (key === "generationSeed") {
            value = Math.max(0, Math.min(Number.MAX_SAFE_INTEGER, Math.round(value)));
          }
          if (key === "denoiseStrength" && value !== null) {
            value = Math.max(0, Math.min(1, value));
          }
        }
        if (key === "resolution") {
          if (!value || !Number.isInteger(value.width) || !Number.isInteger(value.height)) {
            return next;
          }
          value = {
            width: Math.max(64, Math.min(8192, value.width)),
            height: Math.max(64, Math.min(8192, value.height)),
          };
        }
        next.generationParameters[key] = clone(value);
        if (!next.manualParameterKeys.includes(key)) {
          next.manualParameterKeys.push(key);
        }
        markWorkspaceChanged(next);
        next.toast = `已手动覆盖参数：${key}`;
        return next;
      }
      case "MODEL_PROFILES_LOADING":
        next.modelProfilesStatus = "loading";
        return next;
      case "MODEL_PROFILES_LOADED": {
        next.modelProfiles = Array.isArray(action.items) ? clone(action.items) : [];
        next.modelProfilesStatus = "ready";
        if (
          creativeDirectorStageView(next.creativeIntake?.stage).canContinue &&
          applyCanonicalModelToWorkspace(next)
        ) {
          return next;
        }
        const selected =
          next.modelProfiles.find((item) => item.profileId === next.modelProfileId) ||
          next.modelProfiles[0];
        if (selected) {
          next.modelProfileId = selected.profileId;
          for (const [key, value] of Object.entries(
            safeObject(selected.defaultParameters)
          )) {
            if (
              key in next.generationParameters &&
              !next.manualParameterKeys.includes(key)
            ) {
              next.generationParameters[key] = clone(value);
            }
          }
        }
        return next;
      }
      case "MODEL_PROFILES_FAILED":
        next.modelProfilesStatus = "error";
        next.toast = action.error || "出图模型档案加载失败";
        return next;
      case "SELECT_MODEL_PROFILE":
        if (!next.modelProfiles.some((item) => item.profileId === action.profileId)) {
          return next;
        }
        next.modelProfileId = action.profileId;
        markWorkspaceChanged(next);
        return next;
      case "RESTORE_MODEL_DEFAULTS": {
        const selected = next.modelProfiles.find(
          (item) => item.profileId === next.modelProfileId
        );
        if (!selected) return next;
        const defaults = safeObject(selected.defaultParameters);
        for (const [key, value] of Object.entries(defaults)) {
          if (key in next.generationParameters) {
            next.generationParameters[key] = clone(value);
          }
        }
        next.manualParameterKeys = next.manualParameterKeys.filter(
          (key) => !Object.prototype.hasOwnProperty.call(defaults, key)
        );
        markWorkspaceChanged(next);
        next.toast = "已恢复当前模型推荐参数";
        return next;
      }
      case "RANDOM_CATALOG_LOADING":
        next.randomCatalogStatus = "loading";
        return next;
      case "RANDOM_CATALOG_LOADED":
        next.randomCatalog = clone(action.item || null);
        next.randomCatalogStatus = "ready";
        return next;
      case "RANDOM_CATALOG_FAILED":
        next.randomCatalogStatus = "error";
        next.toast = action.error || "词库目录加载失败";
        return next;
      case "APPLY_RANDOM_PLAN": {
        const plan = action.item;
        if (!plan || !Array.isArray(plan.items)) return next;
        const blocks = clone(
          next.appliedBlocks.length ? next.appliedBlocks : data.promptBlocks || []
        );
        const grouped = new Map();
        for (const item of plan.items) {
          let blockId = item?.binding?.blockId;
          if (item?.categoryId === "clothing_outfit") blockId = "outfit";
          if (!blocks.some((block) => block.id === blockId)) continue;
          if (!grouped.has(blockId)) grouped.set(blockId, []);
          grouped.get(blockId).push(item);
        }
        for (const [blockId, items] of grouped) {
          const block = blocks.find((entry) => entry.id === blockId);
          const english = items.map((item) => item.text).join(", ");
          block.en = english;
          block.zh = `待本地 LLM 翻译：${english}`;
          block.source = `确定性词库 ${plan.catalog?.version || ""}`.trim();
          block.confidence = 100;
          block.locked = items.some((item) => item.locked);
        }
        next.randomPlan = clone(plan);
        next.randomSeed = plan.librarySeed;
        initializeVersion(next, compileBlocks(blocks), blocks);
        next.toast = `已按词库 Seed 生成 ${plan.items.length} 项；中文块可用本地 LLM 翻译`;
        return next;
      }
      case "RANDOM_PLAN_FAILED":
        next.toast = action.error || "确定性随机计划生成失败";
        return next;
      case "EDIT_PREVIEW_STARTED":
        next.pendingEditPreview = { loading: true };
        return next;
      case "EDIT_PREVIEW_READY":
        next.pendingEditPreview = clone(action.item || null);
        next.toast = action.item?.ready
          ? `已预览 ${action.item.affectedIds?.length || 0} 个受影响块`
          : "指令仍有冲突或无法确定的内容，尚未修改";
        return next;
      case "EDIT_PREVIEW_FAILED":
        next.pendingEditPreview = null;
        next.toast = action.error || "修改预览失败";
        return next;
      case "DISMISS_EDIT_PREVIEW":
        next.pendingEditPreview = null;
        return next;
      case "APPLY_EDIT_RESULT": {
        if (!Array.isArray(action.item?.workbenchBlocks)) return next;
        initializeVersion(
          next,
          compileBlocks(
            action.item.workbenchBlocks,
            next.output.relationEn,
            next.output.relationZh
          ),
          action.item.workbenchBlocks
        );
        next.instructionHistory = clone(
          action.item.recipe?.instructionHistory || next.instructionHistory
        );
        next.recipeHash = action.item.recipeHash || "";
        next.pendingChange = clone(action.item.change || null);
        next.pendingEditPreview = null;
        next.toast = `中文指令已应用，将自动保存为 V${next.version + 1}`;
        return next;
      }
      case "IMPORT_REFERENCE": {
        const reference = next.references.find((item) => item.id === action.id);
        if (!reference) return next;
        next.view = "text";
        next.textMode = "expand";
        next.draftInput = reference.promptEn;
        initializeVersion(
          next,
          promptFromReference(reference),
          data.promptBlocks || []
        );
        next.toast = `已带入「${reference.title}」`;
        return next;
      }
      case "START_TEXT_EXPANSION":
        next.view = "text";
        next.textMode = "expand";
        next.textGenerating = true;
        next.generationProgress = {
          status: "running",
          task: "expand",
          title: "正在拓展并结构化",
          detail: "请求已发送，等待模型生成与服务端校验。",
          provider:
            next.settings.textProvider === "api" ? "外部 API" : "本地 LLM",
          startedAt: action.startedAt || Date.now(),
          elapsedSeconds: 0,
          finishedAt: 0,
        };
        next.toast = "正在调用文本模型";
        return next;
      case "APPLY_TEXT_EXPANSION": {
        const item = action.item || {};
        const output = {
          positiveEn: item.positiveEn || "",
          positiveZh: item.positiveZh || "",
          negativeEn: item.negativeEn || "",
          negativeZh: item.negativeZh || "",
          relationEn: item.relationEn || "",
          relationZh: item.relationZh || "",
        };
        initializeVersion(next, output, item.blocks || []);
        next.outputChecks = clone(
          item.checks || {
            preservedUserIntent: true,
            bilingualAligned: true,
            conflicts: [],
            assumptions: [],
          }
        );
        next.textGenerating = false;
        const expansionFinishedAt = action.finishedAt || Date.now();
        next.generationProgress = {
          ...next.generationProgress,
          status: "success",
          title: "拓展完成",
          detail: "结构块与双语提示词已更新。",
          finishedAt: expansionFinishedAt,
          elapsedSeconds:
            Math.round(
              Math.max(
                0,
                expansionFinishedAt - next.generationProgress.startedAt
              ) / 100
            ) / 10,
        };
        next.toast = "已生成中英文提示词";
        return next;
      }
      case "TEXT_EXPANSION_FAILED":
        next.textGenerating = false;
        next.toast = action.error || "提示词拓展失败";
        const expansionFailedAt = action.finishedAt || Date.now();
        next.generationProgress = {
          ...next.generationProgress,
          status: "error",
          title: "拓展失败",
          detail: next.toast,
          finishedAt: expansionFailedAt,
          elapsedSeconds:
            Math.round(
              Math.max(
                0,
                expansionFailedAt - next.generationProgress.startedAt
              ) / 100
            ) / 10,
        };
        return next;
      case "START_TEXT_DECOMPOSITION":
        next.view = "text";
        next.textMode = "expand";
        next.textDecomposing = true;
        next.generationProgress = {
          status: "running",
          task: "decompose",
          title: "正在保真拆解",
          detail: "请求已发送，等待模型生成与服务端校验。",
          provider:
            next.settings.textProvider === "api" ? "外部 API" : "本地 LLM",
          startedAt: action.startedAt || Date.now(),
          elapsedSeconds: 0,
          finishedAt: 0,
        };
        next.toast = "正在保真拆解完整提示词";
        return next;
      case "APPLY_TEXT_DECOMPOSITION": {
        const item = action.item || {};
        const output = {
          positiveEn: item.positiveEn || "",
          positiveZh: item.positiveZh || "",
          negativeEn: item.negativeEn || "",
          negativeZh: item.negativeZh || "",
          relationEn: item.relationEn || "",
          relationZh: item.relationZh || "",
        };
        initializeVersion(next, output, item.blocks || []);
        next.outputChecks = clone(
          item.checks || {
            preservedUserIntent: true,
            bilingualAligned: true,
            conflicts: [],
            assumptions: [],
            sourceCoverage: 100,
          }
        );
        next.textDecomposing = false;
        const decompositionFinishedAt = action.finishedAt || Date.now();
        next.generationProgress = {
          ...next.generationProgress,
          status: "success",
          title: "拆解完成",
          detail: "结构块与双语提示词已更新，原始内容未扩写。",
          finishedAt: decompositionFinishedAt,
          elapsedSeconds:
            Math.round(
              Math.max(
                0,
                decompositionFinishedAt - next.generationProgress.startedAt
              ) / 100
            ) / 10,
        };
        next.toast = "已仅拆解，原始内容未扩写";
        return next;
      }
      case "TEXT_DECOMPOSITION_FAILED":
        next.textDecomposing = false;
        next.toast = action.error || "提示词拆解失败";
        const decompositionFailedAt = action.finishedAt || Date.now();
        next.generationProgress = {
          ...next.generationProgress,
          status: "error",
          title: "拆解失败",
          detail: next.toast,
          finishedAt: decompositionFailedAt,
          elapsedSeconds:
            Math.round(
              Math.max(
                0,
                decompositionFailedAt - next.generationProgress.startedAt
              ) / 100
            ) / 10,
        };
        return next;
      case "START_PENDING_TRANSLATION":
        next.translatingPending = true;
        next.toast = "正在使用本地 LLM 翻译";
        return next;
      case "APPLY_PENDING_TRANSLATIONS": {
        const translations = new Map(
          (action.item?.translations || []).map((item) => [item.id, item.zh])
        );
        const applyTranslations = (blocks) => {
          for (const block of blocks) {
            if (
              block.zh?.startsWith("待本地 LLM 翻译：") &&
              translations.has(block.id)
            ) {
              block.zh = translations.get(block.id);
            }
          }
        };
        applyTranslations(next.blocks);
        applyTranslations(next.appliedBlocks);
        if (next.appliedBlocks.length) {
          next.output = compileBlocks(
            next.appliedBlocks,
            next.output.relationEn,
            next.output.relationZh
          );
        }
        markWorkspaceChanged(next);
        next.translatingPending = false;
        next.toast = `已翻译 ${translations.size} 个结构块`;
        return next;
      }
      case "PENDING_TRANSLATION_FAILED":
        next.translatingPending = false;
        next.toast = action.error || "本地 LLM 翻译失败";
        return next;
      case "START_TEXT_RANDOM":
        next.view = "text";
        next.textMode = "random";
        next.textGenerating = true;
        next.generationProgress = {
          status: "running",
          task: "random",
          title: "正在设计随机画面",
          detail: "模型先生成完整视觉蓝图，再编译为 Anima 双语结构化提示词。",
          provider:
            next.settings.textProvider === "api" ? "外部 API" : "本地 LLM",
          startedAt: action.startedAt || Date.now(),
          elapsedSeconds: 0,
          finishedAt: 0,
        };
        next.toast = "正在调用模型生成全随机提示词";
        return next;
      case "APPLY_TEXT_RANDOM": {
        const item = action.item || {};
        initializeVersion(
          next,
          {
            positiveEn: item.positiveEn || "",
            positiveZh: item.positiveZh || "",
            negativeEn: item.negativeEn || "",
            negativeZh: item.negativeZh || "",
            relationEn: item.relationEn || "",
            relationZh: item.relationZh || "",
          },
          item.blocks || []
        );
        next.outputChecks = clone(item.checks || {});
        next.textGenerating = false;
        const finishedAt = action.finishedAt || Date.now();
        next.generationProgress = {
          ...next.generationProgress,
          status: "success",
          title: "全随机生成完成",
          detail: "随机蓝图、十个结构块和中英文提示词已更新。",
          finishedAt,
          elapsedSeconds:
            Math.round(
              Math.max(
                0,
                finishedAt - next.generationProgress.startedAt
              ) / 100
            ) / 10,
        };
        next.toast = "已生成高密度全随机提示词";
        return next;
      }
      case "TEXT_RANDOM_FAILED": {
        next.textGenerating = false;
        next.toast = action.error || "全随机生成失败";
        const finishedAt = action.finishedAt || Date.now();
        next.generationProgress = {
          ...next.generationProgress,
          status: "error",
          title: "全随机生成失败",
          detail: next.toast,
          finishedAt,
          elapsedSeconds:
            Math.round(
              Math.max(
                0,
                finishedAt - next.generationProgress.startedAt
              ) / 100
            ) / 10,
        };
        return next;
      }
      case "SET_TEXT_PROVIDER":
        next.settings.textProvider = action.value;
        next.toast =
          action.value === "local"
            ? "本次任务使用本地 LLM"
            : "本次任务使用外部 API";
        return next;
      case "CREATE_RESOURCE": {
        const resource = normalizeResourceWeights(action.resource);
        const type = resource.type || "snippets";
        if (!next.resources[type]) next.resources[type] = [];
        next.resources[type].push(resource);
        next.resourceDialogOpen = false;
        next.toast = `已加入本次会话：${resource.name}`;
        return next;
      }
      case "ADD_RECIPE": {
        const recipe = action.recipe;
        if (!recipe || !Array.isArray(recipe.blocks) || !recipe.blocks.length) {
          return next;
        }
        next.resources.snippets.push(normalizeResourceWeights(recipe));
        next.toast = `配方「${recipe.name}」已加入会话，正在写入本机数据库`;
        return next;
      }
      case "SAVE_BLOCK_AS_RESOURCE": {
        const block = next.blocks.find((item) => item.id === action.id);
        if (!block) return next;
        next.resources.snippets.push({
          id: `saved-${block.id}-${next.resources.snippets.length + 1}`,
          type: "snippets",
          name: action.name || `${block.label} V${next.version || 1}`,
          meta: block.label,
          targetBlock: block.id,
          en: block.en,
          zh: block.zh,
        });
        next.toast = `已将「${block.label}」加入本次会话资源`;
        return next;
      }
      case "APPLY_RESOURCE": {
        const foundResource = findResource(next.resources, action.id);
        if (!foundResource) return next;
        const resource = normalizeResourceWeights(foundResource);
        if (!next.blocks.length) {
          next.blocks = normalizeBlocks(data.promptBlocks || []);
          next.appliedBlocks = clone(next.blocks);
        }
        const resourceBlocks = resource.blocks || [
          {
            id: resource.targetBlock,
            en: resource.en,
            zh: resource.zh,
            weight: resource.weight,
          },
        ];
        resourceBlocks.forEach((resourceBlock) => {
          const block = next.blocks.find((item) => item.id === resourceBlock.id);
          if (!block) return;
          block.en = resourceBlock.en;
          block.zh =
            resourceBlock.zh || `待本地 LLM 翻译：${resourceBlock.en}`;
          if (resourceBlock.weight !== undefined) {
            block.weight = normalizeBlockWeight(resourceBlock.weight, block.weight);
          }
          block.source = resource.source || block.source;
          markDirty(next, block.id);
        });
        refreshPreview(next);
        next.view = "text";
        next.toast = `已带入资源：${resource.name}，等待应用`;
        return next;
      }
      case "TOGGLE_ANALYZER":
        if (
          next.analyzers[action.id] &&
          next.analyzers[action.id].available !== false
        ) {
          next.analyzers[action.id].selected =
            !next.analyzers[action.id].selected;
        }
        return next;
      case "APPLY_VISION_STATUS": {
        for (const [id, status] of Object.entries(action.models || {})) {
          const analyzer = next.analyzers[id];
          if (!analyzer) continue;
          analyzer.available = Boolean(status.installed);
          analyzer.status = status.installed ? "idle" : "unavailable";
          analyzer.detail =
            status.label || (status.installed ? "已安装" : "未安装");
          analyzer.missing = clone(status.missing || []);
          analyzer.modelPath = status.modelPath || "";
          analyzer.error = "";
          if (!status.installed) analyzer.selected = false;
        }
        return next;
      }
      case "SET_IMAGE":
        next.imageName = action.name || "参考图片.png";
        next.imageMimeType = action.mimeType || "";
        next.imageSize = Number(action.size) || 0;
        next.imageLoaded = true;
        next.view = "image";
        markProjectChanged(next);
        next.analysisNotice = "图片已载入，可以开始分析";
        next.toast = `已载入图片：${next.imageName}`;
        return next;
      case "START_ANALYSIS": {
        const queue = selectedAnalyzerIds(next.analyzers);
        next.view = "image";
        next.analysisQueue = queue;
        next.analysisComplete = false;
        next.analyzers = resetAnalyzerStatuses(next.analyzers, queue);
        next.analysisNotice = queue.length
          ? `正在运行 ${next.analyzers[queue[0]].name}`
          : "请至少选择一个识别模型";
        return next;
      }
      case "APPLY_IMAGE_ANALYSIS": {
        const item = action.item || {};
        for (const analyzer of item.analyzers || []) {
          if (next.analyzers[analyzer.id]) {
            next.analyzers[analyzer.id].status = normalizeModelStatus(
              analyzer.status || "ready"
            );
            next.analyzers[analyzer.id].raw = analyzer.raw || "";
            next.analyzers[analyzer.id].error = analyzer.error || "";
          }
        }
        initializeVersion(
          next,
          {
            positiveEn: item.positiveEn || "",
            positiveZh: item.positiveZh || "",
            negativeEn: item.negativeEn || "",
            negativeZh: item.negativeZh || "",
            relationEn: item.relationEn || "",
            relationZh: item.relationZh || "",
          },
          item.blocks || []
        );
        next.outputChecks = clone(item.checks || {});
        next.rawMergedResult = item.positiveEn || "";
        next.analysisQueue = [];
        next.analysisComplete = true;
        next.activeRawResult = "merged";
        next.analysisNotice = "真实图片分析完成，已生成结构化提示词";
        next.toast = "已根据当前图片完成识别";
        return next;
      }
      case "IMAGE_ANALYSIS_FAILED":
        Object.values(next.analyzers).forEach((model) => {
          model.status = model.available === false ? "unavailable" : "idle";
        });
        next.analysisQueue = [];
        next.analysisComplete = false;
        next.analysisNotice = action.error || "图片分析失败";
        next.toast = next.analysisNotice;
        return next;
      case "RELEASE_MODELS":
        Object.values(next.analyzers).forEach((model) => {
          model.status = model.available === false ? "unavailable" : "idle";
        });
        next.analysisQueue = [];
        next.analysisNotice = "已释放本地识图模型";
        next.toast = "显存已释放";
        return next;
      case "SET_SETTING":
        next.settings[action.key] = action.value;
        return next;
      case "TOGGLE_DRAWER":
        next.drawerOpen =
          typeof action.open === "boolean" ? action.open : !next.drawerOpen;
        return next;
      case "TOGGLE_RESOURCE_DIALOG":
        next.resourceDialogOpen =
          typeof action.open === "boolean"
            ? action.open
            : !next.resourceDialogOpen;
        return next;
      case "SET_RAW_RESULT":
        next.activeRawResult = action.id;
        return next;
      case "TOGGLE_BLOCK_LOCK": {
        const block = next.blocks.find((item) => item.id === action.id);
        if (block) block.locked = !block.locked;
        if (block?.locked) {
          next.selectedVariantBlockIds =
            next.selectedVariantBlockIds.filter((id) => id !== block.id);
        }
        if (block) markDirty(next, block.id);
        return next;
      }
      case "TOGGLE_VARIANT_SELECTION": {
        const block = next.blocks.find((item) => item.id === action.id);
        if (
          !block ||
          block.locked ||
          !RANDOM_VARIANT_BLOCKS.has(block.id) ||
          next.batchRegenerating
        ) {
          return next;
        }
        next.selectedVariantBlockIds =
          next.selectedVariantBlockIds.includes(block.id)
            ? next.selectedVariantBlockIds.filter((id) => id !== block.id)
            : [...next.selectedVariantBlockIds, block.id];
        return next;
      }
      case "CLEAR_VARIANT_SELECTION":
        if (!next.batchRegenerating) next.selectedVariantBlockIds = [];
        return next;
      case "SET_BLOCK_WEIGHT": {
        const block = next.blocks.find((item) => item.id === action.id);
        if (block) {
          block.weight = normalizeBlockWeight(action.value, block.weight);
        }
        if (block) {
          markDirty(next, block.id);
          refreshPreview(next);
        }
        return next;
      }
      case "UPDATE_BLOCK": {
        const block = next.blocks.find((item) => item.id === action.id);
        if (block && !block.locked) {
          block[action.language] = action.value;
          markDirty(next, block.id);
          refreshPreview(next);
        }
        return next;
      }
      case "START_BLOCK_VARIANT":
        next.regeneratingBlockId = action.id;
        next.toast = `正在随机“${action.label || "当前结构块"}”`;
        return next;
      case "START_BLOCKS_VARIANT":
        if (next.selectedVariantBlockIds.length < 2) return next;
        next.batchRegenerating = true;
        next.toast = `正在联合随机 ${next.selectedVariantBlockIds.length} 个结构块`;
        return next;
      case "APPLY_BLOCKS_VARIANT": {
        const items = Array.isArray(action.item?.items)
          ? action.item.items
          : [];
        const selected = new Set(
          Array.isArray(action.blockIds) && action.blockIds.length
            ? action.blockIds
            : next.selectedVariantBlockIds
        );
        for (const item of items) {
          const block = next.blocks.find((entry) => entry.id === item.id);
          if (!block || block.locked || !selected.has(block.id)) continue;
          block.pendingVariant = {
            previousEn: block.en,
            previousZh: block.zh,
            previousSource: block.source,
            previousConfidence: block.confidence,
          };
          block.en = item.en || block.en;
          block.zh = item.zh || block.zh;
          block.source = item.source || "模型 · 联合随机";
          block.confidence = item.confidence || 88;
          markDirty(next, block.id);
        }
        refreshPreview(next);
        const changedCount = items.filter((item) => selected.has(item.id)).length;
        next.batchRegenerating = false;
        next.selectedVariantBlockIds = [];
        next.toast = `已联合随机 ${changedCount} 个结构块，等待应用`;
        return next;
      }
      case "BLOCKS_VARIANT_READY":
        next.batchRegenerating = false;
        next.selectedVariantBlockIds = [];
        next.toast = `已生成 ${action.count} 组候选变体，请对比挑选`;
        return next;
      case "BLOCKS_VARIANT_FAILED":
        next.batchRegenerating = false;
        next.toast = action.error || "联合随机失败";
        return next;
      case "APPLY_BLOCK_VARIANT": {
        const item = action.item || {};
        const block = next.blocks.find((entry) => entry.id === item.id);
        next.regeneratingBlockId = "";
        if (!block || block.locked) return next;
        block.pendingVariant = {
          previousEn: block.en,
          previousZh: block.zh,
          previousSource: block.source,
          previousConfidence: block.confidence,
        };
        block.en = item.en || block.en;
        block.zh = item.zh || block.zh;
        block.source = item.source || "模型 · 随机变体";
        block.confidence = item.confidence || 88;
        markDirty(next, block.id);
        refreshPreview(next);
        next.toast = `已生成“${block.label}”随机变体，可对比后保留或回退`;
        return next;
      }
      case "BLOCK_VARIANT_FAILED":
        next.regeneratingBlockId = "";
        next.toast = action.error || "随机变体生成失败";
        return next;
      case "DISCARD_CHANGES":
        next.blocks = clone(next.appliedBlocks);
        next.dirtyBlockIds = [];
        next.previewOutput = null;
        next.toast = "已撤销未应用修改";
        return next;
      case "APPLY_CHANGES": {
        if (!next.dirtyBlockIds.length) return next;
        next.blocks.forEach((block) => {
          delete block.pendingVariant;
        });
        // Applying manual block edits invalidates the model-authored relation
        // narrative. Persist a clean block-only prompt instead of silently
        // appending stale prose that may reintroduce removed entities or actions.
        const output = compileBlocks(next.blocks);
        next.output = output;
        next.appliedBlocks = clone(next.blocks);
        next.dirtyBlockIds = [];
        next.previewOutput = null;
        markWorkspaceChanged(next);
        next.toast = `修改已应用，保存后生成 V${next.version + 1}`;
        return next;
      }
      case "KEEP_BLOCK_VARIANT": {
        const block = next.blocks.find((item) => item.id === action.id);
        if (block && block.pendingVariant) {
          delete block.pendingVariant;
          next.toast = `已保留“${block.label}”的新变体`;
        }
        return next;
      }
      case "REVERT_BLOCK_VARIANT": {
        const block = next.blocks.find((item) => item.id === action.id);
        if (!block || !block.pendingVariant) return next;
        const previous = block.pendingVariant;
        block.en = previous.previousEn;
        block.zh = previous.previousZh;
        block.source = previous.previousSource;
        block.confidence = previous.previousConfidence;
        delete block.pendingVariant;
        const applied = next.appliedBlocks.find(
          (item) => item.id === block.id
        );
        if (
          applied &&
          applied.en === block.en &&
          applied.zh === block.zh &&
          Number(applied.weight ?? 100) === Number(block.weight ?? 100)
        ) {
          next.dirtyBlockIds = next.dirtyBlockIds.filter(
            (id) => id !== block.id
          );
        }
        refreshPreview(next);
        next.toast = `已回退“${block.label}”到变体前内容`;
        return next;
      }
      case "RESTORE_VERSION": {
        const snapshot = next.versionHistory.find(
          (entry) => entry.version === action.version
        );
        if (!snapshot) return next;
        if (
          (next.dirtyBlockIds.length ||
            next.hasUnsavedChanges ||
            next.hasUnsavedProjectChanges) &&
          action.confirmed !== true
        ) {
          next.toast = "当前有未保存修改，确认放弃后才能载入历史版本";
          return next;
        }
        if (
          snapshot.version === next.version &&
          !next.dirtyBlockIds.length &&
          !next.hasUnsavedChanges
        ) {
          return next;
        }
        const restoredBlocks = normalizeBlocks(snapshot.blocks);
        const restoredMetadata = safeObject(snapshot.metadata);
        next.blocks = clone(restoredBlocks);
        next.appliedBlocks = clone(restoredBlocks);
        next.output = clone(snapshot.output);
        next.view = snapshot.source === "image" ? "image" : "text";
        next.textMode =
          typeof restoredMetadata.textMode === "string"
            ? restoredMetadata.textMode
            : "expand";
        next.draftInput =
          typeof restoredMetadata.draftInput === "string"
            ? restoredMetadata.draftInput
            : "";
        next.randomSeed = restoredMetadata.randomSeed ?? null;
        next.outputChecks = clone(safeObject(restoredMetadata.outputChecks));
        next.imageName =
          typeof restoredMetadata.imageName === "string"
            ? restoredMetadata.imageName
            : "";
        next.imageMimeType = "";
        next.imageSize = 0;
        next.imageLoaded = false;
        next.rawMergedResult = snapshot.output.positiveEn || "";
        next.activeRawResult = "merged";
        next.analysisQueue = [];
        next.analysisComplete = snapshot.source === "image";
        next.analysisNotice =
          snapshot.source === "image"
            ? "已恢复图片解析结果；原始图片需重新载入"
            : "已恢复历史提示词版本";
        next.dirtyBlockIds = [];
        next.previewOutput = null;
        markWorkspaceChanged(next);
        next.restoreFromVersion = snapshot.version;
        next.toast = `已载入 V${snapshot.version}，保存后生成 V${next.version + 1}`;
        return next;
      }
      case "CLEAR_TOAST":
        next.toast = "";
        return next;
      default:
        return next;
    }
  }

  function currentOutput(state) {
    return state.previewOutput || state.output;
  }

  function getCombinedPrompt(state) {
    const output = currentOutput(state);
    return [
      output.positiveEn,
      output.positiveZh,
      output.negativeEn,
      output.negativeZh,
    ].join("\n");
  }

  function isWorkspaceRequestCurrent(request, sessionId, state) {
    return Boolean(
      request &&
        request.sessionId === sessionId &&
        request.workingRevision === state.workingRevision &&
        request.projectRevision === state.projectRevision
    );
  }

  function isCreativeIntakeResponseCurrent(request, sessionId, state) {
    return Boolean(
      request &&
        request.sessionId === sessionId &&
        request.projectRevision === state.projectRevision &&
        request.creativeIntakeRevision === state.creativeIntake?.revision
    );
  }

  const api = {
    createInitialState,
    createNewProjectState,
    hydrateProjectState,
    emptyCreativeIntake,
    normalizeCreativeIntake,
    reconstructDirectorMessages,
    buildCreativeBriefGroups,
    buildBriefRevisionMessage,
    buildCreativeDirectorRequest,
    buildDirectorMessageInputAction,
    buildCreativeIntakeTransitionRequest,
    acceptCreativeIntakeResponse,
    canConfirmCreativeBrief,
    creativeDirectorStageView,
    buildDirectorRenderModel,
    performCreativeIntakeRequest,
    persistCreativeIntakeRevision,
    rollbackCreativeIntakeAfterPersistenceFailure,
    persistedVersionSnapshot,
    buildProjectPayload,
    buildVersionPayload,
    buildWorkspaceCommitPayload,
    buildCreativeIntakeWorkspaceCommitPayload,
    createCreativeIntakeSaveJournal,
    validateCreativeIntakeMetadataCommitResult,
    createClientId,
    shouldConfirmWorkspaceDiscard,
    reduceState,
    getCombinedPrompt,
    isSupportedImageFile,
    DIRECTOR_IMAGE_REQUESTED_USES,
    validateDirectorImageFile,
    validateDirectorImageFileBatch,
    createDirectorImageReference,
    setDirectorImageRequestedUses,
    buildDirectorImageCards,
    buildDirectorImageUiState,
    buildDirectorImageReplaceAction,
    buildDirectorImagesReplaceAction,
    shouldBindDirectorImageFile,
    createDirectorImageCollectionController,
    createSingleImagePreviewController,
    buildDirectorImageEvidence,
    performDirectorImageAnalysis,
    shouldReuseDirectorImageEvidence,
    createDirectorImageFlowGuard,
    resolveDirectorImageEvidence,
    resolveDirectorImageEvidenceCollection,
    resolveCreativeBriefSourceLabel,
    compileBlocks,
    compileBlockFragment,
    composeArtistMix,
    splitPromptItems,
    suggestTags,
    normalizeBlockWeight,
    normalizeBlocks,
    normalizeResourceWeights,
    canonicalizePromptItem,
    normalizeModelStatus,
    statusLabel,
    projectSettingsMetadata,
    settingsWritePayload,
    normalizeCreativeDirectorSkillOverride,
    enqueueByKey,
    isWorkspaceRequestCurrent,
    isCreativeIntakeResponseCurrent,
    randomVariantBlockIds: Array.from(RANDOM_VARIANT_BLOCKS),
  };

  root.PromptStudioApp = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);

(function bootstrapBrowser(root) {
  if (typeof document === "undefined") return;

  const app = root.PromptStudioApp;
  const data = root.PROMPT_STUDIO_DATA;
  const RANDOM_VARIANT_BLOCKS = new Set(app.randomVariantBlockIds);
  let state = app.createInitialState();
  let quickTab = "characters";
  let resourceQuery = "";
  let resourcePage = 1;
  let resourcePages = 1;
  let resourceTotal = 0;
  let resourceSort = "count";
  let resourceSearchTimer = null;
  let resourceRequestId = 0;
  let previewResourceId = "";
  let favoriteResources = [];
  const favoriteMutationQueue = new Map();
  const artistMix = new Map();
  let resourceSource = {
    status: "checking",
    label: "正在检查 AnimaDex",
    counts: {},
  };
  let outputLanguage = "both";
  let collapsedBlocks = false;
  let generationProgressTimer = null;
  let activeTextAbort = null;
  let activeVisionAbort = null;
  let activeDirectorImageAbort = null;
  let activeDirectorAbort = null;
  let directorImageFileErrors = [];
  let directorAnalyzingImageIds = new Set();
  let lastCreativeIntakeTransitionOutcome = {
    accepted: false,
    persisted: false,
  };
  const workspaceRequestAborts = new Set();
  let uploadedImageFile = null;
  let imagePreviewUrl = "";
  const directorImageCollectionController =
    app.createDirectorImageCollectionController({
      createObjectURL: (file) => URL.createObjectURL(file),
      revokeObjectURL: (url) => URL.revokeObjectURL(url),
    });
  const directorImageFlowGuard = app.createDirectorImageFlowGuard();
  let projectListRequestId = 0;
  let projectOpenRequestId = 0;
  let workspaceSessionId = 0;
  let saveInFlight = null;
  let backupInFlight = false;
  let editPreviewRecipe = null;
  let editPreviewPayload = null;
  let editPreviewRevision = null;
  let promptTemplateDialog = {
    open: false,
    id: "",
    label: "",
    kind: "",
    filename: "",
    content: "",
    original: "",
  };

  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) =>
    Array.from(scope.querySelectorAll(selector));

  function escapeHtml(value) {
    return String(value ?? "").replace(
      /[&<>"']/g,
      (character) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[character]
    );
  }

  function readFavoriteResources() {
    try {
      const parsed = JSON.parse(
        localStorage.getItem("promptStudio.resourceFavorites.v1") || "[]"
      );
      return Array.isArray(parsed)
        ? parsed.map((item) => app.normalizeResourceWeights(item))
        : [];
    } catch {
      return [];
    }
  }

  async function legacyApiJson(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      throw new Error(
        (payload && payload.error) || `HTTP ${response.status}`
      );
    }
    if (payload === null) {
      throw new Error("服务返回了无法解析的响应");
    }
    return payload;
  }

  async function apiJson(path, options = {}) {
    let response;
    try {
      response = await fetch(path, {
        ...options,
        headers: {
          "Content-Type": "application/json",
          ...(options.headers || {}),
        },
      });
    } catch (cause) {
      const error = new Error(cause?.message || "网络请求失败，提交结果尚未确认");
      error.cause = cause;
      error.status = 0;
      error.code = "network_error";
      error.payload = null;
      error.responseReceived = false;
      throw error;
    }
    let payload = null;
    try {
      payload = await response.json();
    } catch (cause) {
      if (response.ok) {
        const error = new Error("服务返回了无法解析的响应，提交结果尚未确认");
        error.cause = cause;
        error.status = response.status;
        error.code = "invalid_json_response";
        error.payload = null;
        error.responseReceived = true;
        throw error;
      }
    }
    if (!response.ok) {
      const error = new Error(payload?.error || `HTTP ${response.status}`);
      error.status = response.status;
      error.code = payload?.code || "http_error";
      error.payload = payload;
      error.responseReceived = true;
      throw error;
    }
    if (payload === null) {
      const error = new Error("服务返回了空响应，提交结果尚未确认");
      error.status = response.status;
      error.code = "empty_response";
      error.payload = null;
      error.responseReceived = true;
      throw error;
    }
    return payload;
  }

  function setBackupStatus(message, status = "idle") {
    const element = $("#backupStatus");
    if (element) {
      element.textContent = message;
      element.dataset.status = status;
    }
    $$('[data-backup-action]').forEach((button) => {
      button.disabled = backupInFlight;
    });
  }

  function backupFilename(response, fallback) {
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="([A-Za-z0-9._-]+)"/);
    return match ? match[1] : fallback;
  }

  async function downloadBackup(scope) {
    if (backupInFlight) return;
    if (scope === "project" && !state.projectId) {
      state.toast = "请先保存作品，再导出当前作品备份";
      renderToast();
      return;
    }
    backupInFlight = true;
    setBackupStatus("正在生成经过校验的逻辑备份…", "working");
    try {
      const query = new URLSearchParams({ scope });
      if (scope === "project") query.set("projectId", state.projectId);
      const response = await fetch(`/api/backups/export?${query.toString()}`);
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.error || `HTTP ${response.status}`);
      }
      const blob = await response.blob();
      const fallback = `prompt-studio-${scope}-backup.zip`;
      const link = document.createElement("a");
      const objectUrl = URL.createObjectURL(blob);
      link.href = objectUrl;
      link.download = backupFilename(response, fallback);
      link.hidden = true;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
      const label = scope === "project" ? "当前作品" : "全库";
      setBackupStatus(`${label}备份已生成；密钥、模型与二进制资产未打包`, "success");
      state.toast = `${label}备份已生成`;
    } catch (error) {
      setBackupStatus(error.message || "备份生成失败", "error");
      state.toast = error.message || "备份生成失败";
    } finally {
      backupInFlight = false;
      setBackupStatus(
        $("#backupStatus")?.textContent || "备份操作结束",
        $("#backupStatus")?.dataset.status || "idle"
      );
      renderToast();
    }
  }

  async function inspectAndStageBackup(file) {
    if (!file || backupInFlight) return;
    backupInFlight = true;
    const contentType = file.name.toLowerCase().endsWith(".json")
      ? "application/json"
      : "application/zip";
    setBackupStatus("正在校验清单、哈希和逻辑数据…", "working");
    try {
      const inspected = await apiJson("/api/backups/inspect", {
        method: "POST",
        headers: { "Content-Type": contentType },
        body: file,
      });
      const counts = inspected.item?.counts || {};
      const scope = inspected.item?.manifest?.scope === "full" ? "全库" : "单作品";
      const summary = `${scope}备份：${counts.projects || 0} 个作品、${counts.versions || 0} 个版本`;
      setBackupStatus(`${summary}；校验通过，等待隔离恢复确认`, "success");
      if (
        !window.confirm(
          `${summary}。\n\n下一步只会恢复到隔离数据库，不会替换当前数据。是否继续？`
        )
      ) {
        setBackupStatus(`${summary}；已取消恢复，当前数据未变化`, "idle");
        return;
      }
      setBackupStatus("正在恢复到隔离数据库并执行完整性检查…", "working");
      const restored = await apiJson(
        "/api/backups/stage-restore?conflict=rename",
        {
          method: "POST",
          headers: { "Content-Type": contentType },
          body: file,
        }
      );
      if (restored.item?.activated !== false) {
        throw new Error("服务端未确认隔离恢复状态，已停止后续操作");
      }
      const target = restored.item?.stagingDatabase || "本机恢复目录";
      setBackupStatus(`隔离恢复完成（未激活）：${target}`, "success");
      state.toast = "备份已恢复到隔离库，当前项目库未被替换";
    } catch (error) {
      setBackupStatus(error.message || "备份校验或恢复失败", "error");
      state.toast = error.message || "备份校验或恢复失败";
    } finally {
      backupInFlight = false;
      setBackupStatus(
        $("#backupStatus")?.textContent || "备份操作结束",
        $("#backupStatus")?.dataset.status || "idle"
      );
      const input = $("#backupFileInput");
      if (input) input.value = "";
      renderToast();
    }
  }

  async function loadProjects({ silent = false } = {}) {
    const requestId = ++projectListRequestId;
    if (!silent) dispatch({ type: "PROJECTS_LOADING" });
    try {
      const result = await apiJson("/api/projects");
      if (requestId !== projectListRequestId) return;
      dispatch({
        type: "PROJECTS_LOADED",
        items: Array.isArray(result.items) ? result.items : [],
      });
    } catch (error) {
      if (requestId !== projectListRequestId) return;
      dispatch({
        type: "PROJECTS_FAILED",
        error: error.message || "项目列表加载失败",
      });
    }
  }

  function canReplaceWorkspace() {
    if (saveInFlight) {
      state.toast = "作品正在保存，请等待完成后再切换";
      renderToast();
      return false;
    }
    if (
      app.shouldConfirmWorkspaceDiscard(state) &&
      !window.confirm("当前作品有未保存内容，仍要放弃并切换吗？")
    ) {
      state.toast = "已保留当前未保存内容";
      renderToast();
      return false;
    }
    return true;
  }

  function beginWorkspaceRequest() {
    const request = {
      controller: new AbortController(),
      sessionId: workspaceSessionId,
      workingRevision: state.workingRevision,
      projectRevision: state.projectRevision,
    };
    workspaceRequestAborts.add(request.controller);
    return request;
  }

  function finishWorkspaceRequest(request) {
    workspaceRequestAborts.delete(request.controller);
  }

  function isCurrentWorkspaceRequest(request) {
    return app.isWorkspaceRequestCurrent(
      request,
      workspaceSessionId,
      state
    );
  }

  function discardChangedWorkspaceResult(request, failureType, message) {
    if (request.sessionId !== workspaceSessionId) return true;
    if (isCurrentWorkspaceRequest(request)) return false;
    dispatch({
      type: failureType,
      error: message,
      finishedAt: Date.now(),
    });
    return true;
  }

  function advanceWorkspaceSession() {
    workspaceSessionId += 1;
    for (const controller of workspaceRequestAborts) controller.abort();
    workspaceRequestAborts.clear();
    activeTextAbort = null;
    activeVisionAbort = null;
    activeDirectorImageAbort = null;
    activeDirectorAbort = null;
    editPreviewRecipe = null;
    editPreviewPayload = null;
    editPreviewRevision = null;
    variantMatrix = null;
    hideTagSuggest();
    dispatch({ type: "WORKSPACE_REQUESTS_CANCELLED" });
    return workspaceSessionId;
  }

  function releaseImagePreview() {
    if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    imagePreviewUrl = "";
  }

  async function openProject(projectId) {
    if (!projectId) return;
    if (!canReplaceWorkspace()) return;
    const openingSessionId = advanceWorkspaceSession();
    const requestId = ++projectOpenRequestId;
    const openingWorkingRevision = state.workingRevision;
    const openingProjectRevision = state.projectRevision;
    dispatch({ type: "PROJECT_OPENING", id: projectId });
    try {
      const result = await apiJson(
        `/api/projects/${encodeURIComponent(projectId)}`
      );
      if (requestId !== projectOpenRequestId) return;
      if (openingSessionId !== workspaceSessionId) return;
      if (
        state.workingRevision !== openingWorkingRevision ||
        state.projectRevision !== openingProjectRevision
      ) {
        dispatch({
          type: "PROJECT_OPEN_FAILED",
          error: "打开已取消，已保留加载期间产生的当前修改",
        });
        return;
      }
      // Requests started from the old workspace while this project was loading
      // must not be allowed to write into the newly opened workspace.
      advanceWorkspaceSession();
      uploadedImageFile = null;
      releaseImagePreview();
      directorImageCollectionController.clear();
      dispatch({ type: "PROJECT_OPENED", item: result.item || {} });
    } catch (error) {
      if (requestId !== projectOpenRequestId) return;
      dispatch({
        type: "PROJECT_OPEN_FAILED",
        error: error.message || "项目打开失败",
      });
    }
  }

  function startNewProject() {
    if (!canReplaceWorkspace()) return;
    advanceWorkspaceSession();
    projectOpenRequestId += 1;
    uploadedImageFile = null;
    releaseImagePreview();
    directorImageCollectionController.clear();
    dispatch({ type: "NEW_PROJECT" });
  }

  function isAbortError(error) {
    return Boolean(error) && error.name === "AbortError";
  }

  async function openPromptTemplate(templateId) {
    promptTemplateDialog = {
      open: true,
      id: templateId,
      label: "正在载入模板",
      kind: "",
      filename: "",
      content: "",
      original: "",
    };
    renderPromptTemplateDialog();
    try {
      const result = await apiJson(
        `/api/prompt-templates/${encodeURIComponent(templateId)}`
      );
      const item = result.item;
      promptTemplateDialog = {
        open: true,
        id: item.id,
        label: item.label,
        kind: item.kind,
        filename: item.filename,
        content: item.content || "",
        original: item.content || "",
      };
    } catch {
      promptTemplateDialog.open = false;
      state.toast = "提示词模板暂时无法读取，请确认本地后端已启动";
      renderToast();
    }
    renderPromptTemplateDialog();
  }

  async function savePromptTemplate() {
    if (!promptTemplateDialog.id) return;
    const editor = $("#promptTemplateContent");
    const content = editor ? editor.value : promptTemplateDialog.content;
    try {
      const result = await apiJson(
        `/api/prompt-templates/${encodeURIComponent(promptTemplateDialog.id)}`,
        {
          method: "PUT",
          body: JSON.stringify({ content }),
        }
      );
      const item = result.item;
      promptTemplateDialog = {
        open: true,
        id: item.id,
        label: item.label,
        kind: item.kind,
        filename: item.filename,
        content: item.content || "",
        original: item.content || "",
      };
      state.toast = "提示词模板已保存";
    } catch {
      state.toast = "提示词模板保存失败";
    }
    renderPromptTemplateDialog();
    renderToast();
  }

  function writeFavoriteResources() {
    try {
      localStorage.setItem(
        "promptStudio.resourceFavorites.v1",
        JSON.stringify(favoriteResources)
      );
    } catch {
      state.toast = "浏览器未允许保存收藏";
      renderToast();
    }
    syncFavoriteResourceGroups();
  }

  async function persistFavoriteResource(resource) {
    return apiJson("/api/favorites", {
      method: "POST",
      body: JSON.stringify(app.normalizeResourceWeights(resource)),
    });
  }

  async function removePersistedFavoriteResource(id) {
    return apiJson(`/api/favorites/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  }

  async function loadFavoriteResources(options = {}) {
    const localFavorites = readFavoriteResources();
    try {
      let result = await apiJson("/api/favorites");
      let remoteFavorites = result.items || [];
      if (options.migrateLocal !== false && localFavorites.length) {
        const remoteIds = new Set(remoteFavorites.map((item) => item.id));
        const missingLocal = localFavorites.filter((item) => !remoteIds.has(item.id));
        if (missingLocal.length) {
          await Promise.all(missingLocal.map((item) => persistFavoriteResource(item)));
          result = await apiJson("/api/favorites");
          remoteFavorites = result.items || [];
        }
      }
      favoriteResources = remoteFavorites.map((item) =>
        app.normalizeResourceWeights(item)
      );
      writeFavoriteResources();
      render();
    } catch {
      favoriteResources = localFavorites;
      syncFavoriteResourceGroups();
      renderQuickPicks();
    }
  }

  function isFavoriteTab(tab = quickTab) {
    return tab === "favorite-characters" || tab === "favorite-artists";
  }

  function favoriteTypeForTab(tab = quickTab) {
    if (tab === "favorite-characters") return "characters";
    if (tab === "favorite-artists") return "artists";
    return "";
  }

  function favoriteResourcesForTab(tab = quickTab) {
    const type = favoriteTypeForTab(tab);
    return type
      ? favoriteResources.filter((item) => item.type === type)
      : [...favoriteResources];
  }

  function syncFavoriteResourceGroups() {
    favoriteResources = favoriteResources.map((item) =>
      app.normalizeResourceWeights(item)
    );
    state.resources["favorite-characters"] = favoriteResourcesForTab(
      "favorite-characters"
    );
    state.resources["favorite-artists"] = favoriteResourcesForTab(
      "favorite-artists"
    );
    const persistedSnippets = favoriteResources.filter(
      (item) => item.type === "snippets"
    );
    if (persistedSnippets.length) {
      const known = new Set(state.resources.snippets.map((item) => item.id));
      for (const item of persistedSnippets) {
        if (!known.has(item.id)) state.resources.snippets.push(item);
      }
    }
  }

  function isFavoriteResource(id) {
    return favoriteResources.some((item) => item.id === id);
  }

  async function applyFavoriteResourceToggle(id) {
    const existing = favoriteResources.findIndex((item) => item.id === id);
    let removed = null;
    let added = null;
    if (existing >= 0) {
      [removed] = favoriteResources.splice(existing, 1);
      state.toast = `已取消收藏：${removed.name}`;
    } else {
      const resource = findQuickResource(id);
      if (!resource) return;
      added = {
        ...resource,
        favoritedAt: new Date().toISOString(),
      };
      favoriteResources.unshift(added);
      state.toast = `已收藏：${resource.name}`;
    }
    writeFavoriteResources();
    if (isFavoriteTab()) previewResourceId = "";
    render();
    try {
      if (added) {
        const saved = await persistFavoriteResource(added);
        if (saved?.item?.favoriteId) {
          added.favoriteId = saved.item.favoriteId;
          writeFavoriteResources();
        }
      }
      if (removed) await removePersistedFavoriteResource(removed.favoriteId || removed.id);
    } catch {
      state.toast = "本次收藏已暂存到浏览器，后端恢复后会再迁移";
      renderToast();
    }
  }

  function toggleFavoriteResource(id) {
    return app.enqueueByKey(favoriteMutationQueue, id, () =>
      applyFavoriteResourceToggle(id)
    );
  }

  function findQuickResource(id) {
    for (const items of Object.values(state.resources)) {
      const resource = items.find((item) => item.id === id);
      if (resource) return resource;
    }
    const favorite = favoriteResources.find((item) => item.id === id);
    if (favorite) return favorite;
    return null;
  }

  function applyQuickResource(id) {
    syncFavoriteResourceGroups();
    dispatch({ type: "APPLY_RESOURCE", id });
  }

  async function loadSettings() {
    try {
      const result = await apiJson("/api/settings");
      state.settings = {
        ...state.settings,
        ...(result.settings || {}),
      };
      renderTextProvider();
      renderDrawer();
    } catch {
      // Settings are optional during direct-file prototype use.
    }
  }

  async function loadModelProfiles() {
    dispatch({ type: "MODEL_PROFILES_LOADING" });
    try {
      const result = await apiJson("/api/model-profiles");
      dispatch({ type: "MODEL_PROFILES_LOADED", items: result.items || [] });
    } catch (error) {
      dispatch({
        type: "MODEL_PROFILES_FAILED",
        error: error.message || "出图模型档案加载失败",
      });
    }
  }

  function beginCreativeIntakeRequest(request) {
    const controller = new AbortController();
    activeDirectorAbort = controller;
    workspaceRequestAborts.add(controller);
    return { ...request, controller };
  }

  function finishCreativeIntakeRequest(request) {
    workspaceRequestAborts.delete(request.controller);
    if (activeDirectorAbort === request.controller) {
      activeDirectorAbort = null;
    }
  }

  async function persistAcceptedCreativeIntake(
    targetProjectRevision
  ) {
    const frozenSave = saveInFlight;
    let waitedForFrozenSave = Boolean(frozenSave);
    if (!waitedForFrozenSave) {
      try {
        waitedForFrozenSave = Boolean(readPendingSaveJournal());
      } catch {
        // saveCurrentProject reports corrupt or unavailable recovery state.
      }
    }
    return app.persistCreativeIntakeRevision({
      getState: () => state,
      targetProjectRevision,
      waitedForFrozenSave,
      waitForFrozenSave: () =>
        frozenSave || saveCurrentProject(),
      saveMetadata: () => saveCreativeIntakeMetadata(),
    });
  }

  function selectedLocalDirectorAnalyzers() {
    return Object.values(state.analyzers || {})
      .filter(
        (model) =>
          model.id !== "external" &&
          model.selected &&
          model.available !== false
      )
      .map((model) => model.id)
      .slice(0, 16);
  }

  function clearDirectorImageEvidence(status = "idle") {
    state = app.reduceState(state, {
      type: "DIRECTOR_IMAGE_EVIDENCE_SET",
      item: null,
      failures: [],
    });
    state = app.reduceState(state, {
      type: "DIRECTOR_IMAGE_ANALYSIS_STATUS_CHANGED",
      status,
    });
  }

  function syncDirectorImageAttachmentWithCanonical() {
    const previousSize = directorImageCollectionController.size();
    directorImageCollectionController.reconcile(
      state.creativeIntake.inputs.images
    );
    if (directorImageCollectionController.size() !== previousSize) {
      activeDirectorImageAbort?.abort();
      clearDirectorImageEvidence("idle");
    }
  }

  async function ensureDirectorImageEvidence({
    force = false,
    retryImageIds = [],
  } = {}) {
    const references = state.creativeIntake.inputs.images;
    if (!references.length) {
      clearDirectorImageEvidence("idle");
      return true;
    }
    const analyzerIds = selectedLocalDirectorAnalyzers();
    if (!analyzerIds.length) {
      state = app.reduceState(state, {
        type: "DIRECTOR_IMAGE_EVIDENCE_SET",
        item: null,
        failures: [
          {
            id: "analysis",
            error: "没有可用的本地识图模型，请先在设置中启用一个模型。",
          },
        ],
      });
      state = app.reduceState(state, {
        type: "DIRECTOR_IMAGE_ANALYSIS_STATUS_CHANGED",
        status: "error",
      });
      render();
      return false;
    }

    activeDirectorImageAbort?.abort();
    const controller = new AbortController();
    activeDirectorImageAbort = controller;
    workspaceRequestAborts.add(controller);
    const targetedRetryIds =
      force && retryImageIds.length
        ? retryImageIds.filter((imageId) =>
            references.some((reference) => reference.id === imageId)
          )
        : [];
    directorAnalyzingImageIds = new Set(
      targetedRetryIds.length
        ? targetedRetryIds
        : references.map((reference) => reference.id)
    );
    state = app.reduceState(state, {
      type: "DIRECTOR_IMAGE_ANALYSIS_STATUS_CHANGED",
      status: "analyzing",
    });
    render();
    try {
      const resolved = await app.resolveDirectorImageEvidenceCollection({
        references,
        getEntry: (imageId) =>
          directorImageCollectionController.get(imageId),
        analyzerIds,
        sessionId: workspaceSessionId,
        workspaceRevision: state.workspaceRevision,
        projectRevision: state.projectRevision,
        getCurrentContext: () => ({
          references: state.creativeIntake.inputs.images,
          analyzerIds: selectedLocalDirectorAnalyzers(),
          sessionId: workspaceSessionId,
          workspaceRevision: state.workspaceRevision,
          projectRevision: state.projectRevision,
        }),
        retryImageIds: force
          ? retryImageIds.length
            ? retryImageIds
            : references.map((reference) => reference.id)
          : retryImageIds,
        analyze: (capturedReference, attachment) =>
          app.performDirectorImageAnalysis({
            file: attachment.file,
            imageReference: capturedReference,
            analyzerIds,
            readBase64: fileToBase64,
            apiCall: apiJson,
            signal: controller.signal,
          }),
      });
      if (controller.signal.aborted || resolved.stale) {
        return false;
      }
      const evidenceById = new Map(
        resolved.items.map((item) => [item.imageId, item])
      );
      const failuresById = new Map(
        resolved.failures.map((item) => [item.imageId, item.failures])
      );
      for (const reference of references) {
        const evidence = evidenceById.get(reference.id) || null;
        const failures = failuresById.get(reference.id) || [];
        const attachment = directorImageCollectionController.get(
          reference.id
        );
        directorImageCollectionController.setAnalysis(reference.id, {
          evidence,
          failures,
          status: evidence
            ? failures.length
              ? "partial"
              : "ready"
            : "error",
          evidenceContext: evidence
            ? {
                file: attachment?.file,
                analyzerIds: analyzerIds.slice(),
                sessionId: workspaceSessionId,
                workspaceRevision: state.workspaceRevision,
                projectRevision: state.projectRevision,
              }
            : null,
        });
      }
      const flatFailures = resolved.failures.flatMap((item) =>
        item.failures.map((failure) => ({
          ...failure,
          imageId: item.imageId,
        }))
      );
      state = app.reduceState(state, {
        type: "DIRECTOR_IMAGE_EVIDENCE_SET",
        item: resolved.items,
        failures: flatFailures,
      });
      state = app.reduceState(state, {
        type: "DIRECTOR_IMAGE_ANALYSIS_STATUS_CHANGED",
        status: !resolved.items.length
          ? "error"
          : resolved.failures.length
            ? "partial"
            : "ready",
      });
      render();
      return resolved.items.length > 0;
    } finally {
      workspaceRequestAborts.delete(controller);
      if (activeDirectorImageAbort === controller) {
        activeDirectorImageAbort = null;
        directorAnalyzingImageIds = new Set();
        render();
      }
    }
  }

  async function attachDirectorImageFiles(files, { replaceId = "" } = {}) {
    if (state.directorBusy || activeDirectorImageAbort) return false;
    const currentReferences = state.creativeIntake.inputs.images;
    const replacement = replaceId
      ? currentReferences.find((item) => item.id === replaceId)
      : null;
    const batch = app.validateDirectorImageFileBatch(
      files,
      replacement
        ? currentReferences.filter((item) => item.id !== replaceId)
        : currentReferences,
      replacement ? { maxAccepted: 1 } : {}
    );
    directorImageFileErrors = batch.errors;
    if (!batch.accepted.length) {
      render();
      return false;
    }
    const acceptedFiles = replacement
      ? batch.accepted.slice(0, 1)
      : batch.accepted;
    const created = acceptedFiles.map((file, index) => ({
      file,
      reference: app.createDirectorImageReference(
        file,
        () =>
          replacement && index === 0
            ? replacement.id
            : app.createClientId("image")
      ),
    }));
    let references = currentReferences.slice();
    if (replacement) {
      references = references.map((item) =>
        item.id === replacement.id ? created[0].reference : item
      );
    } else {
      references.push(...created.map((item) => item.reference));
    }
    directorImageFlowGuard.beginMutation(() => {
      activeDirectorImageAbort?.abort();
    });
    clearDirectorImageEvidence("idle");
    await transitionCreativeIntake(
      app.buildDirectorImagesReplaceAction(state.creativeIntake, references)
    );
    for (const { file, reference } of created) {
      if (
        !app.shouldBindDirectorImageFile({
          transitionOutcome: lastCreativeIntakeTransitionOutcome,
          reference,
          current: state.creativeIntake,
        })
      ) {
        continue;
      }
      if (directorImageCollectionController.get(reference.id)) {
        directorImageCollectionController.replace(reference.id, file);
      } else {
        directorImageCollectionController.bind(reference.id, file);
      }
    }
    clearDirectorImageEvidence("idle");
    render();
    return lastCreativeIntakeTransitionOutcome.accepted;
  }

  async function removeDirectorImage(imageId) {
    const reference = state.creativeIntake.inputs.images.find(
      (item) => item.id === imageId
    );
    if (!reference || state.directorBusy) return false;
    directorImageFlowGuard.beginMutation(() => {
      activeDirectorImageAbort?.abort();
    });
    clearDirectorImageEvidence("idle");
    const references = state.creativeIntake.inputs.images.filter(
      (item) => item.id !== imageId
    );
    await transitionCreativeIntake(
      app.buildDirectorImagesReplaceAction(state.creativeIntake, references)
    );
    if (!lastCreativeIntakeTransitionOutcome.accepted) return false;
    activeDirectorImageAbort?.abort();
    directorImageCollectionController.remove(reference.id);
    clearDirectorImageEvidence("idle");
    render();
    return true;
  }

  async function setDirectorImageUses(imageId, requestedUses) {
    const reference = state.creativeIntake.inputs.images.find(
      (item) => item.id === imageId
    );
    if (!reference || state.directorBusy) return false;
    const updated = app.setDirectorImageRequestedUses(
      reference,
      requestedUses
    );
    if (
      JSON.stringify(updated.requestedUses) ===
      JSON.stringify(reference.requestedUses)
    ) {
      return true;
    }
    directorImageFlowGuard.beginMutation(() => {
      activeDirectorImageAbort?.abort();
    });
    clearDirectorImageEvidence("idle");
    const references = state.creativeIntake.inputs.images.map((item) =>
      item.id === imageId ? updated : item
    );
    await transitionCreativeIntake(
      app.buildDirectorImagesReplaceAction(state.creativeIntake, references)
    );
    if (!lastCreativeIntakeTransitionOutcome.accepted) return false;
    clearDirectorImageEvidence("idle");
    render();
    return true;
  }

  async function sendCreativeDirectorMessage(messageOverride = "") {
    if (state.directorBusy) return;
    const input = $("#directorMessageInput");
    const usesOverride = Boolean(String(messageOverride || "").trim());
    const message =
      String(messageOverride || "").trim() ||
      input?.value?.trim() ||
      "";
    if (!message) {
      state = app.reduceState(state, {
        type: "DIRECTOR_REQUEST_FAILED",
        error: "请先描述你想创作的画面。",
      });
      render();
      return;
    }
    try {
      const inputAction = app.buildDirectorMessageInputAction(
        state.creativeIntake,
        message
      );
      if (
        inputAction &&
        !(await transitionCreativeIntake(inputAction))
      ) {
        return;
      }
    } catch (error) {
      state = app.reduceState(state, {
        type: "DIRECTOR_REQUEST_FAILED",
        error: error.message || "创作输入无法保存",
      });
      render();
      return;
    }
    if (state.creativeIntake.inputs.images.length) {
      if (!(await ensureDirectorImageEvidence())) {
        return;
      }
    }

    let request;
    try {
      request = beginCreativeIntakeRequest(
        app.buildCreativeDirectorRequest(
          state,
          workspaceSessionId,
          message
        )
      );
    } catch (error) {
      state = app.reduceState(state, {
        type: "DIRECTOR_REQUEST_FAILED",
        error: error.message || "创意导演请求无效",
      });
      render();
      return;
    }

    const durableState = state;
    state = app.reduceState(state, { type: "DIRECTOR_REQUEST_STARTED" });
    render();
    try {
      const result = await app.performCreativeIntakeRequest({
        getState: () => state,
        sessionId: workspaceSessionId,
        request,
        userMessage: message,
        apiCall: apiJson,
        signal: request.controller.signal,
      });
      if (!result.accepted) {
        if (request.guard.sessionId === workspaceSessionId) {
          state = app.reduceState(state, {
            type: "DIRECTOR_REQUEST_SUCCEEDED",
          });
          render();
        }
        return;
      }
      state = result.state;
      syncDirectorImageAttachmentWithCanonical();
      const acceptedProjectRevision = state.projectRevision;
      if (input && !usesOverride) input.value = "";
      render();
      const persisted = await persistAcceptedCreativeIntake(
        acceptedProjectRevision
      );
      if (request.guard.sessionId !== workspaceSessionId) return false;
      if (!persisted) {
        state = app.rollbackCreativeIntakeAfterPersistenceFailure(
          durableState,
          state,
          "创作阶段保存失败，已恢复到上次保存状态，请重试"
        );
        render();
        return false;
      }
      state = app.reduceState(state, {
        type: "DIRECTOR_REQUEST_SUCCEEDED",
      });
      render();
    } catch (error) {
      if (request.guard.sessionId !== workspaceSessionId) return;
      state = app.reduceState(state, {
        type: "DIRECTOR_REQUEST_FAILED",
        error: isAbortError(error)
          ? "已停止本次创意导演请求；输入内容仍保留。"
          : error.message || "创意导演请求失败，请稍后重试",
      });
      render();
    } finally {
      finishCreativeIntakeRequest(request);
    }
  }

  function creativeIntakeTransitionMessage(action, item) {
    if (action.type === "select_direction") {
      const direction = item.directions?.find(
        (candidate) => candidate.id === item.selectedDirectionId
      );
      return direction ? `已选择方向：${direction.label}` : "";
    }
    if (action.type === "confirm_brief") {
      return "创作简报已确认并锁定。";
    }
    if (action.type === "reopen_brief") {
      return "已返回创作简报；旧的模型选择和下游内容已失效。";
    }
    if (action.type === "select_model") {
      const profile = state.modelProfiles.find(
        (candidate) =>
          candidate.profileId === item.selectedModelProfileId
      );
      return `已选择目标模型：${
        profile?.displayName || item.selectedModelProfileId
      }`;
    }
    return "";
  }

  async function transitionCreativeIntake(action) {
    if (state.directorBusy) return false;
    lastCreativeIntakeTransitionOutcome = {
      accepted: false,
      persisted: false,
    };
    const durableState = state;
    let request;
    try {
      request = beginCreativeIntakeRequest(
        app.buildCreativeIntakeTransitionRequest(
          state,
          workspaceSessionId,
          action
        )
      );
    } catch (error) {
      state = app.reduceState(state, {
        type: "DIRECTOR_REQUEST_FAILED",
        error: error.message || "当前操作不可用",
      });
      render();
      return false;
    }

    state = app.reduceState(state, { type: "DIRECTOR_REQUEST_STARTED" });
    render();
    try {
      const result = await app.performCreativeIntakeRequest({
        getState: () => state,
        sessionId: workspaceSessionId,
        request,
        apiCall: apiJson,
        signal: request.controller.signal,
      });
      if (!result.accepted) {
        if (request.guard.sessionId === workspaceSessionId) {
          state = app.reduceState(state, {
            type: "DIRECTOR_REQUEST_SUCCEEDED",
          });
          render();
        }
        return false;
      }
      state = result.state;
      lastCreativeIntakeTransitionOutcome.accepted = true;
      syncDirectorImageAttachmentWithCanonical();
      const acceptedProjectRevision = state.projectRevision;
      const message = creativeIntakeTransitionMessage(
        action,
        state.creativeIntake
      );
      if (message) {
        state = app.reduceState(state, {
          type: "DIRECTOR_MESSAGE_ADDED",
          message: { role: "system", text: message },
        });
      }
      render();
      const persisted = await persistAcceptedCreativeIntake(
        acceptedProjectRevision
      );
      if (request.guard.sessionId !== workspaceSessionId) return false;
      if (!persisted) {
        const imagesChanged =
          action?.type === "replace_inputs" &&
          JSON.stringify(action.images || []) !==
            JSON.stringify(durableState.creativeIntake.inputs.images);
        state = imagesChanged
          ? app.reduceState(state, {
              type: "DIRECTOR_REQUEST_FAILED",
              error: "图片变更已在本机保留，但保存失败，请重试",
            })
          : app.rollbackCreativeIntakeAfterPersistenceFailure(
              durableState,
              state,
              "创作阶段保存失败，已恢复到上次保存状态，请重试"
            );
        render();
        return false;
      }
      lastCreativeIntakeTransitionOutcome.persisted = true;
      state = app.reduceState(state, {
        type: "DIRECTOR_REQUEST_SUCCEEDED",
      });
      render();
      return true;
    } catch (error) {
      if (request.guard.sessionId !== workspaceSessionId) return false;
      state = app.reduceState(state, {
        type: "DIRECTOR_REQUEST_FAILED",
        error: isAbortError(error)
          ? "已停止当前阶段操作。"
          : error.message || "创作阶段操作失败，请稍后重试",
      });
      render();
      return false;
    } finally {
      finishCreativeIntakeRequest(request);
    }
  }

  async function saveSettings() {
    try {
      await apiJson("/api/settings", {
        method: "PUT",
        body: JSON.stringify(app.settingsWritePayload(state.settings)),
      });
    } catch {
      state.toast = "设置已在本次会话中生效，暂时未写入数据库";
      renderToast();
    }
  }

  function setProviderStatus(elementId, message, status = "") {
    const element = $(`#${elementId}`);
    if (!element) return;
    element.textContent = message;
    element.classList.toggle("connected", status === "connected");
    element.classList.toggle("error", status === "error");
  }

  async function testTextProvider(provider) {
    const statusId = provider === "local" ? "localLlmStatus" : "externalApiStatus";
    setProviderStatus(statusId, "正在测试...");
    await saveSettings();
    try {
      const result = await apiJson("/api/text/provider-test", {
        method: "POST",
        body: JSON.stringify({ provider }),
      });
      const item = result.item || {};
      setProviderStatus(
        statusId,
        `已连接 · ${item.model || "模型"} · ${item.latencyMs || 0} ms`,
        "connected"
      );
    } catch (error) {
      setProviderStatus(statusId, error.message || "连接失败", "error");
    }
  }

  async function controlLocalLlm(action) {
    setProviderStatus(
      "localLlmStatus",
      action === "start" ? "正在启动模型..." : "正在停止模型..."
    );
    try {
      const result = await apiJson(`/api/local-llm/${action}`, {
        method: "POST",
        body: "{}",
      });
      const item = result.item || {};
      setProviderStatus(
        "localLlmStatus",
        item.message || (item.running ? "本地模型运行中" : "本地模型已停止"),
        item.running ? "connected" : ""
      );
    } catch (error) {
      setProviderStatus(
        "localLlmStatus",
        error.message || "本地模型操作失败",
        "error"
      );
    }
  }

  async function loadLocalLlmStatus() {
    try {
      const result = await apiJson("/api/local-llm/status");
      const item = result.item || {};
      setProviderStatus(
        "localLlmStatus",
        item.message || "尚未检测",
        item.running ? "connected" : ""
      );
    } catch (error) {
      setProviderStatus(
        "localLlmStatus",
        error.message || "状态检测失败",
        "error"
      );
    }
  }

  async function loadVisionStatus() {
    try {
      const result = await apiJson("/api/vision/status");
      dispatch({
        type: "APPLY_VISION_STATUS",
        models: result.item || {},
      });
    } catch (error) {
      state.analysisNotice =
        error.message || "无法读取本地识图模型状态";
      renderAnalyzers();
    }
  }

  async function expandTextPrompt() {
    const input = state.draftInput.trim();
    if (!input) {
      state.toast = "请输入需要拓展的提示词";
      renderToast();
      return;
    }
    if (state.textGenerating || state.textDecomposing) return;

    dispatch({ type: "START_TEXT_EXPANSION", startedAt: Date.now() });
    const request = beginWorkspaceRequest();
    activeTextAbort = request.controller;
    try {
      const result = await apiJson("/api/text/expand", {
        method: "POST",
        signal: request.controller.signal,
        body: JSON.stringify({
          input,
          provider: state.settings.textProvider,
          targetModel: "anima",
          expansionLevel: state.settings.expansionLevel || "balanced",
          context: {
            lockedBlocks: state.blocks
              .filter((block) => block.locked)
              .map((block) => ({
                id: block.id,
                en: block.en,
                zh: block.zh,
              })),
          },
        }),
      });
      if (
        discardChangedWorkspaceResult(
          request,
          "TEXT_EXPANSION_FAILED",
          "请求期间内容已修改，已丢弃旧拓展结果"
        )
      ) return;
      dispatch({
        type: "APPLY_TEXT_EXPANSION",
        item: result.item,
        finishedAt: Date.now(),
      });
    } catch (error) {
      if (
        discardChangedWorkspaceResult(
          request,
          "TEXT_EXPANSION_FAILED",
          "请求期间内容已修改，已停止旧拓展任务"
        )
      ) return;
      dispatch({
        type: "TEXT_EXPANSION_FAILED",
        error: isAbortError(error)
          ? "已取消本次拓展"
          : error.message || "提示词拓展失败",
        finishedAt: Date.now(),
      });
    } finally {
      finishWorkspaceRequest(request);
      if (activeTextAbort === request.controller) activeTextAbort = null;
    }
  }

  async function randomizeTextPrompt() {
    if (state.textGenerating || state.textDecomposing) return;
    dispatch({ type: "START_TEXT_RANDOM", startedAt: Date.now() });
    const request = beginWorkspaceRequest();
    activeTextAbort = request.controller;
    try {
      const result = await apiJson("/api/text/random", {
        method: "POST",
        signal: request.controller.signal,
        body: JSON.stringify({
          provider: state.settings.textProvider,
          targetModel: "anima",
        }),
      });
      if (
        discardChangedWorkspaceResult(
          request,
          "TEXT_RANDOM_FAILED",
          "请求期间内容已修改，已丢弃旧随机结果"
        )
      ) return;
      dispatch({
        type: "APPLY_TEXT_RANDOM",
        item: result.item,
        finishedAt: Date.now(),
      });
    } catch (error) {
      if (
        discardChangedWorkspaceResult(
          request,
          "TEXT_RANDOM_FAILED",
          "请求期间内容已修改，已停止旧随机任务"
        )
      ) return;
      dispatch({
        type: "TEXT_RANDOM_FAILED",
        error: isAbortError(error)
          ? "已取消全随机生成"
          : error.message || "全随机生成失败",
        finishedAt: Date.now(),
      });
    } finally {
      finishWorkspaceRequest(request);
      if (activeTextAbort === request.controller) activeTextAbort = null;
    }
  }

  async function decomposeTextPrompt() {
    const input = state.draftInput.trim();
    if (!input) {
      state.toast = "请输入需要拆解的完整提示词";
      renderToast();
      return;
    }
    if (state.textGenerating || state.textDecomposing) return;

    dispatch({ type: "START_TEXT_DECOMPOSITION", startedAt: Date.now() });
    const request = beginWorkspaceRequest();
    activeTextAbort = request.controller;
    try {
      const result = await apiJson("/api/text/decompose", {
        method: "POST",
        signal: request.controller.signal,
        body: JSON.stringify({
          input,
          provider: state.settings.textProvider,
        }),
      });
      if (
        discardChangedWorkspaceResult(
          request,
          "TEXT_DECOMPOSITION_FAILED",
          "请求期间内容已修改，已丢弃旧拆解结果"
        )
      ) return;
      dispatch({
        type: "APPLY_TEXT_DECOMPOSITION",
        item: result.item,
        finishedAt: Date.now(),
      });
    } catch (error) {
      if (
        discardChangedWorkspaceResult(
          request,
          "TEXT_DECOMPOSITION_FAILED",
          "请求期间内容已修改，已停止旧拆解任务"
        )
      ) return;
      dispatch({
        type: "TEXT_DECOMPOSITION_FAILED",
        error: isAbortError(error)
          ? "已取消提示词拆解"
          : error.message || "提示词拆解失败",
        finishedAt: Date.now(),
      });
    } finally {
      finishWorkspaceRequest(request);
      if (activeTextAbort === request.controller) activeTextAbort = null;
    }
  }

  async function translatePendingBlocks() {
    if (state.translatingPending) return;
    const pending = new Map();
    for (const block of [...state.appliedBlocks, ...state.blocks]) {
      if (
        block.zh?.startsWith("待本地 LLM 翻译：") &&
        block.id &&
        block.en?.trim()
      ) {
        pending.set(block.id, { id: block.id, en: block.en.trim() });
      }
    }
    const items = Array.from(pending.values());
    if (!items.length) {
      state.toast = "当前没有待翻译内容";
      renderToast();
      return;
    }
    dispatch({ type: "START_PENDING_TRANSLATION" });
    const request = beginWorkspaceRequest();
    try {
      const result = await apiJson("/api/text/translate-pending", {
        method: "POST",
        signal: request.controller.signal,
        body: JSON.stringify({ items }),
      });
      if (
        discardChangedWorkspaceResult(
          request,
          "PENDING_TRANSLATION_FAILED",
          "请求期间结构块已修改，已丢弃旧翻译结果"
        )
      ) return;
      dispatch({
        type: "APPLY_PENDING_TRANSLATIONS",
        item: result.item,
      });
    } catch (error) {
      if (
        discardChangedWorkspaceResult(
          request,
          "PENDING_TRANSLATION_FAILED",
          "请求期间结构块已修改，已停止旧翻译任务"
        )
      ) return;
      dispatch({
        type: "PENDING_TRANSLATION_FAILED",
        error: isAbortError(error)
          ? "已取消本次翻译"
          : error.message || "本地 LLM 翻译失败",
      });
    } finally {
      finishWorkspaceRequest(request);
    }
  }

  async function regeneratePromptBlock(blockId) {
    const block = state.blocks.find((item) => item.id === blockId);
    if (
      !block ||
      block.locked ||
      !RANDOM_VARIANT_BLOCKS.has(blockId) ||
      state.regeneratingBlockId ||
      state.batchRegenerating
    ) {
      return;
    }
    dispatch({
      type: "START_BLOCK_VARIANT",
      id: blockId,
      label: block.label,
    });
    const request = beginWorkspaceRequest();
    try {
      const result = await apiJson("/api/text/regenerate-block", {
        method: "POST",
        signal: request.controller.signal,
        body: JSON.stringify({
          targetBlockId: blockId,
          blocks: state.blocks,
          provider: state.settings.textProvider,
          expansionLevel: state.settings.expansionLevel || "balanced",
        }),
      });
      if (
        discardChangedWorkspaceResult(
          request,
          "BLOCK_VARIANT_FAILED",
          "请求期间结构块已修改，已丢弃旧变体结果"
        )
      ) return;
      dispatch({ type: "APPLY_BLOCK_VARIANT", item: result.item });
    } catch (error) {
      if (
        discardChangedWorkspaceResult(
          request,
          "BLOCK_VARIANT_FAILED",
          "请求期间结构块已修改，已停止旧变体任务"
        )
      ) return;
      dispatch({
        type: "BLOCK_VARIANT_FAILED",
        error: isAbortError(error)
          ? "已取消随机变体生成"
          : error.message || "随机变体生成失败",
      });
    } finally {
      finishWorkspaceRequest(request);
    }
  }

  const tagSuggest = {
    element: null,
    items: [],
    index: 0,
    target: null,
    tokenStart: 0,
    tokenEnd: 0,
  };

  function ensureTagSuggestElement() {
    if (!tagSuggest.element) {
      const element = document.createElement("div");
      element.className = "tag-suggest hidden";
      element.addEventListener("mousedown", (event) => {
        const button = event.target.closest("button[data-suggest-index]");
        if (!button) return;
        event.preventDefault();
        applyTagSuggestion(Number(button.dataset.suggestIndex));
      });
      document.body.appendChild(element);
      tagSuggest.element = element;
    }
    return tagSuggest.element;
  }

  function hideTagSuggest() {
    if (tagSuggest.element) tagSuggest.element.classList.add("hidden");
    tagSuggest.items = [];
    tagSuggest.target = null;
  }

  function tagSuggestOpen() {
    return Boolean(
      tagSuggest.target &&
        tagSuggest.items.length &&
        tagSuggest.element &&
        !tagSuggest.element.classList.contains("hidden")
    );
  }

  function suggestDictionary() {
    const dictionary = [...(data.promptTagDictionary || [])];
    for (const block of state.blocks) {
      for (const item of app.splitPromptItems(block.en)) {
        dictionary.push(item);
      }
    }
    return dictionary;
  }

  function updateTagSuggest(target) {
    if (target.dataset.language !== "en") {
      hideTagSuggest();
      return;
    }
    const cursor = target.selectionStart ?? target.value.length;
    const before = target.value.slice(0, cursor);
    const match = before.match(/(?:^|[,，;；\n])\s*([^,，;；\n]*)$/);
    const token = match ? match[1] : "";
    if (!token || token.trim().length < 2) {
      hideTagSuggest();
      return;
    }
    const items = app.suggestTags(suggestDictionary(), token.trim());
    if (!items.length) {
      hideTagSuggest();
      return;
    }
    tagSuggest.items = items;
    tagSuggest.index = 0;
    tagSuggest.target = target;
    tagSuggest.tokenStart = cursor - token.length;
    tagSuggest.tokenEnd = cursor;
    const element = ensureTagSuggestElement();
    element.innerHTML = items
      .map(
        (item, index) => `
          <button
            type="button"
            class="${index === 0 ? "active" : ""}"
            data-suggest-index="${index}"
          >${escapeHtml(item)}</button>`
      )
      .join("");
    const rect = target.getBoundingClientRect();
    element.style.left = `${rect.left + window.scrollX}px`;
    element.style.top = `${rect.bottom + window.scrollY + 4}px`;
    element.classList.remove("hidden");
  }

  function moveTagSuggest(delta) {
    if (!tagSuggestOpen()) return;
    const count = tagSuggest.items.length;
    tagSuggest.index = (tagSuggest.index + delta + count) % count;
    tagSuggest.element
      .querySelectorAll("button[data-suggest-index]")
      .forEach((button, index) => {
        button.classList.toggle("active", index === tagSuggest.index);
      });
  }

  function applyTagSuggestion(index = tagSuggest.index) {
    if (!tagSuggestOpen()) return;
    const target = tagSuggest.target;
    const replacement = tagSuggest.items[index];
    if (!target || replacement === undefined) return;
    const value = target.value;
    const nextValue =
      value.slice(0, tagSuggest.tokenStart) +
      replacement +
      value.slice(tagSuggest.tokenEnd);
    const cursor = tagSuggest.tokenStart + replacement.length;
    target.value = nextValue;
    target.setSelectionRange(cursor, cursor);
    hideTagSuggest();
    state = app.reduceState(state, {
      type: "UPDATE_BLOCK",
      id: target.dataset.blockInput,
      language: "en",
      value: nextValue,
    });
    target.closest(".prompt-block")?.classList.add("dirty");
    renderEditorStatus();
    renderOutput();
  }

  async function saveCurrentRecipe() {
    if (!state.blocks.length) {
      state.toast = "还没有结构块，先生成或拆解一次";
      renderToast();
      return;
    }
    const recipe = {
      id: `recipe-${Date.now()}`,
      type: "snippets",
      name: `${state.projectName || "未命名作品"} · 配方 V${state.version || 1}`,
      meta: "配方 · 整组结构块",
      targetBlock: "",
      en: state.blocks
        .map((block) => block.en)
        .filter(Boolean)
        .join(" / ")
        .slice(0, 160),
      zh: "",
      blocks: state.blocks.map((block) => ({
        id: block.id,
        en: block.en,
        zh: block.zh,
        weight: block.weight,
      })),
    };
    dispatch({ type: "ADD_RECIPE", recipe });
    try {
      await persistFavoriteResource(recipe);
      state.toast = "配方已写入本机数据库";
      renderToast();
    } catch {
      state.toast = "配方已保存在本次会话，但服务端持久化失败";
      renderToast();
    }
  }

  let variantMatrix = null;

  async function generateVariantMatrix(count = 3) {
    const targetBlockIds = state.selectedVariantBlockIds.filter((blockId) => {
      const block = state.blocks.find((item) => item.id === blockId);
      return block && !block.locked && RANDOM_VARIANT_BLOCKS.has(blockId);
    });
    if (targetBlockIds.length < 2 || state.batchRegenerating) {
      state.toast = "变体矩阵至少选择两个可随机结构块";
      renderToast();
      return;
    }
    dispatch({ type: "START_BLOCKS_VARIANT" });
    const workspaceRequest = beginWorkspaceRequest();
    const request = () =>
      apiJson("/api/text/regenerate-blocks", {
        method: "POST",
        signal: workspaceRequest.controller.signal,
        body: JSON.stringify({
          targetBlockIds,
          blocks: state.blocks,
          provider: state.settings.textProvider,
          expansionLevel: state.settings.expansionLevel || "balanced",
        }),
      });
    const settled = await Promise.allSettled(
      Array.from({ length: count }, request)
    );
    finishWorkspaceRequest(workspaceRequest);
    if (
      discardChangedWorkspaceResult(
        workspaceRequest,
        "BLOCKS_VARIANT_FAILED",
        "请求期间结构块已修改，已丢弃旧变体矩阵"
      )
    ) return;
    const candidates = settled
      .filter((entry) => entry.status === "fulfilled")
      .map((entry) => entry.value.item);
    if (!candidates.length) {
      const failure = settled.find((entry) => entry.status === "rejected");
      dispatch({
        type: "BLOCKS_VARIANT_FAILED",
        error: failure?.reason?.message || "变体矩阵生成失败",
      });
      return;
    }
    variantMatrix = { blockIds: targetBlockIds, candidates };
    dispatch({ type: "BLOCKS_VARIANT_READY", count: candidates.length });
  }

  function renderVariantMatrix() {
    const container = $("#variantMatrix");
    if (!container) return;
    if (!variantMatrix) {
      container.classList.add("hidden");
      container.innerHTML = "";
      return;
    }
    const labels = {};
    state.blocks.forEach((block) => {
      labels[block.id] = block.label;
    });
    container.classList.remove("hidden");
    container.innerHTML = `
      <div class="mini-heading">
        <strong>变体矩阵</strong>
        <button type="button" data-dismiss-matrix="1">放弃全部</button>
      </div>
      <div class="variant-matrix-grid">
        ${variantMatrix.candidates
          .map((candidate, index) => {
            const items = (candidate.items || []).filter((item) =>
              variantMatrix.blockIds.includes(item.id)
            );
            return `
              <article class="variant-matrix-card">
                <header>候选 ${index + 1}</header>
                ${items
                  .map(
                    (item) => `
                      <div class="variant-matrix-block">
                        <strong>${escapeHtml(labels[item.id] || item.id)}</strong>
                        <p>${escapeHtml(item.en)}</p>
                        <p class="zh">${escapeHtml(item.zh)}</p>
                      </div>`
                  )
                  .join("")}
                <button type="button" data-pick-matrix="${index}">应用这组</button>
              </article>`;
          })
          .join("")}
      </div>
    `;
  }

  async function regeneratePromptBlocks() {
    const targetBlockIds = state.selectedVariantBlockIds.filter((blockId) => {
      const block = state.blocks.find((item) => item.id === blockId);
      return block && !block.locked && RANDOM_VARIANT_BLOCKS.has(blockId);
    });
    if (targetBlockIds.length < 2 || state.batchRegenerating) {
      state.toast = "联合随机至少选择两个可随机结构块";
      renderToast();
      return;
    }
    dispatch({ type: "START_BLOCKS_VARIANT" });
    const request = beginWorkspaceRequest();
    try {
      const result = await apiJson("/api/text/regenerate-blocks", {
        method: "POST",
        signal: request.controller.signal,
        body: JSON.stringify({
          targetBlockIds,
          blocks: state.blocks,
          provider: state.settings.textProvider,
          expansionLevel: state.settings.expansionLevel || "balanced",
        }),
      });
      if (
        discardChangedWorkspaceResult(
          request,
          "BLOCKS_VARIANT_FAILED",
          "请求期间结构块已修改，已丢弃旧联合随机结果"
        )
      ) return;
      dispatch({ type: "APPLY_BLOCKS_VARIANT", item: result.item });
    } catch (error) {
      if (
        discardChangedWorkspaceResult(
          request,
          "BLOCKS_VARIANT_FAILED",
          "请求期间结构块已修改，已停止旧联合随机任务"
        )
      ) return;
      dispatch({
        type: "BLOCKS_VARIANT_FAILED",
        error: isAbortError(error)
          ? "已取消联合随机"
          : error.message || "联合随机失败",
      });
    } finally {
      finishWorkspaceRequest(request);
    }
  }

  function currentRecipeResolvePayload() {
    const versionPayload = app.buildVersionPayload(state);
    return {
      profileId: versionPayload.metadata.modelProfileId,
      prompts: {
        positiveEn: versionPayload.positiveEn,
        positiveZh: versionPayload.positiveZh,
        negativeEn: versionPayload.negativeEn,
        negativeZh: versionPayload.negativeZh,
      },
      blocks: versionPayload.blocks,
      loras: versionPayload.metadata.loras || [],
      parameterLayers: versionPayload.metadata.parameterLayers,
      randomPlan: versionPayload.metadata.randomPlan,
      instructionHistory: versionPayload.metadata.instructionHistory,
      imageRefs: versionPayload.metadata.imageRefs,
      sourceRefs: versionPayload.metadata.sourceRefs || [],
      metadata: {
        textMode: state.textMode,
        draftInput: state.draftInput,
      },
    };
  }

  async function loadRandomCatalog() {
    dispatch({ type: "RANDOM_CATALOG_LOADING" });
    try {
      const result = await apiJson("/api/text/random-catalog");
      dispatch({ type: "RANDOM_CATALOG_LOADED", item: result.item });
    } catch (error) {
      dispatch({
        type: "RANDOM_CATALOG_FAILED",
        error: error.message || "词库目录加载失败",
      });
    }
  }

  async function generateWordlistPlan() {
    const seed = String($("#librarySeed")?.value || "").trim();
    if (seed && !/^[0-9a-fA-F]{32}$/.test(seed)) {
      state.toast = "词库 Seed 必须是 32 位十六进制（128 bit）";
      renderToast();
      return;
    }
    const catalog = state.randomCatalog || {};
    const payload = {
      ...(seed ? { librarySeed: seed } : {}),
      ...(catalog.version
        ? {
            catalogVersion: catalog.version,
            catalogContentSha256: catalog.contentSha256,
            samplerVersion: catalog.samplerVersion,
            mappingVersion: catalog.mappingVersion,
            profile: catalog.profile,
          }
        : {}),
      lockedEntryIds: [],
      rerollEntryIds: [],
    };
    try {
      const result = await apiJson("/api/text/random-plan", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      dispatch({ type: "APPLY_RANDOM_PLAN", item: result.item });
    } catch (error) {
      dispatch({
        type: "RANDOM_PLAN_FAILED",
        error: error.message || "确定性随机计划生成失败",
      });
    }
  }

  async function previewChineseEdit() {
    const instruction = String($("#editInstruction")?.value || "").trim();
    if (!instruction) {
      state.toast = "请输入要修改的中文指令";
      renderToast();
      return;
    }
    if (state.dirtyBlockIds.length) {
      state.toast = "请先应用或撤销当前结构块编辑，再预览中文指令";
      renderToast();
      return;
    }
    dispatch({ type: "EDIT_PREVIEW_STARTED" });
    editPreviewPayload = null;
    const request = beginWorkspaceRequest();
    try {
      const resolved = await apiJson("/api/recipe/resolve", {
        method: "POST",
        signal: request.controller.signal,
        body: JSON.stringify(currentRecipeResolvePayload()),
      });
      if (!app.isWorkspaceRequestCurrent(request, workspaceSessionId, state)) {
        throw new Error("预览期间工作区已变化，请重新预览");
      }
      const result = await apiJson("/api/text/edit-preview", {
        method: "POST",
        signal: request.controller.signal,
        body: JSON.stringify({
          instruction,
          recipe: resolved.item.recipe,
          baseRecipeHash: resolved.item.recipeHash,
          provider: state.settings.textProvider,
          targetModel: "anima",
        }),
      });
      if (!app.isWorkspaceRequestCurrent(request, workspaceSessionId, state)) {
        throw new Error("预览期间工作区已变化，请重新预览");
      }
      editPreviewRecipe = resolved.item.recipe;
      editPreviewPayload = result.item;
      editPreviewRevision = {
        sessionId: workspaceSessionId,
        workingRevision: state.workingRevision,
        projectRevision: state.projectRevision,
      };
      dispatch({ type: "EDIT_PREVIEW_READY", item: result.item });
    } catch (error) {
      const message = ["provider_not_configured", "model_unavailable"].includes(
        error.code
      )
        ? `AI 修改暂不可用：${error.message}。请前往“模型与接口”完成配置或启动模型。`
        : error.message || "AI 修改预览失败，原提示词未改变";
      dispatch({
        type: "EDIT_PREVIEW_FAILED",
        error: message,
      });
    } finally {
      finishWorkspaceRequest(request);
    }
  }

  async function applyChineseEdit() {
    const preview = editPreviewPayload;
    if (!preview?.ready || !editPreviewRecipe || !editPreviewRevision) return;
    if (
      editPreviewRevision.sessionId !== workspaceSessionId ||
      editPreviewRevision.workingRevision !== state.workingRevision ||
      editPreviewRevision.projectRevision !== state.projectRevision
    ) {
      state.toast = "工作区已变化，旧预览不能应用，请重新预览";
      renderToast();
      return;
    }
    try {
      const result = await apiJson("/api/text/edit-apply", {
        method: "POST",
        body: JSON.stringify({
          recipe: editPreviewRecipe,
          preview,
          parentVersion: state.version,
          newVersion: state.version + 1,
        }),
      });
      dispatch({ type: "APPLY_EDIT_RESULT", item: result.item });
      editPreviewRecipe = null;
      editPreviewPayload = null;
      editPreviewRevision = null;
      await saveCurrentProject();
    } catch (error) {
      editPreviewPayload = null;
      dispatch({
        type: "EDIT_PREVIEW_FAILED",
        error: error.message || "中文修改应用失败",
      });
    }
  }

  async function undoLastRecipeChange() {
    if (state.hasUnsavedChanges || state.dirtyBlockIds.length) {
      state.toast = "请先保存或撤销当前修改，再创建撤销版本";
      renderToast();
      return;
    }
    const current = state.versionHistory.find(
      (item) => item.version === state.version
    );
    const parent = [...state.versionHistory]
      .reverse()
      .find(
        (item) =>
          item.version < state.version && item.metadata?.recipe
      );
    if (!current?.metadata?.recipe || !parent?.metadata?.recipe) {
      state.toast = "没有可撤销的完整配方父版本";
      renderToast();
      return;
    }
    try {
      const result = await apiJson("/api/text/edit-undo", {
        method: "POST",
        body: JSON.stringify({
          currentRecipe: current.metadata.recipe,
          parentRecipe: parent.metadata.recipe,
          currentVersion: current.version,
          parentVersion: parent.version,
          newVersion: state.version + 1,
        }),
      });
      dispatch({ type: "APPLY_EDIT_RESULT", item: result.item });
      await saveCurrentProject();
    } catch (error) {
      state.toast = error.message || "撤销版本创建失败";
      renderToast();
    }
  }

  function legacySaveCurrentProject() {
    if (saveInFlight) return saveInFlight;
    if (state.dirtyBlockIds.length) {
      state.toast = "仍有未应用的结构块修改，请先应用或撤回再保存";
      renderToast();
      return Promise.resolve(null);
    }

    const snapshot = JSON.parse(JSON.stringify(state));
    const savedRevision = snapshot.workingRevision;
    const savedProjectRevision = snapshot.projectRevision;
    const sessionId = workspaceSessionId;
    const projectPayload = app.buildProjectPayload(snapshot);
    const versionPayload = app.buildVersionPayload(snapshot);

    saveInFlight = (async () => {
      dispatch({ type: "PROJECT_SAVE_STARTED" });
      try {
        let projectId = snapshot.projectId;
        let projectResult = null;
        const creatingProject = !projectId;
        if (!projectId) {
          projectResult = await apiJson("/api/projects", {
            method: "POST",
            body: JSON.stringify(projectPayload),
          });
          if (sessionId !== workspaceSessionId) {
            return projectResult.item?.id || null;
          }
          projectId = projectResult.item?.id || "";
          if (!projectId) throw new Error("服务端没有返回作品 ID");
          dispatch({
            type: "PROJECT_CREATED",
            item: projectResult.item,
            savedProjectRevision: snapshot.hasUnsavedChanges
              ? undefined
              : savedProjectRevision,
          });
        }

        if (snapshot.hasUnsavedChanges) {
          const guardedVersionPayload = {
            ...versionPayload,
            baseUpdatedAt:
              projectResult?.item?.updatedAt ||
              versionPayload.baseUpdatedAt ||
              snapshot.projectUpdatedAt,
          };
          const versionResult = await apiJson(
            `/api/projects/${encodeURIComponent(projectId)}/versions`,
            {
              method: "POST",
              body: JSON.stringify(guardedVersionPayload),
            }
          );
          if (sessionId !== workspaceSessionId) return projectId;
          const savedVersion = Number(versionResult.item?.version);
          if (!Number.isSafeInteger(savedVersion) || savedVersion < 1) {
            throw new Error("服务端没有返回有效版本");
          }
          dispatch({
            type: "PROJECT_SAVED",
            item: versionResult.item,
            savedRevision,
            savedAt: new Date().toISOString(),
          });
          const synchronizedProjectPayload = {
            ...projectPayload,
            baseUpdatedAt:
              versionResult.item?.projectUpdatedAt ||
              projectResult?.item?.updatedAt ||
              snapshot.projectUpdatedAt,
            metadata: {
              ...projectPayload.metadata,
              workspaceBaseVersion: savedVersion,
            },
          };
          try {
            projectResult = await apiJson(
              `/api/projects/${encodeURIComponent(projectId)}`,
              {
                method: "PUT",
                body: JSON.stringify(synchronizedProjectPayload),
              }
            );
          } catch (error) {
            throw new Error(
              `V${savedVersion} 已保存，但项目信息同步失败：${error.message || "可再次保存重试"}`
            );
          }
          if (sessionId !== workspaceSessionId) return projectId;
          dispatch({
            type: "PROJECT_HEADER_SAVED",
            savedProjectRevision,
            version: savedVersion,
            updatedAt:
              projectResult.item?.updatedAt || new Date().toISOString(),
          });
        } else {
          if (!creatingProject) {
            projectResult = await apiJson(
              `/api/projects/${encodeURIComponent(projectId)}`,
              {
                method: "PUT",
                body: JSON.stringify(projectPayload),
              }
            );
            if (sessionId !== workspaceSessionId) return projectId;
          }
          dispatch({
            type: "PROJECT_HEADER_SAVED",
            savedProjectRevision,
            updatedAt:
              projectResult.item?.updatedAt || new Date().toISOString(),
          });
        }
        await loadProjects({ silent: true });
        return projectId;
      } catch (error) {
        if (sessionId !== workspaceSessionId) return null;
        dispatch({
          type: "PROJECT_SAVE_FAILED",
          error:
            error.message ||
            "作品暂时没有写入数据库，请确认本地后端已启动",
        });
        return null;
      } finally {
        saveInFlight = null;
      }
    })();
    return saveInFlight;
  }

  const PENDING_SAVE_STORAGE_KEY = "promptStudio.pendingSave.v1";

  function readPendingSaveJournal() {
    const raw = localStorage.getItem(PENDING_SAVE_STORAGE_KEY);
    if (!raw) return null;
    let journal;
    try {
      journal = JSON.parse(raw);
      const request = JSON.parse(journal.requestBody);
      if (
        journal.schema !== 1 ||
        !/^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$/.test(journal.operationId) ||
        request.operationId !== journal.operationId ||
        request.project?.id !== journal.projectId ||
        !["pending", "committed"].includes(journal.status)
      ) {
        throw new Error("invalid journal");
      }
    } catch (cause) {
      const error = new Error("检测到损坏的待恢复保存记录；为避免重复写入，已停止自动保存");
      error.code = "save_journal_corrupt";
      error.cause = cause;
      throw error;
    }
    return journal;
  }

  function writePendingSaveJournal(journal) {
    try {
      localStorage.setItem(PENDING_SAVE_STORAGE_KEY, JSON.stringify(journal));
    } catch (cause) {
      const error = new Error("无法持久化保存恢复记录，已在发送请求前停止");
      error.code = "save_journal_unavailable";
      error.cause = cause;
      throw error;
    }
  }

  function removePendingSaveJournal(operationId) {
    const current = readPendingSaveJournal();
    if (!current || current.operationId === operationId) {
      localStorage.removeItem(PENDING_SAVE_STORAGE_KEY);
    }
  }

  function createPendingSaveJournal(snapshot) {
    const operationId = app.createClientId("save");
    const projectId = snapshot.projectId || app.createClientId("project");
    const versionId = snapshot.hasUnsavedChanges
      ? app.createClientId("version")
      : "";
    const request = app.buildWorkspaceCommitPayload(snapshot, {
      operationId,
      projectId,
      versionId,
    });
    return {
      schema: 1,
      status: "pending",
      operationId,
      projectId,
      originalProjectId: snapshot.projectId || "",
      savedRevision: snapshot.workingRevision,
      savedProjectRevision: snapshot.projectRevision,
      originSessionId: workspaceSessionId,
      requestBody: JSON.stringify(request),
      committedResult: null,
      createdAt: new Date().toISOString(),
    };
  }

  function applyCommittedSave(journal, result, { automatic = false } = {}) {
    const project = result?.project;
    if (!project || project.id !== journal.projectId) {
      throw new Error("服务返回的作品与待保存操作不匹配");
    }
    const blankAutomaticWorkspace =
      automatic &&
      !state.projectId &&
      state.workingRevision === 0 &&
      state.projectRevision === 0 &&
      !state.hasUnsavedChanges &&
      !state.hasUnsavedProjectChanges;
    if (blankAutomaticWorkspace) {
      dispatch({ type: "PROJECT_OPENED", item: project });
      state.toast = `已恢复并确认上次保存：${project.name || project.id}`;
      renderToast();
      return;
    }
    const sameWorkspace =
      workspaceSessionId === journal.originSessionId &&
      (journal.originalProjectId
        ? state.projectId === journal.originalProjectId
        : !state.projectId || state.projectId === journal.projectId);
    if (!sameWorkspace) return;
    if (!journal.originalProjectId) {
      dispatch({
        type: "PROJECT_CREATED",
        item: project,
        savedProjectRevision: journal.savedProjectRevision,
      });
    }
    if (result.version) {
      dispatch({
        type: "PROJECT_SAVED",
        item: result.version,
        savedRevision: journal.savedRevision,
        savedProjectRevision: journal.savedProjectRevision,
        savedAt: result.version.createdAt,
      });
    }
    dispatch({
      type: "PROJECT_HEADER_SAVED",
      savedProjectRevision: journal.savedProjectRevision,
      version: result.version?.version,
      updatedAt: project.updatedAt,
    });
  }

  function commitPendingSaveJournal(
    journal,
    { automatic = false } = {}
  ) {
    saveInFlight = (async () => {
      dispatch({ type: "PROJECT_SAVE_STARTED" });
      try {
        let result = journal.committedResult;
        let shouldPersistCommittedResult = false;
        if (journal.status !== "committed" || !result) {
          const response = await apiJson("/api/workspace/commit", {
            method: "POST",
            headers: { "Idempotency-Key": journal.operationId },
            body: journal.requestBody,
          });
          result = response?.item;
          if (result?.operationId !== journal.operationId) {
            throw new Error("服务返回的保存操作编号不匹配");
          }
          shouldPersistCommittedResult = true;
        }
        if (journal.kind === "creative_intake_metadata") {
          app.validateCreativeIntakeMetadataCommitResult(journal, result);
        }
        if (shouldPersistCommittedResult) {
          journal.status = "committed";
          journal.committedResult = result;
          writePendingSaveJournal(journal);
        }
        applyCommittedSave(journal, result, { automatic });
        removePendingSaveJournal(journal.operationId);
        await loadProjects({ silent: true });
        return journal.projectId;
      } catch (error) {
        dispatch({
          type: "PROJECT_SAVE_FAILED",
          error:
            error.message ||
            "作品暂时没有完成可确认写入；恢复记录已保留，可再次保存重试",
        });
        return null;
      } finally {
        saveInFlight = null;
      }
    })();
    return saveInFlight;
  }

  function saveCreativeIntakeMetadata() {
    if (saveInFlight) return saveInFlight;
    let pending;
    try {
      pending = readPendingSaveJournal();
    } catch (error) {
      dispatch({ type: "PROJECT_SAVE_FAILED", error: error.message });
      return Promise.resolve(null);
    }
    if (pending) return saveCurrentProject();

    const snapshot = JSON.parse(JSON.stringify(state));
    const journal = app.createCreativeIntakeSaveJournal(snapshot, {
      originSessionId: workspaceSessionId,
    });
    try {
      writePendingSaveJournal(journal);
    } catch (error) {
      dispatch({ type: "PROJECT_SAVE_FAILED", error: error.message });
      return Promise.resolve(null);
    }
    return commitPendingSaveJournal(journal);
  }

  function saveCurrentProject({ automatic = false } = {}) {
    if (saveInFlight) return saveInFlight;
    let pending;
    try {
      pending = readPendingSaveJournal();
    } catch (error) {
      dispatch({ type: "PROJECT_SAVE_FAILED", error: error.message });
      return Promise.resolve(null);
    }
    if (!pending && state.dirtyBlockIds.length) {
      state.toast = "仍有未应用的结构块修改，请先应用或撤回再保存";
      renderToast();
      return Promise.resolve(null);
    }
    if (!pending) {
      const snapshot = JSON.parse(JSON.stringify(state));
      pending = createPendingSaveJournal(snapshot);
      try {
        writePendingSaveJournal(pending);
      } catch (error) {
        dispatch({ type: "PROJECT_SAVE_FAILED", error: error.message });
        return Promise.resolve(null);
      }
    }

    return commitPendingSaveJournal(pending, { automatic });
  }

  function dispatch(action) {
    state = app.reduceState(state, action);
    render();
  }

  function statusLabel(status) {
    return app.statusLabel(status);
  }

  function statusClass(status) {
    return app.normalizeModelStatus(status);
  }

  function renderNavigation() {
    $$(".top-nav [data-nav]").forEach((button) => {
      button.classList.toggle("active", button.dataset.nav === state.view);
    });
  }

  function renderViews() {
    const director = app.buildDirectorRenderModel(state);
    const showWorkbench = director.showWorkbench;
    $("#homeView").classList.toggle("hidden", showWorkbench);
    $("#workbenchArea").classList.toggle("hidden", !showWorkbench);
    $("#textView").classList.toggle(
      "hidden",
      !showWorkbench || state.view !== "text"
    );
    $("#imageView").classList.toggle(
      "hidden",
      !showWorkbench || state.view !== "image"
    );
  }

  function renderDirector() {
    const model = app.buildDirectorRenderModel(state);
    const conversation = $("#directorConversation");
    const fallbackMessages = [
      {
        role: "assistant",
        text: "告诉我你想创作的画面、角色或情绪。我会先整理为可确认的创作简报。",
      },
      {
        role: "system",
        text: "目前还没有引用素材；可附上一张参考图。",
      },
    ];
    const messages = model.messages.length
      ? model.messages
      : fallbackMessages;
    if (conversation) {
      conversation.innerHTML = messages
        .map((message) => {
          const role = ["user", "assistant", "system"].includes(
            message.role
          )
            ? message.role
            : "system";
          const label =
            role === "user"
              ? "你"
              : role === "assistant"
                ? "创作总监"
                : "阶段记录";
          return `
            <article class="director-message director-message--${role}">
              <span class="${
                role === "system"
                  ? "director-source-badge"
                  : "director-message-role"
              }">${label}</span>
              <p>${escapeHtml(message.text)}</p>
            </article>`;
        })
        .join("");
      conversation.setAttribute(
        "aria-busy",
        model.busy ? "true" : "false"
      );
      conversation.scrollTop = conversation.scrollHeight;
    }

    const errorBanner = $(".director-error-banner");
    if (errorBanner) {
      errorBanner.hidden = !model.error;
      errorBanner.textContent = model.error;
    }

    const input = $("#directorMessageInput");
    const sendButton = $("#directorSendBtn");
    const stopButton = $(
      '.director-composer button[aria-label="停止当前请求"]'
    );
    if (input) input.disabled = model.busy;
    if (sendButton) {
      sendButton.disabled = model.busy;
      sendButton.textContent = model.busy
        ? "总监正在整理…"
        : "发送给总监";
    }
    if (stopButton) stopButton.disabled = !model.busy;

    $$('input[name="directorProvider"]').forEach((control) => {
      control.checked =
        control.value === (state.settings.textProvider || "local");
      control.disabled = model.busy;
    });

    const stageBadge = $(".director-stage-badge");
    if (stageBadge) {
      stageBadge.textContent =
        {
          intake: "第一步：描述与方向",
          direction_selected: "第二步：细化方向",
          brief_draft: "第三步：确认简报",
          brief_confirmed: "第四步：选择模型",
          model_selected: "可进入工作台",
        }[model.stage] || "创作流程";
    }

    const directions = $("#directorDirections");
    if (directions) {
      const cards = model.directions.length
        ? model.directions
            .map(
              (direction) => `
                <button
                  class="director-direction${
                    direction.selected ? " selected" : ""
                  }"
                  type="button"
                  data-director-direction="${escapeHtml(direction.id)}"
                  ${
                    model.busy || model.stage !== "intake"
                      ? "disabled"
                      : ""
                  }
                >
                  <strong>${escapeHtml(direction.label)}</strong>
                  <span>${escapeHtml(direction.summary)}</span>
                </button>`
            )
            .join("")
        : `
            <button class="director-direction" type="button" disabled>
              <strong>方向将在对话后生成</strong>
              <span>选择一个方向后，创作总监会整理简报。</span>
            </button>`;
      directions.innerHTML = `
        <div class="director-section-heading">
          <div>
            <p class="eyebrow">DIRECTIONS</p>
            <h3 id="directorDirectionsTitle">创作方向</h3>
          </div>
          <span>${
            model.directions.length
              ? `${model.directions.length} 个方向`
              : "等待你的描述"
          }</span>
        </div>
        ${cards}`;
    }

    const briefCard = $("#directorBriefCard");
    if (briefCard) {
      const brief = model.brief;
      const status = brief
        ? brief.status === "confirmed"
          ? "已锁定"
          : "待确认"
        : "等待草案";
      const controls = brief
        ? brief.status === "draft"
          ? `
              <button
                class="secondary-button"
                type="button"
                data-action="director-confirm-brief"
                ${
                  !model.canConfirmBrief || model.busy
                    ? "disabled"
                    : ""
                }
              >确认简报</button>`
          : `
              <button
                class="secondary-button"
                type="button"
                data-action="director-reopen-brief"
                ${model.busy ? "disabled" : ""}
              >返回修改简报</button>`
        : `<button class="secondary-button" type="button" disabled>确认简报</button>`;
      const sourceLabels = {
        user: "用户",
        image: "图片",
        ai: "AI",
        model_rule: "模型规则",
      };
      const groups = model.briefGroups
        .map((group) => {
          const rows = group.rows.length
            ? group.rows
                .map(
                  (row) => `
                    <li>
                      <div>
                        ${
                          row.source
                            ? `<span class="director-source-badge director-source-badge--${escapeHtml(
                                row.source.type
                              )}">${escapeHtml(
                                row.sourceLabel ||
                                  sourceLabels[row.source.type] ||
                                  row.source.type
                              )}</span>`
                            : ""
                        }
                        ${
                          row.locked
                            ? '<span class="director-lock-badge">已锁定</span>'
                            : ""
                        }
                        <span>${escapeHtml(row.text)}</span>
                      </div>
                      ${
                        row.revisable
                          ? `<button
                              class="director-brief-revise"
                              type="button"
                              data-director-revise-item="${escapeHtml(row.id)}"
                              ${model.busy ? "disabled" : ""}
                            >要求修改</button>`
                          : ""
                      }
                    </li>`
                )
                .join("")
            : '<li class="director-empty-state">暂无</li>';
          return `
            <section class="director-brief-group" data-brief-group="${escapeHtml(
              group.id
            )}">
              <h4>${escapeHtml(group.label)}</h4>
              <ul>${rows}</ul>
            </section>`;
        })
        .join("");
      briefCard.innerHTML = `
        <div class="director-section-heading">
          <div>
            <p class="eyebrow">CONFIRMATION</p>
            <h3 id="directorBriefTitle">创作简报</h3>
          </div>
          <span class="director-lock-badge">${status}</span>
        </div>
        <p>${
          brief
            ? escapeHtml(brief.summary)
            : "确认方向后，这里会显示可确认的创作简报。"
        }</p>
        <div class="director-brief-groups" id="directorBriefGroups">${groups}</div>
        ${controls}`;
    }

    const modelGate = $("#directorModelGate");
    const modelSelect = $("#directorModelSelect");
    const continueButton = $("#directorContinueBtn");
    if (modelGate) {
      modelGate.classList.toggle("hidden", !model.showModelGate);
      modelGate.classList.toggle("is-locked", !model.showModelGate);
      modelGate.setAttribute(
        "aria-hidden",
        model.showModelGate ? "false" : "true"
      );
    }
    if (modelSelect) {
      const placeholder = model.modelProfiles.length
        ? `<option value="">请选择已加载的模型档案</option>`
        : `<option value="">模型档案暂不可用</option>`;
      modelSelect.innerHTML =
        placeholder +
        model.modelProfiles
          .map(
            (profile) => `
              <option
                value="${escapeHtml(profile.id)}"
                ${profile.selected ? "selected" : ""}
              >${escapeHtml(profile.label)}${
                profile.readiness
                  ? ` · ${escapeHtml(profile.readiness)}`
                  : ""
              }</option>`
          )
          .join("");
      modelSelect.disabled =
        !model.showModelGate ||
        model.busy ||
        !model.modelProfiles.length;
      if (model.selectedModelProfileId) {
        modelSelect.value = model.selectedModelProfileId;
      }
    }
    if (continueButton) {
      continueButton.disabled = !model.canContinue || model.busy;
    }
    const modelProfileStatus = $("#directorModelProfileStatus");
    if (modelProfileStatus) {
      const selectedProfile = model.modelProfiles.find(
        (profile) => profile.selected
      );
      if (!selectedProfile) {
        modelProfileStatus.textContent = model.modelProfiles.length
          ? "请选择目标模型以查看档案就绪度与证据。"
          : "当前没有可用的模型档案。";
      } else {
        const evidence = selectedProfile.evidence.length
          ? selectedProfile.evidence
              .map(
                (item) =>
                  `${item.title || item.id || "未命名证据"}（${
                    item.verificationStatus || "状态未标记"
                  }）`
              )
              .join("；")
          : "暂无已记录证据";
        modelProfileStatus.textContent = `档案状态：${
          selectedProfile.readiness || "未标记"
        }；生成就绪：${
          selectedProfile.generationReady ? "是" : "否"
        }；证据：${evidence}`;
      }
    }

    const workbenchBrief = $("#creativeWorkbenchBrief");
    if (workbenchBrief) {
      workbenchBrief.textContent = model.brief?.summary
        ? `已确认简报：${model.brief.summary}`
        : "等待已确认的创作简报";
    }
    const workbenchModel = $("#creativeWorkbenchModel");
    if (workbenchModel) {
      const selectedProfile = model.modelProfiles.find(
        (profile) => profile.selected
      );
      workbenchModel.textContent = model.selectedModelProfileId
        ? `目标模型：${
            selectedProfile?.label || model.selectedModelProfileId
          }`
        : "等待已选择的目标模型";
    }

    const imageCards = $("#directorImageCards");
    if (imageCards) {
      const useLabels = {
        character: "人物",
        appearance: "外貌",
        outfit: "服装",
        action: "动作",
        environment: "环境",
        composition: "构图",
        lighting: "光影",
        style: "风格",
      };
      const cards = app.buildDirectorImageCards(
        state.creativeIntake,
        (imageId) => directorImageCollectionController.get(imageId),
        { analyzingIds: Array.from(directorAnalyzingImageIds) }
      );
      imageCards.innerHTML = cards.length
        ? cards
            .map((card) => {
              const effectiveStatus = card.status;
              const statusText = {
                missing: "需重新附加原图",
                idle: "发送前将在本机分析",
                analyzing: "正在本地分析…",
                ready: "本地分析完成",
                partial: "已保留成功证据，可重试失败项",
                error: "分析失败，可单独重试",
              }[effectiveStatus] || "等待本地分析";
              return `
                <article class="director-image-card" data-director-image-id="${escapeHtml(
                  card.id
                )}">
                  <div class="director-image-card-main">
                    ${
                      card.previewUrl
                        ? `<img src="${escapeHtml(card.previewUrl)}" alt="${escapeHtml(
                            card.name
                          )} 本地预览" />`
                        : '<div class="director-image-card-placeholder" aria-hidden="true">无本地预览</div>'
                    }
                    <div class="director-image-card-copy">
                      <strong>${escapeHtml(card.name)}</strong>
                      <span>${escapeHtml(statusText)}</span>
                    </div>
                    <div class="director-image-card-actions">
                      <button type="button" data-director-image-action="replace" ${
                        model.busy ? "disabled" : ""
                      }>替换</button>
                      <button type="button" data-director-image-action="remove" ${
                        model.busy ? "disabled" : ""
                      }>删除</button>
                      <button type="button" data-director-image-action="retry" ${
                        !card.canRetry || model.busy ? "disabled" : ""
                      }>重试</button>
                    </div>
                  </div>
                  <fieldset class="director-image-uses" ${
                    model.busy ? "disabled" : ""
                  }>
                    <legend>希望借用这张图的哪些内容</legend>
                    <div class="director-image-use-chips">
                      ${Object.entries(useLabels)
                        .map(
                          ([id, label]) => `
                            <button type="button" data-director-image-use="${id}"
                              aria-pressed="${
                                card.requestedUses.includes(id)
                                  ? "true"
                                  : "false"
                              }" class="${
                                card.requestedUses.includes(id)
                                  ? "is-selected"
                                  : ""
                              }">${label}</button>`
                        )
                        .join("")}
                      <button type="button" data-director-image-action="suggest"
                        aria-pressed="${card.aiSuggest ? "true" : "false"}"
                        class="${card.aiSuggest ? "is-selected" : ""}">让 AI 建议</button>
                    </div>
                  </fieldset>
                  ${
                    card.failures.length
                      ? `<ul class="director-analysis-failures">${card.failures
                          .map(
                            (failure) =>
                              `<li>${escapeHtml(failure)}</li>`
                          )
                          .join("")}</ul>`
                      : ""
                  }
                </article>`;
            })
            .join("")
        : '<p class="director-empty-state">尚未添加参考图。</p>';
    }
    const imageUiState = app.buildDirectorImageUiState(
      state.creativeIntake.inputs.images.length,
      model.busy || model.imageAnalysisStatus === "analyzing"
    );
    const addButton = $("#directorImageAddBtn");
    if (addButton) {
      addButton.classList.toggle("is-disabled", imageUiState.addDisabled);
      addButton.setAttribute(
        "aria-disabled",
        imageUiState.addDisabled ? "true" : "false"
      );
    }
    const imageInput = $("#directorImageInput");
    if (imageInput) imageInput.disabled = imageUiState.inputDisabled;
    const imageDropZone = $("#directorImageDropZone");
    if (imageDropZone) {
      imageDropZone.setAttribute(
        "aria-disabled",
        imageUiState.dropDisabled ? "true" : "false"
      );
      imageDropZone.tabIndex = imageUiState.dropDisabled ? -1 : 0;
    }
    const imageErrors = $("#directorImageErrors");
    if (imageErrors) {
      imageErrors.hidden = !directorImageFileErrors.length;
      imageErrors.innerHTML = directorImageFileErrors
        .map(
          (item) =>
            `<li>${escapeHtml(item.name)}：${escapeHtml(item.error)}</li>`
        )
        .join("");
    }

    const workbench = $("#workbenchArea");
    let reopenButton = $("#directorWorkbenchReopenBtn");
    if (workbench && !reopenButton) {
      reopenButton = document.createElement("button");
      reopenButton.id = "directorWorkbenchReopenBtn";
      reopenButton.type = "button";
      reopenButton.className = "secondary-button";
      reopenButton.dataset.action = "director-reopen-brief";
      reopenButton.textContent = "返回修改创作简报";
      workbench.querySelector(".source-pane")?.prepend(reopenButton);
    }
    if (reopenButton) {
      reopenButton.hidden = !model.showWorkbench;
      reopenButton.disabled = model.busy;
    }
  }

  function formatProjectDate(value) {
    const date = new Date(value || "");
    if (Number.isNaN(date.getTime())) return "时间未知";
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function renderProjects() {
    const container = $("#recentProjectList");
    const status = $("#recentProjectStatus");
    if (!container || !status) return;

    if (state.projectsStatus === "loading") {
      status.textContent = "正在读取本机项目库…";
      status.dataset.status = "loading";
      container.innerHTML = `
        <div class="project-list-message" role="status">
          <span class="project-loading-dot" aria-hidden="true"></span>
          正在加载最近项目
        </div>`;
      return;
    }
    if (state.projectsStatus === "error") {
      status.textContent = "项目库暂时不可用";
      status.dataset.status = "error";
      container.innerHTML = `
        <div class="project-list-message project-list-error" role="alert">
          <strong>无法读取项目列表</strong>
          <span>${escapeHtml(state.projectsError || "请确认本地后端已启动")}</span>
          <button class="secondary-button" type="button" data-action="retry-projects">重试</button>
        </div>`;
      return;
    }
    if (state.projectsStatus !== "ready") {
      status.textContent = "等待读取项目库";
      status.dataset.status = "idle";
      container.innerHTML = "";
      return;
    }

    status.dataset.status = "ready";
    status.textContent = state.projects.length
      ? `${state.projects.length} 个本机项目`
      : "尚无已保存项目";
    if (!state.projects.length) {
      container.innerHTML = `
        <div class="project-list-message project-list-empty">
          <strong>还没有项目</strong>
          <span>完成一次生成并保存后，会出现在这里。</span>
        </div>`;
      return;
    }

    container.innerHTML = state.projects
      .map((project) => {
        const id = typeof project.id === "string" ? project.id : "";
        const name =
          typeof project.name === "string" && project.name.trim()
            ? project.name
            : "未命名作品";
        const latestVersion = Math.max(
          0,
          Number(project.latestVersion) || 0
        );
        const versionCount = Math.max(
          0,
          Number(project.versionCount) || latestVersion
        );
        const opening = state.openingProjectId === id;
        return `
          <article class="project-card">
            <button
              class="project-card-open"
              type="button"
              data-open-project="${escapeHtml(id)}"
              ${opening ? "disabled" : ""}
            >
              <span class="project-card-heading">
                <strong>${escapeHtml(name)}</strong>
                <b>${project.mode === "image" ? "图片解析" : "文生图"}</b>
              </span>
              <span class="project-card-meta">
                <span>${latestVersion ? `最新 V${latestVersion}` : "空草稿"}</span>
                <span>${versionCount} 个版本</span>
                <span>${escapeHtml(formatProjectDate(project.updatedAt))}</span>
              </span>
              <span class="project-card-action">${opening ? "正在打开…" : "打开并继续 →"}</span>
            </button>
          </article>`;
      })
      .join("");
  }

  function renderProjectContext() {
    const nameInput = $("#projectNameInput");
    const saveButton = $("#saveDraftButton");
    const saveStatus = $("#projectSaveStatus");
    if (nameInput && nameInput.value !== state.projectName) {
      nameInput.value = state.projectName;
    }
    if (nameInput) nameInput.disabled = state.saveStatus === "saving";
    if (!saveButton || !saveStatus) return;

    saveButton.disabled = state.saveStatus === "saving";
    saveButton.textContent =
      state.saveStatus === "saving"
        ? "正在保存…"
        : state.hasUnsavedChanges
          ? state.version
            ? `保存为 V${state.version + 1}`
            : "保存首个版本"
          : state.hasUnsavedProjectChanges
            ? "保存项目信息"
          : state.projectId
            ? "保存项目信息"
            : "保存空草稿";

    let message = "尚未保存";
    let statusName = "idle";
    if (state.dirtyBlockIds.length) {
      message = `${state.dirtyBlockIds.length} 项结构块修改尚未应用`;
      statusName = "warning";
    } else if (state.saveStatus === "saving") {
      message = "正在写入本机数据库";
      statusName = "saving";
    } else if (state.saveStatus === "error") {
      message = state.saveError || "保存失败，可重试";
      statusName = "error";
    } else if (state.hasUnsavedChanges) {
      message = state.restoreFromVersion
        ? `已载入 V${state.restoreFromVersion}，保存后生成 V${state.version + 1}`
        : `内容已应用，保存后生成 V${state.version + 1}`;
      statusName = "warning";
    } else if (state.hasUnsavedProjectChanges) {
      message = "项目名称或输入尚未保存";
      statusName = "warning";
    } else if (state.saveStatus === "saved" || state.version > 0) {
      message = state.lastSavedAt
        ? `已保存 · ${formatProjectDate(state.lastSavedAt)}`
        : `已保存 V${state.version}`;
      statusName = "saved";
    } else if (state.projectId) {
      message = "空草稿已保存";
      statusName = "saved";
    }
    saveStatus.textContent = message;
    saveStatus.dataset.status = statusName;
  }

  function renderTextModes() {
    $$("[data-text-mode]").forEach((button) => {
      button.classList.toggle(
        "active",
        button.dataset.textMode === state.textMode
      );
    });
    $$("[data-mode-panel]").forEach((panel) => {
      panel.classList.toggle(
        "hidden",
        panel.dataset.modePanel !== state.textMode
      );
    });
    const activePanel = $(`[data-mode-panel="${state.textMode}"]`);
    const progressPanel = $("#generationProgress");
    if (
      progressPanel &&
      activePanel &&
      state.textMode !== "reference" &&
      progressPanel.parentElement !== activePanel
    ) {
      activePanel.append(progressPanel);
    }
    const draft = $("#draftInput");
    if (draft && draft.value !== state.draftInput) draft.value = state.draftInput;
  }

  function renderReferences() {
    const search = ($("#referenceSearch")?.value || "").trim().toLowerCase();
    const references = state.references.filter((item) => {
      const haystack = [
        item.title,
        item.category,
        item.model,
        ...item.tags,
      ]
        .join(" ")
        .toLowerCase();
      return !search || haystack.includes(search);
    });

    if (!references.length) {
      const message = search
        ? "没有匹配的参考条目，换个关键词试试。"
        : "暂无参考条目。";
      $("#referenceCards").innerHTML =
        `<p class="reference-empty">${escapeHtml(message)}</p>`;
      return;
    }
    $("#referenceCards").innerHTML = references
      .map(
        (item) => `
          <article class="reference-card">
            <img src="${escapeHtml(item.image)}" alt="${escapeHtml(item.title)}" />
            <div class="reference-card-body">
              <strong>${escapeHtml(item.title)}</strong>
              <p>${escapeHtml(item.promptZh)}</p>
              <button type="button" data-import-reference="${escapeHtml(item.id)}">带入拓展</button>
            </div>
          </article>
        `
      )
      .join("");
  }

  function artistTagForResource(item) {
    const items = app.splitPromptItems(item.en || "");
    return items[0] || item.name || "";
  }

  function selectedArtistEntries(favorites) {
    return favorites
      .filter((item) => artistMix.has(item.id))
      .map((item) => ({
        tag: artistTagForResource(item),
        weight: artistMix.get(item.id),
      }));
  }

  function renderArtistMixer() {
    const container = $("#artistMixer");
    if (!container) return;
    const active = quickTab === "favorite-artists";
    container.classList.toggle("hidden", !active);
    if (!active) return;
    const favorites = favoriteResourcesForTab("favorite-artists");
    for (const id of Array.from(artistMix.keys())) {
      if (!favorites.some((item) => item.id === id)) artistMix.delete(id);
    }
    if (!favorites.length) {
      container.innerHTML =
        '<p class="artist-mixer-empty">先在上方列表收藏几位画师，就能在这里混合权重。</p>';
      return;
    }
    const preview = app.composeArtistMix(selectedArtistEntries(favorites));
    container.innerHTML = `
      <div class="mini-heading">
        <strong>画师混合器</strong>
        <span>勾选并配权重</span>
      </div>
      ${favorites
        .map((item) => {
          const checked = artistMix.has(item.id);
          const weight = artistMix.get(item.id) ?? 100;
          const itemId = escapeHtml(item.id);
          return `
            <div class="artist-mix-row">
              <label class="artist-mix-pick">
                <input
                  type="checkbox"
                  data-mix-artist="${itemId}"
                  ${checked ? "checked" : ""}
                />
                <span>${escapeHtml(artistTagForResource(item))}</span>
              </label>
              <label class="artist-mix-weight ${checked ? "" : "hidden"}">
                <input
                  type="range"
                  min="10"
                  max="150"
                  step="5"
                  value="${weight}"
                  data-mix-weight="${itemId}"
                />
                <b>${weight}%</b>
              </label>
            </div>`;
        })
        .join("")}
      <code class="artist-mix-preview ${preview ? "" : "hidden"}" id="artistMixPreview">${escapeHtml(preview)}</code>
      <button
        class="secondary-button full"
        id="applyArtistMixBtn"
        type="button"
        ${preview ? "" : "disabled"}
      >写入画师块</button>
    `;
  }

  function refreshArtistMixPreview() {
    const favorites = favoriteResourcesForTab("favorite-artists");
    const preview = app.composeArtistMix(selectedArtistEntries(favorites));
    const element = $("#artistMixPreview");
    if (element) {
      element.textContent = preview;
      element.classList.toggle("hidden", !preview);
    }
    const button = $("#applyArtistMixBtn");
    if (button) button.disabled = !preview;
  }

  function applyArtistMix() {
    const favorites = favoriteResourcesForTab("favorite-artists");
    const composed = app.composeArtistMix(selectedArtistEntries(favorites));
    if (!composed) return;
    if (!state.blocks.length) {
      state.toast = "请先生成结构块，再写入画师混合";
      renderToast();
      return;
    }
    const artistBlock = state.blocks.find((block) => block.id === "artist");
    if (artistBlock && artistBlock.locked) {
      state.toast = "画师块已锁定，请先解锁";
      renderToast();
      return;
    }
    dispatch({
      type: "UPDATE_BLOCK",
      id: "artist",
      language: "en",
      value: composed,
    });
    dispatch({
      type: "UPDATE_BLOCK",
      id: "artist",
      language: "zh",
      value: `待本地 LLM 翻译：${composed}`,
    });
    state.toast = "画师混合已写入，右侧实时预览";
    renderToast();
  }

  function renderQuickPicks() {
    $$("[data-quick-tab]").forEach((button) => {
      button.classList.toggle("active", button.dataset.quickTab === quickTab);
    });
    const favoriteCharacters = favoriteResourcesForTab("favorite-characters");
    const favoriteArtists = favoriteResourcesForTab("favorite-artists");
    const favoriteCharactersTab = $("#favoriteCharactersTab");
    if (favoriteCharactersTab) {
      favoriteCharactersTab.textContent = favoriteCharacters.length
        ? `角色收藏 ${favoriteCharacters.length}`
        : "角色收藏";
    }
    const favoriteArtistsTab = $("#favoriteArtistsTab");
    if (favoriteArtistsTab) {
      favoriteArtistsTab.textContent = favoriteArtists.length
        ? `画师收藏 ${favoriteArtists.length}`
        : "画师收藏";
    }
    const isExternalTab = quickTab === "characters" || quickTab === "artists";
    const isFavoritesTab = isFavoriteTab();
    const isGridTab = isExternalTab || isFavoritesTab;
    const favoriteQuery = resourceQuery.toLowerCase();
    const items = isFavoritesTab
      ? favoriteResourcesForTab().filter((item) =>
          [item.name, item.meta, item.en, item.source]
            .join(" ")
            .toLowerCase()
            .includes(favoriteQuery)
        )
      : state.resources[quickTab] || [];
    const source = $("#resourceSource");
    const search = $("#resourceSearchField");
    const tools = $("#resourceTools");
    const pager = $("#resourcePager");
    const picks = $("#quickPicks");
    source.classList.toggle("hidden", !(isExternalTab || isFavoritesTab));
    search.classList.toggle("hidden", !(isExternalTab || isFavoritesTab));
    tools.classList.toggle("hidden", !isExternalTab);
    source.dataset.status = resourceSource.status;
    $("#resourceSourceLabel").textContent = isFavoritesTab
      ? `${favoriteTypeForTab() === "characters" ? "角色收藏夹" : "画师收藏夹"} · ${
          favoriteResourcesForTab().length
        } 项`
      : resourceSource.label;
    const scoreSort = $("[data-resource-sort='score']");
    if (scoreSort) scoreSort.classList.toggle("hidden", quickTab !== "artists");
    $$("[data-resource-sort]").forEach((button) => {
      button.classList.toggle("active", button.dataset.resourceSort === resourceSort);
    });
    picks.classList.toggle("resource-grid", isGridTab);
    picks.classList.toggle("resource-list", !isGridTab);
    pager.classList.toggle("hidden", !isExternalTab || resourcePages <= 1);
    $("#resourcePageLabel").textContent = `第 ${resourcePage} / ${resourcePages} 页 · ${resourceTotal} 项`;
    $$("[data-resource-page]").forEach((button) => {
      button.disabled =
        (button.dataset.resourcePage === "prev" && resourcePage <= 1) ||
        (button.dataset.resourcePage === "next" && resourcePage >= resourcePages);
    });
    const searchInput = $("#resourceSearch");
    if (searchInput.value !== resourceQuery) searchInput.value = resourceQuery;
    picks.innerHTML = items.length
      ? items
      .map((item) => {
        const itemId = escapeHtml(item.id);
        const thumbnail =
          item.thumbUrl && item.hasImage !== false
            ? `<img src="${escapeHtml(item.thumbUrl)}" alt="" loading="lazy" />`
            : `<i class="quick-pick-placeholder">${
            item.type === "artists" ? "A" : "角"
              }</i>`;
        const copy = `
          <span class="quick-pick-copy">
            <strong>${escapeHtml(item.name)}</strong>
            <span>${escapeHtml(item.meta)}</span>
            ${item.source ? `<small>${escapeHtml(item.source)}</small>` : ""}
          </span>`;
        if (isGridTab) {
          const favorite = isFavoriteResource(item.id);
          return `
            <article class="quick-pick">
              <button
                class="resource-preview-button"
                type="button"
                data-preview-resource="${itemId}"
                aria-label="预览 ${escapeHtml(item.name)}"
              >
                ${thumbnail}
                ${copy}
              </button>
              <button
                class="resource-favorite-button ${favorite ? "active" : ""}"
                type="button"
                data-favorite-resource="${itemId}"
                title="${favorite ? "取消收藏" : "收藏"}"
                aria-label="${favorite ? "取消收藏" : "收藏"} ${escapeHtml(item.name)}"
              >${favorite ? "★" : "☆"}</button>
              <button
                class="resource-add-button"
                type="button"
                data-quick-pick="${itemId}"
                title="带入结构化编辑"
                aria-label="带入 ${escapeHtml(item.name)}"
              >＋</button>
            </article>`;
        }
        return `
          <button class="quick-pick" type="button" data-quick-pick="${itemId}">
            ${thumbnail}
            ${copy}
            <b>＋</b>
          </button>`;
      })
      .join("")
      : `<p class="empty-resource">${
          resourceSource.status === "loading"
            ? "正在载入本地资源库..."
            : (isExternalTab || isFavoritesTab) && resourceQuery
              ? "没有找到匹配的资源。"
              : isFavoritesTab
                ? favoriteTypeForTab() === "characters"
                  ? "还没有收藏角色。"
                  : "还没有收藏画师。"
              : "还没有保存的素材，可以从结构块保存或点击“新建”。"
        }</p>`;
    renderArtistMixer();
  }

  async function loadAnimaDexResources(options = {}) {
    if (quickTab === "snippets" || isFavoriteTab()) {
      renderQuickPicks();
      return;
    }
    const requestId = ++resourceRequestId;
    resourceSource = {
      ...resourceSource,
      status: "loading",
      label: "正在连接 AnimaDex",
    };
    renderQuickPicks();
    try {
      const params = new URLSearchParams({
        type: quickTab,
        q: resourceQuery,
        page: String(resourcePage),
        sort: resourceSort,
      });
      if (options.bustCache) params.set("_", String(Date.now()));
      const response = await fetch(`/api/animadex/resources?${params}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result = await response.json();
      if (requestId !== resourceRequestId) return;
      state.resources[quickTab] = result.items || [];
      resourcePage = result.page || resourcePage;
      resourcePages = result.pages || 1;
      resourceTotal = result.total || 0;
      const isBundledSample =
        !resourceQuery &&
        ((quickTab === "characters" && result.total === 20) ||
          (quickTab === "artists" && result.total === 10));
      resourceSource = {
        status: "connected",
        label: `${isBundledSample ? "AnimaDex 示例库" : "AnimaDex 已连接"} · ${
          result.total || 0
        } 项`,
        counts: {
          ...resourceSource.counts,
          [quickTab]: result.total || 0,
        },
      };
    } catch {
      if (requestId !== resourceRequestId) return;
      resourcePage = 1;
      resourcePages = 1;
      resourceTotal = (state.resources[quickTab] || []).length;
      resourceSource = {
        ...resourceSource,
        status: "fallback",
        label: "演示素材 · AnimaDex 未启动",
      };
    }
    renderQuickPicks();
  }

  function renderTextProvider() {
    $$("[data-text-provider]").forEach((button) => {
      button.classList.toggle(
        "active",
        button.dataset.textProvider === state.settings.textProvider
      );
    });
    $$("[data-expansion-level]").forEach((button) => {
      button.classList.toggle(
        "active",
        button.dataset.expansionLevel === state.settings.expansionLevel
      );
    });
    const modelName =
      state.settings.textProvider === "local"
        ? state.settings.localTextModel || "未配置模型"
        : state.settings.apiTextModel || "未配置模型";
    $("#textProviderLabel").textContent =
      state.settings.textProvider === "local"
        ? `本地 LLM · ${modelName}`
        : `外部 API · ${modelName}`;
    const expandButton = $("#expandBtn");
    if (expandButton) {
      expandButton.disabled = state.textGenerating || state.textDecomposing;
      expandButton.textContent = state.textGenerating
        ? "正在拓展..."
        : "拓展并结构化";
    }
    const decomposeButton = $("#decomposeBtn");
    if (decomposeButton) {
      decomposeButton.disabled =
        state.textGenerating || state.textDecomposing;
      decomposeButton.textContent = state.textDecomposing
        ? "正在拆解..."
        : "仅拆解";
    }
    const randomButton = $("#randomBtn");
    if (randomButton) {
      randomButton.disabled = state.textGenerating || state.textDecomposing;
      randomButton.textContent =
        state.textGenerating && state.generationProgress.task === "random"
          ? "正在生成完整随机画面..."
          : "生成随机提示词";
    }
    renderGenerationProgress();
  }

  function formatProgressElapsed(seconds) {
    const value = Math.max(0, Number(seconds) || 0);
    return value < 10 ? `${value.toFixed(1)} 秒` : `${Math.round(value)} 秒`;
  }

  function renderGenerationProgress() {
    const panel = $("#generationProgress");
    if (!panel) return;
    const progress = state.generationProgress || {};
    let elapsedSeconds = progress.elapsedSeconds || 0;
    if (progress.status === "running" && progress.startedAt) {
      elapsedSeconds = (Date.now() - progress.startedAt) / 1000;
    }
    panel.dataset.status = progress.status || "idle";
    $("#generationProgressTitle").textContent =
      progress.title || "等待任务";
    $("#generationProgressDetail").textContent =
      progress.detail || "选择任务后显示详细进度。";
    $("#generationProgressProvider").textContent =
      progress.provider || "尚未调用模型";
    $("#generationProgressElapsed").textContent =
      progress.status === "idle"
        ? "--"
        : formatProgressElapsed(elapsedSeconds);
    const cancelButton = $("#cancelGenerationBtn");
    if (cancelButton) {
      cancelButton.classList.toggle(
        "hidden",
        progress.status !== "running"
      );
    }

    if (progress.status === "running" && !generationProgressTimer) {
      generationProgressTimer = window.setInterval(
        renderGenerationProgress,
        250
      );
    } else if (progress.status !== "running" && generationProgressTimer) {
      window.clearInterval(generationProgressTimer);
      generationProgressTimer = null;
    }
  }

  function renderAnalyzers() {
    $("#analyzerList").innerHTML = Object.values(state.analyzers)
      .map(
        (model) => `
          <label class="analyzer-card">
            <input
              type="checkbox"
              data-analyzer="${escapeHtml(model.id)}"
              ${model.selected ? "checked" : ""}
              ${
                model.status === "running" || model.available === false
                  ? "disabled"
                  : ""
              }
            />
            <span>
              <strong>${escapeHtml(model.name)}</strong>
              <span>${escapeHtml(model.error || model.detail)} · ${escapeHtml(model.speed)}</span>
            </span>
            <b class="model-status ${statusClass(model.status)}">${statusLabel(model.status)}</b>
          </label>
        `
      )
      .join("");

    $("#analysisNotice").textContent = state.analysisNotice;
    $("#queueLine").innerHTML = Object.values(state.analyzers)
      .filter((model) => model.selected)
      .map(
        (model) =>
          `<span class="queue-step ${statusClass(model.status)}" title="${escapeHtml(model.name)}: ${statusLabel(model.status)}"></span>`
      )
      .join("");
  }

  function formatFileSize(bytes) {
    const value = Number(bytes) || 0;
    if (!value) return "";
    if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }

  function renderImageUpload() {
    const dropZone = $("#dropZone");
    const preview = $("#imagePreview");
    const hasImage = state.imageLoaded && Boolean(imagePreviewUrl);
    dropZone.classList.toggle("has-image", hasImage);
    preview.classList.toggle("hidden", !hasImage);
    if (hasImage && preview.src !== imagePreviewUrl) {
      preview.src = imagePreviewUrl;
    } else if (!hasImage) {
      preview.removeAttribute("src");
    }
    $("#uploadTitle").textContent = state.imageLoaded
      ? state.imageName
      : "上传一张参考图片";
    $("#uploadDetail").textContent = state.imageLoaded
      ? `${formatFileSize(state.imageSize)} · 点击可重新选择 · 原图仅在本机处理`
      : "PNG、JPG、WEBP · 原图仅在本机处理";
    const analyzeButton = $("#analyzeBtn");
    const hasSelectedAnalyzer = Object.values(state.analyzers).some(
      (model) => model.selected && model.available !== false
    );
    analyzeButton.disabled =
      !state.imageLoaded ||
      !hasSelectedAnalyzer ||
      state.analysisQueue.length > 0;
    analyzeButton.textContent = state.analysisQueue.length
      ? "正在真实识图..."
      : "开始多模型分析";
    const cancelAnalysisButton = $("#cancelAnalysisBtn");
    if (cancelAnalysisButton) {
      cancelAnalysisButton.classList.toggle(
        "hidden",
        state.analysisQueue.length === 0
      );
    }
  }

  function loadImageFile(file) {
    if (!app.isSupportedImageFile(file)) {
      state.toast = "请选择 PNG、JPG 或 WEBP 图片";
      renderToast();
      return false;
    }
    releaseImagePreview();
    uploadedImageFile = file;
    imagePreviewUrl = URL.createObjectURL(file);
    dispatch({
      type: "SET_IMAGE",
      name: file.name,
      mimeType: file.type,
      size: file.size,
    });
    return true;
  }

  function renderRawResults() {
    const completedModels = Object.values(state.analyzers).filter(
      (model) => model.status === "ready"
    );
    const tabs = [];
    if (state.analysisComplete) {
      tabs.push({ id: "merged", name: "合并结果" });
    }
    completedModels.forEach((model) => {
      tabs.push({ id: model.id, name: model.name });
    });
    $("#rawTabs").innerHTML = tabs
      .map(
        (tab) => `
          <button
            class="${state.activeRawResult === tab.id ? "active" : ""}"
            type="button"
            data-raw-result="${escapeHtml(tab.id)}"
          >${escapeHtml(tab.name)}</button>
        `
      )
      .join("");

    let raw = "完成分析后显示模型原始输出。";
    if (state.activeRawResult === "merged" && state.analysisComplete) {
      raw = state.rawMergedResult || "没有可用的合并结果。";
    } else if (state.analyzers[state.activeRawResult]) {
      raw = state.analyzers[state.activeRawResult].raw;
    }
    $("#rawOutput").textContent = raw;
  }

  function renderBlocks() {
    $("#editorEmpty").classList.toggle("hidden", state.blocks.length > 0);
    $("#blockList").classList.toggle("hidden", state.blocks.length === 0);
    const batchBar = $("#batchVariantBar");
    if (batchBar) {
      const selectedCount = state.selectedVariantBlockIds.length;
      batchBar.classList.toggle("hidden", state.blocks.length === 0);
      $("#batchVariantCount").textContent = `已选 ${selectedCount} 项`;
      const batchButton = $("#batchVariantBtn");
      batchButton.disabled =
        selectedCount < 2 ||
        state.batchRegenerating ||
        Boolean(state.regeneratingBlockId);
      batchButton.textContent = state.batchRegenerating
        ? "联合随机中..."
        : "联合随机";
      const matrixButton = $("#variantMatrixBtn");
      if (matrixButton) {
        matrixButton.disabled =
          selectedCount < 2 || state.batchRegenerating;
        matrixButton.textContent = state.batchRegenerating
          ? "生成中..."
          : "矩阵×3";
      }
      $("#clearVariantSelectionBtn").disabled =
        selectedCount === 0 || state.batchRegenerating;
    }
    $("#blockList").innerHTML = state.blocks
      .map((block) => renderBlockCard(block, collapsedBlocks))
      .join("");
  }

  function renderBlockCard(block, collapsed) {
    const blockId = escapeHtml(block.id);
    const variantDisabled =
      block.locked ||
      !RANDOM_VARIANT_BLOCKS.has(block.id) ||
      state.batchRegenerating
        ? "disabled"
        : "";
    const regenDisabled =
      block.locked ||
      !RANDOM_VARIANT_BLOCKS.has(block.id) ||
      Boolean(state.regeneratingBlockId) ||
      state.batchRegenerating
        ? "disabled"
        : "";
    const subtitle = collapsed
      ? `${escapeHtml(block.source)} · 置信度 ${escapeHtml(block.confidence)}%`
      : escapeHtml(block.hint);
    const weightArea = block.id === "negative"
      ? ""
      : collapsed
        ? `<span class="model-status ready">${block.weight}%</span>`
        : `
                  <label class="weight-control">
                    <span>权重</span>
                    <input
                      type="range"
                      min="0"
                      max="120"
                      value="${block.weight}"
                      data-block-weight="${blockId}"
                    />
                    <b>${block.weight}%</b>
                  </label>`;
    const variantCompare =
      !collapsed && block.pendingVariant
        ? `
                <div class="variant-compare">
                  <div class="variant-compare-copy">
                    <span>变体前内容</span>
                    <p>${escapeHtml(block.pendingVariant.previousEn)}</p>
                    <p>${escapeHtml(block.pendingVariant.previousZh)}</p>
                  </div>
                  <div class="variant-compare-actions">
                    <button type="button" data-keep-variant="${blockId}">保留新变体</button>
                    <button type="button" data-revert-variant="${blockId}">回退</button>
                  </div>
                </div>`
        : "";
    const fragment = app.compileBlockFragment(block);
    const fragmentPreview =
      !collapsed && block.id !== "negative" && fragment
        ? `
                <code class="block-fragment" title="该块编译进英文正向提示词的实际片段">${escapeHtml(fragment)}</code>`
        : "";
    const body = collapsed
      ? ""
      : `${variantCompare}
                <div class="block-body">
                  <label>
                    <span>English prompt</span>
                    <textarea
                      data-block-input="${blockId}"
                      data-language="en"
                      ${block.locked ? "readonly" : ""}
                    >${escapeHtml(block.en)}</textarea>
                  </label>
                  <label>
                    <span>中文释义</span>
                    <textarea
                      data-block-input="${blockId}"
                      data-language="zh"
                      ${block.locked ? "readonly" : ""}
                    >${escapeHtml(block.zh)}</textarea>
                  </label>
                </div>${fragmentPreview}
                <div class="block-foot">
                  <span>来源：${escapeHtml(block.source)}</span>
                  <span>置信度 ${escapeHtml(block.confidence)}%</span>
                  <button type="button" data-save-block="${blockId}">保存为资源</button>
                </div>`;
    return `
              <article class="prompt-block ${block.locked ? "locked" : ""} ${
                state.dirtyBlockIds.includes(block.id) ? "dirty" : ""
              }" data-block-card="${blockId}">
                <div class="block-head">
                  <label class="variant-select" title="加入联合随机">
                    <input
                      type="checkbox"
                      data-select-variant-block="${blockId}"
                      ${
                        state.selectedVariantBlockIds.includes(block.id)
                          ? "checked"
                          : ""
                      }
                      ${variantDisabled}
                    />
                    <span>选择</span>
                  </label>
                  <div class="block-title">
                    <strong>${escapeHtml(block.label)}</strong>
                    <span>${subtitle}</span>
                  </div>
                  ${weightArea}
                  <button
                    class="regen-button"
                    type="button"
                    data-regenerate-block="${blockId}"
                    title="基于其他结构块生成随机变体"
                    ${regenDisabled}
                  >${
                    state.regeneratingBlockId === block.id
                      ? "随机中..."
                      : "↻ 随机变体"
                  }</button>
                  <button class="lock-button" type="button" data-lock-block="${blockId}" title="锁定">
                    ${block.locked ? "锁" : "开"}
                  </button>
                </div>${body}
              </article>
            `;
  }

  function renderEditorStatus() {
    const hasVersion = state.version > 0;
    const previewing = Boolean(state.previewOutput);
    $("#versionBadge").textContent = previewing
      ? `${hasVersion ? `V${state.version}` : "未保存"} · 实时预览`
      : state.restoreFromVersion
        ? `V${state.version} · 基于 V${state.restoreFromVersion} 待保存`
        : state.hasUnsavedChanges
          ? hasVersion
            ? `V${state.version} · 待保存 V${state.version + 1}`
            : "待保存 V1"
          : hasVersion
            ? `V${state.version}`
            : "尚未生成";
    const pendingBar = $("#pendingBar");
    const dirtyCount = state.dirtyBlockIds.length;
    pendingBar.classList.toggle("hidden", dirtyCount === 0);
    $("#pendingSummary").textContent = dirtyCount
      ? `已修改 ${dirtyCount} 个结构块，右侧输出为实时预览；应用后进入待保存状态。`
      : "";
    renderVersionTimeline();
  }

  function renderVersionTimeline() {
    const container = $("#versionTimeline");
    if (!container) return;
    const history = state.versionHistory || [];
    container.classList.toggle("hidden", history.length < 2);
    if (history.length < 2) {
      container.innerHTML = "";
      return;
    }
    container.innerHTML = history
      .slice(-8)
      .map((entry) => {
        const isCurrent =
          entry.version === state.version && !state.hasUnsavedChanges;
        const isRestoreSource =
          state.hasUnsavedChanges && entry.version === state.restoreFromVersion;
        const label = entry.restoredFrom
          ? `V${entry.version}↩`
          : `V${entry.version}`;
        return `
          <button
            class="version-chip ${isCurrent ? "active" : ""} ${isRestoreSource ? "restoring" : ""}"
            type="button"
            data-restore-version="${entry.version}"
            title="${
              isCurrent
                ? "当前版本"
                : isRestoreSource
                  ? `当前工作区基于 V${entry.version}`
                  : `载入 V${entry.version}，保存后生成最新版本`
            }"
            ${isCurrent || isRestoreSource ? "disabled" : ""}
          >${label}</button>
        `;
      })
      .join("");
  }

  function renderOutput() {
    const output = state.previewOutput || state.output;
    for (const key of [
      "positiveEn",
      "positiveZh",
      "negativeEn",
      "negativeZh",
    ]) {
      const element = $(`#${key}`);
      if (element) element.value = output[key] || "";
    }
    $$("[data-output-lang]").forEach((button) => {
      button.classList.toggle(
        "active",
        button.dataset.outputLang === outputLanguage
      );
    });
    $$("[data-language]").forEach((section) => {
      section.classList.toggle(
        "hidden",
        outputLanguage !== "both" &&
          section.dataset.language !== outputLanguage
      );
    });
    const translateButton = $("#translatePendingBtn");
    if (translateButton) {
      translateButton.disabled = state.translatingPending;
      translateButton.textContent = state.translatingPending
        ? "翻译中..."
        : "本地 LLM 翻译";
    }
  }

  function renderDrawer() {
    const drawer = $("#modelDrawer");
    drawer.classList.toggle("open", state.drawerOpen);
    drawer.setAttribute("aria-hidden", String(!state.drawerOpen));

    $$("[data-setting-group]").forEach((group) => {
      const key = group.dataset.settingGroup;
      $$("[data-setting]", group).forEach((button) => {
        button.classList.toggle(
          "active",
          state.settings[key] === button.dataset.value
        );
      });
    });

    $$('input[name="residency"]').forEach((input) => {
      input.checked = input.value === state.settings.residency;
    });

    $$("[data-text-setting]").forEach((input) => {
      const key = input.dataset.textSetting;
      if (document.activeElement !== input) {
        input.value = state.settings[key] || "";
      }
      if (key.endsWith("Key")) {
        input.placeholder = state.settings[`${key}Configured`]
          ? "已配置；留空保持不变"
          : "仅保存在本机数据库";
      }
    });

    const skillEditor = $("#creativeDirectorSkillEditor");
    if (skillEditor && document.activeElement !== skillEditor) {
      skillEditor.value =
        state.settings.creativeDirectorSkillOverride || "";
    }
    updateCreativeDirectorSkillCount();

    $("#drawerModelList").innerHTML = Object.values(state.analyzers)
      .filter((model) => model.id !== "external")
      .map(
        (model) => `
          <div class="drawer-model-row">
            <span>
              <strong>${escapeHtml(model.name)}</strong>
              <span>${escapeHtml(model.device)} · ${escapeHtml(model.detail)}</span>
            </span>
            <b class="model-status ${statusClass(model.status)}">${statusLabel(model.status)}</b>
          </div>
        `
      )
      .join("");

    const loaded = Object.values(state.analyzers).filter((model) =>
      ["ready", "running", "waiting"].includes(model.status)
    ).length;
    $("#memoryEstimate").textContent = `预计显存 ${loaded ? (loaded * 2.4).toFixed(1) : "0"} GB`;
  }

  function updateCreativeDirectorSkillCount() {
    const editor = $("#creativeDirectorSkillEditor");
    const counter = $("#creativeDirectorSkillCount");
    if (!editor || !counter) return;
    counter.textContent = `${Array.from(editor.value).length} / 100000`;
  }

  function renderResourceDialog() {
    const dialog = $("#resourceDialog");
    dialog.classList.toggle("open", state.resourceDialogOpen);
    dialog.setAttribute("aria-hidden", String(!state.resourceDialogOpen));
  }

  function renderPromptTemplateDialog() {
    const dialog = $("#promptTemplateDialog");
    if (!dialog) return;
    dialog.classList.toggle("open", promptTemplateDialog.open);
    dialog.setAttribute("aria-hidden", String(!promptTemplateDialog.open));
    $("#promptTemplateKind").textContent =
      promptTemplateDialog.kind === "image" ? "IMAGE PROMPT TEMPLATE" : "TEXT PROMPT TEMPLATE";
    $("#promptTemplateTitle").textContent = promptTemplateDialog.label || "提示词模板";
    $("#promptTemplateFile").textContent = promptTemplateDialog.filename
      ? `prototype/prompts/${promptTemplateDialog.filename}`
      : "prototype/prompts/template.md";
    const picker = $("#promptTemplateSelect");
    if (picker && promptTemplateDialog.id.startsWith("text_")) {
      picker.value = promptTemplateDialog.id;
    }
    if (picker) {
      picker.parentElement.classList.toggle(
        "hidden",
        promptTemplateDialog.kind === "image"
      );
    }
    const editor = $("#promptTemplateContent");
    if (editor && editor.value !== promptTemplateDialog.content) {
      editor.value = promptTemplateDialog.content;
    }
  }

  function renderResourcePreview() {
    const dialog = $("#resourcePreviewDialog");
    const resource = previewResourceId
      ? findQuickResource(previewResourceId)
      : null;
    dialog.classList.toggle("open", Boolean(resource));
    dialog.setAttribute("aria-hidden", String(!resource));
    if (!resource) return;
    $("#resourcePreviewSource").textContent = resource.source || "LOCAL RESOURCE";
    $("#resourcePreviewTitle").textContent = resource.name;
    $("#resourcePreviewMeta").textContent = resource.meta || "";
    $("#resourcePreviewEn").value = resource.en || "";
    $("#resourcePreviewZh").value = resource.zh || "等待本地 LLM 生成中文释义";
    const image = $("#resourcePreviewImage");
    image.src = resource.thumbUrl || "";
    image.alt = resource.name;
    image.parentElement.classList.toggle("hidden", !resource.thumbUrl);
  }

  function renderToast() {
    const toast = $("#toast");
    toast.textContent = state.toast;
    toast.classList.toggle("show", Boolean(state.toast));
    if (state.toast) {
      window.clearTimeout(renderToast.timer);
      renderToast.timer = window.setTimeout(
        () => dispatch({ type: "CLEAR_TOAST" }),
        1800
      );
    }
  }

  function render() {
    renderNavigation();
    renderViews();
    renderDirector();
    renderProjects();
    renderProjectContext();
    renderTextModes();
    renderTextProvider();
    renderReferences();
    renderQuickPicks();
    renderAnalyzers();
    renderImageUpload();
    renderRawResults();
    renderBlocks();
    renderVariantMatrix();
    renderEditorStatus();
    renderOutput();
    renderRecipeConsole();
    renderDrawer();
    renderResourceDialog();
    renderPromptTemplateDialog();
    renderResourcePreview();
    renderToast();
  }

  function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const result = String(reader.result || "");
        resolve(result.includes(",") ? result.split(",", 2)[1] : result);
      };
      reader.onerror = () => reject(new Error("无法读取本地图片"));
      reader.readAsDataURL(file);
    });
  }

  async function startImageAnalysis() {
    if (!uploadedImageFile) {
      state.toast = "请先载入一张图片";
      renderToast();
      return;
    }
    if (state.analysisQueue.length) return;
    dispatch({ type: "START_ANALYSIS" });
    const analyzerIds = Object.values(state.analyzers)
      .filter((model) => model.selected && model.available !== false)
      .map((model) => model.id);
    if (!analyzerIds.length) return;
    const request = beginWorkspaceRequest();
    activeVisionAbort = request.controller;
    try {
      const dataBase64 = await fileToBase64(uploadedImageFile);
      if (
        discardChangedWorkspaceResult(
          request,
          "IMAGE_ANALYSIS_FAILED",
          "请求期间图片或项目已修改，已停止旧图片分析"
        )
      ) return;
      if (request.controller.signal.aborted) {
        const abortError = new Error("图片分析已取消");
        abortError.name = "AbortError";
        throw abortError;
      }
      const result = await apiJson("/api/vision/analyze", {
        method: "POST",
        signal: request.controller.signal,
        body: JSON.stringify({
          filename: uploadedImageFile.name,
          mimeType: uploadedImageFile.type,
          dataBase64,
          analyzerIds,
        }),
      });
      if (
        discardChangedWorkspaceResult(
          request,
          "IMAGE_ANALYSIS_FAILED",
          "请求期间图片或项目已修改，已丢弃旧分析结果"
        )
      ) return;
      dispatch({ type: "APPLY_IMAGE_ANALYSIS", item: result.item });
    } catch (error) {
      if (
        discardChangedWorkspaceResult(
          request,
          "IMAGE_ANALYSIS_FAILED",
          "请求期间图片或项目已修改，已停止旧图片分析"
        )
      ) return;
      dispatch({
        type: "IMAGE_ANALYSIS_FAILED",
        error: isAbortError(error)
          ? "已取消图片分析"
          : error.message || "图片分析失败",
      });
    } finally {
      finishWorkspaceRequest(request);
      if (activeVisionAbort === request.controller) activeVisionAbort = null;
    }
  }

  function renderRecipeConsole() {
    const parameters = state.generationParameters || {};
    const modelSelect = $("#recipeModelProfile");
    if (modelSelect) {
      const profiles = state.modelProfiles || [];
      modelSelect.innerHTML = profiles.length
        ? profiles
            .map(
              (profile) =>
                `<option value="${escapeHtml(profile.profileId)}">${escapeHtml(profile.displayName)}</option>`
            )
            .join("")
        : '<option value="anima-1.1-v1">MiaoMiao Harem Anima_1.1</option>';
      modelSelect.value = state.modelProfileId;
    }
    const selectedProfile = (state.modelProfiles || []).find(
      (item) => item.profileId === state.modelProfileId
    );
    const resolutionPreset = $("#recipeResolutionPreset");
    if (resolutionPreset) {
      resolutionPreset.innerHTML = [
        '<option value="">手动分辨率（不自动选择候选）</option>',
        ...(selectedProfile?.candidateResolutionPresets || []).map(
          (preset) =>
            `<option value="${preset.width}x${preset.height}">${escapeHtml(preset.label)} · 候选，待验证</option>`
        ),
      ].join("");
      resolutionPreset.value = "";
    }
    const bindings = {
      recipeSampler: ["sampler", parameters.sampler],
      recipeScheduler: ["scheduler", parameters.scheduler],
      recipeSteps: ["steps", parameters.steps],
      recipeCfg: ["cfg", parameters.cfg],
      generationSeed: ["generationSeed", parameters.generationSeed],
      recipeDenoise: ["denoiseStrength", parameters.denoiseStrength ?? ""],
    };
    for (const [id, [key, value]] of Object.entries(bindings)) {
      const input = $(`#${id}`);
      if (!input) continue;
      if (document.activeElement !== input) input.value = value ?? "";
      const manual = state.manualParameterKeys.includes(key);
      input.dataset.source = manual ? "manual_override" : "model_default";
      input.title = `来源：${manual ? "手动覆盖" : key === "generationSeed" ? "任务预设" : "模型推荐"}`;
      input.closest("label")?.classList.toggle("manual-override", manual);
    }
    for (const [id, axis] of [["recipeWidth", "width"], ["recipeHeight", "height"]]) {
      const input = $(`#${id}`);
      if (input && document.activeElement !== input) {
        input.value = parameters.resolution?.[axis] || 1024;
      }
      input?.closest("label")?.classList.toggle(
        "manual-override",
        state.manualParameterKeys.includes("resolution")
      );
    }
    const seedInput = $("#librarySeed");
    if (
      seedInput &&
      document.activeElement !== seedInput &&
      state.randomPlan?.librarySeed
    ) {
      seedInput.value = state.randomPlan.librarySeed;
    }
    const status = $("#wordlistStatus");
    if (status && state.randomCatalog) {
      const count = (state.randomCatalog.categories || []).reduce(
        (sum, category) => sum + Number(category.entryCount || 0),
        0
      );
      status.textContent = `${count} 条 · ${state.randomCatalog.version} · ${state.randomCatalog.runtimeReady ? "可发布" : "实验"} · ${state.randomCatalog.semanticReviewRequired ? "待人工语义审核" : "已审核"}`;
    }
    const panel = $("#editPreviewPanel");
    const preview = state.pendingEditPreview;
    if (panel) {
      panel.classList.toggle("hidden", !preview);
      if (preview?.loading) {
        panel.textContent = "AI 正在阅读完整提示词并生成必要修改…";
      } else if (preview) {
        const conflicts = (preview.conflicts || [])
          .map((item) => `<li>冲突：${escapeHtml(item.message || item.code)}</li>`)
          .join("");
        const unresolved = (preview.parse?.unresolved || [])
          .map((item) => `<li>未解析：${escapeHtml(item.message || item.text || item.code)}</li>`)
          .join("");
        const diffs = (preview.diffs || [])
          .map(
            (item) => `<li class="ai-edit-diff">
              <strong>${escapeHtml(item.label || item.id)}</strong>
              <span><b>修改前</b>${escapeHtml(item.before?.zh || "（空）")}</span>
              <span><b>修改后</b>${escapeHtml(item.after?.zh || "（空）")}</span>
              <em>${escapeHtml(item.reason || "AI 未说明原因")}</em>
            </li>`
          )
          .join("");
        const affectedCount = (preview.affectedIds || []).length;
        const unchangedCount = (preview.lockedIds || []).length;
        panel.innerHTML = `
          <strong>${preview.ready ? "可确认" : "尚不可确认"}</strong>
          <span>将修改 ${affectedCount} 个结构块，其余 ${unchangedCount} 个保持不变</span>
          <ul>${diffs}${conflicts}${unresolved}</ul>
        `;
      }
    }
    const applyButton = $('[data-action="apply-edit"]');
    if (applyButton) applyButton.disabled = !preview?.ready;
  }

  async function copyText(text) {
    if (!text) {
      state.toast = "当前没有可复制内容";
      renderToast();
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      state.toast = "已复制到剪贴板";
    } catch {
      state.toast = "浏览器未允许剪贴板访问";
    }
    renderToast();
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;

    if (button.id === "creativeDirectorSkillSave") {
      const editor = $("#creativeDirectorSkillEditor");
      if (!editor) return;
      try {
        state = app.reduceState(state, {
          type: "SET_SETTING",
          key: "creativeDirectorSkillOverride",
          value: app.normalizeCreativeDirectorSkillOverride(editor.value),
        });
        state.toast = state.settings.creativeDirectorSkillOverride
          ? "创作总监 Skill 覆盖已保存。"
          : "已使用内置默认 Skill。";
        saveSettings();
        render();
      } catch (error) {
        state.toast = error.message;
        renderToast();
      }
      return;
    }
    if (button.id === "creativeDirectorSkillReset") {
      if (
        !window.confirm(
          "确定恢复内置默认 Skill？当前自定义覆盖将被清空。"
        )
      ) {
        return;
      }
      state = app.reduceState(state, {
        type: "SET_SETTING",
        key: "creativeDirectorSkillOverride",
        value: "",
      });
      state.toast = "已恢复内置默认 Skill。";
      saveSettings();
      render();
      return;
    }

    if (button.dataset.openProject) {
      openProject(button.dataset.openProject);
      return;
    }
    if (button.dataset.directorDirection) {
      transitionCreativeIntake({
        type: "select_direction",
        directionId: button.dataset.directorDirection,
      });
      return;
    }
    if (button.dataset.directorReviseItem) {
      const item = state.creativeIntake.brief?.items.find(
        (candidate) => candidate.id === button.dataset.directorReviseItem
      );
      if (!item) return;
      sendCreativeDirectorMessage(app.buildBriefRevisionMessage(item));
      return;
    }
    if (button.dataset.directorImageUse) {
      const card = button.closest("[data-director-image-id]");
      const imageId = card?.dataset.directorImageId || "";
      const reference = state.creativeIntake.inputs.images.find(
        (item) => item.id === imageId
      );
      if (!reference) return;
      const uses = new Set(reference.requestedUses || []);
      if (uses.has(button.dataset.directorImageUse)) {
        uses.delete(button.dataset.directorImageUse);
      } else {
        uses.add(button.dataset.directorImageUse);
      }
      setDirectorImageUses(
        imageId,
        app.DIRECTOR_IMAGE_REQUESTED_USES
          .map((item) => item.id)
          .filter((id) => uses.has(id))
      );
      return;
    }
    if (button.dataset.directorImageAction) {
      const card = button.closest("[data-director-image-id]");
      const imageId = card?.dataset.directorImageId || "";
      if (!imageId) return;
      if (button.dataset.directorImageAction === "suggest") {
        setDirectorImageUses(imageId, []);
      } else if (button.dataset.directorImageAction === "remove") {
        removeDirectorImage(imageId);
      } else if (button.dataset.directorImageAction === "retry") {
        ensureDirectorImageEvidence({
          force: true,
          retryImageIds: [imageId],
        });
      } else if (button.dataset.directorImageAction === "replace") {
        const input = $("#directorImageInput");
        input.dataset.replaceId = imageId;
        input.click();
      }
      return;
    }
    if (button.id === "directorContinueBtn") {
      state = app.reduceState(state, {
        type: "ENTER_CREATIVE_WORKBENCH",
      });
      render();
      if (state.hasUnsavedProjectChanges) saveCurrentProject();
      return;
    }
    if (button.dataset.nav) {
      if (button.dataset.nav === "home") startNewProject();
      else dispatch({ type: "NAVIGATE", view: button.dataset.nav });
      return;
    }
    if (button.dataset.action === "retry-projects") {
      loadProjects();
      return;
    }
    if (button.dataset.textMode) {
      dispatch({ type: "SET_TEXT_MODE", mode: button.dataset.textMode });
      return;
    }
    if (button.dataset.textProvider) {
      dispatch({
        type: "SET_TEXT_PROVIDER",
        value: button.dataset.textProvider,
      });
      saveSettings();
      return;
    }
    if (button.dataset.expansionLevel) {
      dispatch({
        type: "SET_SETTING",
        key: "expansionLevel",
        value: button.dataset.expansionLevel,
      });
      renderTextProvider();
      saveSettings();
      return;
    }
    if (button.dataset.importReference) {
      dispatch({
        type: "IMPORT_REFERENCE",
        id: button.dataset.importReference,
      });
      return;
    }
    if (button.dataset.quickTab) {
      quickTab = button.dataset.quickTab;
      resourceQuery = "";
      resourcePage = 1;
      if (isFavoriteTab()) {
        loadFavoriteResources({ migrateLocal: false });
        resourcePages = 1;
        resourceTotal = favoriteResourcesForTab().length;
      }
      if (quickTab !== "artists" && resourceSort === "score") resourceSort = "count";
      renderQuickPicks();
      loadAnimaDexResources();
      return;
    }
    if (button.dataset.resourceSort) {
      resourceSort = button.dataset.resourceSort;
      resourcePage = 1;
      loadAnimaDexResources({ bustCache: true });
      return;
    }
    if (button.dataset.resourcePage) {
      if (button.dataset.resourcePage === "prev" && resourcePage > 1) {
        resourcePage -= 1;
      } else if (
        button.dataset.resourcePage === "next" &&
        resourcePage < resourcePages
      ) {
        resourcePage += 1;
      }
      loadAnimaDexResources({ bustCache: true });
      return;
    }
    if (button.dataset.previewResource) {
      previewResourceId = button.dataset.previewResource;
      renderResourcePreview();
      return;
    }
    if (button.dataset.favoriteResource) {
      toggleFavoriteResource(button.dataset.favoriteResource);
      return;
    }
    if (button.dataset.quickPick) {
      applyQuickResource(button.dataset.quickPick);
      return;
    }
    if (button.dataset.rawResult) {
      dispatch({ type: "SET_RAW_RESULT", id: button.dataset.rawResult });
      return;
    }
    if (button.dataset.pickMatrix !== undefined && variantMatrix) {
      const candidate =
        variantMatrix.candidates[Number(button.dataset.pickMatrix)];
      if (candidate) {
        dispatch({
          type: "APPLY_BLOCKS_VARIANT",
          item: candidate,
          blockIds: variantMatrix.blockIds,
        });
      }
      variantMatrix = null;
      renderVariantMatrix();
      return;
    }
    if (button.dataset.dismissMatrix) {
      variantMatrix = null;
      renderVariantMatrix();
      return;
    }
    if (button.id === "applyArtistMixBtn") {
      applyArtistMix();
      return;
    }
    if (button.dataset.keepVariant) {
      dispatch({ type: "KEEP_BLOCK_VARIANT", id: button.dataset.keepVariant });
      return;
    }
    if (button.dataset.revertVariant) {
      dispatch({
        type: "REVERT_BLOCK_VARIANT",
        id: button.dataset.revertVariant,
      });
      return;
    }
    if (button.dataset.restoreVersion) {
      const hasUnsavedWorkspace =
        state.dirtyBlockIds.length ||
        state.hasUnsavedChanges ||
        state.hasUnsavedProjectChanges;
      if (
        hasUnsavedWorkspace &&
        !window.confirm("载入历史版本会放弃当前未保存修改，是否继续？")
      ) {
        state.toast = "已保留当前未保存修改";
        renderToast();
        return;
      }
      dispatch({
        type: "RESTORE_VERSION",
        version: Number(button.dataset.restoreVersion),
        confirmed: true,
      });
      return;
    }
    if (button.dataset.lockBlock) {
      dispatch({ type: "TOGGLE_BLOCK_LOCK", id: button.dataset.lockBlock });
      return;
    }
    if (button.dataset.regenerateBlock) {
      regeneratePromptBlock(button.dataset.regenerateBlock);
      return;
    }
    if (button.dataset.saveBlock) {
      dispatch({
        type: "SAVE_BLOCK_AS_RESOURCE",
        id: button.dataset.saveBlock,
      });
      quickTab = "snippets";
      renderQuickPicks();
      return;
    }
    if (button.dataset.outputLang) {
      outputLanguage = button.dataset.outputLang;
      renderOutput();
      return;
    }
    if (button.dataset.copy) {
      const output = state.previewOutput || state.output;
      copyText(output[button.dataset.copy]);
      return;
    }
    if (button.dataset.setting) {
      dispatch({
        type: "SET_SETTING",
        key: button.dataset.setting,
        value: button.dataset.value,
      });
      saveSettings();
      return;
    }

    const action = button.dataset.action;
    if (action === "director-confirm-brief") {
      transitionCreativeIntake({ type: "confirm_brief" });
      return;
    } else if (action === "director-reopen-brief") {
      transitionCreativeIntake({ type: "reopen_brief" });
      return;
    } else if (action === "cancel-generation") {
      if (activeTextAbort) activeTextAbort.abort();
      return;
    }
    if (action === "cancel-analysis") {
      if (activeVisionAbort) activeVisionAbort.abort();
      return;
    }
    if (action === "open-models") {
      dispatch({ type: "TOGGLE_DRAWER", open: true });
      loadLocalLlmStatus();
    } else if (action === "close-models") {
      dispatch({ type: "TOGGLE_DRAWER", open: false });
    } else if (action === "release-models") {
      dispatch({ type: "RELEASE_MODELS" });
    } else if (action === "start-local-llm") {
      controlLocalLlm("start");
    } else if (action === "stop-local-llm") {
      controlLocalLlm("stop");
    } else if (action === "test-local-llm") {
      testTextProvider("local");
    } else if (action === "test-external-api") {
      testTextProvider("api");
    } else if (action === "export-all") {
      downloadBackup("full");
    } else if (action === "export-project") {
      downloadBackup("project");
    } else if (action === "select-backup") {
      $("#backupFileInput")?.click();
    } else if (action === "open-reference") {
      dispatch({ type: "SET_TEXT_MODE", mode: "reference" });
    } else if (action === "new-resource") {
      dispatch({ type: "TOGGLE_RESOURCE_DIALOG", open: true });
    } else if (action === "close-resource") {
      dispatch({ type: "TOGGLE_RESOURCE_DIALOG", open: false });
    } else if (action === "close-resource-preview") {
      previewResourceId = "";
      renderResourcePreview();
    } else if (action === "edit-prompt-template") {
      openPromptTemplate(button.dataset.template);
    } else if (action === "close-prompt-template") {
      promptTemplateDialog.open = false;
      renderPromptTemplateDialog();
    } else if (action === "revert-prompt-template") {
      promptTemplateDialog.content = promptTemplateDialog.original;
      renderPromptTemplateDialog();
    } else if (action === "save-prompt-template") {
      savePromptTemplate();
    } else if (action === "apply-preview-resource") {
      const resourceId = previewResourceId;
      previewResourceId = "";
      if (resourceId) dispatch({ type: "APPLY_RESOURCE", id: resourceId });
    } else if (action === "discard-changes") {
      dispatch({ type: "DISCARD_CHANGES" });
    } else if (action === "apply-changes") {
      dispatch({ type: "APPLY_CHANGES" });
    } else if (action === "clear-variant-selection") {
      dispatch({ type: "CLEAR_VARIANT_SELECTION" });
    } else if (action === "save-recipe") {
      saveCurrentRecipe();
    } else if (action === "variant-matrix") {
      generateVariantMatrix();
    } else if (action === "regenerate-selected-blocks") {
      regeneratePromptBlocks();
    } else if (action === "quick-resource") {
      dispatch({ type: "SET_TEXT_MODE", mode: "expand" });
      quickTab = button.dataset.resource === "artist" ? "artists" : "characters";
      resourcePage = 1;
      if (quickTab !== "artists" && resourceSort === "score") resourceSort = "count";
      state.toast =
        button.dataset.resource === "artist" ? "已打开画师快速选择" : "已打开角色快速选择";
      render();
      loadAnimaDexResources();
    } else if (action === "refresh-resources") {
      resourcePage = 1;
      loadAnimaDexResources({ bustCache: true });
    } else if (action === "collapse-blocks") {
      collapsedBlocks = !collapsedBlocks;
      button.textContent = collapsedBlocks ? "展开全部" : "收起全部";
      renderBlocks();
    } else if (action === "refresh-output") {
      state.toast = "双语释义已刷新";
      renderToast();
    } else if (action === "translate-pending") {
      translatePendingBlocks();
    } else if (action === "wordlist-plan") {
      generateWordlistPlan();
    } else if (action === "preview-edit") {
      previewChineseEdit();
    } else if (action === "restore-model-defaults") {
      dispatch({ type: "RESTORE_MODEL_DEFAULTS" });
    } else if (action === "apply-edit") {
      applyChineseEdit();
    } else if (action === "dismiss-edit") {
      editPreviewRecipe = null;
      editPreviewPayload = null;
      editPreviewRevision = null;
      dispatch({ type: "DISMISS_EDIT_PREVIEW" });
    } else if (action === "undo-recipe") {
      undoLastRecipeChange();
    } else if (action === "copy-all") {
      copyText(app.getCombinedPrompt(state));
    } else if (action === "save-draft") {
      saveCurrentProject();
    }
  });

  document.addEventListener("input", (event) => {
    if (
      event.target.dataset.generationParam &&
      event.target.tagName !== "SELECT"
    ) {
      state = app.reduceState(state, {
        type: "SET_GENERATION_PARAMETER",
        key: event.target.dataset.generationParam,
        value: event.target.value,
      });
      renderProjectContext();
      renderRecipeConsole();
    } else if (event.target.dataset.resolutionAxis) {
      const width = Number($("#recipeWidth")?.value);
      const height = Number($("#recipeHeight")?.value);
      state = app.reduceState(state, {
        type: "SET_GENERATION_PARAMETER",
        key: "resolution",
        value: { width, height },
      });
      renderProjectContext();
      renderRecipeConsole();
    } else if (event.target.id === "projectNameInput") {
      dispatch({ type: "SET_PROJECT_NAME", value: event.target.value });
    } else if (event.target.id === "draftInput") {
      dispatch({ type: "SET_DRAFT", value: event.target.value });
    } else if (event.target.id === "referenceSearch") {
      renderReferences();
    } else if (event.target.id === "resourceSearch") {
      resourceQuery = event.target.value.trim();
      resourcePage = 1;
      window.clearTimeout(resourceSearchTimer);
      resourceSearchTimer = window.setTimeout(() => {
        if (isFavoriteTab()) renderQuickPicks();
        else loadAnimaDexResources();
      }, 260);
    } else if (event.target.id === "promptTemplateContent") {
      promptTemplateDialog.content = event.target.value;
    } else if (event.target.id === "creativeDirectorSkillEditor") {
      updateCreativeDirectorSkillCount();
    } else if (event.target.dataset.mixWeight) {
      const id = event.target.dataset.mixWeight;
      if (artistMix.has(id)) {
        artistMix.set(id, Number(event.target.value));
        const label = event.target.closest(".artist-mix-weight");
        if (label) label.querySelector("b").textContent = `${event.target.value}%`;
        refreshArtistMixPreview();
      }
    } else if (event.target.dataset.blockWeight) {
      state = app.reduceState(state, {
        type: "SET_BLOCK_WEIGHT",
        id: event.target.dataset.blockWeight,
        value: event.target.value,
      });
      const control = event.target.closest(".weight-control");
      if (control) control.querySelector("b").textContent = `${event.target.value}%`;
      event.target.closest(".prompt-block")?.classList.add("dirty");
      renderEditorStatus();
      renderOutput();
    } else if (event.target.dataset.blockInput) {
      state = app.reduceState(state, {
        type: "UPDATE_BLOCK",
        id: event.target.dataset.blockInput,
        language: event.target.dataset.language,
        value: event.target.value,
      });
      event.target.closest(".prompt-block")?.classList.add("dirty");
      renderEditorStatus();
      renderOutput();
      updateTagSuggest(event.target);
    }
  });

  document.addEventListener(
    "blur",
    (event) => {
      if (event.target === tagSuggest.target) hideTagSuggest();
    },
    true
  );

  document.addEventListener("keydown", (event) => {
    if (tagSuggestOpen() && event.target === tagSuggest.target) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        moveTagSuggest(1);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        moveTagSuggest(-1);
        return;
      }
      if (event.key === "Tab" || (event.key === "Enter" && !event.ctrlKey && !event.metaKey)) {
        event.preventDefault();
        applyTagSuggestion();
        return;
      }
      if (event.key === "Escape") {
        hideTagSuggest();
        return;
      }
    }
    const modifier = event.ctrlKey || event.metaKey;
    if (event.key === "Escape") {
      if (state.drawerOpen) {
        dispatch({ type: "TOGGLE_DRAWER", open: false });
        return;
      }
      if (state.resourceDialogOpen) {
        dispatch({ type: "TOGGLE_RESOURCE_DIALOG", open: false });
        return;
      }
      return;
    }
    if (!modifier) return;
    if (event.key === "Enter" && event.shiftKey) {
      if (state.view === "text" && state.dirtyBlockIds.length) {
        event.preventDefault();
        dispatch({ type: "APPLY_CHANGES" });
      }
      return;
    }
    if (event.key === "Enter") {
      if (state.view === "text" && !state.textGenerating && !state.textDecomposing) {
        event.preventDefault();
        expandTextPrompt();
      }
      return;
    }
    if ((event.key === "s" || event.key === "S") && !event.shiftKey) {
      if (state.view !== "home") {
        event.preventDefault();
        saveCurrentProject();
      }
    }
  });

  document.addEventListener("change", (event) => {
    if (event.target.id === "directorModelSelect") {
      if (event.target.value) {
        transitionCreativeIntake({
          type: "select_model",
          modelProfileId: event.target.value,
        });
      }
      return;
    }
    if (event.target.name === "directorProvider") {
      state = app.reduceState(state, {
        type: "SET_TEXT_PROVIDER",
        value: event.target.value,
      });
      render();
      saveSettings();
      return;
    }
    if (event.target.id === "recipeModelProfile") {
      dispatch({
        type: "SELECT_MODEL_PROFILE",
        profileId: event.target.value,
      });
      return;
    }
    if (event.target.id === "recipeResolutionPreset") {
      const match = /^(\d+)x(\d+)$/.exec(event.target.value);
      if (match) {
        dispatch({
          type: "SET_GENERATION_PARAMETER",
          key: "resolution",
          value: { width: Number(match[1]), height: Number(match[2]) },
        });
      }
      return;
    }
    if (
      event.target.dataset.generationParam &&
      event.target.tagName === "SELECT"
    ) {
      state = app.reduceState(state, {
        type: "SET_GENERATION_PARAMETER",
        key: event.target.dataset.generationParam,
        value: event.target.value,
      });
      renderProjectContext();
      renderRecipeConsole();
      return;
    }
    if (event.target.dataset.mixArtist) {
      const id = event.target.dataset.mixArtist;
      if (event.target.checked) artistMix.set(id, artistMix.get(id) ?? 100);
      else artistMix.delete(id);
      renderArtistMixer();
      return;
    }
    if (event.target.dataset.selectVariantBlock) {
      dispatch({
        type: "TOGGLE_VARIANT_SELECTION",
        id: event.target.dataset.selectVariantBlock,
      });
    } else if (event.target.dataset.analyzer) {
      dispatch({
        type: "TOGGLE_ANALYZER",
        id: event.target.dataset.analyzer,
      });
    } else if (event.target.name === "residency") {
      dispatch({
        type: "SET_SETTING",
        key: "residency",
        value: event.target.value,
      });
      saveSettings();
    } else if (event.target.id === "autoCombineToggle") {
      dispatch({
        type: "SET_SETTING",
        key: "autoCombine",
        value: event.target.checked,
      });
      saveSettings();
    } else if (event.target.dataset.textSetting) {
      dispatch({
        type: "SET_SETTING",
        key: event.target.dataset.textSetting,
        value: event.target.value.trim(),
      });
      saveSettings();
    } else if (event.target.id === "promptTemplateSelect") {
      openPromptTemplate(event.target.value);
    } else if (event.target.id === "imageInput") {
      const file = event.target.files?.[0];
      if (file) loadImageFile(file);
      event.target.value = "";
    } else if (event.target.id === "directorImageInput") {
      const files = Array.from(event.target.files || []);
      if (files.length) {
        attachDirectorImageFiles(files, {
          replaceId: event.target.dataset.replaceId || "",
        });
      }
      delete event.target.dataset.replaceId;
      event.target.value = "";
    } else if (event.target.id === "backupFileInput") {
      const file = event.target.files?.[0];
      if (file) inspectAndStageBackup(file);
    }
  });

  window.addEventListener("beforeunload", (event) => {
    if (!saveInFlight && !app.shouldConfirmWorkspaceDiscard(state)) return;
    event.preventDefault();
    event.returnValue = "";
  });
  window.addEventListener("pagehide", () => {
    directorImageCollectionController.clear();
  });

  $("#expandBtn").addEventListener("click", expandTextPrompt);
  $("#decomposeBtn").addEventListener("click", decomposeTextPrompt);
  $("#randomBtn").addEventListener("click", randomizeTextPrompt);
  $("#dropZone").addEventListener("click", () => $("#imageInput").click());
  $("#analyzeBtn").addEventListener("click", startImageAnalysis);

  $(".director-composer")?.addEventListener("submit", (event) => {
    event.preventDefault();
    sendCreativeDirectorMessage();
  });
  $(
    '.director-composer button[aria-label="停止当前请求"]'
  )?.addEventListener("click", () => {
    activeDirectorAbort?.abort();
  });

  const dropZone = $("#dropZone");
  dropZone.addEventListener("dragover", (event) => event.preventDefault());
  dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    const file = event.dataTransfer.files?.[0];
    if (file) loadImageFile(file);
  });

  const directorImageDropZone = $("#directorImageDropZone");
  directorImageDropZone?.addEventListener("click", () => {
    if (directorImageDropZone.getAttribute("aria-disabled") === "true") {
      return;
    }
    const input = $("#directorImageInput");
    delete input.dataset.replaceId;
    input.click();
  });
  directorImageDropZone?.addEventListener("keydown", (event) => {
    if (!["Enter", " "].includes(event.key)) return;
    event.preventDefault();
    directorImageDropZone.click();
  });
  directorImageDropZone?.addEventListener("dragover", (event) => {
    event.preventDefault();
    if (directorImageDropZone.getAttribute("aria-disabled") === "true") {
      return;
    }
    directorImageDropZone.classList.add("is-dragging");
  });
  directorImageDropZone?.addEventListener("dragleave", () => {
    directorImageDropZone.classList.remove("is-dragging");
  });
  directorImageDropZone?.addEventListener("drop", (event) => {
    event.preventDefault();
    directorImageDropZone.classList.remove("is-dragging");
    if (directorImageDropZone.getAttribute("aria-disabled") === "true") {
      return;
    }
    attachDirectorImageFiles(Array.from(event.dataTransfer?.files || []));
  });

  $("#resourceForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const name = $("#resourceName").value.trim();
    const targetBlock = $("#resourceTarget").value;
    const en = $("#resourceEn").value.trim();
    const zh = $("#resourceZh").value.trim();
    if (!name || !en || !zh) return;
    dispatch({
      type: "CREATE_RESOURCE",
      resource: {
        id: `custom-${Date.now()}`,
        type: "snippets",
        name,
        meta: $("#resourceTarget").selectedOptions[0].textContent,
        targetBlock,
        en,
        zh,
      },
    });
    event.target.reset();
    quickTab = "snippets";
    renderQuickPicks();
  });

  favoriteResources = readFavoriteResources();
  syncFavoriteResourceGroups();
  render();
  loadProjects();
  loadSettings();
  loadModelProfiles();
  loadLocalLlmStatus();
  loadVisionStatus();
  loadFavoriteResources();
  loadAnimaDexResources();
  loadRandomCatalog();
  try {
    if (localStorage.getItem(PENDING_SAVE_STORAGE_KEY)) {
      saveCurrentProject({ automatic: true });
    }
  } catch {
    // The save path reports storage failures without sending a write request.
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
