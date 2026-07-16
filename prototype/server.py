"""Local development server and AnimaDex gateway for Prompt Studio."""

from __future__ import annotations

import base64
import binascii
import json
import math
import os
import socket
import sqlite3
import threading
import time
from io import BytesIO
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from PIL import Image, UnidentifiedImageError

from db import (
    create_project,
    create_prompt_version,
    delete_favorite,
    get_project,
    get_settings,
    init_db,
    list_favorites,
    list_projects,
    put_settings,
    upsert_favorite,
)
from local_llm import (
    LocalLlmError,
    local_llm_status,
    start_local_llm,
    stop_local_llm,
)
from prompt_engine import (
    PromptEngineError,
    decompose_text_prompt,
    expand_text_prompt,
    generate_random_text_prompt,
    probe_text_provider,
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
REQUEST_BODY_TIMEOUT_SECONDS = 2.0
VISION_REQUEST_BODY_TIMEOUT_SECONDS = 30.0
CONNECTION_TIMEOUT_SECONDS = 5.0
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

    redacted = {
        key: value
        for key, value in settings.items()
        if key in ALLOWED_SETTING_KEYS and key not in SENSITIVE_SETTING_KEYS
    }
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
        if key in validated and (
            not isinstance(validated[key], str)
            or len(validated[key]) > max_length
        ):
            raise ValueError(f"{key} 必须是长度不超过 {max_length} 的字符串")
    for key, values in ENUM_SETTING_VALUES.items():
        if key in validated and validated[key] not in values:
            raise ValueError(f"{key} 的值无效")
    for key in BOOLEAN_SETTING_KEYS:
        if key in validated and not isinstance(validated[key], bool):
            raise ValueError(f"{key} 必须是布尔值")
    return validated


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


class PromptStudioHandler(SimpleHTTPRequestHandler):
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

    def _validate_host_and_target(self) -> None:
        host_headers = self.headers.get_all("Host", [])
        if len(host_headers) != 1 or not is_allowed_host(host_headers[0]):
            raise RequestValidationError("请求 Host 不在本地允许列表中", status=421)
        if not self.path.startswith("/") or self.path.startswith("//"):
            raise RequestValidationError("请求目标格式无效")

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

        content_type_headers = self.headers.get_all("Content-Type", [])
        if len(content_type_headers) != 1:
            raise RequestValidationError(
                "写操作必须使用 application/json",
                status=415,
            )
        media_type, *parameters = [
            item.strip().lower() for item in content_type_headers[0].split(";")
        ]
        if media_type != "application/json" or any(
            parameter.startswith("charset=")
            and parameter.split("=", 1)[1].strip('"') not in {"utf-8", "utf8"}
            for parameter in parameters
        ):
            raise RequestValidationError(
                "写操作必须使用 UTF-8 application/json",
                status=415,
            )

        path = urlparse(self.path).path
        max_bytes = (
            MAX_VISION_JSON_BODY_BYTES
            if path == "/api/vision/analyze"
            else MAX_JSON_BODY_BYTES
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
            self._send_json_if_possible({"error": str(error)}, error.status)
        except json.JSONDecodeError:
            self._send_json_if_possible({"error": "请求 JSON 无法解析"}, 400)
        except ValueError as error:
            self._send_json_if_possible({"error": str(error)}, 400)
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
        if parsed.path == "/api/settings":
            return self.handle_settings_put()
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
        return self.send_json({"error": "未找到 API"}, status=404)

    def handle_projects_get(self, path: str) -> None:
        init_db()
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
        init_db()
        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        project = create_project(payload)
        self.send_json({"item": project}, status=201)

    def handle_text_expand(self) -> None:
        init_db()
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
        init_db()
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
        init_db()
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
        init_db()
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
        init_db()
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
        init_db()
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
        init_db()
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
        init_db()
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
        init_db()
        if not get_project(project_id):
            return self.send_json({"error": "作品不存在"}, status=404)
        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        version = create_prompt_version(project_id, payload)
        self.send_json({"item": version}, status=201)

    def handle_favorites_get(self, raw_query: str) -> None:
        init_db()
        query = parse_qs(raw_query)
        favorite_type = query.get("type", [""])[0].strip()
        term = query.get("q", [""])[0].strip()
        self.send_json({"items": list_favorites(favorite_type, term)})

    def handle_favorite_upsert(self) -> None:
        init_db()
        try:
            payload = self.read_json()
            item = upsert_favorite(payload)
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        self.send_json({"item": item}, status=201)

    def handle_favorite_delete(self, favorite_id: str) -> None:
        init_db()
        if not favorite_id:
            return self.send_json({"error": "收藏 ID 无效"}, status=400)
        self.send_json({"deleted": delete_favorite(favorite_id)})

    def handle_settings_get(self) -> None:
        init_db()
        self.send_json({"settings": redact_settings(get_settings())})

    def handle_settings_put(self) -> None:
        init_db()
        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        settings_update = validate_settings_update(
            preserve_saved_setting_keys(payload)
        )
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
