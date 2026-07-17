"""Prompt assembly and OpenAI-compatible text generation for Prompt Studio."""

from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from unicode15_data import is_unicode15_assigned, lowercase_unicode15


ROOT = Path(__file__).resolve().parent
PROMPT_ROOT = ROOT / "prompts" / "text"
TEXT_MODULES = {
    "core": PROMPT_ROOT / "core" / "visual_expansion.md",
    "mode": PROMPT_ROOT / "modes" / "expand.md",
    "format": PROMPT_ROOT / "formats" / "hybrid.md",
    "anima": PROMPT_ROOT / "models" / "anima.md",
    "schema": PROMPT_ROOT / "schemas" / "bilingual_structured_output.md",
    "decompose": PROMPT_ROOT / "modes" / "decompose.md",
}
REQUIRED_BLOCK_IDS = (
    "quality",
    "artist",
    "subject",
    "appearance",
    "outfit",
    "expression",
    "pose",
    "interaction",
    "scene",
    "composition",
    "lighting",
    "effects",
    "negative",
)
LEGACY_BLOCK_IDS = (
    "quality",
    "artist",
    "subject",
    "appearance",
    "pose",
    "scene",
    "composition",
    "lighting",
    "effects",
    "negative",
)
RANDOM_VARIANT_BLOCK_IDS = {
    "subject",
    "appearance",
    "outfit",
    "expression",
    "pose",
    "interaction",
    "scene",
    "composition",
    "lighting",
    "effects",
}
PROMPT_WHITESPACE_PATTERN = re.compile(
    "[\\u0009-\\u000d\\u0020\\u0085\\u00a0\\u1680"
    "\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff]+"
)
PROMPT_EDGE_WHITESPACE_PATTERN = re.compile(
    "^[\\u0009-\\u000d\\u0020\\u0085\\u00a0\\u1680"
    "\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff]+|"
    "[\\u0009-\\u000d\\u0020\\u0085\\u00a0\\u1680"
    "\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff]+$"
)
BLOCK_LABELS = {
    "quality": "质量与基础修饰",
    "artist": "画师与风格",
    "subject": "主体与角色",
    "appearance": "外貌与服装",
    "outfit": "服装与配饰",
    "expression": "表情与视线",
    "pose": "动作与姿态",
    "interaction": "角色与物体互动",
    "scene": "场景与环境",
    "composition": "构图与镜头",
    "lighting": "光影与色彩",
    "effects": "特效与道具",
    "unclassified": "其他 / 未分类",
    "negative": "负面提示词",
}
DECOMPOSITION_BLOCK_IDS = (
    "quality",
    "artist",
    "subject",
    "appearance",
    "outfit",
    "expression",
    "pose",
    "interaction",
    "scene",
    "composition",
    "lighting",
    "effects",
    "unclassified",
    "negative",
)
ANIMA_QUALITY = {
    "en": "masterpiece, best quality, score_7",
    "zh": "杰作，最佳质量，score_7",
}
ANIMA_NEGATIVE = {
    "en": (
        "worst quality, low quality, score_1, score_2, score_3, "
        "artist name, blurry, jpeg artifacts, lowres, censor"
    ),
    "zh": (
        "最差质量，低质量，score_1，score_2，score_3，"
        "画师名称，模糊，JPEG 压缩痕迹，低分辨率，审查遮挡"
    ),
}
GENERATION_PROFILES = {
    "strict": {"temperature": 0.3, "max_tokens": 1800, "minimum_items": 0},
    "balanced": {"temperature": 0.5, "max_tokens": 2400, "minimum_items": 25},
    "creative": {"temperature": 0.7, "max_tokens": 3000, "minimum_items": 35},
}
Transport = Callable[[str, dict, dict, float], dict]


@dataclass
class PromptEngineError(Exception):
    message: str
    code: str = "prompt_engine_error"
    status: int = 500

    def __str__(self) -> str:
        return self.message


def read_module(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise PromptEngineError(
            f"提示词模块无法读取：{path.name}",
            code="prompt_module_error",
        ) from error


def build_text_expand_system_prompt(
    target_model: str = "anima", custom_instructions: str = ""
) -> str:
    model_id = str(target_model or "anima").strip().lower()
    if model_id != "anima":
        raise PromptEngineError(
            f"暂不支持目标模型：{target_model}",
            code="unsupported_model",
            status=400,
        )
    parts = [
        read_module(TEXT_MODULES["core"]),
        read_module(TEXT_MODULES["mode"]),
        read_module(TEXT_MODULES["format"]),
        read_module(TEXT_MODULES[model_id]),
        read_module(TEXT_MODULES["schema"]),
    ]
    if custom_instructions.strip():
        parts.append(
            "# 用户自定义补充规则\n\n"
            + custom_instructions.strip()
        )
    return "\n\n---\n\n".join(parts)


def build_text_decompose_system_prompt() -> str:
    return read_module(TEXT_MODULES["decompose"])


def chat_completions_url(base_url: str) -> str:
    normalized = str(base_url or "").strip().rstrip("/")
    if not normalized:
        return ""
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def resolve_text_provider(settings: dict, provider: str) -> dict:
    provider_id = str(provider or "local").strip().lower()
    if provider_id == "local":
        base_url = settings.get("localTextUrl")
        model = settings.get("localTextModel")
        api_key = str(settings.get("localTextKey") or "").strip()
    elif provider_id == "api":
        base_url = settings.get("apiTextUrl")
        model = settings.get("apiTextModel")
        api_key = str(settings.get("apiTextKey") or "").strip()
    else:
        raise PromptEngineError(
            "不支持的文本推理来源",
            code="unsupported_provider",
            status=400,
        )

    url = chat_completions_url(str(base_url or ""))
    model = str(model or "").strip()
    if not url or not model or (provider_id == "api" and not api_key):
        label = "本地 LLM" if provider_id == "local" else "外部 API"
        raise PromptEngineError(
            f"{label} 尚未配置完整，请填写接口地址、模型名称"
            + ("和 API Key" if provider_id == "api" else ""),
            code="provider_not_configured",
            status=400,
        )

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return {
        "provider": provider_id,
        "url": url,
        "model": model,
        "headers": headers,
    }


def apply_provider_request_options(config: dict, body: dict) -> dict:
    request = dict(body)
    if "api.deepseek.com" in config.get("url", "").casefold():
        request["thinking"] = {"type": "disabled"}
        if request.get("max_tokens", 0) > 8:
            request["response_format"] = {"type": "json_object"}
    return request


def probe_text_provider(
    settings: dict,
    provider: str,
    transport: Transport | None = None,
    timeout: float = 30,
) -> dict:
    config = resolve_text_provider(settings, provider)
    caller = transport or default_transport
    started = time.monotonic()
    probe_message = "Reply with exactly: OK"
    if provider == "local":
        probe_message = f"/no_think\n{probe_message}"
    body = apply_provider_request_options(
        config,
        {
            "model": config["model"],
            "messages": [
                {
                    "role": "user",
                    "content": probe_message,
                }
            ],
            "temperature": 0,
            "max_tokens": 8,
            "stream": False,
        },
    )
    response = caller(
        config["url"],
        body,
        config["headers"],
        timeout,
    )
    content = response_content(response)
    return {
        "connected": True,
        "provider": config["provider"],
        "model": config["model"],
        "content": content,
        "latencyMs": round((time.monotonic() - started) * 1000),
    }


def default_transport(
    url: str, body: dict, headers: dict, timeout: float
) -> dict:
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise PromptEngineError(
            f"模型接口返回 HTTP {error.code}：{detail[:300]}",
            code="upstream_http_error",
            status=502,
        ) from error
    except (URLError, TimeoutError, OSError) as error:
        raise PromptEngineError(
            "无法连接文本模型，请确认模型服务已启动且接口地址正确",
            code="model_unavailable",
            status=503,
        ) from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PromptEngineError(
            "模型接口没有返回有效 JSON",
            code="upstream_invalid_response",
            status=502,
        ) from error


def response_content(response: dict) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise PromptEngineError(
            "模型响应缺少 choices[0].message.content",
            code="upstream_invalid_response",
            status=502,
        ) from error
    if not isinstance(content, str) or not content.strip():
        raise PromptEngineError(
            "模型返回了空内容",
            code="upstream_invalid_response",
            status=502,
        )
    cleaned = content.strip()
    if re.search(r"</think>", cleaned, flags=re.IGNORECASE):
        cleaned = re.sub(
            r"^.*?</think>\s*",
            "",
            cleaned,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()
    else:
        cleaned = re.sub(
            r"<think>.*?</think>\s*",
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()
    if not cleaned:
        raise PromptEngineError(
            "模型只返回了思考内容，没有可用结果",
            code="upstream_invalid_response",
            status=502,
        )
    return cleaned


def parse_model_json(content: str) -> dict:
    text = content.strip()
    fenced = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL
    )
    if fenced:
        text = fenced.group(1).strip()
    elif "{" in text and "}" in text:
        text = text[text.find("{") : text.rfind("}") + 1].strip()
    in_string = False
    escaped = False
    cleaned_chars = []
    for character in text:
        if character == "\u0332" and not in_string:
            continue
        cleaned_chars.append(character)
        if escaped:
            escaped = False
        elif character == "\\" and in_string:
            escaped = True
        elif character == '"':
            in_string = not in_string
    text = "".join(cleaned_chars)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise PromptEngineError(
            "模型输出不是严格 JSON",
            code="invalid_model_output",
            status=502,
        ) from error
    if not isinstance(payload, dict):
        raise PromptEngineError(
            "模型输出必须是 JSON 对象",
            code="invalid_model_output",
            status=502,
        )
    return payload


def clean_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def split_prompt_items(value: str) -> list[str]:
    items = [
        PROMPT_EDGE_WHITESPACE_PATTERN.sub("", item)
        for item in re.split(r"[,，;；\n]+", value)
    ]
    return [item for item in items if item]


def prompt_item_key(value: str) -> str:
    """Match the browser's frozen Unicode-15 prompt deduplication."""

    output: list[str] = []
    assigned_run: list[str] = []

    def flush_assigned_run() -> None:
        if not assigned_run:
            return
        normalized_run = unicodedata.normalize("NFKC", "".join(assigned_run))
        output.append(lowercase_unicode15(normalized_run).replace("ß", "ss"))
        assigned_run.clear()

    for character in str(value or ""):
        if is_unicode15_assigned(ord(character)):
            assigned_run.append(character)
        else:
            flush_assigned_run()
            output.append(character)
    flush_assigned_run()
    normalized = "".join(output)
    collapsed = PROMPT_WHITESPACE_PATTERN.sub(" ", normalized).strip(" ")
    return collapsed


def source_item_key(value: str) -> str:
    return prompt_item_key(value)


def contains_cjk(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", value))


def merge_fixed_prompt(fixed: str, model_value: str, separator: str) -> str:
    fixed_items = split_prompt_items(fixed)
    seen = {prompt_item_key(item) for item in fixed_items}
    extras = []
    for item in split_prompt_items(model_value):
        normalized = prompt_item_key(item)
        if normalized not in seen:
            extras.append(item)
            seen.add(normalized)
    return separator.join([*fixed_items, *extras])


def compile_normalized_blocks(
    blocks: list[dict], relation_en: str, relation_zh: str
) -> dict:
    positive_blocks = [block for block in blocks if block["id"] != "negative"]

    def unique_items(language: str) -> list[str]:
        seen = set()
        values = []
        for block in positive_blocks:
            for item in split_prompt_items(block[language]):
                normalized = prompt_item_key(item)
                if normalized in seen:
                    continue
                seen.add(normalized)
                values.append(item)
        return values

    positive_en = ", ".join(unique_items("en"))
    positive_zh = "；".join(unique_items("zh"))
    negative = blocks[-1]
    return {
        "positiveEn": ". ".join(
            value for value in (positive_en, relation_en) if value
        ),
        "positiveZh": "。".join(
            value for value in (positive_zh, relation_zh) if value
        ),
        "negativeEn": negative["en"],
        "negativeZh": negative["zh"],
        "relationEn": relation_en,
        "relationZh": relation_zh,
    }


def normalize_text_expansion(payload: dict) -> dict:
    raw_blocks = payload.get("blocks")
    if not isinstance(raw_blocks, list):
        raise PromptEngineError(
            "模型输出缺少 blocks 数组",
            code="invalid_model_output",
            status=502,
        )
    block_map = {
        block.get("id"): block
        for block in raw_blocks
        if isinstance(block, dict) and isinstance(block.get("id"), str)
    }
    block_ids = set(block_map)
    if block_ids == set(LEGACY_BLOCK_IDS):
        for block_id in ("outfit", "expression", "interaction"):
            block_map[block_id] = {
                "id": block_id,
                "en": "",
                "zh": "",
                "confidence": 0,
            }
    elif block_ids != set(REQUIRED_BLOCK_IDS):
        raise PromptEngineError(
            "模型输出的结构块不完整",
            code="invalid_model_output",
            status=502,
        )

    blocks = []
    for block_id in REQUIRED_BLOCK_IDS:
        block = block_map[block_id]
        en = clean_string(block.get("en"))
        zh = clean_string(block.get("zh"))
        if bool(en) != bool(zh):
            raise PromptEngineError(
                f"结构块 {block_id} 的中英文内容不成对",
                code="invalid_model_output",
                status=502,
            )
        if block_id == "quality":
            en = ANIMA_QUALITY["en"]
            zh = ANIMA_QUALITY["zh"]
        elif block_id == "negative":
            en = merge_fixed_prompt(ANIMA_NEGATIVE["en"], en, ", ")
            zh = merge_fixed_prompt(ANIMA_NEGATIVE["zh"], zh, "，")
        source = (
            "model_profile"
            if block_id in {"quality", "negative"}
            else "resource"
            if block_id == "artist" and en
            else "expanded"
        )
        confidence = block.get("confidence", 85)
        try:
            confidence = max(0, min(100, int(confidence)))
        except (TypeError, ValueError):
            confidence = 85
        blocks.append(
            {
                "id": block_id,
                "label": BLOCK_LABELS[block_id],
                "en": en,
                "zh": zh,
                "weight": 100,
                "locked": False,
                "source": source,
                "confidence": confidence,
            }
        )

    checks = payload.get("checks")
    checks = checks if isinstance(checks, dict) else {}
    relation_en = clean_string(payload.get("relationEn"))
    relation_zh = clean_string(payload.get("relationZh"))
    if bool(relation_en) != bool(relation_zh):
        raise PromptEngineError(
            "关系描述的中英文内容不成对",
            code="invalid_model_output",
            status=502,
        )
    compiled = compile_normalized_blocks(blocks, relation_en, relation_zh)
    normalized = {
        **compiled,
        "blocks": blocks,
        "checks": {
            "preservedUserIntent": bool(
                checks.get("preservedUserIntent", True)
            ),
            "bilingualAligned": bool(checks.get("bilingualAligned", True)),
            "conflicts": clean_string_list(checks.get("conflicts")),
            "assumptions": clean_string_list(checks.get("assumptions")),
        },
    }
    return normalized


def normalize_text_decomposition(
    payload: dict,
    user_input: str,
    provider: str = "local",
) -> dict:
    source_items = split_prompt_items(str(user_input or ""))
    if not source_items:
        raise PromptEngineError(
            "请输入需要拆解的完整提示词",
            code="empty_input",
            status=400,
        )
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise PromptEngineError(
            "拆解结果缺少 items 数组",
            code="invalid_model_output",
            status=502,
        )

    source_indexes: dict[str, list[int]] = {}
    for index, item in enumerate(source_items):
        source_indexes.setdefault(source_item_key(item), []).append(index)
    used_indexes: set[int] = set()
    grouped = {block_id: [] for block_id in DECOMPOSITION_BLOCK_IDS}

    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise PromptEngineError(
                "拆解条目格式无效",
                code="invalid_model_output",
                status=502,
            )
        raw_index = raw_item.get("index")
        source_index = (
            raw_index
            if isinstance(raw_index, int)
            and 0 <= raw_index < len(source_items)
            and raw_index not in used_indexes
            else None
        )
        if source_index is None:
            source = clean_string(raw_item.get("source"))
            matches = source_indexes.get(source_item_key(source), [])
            source_index = next(
                (index for index in matches if index not in used_indexes),
                None,
            )
        if source_index is None:
            raise PromptEngineError(
                "模型返回了重复、改写或不存在的原文片段",
                code="source_coverage_failed",
                status=502,
            )
        used_indexes.add(source_index)
        exact_source = source_items[source_index]
        block_id = clean_string(raw_item.get("blockId"))
        if block_id not in DECOMPOSITION_BLOCK_IDS:
            block_id = "unclassified"
        en = clean_string(raw_item.get("en"))
        zh = clean_string(raw_item.get("zh"))
        translation = clean_string(raw_item.get("translation"))
        if contains_cjk(exact_source):
            zh = exact_source
            en = translation or en
        else:
            en = exact_source
            zh = translation or zh
        if not en or not zh:
            raise PromptEngineError(
                "拆解条目缺少中英文对应内容",
                code="invalid_model_output",
                status=502,
            )
        grouped[block_id].append({"en": en, "zh": zh})

    if len(used_indexes) != len(source_items):
        missing = [
            item
            for index, item in enumerate(source_items)
            if index not in used_indexes
        ]
        raise PromptEngineError(
            "模型未完整返回原文片段：" + "、".join(missing[:5]),
            code="source_coverage_failed",
            status=502,
        )

    source_label = (
        "本地 LLM · 仅拆解"
        if provider == "local"
        else "外部 API · 仅拆解"
    )
    blocks = []
    for block_id in DECOMPOSITION_BLOCK_IDS:
        items = grouped[block_id]
        blocks.append(
            {
                "id": block_id,
                "label": BLOCK_LABELS[block_id],
                "en": ", ".join(item["en"] for item in items),
                "zh": "；".join(item["zh"] for item in items),
                "weight": 100,
                "locked": False,
                "source": source_label,
                "confidence": 95,
            }
        )

    positive_blocks = [
        block
        for block in blocks
        if block["id"] != "negative" and block["en"]
    ]
    negative = next(block for block in blocks if block["id"] == "negative")
    return {
        "positiveEn": ", ".join(block["en"] for block in positive_blocks),
        "positiveZh": "；".join(block["zh"] for block in positive_blocks),
        "negativeEn": negative["en"],
        "negativeZh": negative["zh"],
        "relationEn": "",
        "relationZh": "",
        "blocks": blocks,
        "checks": {
            "preservedUserIntent": True,
            "bilingualAligned": True,
            "conflicts": [],
            "assumptions": [],
            "sourceCoverage": 100,
        },
    }


def validate_expansion_quality(
    result: dict,
    expansion_level: str,
    user_input: str = "",
) -> None:
    level = str(expansion_level or "balanced").strip().lower()
    profile = GENERATION_PROFILES.get(level, GENERATION_PROFILES["balanced"])
    input_value = str(user_input or "").strip()
    input_parts = [
        item
        for item in re.split(r"[,，;；\n]+", input_value)
        if item.strip()
    ]
    sparse_input = bool(input_value) and len(input_parts) <= 3 and len(
        input_value
    ) <= 40
    detailed_input = len(input_parts) >= 6 or len(input_value) >= 70
    minimum_items = profile["minimum_items"]
    if level == "balanced" and detailed_input:
        minimum_items = 18
    if minimum_items <= 0:
        return

    visual_blocks = [
        block
        for block in result.get("blocks", [])
        if block.get("id")
        not in {"quality", "artist", "negative"}
    ]
    block_items = {
        block.get("id"): split_prompt_items(clean_string(block.get("en")))
        for block in visual_blocks
    }
    item_count = sum(len(items) for items in block_items.values())
    block_map = {block.get("id"): block for block in visual_blocks}
    issues = []
    if item_count < minimum_items:
        issues.append(
            f"当前只有约 {item_count} 个有效视觉项，至少需要 {minimum_items} 个"
        )

    minimum_by_block = {
        "scene": 5 if level == "balanced" else 7,
        "composition": 4 if level == "balanced" else 5,
        "lighting": 4 if level == "balanced" else 6,
    }
    if level == "balanced" and detailed_input:
        minimum_by_block.update({"scene": 3, "lighting": 3})
    for block_id, minimum in minimum_by_block.items():
        count = len(block_items.get(block_id, []))
        if count < minimum:
            issues.append(f"{block_id} 只有 {count} 项，至少需要 {minimum} 项")

    composition_text = clean_string(
        block_map.get("composition", {}).get("en")
    )
    if not re.search(
        r"\b(?:shot|view|angle|perspective|framing|composition|centered|"
        r"foreground|background|depth of field|close-up|full body|wide)\b",
        composition_text,
        flags=re.IGNORECASE,
    ):
        issues.append("构图块缺少景别、视角、位置或景深信息")

    lighting_text = clean_string(block_map.get("lighting", {}).get("en"))
    if not re.search(
        r"\b(?:light|lighting|shadow|rim|backlit|highlight|reflection|"
        r"color|palette|temperature|warm|cool)\b",
        lighting_text,
        flags=re.IGNORECASE,
    ):
        issues.append("光影块缺少光源、阴影、反射或色彩信息")

    appearance_text = clean_string(block_map.get("appearance", {}).get("en"))
    if re.search(
        r"\b(?:posture|standing|sitting|hunched|leaning|looking|"
        r"walking|running|crouching|kneeling|reaching|gesturing|vision)\b",
        appearance_text,
        flags=re.IGNORECASE,
    ):
        issues.append("外观块混入动作、姿态或不可见的主观感受")

    if level in {"balanced", "creative"} and not clean_string(
        result.get("relationEn")
    ):
        issues.append("balanced/creative 缺少关系描述")

    all_items = [
        prompt_item_key(item)
        for items in block_items.values()
        for item in items
    ]
    duplicate_items = {
        item for item in all_items if all_items.count(item) > 1
    }
    if len(duplicate_items) > 2:
        issues.append(
            "多个结构块重复相同标签：" + "、".join(sorted(duplicate_items))
        )

    if level == "balanced" and sparse_input:
        user_text = str(user_input or "").casefold()
        stable_pattern = re.compile(
            r"\b(?:long|short|brown|black|blue|green|red|blonde|white|"
            r"purple|pink|dress|skirt|shirt|blouse|coat|jacket|uniform|"
            r"transparent|translucent)\b",
            flags=re.IGNORECASE,
        )
        unsupported_appearance = [
            item
            for item in block_items.get("appearance", [])
            if stable_pattern.search(item)
            and not any(
                token in user_text
                for token in re.findall(r"[a-z]+", item.casefold())
                if len(token) > 3
            )
        ]
        if unsupported_appearance:
            issues.append(
                "稀疏 balanced 输入出现无依据的稳定外貌或服装："
                + "、".join(unsupported_appearance)
            )

        location_pattern = re.compile(
            r"\b(?:city|urban|street|station|cafe|school|temple|forest|"
            r"beach|room|street lamp|neon|umbrella|vehicle|car|train)\b",
            flags=re.IGNORECASE,
        )
        unsupported_location = [
            item
            for block_id in ("scene", "effects")
            for item in block_items.get(block_id, [])
            if location_pattern.search(item)
            and not any(
                token in user_text
                for token in re.findall(r"[a-z]+", item.casefold())
                if len(token) > 3
            )
        ]
        if unsupported_location:
            issues.append(
                "稀疏 balanced 输入出现无依据的具体地点或道具："
                + "、".join(unsupported_location)
            )

    subject_text = clean_string(block_map.get("subject", {}).get("en"))
    if re.search(r"成年|adult", input_value, flags=re.IGNORECASE) and re.search(
        r"\b(?:middle-aged|young|teen|teenage|child|girl)\b",
        subject_text,
        flags=re.IGNORECASE,
    ):
        issues.append("用户指定成年角色，但输出年龄层发生漂移")

    if issues:
        details = "；".join(issues)
        raise PromptEngineError(
            f"模型输出内容密度不足：{details}",
            code="insufficient_detail",
            status=502,
        )


def build_user_message(
    user_input: str,
    target_model: str,
    context: str,
    expansion_level: str = "balanced",
    creative_blueprint: dict | None = None,
) -> str:
    level = str(expansion_level or "balanced").strip().lower()
    if level not in {"strict", "balanced", "creative"}:
        level = "balanced"
    sparse = len(
        [
            item
            for item in re.split(r"[,，;；\n]+", str(user_input or ""))
            if item.strip()
        ]
    ) <= 3 and len(str(user_input or "").strip()) <= 40
    return json.dumps(
        {
            "task": "expand_text_to_image_prompt",
            "input": user_input,
            "targetModel": target_model,
            "expansionLevel": level,
            "context": context,
            "inputProfile": {"sparse": sparse},
            "inferencePolicy": {
                "allowStableAppearanceInvention": (
                    level == "creative" or not sparse
                ),
                "allowSpecificLocationInvention": (
                    level == "creative" or not sparse
                ),
                "preferPhysicalConsequences": level != "creative",
                "neutralSceneScaffold": (
                    "open outdoor setting, overcast sky, wet ground, shallow "
                    "puddles, indistinct distant background"
                    if level == "balanced" and sparse
                    else ""
                ),
                "appearanceScaffold": (
                    "weather-affected state only; no invented hair color, eye "
                    "color, exact garment, garment color, or special material"
                    if level == "balanced" and sparse
                    else ""
                ),
            },
            "creativeBlueprint": creative_blueprint or {},
        },
        ensure_ascii=False,
        indent=2,
    )


def generate_random_text_prompt(
    settings: dict,
    provider: str = "local",
    target_model: str = "anima",
    transport: Transport | None = None,
    timeout: float = 120,
) -> dict:
    config = resolve_text_provider(settings, provider)
    caller = transport or default_transport
    blueprint_system = (
        "你是 AI 绘图的随机画面总设计师。生成一套原创、连贯、可直接编译为 "
        "Anima 提示词的完整视觉蓝图。不要参考用户输入，因为本任务是全随机。"
        "主体必须是明确的成年角色或无年龄歧义的非人主体；禁止已知 IP、画师名、"
        "品牌和 LoRA。画面必须有单一清晰事件，人物、动作、环境、镜头和光线互相"
        "支持，不能拼接互斥设定。请覆盖 subject、appearance、outfit、expression、pose、interaction、scene、"
        "composition、lighting、effects，每类给出具体可见事实，总计 45-60 项，"
        "并提供一段关系描述。只返回严格 JSON。"
    )
    blueprint_request = {
        "task": "random_visual_blueprint",
        "targetModel": target_model,
        "requirements": {
            "original": True,
            "coherent": True,
            "minimumVisualFacts": 45,
            "maximumVisualFacts": 60,
            "adultOrAgeNeutral": True,
            "forbidden": ["known IP", "artist name", "brand", "LoRA"],
        },
        "outputSchema": {
            "concept": "one coherent scene concept",
            "subject": ["visible subject facts"],
            "appearance": ["body and appearance facts"],
            "outfit": ["clothing, material, footwear and accessory facts"],
            "expression": ["facial expression and gaze facts"],
            "pose": ["pose and body action facts"],
            "interaction": ["subject-to-subject or subject-to-object relation facts"],
            "scene": ["environment and foreground/background facts"],
            "composition": ["shot, angle, framing, depth facts"],
            "lighting": ["lighting and color facts"],
            "effects": ["physical effects and props"],
            "relation": "one or two English sentences",
            "assumptions": ["short Chinese assumptions"],
        },
    }
    blueprint_message = json.dumps(
        blueprint_request,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if provider == "local":
        blueprint_message = f"/no_think\n{blueprint_message}"
    blueprint_body = apply_provider_request_options(
        config,
        {
            "model": config["model"],
            "messages": [
                {"role": "system", "content": blueprint_system},
                {"role": "user", "content": blueprint_message},
            ],
            "temperature": 1.0,
            "max_tokens": 1800,
            "response_format": {"type": "json_object"},
            "stream": False,
        },
    )
    blueprint_response = caller(
        config["url"],
        blueprint_body,
        config["headers"],
        timeout,
    )
    blueprint = parse_model_json(response_content(blueprint_response))
    concept = clean_string(blueprint.get("concept"))
    required_blueprint_lists = (
        "subject",
        "appearance",
        "outfit",
        "expression",
        "pose",
        "interaction",
        "scene",
        "composition",
        "lighting",
        "effects",
    )
    if not concept or any(
        not isinstance(blueprint.get(key), list) or not blueprint.get(key)
        for key in required_blueprint_lists
    ):
        raise PromptEngineError(
            "随机蓝图缺少主体、动作、场景、构图、光影或特效信息",
            code="invalid_model_output",
            status=502,
        )

    system_prompt = build_text_expand_system_prompt(
        target_model,
        (
            "当前任务是全随机创作，不存在需要保留的用户原始提示词。"
            "必须完整采用 user 消息中的 randomBlueprint，编译为高密度创意模式结果。"
            "十三个结构块必须全部存在；除 artist 可为空外，其余正向视觉块应有具体内容。"
            "英文与中文必须描述同一组事实。"
        ),
    )
    compile_payload = {
        "task": "compile_random_anima_prompt",
        "targetModel": target_model,
        "expansionLevel": "creative",
        "input": concept,
        "inputProfile": {"sparse": False, "random": True},
        "randomBlueprint": blueprint,
        "requirements": {
            "minimumVisualItems": 35,
            "requiredBlocks": list(REQUIRED_BLOCK_IDS),
            "bilingual": True,
            "includeRelation": True,
            "deduplicate": True,
        },
    }
    compile_message = json.dumps(
        compile_payload,
        ensure_ascii=False,
        indent=2,
    )
    if provider == "local":
        compile_message = f"/no_think\n{compile_message}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": compile_message},
    ]

    def call(current_messages: list[dict], temperature: float) -> str:
        body = apply_provider_request_options(
            config,
            {
                "model": config["model"],
                "messages": current_messages,
                "temperature": temperature,
                "max_tokens": GENERATION_PROFILES["creative"]["max_tokens"],
                "response_format": {"type": "json_object"},
                "stream": False,
            },
        )
        response = caller(
            config["url"],
            body,
            config["headers"],
            timeout,
        )
        return response_content(response)

    def parse_result(content: str) -> dict:
        result = normalize_text_expansion(parse_model_json(content))
        validate_expansion_quality(result, "creative", concept)
        result.setdefault("checks", {})["randomBlueprint"] = blueprint
        return result

    first_content = call(messages, GENERATION_PROFILES["creative"]["temperature"])
    try:
        return parse_result(first_content)
    except PromptEngineError as first_error:
        if first_error.code not in {
            "invalid_model_output",
            "insufficient_detail",
        }:
            raise
        repair_message = (
            f"上一次完整结果未通过校验：{first_error.message}。"
            "重新输出完整严格 JSON。保留同一蓝图和事件，补齐十三个结构块、中英文、"
            "关系描述与至少 35 个去重后的可见细节；不要解释，不要代码块。"
        )
        return parse_result(
            call(
                messages + [{"role": "user", "content": repair_message}],
                0.5,
            )
        )


def expand_text_prompt(
    user_input: str,
    settings: dict,
    provider: str = "local",
    target_model: str = "anima",
    context: str = "",
    expansion_level: str = "balanced",
    transport: Transport | None = None,
    timeout: float = 90,
) -> dict:
    input_text = str(user_input or "").strip()
    if not input_text:
        raise PromptEngineError(
            "请输入需要拓展的提示词",
            code="empty_input",
            status=400,
        )
    config = resolve_text_provider(settings, provider)
    level = str(expansion_level or "balanced").strip().lower()
    if level not in GENERATION_PROFILES:
        level = "balanced"
    generation = GENERATION_PROFILES[level]
    system_prompt = build_text_expand_system_prompt(target_model)
    caller = transport or default_transport
    creative_blueprint = None
    if level == "creative":
        blueprint_system = (
            "你是 AI 绘图的视觉概念设计师。只为用户已有主题设计一套连贯方案，"
            "不输出最终提示词。不得改变主体数量、核心动作、天气和明确条件，不加入"
            "已知 IP、画师、LoRA 或品牌。年龄未指定时保持中性，不写 child、young、"
            "adult 或 middle-aged。只选择一个一致的动作和场景方案，不并列互斥动作。"
            "严格输出 JSON：concept 为短字符串；subjectDesign 1-3项；appearance 6-10项；"
            "pose 5-8项；scene 8-12项；composition 4-6项；lighting 5-8项；effects 4-7项；"
            "relation 为1-2句英文数组；assumptions 为3-6条简短中文数组。整份蓝图总计"
            "35-50个不重复视觉事实。每项必须具体可见且不超过10个英文单词。禁止用"
            "rain style、rain effect、rain detail、rain motion 等同义变体凑数。"
        )
        blueprint_request = json.dumps(
            {
                "task": "creative_visual_blueprint",
                "input": input_text,
                "context": context,
                "targetModel": target_model,
            },
            ensure_ascii=False,
        )
        if provider == "local":
            blueprint_request = f"/no_think\n{blueprint_request}"
        blueprint_body = apply_provider_request_options(
            config,
            {
                "model": config["model"],
                "messages": [
                    {"role": "system", "content": blueprint_system},
                    {"role": "user", "content": blueprint_request},
                ],
                "temperature": 0.8,
                "max_tokens": 1200,
                "response_format": {"type": "json_object"},
                "stream": False,
            },
        )
        blueprint_response = caller(
            config["url"],
            blueprint_body,
            config["headers"],
            timeout,
        )
        creative_blueprint = parse_model_json(
            response_content(blueprint_response)
        )
    user_message = build_user_message(
        input_text,
        target_model,
        context,
        level,
        creative_blueprint,
    )
    if provider == "local":
        user_message = f"/no_think\n{user_message}"
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": user_message,
        },
    ]
    def call(current_messages: list[dict]) -> str:
        request_body = apply_provider_request_options(
            config,
            {
                "model": config["model"],
                "messages": current_messages,
                "temperature": generation["temperature"],
                "max_tokens": generation["max_tokens"],
                "stream": False,
            },
        )
        response = caller(
            config["url"],
            request_body,
            config["headers"],
            timeout,
        )
        return response_content(response)

    def parse_result(content: str) -> dict:
        result = normalize_text_expansion(parse_model_json(content))
        validate_expansion_quality(result, level, input_text)
        return result

    first_content = call(messages)
    try:
        return parse_result(first_content)
    except PromptEngineError as first_error:
        if first_error.code not in {
            "invalid_model_output",
            "insufficient_detail",
        }:
            raise
        if first_error.code == "insufficient_detail":
            repair_instruction = (
                f"上一条 JSON 结构正确但内容密度不足：{first_error.message}。"
                "逐项修复上面列出的所有问题。凡是被指出为无依据的外貌、服装、"
                "地点或道具，必须删除，不能换成同类的另一种具体设定。"
                "稀疏 balanced 输入只补充天气或动作造成的可见状态，并使用请求中"
                "给出的 neutralSceneScaffold；把丰富度放在环境物理反应、前中后景、"
                "构图、主辅光与效果。"
                "删除重复和空泛词，重新输出完整严格 JSON。"
            )
        else:
            repair_instruction = (
                "上一条输出无法解析。只修复为严格 JSON，保持原有视觉内容，"
                "补齐所有必需字段和结构块，不要输出代码块或解释。"
            )
        repair_messages = messages + [
            {
                "role": "user",
                "content": repair_instruction,
            },
        ]
        second_content = call(repair_messages)
        return parse_result(second_content)


def decompose_text_prompt(
    user_input: str,
    settings: dict,
    provider: str = "local",
    transport: Transport | None = None,
    timeout: float = 90,
) -> dict:
    input_text = str(user_input or "").strip()
    if not input_text:
        raise PromptEngineError(
            "请输入需要拆解的完整提示词",
            code="empty_input",
            status=400,
        )
    source_items = split_prompt_items(input_text)
    config = resolve_text_provider(settings, provider)
    indexed_source_items = [
        {"index": index, "text": item}
        for index, item in enumerate(source_items)
    ]
    caller = transport or default_transport
    system_prompt = build_text_decompose_system_prompt()

    def call(request_payload: dict, temperature: float = 0.1) -> str:
        is_initial = request_payload["task"] == "decompose_existing_prompt"
        message = json.dumps(
            request_payload,
            ensure_ascii=False,
            indent=2 if is_initial else None,
            separators=None if is_initial else (",", ":"),
        )
        if provider == "local":
            message = f"/no_think\n{message}"
        batch_size = len(request_payload["sourceItems"])
        request_body = apply_provider_request_options(
            config,
            {
                "model": config["model"],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message},
                ],
                "temperature": temperature,
                "max_tokens": min(1600, max(600, batch_size * 60)),
                "response_format": {"type": "json_object"},
                "stream": False,
            },
        )
        response = caller(
            config["url"],
            request_body,
            config["headers"],
            timeout,
        )
        return response_content(response)

    def validate_batch(payload: dict, batch: list[dict]) -> list[dict]:
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise PromptEngineError(
                "拆解结果缺少 items 数组",
                code="invalid_model_output",
                status=502,
            )
        expected = {item["index"]: item["text"] for item in batch}
        source_matches = {
            source_item_key(item["text"]): item["index"] for item in batch
        }
        used = set()
        normalized_items = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                raise PromptEngineError(
                    "拆解条目格式无效",
                    code="invalid_model_output",
                    status=502,
                )
            index = raw_item.get("index")
            if not isinstance(index, int) or index not in expected or index in used:
                index = source_matches.get(
                    source_item_key(clean_string(raw_item.get("source")))
                )
            if index is None or index in used:
                raise PromptEngineError(
                    "模型返回了重复、改写或不存在的原文片段",
                    code="source_coverage_failed",
                    status=502,
                )
            exact_source = expected[index]
            translation = clean_string(raw_item.get("translation"))
            alternate = (
                clean_string(raw_item.get("en"))
                if contains_cjk(exact_source)
                else clean_string(raw_item.get("zh"))
            )
            if not translation and not alternate:
                raise PromptEngineError(
                    "拆解条目缺少中英文对应内容",
                    code="invalid_model_output",
                    status=502,
                )
            used.add(index)
            normalized_items.append({**raw_item, "index": index})
        if used != set(expected):
            raise PromptEngineError(
                "模型未完整返回当前批次的原文片段",
                code="source_coverage_failed",
                status=502,
            )
        return normalized_items

    def process_batch(batch: list[dict]) -> list[dict]:
        request_payload = {
            "task": "decompose_existing_prompt",
            "sourceItems": batch,
            "allowedBlocks": list(DECOMPOSITION_BLOCK_IDS),
        }
        try:
            return validate_batch(
                parse_model_json(call(request_payload)),
                batch,
            )
        except PromptEngineError as first_error:
            if first_error.code not in {
                "invalid_model_output",
                "source_coverage_failed",
            }:
                raise
            repair_payload = {
                "task": "repair_decomposition_json",
                "sourceItems": batch,
                "allowedBlocks": list(DECOMPOSITION_BLOCK_IDS),
                "failure": first_error.message,
                "requirements": [
                    "重新生成当前批次的完整结果，不要续写上一条内容",
                    "每个 index 必须恰好出现一次",
                    "只返回 index、blockId、translation",
                    "不得输出 source 原文、解释或代码块",
                ],
            }
            return validate_batch(
                parse_model_json(call(repair_payload, 0.0)),
                batch,
            )

    all_items = []
    for start in range(0, len(indexed_source_items), 24):
        all_items.extend(process_batch(indexed_source_items[start : start + 24]))
    return normalize_text_decomposition(
        {"items": all_items},
        input_text,
        provider,
    )


def translate_pending_items(
    items: list[dict],
    settings: dict,
    transport: Transport | None = None,
    timeout: float = 90,
) -> dict:
    if not isinstance(items, list) or not items:
        raise PromptEngineError(
            "没有需要翻译的提示词内容",
            code="empty_input",
            status=400,
        )
    normalized_items = []
    expected_ids = set()
    english_by_id = {}
    for raw_item in items:
        item_id = clean_string(raw_item.get("id")) if isinstance(raw_item, dict) else ""
        english = clean_string(raw_item.get("en")) if isinstance(raw_item, dict) else ""
        if not item_id or not english or item_id in expected_ids:
            raise PromptEngineError(
                "待翻译项目缺少唯一 ID 或英文原文",
                code="invalid_input",
                status=400,
            )
        expected_ids.add(item_id)
        english_by_id[item_id] = english
        normalized_items.append({"id": item_id, "en": english})

    config = resolve_text_provider(settings, "local")
    request_payload = {
        "task": "translate_pending_prompt_blocks",
        "items": normalized_items,
        "requirements": [
            "只把 en 翻译为简洁准确的中文 AI 绘图提示词",
            "保留角色名、画师名、模型标签、权重和专有词的含义",
            "每个 id 恰好返回一次，不得新增、删除或重复",
            "只返回严格 JSON，不要代码块或解释",
        ],
        "outputSchema": {
            "translations": [{"id": "原项目 ID", "zh": "中文翻译"}]
        },
    }
    caller = transport or default_transport

    def call(payload: dict, temperature: float) -> dict:
        message = "/no_think\n" + json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        request_body = apply_provider_request_options(
            config,
            {
                "model": config["model"],
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是 AI 绘图提示词双语翻译器。"
                            "必须把每个英文逗号标签翻译为中文，"
                            "不补充、不删减、不改写视觉内容。"
                        ),
                    },
                    {"role": "user", "content": message},
                ],
                "temperature": temperature,
                "max_tokens": min(1600, max(500, len(normalized_items) * 180)),
                "response_format": {"type": "json_object"},
                "stream": False,
            },
        )
        response = caller(
            config["url"],
            request_body,
            config["headers"],
            timeout,
        )
        return parse_model_json(response_content(response))

    def normalize(payload: dict) -> dict:
        raw_translations = payload.get("translations")
        if not isinstance(raw_translations, list):
            raise PromptEngineError(
                "翻译结果缺少 translations 数组",
                code="invalid_model_output",
                status=502,
            )
        translations = {}
        for raw_translation in raw_translations:
            if not isinstance(raw_translation, dict):
                raise PromptEngineError(
                    "翻译条目格式无效",
                    code="invalid_model_output",
                    status=502,
                )
            item_id = clean_string(raw_translation.get("id"))
            chinese = clean_string(raw_translation.get("zh"))
            replacements = {
                "multiple girls": "多名女性角色",
                "1girl": "一名女性角色",
                "1boy": "一名男性角色",
                "solo": "单人",
            }
            for tag, translated_tag in replacements.items():
                chinese = re.sub(
                    rf"(?<![\w]){re.escape(tag)}(?![\w])",
                    translated_tag,
                    chinese,
                    flags=re.IGNORECASE,
                )
            if item_id in english_by_id and not contains_cjk(chinese):
                technical_translation = english_by_id[item_id]
                for tag, translated_tag in replacements.items():
                    technical_translation = re.sub(
                        rf"(?<![\w]){re.escape(tag)}(?![\w])",
                        translated_tag,
                        technical_translation,
                        flags=re.IGNORECASE,
                    )
                if contains_cjk(technical_translation):
                    chinese = technical_translation
            if (
                item_id not in expected_ids
                or item_id in translations
                or not chinese
                or not contains_cjk(chinese)
            ):
                raise PromptEngineError(
                    "模型返回了缺失、重复或无效的中文翻译",
                    code="invalid_model_output",
                    status=502,
                )
            translations[item_id] = chinese
        if set(translations) != expected_ids:
            raise PromptEngineError(
                "模型没有完整返回全部待翻译项目",
                code="invalid_model_output",
                status=502,
            )
        return {
            "translations": [
                {"id": item["id"], "zh": translations[item["id"]]}
                for item in normalized_items
            ]
        }

    try:
        return normalize(call(request_payload, 0.1))
    except PromptEngineError as first_error:
        if first_error.code != "invalid_model_output":
            raise
        repair_payload = {
            "task": "repair_pending_prompt_translation",
            "items": normalized_items,
            "failure": first_error.message,
            "requirements": [
                "逐个翻译每个 en 中的全部英文逗号标签",
                "1girl 翻译为一名女性角色，1boy 翻译为一名男性角色",
                "禁止照抄整段英文",
                "每个 id 恰好返回一次，只返回严格 JSON",
            ],
            "outputSchema": request_payload["outputSchema"],
        }
        return normalize(call(repair_payload, 0.0))


def regenerate_prompt_block(
    target_block_id: str,
    blocks: list[dict],
    settings: dict,
    provider: str = "local",
    expansion_level: str = "balanced",
    transport: Transport | None = None,
    timeout: float = 90,
) -> dict:
    block_id = str(target_block_id or "").strip()
    if block_id not in RANDOM_VARIANT_BLOCK_IDS:
        raise PromptEngineError(
            "该结构块不支持随机变体",
            code="unsupported_block",
            status=400,
        )
    if not isinstance(blocks, list):
        raise PromptEngineError(
            "缺少当前结构块上下文",
            code="invalid_block_context",
            status=400,
        )
    block_map = {
        str(block.get("id") or "").strip(): block
        for block in blocks
        if isinstance(block, dict)
    }
    current = block_map.get(block_id)
    if not current:
        raise PromptEngineError(
            "未找到需要随机的结构块",
            code="invalid_block_context",
            status=400,
        )
    if current.get("locked"):
        raise PromptEngineError(
            "该结构块已锁定，请先解锁",
            code="block_locked",
            status=409,
        )

    level = str(expansion_level or "balanced").strip().lower()
    if level not in GENERATION_PROFILES:
        level = "balanced"
    config = resolve_text_provider(settings, provider)
    caller = transport or default_transport
    context_blocks = [
        {
            "id": item_id,
            "en": clean_string(item.get("en")),
            "zh": clean_string(item.get("zh")),
            "locked": bool(item.get("locked")),
        }
        for item_id, item in block_map.items()
    ]
    system_prompt = (
        "你是 Anima 绘图提示词的单块随机变体设计器。只重写指定结构块，"
        "其他结构块是必须兼容的画面事实，不能改写。随机变体要与当前内容明显不同，"
        "但保持主体数量、已知角色、时代、天气、动作因果和锁定内容一致。"
        "不要加入画师、IP、LoRA、品牌、质量词、负面词或技术参数。"
        "使用具体可见、去重、适合 Anima 的英文 tags；中文必须表达相同事实。"
        "只输出严格 JSON："
        '{"en":"英文变体","zh":"中文对应内容","assumptions":["简短中文补充项"]}。'
    )
    request_payload = {
        "task": "randomize_single_prompt_block",
        "targetBlock": block_id,
        "targetLabel": BLOCK_LABELS[block_id],
        "expansionLevel": level,
        "currentBlock": {
            "en": clean_string(current.get("en")),
            "zh": clean_string(current.get("zh")),
        },
        "allBlocks": context_blocks,
        "requirements": [
            "只返回目标结构块的新内容",
            "必须与当前结构块明显不同",
            "必须兼容其他结构块，锁定块视为不可变事实",
            "不要在多个结构块之间搬运同一内容",
        ],
    }
    user_message = json.dumps(
        request_payload,
        ensure_ascii=False,
        indent=2,
    )
    if provider == "local":
        user_message = f"/no_think\n{user_message}"
    request_body = apply_provider_request_options(
        config,
        {
            "model": config["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.9,
            "max_tokens": 700,
            "response_format": {"type": "json_object"},
            "stream": False,
        },
    )
    response = caller(
        config["url"],
        request_body,
        config["headers"],
        timeout,
    )
    draft_payload = parse_model_json(response_content(response))
    draft_en = clean_string(draft_payload.get("en"))
    draft_zh = clean_string(draft_payload.get("zh"))
    if not draft_en or not draft_zh:
        raise PromptEngineError(
            "随机变体缺少中英文内容",
            code="invalid_model_output",
            status=502,
        )

    immutable_blocks = [
        block for block in context_blocks if block["id"] != block_id
    ]
    review_payload = {
        "task": "review_single_block_variant",
        "targetBlock": block_id,
        "candidate": {
            "en": draft_en,
            "zh": draft_zh,
        },
        "immutableBlocks": immutable_blocks,
        "rules": [
            "候选可以改变目标块，但不能改变或冲突其他结构块中的事实",
            "目标块只保留属于自身分类的信息",
            "场景块不得改写人物姿态，构图块不得改写场景，光影块不得改写动作",
            "若其他块包含天气、时间、动作或角色事实，目标块必须与之兼容",
            "删除冲突内容后仍需保持与原目标块明显不同",
            "中英文必须表达相同事实",
        ],
    }
    review_message = json.dumps(
        review_payload,
        ensure_ascii=False,
        indent=2,
    )
    if provider == "local":
        review_message = f"/no_think\n{review_message}"
    review_body = apply_provider_request_options(
        config,
        {
            "model": config["model"],
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是提示词结构块兼容性审校器。只修正候选结构块，"
                        "确保它不改变、不冲突 immutableBlocks。只输出严格 JSON："
                        '{"en":"修正后的英文","zh":"对应中文",'
                        '"assumptions":["简短中文说明"]}。'
                    ),
                },
                {"role": "user", "content": review_message},
            ],
            "temperature": 0.2,
            "max_tokens": 700,
            "response_format": {"type": "json_object"},
            "stream": False,
        },
    )
    reviewed_response = caller(
        config["url"],
        review_body,
        config["headers"],
        timeout,
    )
    payload = parse_model_json(response_content(reviewed_response))
    en = clean_string(payload.get("en"))
    zh = clean_string(payload.get("zh"))
    if not en or not zh:
        raise PromptEngineError(
            "随机变体缺少中英文内容",
            code="invalid_model_output",
            status=502,
        )
    if (
        en.casefold() == clean_string(current.get("en")).casefold()
        and zh == clean_string(current.get("zh"))
    ):
        raise PromptEngineError(
            "模型没有生成不同的变体，请再次随机",
            code="unchanged_variant",
            status=502,
        )
    return {
        "id": block_id,
        "label": BLOCK_LABELS[block_id],
        "en": en,
        "zh": zh,
        "weight": current.get("weight", 100),
        "locked": False,
        "source": (
            "本地 LLM · 随机变体"
            if provider == "local"
            else "外部 API · 随机变体"
        ),
        "confidence": 88,
        "assumptions": clean_string_list(payload.get("assumptions")),
    }


def regenerate_prompt_blocks(
    target_block_ids: list[str],
    blocks: list[dict],
    settings: dict,
    provider: str = "local",
    expansion_level: str = "balanced",
    transport: Transport | None = None,
    timeout: float = 120,
) -> dict:
    requested_ids = [
        str(block_id or "").strip()
        for block_id in target_block_ids
        if str(block_id or "").strip()
    ]
    selected_ids = list(dict.fromkeys(requested_ids))
    if not selected_ids:
        raise PromptEngineError(
            "请至少选择两个结构块",
            code="missing_block_selection",
            status=400,
        )
    unsupported = [
        block_id
        for block_id in selected_ids
        if block_id not in RANDOM_VARIANT_BLOCK_IDS
    ]
    if unsupported:
        raise PromptEngineError(
            "包含不支持联合随机的结构块",
            code="unsupported_block",
            status=400,
        )
    if not isinstance(blocks, list):
        raise PromptEngineError(
            "缺少当前结构块上下文",
            code="invalid_block_context",
            status=400,
        )
    block_map = {
        str(block.get("id") or "").strip(): block
        for block in blocks
        if isinstance(block, dict)
    }
    missing = [block_id for block_id in selected_ids if block_id not in block_map]
    if missing:
        raise PromptEngineError(
            "未找到部分已选结构块",
            code="invalid_block_context",
            status=400,
        )
    locked = [
        block_id
        for block_id in selected_ids
        if bool(block_map[block_id].get("locked"))
    ]
    if locked:
        raise PromptEngineError(
            "已选结构块中包含锁定项，请先取消选择或解锁",
            code="block_locked",
            status=409,
        )
    if len(selected_ids) < 2:
        raise PromptEngineError(
            "联合随机至少需要选择两个结构块",
            code="insufficient_block_selection",
            status=400,
        )

    level = str(expansion_level or "balanced").strip().lower()
    if level not in GENERATION_PROFILES:
        level = "balanced"
    config = resolve_text_provider(settings, provider)
    caller = transport or default_transport
    all_blocks = [
        {
            "id": block_id,
            "label": BLOCK_LABELS.get(block_id, block_id),
            "en": clean_string(block.get("en")),
            "zh": clean_string(block.get("zh")),
            "locked": bool(block.get("locked")),
        }
        for block_id, block in block_map.items()
    ]
    selected_blocks = [
        block for block in all_blocks if block["id"] in selected_ids
    ]
    immutable_blocks = [
        block for block in all_blocks if block["id"] not in selected_ids
    ]

    def call(system_prompt: str, payload: dict, temperature: float) -> dict:
        user_message = json.dumps(payload, ensure_ascii=False, indent=2)
        if provider == "local":
            user_message = f"/no_think\n{user_message}"
        body = apply_provider_request_options(
            config,
            {
                "model": config["model"],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "temperature": temperature,
                "max_tokens": min(3000, 500 + len(selected_ids) * 450),
                "response_format": {"type": "json_object"},
                "stream": False,
            },
        )
        response = caller(
            config["url"],
            body,
            config["headers"],
            timeout,
        )
        return parse_model_json(response_content(response))

    def normalize_items(payload: dict) -> list[dict]:
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise PromptEngineError(
                "联合随机结果缺少 items 数组",
                code="invalid_model_output",
                status=502,
            )
        returned_ids = [
            clean_string(item.get("id"))
            for item in raw_items
            if isinstance(item, dict)
        ]
        if (
            len(raw_items) != len(selected_ids)
            or len(returned_ids) != len(raw_items)
            or len(set(returned_ids)) != len(returned_ids)
        ):
            raise PromptEngineError(
                "联合随机必须让每个所选结构块恰好返回一次",
                code="invalid_model_output",
                status=502,
            )
        item_map = {
            clean_string(item.get("id")): item
            for item in raw_items
            if isinstance(item, dict)
        }
        if set(item_map) != set(selected_ids):
            raise PromptEngineError(
                "联合随机返回的结构块与所选项目不一致",
                code="invalid_model_output",
                status=502,
            )
        normalized = []
        for block_id in selected_ids:
            item = item_map[block_id]
            en = clean_string(item.get("en"))
            zh = clean_string(item.get("zh"))
            if not en or not zh:
                raise PromptEngineError(
                    f"联合随机的 {block_id} 缺少中英文内容",
                    code="invalid_model_output",
                    status=502,
                )
            current = block_map[block_id]
            if (
                en.casefold() == clean_string(current.get("en")).casefold()
                and zh == clean_string(current.get("zh"))
            ):
                raise PromptEngineError(
                    f"模型没有改变 {BLOCK_LABELS[block_id]}，请再次随机",
                    code="unchanged_variant",
                    status=502,
                )
            normalized.append({"id": block_id, "en": en, "zh": zh})
        return normalized

    reviewed = {}
    items = []
    last_error = None
    for attempt in range(2):
        try:
            draft = call(
                (
                    "你是 Anima 绘图提示词的多结构块联合随机设计器。必须同时重写所有"
                    " selectedBlocks，并把它们设计成一套连贯画面；immutableBlocks 是不可"
                    "改变的事实。所选块都必须与当前内容明显不同，但不能改变未选块中的主体"
                    "数量、角色身份、时代、天气、动作因果或锁定事实。每项只写属于该结构块"
                    "的具体可见内容，中英文表达相同事实。不要加入画师、IP、LoRA、品牌、"
                    "质量词、负面词或技术参数。严格输出 JSON："
                    '{"items":[{"id":"结构块ID","en":"英文","zh":"中文"}],'
                    '"assumptions":["简短中文说明"]}。'
                ),
                {
                    "task": "randomize_multiple_prompt_blocks",
                    "attempt": attempt + 1,
                    "retryInstruction": (
                        "上次结果未通过变化校验；本次必须选择明显不同的新画面方案。"
                        if attempt
                        else ""
                    ),
                    "expansionLevel": level,
                    "selectedBlocks": selected_blocks,
                    "immutableBlocks": immutable_blocks,
                    "requiredIds": selected_ids,
                },
                0.95 if attempt else 0.9,
            )
            draft_items = normalize_items(draft)
            reviewed = call(
                (
                    "你是多结构块联合变体的整体兼容性审校器。检查 candidates 彼此之间以及"
                    "它们与 immutableBlocks 是否构成同一幅可实现画面。只能修正 candidates，"
                    "不能改变、复制或覆盖未选块。保持每个候选属于自己的结构分类，确保所有"
                    " requiredIds 恰好返回一次，中英文对齐，且每项仍与原内容明显不同。"
                    "只输出严格 JSON："
                    '{"items":[{"id":"结构块ID","en":"英文","zh":"中文"}],'
                    '"assumptions":["简短中文说明"]}。'
                ),
                {
                    "task": "review_multiple_block_variant",
                    "requiredIds": selected_ids,
                    "originalSelectedBlocks": selected_blocks,
                    "candidates": draft_items,
                    "immutableBlocks": immutable_blocks,
                },
                0.2,
            )
            items = normalize_items(reviewed)
            break
        except PromptEngineError as error:
            last_error = error
            if attempt or error.code not in {
                "unchanged_variant",
                "invalid_model_output",
            }:
                raise
    if not items and last_error:
        raise last_error
    source = (
        "本地 LLM · 联合随机"
        if provider == "local"
        else "外部 API · 联合随机"
    )
    return {
        "items": [
            {
                **item,
                "label": BLOCK_LABELS[item["id"]],
                "weight": block_map[item["id"]].get("weight", 100),
                "locked": False,
                "source": source,
                "confidence": 88,
            }
            for item in items
        ],
        "assumptions": clean_string_list(reviewed.get("assumptions")),
    }
