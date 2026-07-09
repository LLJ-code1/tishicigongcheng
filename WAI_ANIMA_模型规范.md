# WAI-ANIMA 模型规范

> 文档版本：1.0  
> 更新日期：2026-06-10  
> 适用模型：WAI-ANIMA  
> 当前优先版本：v1.0(base 1.0)  
> Civitai 页面：https://civitai.red/models/2544636/wai-anima?modelVersionId=2983680  
> Civitai API：https://civitai.red/api/v1/models/2544636

## 1. 文档目的

本文档用于记录 WAI-ANIMA 的模型信息、运行依赖、提示词写法、推荐参数、模型特点和项目内使用规范。

WAI-ANIMA 应作为独立模型 profile 维护，不应直接套用 MiaoMiao Harem Anima、Illustrious、Pony 或 NoobAI 的完整提示词体系。

## 2. 已核验的模型信息

以下信息来自 2026-06-10 获取的 Civitai 页面/API 数据。

| 项目 | 内容 |
|---|---|
| 模型名称 | WAI-ANIMA |
| 模型 ID | 2544636 |
| 作者 | WAI0731 |
| 模型类型 | Checkpoint |
| 当前版本 | v1.0(base 1.0) |
| 当前版本 ID | 2983680 |
| 基础模型 | Anima |
| 基础模型类型 | Standard |
| 发布时间 | 2026-06-04 |
| 页面标记 | base model |
| 页面 NSFW 标记 | 模型 nsfw=false；版本示例 nsfwLevel=2 |
| 作者说明 | Based on BASE 1.0；当前仍处于探索阶段，后续可能修复和改进 |

同一页面还包含旧版本：

| 版本 | 版本 ID | 基础模型 | 发布时间 | 备注 |
|---|---:|---|---|---|
| v1.0(base 1.0) | 2983680 | Anima | 2026-06-04 | 当前优先版本 |
| PW3 | 2859702 | Anima | 2026-04-15 | 基于早期 preview3 思路，提示词前缀不同 |

## 3. 文件与运行环境

作者页面明确要求使用：

```text
ComfyUI or Forge Neo
```

当前版本文件列表：

| 文件 | 类型 | 备注 |
|---|---|---|
| waiANIMA_v10Base10.safetensors | Model | 主 checkpoint |
| waiANIMA_v10Base10_txt.safetensors | Text Encoder | Civitai 版本文件列表中的文本编码器 |
| qwen_image_vae.safetensors | VAE | 必需 VAE |

作者页面文案同时提到需要下载：

```text
qwen_3_06b_base.safetensors
qwen_image_vae.safetensors
```

项目内使用时，以实际工作流需要的文本编码器文件名为准，但必须确认文本编码器和 VAE 都已正确加载。WAI-ANIMA 不应按普通 SDXL checkpoint 的方式只放一个模型文件就直接运行。

## 4. 官方推荐参数

当前版本 v1.0(base 1.0)：

```yaml
model: WAI-ANIMA v1.0(base 1.0)
model_version_id: 2983680
steps: 20-30
cfg_scale: 4-5
sampler: Euler A
scheduler: Normal
```

作者示例图设置：

```yaml
resolution: 1024x1344
hires_upscale: 1.5
hires_steps: 20
hires_upscaler: R-ESRGAN 4x+ Anime6B
denoising_strength: 0.35-0.5
```

注意：作者明确提示 Hires upscale 不要超过 1.5x。

旧版本 PW3 / BASE ANIMA preview3 的页面段落中还出现：

```yaml
sampler: Euler A Normal OR ER SDE BETA
positive_prefix: masterpiece, best quality, score_9, score_8, score_7,
```

这不应直接覆盖当前 v1.0(base 1.0) 的写法。当前优先版本的官方正向前缀是 `score_7`，不是 Pony/Illustrious 常见的完整 `score_9, score_8_up` 体系。

## 5. 官方提示词写法

当前版本官方正向前缀：

```text
masterpiece, best quality, score_7,
```

当前版本官方负面提示词：

```text
worst quality, low quality, score_1, score_2, score_3, artist name, blurry, jpeg artifacts, lowres, censor
```

项目内建议保留官方前缀的简洁性，不默认加入过长质量词堆叠。可以按场景少量增加画面目标，例如：

```text
detailed anime illustration, soft lighting, clean composition
```

但不要把 WAI-ANIMA 强行写成 Pony、Illustrious 或 MiaoMiao Harem Anima 的完整前缀。

## 6. 项目推荐提示词结构

WAI-ANIMA 仍适合使用 tag 与自然语言混合写法，但整体长度应比复杂 SDXL tag 流更克制。

推荐结构：

```text
质量前缀,
主体数量与身份,
人物外观,
服装,
姿态与动作,
构图与镜头,
背景与光线,
1-3 句英文自然语言补充空间关系和可见性
```

简单角色图可以主要依赖 tag：

```text
masterpiece, best quality, score_7,
1girl, solo, adult woman, long hair, looking at viewer, standing, full body, simple background, soft lighting
```

复杂动作、多人关系、左右方向、前中后景关系，需要加入英文自然语言。不要只靠 tag 表达空间关系。

```text
The main subject stands in the foreground on the left side, while the second character is placed slightly behind her on the right. Their faces and hands remain unobstructed, with clear separation between foreground and background.
```

## 7. 多人场景写法

WAI-ANIMA 可以尝试多人场景，但必须降低冲突密度。多人提示词不要写成一整串无法归属的服装和动作。

推荐写法：

```text
2girls, two adult women, clear two-person composition,
woman on the left, woman on the right,
distinct outfits, distinct hair colors, clear pose separation,
```

然后用自然语言绑定每个角色：

```text
The woman on the left is the main subject and stands closer to the camera. The woman on the right supports her pose from the side. Their limbs do not overlap in a confusing way, and both faces remain visible.
```

多人场景原则：

1. 每个角色都要有位置。
2. 每个角色的动作要绑定到角色。
3. 尽量减少相似发色、相似服装和相似姿态。
4. 手、脚、道具、脸部需要明确可见性约束。
5. 如果人物关系复杂，优先减少背景细节。

## 8. 模型特点与项目判断

高置信度，来自作者页面/API：

- WAI-ANIMA 是 WAI0731 发布的 Anima checkpoint。
- 当前优先版本为 `v1.0(base 1.0)`，版本 ID 为 `2983680`。
- 作者说明该版本基于 BASE 1.0，仍处于探索阶段。
- 需要 ComfyUI 或 Forge Neo。
- 需要文本编码器和 Qwen Image VAE。
- 推荐 Steps 为 20-30，CFG 为 4-5。
- 当前版本官方正向前缀为 `masterpiece, best quality, score_7,`。
- 当前版本官方负面词包含 `score_1, score_2, score_3, artist name, blurry, jpeg artifacts, lowres, censor`。

中等置信度，来自外部实测文章和社区迁移记录：

- WAI-ANIMA 相比 Anima preview3-base，tag 响应和角色一致性更好。
- 相比成熟的 WAI-Illustrious，WAI-ANIMA 的精确 tag 控制仍较弱。
- WAI-ANIMA 更擅长氛围、背景纵深、动态构图和绘画感光影。
- 对复杂服装、固定角色设定和高一致性批量生产，Illustrious 生态仍更成熟。
- 自然语言对 layout、角色关系和空间组织有帮助，但不应写得过长。

项目判断：

WAI-ANIMA 适合用于氛围型二次元单图、动态姿态、电影感光照、幻想场景和中等复杂度多人构图。不适合作为高度可控角色设定图、严格换装测试或大批量角色一致性生产的唯一模型。

## 9. 与 MiaoMiao Harem Anima 的区别

| 项目 | WAI-ANIMA | MiaoMiao Harem Anima |
|---|---|---|
| 页面/作者 | WAI0731 | MiaoMiao Harem 页面 |
| 当前项目版本 | v1.0(base 1.0) | Anima_1.1 |
| 当前版本 ID | 2983680 | 3004063 |
| 官方前缀 | `masterpiece, best quality, score_7,` | `masterpiece, best quality, score_7, safe,` 可增强 |
| 推荐 CFG | 4-5 | 4-6，项目常用 5.5 |
| 推荐 Steps | 20-30 | 30 |
| 依赖 | 明确需要文本编码器和 Qwen Image VAE | 按具体工作流确认 |
| 项目定位 | 氛围、动态、绘画感、WAI 调校 | 现有魔法战斗场景主 profile |

不要因为二者都属于 Anima 生态，就混用全部参数和前缀。

## 10. 项目标准模板

### 10.1 中文需求记录模板

```text
画面主体：
人物数量：
角色关系：
服装：
姿态动作：
构图位置：
背景：
光线与氛围：
必须清晰可见：
不希望出现：
```

### 10.2 英文正向模板

```text
masterpiece, best quality, score_7,

[subject count], [character identity], [appearance tags], [outfit tags], [pose tags], [composition tags], [background tags], [lighting tags],

[Natural language sentence 1: character positions and camera angle.]
[Natural language sentence 2: action relationship and limb/prop visibility.]
[Natural language sentence 3: background separation, lighting, and clarity constraints.]
```

### 10.3 负面模板

```text
worst quality, low quality, score_1, score_2, score_3, artist name, blurry, jpeg artifacts, lowres, censor, text, watermark, signature, bad anatomy, bad hands, bad feet, extra fingers, missing fingers, extra limbs, fused limbs, distorted face, obscured face, duplicate character, cropped body, out of frame
```

负面词应按场景裁剪，不要每次无限追加。多人场景优先增加肢体和人物重复相关负面词；纯头像或半身图可以删掉脚部、腿部类负面词。

## 11. 调整策略

| 问题 | 优先调整 |
|---|---|
| 构图不听话 | 用英文自然语言明确 left/right/foreground/background |
| 多人角色混淆 | 给每个角色绑定位置、发色、服装和动作 |
| 服装串到另一个角色 | 减少角色数量或让两个角色服装颜色明显区分 |
| 动作姿态失败 | 缩短背景描述，强化 pose tag 与一条自然语言动作句 |
| 手脚错误 | 增加 `clear hands, clear feet, accurate limbs`，减少遮挡动作 |
| 画面太乱 | 降低背景、粒子、装饰和道具数量 |
| 细节发糊 | Steps 提到 30，使用 Hires 1.5x 以内，denoise 0.35-0.5 |
| 风格过硬或过曝 | 减少额外质量词，CFG 保持 4-4.5 |
| 角色一致性不足 | 固定 seed，减少变体变量，必要时考虑 LoRA 或参考图工作流 |

## 12. 维护规则

- 每次使用必须记录模型版本 ID，不只写 WAI-ANIMA。
- 当前优先版本为 `2983680 / v1.0(base 1.0)`。
- `PW3 / 2859702` 的 `score_9, score_8, score_7` 前缀只能作为旧版本记录，不自动继承到当前版本。
- 作者页面参数与项目实测参数分开记录。
- 对多人场景保留中文需求记录和英文执行版本。
- 如果模型升级，重新跑固定测试集，不默认沿用本文件结论。
- 若使用外部社区文本编码器或额外 LoRA，必须单独记录来源和权重。

## 13. 信息来源

高置信度来源：

- WAI-ANIMA Civitai 页面：https://civitai.red/models/2544636/wai-anima?modelVersionId=2983680
- WAI-ANIMA Civitai API：https://civitai.red/api/v1/models/2544636
- Anima 官方 preview3-base 链接由作者页面引用：https://civitai.com/models/2458426/anima-official?modelVersionId=2836417

中等置信度来源，仅作实测参考：

- WAI-Anima v1 vs WAI-Illustrious on M1 Max ComfyUI：https://lilting.ch/en/articles/wai-anima-anima-ecosystem-evolution
- SD WebUI Reforge 到 Forge Neo / WAI-ANIMA 迁移记录：https://note.com/moribro/n/nf35d19554803
- WAI-ANIMA v1.0 使用记录：https://note.com/takeshi3868/n/nb88eda7ab659
- ANIMA preview3 base 的 WAI-ANIMA 观察：https://note.com/nobinlog/n/n69fd4eaf475a
