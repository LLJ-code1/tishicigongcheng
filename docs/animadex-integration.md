# AnimaDex 接入说明

## 当前结构

```text
浏览器 -> Prompt Studio 本地网关 :57913 -> AnimaDex API :5000 -> SQLite + 图片目录
```

Prompt Studio 不直接修改 AnimaDex 数据库。角色、画师、缩略图和搜索由 AnimaDex 提供；作品版本、中文释义、收藏、别名和用户素材应由 Prompt Studio 自己保存。

## 资源映射

- 角色 `trigger` -> 主体与角色
- 角色 `tags` -> 外观与服装
- 画师 `trigger` -> 画师与风格
- AnimaDex 英文标签保持原样，中文释义由本地 LLM 按需生成并缓存
- 带入资源只产生待应用修改，用户确认后才生成新版本

## 演示库与完整库

仓库自带 20 个角色和 10 个画师示例。完整目录需要在 animadex.net 获取离线导出令牌，再按 AnimaDex 的 `docs/import-from-site.md` 运行导入。Prompt Studio 不需要再次导入；AnimaDex 数据库更新后刷新资源列表即可。

## Prompt Studio 已保存的覆盖数据

Prompt Studio 已用自己的 SQLite 数据库存储作品、版本、资源、收藏和设置，
不会把这些状态写回 AnimaDex。收藏已区分角色与画师；别名、备注、自定义标签
和译名缓存仍需补齐。

## 后续优化

1. 增加版权、发色、瞳色、性别和画师分类筛选，以及分页或虚拟列表。
2. 补齐中文译名、别名、备注、自定义标签和按原始标签哈希缓存的翻译。
3. 将角色标签进一步拆分到主体、外观、服装和配饰，允许用户预览后选择写入范围。
4. 增加 AnimaDex 导入状态、数据版本、同步时间和更新入口。
