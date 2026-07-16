(function (root) {
  const data =
    root.PROMPT_STUDIO_DATA ||
    (typeof require !== "undefined" ? require("./data.js") : {});
  const RANDOM_VARIANT_BLOCKS = new Set([
    "subject",
    "appearance",
    "pose",
    "scene",
    "composition",
    "lighting",
    "effects",
  ]);

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
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

  function createInitialState() {
    return {
      view: "home",
      projectId: null,
      projectName: "未命名作品",
      textMode: "expand",
      draftInput: "",
      randomSeed: null,
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
      generationProgress: {
        status: "idle",
        task: "",
        title: "等待任务",
        detail: "点击“仅拆解”或“拓展并结构化”后，这里会显示当前进度。",
        provider: "",
        startedAt: 0,
        elapsedSeconds: 0,
        finishedAt: 0,
      },
      regeneratingBlockId: "",
      settings: {
        textProvider: "local",
        localTextUrl: "http://127.0.0.1:8080/v1",
        localTextModel: "Huihui-Qwen3-14B-abliterated-v2.Q4_K_M.gguf",
        localTextKey: "",
        apiTextUrl: "",
        apiTextModel: "",
        apiTextKey: "",
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
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function formatBlockWeight(weight) {
    const value = Number(weight);
    if (!Number.isFinite(value) || value === 100) return "";
    return (value / 100).toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
  }

  function compileBlockFragment(block) {
    const factor = formatBlockWeight(block.weight);
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
      const weight = Number.isFinite(Number(block.weight))
        ? Number(block.weight)
        : 100;
      if (weight === 0) continue;
      const factor = formatBlockWeight(weight);
      for (const item of splitPromptItems(block.en)) {
        const key = item.toLowerCase();
        if (seenEn.has(key)) continue;
        seenEn.add(key);
        enItems.push(factor ? `(${item}:${factor})` : item);
      }
      for (const item of splitPromptItems(block.zh)) {
        const key = item.toLowerCase();
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
    const query = String(token || "").trim().toLowerCase();
    if (query.length < 2) return [];
    const source = Array.isArray(dictionary) ? dictionary : [];
    const seen = new Set();
    const starts = [];
    const contains = [];
    for (const raw of source) {
      const tag = String(raw || "").trim();
      const key = tag.toLowerCase();
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
        const factor = formatBlockWeight(entry.weight);
        return factor ? `(${tag}:${factor})` : tag;
      })
      .filter(Boolean)
      .join(", ");
  }

  function refreshPreview(next) {
    next.previewOutput = next.dirtyBlockIds.length
      ? compileBlocks(
          next.blocks,
          next.output.relationEn,
          next.output.relationZh
        )
      : null;
  }

  function initializeVersion(next, output, blocks) {
    next.output = clone(output);
    next.blocks = clone(blocks);
    next.appliedBlocks = clone(blocks);
    next.dirtyBlockIds = [];
    next.selectedVariantBlockIds = [];
    next.batchRegenerating = false;
    next.version = 1;
    next.versionHistory = [
      {
        version: 1,
        output: clone(output),
        blocks: clone(blocks),
      },
    ];
  }

  function markDirty(next, blockId) {
    if (!next.dirtyBlockIds.includes(blockId)) {
      next.dirtyBlockIds.push(blockId);
    }
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

    switch (action.type) {
      case "NAVIGATE":
        next.view = action.view;
        next.toast = "";
        return next;
      case "SET_TEXT_MODE":
        next.view = "text";
        next.textMode = action.mode;
        return next;
      case "SET_DRAFT":
        next.draftInput = action.value;
        return next;
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
        if (next.versionHistory.length) {
          const current = next.versionHistory[next.versionHistory.length - 1];
          if (current.version === next.version) {
            current.output = clone(next.output);
            current.blocks = clone(next.appliedBlocks);
          }
        }
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
        const resource = clone(action.resource);
        const type = resource.type || "snippets";
        if (!next.resources[type]) next.resources[type] = [];
        next.resources[type].push(resource);
        next.resourceDialogOpen = false;
        next.toast = `已新建资源：${resource.name}`;
        return next;
      }
      case "ADD_RECIPE": {
        const recipe = action.recipe;
        if (!recipe || !Array.isArray(recipe.blocks) || !recipe.blocks.length) {
          return next;
        }
        next.resources.snippets.push(clone(recipe));
        next.toast = `已保存配方「${recipe.name}」`;
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
        next.toast = `已保存「${block.label}」为长期资源`;
        return next;
      }
      case "APPLY_RESOURCE": {
        const resource = findResource(next.resources, action.id);
        if (!resource) return next;
        if (!next.blocks.length) {
          next.blocks = clone(data.promptBlocks || []);
          next.appliedBlocks = clone(next.blocks);
        }
        const resourceBlocks = resource.blocks || [
          {
            id: resource.targetBlock,
            en: resource.en,
            zh: resource.zh,
          },
        ];
        resourceBlocks.forEach((resourceBlock) => {
          const block = next.blocks.find((item) => item.id === resourceBlock.id);
          if (!block) return;
          block.en = resourceBlock.en;
          block.zh =
            resourceBlock.zh || `待本地 LLM 翻译：${resourceBlock.en}`;
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
            next.analyzers[analyzer.id].status = analyzer.status || "ready";
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
        if (block) block.weight = Number(action.value);
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
        const output = compileBlocks(
          next.blocks,
          next.output.relationEn,
          next.output.relationZh
        );
        next.version = Math.max(1, next.version) + 1;
        next.output = output;
        next.appliedBlocks = clone(next.blocks);
        next.dirtyBlockIds = [];
        next.versionHistory.push({
          version: next.version,
          output: clone(output),
          blocks: clone(next.blocks),
        });
        next.previewOutput = null;
        next.toast = `修改已应用，生成 V${next.version}`;
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
        if (snapshot.version === next.version && !next.dirtyBlockIds.length) {
          return next;
        }
        next.version = Math.max(1, next.version) + 1;
        next.blocks = clone(snapshot.blocks);
        next.appliedBlocks = clone(snapshot.blocks);
        next.output = clone(snapshot.output);
        next.dirtyBlockIds = [];
        next.previewOutput = null;
        next.versionHistory.push({
          version: next.version,
          output: clone(snapshot.output),
          blocks: clone(snapshot.blocks),
          restoredFrom: snapshot.version,
        });
        next.toast = `已从 V${snapshot.version} 恢复，生成 V${next.version}`;
        return next;
      }
      case "CLEAR_TOAST":
        next.toast = "";
        return next;
      default:
        return next;
    }
  }

  function getCombinedPrompt(state) {
    return [
      state.output.positiveEn,
      state.output.positiveZh,
      state.output.negativeEn,
      state.output.negativeZh,
    ].join("\n");
  }

  const api = {
    createInitialState,
    reduceState,
    getCombinedPrompt,
    isSupportedImageFile,
    compileBlocks,
    compileBlockFragment,
    composeArtistMix,
    splitPromptItems,
    suggestTags,
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
  let uploadedImageFile = null;
  let imagePreviewUrl = "";
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
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  async function apiJson(path, options = {}) {
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
      body: JSON.stringify(resource),
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
      favoriteResources = remoteFavorites;
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

  async function toggleFavoriteResource(id) {
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
      if (added) await persistFavoriteResource(added);
      if (removed) await removePersistedFavoriteResource(removed.favoriteId || removed.id);
    } catch {
      state.toast = "本次收藏已暂存到浏览器，后端恢复后会再迁移";
      renderToast();
    }
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

  async function saveSettings() {
    try {
      await apiJson("/api/settings", {
        method: "PUT",
        body: JSON.stringify(state.settings),
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
    activeTextAbort = new AbortController();
    try {
      const result = await apiJson("/api/text/expand", {
        method: "POST",
        signal: activeTextAbort.signal,
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
      dispatch({
        type: "APPLY_TEXT_EXPANSION",
        item: result.item,
        finishedAt: Date.now(),
      });
    } catch (error) {
      dispatch({
        type: "TEXT_EXPANSION_FAILED",
        error: isAbortError(error)
          ? "已取消本次拓展"
          : error.message || "提示词拓展失败",
        finishedAt: Date.now(),
      });
    } finally {
      activeTextAbort = null;
    }
  }

  async function randomizeTextPrompt() {
    if (state.textGenerating || state.textDecomposing) return;
    dispatch({ type: "START_TEXT_RANDOM", startedAt: Date.now() });
    activeTextAbort = new AbortController();
    try {
      const result = await apiJson("/api/text/random", {
        method: "POST",
        signal: activeTextAbort.signal,
        body: JSON.stringify({
          provider: state.settings.textProvider,
          targetModel: "anima",
        }),
      });
      dispatch({
        type: "APPLY_TEXT_RANDOM",
        item: result.item,
        finishedAt: Date.now(),
      });
    } catch (error) {
      dispatch({
        type: "TEXT_RANDOM_FAILED",
        error: isAbortError(error)
          ? "已取消全随机生成"
          : error.message || "全随机生成失败",
        finishedAt: Date.now(),
      });
    } finally {
      activeTextAbort = null;
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
    activeTextAbort = new AbortController();
    try {
      const result = await apiJson("/api/text/decompose", {
        method: "POST",
        signal: activeTextAbort.signal,
        body: JSON.stringify({
          input,
          provider: state.settings.textProvider,
        }),
      });
      dispatch({
        type: "APPLY_TEXT_DECOMPOSITION",
        item: result.item,
        finishedAt: Date.now(),
      });
    } catch (error) {
      dispatch({
        type: "TEXT_DECOMPOSITION_FAILED",
        error: isAbortError(error)
          ? "已取消提示词拆解"
          : error.message || "提示词拆解失败",
        finishedAt: Date.now(),
      });
    } finally {
      activeTextAbort = null;
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
    try {
      const result = await apiJson("/api/text/translate-pending", {
        method: "POST",
        body: JSON.stringify({ items }),
      });
      dispatch({
        type: "APPLY_PENDING_TRANSLATIONS",
        item: result.item,
      });
    } catch (error) {
      dispatch({
        type: "PENDING_TRANSLATION_FAILED",
        error: error.message || "本地 LLM 翻译失败",
      });
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
    try {
      const result = await apiJson("/api/text/regenerate-block", {
        method: "POST",
        body: JSON.stringify({
          targetBlockId: blockId,
          blocks: state.blocks,
          provider: state.settings.textProvider,
          expansionLevel: state.settings.expansionLevel || "balanced",
        }),
      });
      dispatch({ type: "APPLY_BLOCK_VARIANT", item: result.item });
    } catch (error) {
      dispatch({
        type: "BLOCK_VARIANT_FAILED",
        error: error.message || "随机变体生成失败",
      });
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
      })),
    };
    dispatch({ type: "ADD_RECIPE", recipe });
    try {
      await persistFavoriteResource(recipe);
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
    const request = () =>
      apiJson("/api/text/regenerate-blocks", {
        method: "POST",
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
    try {
      const result = await apiJson("/api/text/regenerate-blocks", {
        method: "POST",
        body: JSON.stringify({
          targetBlockIds,
          blocks: state.blocks,
          provider: state.settings.textProvider,
          expansionLevel: state.settings.expansionLevel || "balanced",
        }),
      });
      dispatch({ type: "APPLY_BLOCKS_VARIANT", item: result.item });
    } catch (error) {
      dispatch({
        type: "BLOCKS_VARIANT_FAILED",
        error: error.message || "联合随机失败",
      });
    }
  }

  async function saveCurrentProject() {
    try {
      if (!state.projectId) {
        const result = await apiJson("/api/projects", {
          method: "POST",
          body: JSON.stringify({
            name: state.projectName || "未命名作品",
            mode: state.view === "image" ? "image" : "text",
            metadata: {
              textMode: state.textMode,
              draftInput: state.draftInput,
              settings: state.settings,
              imageName: state.imageName,
            },
          }),
        });
        state.projectId = result.item?.id || state.projectId;
      }
      if (state.projectId && state.version) {
        await apiJson(`/api/projects/${encodeURIComponent(state.projectId)}/versions`, {
          method: "POST",
          body: JSON.stringify({
            source: state.textMode,
            positiveEn: state.output.positiveEn,
            positiveZh: state.output.positiveZh,
            negativeEn: state.output.negativeEn,
            negativeZh: state.output.negativeZh,
            blocks: state.appliedBlocks,
            metadata: {
              randomSeed: state.randomSeed,
              dirtyBlockIds: state.dirtyBlockIds,
            },
          }),
        });
      }
      state.toast = `作品已保存：${state.projectName || "未命名作品"}`;
    } catch {
      state.toast = "作品暂时没有写入数据库，请确认本地后端已启动";
    }
    renderToast();
  }

  function dispatch(action) {
    state = app.reduceState(state, action);
    render();
  }

  function statusLabel(status) {
    return {
      idle: "未加载",
      waiting: "等待中",
      running: "运行中",
      ready: "已就绪",
      releasing: "释放中",
      error: "错误",
      unavailable: "未安装",
    }[status] || status;
  }

  function renderNavigation() {
    $$(".top-nav [data-nav]").forEach((button) => {
      button.classList.toggle("active", button.dataset.nav === state.view);
    });
  }

  function renderViews() {
    $("#homeView").classList.toggle("hidden", state.view !== "home");
    $("#workbenchArea").classList.toggle("hidden", state.view === "home");
    $("#textView").classList.toggle("hidden", state.view !== "text");
    $("#imageView").classList.toggle("hidden", state.view !== "image");
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
            <b class="model-status ${escapeHtml(model.status)}">${statusLabel(model.status)}</b>
          </label>
        `
      )
      .join("");

    $("#analysisNotice").textContent = state.analysisNotice;
    $("#queueLine").innerHTML = Object.values(state.analyzers)
      .filter((model) => model.selected)
      .map(
        (model) =>
          `<span class="queue-step ${escapeHtml(model.status)}" title="${escapeHtml(model.name)}: ${statusLabel(model.status)}"></span>`
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
    if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
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
    const weightArea = collapsed
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
    $("#versionBadge").textContent = hasVersion
      ? previewing
        ? `V${state.version} · 实时预览`
        : `V${state.version}`
      : "尚未生成";
    const pendingBar = $("#pendingBar");
    const dirtyCount = state.dirtyBlockIds.length;
    pendingBar.classList.toggle("hidden", dirtyCount === 0);
    $("#pendingSummary").textContent = dirtyCount
      ? `已修改 ${dirtyCount} 个结构块，右侧输出为实时预览；应用后固化为 V${state.version + 1}。`
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
        const isCurrent = entry.version === state.version;
        const label = entry.restoredFrom
          ? `V${entry.version}↩`
          : `V${entry.version}`;
        return `
          <button
            class="version-chip ${isCurrent ? "active" : ""}"
            type="button"
            data-restore-version="${entry.version}"
            title="${
              isCurrent
                ? "当前版本"
                : `恢复到 V${entry.version}（会生成新版本，不丢历史）`
            }"
            ${isCurrent ? "disabled" : ""}
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
    });

    $("#drawerModelList").innerHTML = Object.values(state.analyzers)
      .filter((model) => model.id !== "external")
      .map(
        (model) => `
          <div class="drawer-model-row">
            <span>
              <strong>${escapeHtml(model.name)}</strong>
              <span>${escapeHtml(model.device)} · ${escapeHtml(model.detail)}</span>
            </span>
            <b class="model-status ${escapeHtml(model.status)}">${statusLabel(model.status)}</b>
          </div>
        `
      )
      .join("");

    const loaded = Object.values(state.analyzers).filter((model) =>
      ["ready", "running", "waiting"].includes(model.status)
    ).length;
    $("#memoryEstimate").textContent = `预计显存 ${loaded ? (loaded * 2.4).toFixed(1) : "0"} GB`;
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
    try {
      const dataBase64 = await fileToBase64(uploadedImageFile);
      activeVisionAbort = new AbortController();
      const result = await apiJson("/api/vision/analyze", {
        method: "POST",
        signal: activeVisionAbort.signal,
        body: JSON.stringify({
          filename: uploadedImageFile.name,
          mimeType: uploadedImageFile.type,
          dataBase64,
          analyzerIds,
        }),
      });
      dispatch({ type: "APPLY_IMAGE_ANALYSIS", item: result.item });
    } catch (error) {
      dispatch({
        type: "IMAGE_ANALYSIS_FAILED",
        error: isAbortError(error)
          ? "已取消图片分析"
          : error.message || "图片分析失败",
      });
    } finally {
      activeVisionAbort = null;
    }
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

    if (button.dataset.nav) {
      dispatch({ type: "NAVIGATE", view: button.dataset.nav });
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
      dispatch({
        type: "RESTORE_VERSION",
        version: Number(button.dataset.restoreVersion),
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
      copyText(state.output[button.dataset.copy]);
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
    if (action === "cancel-generation") {
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
    } else if (action === "copy-all") {
      copyText(app.getCombinedPrompt(state));
    } else if (action === "save-draft") {
      saveCurrentProject();
    }
  });

  document.addEventListener("input", (event) => {
    if (event.target.id === "draftInput") {
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
      if (state.view === "text" && state.version > 0) {
        event.preventDefault();
        saveCurrentProject();
      }
    }
  });

  document.addEventListener("change", (event) => {
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
    }
  });

  $("#expandBtn").addEventListener("click", expandTextPrompt);
  $("#decomposeBtn").addEventListener("click", decomposeTextPrompt);
  $("#randomBtn").addEventListener("click", randomizeTextPrompt);
  $("#dropZone").addEventListener("click", () => $("#imageInput").click());
  $("#analyzeBtn").addEventListener("click", startImageAnalysis);

  const dropZone = $("#dropZone");
  dropZone.addEventListener("dragover", (event) => event.preventDefault());
  dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    const file = event.dataTransfer.files?.[0];
    if (file) loadImageFile(file);
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
  loadSettings();
  loadLocalLlmStatus();
  loadVisionStatus();
  loadFavoriteResources();
  loadAnimaDexResources();
})(typeof globalThis !== "undefined" ? globalThis : this);
