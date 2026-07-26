"""Local development server and AnimaDex gateway for Prompt Studio."""

from __future__ import annotations

import base64
import binascii
import json
import math
import os
import socket
import sqlite3
import tempfile
import threading
import time
import uuid
from copy import deepcopy
from io import BytesIO
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from PIL import Image, UnidentifiedImageError

import db as prompt_db
from backup import (
    BackupConflictError,
    BackupValidationError,
    MAX_ARCHIVE_BYTES as MAX_BACKUP_ARCHIVE_BYTES,
    export_database,
    export_project,
    inspect_backup,
    restore_backup,
)
from creative_intake import (
    CreativeIntakeValidationError,
    apply_creative_intake_transition,
)
from creative_director import (
    CreativeDirectorError,
    run_creative_director_turn,
)
from db import (
    IdempotencyConflictError,
    ProjectConflictError,
    VersionConflictError,
    commit_workspace,
    create_project,
    create_prompt_version,
    delete_favorite,
    get_project,
    get_settings,
    init_db,
    list_favorites,
    list_projects,
    put_settings,
    recovery_directory,
    update_project,
    upsert_favorite,
    validate_idempotency_key,
)
from local_llm import (
    LocalLlmError,
    local_llm_status,
    start_local_llm,
    stop_local_llm,
)
from ai_edit_engine import (
    AIEditError,
    apply_ai_edit_preview,
    create_ai_edit_preview,
)
from edit_engine import (
    EditEngineError,
    create_undo_version,
    recipe_hash as edit_recipe_hash,
)
from model_profiles import (
    DEFAULT_PROFILE_ID,
    candidate_resolution_presets,
    compile_positive_prefix,
    list_model_profiles,
    load_model_profile,
    model_reference,
    profile_default_parameters,
    validated_resolution_presets,
    validate_researched_model_profile,
    ModelProfileError,
)
import model_profile_store
import model_research
import lora_profile_store
import creative_intake
from model_adapted_decomposition import (
    DecompositionError,
    generate_model_adapted_decomposition,
)
from random_sampler import (
    CatalogBoundaryError,
    LockedConflictError,
    PlanRequestError,
    RandomSamplerError,
    SamplingExhaustedError,
    generate_library_seed,
    load_catalog,
    resolve_random_plan,
)
from recipe import (
    REQUIRED_PARAMETER_KEYS,
    build_recipe,
    inflate_workbench_blocks,
    normalize_recipe,
    project_blocks_to_workbench,
    project_recipe_to_prompt_version,
    recipe_hash,
    resolve_parameters,
)
from prompt_engine import (
    PromptEngineError,
    decompose_text_prompt,
    expand_text_prompt,
    generate_random_text_prompt,
    probe_text_provider,
    apply_provider_request_options,
    default_transport,
    resolve_text_provider,
    response_content,
    regenerate_prompt_block,
    regenerate_prompt_blocks,
    translate_pending_items,
)
from vision_engine import (
    VisionEngineError,
    analyze_image_bytes,
    vision_model_status,
)


ROOT = Path(__file__).resolve().parent
HOST = os.environ.get("PROMPT_STUDIO_HOST", "127.0.0.1")
PORT = int(os.environ.get("PROMPT_STUDIO_PORT", "57913"))
ANIMADEX_URL = os.environ.get("ANIMADEX_URL", "http://127.0.0.1:5000").rstrip("/")
UPSTREAM_TIMEOUT = float(os.environ.get("ANIMADEX_TIMEOUT", "3"))
RESOURCE_PAGE_SIZE = 72
ALLOWED_MODES = {"characters", "artists"}
PROMPT_TEMPLATE_DIR = ROOT / "prompts"
MAX_JSON_BODY_BYTES = 1024 * 1024
MAX_VISION_JSON_BODY_BYTES = 30 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000
MAX_CREATIVE_DIRECTOR_SKILL_OVERRIDE_CHARACTERS = 100_000
REQUEST_BODY_TIMEOUT_SECONDS = 2.0
VISION_REQUEST_BODY_TIMEOUT_SECONDS = 30.0
CONNECTION_TIMEOUT_SECONDS = 5.0
BACKUP_UPLOAD_TIMEOUT_SECONDS = 30.0
BACKUP_STAGE_LOCK = threading.Lock()
PUBLIC_STATIC_FILES = {
    "/",
    "/index.html",
    "/app.js",
    "/data.js",
    "/styles.css",
    "/unicode15-data.js",
}
SENSITIVE_SETTING_KEYS = {"apiTextKey", "localTextKey"}
STRING_SETTING_LIMITS = {
    "localTextUrl": 4_096,
    "localTextModel": 512,
    "localTextKey": 8_192,
    "apiTextUrl": 4_096,
    "apiTextModel": 512,
    "apiTextKey": 8_192,
    "creativeDirectorSkillOverride": (
        MAX_CREATIVE_DIRECTOR_SKILL_OVERRIDE_CHARACTERS
    ),
}
ENUM_SETTING_VALUES = {
    "textProvider": {"local", "api"},
    "expansionLevel": {"strict", "balanced", "creative"},
    "visionProvider": {"local", "api", "mixed"},
    "residency": {"smart", "release", "keep"},
}
BOOLEAN_SETTING_KEYS = {"autoCombine"}
ALLOWED_SETTING_KEYS = (
    set(STRING_SETTING_LIMITS)
    | set(ENUM_SETTING_VALUES)
    | BOOLEAN_SETTING_KEYS
)
BASE_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
EXPLICIT_ALLOWED_HOSTS = {
    item.strip().lower().strip("[]")
    for item in os.environ.get("PROMPT_STUDIO_ALLOWED_HOSTS", "").split(",")
    if item.strip()
}
ALLOWED_HOSTS = BASE_ALLOWED_HOSTS | EXPLICIT_ALLOWED_HOSTS
ALLOW_NETWORK_BIND = os.environ.get("PROMPT_STUDIO_ALLOW_NETWORK", "").strip() == "1"
DEFAULT_TEXT_SETTINGS = {
    "textProvider": "local",
    "localTextUrl": "http://127.0.0.1:8080/v1",
    "localTextModel": "Huihui-Qwen3-14B-abliterated-v2.Q4_K_M.gguf",
    "localTextKey": "",
    "expansionLevel": "balanced",
    "apiTextUrl": "",
    "apiTextModel": "",
    "apiTextKey": "",
}
PROMPT_TEMPLATES = {
    "text_expand": {
        "id": "text_expand",
        "label": "文生图旧版整合模板",
        "kind": "text",
        "filename": "text_expand.md",
    },
    "text_core_visual": {
        "id": "text_core_visual",
        "label": "文生图 · 核心视觉扩写",
        "kind": "text",
        "filename": "text/core/visual_expansion.md",
    },
    "text_mode_expand": {
        "id": "text_mode_expand",
        "label": "文生图 · 拓展已有提示词",
        "kind": "text",
        "filename": "text/modes/expand.md",
    },
    "text_format_hybrid": {
        "id": "text_format_hybrid",
        "label": "文生图 · Tags 与自然语言混合编译",
        "kind": "text",
        "filename": "text/formats/hybrid.md",
    },
    "text_model_anima": {
        "id": "text_model_anima",
        "label": "文生图 · Anima 模型配置",
        "kind": "text",
        "filename": "text/models/anima.md",
    },
    "text_schema_bilingual": {
        "id": "text_schema_bilingual",
        "label": "文生图 · 双语输出结构",
        "kind": "text",
        "filename": "text/schemas/bilingual_structured_output.md",
    },
    "image_analyze": {
        "id": "image_analyze",
        "label": "图生图解析模板",
        "kind": "image",
        "filename": "image_analyze.md",
    },
}


class RequestValidationError(Exception):
    """An HTTP request failed boundary validation before business routing."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def parse_host_header(value: str) -> tuple[str, int | None] | None:
    """Parse an HTTP authority without accepting userinfo or ambiguous ports."""

    authority = str(value or "").strip().lower()
    if (
        not authority
        or any(character.isspace() for character in authority)
        or any(character in authority for character in "/\\@?#")
    ):
        return None

    port: int | None = None
    if authority.startswith("["):
        closing = authority.find("]")
        if closing < 0:
            return None
        hostname = authority[1:closing]
        remainder = authority[closing + 1 :]
        if remainder:
            if not remainder.startswith(":") or not remainder[1:].isdigit():
                return None
            port = int(remainder[1:])
    else:
        if authority.count(":") > 1:
            return None
        hostname, separator, raw_port = authority.partition(":")
        if separator:
            if not raw_port.isdigit():
                return None
            port = int(raw_port)

    if not hostname or (port is not None and not 1 <= port <= 65535):
        return None
    return hostname, port


def is_allowed_host(value: str) -> bool:
    parsed = parse_host_header(value)
    return bool(parsed and parsed[0] in ALLOWED_HOSTS)


def is_same_origin(origin: str, host_header: str) -> bool:
    request_authority = parse_host_header(host_header)
    if not request_authority:
        return False
    try:
        parsed_origin = urlparse(origin)
        origin_port = parsed_origin.port
    except ValueError:
        return False
    if (
        parsed_origin.scheme.lower() != "http"
        or not parsed_origin.hostname
        or parsed_origin.username is not None
        or parsed_origin.password is not None
        or parsed_origin.path not in {"", "/"}
        or parsed_origin.params
        or parsed_origin.query
        or parsed_origin.fragment
    ):
        return False
    request_host, request_port = request_authority
    return (
        parsed_origin.hostname.lower() == request_host
        and (origin_port or 80) == (request_port or 80)
    )


def redact_settings(settings: dict) -> dict:
    """Return settings without ever exposing saved provider credentials."""

    redacted = {}
    for key, value in settings.items():
        if key in SENSITIVE_SETTING_KEYS:
            continue
        if key in STRING_SETTING_LIMITS:
            if not isinstance(value, str) or len(value) > STRING_SETTING_LIMITS[key]:
                continue
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                continue
            redacted[key] = value
        elif (
            key in ENUM_SETTING_VALUES
            and isinstance(value, str)
            and value in ENUM_SETTING_VALUES[key]
        ):
            redacted[key] = value
        elif key in BOOLEAN_SETTING_KEYS and isinstance(value, bool):
            redacted[key] = value
    for key in SENSITIVE_SETTING_KEYS:
        redacted[f"{key}Configured"] = bool(settings.get(key))
        redacted[key] = ""
    return redacted


def preserve_saved_setting_keys(payload: dict) -> dict:
    """Treat omitted or blank credential fields as 'leave the saved key alone'."""

    sanitized = dict(payload)
    for key in SENSITIVE_SETTING_KEYS:
        if key not in sanitized:
            continue
        value = sanitized[key]
        if value is None or (isinstance(value, str) and not value.strip()):
            sanitized.pop(key, None)
        elif not isinstance(value, str):
            raise ValueError(f"{key} 必须是字符串")
    return sanitized


def validate_settings_update(payload: dict) -> dict:
    unknown = set(payload) - ALLOWED_SETTING_KEYS
    if unknown:
        raise ValueError("设置包含不支持的字段")
    validated = dict(payload)
    for key, max_length in STRING_SETTING_LIMITS.items():
        if key not in validated:
            continue
        if (
            not isinstance(validated[key], str)
            or len(validated[key]) > max_length
        ):
            raise ValueError(f"{key} 必须是长度不超过 {max_length} 的字符串")
        try:
            validated[key].encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError(f"{key} 必须是有效的 UTF-8 字符串") from error
    for key, values in ENUM_SETTING_VALUES.items():
        if key in validated and (
            not isinstance(validated[key], str)
            or validated[key] not in values
        ):
            raise ValueError(f"{key} 的值无效")
    for key in BOOLEAN_SETTING_KEYS:
        if key in validated and not isinstance(validated[key], bool):
            raise ValueError(f"{key} 必须是布尔值")
    return validated


def validate_creative_director_skill_override(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) > MAX_CREATIVE_DIRECTOR_SKILL_OVERRIDE_CHARACTERS
    ):
        raise CreativeDirectorError(
            "creative director Skill override must be UTF-8 text no longer "
            "than 100000 characters",
            code="invalid_creative_director_request",
            status=400,
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise CreativeDirectorError(
            "creative director Skill override must be valid UTF-8 text",
            code="invalid_creative_director_request",
            status=400,
        ) from error
    return value


def validate_creative_director_request_utf8(value: object) -> None:
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as error:
                raise CreativeDirectorError(
                    "creative director request must contain valid UTF-8 text",
                    code="invalid_creative_director_request",
                    status=400,
                ) from error
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def strict_json_object_pairs(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"请求 JSON 包含重复字段：{key}")
        result[key] = value
    return result


def strict_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("请求 JSON 不允许 NaN 或 Infinity")
    return parsed


def reject_json_constant(value: str) -> None:
    raise ValueError(f"请求 JSON 不允许 {value}")


def fetch_upstream(path: str) -> tuple[bytes, str]:
    request = Request(
        f"{ANIMADEX_URL}{path}",
        headers={"Accept": "application/json,image/*", "User-Agent": "PromptStudio/0.1"},
    )
    with urlopen(request, timeout=UPSTREAM_TIMEOUT) as response:
        return response.read(), response.headers.get_content_type()


def fetch_json(path: str) -> dict:
    body, _ = fetch_upstream(path)
    return json.loads(body.decode("utf-8"))


def search_path(mode: str, query: str, page: int, sort: str = "count") -> str:
    params = {"page": max(1, page)}
    if query:
        params["q"] = query
    if sort in {"count", "az", "score", "random"}:
        params["sort"] = sort
    return f"/api/{mode}/search?{urlencode(params)}"


def is_public_static_path(path: str) -> bool:
    """Allow only the workbench files needed by the browser.

    SimpleHTTPRequestHandler otherwise exposes every file below ``ROOT``, including
    the local SQLite database that stores provider settings and API keys.
    """

    decoded_path = unquote(urlparse(path).path)
    if decoded_path in PUBLIC_STATIC_FILES:
        return True
    try:
        resolved_path = (ROOT / decoded_path.lstrip("/")).resolve()
        assets_directory = (ROOT / "assets").resolve()
        resolved_path.relative_to(assets_directory)
    except (OSError, ValueError):
        return False
    return True


def pending_translation(value: str) -> str:
    return f"待本地 LLM 翻译：{value}" if value else ""


def normalize_tags(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(tag).strip() for tag in value if str(tag).strip())
    return str(value or "").strip()


def detect_image_mime_type(image_bytes: bytes) -> str:
    """Identify the image format from its signature instead of client metadata."""

    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if (
        len(image_bytes) >= 12
        and image_bytes[:4] == b"RIFF"
        and image_bytes[8:12] == b"WEBP"
    ):
        return "image/webp"
    return ""


def validate_image_bytes(image_bytes: bytes, declared_mime_type: str) -> None:
    """Fully decode a bounded image and confirm its real format and dimensions."""

    format_mime_types = {
        "PNG": "image/png",
        "JPEG": "image/jpeg",
        "WEBP": "image/webp",
    }
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            detected_mime_type = format_mime_types.get(
                str(image.format or "").upper(),
                "",
            )
            width, height = image.size
            if (
                not detected_mime_type
                or detected_mime_type != declared_mime_type
                or width <= 0
                or height <= 0
                or width * height > MAX_IMAGE_PIXELS
            ):
                raise ValueError("图片格式、尺寸或像素数量无效")
            image.verify()

        # ``verify`` checks the container. Reopening and loading forces pixel data
        # through the decoder so a valid signature with a truncated body is rejected.
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
    except ValueError:
        raise
    except (
        Image.DecompressionBombError,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
    ) as error:
        raise ValueError("图片文件损坏或无法完整解码") from error


def map_character(item: dict) -> dict:
    slug = str(item.get("slug", ""))
    trigger = str(item.get("trigger") or item.get("name") or slug)
    tags = normalize_tags(item.get("tags") or item.get("core_tags"))
    copyright_name = str(item.get("copyright_name") or item.get("copyright") or "未知作品")
    blocks = [{"id": "subject", "en": trigger, "zh": pending_translation(trigger)}]
    if tags:
        blocks.append(
            {"id": "appearance", "en": tags, "zh": pending_translation(tags)}
        )
    return {
        "id": f"animadex-characters-{slug}",
        "sourceId": slug,
        "source": "AnimaDex",
        "type": "characters",
        "name": str(item.get("name") or slug.replace("_", " ")),
        "meta": copyright_name,
        "targetBlock": "subject",
        "en": ", ".join(value for value in (trigger, tags) if value),
        "zh": pending_translation(trigger),
        "blocks": blocks,
        "thumbUrl": f"/api/animadex/thumb/characters/{quote(slug, safe='')}",
        "hasImage": bool(item.get("has_image", True)),
    }


def map_artist(item: dict) -> dict:
    slug = str(item.get("slug", ""))
    trigger = str(item.get("trigger") or item.get("name") or slug)
    score = item.get("score")
    meta = "画师触发词" if score in (None, "") else f"画师评分 {score}"
    return {
        "id": f"animadex-artists-{slug}",
        "sourceId": slug,
        "source": "AnimaDex",
        "type": "artists",
        "name": str(item.get("name") or slug.replace("_", " ")),
        "meta": meta,
        "targetBlock": "artist",
        "en": trigger,
        "zh": pending_translation(trigger),
        "blocks": [
            {"id": "artist", "en": trigger, "zh": pending_translation(trigger)}
        ],
        "thumbUrl": f"/api/animadex/thumb/artists/{quote(slug, safe='')}",
        "hasImage": bool(item.get("has_image", True)),
    }


def normalize_search_result(mode: str, payload: dict) -> dict:
    raw_items = payload.get("results") or []
    mapper = map_character if mode == "characters" else map_artist
    return {
        "connected": True,
        "source": "AnimaDex",
        "mode": mode,
        "total": int(payload.get("total") or len(raw_items)),
        "page": int(payload.get("page") or 1),
        "pages": int(payload.get("pages") or 1),
        "pageSize": int(payload.get("page_size") or RESOURCE_PAGE_SIZE),
        "items": [mapper(item) for item in raw_items],
    }


def prompt_template_path(template_id: str) -> Path | None:
    template = PROMPT_TEMPLATES.get(template_id)
    if not template:
        return None
    return PROMPT_TEMPLATE_DIR / template["filename"]


def read_prompt_template(template_id: str) -> dict | None:
    template = PROMPT_TEMPLATES.get(template_id)
    path = prompt_template_path(template_id)
    if not template or not path:
        return None
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    updated_at = path.stat().st_mtime if path.exists() else None
    return {
        "id": template["id"],
        "label": template["label"],
        "kind": template["kind"],
        "filename": template["filename"],
        "content": content,
        "updatedAt": updated_at,
    }


def write_prompt_template(template_id: str, content: str) -> dict | None:
    path = prompt_template_path(template_id)
    if not path:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return read_prompt_template(template_id)


def process_text_expand_request(
    payload: dict,
    settings_payload: dict | None = None,
    engine=expand_text_prompt,
) -> dict:
    user_input = str(payload.get("input") or "").strip()
    if not user_input:
        raise ValueError("请输入需要拓展的提示词")

    saved_settings = (
        settings_payload if settings_payload is not None else get_settings()
    )
    settings = {**DEFAULT_TEXT_SETTINGS, **saved_settings}
    provider = str(
        payload.get("provider") or settings.get("textProvider") or "local"
    ).strip()
    if provider not in {"local", "api"}:
        raise ValueError("不支持的文本推理来源")

    target_model = str(payload.get("targetModel") or "anima").strip()
    expansion_level = str(
        payload.get("expansionLevel") or "balanced"
    ).strip().lower()
    if expansion_level not in {"strict", "balanced", "creative"}:
        expansion_level = "balanced"
    context = payload.get("context") or ""
    if not isinstance(context, str):
        context = json.dumps(context, ensure_ascii=False)

    return engine(
        user_input,
        settings,
        provider,
        target_model,
        context,
        expansion_level,
    )


def process_creative_director_request(
    payload: dict,
    settings_payload: dict | None = None,
    engine=None,
    transport=None,
) -> dict:
    allowed = {
        "current",
        "message",
        "imageEvidence",
        "provider",
        "skillOverride",
    }
    if set(payload) - allowed or not {"current", "message"} <= set(payload):
        raise CreativeDirectorError(
            "creative director request fields are invalid",
            code="invalid_creative_director_request",
            status=400,
        )
    validate_creative_director_request_utf8(payload)

    saved_settings = (
        settings_payload if settings_payload is not None else get_settings()
    )
    if not isinstance(saved_settings, dict):
        raise CreativeDirectorError(
            "creative director settings are invalid",
            code="invalid_creative_director_request",
            status=400,
        )
    settings = {**DEFAULT_TEXT_SETTINGS, **saved_settings}
    skill_override = validate_creative_director_skill_override(
        payload.get(
            "skillOverride",
            settings.get("creativeDirectorSkillOverride", ""),
        )
    )
    image_evidence = payload.get("imageEvidence")

    caller = engine or run_creative_director_turn
    return caller(
        current=payload["current"],
        user_message=payload["message"],
        image_evidence=image_evidence,
        provider=payload.get("provider") or settings.get("textProvider") or "local",
        settings=settings,
        skill_override=skill_override,
        transport=transport,
    )


def configured_decomposition_provider(
    settings: dict,
    provider_id: str,
    *,
    transport=None,
):
    """Build the server-owned OpenAI-compatible provider boundary."""
    def provider(messages: list[dict]) -> object:
        config = resolve_text_provider(settings, provider_id)
        caller = transport or default_transport
        body = apply_provider_request_options(
            config,
            {
                "model": config["model"],
                "messages": messages,
                "temperature": 0,
                "max_tokens": 8_192,
                "stream": False,
            },
        )
        response = caller(config["url"], body, config["headers"], 120)
        return response_content(response)

    return provider


def process_model_adapted_decomposition_request(
    payload: dict,
    *,
    provider,
    db_path,
    snapshot_loader=model_profile_store.get_activated_profile_snapshot,
) -> dict:
    """Resolve trusted model evidence and authoritatively install one draft."""
    if not isinstance(payload, dict):
        raise DecompositionError(
            "decomposition preview request must be an object",
            code="invalid_request",
        )
    allowed = {
        "current",
        "profileVersionId",
        "profileContentSha256",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown or set(payload) != allowed:
        raise DecompositionError(
            "decomposition preview request fields are invalid",
            code="invalid_request",
        )
    try:
        current = creative_intake.normalize_creative_intake(payload["current"])
    except CreativeIntakeValidationError as error:
        raise DecompositionError(
            "canonical creative intake is invalid",
            code="invalid_request",
        ) from error
    if (
        current["stage"] not in {"model_selected", "decomposition_draft"}
        or current["brief"] is None
        or current["brief"].get("status") != "confirmed"
        or not current["selectedModelProfileId"]
    ):
        raise DecompositionError(
            "creative intake no longer permits a decomposition preview",
            code="stale_intake",
        )
    if current["stage"] == "decomposition_draft":
        existing = current["decomposition"]
        if (
            existing is None
            or existing["status"] != "draft"
            or existing["profileVersionId"] != payload["profileVersionId"]
            or existing["profileContentSha256"]
            != str(payload["profileContentSha256"]).lower()
            or existing["briefContentSha256"]
            != creative_intake.canonical_brief_sha256(current["brief"])
        ):
            raise DecompositionError(
                "decomposition draft lineage no longer permits regeneration",
                code="stale_intake",
            )

    snapshot = snapshot_loader(
        current["selectedModelProfileId"],
        payload["profileVersionId"],
        payload["profileContentSha256"],
        db_path=db_path,
    )
    generation_intake = deepcopy(current)
    if generation_intake["stage"] == "decomposition_draft":
        generation_intake["stage"] = "model_selected"
        generation_intake["decomposition"] = None
    draft = generate_model_adapted_decomposition(
        intake=generation_intake,
        profile_snapshot=snapshot,
        provider=provider,
    )
    item = apply_creative_intake_transition(
        current,
        {
            "type": (
                "regenerate_decomposition_draft"
                if current["stage"] == "decomposition_draft"
                else "set_decomposition_draft"
            ),
            "decomposition": draft,
        },
    )
    return {
        "item": item,
        "profile": {
            "profileId": snapshot["profileId"],
            "profileVersionId": snapshot["profileVersionId"],
            "profileContentSha256": snapshot["profileContentSha256"],
        },
        "warnings": deepcopy(snapshot.get("warnings", [])),
    }


def process_text_random_request(
    payload: dict,
    settings_payload: dict | None = None,
    engine=generate_random_text_prompt,
) -> dict:
    saved_settings = (
        settings_payload if settings_payload is not None else get_settings()
    )
    settings = {**DEFAULT_TEXT_SETTINGS, **saved_settings}
    provider = str(
        payload.get("provider") or settings.get("textProvider") or "local"
    ).strip().lower()
    if provider not in {"local", "api"}:
        raise ValueError("不支持的文本推理来源")
    target_model = str(payload.get("targetModel") or "anima").strip()
    return engine(settings, provider, target_model)


def process_text_decompose_request(
    payload: dict,
    settings_payload: dict | None = None,
    engine=decompose_text_prompt,
) -> dict:
    user_input = str(payload.get("input") or "").strip()
    if not user_input:
        raise ValueError("请输入需要拆解的完整提示词")
    saved_settings = (
        settings_payload if settings_payload is not None else get_settings()
    )
    settings = {**DEFAULT_TEXT_SETTINGS, **saved_settings}
    provider = str(
        payload.get("provider") or settings.get("textProvider") or "local"
    ).strip().lower()
    if provider not in {"local", "api"}:
        raise ValueError("不支持的文本推理来源")
    return engine(user_input, settings, provider)


def process_text_provider_test(
    payload: dict,
    settings_payload: dict | None = None,
    probe=probe_text_provider,
) -> dict:
    saved_settings = (
        settings_payload if settings_payload is not None else get_settings()
    )
    settings = {**DEFAULT_TEXT_SETTINGS, **saved_settings}
    provider = str(
        payload.get("provider") or settings.get("textProvider") or "local"
    ).strip().lower()
    if provider not in {"local", "api"}:
        raise ValueError("不支持的文本推理来源")
    return probe(settings, provider)


def process_text_regenerate_block_request(
    payload: dict,
    settings_payload: dict | None = None,
    engine=regenerate_prompt_block,
) -> dict:
    target_block_id = str(payload.get("targetBlockId") or "").strip()
    blocks = payload.get("blocks")
    if not target_block_id or not isinstance(blocks, list):
        raise ValueError("缺少随机变体所需的结构块信息")
    saved_settings = (
        settings_payload if settings_payload is not None else get_settings()
    )
    settings = {**DEFAULT_TEXT_SETTINGS, **saved_settings}
    provider = str(
        payload.get("provider") or settings.get("textProvider") or "local"
    ).strip().lower()
    if provider not in {"local", "api"}:
        raise ValueError("不支持的文本推理来源")
    level = str(
        payload.get("expansionLevel")
        or settings.get("expansionLevel")
        or "balanced"
    ).strip().lower()
    if level not in {"strict", "balanced", "creative"}:
        level = "balanced"
    return engine(target_block_id, blocks, settings, provider, level)


def process_text_regenerate_blocks_request(
    payload: dict,
    settings_payload: dict | None = None,
    engine=regenerate_prompt_blocks,
) -> dict:
    target_block_ids = payload.get("targetBlockIds")
    blocks = payload.get("blocks")
    if not isinstance(target_block_ids, list) or not isinstance(blocks, list):
        raise ValueError("缺少联合随机所需的结构块信息")
    saved_settings = (
        settings_payload if settings_payload is not None else get_settings()
    )
    settings = {**DEFAULT_TEXT_SETTINGS, **saved_settings}
    provider = str(
        payload.get("provider") or settings.get("textProvider") or "local"
    ).strip().lower()
    if provider not in {"local", "api"}:
        raise ValueError("不支持的文本推理来源")
    level = str(
        payload.get("expansionLevel")
        or settings.get("expansionLevel")
        or "balanced"
    ).strip().lower()
    if level not in {"strict", "balanced", "creative"}:
        level = "balanced"
    return engine(target_block_ids, blocks, settings, provider, level)


def process_text_translate_pending_request(
    payload: dict,
    settings_payload: dict | None = None,
    engine=translate_pending_items,
) -> dict:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("没有需要本地 LLM 翻译的提示词内容")
    saved_settings = (
        settings_payload if settings_payload is not None else get_settings()
    )
    settings = {**DEFAULT_TEXT_SETTINGS, **saved_settings}
    return engine(items, settings)


def process_vision_analyze_request(
    payload: dict,
    settings_payload: dict | None = None,
    engine=analyze_image_bytes,
) -> dict:
    filename = str(payload.get("filename") or "").strip()
    mime_type = str(payload.get("mimeType") or "").strip().lower()
    encoded = payload.get("dataBase64")
    analyzer_ids = payload.get("analyzerIds")
    if (
        not filename
        or mime_type not in {"image/png", "image/jpeg", "image/webp"}
        or not isinstance(encoded, str)
        or not encoded
        or not isinstance(analyzer_ids, list)
        or not analyzer_ids
    ):
        raise ValueError("缺少有效的图片数据或识图模型")
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("图片数据不是有效的 Base64") from error
    if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("图片为空或超过 20 MB")
    detected_mime_type = detect_image_mime_type(image_bytes)
    if not detected_mime_type:
        raise ValueError("图片内容不是受支持的 PNG、JPG 或 WEBP 文件")
    if detected_mime_type != mime_type:
        raise ValueError("图片内容与声明的格式不一致，请重新选择文件")
    validate_image_bytes(image_bytes, mime_type)
    saved_settings = (
        settings_payload if settings_payload is not None else get_settings()
    )
    settings = {**DEFAULT_TEXT_SETTINGS, **saved_settings}
    return engine(image_bytes, filename, analyzer_ids, settings)


def model_profile_api_item(profile_id: str = DEFAULT_PROFILE_ID) -> dict:
    profile = load_model_profile(profile_id)
    return {
        **profile,
        "modelReference": model_reference(profile),
        "defaultParameters": profile_default_parameters(profile),
        "validatedResolutionPresets": validated_resolution_presets(profile),
        "candidateResolutionPresets": candidate_resolution_presets(profile),
        "compiledPositivePrefix": compile_positive_prefix(profile),
        "generationReady": bool(validated_resolution_presets(profile)),
    }


def process_recipe_resolve_request(
    payload: dict,
    *,
    db_path=None,
    snapshot_loader=model_profile_store.get_activated_profile_snapshot,
) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("配方请求必须是对象")
    allowed = {
        "profileId",
        "profileVersionId",
        "profileContentSha256",
        "prompts",
        "blocks",
        "loras",
        "parameterLayers",
        "randomPlan",
        "instructionHistory",
        "imageRefs",
        "sourceRefs",
        "metadata",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError("配方请求包含不允许的字段：" + ", ".join(unknown))
    profile_id = payload.get("profileId", DEFAULT_PROFILE_ID)
    profile_version_id = payload.get("profileVersionId")
    profile_content_sha256 = payload.get("profileContentSha256")
    if (profile_version_id is None) != (profile_content_sha256 is None):
        raise ValueError(
            "profileVersionId and profileContentSha256 must be provided together"
        )
    if profile_version_id is None:
        profile = load_model_profile(profile_id)
        profile_item = model_profile_api_item(profile_id)
        exact_lineage = {
            "profileVersionId": None,
            "profileContentSha256": None,
        }
    else:
        snapshot = snapshot_loader(
            profile_id,
            profile_version_id,
            str(profile_content_sha256).lower(),
            db_path=db_path,
        )
        if (
            snapshot.get("profileId") != profile_id
            or snapshot.get("profileVersionId") != profile_version_id
            or snapshot.get("profileContentSha256")
            != str(profile_content_sha256).lower()
        ):
            raise ValueError("激活模型档案返回了不匹配的精确版本")
        profile = deepcopy(snapshot["profile"])
        if "profileId" not in profile and "id" in profile:
            profile["profileId"] = profile.pop("id")
        if profile.get("profileId") != profile_id:
            raise ValueError("激活模型档案身份不匹配")
        exact_lineage = {
            "profileVersionId": profile_version_id,
            "profileContentSha256": str(profile_content_sha256).lower(),
        }
        profile_item = {
            **deepcopy(profile),
            **exact_lineage,
            "defaultParameters": profile_default_parameters(profile),
            "validatedResolutionPresets": validated_resolution_presets(profile),
            "candidateResolutionPresets": candidate_resolution_presets(profile),
            "compiledPositivePrefix": compile_positive_prefix(profile),
            "generationReady": bool(validated_resolution_presets(profile)),
        }
    layers = payload.get("parameterLayers", {})
    if not isinstance(layers, dict):
        raise ValueError("parameterLayers 必须是对象")
    allowed_layers = {
        "model_default",
        "lora_requirement",
        "task_preset",
        "manual_override",
    }
    unknown_layers = sorted(set(layers) - allowed_layers)
    if unknown_layers:
        raise ValueError(
            "parameterLayers 包含不允许的层：" + ", ".join(unknown_layers)
        )
    task_preset = deepcopy(layers.get("task_preset") or {})
    all_layers = [
        profile_default_parameters(profile),
        layers.get("lora_requirement") or {},
        task_preset,
        layers.get("manual_override") or {},
    ]
    if not any("resolution" in layer for layer in all_layers):
        task_preset["resolution"] = {"width": 1024, "height": 1024}
    if not any("generationSeed" in layer for layer in all_layers):
        task_preset["generationSeed"] = 0
    parameters = resolve_parameters(
        model_default=profile_default_parameters(profile),
        lora_requirement=layers.get("lora_requirement") or {},
        task_preset=task_preset,
        manual_override=layers.get("manual_override") or {},
        required_keys=REQUIRED_PARAMETER_KEYS,
    )
    metadata = deepcopy(payload.get("metadata") or {})
    if not isinstance(metadata, dict):
        raise ValueError("metadata 必须是对象")
    validated_sizes = validated_resolution_presets(profile)
    metadata.update(
        {
            "profileRevision": profile["schemaVersion"],
            "parameterPrecedence": [
                "manual_override",
                "task_preset",
                "lora_requirement",
                "model_default",
            ],
            "resolutionValidation": (
                "locally_validated" if validated_sizes else "unverified"
            ),
            "generationReady": bool(validated_sizes),
        }
    )
    recipe_value = build_recipe(
        model={**model_reference(profile), **exact_lineage},
        prompts=payload.get("prompts"),
        blocks=inflate_workbench_blocks(payload.get("blocks")),
        parameters=parameters,
        loras=payload.get("loras") or [],
        random_plan=payload.get("randomPlan"),
        instruction_history=payload.get("instructionHistory") or [],
        image_refs=payload.get("imageRefs") or [],
        source_refs=payload.get("sourceRefs") or [],
        metadata=metadata,
    )
    return {
        "recipe": recipe_value,
        "recipeHash": recipe_hash(recipe_value),
        "profile": profile_item,
    }


def enrich_prompt_version_recipe(version_payload: dict) -> dict:
    if not isinstance(version_payload, dict):
        raise ValueError("version 必须是对象")
    original = deepcopy(version_payload)
    metadata = deepcopy(original.get("metadata") or {})
    if not isinstance(metadata, dict):
        raise ValueError("version.metadata 必须是对象")
    if "recipe" in metadata:
        normalized = normalize_recipe(metadata["recipe"])
    else:
        resolved = process_recipe_resolve_request(
            {
                "profileId": metadata.get("modelProfileId", DEFAULT_PROFILE_ID),
                "prompts": {
                    "positiveEn": original.get("positiveEn", ""),
                    "positiveZh": original.get("positiveZh", ""),
                    "negativeEn": original.get("negativeEn", ""),
                    "negativeZh": original.get("negativeZh", ""),
                },
                "blocks": original.get("blocks", []),
                "loras": metadata.get("loras", []),
                "parameterLayers": metadata.get("parameterLayers", {}),
                "randomPlan": metadata.get("randomPlan"),
                "instructionHistory": metadata.get("instructionHistory", []),
                "imageRefs": metadata.get("imageRefs", []),
                "sourceRefs": metadata.get("sourceRefs", []),
                "metadata": {
                    "textMode": metadata.get("textMode", ""),
                    "draftInput": metadata.get("draftInput", ""),
                },
            }
        )
        normalized = resolved["recipe"]
    passthrough_metadata = {
        key: value
        for key, value in metadata.items()
        if key not in {"recipe", "recipeHash", "recipeSchemaVersion"}
    }
    projected = project_recipe_to_prompt_version(
        normalized,
        source=original.get("source", "manual"),
        base_version=original.get("baseVersion"),
        metadata=passthrough_metadata,
        block_projection="workbench-thirteen",
    )
    if "id" in original:
        projected["id"] = original["id"]
    return projected


def enrich_workspace_commit_payload(payload: dict) -> dict:
    enriched = deepcopy(payload)
    if isinstance(enriched, dict) and enriched.get("version") is not None:
        enriched["version"] = enrich_prompt_version_recipe(enriched["version"])
    return enriched


def random_catalog_api_item() -> dict:
    catalog = load_catalog(experimental=True)
    categories = []
    for category in catalog.categories:
        categories.append(
            {
                "id": category.category_id,
                "sourceFile": category.source_file,
                "primaryBlock": category.primary_block,
                "allowedBlocks": list(category.allowed_blocks),
                "entryCount": len(category.entries),
                "entries": [
                    {
                        "entryId": entry.entry_id,
                        "text": entry.text,
                        "primaryBlock": entry.primary_block,
                        "allowedBlocks": list(entry.allowed_blocks),
                    }
                    for entry in category.entries
                ],
            }
        )
    return {
        "schemaVersion": catalog.schema_version,
        "version": catalog.version,
        "contentSha256": catalog.content_sha256,
        "samplerVersion": catalog.sampler_version,
        "mappingVersion": catalog.mapping_version,
        "profile": catalog.profile,
        "runtimeReady": catalog.runtime_ready,
        "semanticReviewRequired": catalog.semantic_review_required,
        "experimental": True,
        "categories": categories,
    }


def process_random_plan_request(
    payload: dict,
    *,
    experimental_enabled: bool,
) -> dict:
    if not experimental_enabled:
        raise CatalogBoundaryError(
            "词库尚未完成人工语义审核；需由服务端显式开启实验模式"
        )
    if not isinstance(payload, dict):
        raise PlanRequestError("随机计划请求必须是对象")
    catalog = load_catalog(experimental=True)
    request = deepcopy(payload)
    if "librarySeed" not in request:
        request["librarySeed"] = generate_library_seed()
    request.setdefault("catalogVersion", catalog.version)
    request.setdefault("catalogContentSha256", catalog.content_sha256)
    request.setdefault("samplerVersion", catalog.sampler_version)
    request.setdefault("mappingVersion", catalog.mapping_version)
    request.setdefault("profile", catalog.profile)
    return resolve_random_plan(request, catalog=catalog)


def process_edit_preview_request(
    payload: dict,
    settings_payload: dict | None = None,
    engine=create_ai_edit_preview,
    normalizer=normalize_recipe,
) -> dict:
    if not isinstance(payload, dict):
        raise AIEditError("编辑预览请求必须是对象", "invalid_request", 400)
    allowed = {
        "instruction",
        "recipe",
        "baseRecipeHash",
        "provider",
        "targetModel",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise AIEditError(
            "编辑预览包含不允许的字段：" + ", ".join(unknown),
            "invalid_request",
            400,
        )
    saved_settings = (
        settings_payload if settings_payload is not None else get_settings()
    )
    settings = {**DEFAULT_TEXT_SETTINGS, **saved_settings}
    normalized = normalizer(payload.get("recipe"))
    return engine(
        payload.get("instruction"),
        normalized,
        payload.get("baseRecipeHash"),
        settings,
        str(payload.get("provider") or settings.get("textProvider") or "local"),
        str(payload.get("targetModel") or "anima"),
    )


def process_edit_apply_request(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise AIEditError("编辑确认请求必须是对象", "invalid_request", 400)
    allowed = {"recipe", "preview", "parentVersion", "newVersion"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise AIEditError(
            "编辑确认包含不允许的字段：" + ", ".join(unknown),
            "invalid_request",
            400,
        )
    normalized = normalize_recipe(payload.get("recipe"))
    result = apply_ai_edit_preview(
        normalized,
        payload.get("preview"),
        payload.get("parentVersion"),
        payload.get("newVersion"),
    )
    history = [*result["recipe"].get("instructionHistory", []), result["change"]]
    result["recipe"] = normalize_recipe(
        {**result["recipe"], "instructionHistory": history}
    )
    result["recipeHash"] = recipe_hash(result["recipe"])
    result["workbenchBlocks"] = project_blocks_to_workbench(
        result["recipe"]["blocks"]
    )
    return result


def process_edit_undo_request(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise EditEngineError("撤销请求必须是对象")
    allowed = {
        "currentRecipe",
        "parentRecipe",
        "currentVersion",
        "parentVersion",
        "newVersion",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise EditEngineError("撤销请求包含不允许的字段：" + ", ".join(unknown))
    current = normalize_recipe(payload.get("currentRecipe"))
    parent = normalize_recipe(payload.get("parentRecipe"))
    result = create_undo_version(
        current,
        parent,
        payload.get("currentVersion"),
        payload.get("parentVersion"),
        payload.get("newVersion"),
    )
    history = [*result["recipe"].get("instructionHistory", []), result["change"]]
    result["recipe"] = normalize_recipe(
        {**result["recipe"], "instructionHistory": history}
    )
    result["recipeHash"] = recipe_hash(result["recipe"])
    result["workbenchBlocks"] = project_blocks_to_workbench(
        result["recipe"]["blocks"]
    )
    return result


class PromptStudioHandler(SimpleHTTPRequestHandler):
    research_fetcher = staticmethod(model_research.production_fetcher)
    research_resolver = staticmethod(model_research.default_resolver)
    research_clock = staticmethod(prompt_db.now_iso)
    decomposition_provider = None

    def __init__(self, *args, **kwargs):
        self._response_started = False
        self._validated_content_length: int | None = None
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def setup(self) -> None:
        self._headers_complete = threading.Event()
        self._header_deadline_exceeded = False
        super().setup()
        self.connection.settimeout(CONNECTION_TIMEOUT_SECONDS)
        self._header_timer = threading.Timer(
            CONNECTION_TIMEOUT_SECONDS,
            self._expire_header_deadline,
        )
        self._header_timer.daemon = True
        self._header_timer.start()

    def _expire_header_deadline(self) -> None:
        if self._headers_complete.is_set():
            return
        self._header_deadline_exceeded = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def parse_request(self) -> bool:
        try:
            parsed = super().parse_request()
        except OSError:
            if not self._header_deadline_exceeded:
                raise
            parsed = False
        finally:
            self._headers_complete.set()
            self._header_timer.cancel()
        if not self._header_deadline_exceeded:
            return parsed
        self.close_connection = True
        return False

    def finish(self) -> None:
        if hasattr(self, "_headers_complete"):
            self._headers_complete.set()
        if hasattr(self, "_header_timer"):
            self._header_timer.cancel()
        super().finish()

    def log_message(self, format: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_response(self, code: int, message: str | None = None) -> None:
        self._response_started = True
        super().send_response(code, message)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "frame-ancestors 'none'; object-src 'none'; base-uri 'none'",
        )
        super().end_headers()

    def send_json(self, payload: dict, status: int = 200) -> None:
        try:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError):
            status = 500
            body = json.dumps(
                {"error": "服务器返回了无法序列化的数据"},
                ensure_ascii=False,
            ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(
        self,
        body: bytes,
        *,
        content_type: str,
        filename: str | None = None,
        status: int = 200,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{filename}"',
            )
        self.end_headers()
        self.wfile.write(body)

    def _validate_host_and_target(self) -> None:
        host_headers = self.headers.get_all("Host", [])
        if len(host_headers) != 1 or not is_allowed_host(host_headers[0]):
            raise RequestValidationError("请求 Host 不在本地允许列表中", status=421)
        if not self.path.startswith("/") or self.path.startswith("//"):
            raise RequestValidationError("请求目标格式无效")
        # ``urlparse(...).params`` only reports parameters attached to the
        # final path segment.  Reject literal path parameters in every segment
        # before routing, while leaving an encoded ``%3B`` available as ID
        # data and allowing semicolons in the query string.
        raw_path = self.path.partition("?")[0]
        if ";" in raw_path:
            raise RequestValidationError("请求目标不接受路径参数")

    def _validate_content_length(self, *, required: bool, max_bytes: int) -> int:
        transfer_encoding = self.headers.get_all("Transfer-Encoding", [])
        if transfer_encoding:
            raise RequestValidationError("不支持 Transfer-Encoding 请求体")
        raw_lengths = self.headers.get_all("Content-Length", [])
        if len(raw_lengths) > 1:
            raise RequestValidationError("Content-Length 必须且只能出现一次")
        if not raw_lengths:
            if required:
                raise RequestValidationError("缺少 Content-Length", status=411)
            return 0
        raw_length = raw_lengths[0].strip()
        if not raw_length.isascii() or not raw_length.isdigit():
            raise RequestValidationError("Content-Length 必须是非负十进制整数")
        length = int(raw_length, 10)
        if length > max_bytes:
            raise RequestValidationError("请求内容超过大小限制", status=413)
        return length

    def _validate_mutating_request(self) -> None:
        fetch_site_headers = self.headers.get_all("Sec-Fetch-Site", [])
        if len(fetch_site_headers) > 1:
            raise RequestValidationError("Sec-Fetch-Site 请求头无效", status=403)
        if fetch_site_headers and fetch_site_headers[0].strip().lower() not in {
            "none",
            "same-origin",
        }:
            raise RequestValidationError("拒绝跨站写操作", status=403)

        origin_headers = self.headers.get_all("Origin", [])
        host_header = self.headers.get("Host", "")
        if len(origin_headers) > 1 or (
            origin_headers and not is_same_origin(origin_headers[0], host_header)
        ):
            raise RequestValidationError("写操作必须来自当前本地页面", status=403)

        path = urlparse(self.path).path
        backup_upload = path in {
            "/api/backups/inspect",
            "/api/backups/stage-restore",
        }
        content_type_headers = self.headers.get_all("Content-Type", [])
        if len(content_type_headers) != 1:
            raise RequestValidationError(
                (
                    "备份上传必须声明 application/zip、application/json 或 application/octet-stream"
                    if backup_upload
                    else "写操作必须使用 application/json"
                ),
                status=415,
            )
        media_type, *parameters = [
            item.strip().lower() for item in content_type_headers[0].split(";")
        ]
        invalid_charset = any(
            parameter.startswith("charset=")
            and parameter.split("=", 1)[1].strip('"') not in {"utf-8", "utf8"}
            for parameter in parameters
        )
        allowed_media_types = (
            {"application/zip", "application/json", "application/octet-stream"}
            if backup_upload
            else {"application/json"}
        )
        if media_type not in allowed_media_types or invalid_charset:
            raise RequestValidationError(
                (
                    "备份上传格式不受支持"
                    if backup_upload
                    else "写操作必须使用 UTF-8 application/json"
                ),
                status=415,
            )

        max_bytes = (
            MAX_BACKUP_ARCHIVE_BYTES
            if backup_upload
            else (
                MAX_VISION_JSON_BODY_BYTES
                if path == "/api/vision/analyze"
                else MAX_JSON_BODY_BYTES
            )
        )
        self._validated_content_length = self._validate_content_length(
            required=self.command in {"POST", "PUT"},
            max_bytes=max_bytes,
        )
        if self.command == "DELETE" and self._validated_content_length:
            raise RequestValidationError("DELETE 请求不接受请求体")

    def _dispatch_request(self, action, *, mutating: bool = False) -> None:
        try:
            self._validate_host_and_target()
            if mutating:
                self._validate_mutating_request()
            action()
        except RequestValidationError as error:
            self._send_json_if_possible(
                self._request_validation_payload(str(error)),
                error.status,
            )
        except json.JSONDecodeError:
            self._send_json_if_possible(
                self._request_validation_payload("请求 JSON 无法解析"),
                400,
            )
        except VersionConflictError as error:
            self._send_json_if_possible(
                {
                    "error": str(error),
                    "code": "version_conflict",
                    "expectedVersion": error.expected,
                    "currentVersion": error.actual,
                },
                409,
            )
        except ProjectConflictError as error:
            self._send_json_if_possible(
                {
                    "error": str(error),
                    "code": "project_conflict",
                    "expected": error.expected,
                    "actual": error.actual,
                    "expectedUpdatedAt": error.expected,
                    "actualUpdatedAt": error.actual,
                },
                409,
            )
        except IdempotencyConflictError as error:
            self._send_json_if_possible(
                {
                    "error": str(error),
                    "code": "idempotency_conflict",
                    "operation": error.operation,
                },
                409,
            )
        except BackupConflictError as error:
            self._send_json_if_possible(
                {
                    "error": str(error),
                    "code": "backup_conflict",
                    "conflicts": list(error.conflicts),
                },
                409,
            )
        except lora_profile_store.LoraProfileConflictError as error:
            self._send_json_if_possible(
                {
                    "error": str(error),
                    "code": "lora_profile_conflict",
                    "expectedUpdatedAt": error.expected,
                    "actualUpdatedAt": error.actual,
                },
                409,
            )
        except BackupValidationError as error:
            self._send_json_if_possible(
                {"error": str(error), "code": "invalid_backup"},
                400,
            )
        except EditEngineError as error:
            self._send_json_if_possible(
                {"error": str(error), "code": error.code},
                error.status,
            )
        except AIEditError as error:
            self._send_json_if_possible(
                {"error": str(error), "code": error.code},
                error.status,
            )
        except model_research.ResearchError as error:
            self._send_json_if_possible(
                {"error": error.message, "code": error.code},
                400,
            )
        except model_profile_store.ProfileStoreError as error:
            self._send_json_if_possible(
                {"error": str(error), "code": error.code},
                409 if error.code in {
                    "active_version_changed", "profile_hash_mismatch"
                } else 400,
            )
        except ModelProfileError:
            self._send_json_if_possible(
                {
                    "error": "researched model profile is invalid",
                    "code": "invalid_model_profile",
                },
                422,
            )
        except LockedConflictError as error:
            detail = error.as_dict()
            self._send_json_if_possible(
                {"error": detail.pop("message"), **detail},
                409,
            )
        except CatalogBoundaryError as error:
            self._send_json_if_possible(
                {"error": str(error), "code": error.code},
                409,
            )
        except SamplingExhaustedError as error:
            self._send_json_if_possible(
                {"error": str(error), "code": error.code, "trace": error.trace},
                422,
            )
        except RandomSamplerError as error:
            self._send_json_if_possible(
                {"error": str(error), "code": error.code},
                400,
            )
        except ValueError as error:
            self._send_json_if_possible(
                self._request_validation_payload(str(error)),
                400,
            )
        except sqlite3.IntegrityError:
            self._send_json_if_possible({"error": "数据冲突，请刷新后重试"}, 409)
        except Exception as error:  # Keep request threads from leaking tracebacks.
            self.log_error("Unhandled request error: %s", type(error).__name__)
            self._send_json_if_possible({"error": "服务器内部错误"}, 500)

    def _send_json_if_possible(self, payload: dict, status: int) -> None:
        if self._response_started:
            self.close_connection = True
            return
        try:
            self.send_json(payload, status=status)
        except OSError:
            self.close_connection = True

    def _request_validation_payload(self, message: str) -> dict:
        payload = {"error": message}
        if (
            urlparse(self.path).path
            == "/api/creative-intake/decomposition-preview"
        ):
            payload["code"] = "invalid_request"
        return payload

    def read_idempotency_key(self) -> str | None:
        values = self.headers.get_all("Idempotency-Key") or []
        if len(values) > 1:
            raise RequestValidationError(
                "Idempotency-Key 请求头不能重复",
                status=400,
            )
        try:
            return validate_idempotency_key(values[0] if values else None)
        except ValueError as error:
            raise RequestValidationError(str(error), status=400) from error

    def read_binary(self, *, max_bytes: int = MAX_BACKUP_ARCHIVE_BYTES) -> bytes:
        length = self._validated_content_length
        if length is None:
            length = self._validate_content_length(
                required=True,
                max_bytes=max_bytes,
            )
        if length == 0:
            raise RequestValidationError("备份文件不能为空")
        previous_timeout = self.connection.gettimeout()
        deadline = time.monotonic() + BACKUP_UPLOAD_TIMEOUT_SECONDS
        chunks: list[bytes] = []
        remaining = length
        try:
            while remaining:
                time_left = deadline - time.monotonic()
                if time_left <= 0:
                    raise RequestValidationError("备份文件读取超过总时限")
                self.connection.settimeout(time_left)
                chunk = self.rfile.read1(remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        except (TimeoutError, OSError) as error:
            raise RequestValidationError("备份文件读取超时或不完整") from error
        finally:
            self.connection.settimeout(previous_timeout)
        body = b"".join(chunks)
        if len(body) != length:
            raise RequestValidationError("备份文件长度与 Content-Length 不一致")
        return body

    def read_json(self) -> dict:
        length = self._validated_content_length
        if length is None:
            length = self._validate_content_length(
                required=True,
                max_bytes=MAX_JSON_BODY_BYTES,
            )
        if length == 0:
            return {}
        previous_timeout = self.connection.gettimeout()
        timeout_seconds = (
            VISION_REQUEST_BODY_TIMEOUT_SECONDS
            if urlparse(self.path).path == "/api/vision/analyze"
            else REQUEST_BODY_TIMEOUT_SECONDS
        )
        deadline = time.monotonic() + timeout_seconds
        chunks: list[bytes] = []
        remaining = length
        try:
            while remaining:
                time_left = deadline - time.monotonic()
                if time_left <= 0:
                    raise RequestValidationError("请求体读取超过总时限")
                self.connection.settimeout(time_left)
                chunk = self.rfile.read1(remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        except (TimeoutError, OSError) as error:
            raise RequestValidationError("请求体读取超时或不完整") from error
        finally:
            self.connection.settimeout(previous_timeout)
        raw_body = b"".join(chunks)
        if len(raw_body) != length:
            raise RequestValidationError("请求体长度与 Content-Length 不一致")
        try:
            body = raw_body.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("请求 JSON 必须使用 UTF-8 编码") from error
        try:
            payload = json.loads(
                body,
                parse_constant=reject_json_constant,
                parse_float=strict_json_float,
                object_pairs_hook=strict_json_object_pairs,
            )
        except RecursionError as error:
            raise ValueError("请求 JSON 嵌套层级过深") from error
        if not isinstance(payload, dict):
            raise ValueError("请求 JSON 顶层必须是对象")
        return payload

    def send_bad_json(self) -> None:
        self.send_json({"error": "请求 JSON 无法解析"}, status=400)

    def send_upstream_error(self, error: Exception) -> None:
        detail = "AnimaDex 未启动或暂时不可访问"
        if isinstance(error, HTTPError):
            detail = f"AnimaDex 返回 HTTP {error.code}"
        self.send_json(
            {
                "connected": False,
                "source": "demo",
                "error": detail,
                "upstream": ANIMADEX_URL,
            },
            status=503,
        )

    def do_GET(self) -> None:
        self._dispatch_request(self._route_get)

    def _route_get(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/prompt-templates" or parsed.path.startswith(
            "/api/prompt-templates/"
        ):
            return self.handle_prompt_templates_get(parsed.path)
        if parsed.path == "/api/projects" or parsed.path.startswith("/api/projects/"):
            return self.handle_projects_get(parsed.path)
        if parsed.path == "/api/favorites":
            return self.handle_favorites_get(parsed.query)
        if parsed.path == "/api/settings":
            return self.handle_settings_get()
        if parsed.path == "/api/model-profiles":
            return self.handle_model_profiles_get("")
        if parsed.path.startswith("/api/model-profiles/"):
            return self.handle_model_profiles_get(
                unquote(parsed.path.rsplit("/", 1)[-1])
            )
        if parsed.path == "/api/lora-profiles":
            return self.handle_lora_profiles_get("")
        if parsed.path.startswith("/api/lora-profiles/"):
            raw_id = parsed.path[len("/api/lora-profiles/") :]
            if not raw_id or "/" in raw_id or "\\" in raw_id:
                return self.send_json(
                    {"error": "LoRA 档案 ID 无效", "code": "invalid_id"},
                    status=400,
                )
            return self.handle_lora_profiles_get(unquote(raw_id))
        research_parts = parsed.path.split("/")
        if (
            len(research_parts) == 4
            and research_parts[1:3] == ["api", "model-research"]
            and research_parts[3]
        ):
            return self.handle_model_research_get(
                unquote(research_parts[3])
            )
        if parsed.path.startswith("/api/model-research/"):
            return self.send_json(
                {"error": "research route not found", "code": "not_found"},
                status=404,
            )
        version_parts = parsed.path.split("/")
        if (
            len(version_parts) == 4
            and version_parts[1:3] == ["api", "model-profile-versions"]
            and version_parts[3]
        ):
            return self.handle_model_profile_version_get(
                unquote(version_parts[3])
            )
        if parsed.path.startswith("/api/model-profile-versions/"):
            return self.send_json(
                {"error": "profile version route not found", "code": "not_found"},
                status=404,
            )
        if parsed.path == "/api/text/random-catalog":
            return self.handle_random_catalog_get()
        if parsed.path == "/api/backups/export":
            return self.handle_backup_export(parsed.query)
        if parsed.path == "/api/local-llm/status":
            return self.send_json({"item": local_llm_status()})
        if parsed.path == "/api/vision/status":
            return self.send_json({"item": vision_model_status()})
        if parsed.path == "/api/animadex/status":
            return self.handle_status()
        if parsed.path == "/api/animadex/resources":
            return self.handle_resources(parsed.query)
        if parsed.path.startswith("/api/animadex/thumb/"):
            return self.handle_thumbnail(parsed.path)
        if not is_public_static_path(parsed.path):
            return self.send_error(404)
        return super().do_GET()

    def do_HEAD(self) -> None:
        self._dispatch_request(self._route_head)

    def _route_head(self) -> None:
        parsed = urlparse(self.path)
        if not is_public_static_path(parsed.path):
            return self.send_error(404)
        return super().do_HEAD()

    def do_POST(self) -> None:
        self._dispatch_request(self._route_post, mutating=True)

    def _route_post(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/text/expand":
            return self.handle_text_expand()
        if parsed.path == "/api/text/random":
            return self.handle_text_random()
        if parsed.path == "/api/text/random-plan":
            return self.handle_random_plan()
        if parsed.path == "/api/recipe/resolve":
            return self.handle_recipe_resolve()
        if parsed.path == "/api/text/edit-preview":
            return self.handle_edit_preview()
        if parsed.path == "/api/text/edit-apply":
            return self.handle_edit_apply()
        if parsed.path == "/api/text/edit-undo":
            return self.handle_edit_undo()
        if parsed.path == "/api/text/decompose":
            return self.handle_text_decompose()
        if parsed.path == "/api/text/regenerate-block":
            return self.handle_text_regenerate_block()
        if parsed.path == "/api/text/regenerate-blocks":
            return self.handle_text_regenerate_blocks()
        if parsed.path == "/api/text/translate-pending":
            return self.handle_text_translate_pending()
        if parsed.path == "/api/vision/analyze":
            return self.handle_vision_analyze()
        if parsed.path == "/api/text/provider-test":
            return self.handle_text_provider_test()
        if parsed.path == "/api/local-llm/start":
            return self.handle_local_llm_start()
        if parsed.path == "/api/local-llm/stop":
            return self.handle_local_llm_stop()
        if parsed.path == "/api/workspace/commit":
            return self.handle_workspace_commit()
        if parsed.path == "/api/creative-intake/transition":
            return self.handle_creative_intake_transition()
        if parsed.path == "/api/creative-intake/director":
            return self.handle_creative_director()
        if parsed.path == "/api/creative-intake/decomposition-preview":
            return self.handle_model_adapted_decomposition()
        if parsed.path == "/api/model-research":
            return self.handle_model_research_create()
        if parsed.path == "/api/lora-profiles":
            return self.handle_lora_profile_create()
        research_version_parts = parsed.path.split("/")
        if (
            len(research_version_parts) == 5
            and research_version_parts[1:3] == ["api", "model-profile-versions"]
            and research_version_parts[3]
            and research_version_parts[4] in {"review", "activate"}
        ):
            version_id = unquote(research_version_parts[3])
            if research_version_parts[4] == "review":
                return self.handle_model_profile_review(version_id)
            return self.handle_model_profile_activate(version_id)
        if parsed.path == "/api/backups/inspect":
            return self.handle_backup_inspect(parsed.query)
        if parsed.path == "/api/backups/stage-restore":
            return self.handle_backup_stage_restore(parsed.query)
        if parsed.path == "/api/projects":
            return self.handle_project_create()
        version_parts = parsed.path.split("/")
        if (
            len(version_parts) == 5
            and version_parts[1:3] == ["api", "projects"]
            and version_parts[3]
            and version_parts[4] == "versions"
            and not parsed.params
        ):
            project_id = unquote(version_parts[3])
            return self.handle_version_create(project_id)
        if parsed.path == "/api/favorites":
            return self.handle_favorite_upsert()
        return self.send_json({"error": "未找到 API"}, status=404)

    def do_PUT(self) -> None:
        self._dispatch_request(self._route_put, mutating=True)

    def _route_put(self) -> None:
        parsed = urlparse(self.path)
        template_parts = parsed.path.split("/")
        if (
            len(template_parts) == 4
            and template_parts[1:3] == ["api", "prompt-templates"]
            and template_parts[3]
            and not parsed.params
        ):
            template_id = unquote(parsed.path.rsplit("/", 1)[-1])
            return self.handle_prompt_template_put(template_id)
        if (
            len(template_parts) == 4
            and template_parts[1:3] == ["api", "projects"]
            and template_parts[3]
            and not parsed.params
        ):
            project_id = unquote(template_parts[3])
            return self.handle_project_update(project_id)
        if parsed.path == "/api/settings":
            return self.handle_settings_put()
        if parsed.path.startswith("/api/lora-profiles/"):
            raw_id = parsed.path[len("/api/lora-profiles/") :]
            if not raw_id or "/" in raw_id or "\\" in raw_id:
                return self.send_json(
                    {"error": "LoRA 档案 ID 无效", "code": "invalid_id"},
                    status=400,
                )
            return self.handle_lora_profile_update(unquote(raw_id))
        version_parts = parsed.path.split("/")
        if (
            len(version_parts) == 4
            and version_parts[1:3] == ["api", "model-profile-versions"]
            and version_parts[3]
        ):
            return self.handle_model_profile_version_put(unquote(version_parts[3]))
        return self.send_json({"error": "未找到 API"}, status=404)

    def do_DELETE(self) -> None:
        self._dispatch_request(self._route_delete, mutating=True)

    def _route_delete(self) -> None:
        parsed = urlparse(self.path)
        favorite_parts = parsed.path.split("/")
        if (
            len(favorite_parts) == 4
            and favorite_parts[1:3] == ["api", "favorites"]
            and favorite_parts[3]
            and not parsed.params
        ):
            favorite_id = unquote(favorite_parts[3])
            return self.handle_favorite_delete(favorite_id)
        if parsed.path.startswith("/api/lora-profiles/"):
            raw_id = parsed.path[len("/api/lora-profiles/") :]
            if not raw_id or "/" in raw_id or "\\" in raw_id:
                return self.send_json(
                    {"error": "LoRA 档案 ID 无效", "code": "invalid_id"},
                    status=400,
                )
            query = parse_qs(parsed.query, keep_blank_values=True)
            if set(query) != {"baseUpdatedAt"} or len(query["baseUpdatedAt"]) != 1:
                return self.send_json(
                    {"error": "baseUpdatedAt 查询参数无效"}, status=400
                )
            return self.handle_lora_profile_delete(
                unquote(raw_id), query["baseUpdatedAt"][0]
            )
        return self.send_json({"error": "未找到 API"}, status=404)

    def handle_projects_get(self, path: str) -> None:
        if path == "/api/projects":
            return self.send_json({"items": list_projects()})
        prefix = "/api/projects/"
        raw_project_id = path[len(prefix) :]
        if not raw_project_id or "/" in raw_project_id or "\\" in raw_project_id:
            return self.send_json({"error": "作品 ID 无效"}, status=400)
        project_id = unquote(raw_project_id)
        project = get_project(project_id)
        if not project:
            return self.send_json({"error": "作品不存在"}, status=404)
        self.send_json({"item": project})

    def handle_prompt_templates_get(self, path: str) -> None:
        if path == "/api/prompt-templates":
            items = [
                read_prompt_template(template_id)
                for template_id in PROMPT_TEMPLATES
            ]
            return self.send_json({"items": [item for item in items if item]})
        prefix = "/api/prompt-templates/"
        template_id = unquote(path[len(prefix) :])
        if not template_id or "/" in template_id:
            return self.send_json({"error": "模板 ID 无效"}, status=400)
        item = read_prompt_template(template_id)
        if not item:
            return self.send_json({"error": "模板不存在"}, status=404)
        self.send_json({"item": item})

    def handle_prompt_template_put(self, template_id: str) -> None:
        if not template_id or "/" in template_id:
            return self.send_json({"error": "模板 ID 无效"}, status=400)
        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        content = payload.get("content")
        if not isinstance(content, str):
            return self.send_json({"error": "模板内容必须是字符串"}, status=400)
        item = write_prompt_template(template_id, content)
        if not item:
            return self.send_json({"error": "模板不存在"}, status=404)
        self.send_json({"item": item})

    def handle_project_create(self) -> None:
        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        project = create_project(
            payload,
            idempotency_key=self.read_idempotency_key(),
        )
        self.send_json({"item": project}, status=201)

    def handle_workspace_commit(self) -> None:
        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        payload = enrich_workspace_commit_payload(payload)
        result = commit_workspace(
            payload,
            idempotency_key=self.read_idempotency_key(),
        )
        status = 201 if result.get("createdProject") or result.get("version") else 200
        self.send_json({"item": result}, status=status)

    @staticmethod
    def _backup_query(query: str, allowed: set[str]) -> dict[str, str]:
        values = parse_qs(query, keep_blank_values=True)
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise RequestValidationError(
                "备份请求包含不允许的查询参数：" + ", ".join(unknown)
            )
        result: dict[str, str] = {}
        for key, items in values.items():
            if len(items) != 1:
                raise RequestValidationError(f"备份查询参数 {key} 不能重复")
            result[key] = items[0]
        return result

    def handle_backup_export(self, query: str) -> None:
        options = self._backup_query(query, {"scope", "projectId"})
        scope = options.get("scope", "full")
        project_id = options.get("projectId")
        if scope not in {"full", "project"}:
            raise RequestValidationError("备份 scope 必须是 full 或 project")
        if scope == "project" and not project_id:
            raise RequestValidationError("项目备份必须提供 projectId")
        if scope == "full" and project_id is not None:
            raise RequestValidationError("完整备份不能提供 projectId")
        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        filename = f"prompt-studio-{scope}-{timestamp}.zip"
        with tempfile.TemporaryDirectory(prefix="prompt-studio-export-") as directory:
            destination = Path(directory) / filename
            if scope == "project":
                export_project(
                    destination,
                    project_id,
                    db_path=prompt_db.DEFAULT_DB_PATH,
                    archive_format="zip",
                )
            else:
                export_database(
                    destination,
                    db_path=prompt_db.DEFAULT_DB_PATH,
                    archive_format="zip",
                )
            body = destination.read_bytes()
        self.send_bytes(
            body,
            content_type="application/zip",
            filename=filename,
        )

    def _uploaded_backup_file(self, directory: str) -> Path:
        body = self.read_binary()
        source = Path(directory) / "uploaded.backup"
        source.write_bytes(body)
        return source

    def handle_backup_inspect(self, query: str) -> None:
        self._backup_query(query, set())
        with tempfile.TemporaryDirectory(prefix="prompt-studio-inspect-") as directory:
            report = inspect_backup(self._uploaded_backup_file(directory))
        self.send_json({"item": report})

    @staticmethod
    def _clone_current_database(target: Path) -> None:
        source_connection = sqlite3.connect(
            f"{prompt_db.DEFAULT_DB_PATH.resolve().as_uri()}?mode=ro",
            uri=True,
        )
        target_connection = sqlite3.connect(target)
        try:
            source_connection.execute("PRAGMA query_only = ON")
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()

    def handle_backup_stage_restore(self, query: str) -> None:
        options = self._backup_query(query, {"conflict"})
        conflict = options.get("conflict", "reject")
        if conflict not in {"reject", "rename"}:
            raise RequestValidationError("恢复冲突策略必须是 reject 或 rename")
        with tempfile.TemporaryDirectory(prefix="prompt-studio-restore-") as directory:
            source = self._uploaded_backup_file(directory)
            inspection = inspect_backup(source)
            target_directory = recovery_directory(prompt_db.DEFAULT_DB_PATH)
            timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            target = target_directory / (
                f"restore-preview-{timestamp}-{uuid.uuid4().hex[:8]}.db"
            )
            with BACKUP_STAGE_LOCK:
                target_directory.mkdir(parents=True, exist_ok=True)
                try:
                    if inspection["manifest"]["scope"] == "project":
                        self._clone_current_database(target)
                    report = restore_backup(source, target, conflict=conflict)
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
        self.send_json(
            {
                "item": {
                    **report,
                    "inspection": inspection,
                    "stagingDatabase": str(target),
                    "activated": False,
                }
            },
            status=201,
        )

    def handle_model_profiles_get(self, profile_id: str) -> None:
        researched = model_profile_store.list_active_profile_versions(
            db_path=prompt_db.DEFAULT_DB_PATH
        )
        researched_items = []
        for version in researched:
            try:
                self._require_profile_hash(version)
                researched_items.append(self._researched_profile_api_item(version))
            except (ModelProfileError, model_profile_store.ProfileStoreError):
                continue
        if profile_id:
            for item in researched_items:
                if item["profileId"] == profile_id:
                    return self.send_json({"item": item})
            return self.send_json({"item": model_profile_api_item(profile_id)})
        items = [
            model_profile_api_item(profile["profileId"])
            for profile in list_model_profiles()
        ]
        researched_ids = {item["profileId"] for item in researched_items}
        self.send_json({
            "items": researched_items + [
                item for item in items if item["profileId"] not in researched_ids
            ]
        })

    def handle_lora_profiles_get(self, profile_id: str) -> None:
        if not profile_id:
            return self.send_json(
                {"items": lora_profile_store.list_lora_profiles()}
            )
        item = lora_profile_store.get_lora_profile(profile_id)
        if item is None:
            return self.send_json(
                {"error": "LoRA 档案不存在", "code": "not_found"},
                status=404,
            )
        self.send_json({"item": item})

    def handle_lora_profile_create(self) -> None:
        item = lora_profile_store.create_lora_profile(self.read_json())
        self.send_json({"item": item}, status=201)

    def handle_lora_profile_update(self, profile_id: str) -> None:
        item = lora_profile_store.update_lora_profile(
            profile_id, self.read_json()
        )
        if item is None:
            return self.send_json(
                {"error": "LoRA 档案不存在", "code": "not_found"},
                status=404,
            )
        self.send_json({"item": item})

    def handle_lora_profile_delete(
        self, profile_id: str, base_updated_at: str
    ) -> None:
        deleted = lora_profile_store.delete_lora_profile(
            profile_id, base_updated_at=base_updated_at
        )
        if not deleted:
            return self.send_json(
                {"error": "LoRA 档案不存在", "code": "not_found"},
                status=404,
            )
        self.send_json({"deleted": True, "id": profile_id})

    @staticmethod
    def _strict_payload(payload: dict, allowed: set[str]) -> None:
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise model_research.ResearchError(
                "invalid_request",
                "unsupported fields: " + ", ".join(unknown),
            )

    @staticmethod
    def _researched_profile_api_item(version: dict) -> dict:
        profile = deepcopy(version["profile"])
        profile_id = profile.pop("id", profile.get("profileId"))
        profile["profileId"] = profile_id
        profile = validate_researched_model_profile(profile)
        research = profile.get("metadata", {}).get("research", {})
        warnings = list(research.get("warnings", []))
        evidence = profile.get("evidence", [])
        validated = profile.get("resolutions", {}).get("validatedPresets", [])
        return {
            **profile,
            "profileVersionId": version["versionId"],
            "profileContentSha256": version["contentSha256"],
            "evidenceSummary": {
                "snapshotCount": len(evidence),
                "succeededCount": sum(
                    item.get("fetchStatus") == "succeeded"
                    for item in evidence if isinstance(item, dict)
                ),
            },
            "warnings": warnings,
            "generationReady": bool(validated),
        }

    @staticmethod
    def _validate_stored_researched_profile(profile: dict) -> dict:
        candidate = deepcopy(profile)
        candidate["profileId"] = candidate.pop("id", candidate.get("profileId"))
        return validate_researched_model_profile(candidate)

    @staticmethod
    def _require_profile_hash(version: dict) -> None:
        actual = prompt_db.canonical_json_hash(version["profile"])
        if actual != version.get("contentSha256"):
            raise model_profile_store.ProfileStoreError(
                "profile_hash_mismatch",
                "stored profile content hash does not match its canonical content",
            )

    def handle_model_research_create(self) -> None:
        payload = self.read_json()
        self._strict_payload(payload, {"sourceUrl"})
        source_url = payload.get("sourceUrl")
        request = model_research.ResearchRequest(source_url)
        result = model_research.research_source(
            request,
            registry=model_research.default_adapter_registry(),
            fetcher=self.research_fetcher,
            resolver=self.research_resolver,
            clock=self.research_clock,
        )
        pending = model_profile_store.create_research_run(
            request.source_url, db_path=prompt_db.DEFAULT_DB_PATH
        )
        run = model_profile_store.complete_research_run(
            pending["runId"],
            [result.snapshot],
            result.claims,
            db_path=prompt_db.DEFAULT_DB_PATH,
        )
        profile = model_research.build_pending_profile(
            request.source_url, [result.snapshot], result.claims
        )
        if result.snapshot.fetch_status == "failed":
            profile["metadata"]["research"]["warnings"] = list(dict.fromkeys([
                *profile["metadata"]["research"]["warnings"],
                "retryable_fetch_failure",
            ]))
        self._validate_stored_researched_profile(profile)
        draft = model_profile_store.create_profile_draft(
            run["runId"], profile, result.claims,
            db_path=prompt_db.DEFAULT_DB_PATH,
        )
        self.send_json(
            {
                "item": {
                    "run": run,
                    "snapshots": run["snapshots"],
                    "claims": run["claims"],
                    "draftVersion": draft,
                    "researchStatus": profile["metadata"]["research"]["researchStatus"],
                    "warnings": profile["metadata"]["research"]["warnings"],
                }
            },
            status=201,
        )

    def handle_model_research_get(self, run_id: str) -> None:
        if not run_id or "/" in run_id or "\\" in run_id:
            raise model_research.ResearchError("invalid_run_id", "invalid run ID")
        prompt_db.init_db(prompt_db.DEFAULT_DB_PATH)
        with prompt_db.database(prompt_db.DEFAULT_DB_PATH) as connection:
            row = prompt_db.research_run_row(connection, run_id)
            if row is None:
                return self.send_json(
                    {"error": "research run not found", "code": "unknown_run"},
                    status=404,
                )
            item = model_profile_store._run_result(connection, row)
        self.send_json({"item": item})

    def handle_model_profile_version_get(self, version_id: str) -> None:
        if not version_id or "/" in version_id or "\\" in version_id:
            raise model_profile_store.ProfileStoreError(
                "unknown_version", "profile version does not exist"
            )
        item = model_profile_store.get_profile_version(
            version_id, db_path=prompt_db.DEFAULT_DB_PATH
        )
        if item is None:
            raise model_profile_store.ProfileStoreError(
                "unknown_version", "profile version does not exist"
            )
        self._require_profile_hash(item)
        self.send_json({"item": item})

    def handle_model_profile_version_put(self, version_id: str) -> None:
        payload = self.read_json()
        self._strict_payload(
            payload, {"claimDecisions", "manualFields", "reviewNote"}
        )
        decisions = payload.get("claimDecisions", {})
        manual = payload.get("manualFields", {})
        note = payload.get("reviewNote")
        if not isinstance(decisions, dict) or not isinstance(manual, dict):
            raise model_research.ResearchError(
                "invalid_request", "claimDecisions and manualFields must be objects"
            )
        parent = model_profile_store.get_profile_version(
            version_id, db_path=prompt_db.DEFAULT_DB_PATH
        )
        if parent is None:
            raise model_profile_store.ProfileStoreError(
                "unknown_version", "profile version does not exist"
            )
        if parent["lifecycleStatus"] != "draft":
            raise model_profile_store.ProfileStoreError(
                "not_draft", "only a draft may be revised"
            )
        allowed_manual = {
            "displayName",
            "model.family",
            "model.versionName",
            "model.versionId",
            "model.baseModel",
            "notes",
        }
        if any(path not in allowed_manual for path in manual):
            raise model_research.ResearchError(
                "unsupported_field", "manual field is not allowlisted"
            )
        prompt_db.init_db(prompt_db.DEFAULT_DB_PATH)
        with prompt_db.database(prompt_db.DEFAULT_DB_PATH) as connection:
            claims = [
                model_research.normalize_claim(item)
                for item in model_profile_store._claims_by_id(
                    connection, parent["researchRunId"]
                ).values()
            ]
        projection_manual = {
            path: value for path, value in manual.items()
            if path != "notes"
        }
        projected, _ = model_research.apply_claim_decisions(
            parent["profile"], claims, decisions, projection_manual
        )
        if "displayName" in manual:
            display_name = manual["displayName"]
            if (
                not isinstance(display_name, str)
                or not display_name.strip()
                or len(display_name.strip()) > 256
            ):
                raise model_research.ResearchError(
                    "invalid_request", "displayName is invalid"
                )
            projected["displayName"] = display_name.strip()
        if "notes" in manual:
            notes = manual["notes"]
            if not isinstance(notes, str) or len(notes) > 4096:
                raise model_research.ResearchError(
                    "invalid_request", "notes is invalid"
                )
            note_parts = [
                item.strip()
                for item in (note, notes)
                if isinstance(item, str) and item.strip()
            ]
            note = "\n\n".join(note_parts)
            if len(note) > 4096:
                raise model_research.ResearchError(
                    "invalid_request", "combined review note is too long"
                )
        projected["id"] = parent["profileId"]
        projected.pop("profileId", None)
        self._validate_stored_researched_profile(projected)
        item = model_profile_store.revise_profile_draft(
            version_id, projected, decisions, note,
            db_path=prompt_db.DEFAULT_DB_PATH,
        )
        self.send_json({"item": item})

    def handle_model_profile_review(self, version_id: str) -> None:
        payload = self.read_json()
        self._strict_payload(payload, {"reviewerNote"})
        version = model_profile_store.get_profile_version(
            version_id, db_path=prompt_db.DEFAULT_DB_PATH
        )
        if version is None:
            raise model_profile_store.ProfileStoreError(
                "unknown_version", "profile version does not exist"
            )
        model = version["profile"].get("model", {})
        display_name = version["profile"].get("displayName")
        version_name = model.get("versionName")
        exact_model_version_id = model.get("versionId")
        audit = (
            version["profile"].get("metadata", {})
            .get("research", {})
            .get("claimAudit", [])
        )
        verified_name = any(
            isinstance(item, dict)
            and item.get("fieldPath") == "displayName"
            and item.get("applicationStatus") == "approved"
            and item.get("evidenceClass") in {"original_source", "user_supplied"}
            for item in audit
        )
        if (
            not isinstance(display_name, str)
            or not display_name.strip()
            or not isinstance(version_name, str)
            or not version_name.strip()
            or not isinstance(exact_model_version_id, int)
            or isinstance(exact_model_version_id, bool)
            or display_name == "Pending model research"
            or not verified_name
        ):
            raise model_profile_store.ProfileStoreError(
                "unresolved_model_identity",
                "exact model version identity must be resolved before review",
            )
        self._validate_stored_researched_profile(version["profile"])
        item = model_profile_store.review_profile_version(
            version_id, payload.get("reviewerNote"),
            db_path=prompt_db.DEFAULT_DB_PATH,
        )
        self.send_json({"item": item})

    def handle_model_profile_activate(self, version_id: str) -> None:
        payload = self.read_json()
        self._strict_payload(payload, {"expectedActiveVersionId"})
        if "expectedActiveVersionId" not in payload:
            raise model_research.ResearchError(
                "invalid_request", "expectedActiveVersionId is required"
            )
        version = model_profile_store.get_profile_version(
            version_id, db_path=prompt_db.DEFAULT_DB_PATH
        )
        if version is None:
            raise model_profile_store.ProfileStoreError(
                "unknown_version", "profile version does not exist"
            )
        self._require_profile_hash(version)
        self._validate_stored_researched_profile(version["profile"])
        item = model_profile_store.activate_profile_version(
            version_id, payload["expectedActiveVersionId"],
            db_path=prompt_db.DEFAULT_DB_PATH,
        )
        self.send_json({"item": item})

    def handle_random_catalog_get(self) -> None:
        self.send_json({"item": random_catalog_api_item()})

    def handle_random_plan(self) -> None:
        payload = self.read_json()
        item = process_random_plan_request(
            payload,
            experimental_enabled=(
                os.environ.get("PROMPT_STUDIO_EXPERIMENTAL_WORDLISTS") == "1"
            ),
        )
        self.send_json({"item": item}, status=201)

    def handle_recipe_resolve(self) -> None:
        item = process_recipe_resolve_request(self.read_json())
        self.send_json({"item": item})

    def handle_creative_intake_transition(self) -> None:
        payload = self.read_json()
        unknown = set(payload) - {"current", "action"}
        if unknown:
            return self.send_json(
                {
                    "error": f"unsupported fields: {', '.join(sorted(unknown))}",
                    "code": "invalid_request",
                },
                status=400,
            )
        try:
            item = apply_creative_intake_transition(
                payload.get("current"),
                payload.get("action"),
            )
        except CreativeIntakeValidationError as error:
            return self.send_json(
                {"error": str(error), "code": error.code},
                status=400,
            )
        self.send_json({"item": item})

    def handle_creative_director(self) -> None:
        try:
            result = process_creative_director_request(self.read_json())
        except CreativeIntakeValidationError as error:
            return self.send_json(
                {
                    "error": "创作状态转换被拒绝",
                    "code": error.code,
                },
                status=400,
            )
        except PromptEngineError as error:
            safe_errors = {
                "invalid_creative_director_request": (
                    "创意导演请求无效",
                    400,
                ),
                "unsupported_provider": ("文本推理来源不受支持", 400),
                "provider_not_configured": ("文本推理来源尚未配置完整", 400),
                "upstream_http_error": ("创意导演模型请求失败", 502),
                "model_unavailable": ("创意导演模型暂时不可用", 503),
                "upstream_invalid_response": (
                    "创意导演模型返回无效响应",
                    502,
                ),
                "invalid_model_output": ("创意导演模型输出无效", 502),
                "provider_secret_echo": ("创意导演模型输出被安全策略拒绝", 502),
            }
            code = error.code if error.code in safe_errors else "creative_director_error"
            message, status = safe_errors.get(
                code,
                ("创意导演请求失败", 500),
            )
            return self.send_json(
                {"error": message, "code": code},
                status=status,
            )
        self.send_json(result)

    def handle_model_adapted_decomposition(self) -> None:
        try:
            provider = self.decomposition_provider
            if provider is None:
                settings = {**DEFAULT_TEXT_SETTINGS, **get_settings()}
                provider = configured_decomposition_provider(
                    settings,
                    settings.get("textProvider") or "local",
                )
            result = process_model_adapted_decomposition_request(
                self.read_json(),
                provider=provider,
                db_path=prompt_db.DEFAULT_DB_PATH,
                snapshot_loader=model_profile_store.get_activated_profile_snapshot,
            )
        except model_profile_store.ProfileStoreError as error:
            status = 409 if error.code in {
                "unknown_version",
                "profile_version_mismatch",
                "profile_not_activated",
                "profile_hash_mismatch",
            } else 400
            return self.send_json(
                {"error": "目标模型档案不可用于当前预览", "code": error.code},
                status=status,
            )
        except DecompositionError as error:
            statuses = {
                "invalid_request": 400,
                "stale_intake": 409,
                "locked_fact_changed": 422,
                "invalid_provider_output": 502,
                "provider_failed": 502,
            }
            return self.send_json(
                {"error": "模型适配拆解预览失败", "code": error.code},
                status=statuses.get(error.code, 422),
            )
        except PromptEngineError as error:
            return self.send_json(
                {"error": "文本推理来源不可用", "code": error.code},
                status=error.status,
            )
        self.send_json(result)

    def handle_edit_preview(self) -> None:
        item = process_edit_preview_request(self.read_json())
        self.send_json({"item": item})

    def handle_edit_apply(self) -> None:
        item = process_edit_apply_request(self.read_json())
        self.send_json({"item": item})

    def handle_edit_undo(self) -> None:
        item = process_edit_undo_request(self.read_json())
        self.send_json({"item": item})

    def handle_project_update(self, project_id: str) -> None:
        if not project_id or "\\" in project_id:
            return self.send_json({"error": "作品 ID 无效"}, status=400)
        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        if "baseUpdatedAt" not in payload:
            return self.send_json(
                {
                    "error": "更新作品必须提供 baseUpdatedAt 并发基线",
                    "code": "project_precondition_required",
                },
                status=428,
            )
        project = update_project(
            project_id,
            payload,
            idempotency_key=self.read_idempotency_key(),
        )
        if not project:
            return self.send_json({"error": "作品不存在"}, status=404)
        self.send_json({"item": project})

    def handle_text_expand(self) -> None:
        try:
            payload = self.read_json()
            item = process_text_expand_request(payload)
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        except PromptEngineError as error:
            return self.send_json(
                {"error": error.message, "code": error.code},
                status=error.status,
            )
        self.send_json({"item": item})

    def handle_text_random(self) -> None:
        try:
            payload = self.read_json()
            item = process_text_random_request(payload)
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        except PromptEngineError as error:
            return self.send_json(
                {"error": error.message, "code": error.code},
                status=error.status,
            )
        self.send_json({"item": item})

    def handle_text_decompose(self) -> None:
        try:
            payload = self.read_json()
            item = process_text_decompose_request(payload)
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        except PromptEngineError as error:
            return self.send_json(
                {"error": error.message, "code": error.code},
                status=error.status,
            )
        self.send_json({"item": item})

    def handle_text_provider_test(self) -> None:
        try:
            payload = self.read_json()
            item = process_text_provider_test(payload)
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        except PromptEngineError as error:
            return self.send_json(
                {"error": error.message, "code": error.code},
                status=error.status,
            )
        self.send_json({"item": item})

    def handle_text_regenerate_block(self) -> None:
        try:
            payload = self.read_json()
            item = process_text_regenerate_block_request(payload)
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        except PromptEngineError as error:
            return self.send_json(
                {"error": error.message, "code": error.code},
                status=error.status,
            )
        self.send_json({"item": item})

    def handle_text_regenerate_blocks(self) -> None:
        try:
            payload = self.read_json()
            item = process_text_regenerate_blocks_request(payload)
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        except PromptEngineError as error:
            return self.send_json(
                {"error": error.message, "code": error.code},
                status=error.status,
            )
        self.send_json({"item": item})

    def handle_text_translate_pending(self) -> None:
        try:
            payload = self.read_json()
            item = process_text_translate_pending_request(payload)
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        except PromptEngineError as error:
            return self.send_json(
                {"error": error.message, "code": error.code},
                status=error.status,
            )
        self.send_json({"item": item})

    def handle_vision_analyze(self) -> None:
        try:
            payload = self.read_json()
            item = process_vision_analyze_request(payload)
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        except (PromptEngineError, VisionEngineError) as error:
            return self.send_json(
                {"error": error.message, "code": error.code},
                status=error.status,
            )
        self.send_json({"item": item})

    def handle_local_llm_start(self) -> None:
        try:
            payload = self.read_json()
            if payload:
                raise ValueError("本地 LLM 启动接口不接受额外字段")
            item = start_local_llm()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        except LocalLlmError as error:
            return self.send_json(
                {"error": error.message, "code": error.code},
                status=error.status,
            )
        self.send_json({"item": item})

    def handle_local_llm_stop(self) -> None:
        try:
            payload = self.read_json()
            if payload:
                raise ValueError("本地 LLM 停止接口不接受额外字段")
            item = stop_local_llm()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        except LocalLlmError as error:
            return self.send_json(
                {"error": error.message, "code": error.code},
                status=error.status,
            )
        self.send_json({"item": item})

    def handle_version_create(self, project_id: str) -> None:
        if not get_project(project_id):
            return self.send_json({"error": "作品不存在"}, status=404)
        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        if "baseUpdatedAt" not in payload:
            return self.send_json(
                {
                    "error": "创建提示词版本必须提供 baseUpdatedAt 并发基线",
                    "code": "project_precondition_required",
                },
                status=428,
            )
        version = create_prompt_version(
            project_id,
            payload,
            idempotency_key=self.read_idempotency_key(),
        )
        self.send_json({"item": version}, status=201)

    def handle_favorites_get(self, raw_query: str) -> None:
        query = parse_qs(raw_query)
        favorite_type = query.get("type", [""])[0].strip()
        term = query.get("q", [""])[0].strip()
        self.send_json({"items": list_favorites(favorite_type, term)})

    def handle_favorite_upsert(self) -> None:
        try:
            payload = self.read_json()
            item = upsert_favorite(payload)
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        self.send_json({"item": item}, status=201)

    def handle_favorite_delete(self, favorite_id: str) -> None:
        if not favorite_id:
            return self.send_json({"error": "收藏 ID 无效"}, status=400)
        self.send_json({"deleted": delete_favorite(favorite_id)})

    def handle_settings_get(self) -> None:
        self.send_json({"settings": redact_settings(get_settings())})

    def handle_settings_put(self) -> None:
        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        settings_update = validate_settings_update(
            preserve_saved_setting_keys(payload)
        )
        reset_keys = set()
        skill_override = settings_update.get("creativeDirectorSkillOverride")
        if isinstance(skill_override, str) and not skill_override.strip():
            settings_update.pop("creativeDirectorSkillOverride")
            reset_keys.add("creativeDirectorSkillOverride")
        if reset_keys:
            saved_settings = put_settings(
                settings_update,
                reset_keys=reset_keys,
            )
        else:
            saved_settings = put_settings(settings_update)
        self.send_json({"settings": redact_settings(saved_settings)})

    def handle_status(self) -> None:
        try:
            characters = fetch_json(search_path("characters", "", 1))
            artists = fetch_json(search_path("artists", "", 1))
            self.send_json(
                {
                    "connected": True,
                    "source": "AnimaDex",
                    "upstream": ANIMADEX_URL,
                    "counts": {
                        "characters": int(characters.get("total") or 0),
                        "artists": int(artists.get("total") or 0),
                    },
                }
            )
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            self.send_upstream_error(error)

    def handle_resources(self, raw_query: str) -> None:
        query = parse_qs(raw_query)
        mode = query.get("type", ["characters"])[0]
        term = query.get("q", [""])[0].strip()
        sort = query.get("sort", ["count"])[0]
        try:
            page = int(query.get("page", ["1"])[0])
        except ValueError:
            page = 1
        if mode not in ALLOWED_MODES:
            return self.send_json({"error": "不支持的资源类型"}, status=400)
        try:
            payload = fetch_json(search_path(mode, term, page, sort))
            self.send_json(normalize_search_result(mode, payload))
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            self.send_upstream_error(error)

    def handle_thumbnail(self, path: str) -> None:
        parts = path.split("/")
        if len(parts) != 6 or parts[4] not in ALLOWED_MODES:
            return self.send_error(404)
        mode, slug = parts[4], unquote(parts[5])
        slug_parts = slug.replace("\\", "/").split("/")
        if (
            not slug
            or len(slug) > 1_024
            or any(ord(character) < 32 or ord(character) == 127 for character in slug)
            or any(item in {".", ".."} for item in slug_parts)
        ):
            return self.send_error(400)
        try:
            body, content_type = fetch_upstream(f"/thumb/{mode}/{quote(slug, safe='')}")
            self.send_response(200)
            self.send_header("Content-Type", content_type or "image/webp")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
        except (HTTPError, URLError, TimeoutError) as error:
            self.send_error(404, str(error))


if __name__ == "__main__":
    if HOST.lower().strip("[]") not in BASE_ALLOWED_HOSTS:
        if not ALLOW_NETWORK_BIND:
            raise SystemExit(
                "拒绝非回环监听；如确需局域网访问，请显式设置 "
                "PROMPT_STUDIO_ALLOW_NETWORK=1 和 PROMPT_STUDIO_ALLOWED_HOSTS。"
            )
        if not EXPLICIT_ALLOWED_HOSTS:
            raise SystemExit(
                "非回环监听必须通过 PROMPT_STUDIO_ALLOWED_HOSTS 配置允许的 Host。"
            )
    db_path = init_db()
    server = ThreadingHTTPServer((HOST, PORT), PromptStudioHandler)
    print(f"Prompt Studio: http://{HOST}:{PORT}")
    print(f"AnimaDex source: {ANIMADEX_URL}")
    print(f"Prompt Studio DB: {db_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
