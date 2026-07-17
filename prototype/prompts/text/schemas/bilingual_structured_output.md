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
