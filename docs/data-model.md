# 数据模型

## 数据库结构版本

Prompt Studio SQLite 当前为 schema v1，并使用专用 `application_id` 防止把其他
SQLite 文件误当工作区。首次启动对兼容的旧 v0 库先生成本地精确人工故障回滚副本，
再只登记结构版本；空库直接创建 v1。未来版本、残缺结构、错误应用标识和完整性失败
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
- `loras`
- `parameters`：sampler、scheduler、steps、CFG、resolution、generationSeed 和可选
  denoiseStrength；每项保存值与来源
- `randomPlan`：目录/Hash/抽样器/映射版本、Seed、抽中项、冲突和重抽轨迹
- `instructionHistory`：局部修改、撤销和版本父子关系
- `imageRefs`、`sourceRefs`、`metadata`

规范块 ID 为 `identity`、`appearance`、`clothing`、`expression`、`action`、
`interaction`、`scene`、`camera`、`lighting`、`style`、`effects`、`quality`、
`negative`。工作台保留历史公开 ID，通过一对一适配使用 `subject`、`outfit`、`pose`、
`composition` 和 `artist` 等名称。

## resources

用户资源预留表，目标用于保存“我的素材”、参考提示词库和本地覆盖资源。当前前端的
“新建资源”和结构块“保存为资源”只加入本次页面会话内存，尚未写入该表；持久参考
提示词库的界面也尚未上线。不要把表已存在等同于对应产品链路已接通。

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

备份明确排除 API Key、接口 URL、内部幂等账本、模型、LoRA、图片和其他二进制资产。
检查阶段不打开数据库；恢复只允许显式非默认目标，现有目标在一个事务中写入，新目标
通过临时库验证后原子替换。HTTP 层始终恢复到 `.prompt-studio-recovery/` 隔离库并返回
`activated=false`。
