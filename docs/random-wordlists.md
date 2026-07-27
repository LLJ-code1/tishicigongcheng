# 可复现随机词库

## 当前状态

`词库原稿/` 是随机元素的唯一人工源，共 9 类、471 条。当前已建立可重复的
编译、校验和确定性抽样链路。`GET /api/text/random-catalog` 可读取目录；服务端设置
`PROMPT_STUDIO_EXPERIMENTAL_WORDLISTS=1` 后，`POST /api/text/random-plan` 可按固定
Seed 生成计划，前端把计划写入十三块工作台并随 Recipe 保存。现有
`POST /api/text/random` 仍保留为独立的模型驱动全随机，不等同于词库计划。

编译产物为 `prototype/data/random-wordlists/v1.json`。当前目录版本是
`v1-67192b4d6e8a41b8`，内容 Hash 是
`67192b4d6e8a41b8a6be833bc8a6596b351dd162cab8bdba4b4d84cea2024589`，规范化所用
Unicode 数据版本为 `15.0.0`。其中
`runtimeReady = false`、`semanticReviewRequired = true` 是防止前端或后端误把
未完成语义审查的目录当成正式随机源。当前目录版本和内容 Hash 由源内容自动计算，
执行构建会按当前原稿重建 `v1.json`，并同时在 `catalogs/<version>.json` 写入不可变
副本；旧项目可按已保存版本重开，缺失归档时只禁用重抽。实验接口显式绕过发布门禁，
只供本机验证；不能仅凭实验链路可运行就把两个门禁标记改为完成。

## 构建与校验

```powershell
python scripts/build_random_wordlists.py
python scripts/build_random_wordlists.py --check
```

编译器固定校验：

- 9 个文件、非空类别和构建时记录的实际条数；`--check` 将原稿结果与上次发布目录逐字节比对。
- 严格 UTF-8、无 BOM；v1 prompt profile 的每条词只允许 printable ASCII
  `0x20–0x7E`，同时拒绝 `Cc/Cf/Cs/Co/Cn/Zl/Zp` Unicode 类别、空行、首尾空白和
  提示词分隔符污染。
- 单个源文件最多 128 KiB，单条词的 UTF-8 编码最多 512 字节。
- 使用 Python Unicode `15.0.0` 的 NFKC、大小写折叠和空白归一后保持全库唯一；
  目录写入 `normalizationUnicodeVersion`，环境版本变化会改变产物并由 `--check` 暴露。
- 基于“类别 + 规范化文本 SHA-256”的稳定 `entryId`；移动行号不改变身份。
- 确定性 JSON、内容 Hash、来源文件和来源行号。

CI 会执行 `--check`，原稿变化但忘记重新编译时直接失败。

## 为什么不直接把 471 条塞进模型

按文件名把每条词粗暴映射到单一结构块会产生静默冲突。例如镜头条目可能同时约束
姿势，服装条目可能包含道具，天气可能同时影响场景、光影和特效。工作台和 Recipe
当前目录声明 `mappingVersion = thirteen-block-v1`；九个源文件按主结构块放入
`词库原稿/blocks/<primaryBlock>/`，`clothing_outfit` 原生绑定到 `outfit`。旧的
`ten-block-v1` 归档仍可只读重开；条目 ID 继续只由“类别 + 规范化文本”计算，因此目录重组不改变历史条目身份。

因此正式接入前必须逐条补齐允许结构块、兼容标签和显式互斥组。LLM 只能组织已抽中
元素，不能决定抽什么，也不能无记录地丢弃硬约束。

## 当前实验链路

```text
词库原稿
  → 确定性编译目录
  → 固定 Seed 的 random plan（不调用模型）
  → 冲突检查与确定性重抽
  → 前端按服务端 binding 写入十三块
  → 英文提示词编译（中文先标记待本地 LLM 翻译）
  → Recipe v1 + 完整随机轨迹原子保存
```

当前接口：

- `GET /api/text/random-catalog`：目录版本、类别、数量和抽取规则。
- `POST /api/text/random-plan`：只计算抽取计划，不调用 LLM。
- `POST /api/text/random`：既有模型驱动全随机；当前不接收词库 `randomSpec`。

计划请求中的锁定/重抽 `entryId` 由服务端从指定目录重新解析，不能信任客户端自带
文本、文件路径或结构块映射。目录版本、内容 Hash、抽样器和映射版本不匹配会被拒绝。
可选 `scopeCategoryIds` 只能包含已发布分类；锁定、重抽和条数覆盖也必须落在该作用域，
用于单块骰子而不改变其他结构块。

## Seed 与可复现边界

三种 Seed 不混用：

| 字段 | 决定内容 |
|---|---|
| `librarySeed` | 抽中哪些词条及确定性冲突重抽 |
| `modelSeed` | 可选，仅用于支持 Seed 的文本模型 |
| `generationSeed` | Anima 最终出图 Seed，属于完整配方 |

同一个 `librarySeed` 在当前实验链路中保证 random plan 一致；本地 LLM 中文翻译不属于
该确定性边界。精确恢复依赖保存后的结构块、输出和轨迹；精确复现图片还依赖完整模型、
LoRA、参数和 `generationSeed`。

抽样器固定为 `sha256-counter-v1`，Seed 使用 128 位十六进制字符串。每个类别使用
独立哈希流，使新增一个类别不会改变其他类别的抽取结果；不得依赖 Python
`random.sample()` 的跨版本实现细节。

## 冲突与持久化规则

- 精确重复由编译器拒绝；同义项只通过人工 alias group 管理。
- 硬冲突按同一 Seed 确定性重抽，并保存被拒词条和原因。
- 用户锁定项发生硬冲突时不自动替换，返回冲突供用户选择。
- 软冲突可以保留为创意，但必须展示；模型不得静默丢弃。
- 每个抽中 `entryId` 必须恰好绑定一次；漏词只允许一次修复，仍失败则明确报错。
- 每个项目版本保存目录、映射和抽样器版本、实际抽中项、拒绝记录、冲突决定、绑定块
  以及最终输出。旧目录缺失时仍可打开已保存结果，只禁用重新抽取。

## 迭代顺序

1. **已完成**：严格源校验、稳定 ID、可重复审查目录、CI stale 检查、
   `sha256-counter-v1` sampler、random plan API、锁定/重抽/冲突轨迹、前端 Seed 入口
   和 Recipe 持久化。
2. **当前门禁**：仅服务端实验开关允许抽样；目录仍明确为不可发布状态。
3. 逐条语义审查，先发布 `adult-character-v1`，不开放非人、无人和多人物随机。
4. **已完成**：发布十三块原生 `mappingVersion` 并移除前端对服饰块的硬编码兜底；逐条语义审核和正式 runtime 发布仍未完成。
5. 正式 runtime 发布前建立不可变目录版本历史；旧版本继续用保存的结构块与轨迹恢复。
