# 数据模型

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

## prompt_versions

提示词版本表。每次生成、应用修改或图生图合并后新增一条版本。

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

版本号由服务端在 `BEGIN IMMEDIATE` 事务中连续分配，不接受客户端指定。
`blocks_json` 必须是对象数组，结构块权重范围为 0–120；历史坏形状读取时降级或跳过，
不会原样传给前端。

## resources

用户资源表。保存我的素材、后续参考提示词库和本地覆盖资源。

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


补充：`type = snippets` 且携带 `blocks` 数组的收藏用于"配方"——
把当前十个结构块整组保存；前端从"我的素材"带入时按 `blocks`
逐块回填。未新增表或字段。

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
