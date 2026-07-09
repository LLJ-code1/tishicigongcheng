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

## settings

设置表。以 key-value 方式保存。

关键字段：

- `key`
- `value_json`
- `updated_at`
