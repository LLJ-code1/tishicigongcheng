"""Local development server and AnimaDex gateway for Prompt Studio."""

from __future__ import annotations

import base64
import binascii
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

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
MAX_JSON_BODY_BYTES = 30 * 1024 * 1024
PUBLIC_STATIC_FILES = {"/", "/index.html", "/app.js", "/data.js", "/styles.css"}
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
    if not image_bytes or len(image_bytes) > 20 * 1024 * 1024:
        raise ValueError("图片为空或超过 20 MB")
    detected_mime_type = detect_image_mime_type(image_bytes)
    if not detected_mime_type:
        raise ValueError("图片内容不是受支持的 PNG、JPG 或 WEBP 文件")
    if detected_mime_type != mime_type:
        raise ValueError("图片内容与声明的格式不一致，请重新选择文件")
    saved_settings = (
        settings_payload if settings_payload is not None else get_settings()
    )
    settings = {**DEFAULT_TEXT_SETTINGS, **saved_settings}
    return engine(image_bytes, filename, analyzer_ids, settings)


class PromptStudioHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, format: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        raw_length = self.headers.get("Content-Length") or "0"
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ValueError("请求长度无效") from error
        if length <= 0:
            return {}
        if length > MAX_JSON_BODY_BYTES:
            raise ValueError("请求内容超过 30 MB 限制")
        try:
            body = self.rfile.read(length).decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("请求 JSON 必须使用 UTF-8 编码") from error
        payload = json.loads(body) if body.strip() else {}
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
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/prompt-templates"):
            return self.handle_prompt_templates_get(parsed.path)
        if parsed.path.startswith("/api/projects"):
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

    def do_POST(self) -> None:
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
        if parsed.path.startswith("/api/projects/") and parsed.path.endswith(
            "/versions"
        ):
            project_id = unquote(parsed.path.split("/")[-2])
            return self.handle_version_create(project_id)
        if parsed.path == "/api/favorites":
            return self.handle_favorite_upsert()
        return self.send_json({"error": "未找到 API"}, status=404)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/prompt-templates/"):
            template_id = unquote(parsed.path.rsplit("/", 1)[-1])
            return self.handle_prompt_template_put(template_id)
        if parsed.path == "/api/settings":
            return self.handle_settings_put()
        return self.send_json({"error": "未找到 API"}, status=404)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/favorites/"):
            favorite_id = unquote(parsed.path.rsplit("/", 1)[-1])
            return self.handle_favorite_delete(favorite_id)
        return self.send_json({"error": "未找到 API"}, status=404)

    def handle_projects_get(self, path: str) -> None:
        init_db()
        if path == "/api/projects":
            return self.send_json({"items": list_projects()})
        prefix = "/api/projects/"
        project_id = unquote(path[len(prefix) :])
        if not project_id or "/" in project_id:
            return self.send_json({"error": "作品 ID 无效"}, status=400)
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
            item = start_local_llm()
        except LocalLlmError as error:
            return self.send_json(
                {"error": error.message, "code": error.code},
                status=error.status,
            )
        self.send_json({"item": item})

    def handle_local_llm_stop(self) -> None:
        try:
            item = stop_local_llm()
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
        self.send_json({"settings": get_settings()})

    def handle_settings_put(self) -> None:
        init_db()
        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            return self.send_bad_json()
        except ValueError as error:
            return self.send_json({"error": str(error)}, status=400)
        self.send_json({"settings": put_settings(payload)})

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
        if not slug or "/" in slug or "\\" in slug or slug in {".", ".."}:
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
