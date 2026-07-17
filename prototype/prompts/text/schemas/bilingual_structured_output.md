# 双语结构化输出

只输出一个严格 JSON 对象，不要 Markdown、代码块、解释、前后缀或思考内容。总提示词由后端从结构块编译，你只需输出以下字段：

```json
{
  "relationEn": "",
  "relationZh": "",
  "blocks": [
    {"id": "quality", "en": "", "zh": "", "confidence": 100},
    {"id": "artist", "en": "", "zh": "", "confidence": 100},
    {"id": "subject", "en": "", "zh": "", "confidence": 100},
    {"id": "appearance", "en": "", "zh": "", "confidence": 100},
    {"id": "outfit", "en": "", "zh": "", "confidence": 100},
    {"id": "expression", "en": "", "zh": "", "confidence": 100},
    {"id": "pose", "en": "", "zh": "", "confidence": 100},
    {"id": "interaction", "en": "", "zh": "", "confidence": 100},
    {"id": "scene", "en": "", "zh": "", "confidence": 100},
    {"id": "composition", "en": "", "zh": "", "confidence": 100},
    {"id": "lighting", "en": "", "zh": "", "confidence": 100},
    {"id": "effects", "en": "", "zh": "", "confidence": 100},
    {"id": "negative", "en": "", "zh": "", "confidence": 100}
  ],
  "checks": {
    "preservedUserIntent": true,
    "bilingualAligned": true,
    "conflicts": [],
    "assumptions": []
  }
}
```

十三个 `id` 必须使用上述字符串并保持顺序，不能用数字。`quality` 和 `negative` 可留空，由后端注入固定内容。每块 `en` 与 `zh` 必须同时为空或同时有内容；`artist` 没有明确来源时为空。`conflicts` 和 `assumptions` 必须是字符串数组。

同一个视觉事实只能归入一个最合适的结构块，不能在其他块重复或改写同义词再次出现：服装款式、颜色和材质只进 `outfit`，不进 `subject` 或 `appearance`；眼睛开合、嘴部和情绪只进 `expression`，不进 `pose`；雨滴、飞溅、涟漪、雾和粒子只进 `effects`，不进 `scene` 或 `lighting`。需要跨块表达空间关系时写入 `relationEn` / `relationZh`，不要复制标签。
