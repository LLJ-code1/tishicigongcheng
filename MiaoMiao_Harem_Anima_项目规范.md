# MiaoMiao Harem Anima 提示词项目规范

> 文档版本：1.0  
> 更新日期：2026-06-08  
> 适用模型：MiaoMiao Harem 的 Anima 分支  
> 当前优先版本：`Anima_1.1`  
> 模型页面：https://civitai.com/models/934764/miaomiao-harem

## 1. 文档目的

本文档用于统一项目内 MiaoMiao Harem Anima 模型的提示词写法、模型参数、场景推导方式和质量检查标准。

本文档解决以下问题：

- 中文需求如何整理成模型可理解的提示词。
- 标签、英文自然语言和中文说明分别承担什么作用。
- 如何控制人物位置、动作方向、服装、魔法特效和背景。
- 如何避免复杂动态场景中出现方向错误、构图失控和人体崩坏。
- 如何把一次成功的提示词沉淀为可复用模板。

## 2. 已核验的模型信息

以下内容来自 2026-06-08 获取的 Civitai 模型页面及 API 数据。

### 2.1 版本

模型页面包含多个不同架构的历史版本，不能只根据模型总名称判断提示词格式。

当前 Anima 分支包括：

| 版本 | 基础模型 | 发布日期 | Civitai 版本 ID |
|---|---|---:|---:|
| Anima_1.1 | Anima | 2026-06-04 | 3004063 |
| Anima_Base | Anima | 2026-05-24 | 2967371 |
| Anima_1.0 | Anima | 2026-05-21 | 2963929 |

本项目默认以 `Anima_1.1` 为目标。切换到同页面的 Illustrious、NoobAI 或 Pony 版本时，不能直接沿用本文档的全部参数。

### 2.2 作者说明中的提示词能力

模型支持三种输入方式：

1. 纯 Danbooru 风格标签。
2. 自然语言描述。
3. 标签与英文自然语言混合。

对于简单角色立绘，纯标签已经够用。对于本文这类包含人物位置、攻击方向、腾空姿势、风场和多层魔法效果的复杂场景，项目统一使用混合写法：

```text
质量前缀 + 主体标签 + 构图与动作标签 + 英文自然语言场景描述
```

### 2.3 作者推荐基础参数

| 参数 | 推荐值 |
|---|---|
| Scheduler / Sampler | Euler Normal 或 Euler a Normal |
| Steps | 30 |
| CFG Scale | 4.0-6.0，推荐 5.5 |
| 分辨率范围 | 512² 到 1536² |
| 页面常用推荐 | 768x1024、832x1216、896x1152、1024x1024、920x1536 |

本文场景是横向攻击构图，项目建议从以下横图尺寸开始测试：

```text
1344x768
1536x864
1216x832
```

这些横图尺寸是针对当前场景的项目建议，并非模型作者页面给出的固定横图推荐。

## 3. 标准提示词规范

### 3.1 推荐质量前缀

作者提供的精简推荐前缀：

```text
masterpiece, best quality, score_7, safe,
```

可选增强词：

```text
very aesthetic, ultra-detailed, high contrast,
```

模型页面早期默认示例中存在重复的 `masterpiece, best quality`。项目内应去重，避免无意义占用提示词长度。

项目标准前缀：

```text
masterpiece, best quality, score_7, safe, very aesthetic, ultra-detailed, high contrast,
```

### 3.2 标签书写规则

- 标签尽量使用小写。
- 普通复合标签使用空格，例如 `long pink hair`。
- 不使用 `long_pink_hair` 这类下划线写法。
- `score_7` 等固定质量标签保留下划线。
- 标签之间使用英文逗号分隔。
- 同一概念不要反复堆叠近义标签。
- 重要空间关系需要在自然语言段落中再次明确。

### 3.3 为什么使用英文自然语言

中文可以用于需求记录和人工审阅，但模型执行提示词优先使用英文。英文自然语言主要负责：

- 人物位于画面哪一侧。
- 攻击向哪个方向释放。
- 身体与四肢如何形成动态姿态。
- 风、头发、裙摆和碎片朝哪个方向运动。
- 前景、中景和背景的空间层次。
- 哪些元素不能遮挡脸、手和魔杖。

标签负责“有什么”，自然语言负责“这些东西如何组织在一起”。

### 3.4 标准提示词顺序

```text
1. 质量与内容等级
2. 人物数量和身份
3. 外貌特征
4. 服装
5. 动作和腾空状态
6. 构图与攻击方向
7. 魔法与风场效果
8. 背景
9. 光线、配色和画风
10. 解剖与可见性约束
```

## 4. 场景需求定义

### 4.1 用户明确要求

- 单个成年女性角色。
- 手持魔杖释放冲击波。
- 人物占画面左侧约三分之二。
- 冲击波朝画面右侧释放。
- 人物完整腾空。
- 身旁有被魔力掀起的狂风和魔法效果。
- 超短裙和黑色丝袜。
- 外层衣物被狂风吹起并出现战斗破损。
- 整体参考柔美、梦幻、华丽的日系动漫插画质感。

### 4.2 项目补充的视觉逻辑

- 魔杖与手臂朝右，身体因反作用力略向左后方倾斜。
- 双腿自然弯曲，形成明确腾空感，不能像站在看不见的地面上。
- 头发、丝带、裙摆、披风和碎片统一朝左后方运动。
- 冲击波从魔杖尖端起始，不能从手掌、身体或画外出现。
- 画面右侧必须保留攻击路径，不能被背景人物或大型物体占满。
- 魔法特效围绕攻击主轴组织，不能形成无方向的粒子堆积。
- 服装破损集中在外层披风、宽袖、丝带和装饰布料。
- 内层服装保持完整覆盖，呈现战斗损伤而非裸露效果。
- 角色面部、双手和魔杖必须清晰可辨。

## 5. 当前场景标准成品

### 5.1 中文需求说明

中文版本用于存档、沟通和交给其他 AI 理解，不建议单独作为最终出图提示词。

```text
一幅华丽、梦幻并具有强烈动势的日系二次元魔法战斗插画，采用16:9横向构图。一名明确成年的粉色长发女性魔法师完整腾空，占据画面左侧约三分之二。她以有力量感的对角线姿态悬浮在夜空中，身体被魔法反作用力推向左后方，双腿自然弯曲。她双手握住镶嵌粉紫色水晶的精致魔杖，朝画面右侧释放一道巨大的魔法冲击波。

冲击波从魔杖尖端产生，由粉色、紫色、金色和蓝白色能量构成，向右扩张为螺旋状能量束。能量周围分布多层透明魔法阵、同心压力波、弧形闪电、星屑、花瓣和晶体碎片。右侧保留充足空间，用于表现攻击的方向、速度和规模。

魔力在人物周围掀起螺旋狂风。她的粉色长发、超短百褶裙、丝带、披风和宽大衣袖全部朝左后方猛烈飞扬。外层披风、衣袖和装饰布料受到风暴冲击而破损，碎片被卷入气流并逐渐化作发光粒子；内层白色与淡紫色魔法战斗服保持完整。她穿着黑色半透明丝袜和精致短靴。

背景是夜晚的幻想城市上空，远处有尖塔、漂浮灯火、湖面倒影和烟花般的魔法光芒。粉紫色辉光照亮角色正面，金色轮廓光勾勒身体边缘，蓝白色冲击波在头发、皮肤和衣服上形成反射光。整体具有精致动漫人物绘制、游戏卡面构图、细腻皮肤、丰富衣料层次、电影级体积光和清晰的速度感。人物面部、双手和完整魔杖清晰可见，冲击波不遮挡脸部。
```

### 5.2 英文最终正向提示词

```text
masterpiece, best quality, score_7, safe, very aesthetic, ultra-detailed, high contrast,

1girl, solo, adult woman, magical girl, fantasy mage, full body, airborne, floating, dynamic action pose, diagonal composition, left side composition, long pink hair, flowing hair, green eyes, determined expression, white and lavender magical outfit, very short pleated skirt, black pantyhose, ankle boots, holding an ornate crystal wand with both hands, casting magic, powerful magic blast, magical shockwave, spiraling energy beam, transparent magic circles, concentric pressure waves, curved lightning, strong wind, wind vortex, glowing particles, flower petals, crystal fragments, torn cape, damaged sleeves, fluttering ribbons, fantasy city at night, distant towers, floating lanterns, lake reflections, pink and violet glow, golden rim light, blue-white reflected light, cinematic volumetric lighting,

An adult pink-haired female mage is fully suspended in the night sky and occupies roughly the left two-thirds of a wide 16:9 composition. Her entire body forms a powerful diagonal silhouette, with her torso pushed slightly toward the left rear by magical recoil and both legs naturally bent to make the airborne motion unmistakable. She grips an ornate wand set with a pink-violet crystal in both hands and aims it toward the right side of the frame.

An enormous magical shockwave originates clearly from the tip of the wand and expands toward the right as a brilliant spiral of pink, violet, gold, and blue-white energy. Layered transparent magic circles, concentric pressure waves, curved lightning, sparkling stars, flower petals, and crystal fragments follow the same rightward attack axis. Leave a large area of negative space on the right so the direction, speed, range, and scale of the blast remain clearly readable.

A violent vortex of magical wind surrounds her body. Her long hair, short pleated skirt, ribbons, cape, and oversized sleeves stream forcefully toward the left rear, opposite the direction of the attack. The outer cape, sleeves, and decorative fabric are damaged and tearing apart under the pressure, with loose fragments caught in the wind and dissolving into luminous particles. The inner white and lavender battle outfit remains intact and fully covering. She wears sheer black pantyhose and elegant ankle boots.

The background shows a dreamlike fantasy city beneath a dark starry sky, with distant towers, floating lanterns, reflections across a lake, and firework-like magical lights. Soft pink and violet illumination covers her face and costume, warm golden rim light defines her silhouette, and the blue-white blast casts vivid reflected light across her hair and clothing. Polished Japanese anime illustration, fantasy game-card aesthetics, delicate skin rendering, layered translucent fabric, cinematic lighting, strong motion, clean silhouette, clearly visible face, accurate hands, complete wand, no text or graphic layout.
```

### 5.3 推荐负面提示词

作者提供的基础负面词：

```text
worst quality, low quality, score_1, score_2, score_3, artist name
```

本场景增强版：

```text
worst quality, low quality, score_1, score_2, score_3, artist name, text, watermark, signature, logo, jpeg artifacts, unfinished, blurry, flat lighting, bad anatomy, bad hands, malformed hands, fused fingers, missing fingers, extra fingers, extra arms, extra legs, duplicate character, multiple girls, cropped body, out of frame, broken wand, duplicated wand, floating wand, magic blast from hand, magic blast from body, blast pointing left, weak magic effect, static pose, standing on ground, invisible ground, tangled limbs, distorted face, obscured face, photorealistic, realistic photography, 3d render
```

负面词不是越多越好。如果画面细节被压制，应先移除风格类负面词，再逐步缩短人体类负面词。

## 6. 推荐参数

### 6.1 首轮测试

```yaml
model: MiaoMiao Harem Anima_1.1
resolution: 1344x768
sampler: Euler
scheduler: Normal
steps: 30
cfg: 5.5
batch_size: 4
seed: fixed_for_comparison
```

### 6.2 调整策略

| 问题 | 优先调整 |
|---|---|
| 人物总跑到中间 | 强化 `left side composition` 和英文首段位置描述 |
| 冲击波方向错误 | 强化 `toward the right side of the frame`，加入负面 `blast pointing left` |
| 没有腾空感 | 增加 `fully suspended`、`airborne`、`both legs naturally bent` |
| 风向混乱 | 明确服装和头发朝左后方，攻击朝右 |
| 魔法遮脸 | 强调 `clearly visible face` 和 `blast does not cover her face` |
| 人物过小 | 使用 `character-dominant composition`，减少背景描述 |
| 特效过多变脏 | 删除部分粒子、花瓣或闪电词，只保留两到三类特效 |
| 服装破损过度 | 强调外层破损、内层完整，删除容易导致裸露的词 |
| 画面太静态 | 使用 Euler a Normal，并强化 recoil、diagonal silhouette 和 streaming fabric |
| 画面过于放飞 | 使用 Euler Normal，降低 CFG 到 4.5-5.0，缩短自然语言 |

## 7. 迭代和变体规则

每次只改变一个主要变量，便于判断结果来源。

### 7.1 构图变体

```text
low-angle wide shot
eye-level cinematic shot
dutch angle
three-quarter side view
```

### 7.2 魔法变体

```text
pink-violet arcane shockwave
blue-white frozen energy beam
golden solar magic blast
crimson lightning vortex
```

### 7.3 背景变体

```text
fantasy city above a lake
ruined floating temple
storm clouds above a castle
moonlit flower field in the sky
```

### 7.4 表情变体

```text
determined expression
shouting while casting
calm and focused expression
strained expression under magical recoil
```

## 8. 质量检查清单

生成后按以下顺序检查：

1. 是否只有一个成年女性角色。
2. 人物是否主要位于左侧并占据约三分之二。
3. 是否能看到完整身体，且确实腾空。
4. 魔杖是否完整，并被双手合理握持。
5. 冲击波是否从魔杖尖端向右释放。
6. 右侧是否留有足够攻击空间。
7. 头发、衣物和碎片是否朝左后方运动。
8. 风向与攻击方向是否形成合理反作用关系。
9. 超短裙、黑色丝袜和白紫色战斗服是否清晰。
10. 服装破损是否集中在外层，内层是否完整。
11. 魔法效果是否有层次且没有遮挡面部。
12. 背景是否服务主体，而不是抢夺视觉中心。
13. 是否出现文字、水印、签名或画面排版。
14. 人体、手指、腿部和魔杖是否存在明显结构问题。

## 9. 项目维护规则

- 模型页面总 ID 与具体版本 ID 必须分别记录。
- 每个成功案例记录模型版本、尺寸、采样器、Steps、CFG 和 Seed。
- 模型升级后重新跑固定测试集，不默认沿用旧版本结论。
- 作者推荐参数与项目实测参数分开记录。
- 提示词模板不应绑定某一张参考图的文字、水印或版式。
- 参考图仅抽取颜色、光线、材质、人物精细度和整体氛围。
- 任何复杂提示词都保留中文需求说明和英文执行版本。

## 10. 信息来源与置信度

高置信度、来自模型页面或 API：

- Anima 版本名称、发布日期和版本 ID。
- 支持标签、自然语言和混合写法。
- 推荐前缀。
- 推荐负面词。
- 30 Steps。
- CFG 4.0-6.0，推荐 5.5。
- Euler Normal / Euler a Normal。
- 标签小写、普通复合标签使用空格。

项目建议、需要通过本地出图验证：

- 横图尺寸 1344x768、1536x864、1216x832。
- 当前复杂场景的具体增强负面词。
- 人物占左侧三分之二的自然语言控制方式。
- 风向、反作用力和服装破损的详细描述强度。

