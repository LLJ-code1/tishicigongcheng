# 元素词库原稿

真随机抽卡的骰子面。原则：**熵由代码产生，LLM 只负责把抽中的元素圆成一个成立的画面**。
本目录是唯一人工源。`scripts/build_random_wordlists.py` 会把它严格校验并编译为
`prototype/data/random-wordlists/v1.json`；该产物当前明确标记为
`runtimeReady = false`、`semanticReviewRequired = true`。它已接入受
`PROMPT_STUDIO_EXPERIMENTAL_WORDLISTS=1` 保护的 `POST /api/text/random-plan`，但
不改变既有模型驱动的 `POST /api/text/random`，也不代表已通过正式发布审核。
完整接入约定见 [可复现随机词库](../docs/random-wordlists.md)。

## 文件与类型

| 文件 | 类型 | 条数 | 建议抽取数 |
|---|---|---|---|
| theme_mood.txt | 题材/氛围 | 50 | 1 |
| scene_environment.txt | 场景/环境 | 70 | 1 |
| pose_action.txt | 姿势/动作 | 60 | 1 |
| clothing_outfit.txt | 服装 | 60 | 1 |
| composition_camera.txt | 构图/镜头 | 45 | 1 |
| lighting_color.txt | 光影/色彩 | 50 | 1 |
| effects_props.txt | 特效/道具 | 56 | 1–2 |
| weather_time.txt | 天气/时间 | 30 | 0–1（50% 概率参与） |
| appearance_traits.txt | 外貌特征 | 50 | 0–2 |

角色和画师两类不在本目录：运行时从 AnimaDex / 画师收藏抽取。

## 格式约定

- 每行一条，纯英文，可直接进 Anima 混合格式提示词
- 当前词库只维护 SFW 内容；内容模式及其安全边界仍待单独设计，仓库目前没有 NSFW
  确定性注入脚本
- 无已知 IP、画师名、品牌（与 `generate_random_text_prompt` 现有禁令一致）

## 构建检查

```powershell
python scripts/build_random_wordlists.py
python scripts/build_random_wordlists.py --check
```

编译器固定检查 9 类、471 条、严格 UTF-8、v1 profile 的 printable ASCII
`0x20–0x7E` 限制、`Cc/Cf/Cs/Co/Cn/Zl/Zp` Unicode 类别、空白/分隔符污染、128 KiB
单文件上限、512 字节单条上限和全库规范化重复。规范化 Unicode 版本为 `15.0.0`，
当前目录版本为 `v1-05044da9a25cd532`。词条身份由类别和规范化文本 Hash 决定，不
使用行号。

当前 `v1.json` 是实验链路使用的可重建审查产物，构建会按原稿覆盖它。正式发布
sampler 前必须完成逐条语义审核并建立不可变版本历史，避免旧项目引用的目录被原地
替换。

## 运行规则

1. 使用冻结的 `sha256-counter-v1` 抽样器和 128 位十六进制 `librarySeed`；不依赖
   Python `random.sample()` 的跨版本行为。
2. 抽中的元素作为**硬约束**进入 blueprint：LLM 可以补充细节，但不得替换或静默
   丢弃；服务端必须核对每个 `entryId` 的结构块绑定。
3. 硬冲突由代码确定性重抽并记录；锁定项冲突交给用户选择。软冲突可以保留为创意，
   但必须展示，不能让 LLM 自行舍弃。
4. 抽样器、目录、映射版本、Seed、抽中项、拒绝项、冲突决定和最终输出都进入完整
   Recipe。工作台已使用十三块；目录仍声明 `ten-block-v1`，前端把服装兼容映射到
   `outfit`，正式发布前需新增十三块原生映射版本。

## 维护建议

- 每类保持 30–80 条：太少会重复，太多稀释质量
- 出图后觉得"这个元素总出效果"→ 保留；"这个元素画不出来"→ 删掉或改写
- 新条目标准：必须是**可见的画面事实**，不收抽象概念（"美丽""高级感"不收）
