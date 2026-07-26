# 数据模型

## 创意导演会话与 Skill 覆盖

`projects.metadata_json.creativeIntake` 保存服务器规范化的创作会话，包括阶段、输入的
安全图片引用、方向、需求卡、来源、锁、冲突、模型档案选择和下游失效状态。图片二进制、
本地路径、API Key 和模型返回的临时展示消息不进入该字段。需求卡和模型选择通过
metadata-only workspace commit 保存，不会伪造 Recipe 版本。

`settings.creativeDirectorSkillOverride` 是全局设置，不属于项目或 Recipe。值为空时使用
`prototype/prompts/creative_director.md`；非空值在下一次导演请求中覆盖默认 Skill。
服务器限制其为不超过 100,000 字符的字符串，并继续执行固定的结构化输出、动作白名单、
密钥回显和服务器权威转换约束，因此自定义 Skill 不能解除安全边界。

## 数据库结构版本

Prompt Studio SQLite 当前为 schema v2，并使用专用 `application_id` 防止把其他
SQLite 文件误当工作区。首次启动对兼容的旧 v0/v1 库先生成本地精确人工故障回滚副本，
再迁移并登记结构版本；空库直接创建 v2。未来版本、残缺结构、错误应用标识和完整性失败
会在写入前拒绝。

`.prompt-studio-recovery/` 人工故障回滚副本可能含本机密钥，只用于迁移故障时由用户
手动回滚并由 Git 忽略；它不是排除密钥和模型文件的可分享备份包。

## projects

作品表。一个新建作品对应一条记录。

关键字段：

- `id`
- `name`
- `mode`
- `status`
- `created_at`
- `updated_at`
- `metadata_json`

项目 ID 由服务端生成；显式 ID 只允许单一路径段安全字符。`mode` 当前只允许
`text`/`image`，metadata 必须是对象，并在写入和读取时递归剔除密钥、token、secret
等敏感字段。为兼容旧库，已有的空格、Unicode 或斜杠 ID 仍可读取和新增版本；HTTP
调用时必须把完整 ID 编码为一个路径段。

项目列表查询额外返回计算字段 `versionCount` 和 `latestVersion`。项目更新接口只允许
`name`、`mode`、`status`、`metadata`，不允许客户端改写 ID、创建时间或版本历史。
`PUT /api/projects/<id>` 必须携带当前客户端持有的 `baseUpdatedAt`；缺失返回
`428 project_precondition_required`，与当前 `updated_at` 不同返回
`409 project_conflict`，因此名称、草稿和 metadata 也不是 last-write-wins。
当前工作台不再用多个旧接口拼接保存，而是在客户端预分配项目 ID 后调用
`POST /api/workspace/commit`。同一个 `operationId` 同时作为 `Idempotency-Key`，项目
头、可选提示词版本和幂等结果在一个事务中提交。数据库已提交但 HTTP 响应丢失时，
完全相同的请求会重放第一次结果，不会产生孤儿或重复项目。

## `projects.metadata_json.creativeIntake`

创意意图会话是项目元数据中的一个版本化领域对象，而不是新 SQLite 表。保存时前端把
规范化后的值放入 `project.metadata.creativeIntake`，并通过
`POST /api/workspace/commit` 与项目头（以及可选的提示词版本）一同在一个事务中提交。
该领域对象本身不要求新增表；项目重开、逻辑备份和隔离恢复
会自然保留这份元数据。

顶层对象的 `schemaVersion` 固定为 `1`，且不接受未知字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `schemaVersion` | `1` | 创意意图数据契约版本。 |
| `revision` | 非负整数 | 服务器每成功应用一次转换恰好加一；失败不改变原会话。 |
| `stage` | 枚举 | 当前受服务器校验的流程阶段。 |
| `inputs` | 对象 | `{ "text", "images" }` 的原始文字和本地图片引用描述。 |
| `directions` | 数组 | 至多三条候选方向，每项为 `{ "id", "label", "summary" }`。 |
| `selectedDirectionId` | 字符串或 `null` | 必须引用 `directions` 中已有的 `id`。 |
| `brief` | 对象或 `null` | 当前草稿或已确认的创意 brief。 |
| `selectedModelProfileId` | 字符串或 `null` | 已选择模型档案的标识。 |
| `decomposition` | 对象或 `null` | 精确绑定 brief 与激活模型档案版本的十三块拆解草稿或确认结果。 |
| `recipeStatus` | `missing`、`stale` 或 `ready` | 下游配方是否缺失、因上游改动过期，或已就绪。 |
| `conflicts` | 数组 | 至多 100 条显式冲突；每项含 `id`、`code`、`message`、`status` 和 `itemIds`，其中 `status` 只能为 `open` 或 `resolved`。 |

允许的 `stage` 依次为 `intake`、`direction_selected`、`brief_draft`、
`brief_confirmed`、`model_selected`、`decomposition_draft` 和
`decomposition_confirmed`。规范化会拒绝不匹配的阶段，例如在 `brief_confirmed` 之前
出现已确认 brief，或在 `model_selected` 之前出现拆解数据。

`inputs.images` 最多八项，每项严格为
`{ "id", "name", "mimeType", "status", "requestedUses" }`。`name` 必须是普通文件名，
该对象不能包含图片字节、文件系统路径、API key 或任意元数据。brief 条目和拆解块中的
`source` 严格为 `{ "type", "refId" }`：`type` 为 `user`、`image`、`ai` 或
`model_rule`；`user`/`ai` 的 `refId` 必须为 `null`，`image` 的 `refId` 必须引用
`inputs.images` 中的图片 `id`，而 `model_rule` 使用非空模型规则标识。

`requestedUses` 可从 `character`、`appearance`、`outfit`、`action`、`environment`、
`composition`、`lighting`、`style` 中逐图选择；空数组表示让 AI 提候选，不代表已经
确认任何借用元素。浏览器 `File`、object URL、分析缓存和失败详情只存在于内存控制器，
不属于 `creativeIntake`。项目恢复只重建上述安全引用；缺少 `File` 的图片需要按原
稳定 ID 重新附加。每个图片来源 brief 项都必须保留自己的 `source.refId`，不能靠文案
推断来源。

导演请求的 `imageEvidence` 不是持久化字段。它接受 `null`、兼容的单对象或最多八项
数组，但服务端一律规范为数组，并按 `inputs.images` 顺序发送给文本提供商。每项必须
绑定当前图片 ID 和相同 `requestedUses`，只允许有界文字；重复/未知 ID、path、base64、
bytes、blob URL、密钥样字段或任一当前图片的不安全 MIME/status 都在 provider 调用前
拒绝。

`brief` 包含 `status`（`draft` 或 `confirmed`）、`summary`、`items`、`aiAdditions` 和
`openQuestions`。每个 `items` 条目为
`{ "id", "category", "text", "source", "locked" }`。`confirm_brief` 会把全部条目设为
`locked: true`；为兼容早期 schema v1 写出的 confirmed brief，Python 与前端
normalizer 也会在精确阶段门禁前把其中全部条目规范为已锁定，保留其他合法内容，因此
项目恢复和转换 API 不会把这类历史会话清空。draft brief 不参与该兼容迁移，其每项
`locked` 原值保持不变。

当已锁定条目的 `id`、`category`、`text`、`source` 或 `locked` 状态被改变，或条目被
移除时，`set_brief_draft` 必须在本次动作的 `approvedLockedItemIds` 中明确列出该
`id`；授权不会持久化。因此把锁降级为 `false` 本身也需要本次明确授权。`confirm_brief`
在仍有 `openQuestions` 或 `conflicts` 中存在 `status: "open"` 时拒绝确认。

`decomposition` 根包含 `status`、`briefContentSha256`、`profileVersionId`、
`profileContentSha256` 和严格按规范顺序排列的十三个 `blocks`。块包含 `id`、`category`、
`zh`、`en`、`source`、`locked`、`approved`、`reason`、`risks`、`semanticItems` 和
`ruleRefs`。`zh`/`semanticItems` 是确认语义，`en`/`reason`/`ruleRefs` 是模型适配；适配
不得改变、删除或移动锁定事实。`ruleRefs` 只能引用当前激活不可变版本中已批准 claim
及其 evidence snapshot。确认拆解前，每个块必须 `approved: true` 且没有开放冲突。

确认交接后的 Recipe `model` 同时保存 `profileVersionId` 和
`profileContentSha256`；内置未研究档案明确保存两个 `null`。档案内容 Hash 与 checkpoint
文件 Hash 是两种独立身份。Recipe `sourceRefs` 保存 brief 条目、图片、claim 和 evidence
引用；重开 brief 只让当前拆解/Recipe 失效，不删除历史版本。

失效规则由服务器转换统一执行：`replace_inputs` 清除方向、brief、模型、拆解和冲突，
并将配方置为 `missing`；`select_model` 保留已确认 brief、写入新的
`selectedModelProfileId`、清除已有拆解并将 `recipeStatus` 置为 `stale`；`reopen_brief`
把 brief 改回 draft，清除模型选择和拆解，并置为 `stale`。设置拆解草稿也保持 `stale`，
确认拆解才置为 `ready`。这些规则不由客户端自行重写。

## prompt_versions

提示词版本表。生成、应用修改或图生图合并先更新前端工作区并标记为待保存；用户
显式保存包含内容变化的作品后，才新增一条服务端版本。

关键字段：

- `id`
- `project_id`
- `version`
- `source`
- `positive_en`
- `positive_zh`
- `negative_en`
- `negative_zh`
- `blocks_json`
- `metadata_json`
- `created_at`

版本号由服务端在 `BEGIN IMMEDIATE` 事务中连续分配，不接受客户端指定。新客户端用
请求字段 `baseVersion` 声明自己基于的持久化版本，同时用 `baseUpdatedAt` 声明读取到
的项目头基线；两个字段都只用于事务内乐观并发检查，不作为列保存。数据库先比较版本
再比较项目更新时间：前者过期返回 `409 version_conflict`，后者过期返回
`409 project_conflict`，事务均不写入。
版本创建会同时推进项目 `updated_at`，并以 `projectUpdatedAt` 返回新基线；同一次保存
随后更新项目 metadata 时必须使用该值，避免与自己刚创建的版本发生 CAS 冲突。
这些旧版 CAS 端点继续兼容。当前工作台使用原子提交，`version.id` 也在请求前固定；
相同操作并发或响应丢失重放只会创建一个连续版本，不同正文复用同一幂等键返回
`409 idempotency_conflict`。
`blocks_json` 必须是对象数组，结构块权重范围为 0–120；历史坏形状读取时降级或跳过，
不会原样传给前端。

新版本 `metadata_json` 内嵌 `recipeSchemaVersion = 1`、`recipeHash` 和完整
`recipe`。顶层正负提示词与 `blocks_json` 是便于旧客户端读取的十三块投影；内嵌
Recipe 才是完整恢复的权威。旧版本没有 Recipe 时，服务端会用既有提示词、块和
metadata 生成兼容 Recipe，不伪造不存在的 LoRA、图片或随机轨迹。

## Recipe v1（嵌入版本 metadata）

Recipe 不是新 SQLite 表，而是随每个不可变提示词版本保存的领域对象：

- `kind = anima_prompt_recipe`、`schemaVersion = 1`
- `model`：档案 ID、模型名、精确版本 ID 和验证状态
- `prompts`：中英文正向/负向提示词
- `blocks`：十三个规范块
- `loras`：当次明确选择的 LoRA 快照，含档案 ID、名称/版本、权重、触发词、兼容状态
  和原始链接；不依赖当前目录重新解析
- `parameters`：sampler、scheduler、steps、CFG、resolution、generationSeed 和可选
  denoiseStrength；每项保存值与来源
- `randomPlan`：目录/Hash/抽样器/映射版本、Seed、抽中项、冲突和重抽轨迹
- `instructionHistory`：局部修改、撤销和版本父子关系
- `imageRefs`、`sourceRefs`、`metadata`

规范块 ID 为 `identity`、`appearance`、`clothing`、`expression`、`action`、
`interaction`、`scene`、`camera`、`lighting`、`style`、`effects`、`quality`、
`negative`。工作台保留历史公开 ID，通过一对一适配使用 `subject`、`outfit`、`pose`、
`composition` 和 `artist` 等名称。

## 官网研究与不可变模型档案

四张表共同保存研究证据与档案版本，均属于 Prompt Studio SQLite：

| 表 | 关键字段与约束 |
| --- | --- |
| `model_research_runs` | `id` 主键；原始 `source_url`、状态、错误码和创建/完成时间。 |
| `model_evidence_snapshots` | `run_id` 外键；请求/最终 URL、证据类别、抓取时间、Content-Type、正文 SHA-256、提取文本、抓取状态/错误。 |
| `model_evidence_claims` | `run_id` 外键；字段路径、严格 JSON 值、证据类别、snapshot 引用、理由、验证状态和应用状态。 |
| `model_profile_versions` | `profile_id + revision` 唯一；父版本/研究运行外键、生命周期、档案 JSON、claim 决策、正文 SHA-256、审核说明和时间。 |

`idx_model_profile_one_active` 是 `lifecycle_status='active'` 的局部唯一索引，保证每个
`profile_id` 最多一个活动版本。修订、审核都插入新行并连接
`parent_version_id`，不改旧档案 JSON；激活只在 `BEGIN IMMEDIATE` 事务内把已审核版本
切为 active、旧活动版本切为 superseded，并校验调用方提供的
`expectedActiveVersionId`。`content_sha256` 是规范 JSON 的 SHA-256，读取、审核和激活
都会阻止正文与 Hash 不一致的记录继续流转。

claim 的 `application_status` 初始为 `proposed`，只有 `approved` 的 allowlist 字段
进入档案投影；`rejected` 和未决项仍留在审计轨迹。原始、补充、AI 推断和本地验证
不能互相冒充；人工补充身份字段标记为 `user_supplied`。没有真实 checkpoint/参数
证据时，不得生成 `locally_validated` 或把候选尺寸提升为 `validatedPresets`。

抓取失败或身份不完整的档案保持
`metadata.research.researchStatus="pending_verification"`，缺失字段留空并使用下游通用
规则。活动版本通过现有模型档案校验器投影到统一读取 API，返回不可变
`profileVersionId`、`profileContentSha256`、证据摘要、warnings 和
`generationReady`。Recipe 继续保存当次使用的版本 ID、Hash 和快照，档案后来被替换
不会修改历史 Recipe。

## resources

通用用户资源表。`type=lora_profile` 已用于 LoRA-lite 元数据档案；前端可创建、编辑、
删除并用 `updated_at` 做 CAS。其他“新建资源”、单块素材和持久参考提示词库仍只在
会话或后续范围，不能因 LoRA 这一种类型已接通而视为全部资源链路完成。

关键字段：

- `id`
- `type`
- `name`
- `meta`
- `target_block`
- `en`
- `zh`
- `blocks_json`
- `metadata_json`
- `created_at`
- `updated_at`

LoRA-lite 的 `metadata_json` 保存 `version`、HTTPS `sourceUrl`、`triggerWords`、
`suggestedWeight`（0–2）、`notes`、`compatibleModelProfileIds`、
`conflictLoraProfileIds` 和 `compatibilityNotes`。档案不保存文件路径、文件名或 Hash，
不会扫描、下载、移动或删除 LoRA 文件。删除目录档案不会删除版本 Recipe 中已经内嵌的
LoRA 快照。

## favorites

收藏表。必须区分角色和画师。

关键字段：

- `id`
- `resource_id`
- `type`
- `name`
- `meta`
- `source`
- `source_id`
- `target_block`
- `en`
- `zh`
- `thumb_url`
- `has_image`
- `blocks_json`
- `metadata_json`
- `created_at`
- `updated_at`

其中 `type` 当前至少包含：

- `characters`
- `artists`


补充：`type = snippets` 且携带 `blocks` 数组的收藏用于“结构块配方”——
把当前十三个工作台结构块整组保存；前端从“我的素材”带入时按 `blocks`
逐块回填。该入口通过 favorites API 持久化，未新增表或字段；它不等同于包含模型、
LoRA、参数、来源和图片关联的完整生成配方。

AnimaDex 的真实 slug 可能包含括号、斜杠、井号或百分号。`resource_id` 保留原值，
HTTP 删除使用根据它生成的稳定 `favorite-<hash>` ID；旧收藏也会映射到同一路由 ID，
一次删除会清理同一 `resource_id` 的历史重复行。

## settings

设置表。以 key-value 方式保存。

关键字段：

- `key`
- `value_json`
- `updated_at`

HTTP 设置接口只接受已声明字段和类型。`apiTextKey`、`localTextKey` 仍只保存在本机
SQLite，GET 返回空值与 `*Configured` 标记；空白 PUT 不会清除已有密钥。

原子操作的内部幂等账本复用 `settings` 表并使用保留前缀。它保存请求 Hash 和成功
响应，用于同请求重放；普通设置 GET 会隐藏这些记录，设置 PUT 禁止访问保留前缀，
逻辑备份也排除它们。账本损坏或引用不存在记录时系统失败关闭，不会猜测提交状态。

## 随机词库目录（非 SQLite）

`prototype/data/random-wordlists/v1.json` 是由 `词库原稿/` 确定性生成的当前审查目录，
不是用户数据表。它保存目录/抽样器/映射版本、稳定词条 ID、来源位置、抽取规则和初步
允许结构块，并明确标记 `runtimeReady = false`。当前目录是
`v1-05044da9a25cd532`，规范化 Unicode 版本为 `15.0.0`；在尚未正式发布运行时前，
同一路径可由原稿重建覆盖。实验开关开启时，`random-plan` 会读取该目录并把完整轨迹
保存进 Recipe；正式发布 sampler 前仍必须完成逐条语义审核、建立不可变目录版本历史，
并发布十三块原生 mapping 版本。

## 逻辑备份格式（非 SQLite schema）

全库或单项目导出使用 `anima-prompt-studio-logical-backup` v1 manifest 和严格 JSON
记录。全库可包含 projects/versions、resources、favorites 和安全设置；单项目只包含
目标项目及版本。每个文件记录大小和 SHA-256。

备份明确排除 API Key、接口 URL、内部幂等账本、模型/LoRA 文件、图片和其他二进制
资产；全库备份仍包含安全的 `lora_profile` 元数据记录。
检查阶段不打开数据库；恢复只允许显式非默认目标，现有目标在一个事务中写入，新目标
通过临时库验证后原子替换。HTTP 层始终恢复到 `.prompt-studio-recovery/` 隔离库并返回
`activated=false`。
