# Anima-Style-Explorer 接入评估

> 评估日期：2026-06-11
> 本地路径：`H:\提示词工程\Anima-Style-Explorer`
> 仓库：`ThetaCursed/Anima-Style-Explorer`
> 当前提交：`afdf2abc605a58c0c1e2a3c7e55f9775e17f0cba`
> 许可证：MIT

## 1. 项目定位

Anima-Style-Explorer 不是模型、训练脚本或通用提示词生成器，而是一个面向 Anima 2B 的画师风格可视化索引工具。

它的价值在于把 Danbooru artist tag 从纯文本词表变成可检索、可预览、可收藏、可导出的风格参考库。对我们的提示词工程来说，它适合进入「模型 Profile / 风格 Profile / 标签词典」层，而不是替代现有的场景理解、提示词适配器或负面词规则。

## 2. 核心资产

仓库内有两类最重要的数据：

1. `app/data.js`
   - 40,600 条可视化画师风格记录。
   - 字段包括 `id`、`name`、`post_count`、`p`、`uniqueness_score`。
   - 前端会转换成 `artist`、`worksCount`、`image`、`uniquenessRank` 等运行时字段。
   - 预览图并不存放在本仓库，而是通过 jsDelivr 访问 `ThetaCursed/Anima-Assets`。

2. `Anima2B_Artist_Index_59k.txt`
   - 59,676 个 Anima 2B 训练快照中的 Danbooru artist tag。
   - 文档说明其用途是固定快照，不建议依赖实时 Danbooru tag。
   - 适合做离线检索、tag 校验、自动补全和候选推荐。

## 3. 与当前提示词工程的契合点

当前项目文档已经强调：

- 中文需求用于记录和沟通。
- LLM 负责理解意图、补全场景、输出结构化数据。
- 确定性代码负责把结构化数据转换为不同模型的提示词。
- 模型配置、负面词、变体策略应独立管理。

Anima-Style-Explorer 可以补上的正是「画师风格」这一层：

```text
SceneSpec
  -> Normalize / Validate
  -> ModelProfile
  -> StyleProfile  <- 从 Anima-Style-Explorer 提取
  -> PromptAdapter
  -> PromptResult
```

建议把 artist tag 不直接塞进自由文本，而是进入独立字段：

```json
{
  "style": {
    "artist_tags": [
      {
        "tag": "@dairi",
        "source": "Anima-Style-Explorer",
        "works_count": 15864,
        "uniqueness_score": 4.12,
        "confidence": "model_snapshot"
      }
    ],
    "style_intent": "soft anime illustration, clean linework"
  }
}
```

这样后续适配不同模型时，可以决定是否保留 `@artist` 语法、是否降权、是否改写为普通风格描述。

## 4. 推荐接入方式

### P0：作为本地参考工具使用

直接打开 `index.html` 或启动静态服务，人工搜索、收藏、导出 `.txt`，再把画师 tag 放入实验记录。

适合现在马上用：

- 找相近风格参考。
- 比较高样本量与高独特性画师的效果差异。
- 给同一个 SceneSpec 做多组 artist tag A/B 测试。

### P1：抽取为机器可读数据

从 `app/data.js` 提取为 `jsonl` 或 `sqlite`：

```json
{"id":"87540","tag":"@dairi","name":"dairi","works_count":15864,"partition":1,"uniqueness_score":4.12}
```

用途：

- Prompt UI 的 artist 自动补全。
- 校验用户输入的 artist 是否在 Anima 2B 训练快照内。
- 按 `works_count` 推荐稳定风格，按 `uniqueness_score` 推荐差异化风格。
- 给每次出图记录保存所用 artist tag 和来源版本。

### P2：纳入 ModelProfile

在 Anima 系模型的 profile 中增加：

```yaml
artist_style_source:
  repo: ThetaCursed/Anima-Style-Explorer
  commit: afdf2abc605a58c0c1e2a3c7e55f9775e17f0cba
  visual_index_count: 40600
  text_index_count: 59676
  prompt_syntax: "@artist-name"
```

这样模型升级或替换时，不会把旧快照的画师 token 误认为所有 Anima 系 checkpoint 都稳定支持。

## 5. 使用原则

建议将画师 tag 放在质量前缀之后、主体和场景之前：

```text
masterpiece, best quality, score_7, safe,
@artist_name,
1girl, solo, ...
```

但要保留三条约束：

1. 一次实验最多改变 1-2 个 artist tag，避免无法判断效果来源。
2. 高 `works_count` 通常更稳定，高 `uniqueness_score` 更适合探索明显风格差异。
3. artist tag 是风格影响，不应承担构图、动作、空间关系和负面约束的职责。

## 6. 风险与边界

- 该仓库说明它是第三方独立整理资源，不是 Anima 官方资料。
- 预览图来自远程资产仓库，本地离线打开时如果没有网络，核心 UI 可用，但预览图可能无法加载。
- `Anima2B_Artist_Index_59k.txt` 声称是训练快照，适合作为项目内固定版本资源，但仍应记录来源 commit。
- 不建议把画师 tag 默认加入所有提示词。它应该是可选风格层，并且每次实验记录清楚。
- 现有 UI 里的收藏数据保存在浏览器 IndexedDB，不适合直接当团队共享数据源；团队共享应使用导出的 txt/json 或自行抽取数据表。

## 7. 下一步建议

1. 先用该工具人工筛 20-50 个适合当前项目审美的画师 tag。
2. 建立 `style_profiles/anima_artist_styles.jsonl`，记录 tag、样本量、独特性、人工备注、测试结果。
3. 在 PromptResult 中增加 `style_trace`，记录每个 artist tag 的来源和版本。
4. 做固定 SceneSpec 的 A/B 测试：无 artist、单 artist、高样本 artist、高独特性 artist、双 artist 混合。
5. 只有通过本地出图验证的 artist tag，才进入项目推荐预设。
