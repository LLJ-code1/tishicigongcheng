# Prompt Studio API 接入指南

## 创意导演接入

`POST /api/creative-intake/director` 接收当前 canonical session、用户文字、推理来源、
可选的 Skill 覆盖和 `imageEvidence` 数组。数组最多八项，服务端按 canonical 图片
顺序规范化；每项只含绑定图片 ID、逐图 `requestedUses` 和有界本地图像文字证据。
外部文本提供商不得接收图片字节、文件路径、blob URL、base64 或 API Key。模型响应
只允许 `message` 和一个候选 `action`；确认需求卡、选择模型等越权动作会被拒绝。

客户端随后把候选动作提交到 `POST /api/creative-intake/transition`。只有该接口返回的
完整规范会话可以替换本地 canonical session，并且响应修订号必须恰好增加 1。保存使用
metadata-only `POST /api/workspace/commit`，与尚未保存的 Recipe 编辑相互隔离。

全局 `PUT /api/settings` 可写 `creativeDirectorSkillOverride`。空字符串恢复
`prototype/prompts/creative_director.md`；最大长度 100,000 字符。`GET /api/settings`
永不回显 API Key，Skill 编辑器也不读取或展示密钥字段。

本文面向需要调用本地 Prompt Studio HTTP 服务的前端或脚本。默认地址为
`http://127.0.0.1:57913`；所有写接口只接受同源的 UTF-8
`application/json`，备份上传接口除外。

## 核心约定

- 成功响应通常使用 `{ "item": ... }`，列表接口使用 `{ "items": [...] }`。
- JSON 必须是对象，拒绝重复键、`NaN`、`Infinity`、短正文和
  `Transfer-Encoding`。
- `POST /api/workspace/commit` 是工作台当前的保存入口。旧的项目、版本和 PUT
  接口继续兼容，但不要用它们组合新的前端保存流程。
- 项目版本中的 `metadata.recipe` 是权威的完整 Recipe v1；顶层提示词和十三块
  `blocks` 是兼容投影。
- API Key、内部幂等账本和数据库文件不会通过普通读取接口返回。

## API 速查

| 方法 | 路由 | 用途 |
| --- | --- | --- |
| `POST` | `/api/creative-intake/transition` | 由服务器规范化并应用一次创意意图状态转换 |
| `POST` | `/api/creative-intake/decomposition-preview` | 生成精确模型版本适配的十三块草稿 |
| `POST` | `/api/workspace/commit` | 原子保存项目头和可选完整版本 |
| `GET` | `/api/model-profiles` | 列出模型档案 |
| `GET` | `/api/model-profiles/<id>` | 读取单个模型档案及验证状态 |
| `POST` | `/api/recipe/resolve` | 合成并校验 Recipe v1、参数来源和 Hash |
| `GET` | `/api/text/random-catalog` | 读取词库目录、版本和发布门禁状态 |
| `POST` | `/api/text/random-plan` | 计算确定性抽样计划，不调用文本模型 |
| `POST` | `/api/text/edit-preview` | 调用文本模型理解中文局部修改并返回签名差异预览 |
| `POST` | `/api/text/edit-apply` | 校验原预览并生成追加式修改记录 |
| `POST` | `/api/text/edit-undo` | 生成追加式撤销记录，不删除历史 |
| `GET` | `/api/backups/export` | 导出全库或单项目逻辑 ZIP |
| `POST` | `/api/backups/inspect` | 只校验备份，不写数据库 |
| `POST` | `/api/backups/stage-restore` | 恢复到隔离数据库，不激活 |

既有项目、收藏、设置、文本模型、图片分析与 AnimaDex 路由见
[架构说明](architecture.md#api-约定)。

## 创意意图状态转换

`POST /api/creative-intake/transition` 只接受包含 `current` 和 `action` 的对象 JSON。
服务端使用 `creative_intake.py` 规范化 `current`、验证当前阶段与动作，然后返回
`{ "item": <完整规范会话> }`。成功转换把 `revision` 加一；客户端不得在本地授权锁定
条目或直接跳过阶段。受支持动作是 `replace_inputs`、`set_directions`、
`select_direction`、`set_brief_draft`、`confirm_brief`、`select_model`、
`set_decomposition_draft`、`confirm_decomposition` 和 `reopen_brief`。

### 合法的 `replace_inputs`

```json
{
  "current": {
    "schemaVersion": 1,
    "revision": 0,
    "stage": "intake",
    "inputs": { "text": "", "images": [] },
    "directions": [],
    "selectedDirectionId": null,
    "brief": null,
    "selectedModelProfileId": null,
    "decomposition": null,
    "recipeStatus": "missing",
    "conflicts": []
  },
  "action": {
    "type": "replace_inputs",
    "text": "雨夜中奔跑的红发女孩",
    "images": [
      {
        "id": "image-1",
        "name": "reference.png",
        "mimeType": "image/png",
        "status": "local_reference_not_embedded",
        "requestedUses": ["action"]
      }
    ]
  }
}
```

响应：

```json
{
  "item": {
    "schemaVersion": 1,
    "revision": 1,
    "stage": "intake",
    "inputs": {
      "text": "雨夜中奔跑的红发女孩",
      "images": [
        {
          "id": "image-1",
          "name": "reference.png",
          "mimeType": "image/png",
          "status": "local_reference_not_embedded",
          "requestedUses": ["action"]
        }
      ]
    },
    "directions": [],
    "selectedDirectionId": null,
    "brief": null,
    "selectedModelProfileId": null,
    "decomposition": null,
    "recipeStatus": "missing",
    "conflicts": []
  }
}
```

`images` 最多八项。图片仅以这个引用描述进入会话；不要发送图片字节、路径、base64、
blob URL、密钥或任意附加元数据。每张图的 `requestedUses` 独立保存，空数组表示让 AI
建议候选用途，不等于确认借用。

### 多图 `imageEvidence`

```json
{
  "current": "<完整 canonical session>",
  "message": "用第一张的动作、第二张的衣服、第三张的环境",
  "provider": "local",
  "imageEvidence": [
    {
      "imageId": "image-action",
      "requestedUses": ["action"],
      "summary": "人物向前奔跑",
      "sourceModels": ["florence-promptgen"],
      "uncertain": false
    },
    {
      "imageId": "image-outfit",
      "requestedUses": ["outfit"],
      "summary": "红色长外套",
      "sourceModels": ["florence-promptgen"],
      "uncertain": false
    },
    {
      "imageId": "image-environment",
      "requestedUses": ["environment"],
      "summary": "雨夜街道",
      "sourceModels": ["florence-promptgen"],
      "uncertain": false
    }
  ]
}
```

每条 evidence 都必须同时包含 `imageId`、`requestedUses`、`summary`、
`sourceModels` 和 `uncertain`；缺少字段或增加任意额外字段都会被拒绝。

客户端应逐图调用本地 `/api/vision/analyze`，保留成功项并只重试失败图片。服务端兼容
旧客户端的单个 evidence 对象，但传给 provider 的 `imageEvidence` 始终是数组。brief
采用图片内容时必须写入 `source: {"type":"image","refId":"image-action"}` 一类稳定
引用。刷新或重开后 canonical 引用仍在，浏览器 `File` 不在；只为缺失文件的图片提示
重新附加，不应删除其他图片的用途或来源。

### 合法的 `confirm_brief`

```json
{
  "current": {
    "schemaVersion": 1,
    "revision": 4,
    "stage": "brief_draft",
    "inputs": { "text": "雨夜中奔跑的红发女孩", "images": [] },
    "directions": [
      { "id": "main", "label": "主方向", "summary": "雨夜奔跑" }
    ],
    "selectedDirectionId": "main",
    "brief": {
      "status": "draft",
      "summary": "雨夜中的追逐镜头",
      "items": [
        {
          "id": "action-1",
          "category": "action",
          "text": "奔跑",
          "source": { "type": "user", "refId": null },
          "locked": true
        }
      ],
      "aiAdditions": [],
      "openQuestions": []
    },
    "selectedModelProfileId": null,
    "decomposition": null,
    "recipeStatus": "missing",
    "conflicts": []
  },
  "action": { "type": "confirm_brief" }
}
```

响应中的 `item` 与请求会话相同，但 `revision` 为 `5`、`stage` 为
`brief_confirmed`，且 `brief.status` 为 `confirmed`。若 `openQuestions` 非空或任何
`conflicts` 项的 `status` 为 `open`，服务端以 `400` 拒绝请求。

### 已锁定条目的冲突

下面的 `set_brief_draft` 试图改变已锁定的 `action-1`，但没有用
`approvedLockedItemIds` 显式授权：

```json
{
  "current": {
    "schemaVersion": 1,
    "revision": 4,
    "stage": "brief_draft",
    "inputs": { "text": "雨夜中奔跑的红发女孩", "images": [] },
    "directions": [
      { "id": "main", "label": "主方向", "summary": "雨夜奔跑" }
    ],
    "selectedDirectionId": "main",
    "brief": {
      "status": "draft",
      "summary": "雨夜中的追逐镜头",
      "items": [
        {
          "id": "action-1",
          "category": "action",
          "text": "奔跑",
          "source": { "type": "user", "refId": null },
          "locked": true
        }
      ],
      "aiAdditions": [],
      "openQuestions": []
    },
    "selectedModelProfileId": null,
    "decomposition": null,
    "recipeStatus": "missing",
    "conflicts": []
  },
  "action": {
    "type": "set_brief_draft",
    "brief": {
      "status": "draft",
      "summary": "雨夜中的追逐镜头",
      "items": [
        {
          "id": "action-1",
          "category": "action",
          "text": "慢跑",
          "source": { "type": "user", "refId": null },
          "locked": true
        }
      ],
      "aiAdditions": [],
      "openQuestions": []
    },
    "conflicts": []
  }
}
```

实际错误响应为：

```json
{
  "error": "locked item action-1 requires approval",
  "code": "locked_item"
}
```

该锁定冲突使用实现中的稳定错误码 `locked_item`；如需本次修改，应在动作加入
`"approvedLockedItemIds": ["action-1"]`。该授权只适用于这一次转换，不写入会话。

## 原子保存与重放

客户端在发请求前生成并持久化以下值：

- `operationId`：1–128 位安全 ID，同时作为 `Idempotency-Key` 请求头。
- 新项目的 `project.id`。
- 新版本的 `version.id`。
- 完整、冻结的请求正文。

请求骨架：

```json
{
  "operationId": "save-01HXYZ",
  "createProject": true,
  "project": {
    "id": "project-01HXYZ",
    "name": "示例作品",
    "mode": "text",
    "status": "draft",
    "metadata": {}
  },
  "version": {
    "id": "version-01HXYZ",
    "baseVersion": 0,
    "source": "manual",
    "positiveEn": "masterpiece, best quality, score_7",
    "positiveZh": "杰作，最佳质量",
    "negativeEn": "low quality",
    "negativeZh": "低质量",
    "blocks": [],
    "metadata": {}
  }
}
```

更新已有项目时设 `createProject=false`，并在 `project.baseUpdatedAt` 中发送客户端最后
一次收到的服务端项目更新时间。没有内容版本变化时，`version` 可以为 `null`。

若数据库已提交但响应丢失，必须用同一个请求头和逐字等价的 JSON 内容重放。服务端
返回第一次提交结果，不会创建第二个版本；同一幂等键配不同正文返回
`409 idempotency_conflict`。工作台前端把未确认请求记录在
`localStorage` 的 `promptStudio.pendingSave.v1`，启动时自动恢复。

## 模型适配十三块预览

只有 `model_selected` 阶段可以创建新预览；退回块允许在同一 brief 和精确模型血统下
整体重生成。客户端只发送 canonical session 和模型目录刚返回的不可变版本身份：

```http
POST /api/creative-intake/decomposition-preview
Content-Type: application/json

{
  "current": {"schemaVersion":1,"revision":8,"stage":"model_selected"},
  "profileVersionId":"profile-version-7",
  "profileContentSha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

示例中的 `current` 省略了必填的完整 brief、模型选择和其他 canonical 字段；生产请求
不得省略。成功响应的 `item.decomposition` 含精确 brief Hash、模型版本/Hash 和严格
十三块。每块的 `zh`/`semanticItems` 是确认语义，`en`/`reason`/`ruleRefs` 是模型适配。
`warnings` 会保留未批准 claim 的 ID 和决定，但这些 claim 不会应用。

常见错误：`stale_intake`/`profile_hash_mismatch` 返回 409，要求刷新档案并重新确认；
`locked_fact_changed`/`unapproved_rule` 返回 422，不写入草稿；`provider_failed` 和连续
无效输出返回 502，保留上一次合法状态。前端还会校验项目、会话、brief、模型 ID、
版本和 Hash，丢弃迟到响应。

## Recipe 与模型档案

当前内置档案 ID 为 `anima-1.1-v1`，精确模型版本 ID 为 `3004063`。档案默认
Steps 30、CFG 5.5、Euler/Euler a + Normal，并明确不注入 `safe`。

`POST /api/recipe/resolve` 接收提示词、十三块、LoRA、参数层、随机计划、中文修改
历史、图片引用和来源引用，返回：

- `recipe`：规范化 Recipe v1。
- `recipeHash`：严格 JSON 的 SHA-256。
- `profile`：档案、参数和验证状态。

使用研究档案时，请求还必须同时提供 `profileVersionId` 与
`profileContentSha256`。服务端从数据库重新解析该精确激活版本，不接受客户端档案正文，
也不会在查找失败时退回另一个活动版本。返回 Recipe 的 `model` 原样保存这两个字段；
内置未研究档案则保存两个 `null`。

参数优先级固定为：

```text
manual_override > task_preset > lora_requirement > model_default
```

当前仓库没有可核验的 checkpoint 文件、Hash 和本机出图尺寸证据，因此档案返回
`generationReady=false`，尺寸状态为 `unverified`。调用方不得把候选尺寸展示成已验证
推荐。

## 官网模型研究、审核与激活

只提交注册 adapter 支持的原始 `https://` 模型页：

```http
POST /api/model-research
Content-Type: application/json

{"sourceUrl":"https://civitai.com/models/934764/example"}
```

成功返回 `201`，`item` 中包含 `run`、不可变 `snapshots`、初始为 proposed 的
`claims`、`draftVersion`、`researchStatus` 和 `warnings`。查看已保存运行：

```http
GET /api/model-research/<runId>
```

人工决定 claim 并补充精确身份会创建新的 draft 子版本；不能修改 snapshot：

```http
PUT /api/model-profile-versions/<versionId>
Content-Type: application/json

{
  "claimDecisions":{"claim-1":"approved","claim-2":"rejected"},
  "manualFields":{
    "displayName":"Example",
    "model.versionName":"v1",
    "model.versionId":22,
    "notes":"参数仍待本地验证"
  },
  "reviewNote":"已核对官网版本身份"
}
```

审核同样创建不可变子版本：

```http
POST /api/model-profile-versions/<versionId>/review
Content-Type: application/json

{"reviewerNote":"已核对原始页面与精确版本号"}
```

激活必须显式提交调用方最后看到的活动版本；第一次激活也必须传 `null`：

```http
POST /api/model-profile-versions/<versionId>/activate
Content-Type: application/json

{"expectedActiveVersionId":null}
```

读取单个不可变版本使用 `GET /api/model-profile-versions/<versionId>`。激活成功后重新
读取 `GET /api/model-profiles`；研究档案与内置档案使用同一目录形状，并额外包含
`profileVersionId`、`profileContentSha256`、`evidenceSummary`、`warnings` 和
`generationReady`。

| HTTP / code | 含义 | 处理 |
| --- | --- | --- |
| `400 unsupported_source` | host/path、redirect 或 adapter 不受支持 | 改用受支持的原始模型页，或继续使用已有模型 |
| `400 unsafe_address` | localhost、IP 字面量或 DNS 含非 global 地址 | 请求未发送；检查 URL/DNS，不绕过策略 |
| `400 invalid_request` | 未知字段、缺少 CAS 字段或值越界 | 按严格请求 schema 修正 |
| `404 unknown_run` | 研究运行不存在 | 重新读取服务端历史 |
| `400 unknown_version` | 档案版本不存在 | 重新读取服务端历史 |
| `409 active_version_changed` | 活动版本已被其他操作替换 | 刷新目录，核对新活动版本后再次确认 |
| `409 profile_hash_mismatch` | 存储正文与规范 Hash 不一致 | 停止审核/激活，保留数据库并排查 |
| `409 unresolved_model_identity` | 名称、精确版本号或来源仍未确认 | 决定 claim 或补充人工身份字段后创建新 draft |

超时、抓取或解析失败可能仍返回 `201` 的 `pending_verification` 草稿；它不是“官网已
确认”。调用方应展示错误/warning，允许显式重试或人工补充身份，同时保持已有模型
选择和通用规则可用。当前阶段不接受 supplemental URL、客户端页面正文或任意证据类别。

## 确定性词库

`GET /api/text/random-catalog` 始终可读，方便审查 9 类、471 条目录。当前目录仍返回：

```text
runtimeReady=false
semanticReviewRequired=true
```

只有服务端显式设置 `PROMPT_STUDIO_EXPERIMENTAL_WORDLISTS=1` 时，
`POST /api/text/random-plan` 才可用。最小请求只需 128 位十六进制 Seed；不传 Seed 时
服务端生成一个：

```json
{
  "librarySeed": "00112233445566778899aabbccddeeff"
}
```

服务端会补齐并核对目录版本、目录 Hash、`sha256-counter-v1` 抽样器和映射版本，返回
抽中项、绑定块、重抽/拒绝轨迹、锁定与冲突。该接口不调用 LLM。前端把英文词条写入
十三块工作台；中文暂标为“待本地 LLM 翻译”。

实验开关只表示允许个人验证，不表示词库已经通过逐条语义审核或可以公开发布。当前
目录的 `mappingVersion` 仍为 `ten-block-v1`，前端通过兼容适配把服装独立映射到
`outfit`；正式发布前需要不可变目录历史和新的十三块映射版本。

## AI 中文局部修改

调用顺序固定为：

1. `edit-preview`：发送 `instruction`、当前 `recipe`、`baseRecipeHash`、
   `provider`（`local` 或 `api`）和 `targetModel`（当前为 `anima`）。
2. 只在响应 `ready=true` 时让用户确认；展示 `affectedIds`、`lockedIds` 和
   `diffs`。每项 diff 包含完整 `before`、`after` 和中文 `reason`。
3. `edit-apply`：回传原 Recipe 和服务端返回的完整 `preview`，再附父/新版本号。
4. 把返回 Recipe 通过 `workspace/commit` 保存为新版本。
5. 撤销时调用 `edit-undo`，再把撤销结果另存为新版本。

不要修改、重算或裁剪 preview；服务端 HMAC、Recipe Hash 和工作区修订共同阻止旧
预览、伪造差异和并发漂移。模型不可用返回 `provider_not_configured` 或
`model_unavailable`；非法、重复、空白或无变化的 AI 结果整份拒绝，不回退固定词典。

## 备份、校验与隔离恢复

导出：

```text
GET /api/backups/export?scope=full
GET /api/backups/export?scope=project&projectId=<encoded-id>
```

导出的 ZIP 是逻辑 JSON 包。它包含项目/版本，以及全库导出时的资源、收藏和安全设置；
排除 API Key、接口 URL、内部幂等账本、模型、LoRA、图片和其他二进制文件。

校验和恢复接口接收原始备份字节，`Content-Type` 可为 `application/zip`、
`application/json` 或 `application/octet-stream`：

```text
POST /api/backups/inspect
POST /api/backups/stage-restore?conflict=reject
POST /api/backups/stage-restore?conflict=rename
```

上传上限 64 MiB。系统先校验严格 JSON、manifest、SHA-256、ZIP 路径、重复成员、文件
数量、解压总量和压缩比，再写隔离库。恢复响应始终包含 `activated=false` 和
`stagingDatabase`；当前 API 不会替换正在使用的数据库。

## 常见错误

| HTTP / code | 含义 | 处理 |
| --- | --- | --- |
| `400` | schema、严格 JSON、Hash 或 ID 无效 | 修正请求，不原样盲重试 |
| `409 version_conflict` | `baseVersion` 已过期 | 重新打开项目并人工合并 |
| `409 project_conflict` | `baseUpdatedAt` 已过期 | 重新读取项目头 |
| `409 idempotency_conflict` | 幂等键被不同正文复用 | 视为客户端日志损坏，生成新操作前先对账 |
| `409 random_catalog_not_release_ready` | 未开启实验词库 | 启用实验开关或继续使用模型驱动全随机 |
| `409 backup_conflict` | 恢复目标已有同 ID 数据 | 选择 `rename` 或换干净隔离库 |
| `400 invalid_backup` | 备份损坏、不完整或越界 | 不得写入/激活该备份 |
| `413` | 正文超过该路由上限 | 缩小请求或备份 |
| `415` | Content-Type 不受支持 | 使用接口要求的媒体类型 |
