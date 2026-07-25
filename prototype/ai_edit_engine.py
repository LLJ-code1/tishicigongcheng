"""AI-backed, fail-closed local editing for Recipe v1 prompts."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prompt_engine import (
    PromptEngineError,
    apply_provider_request_options,
    default_transport,
    parse_model_json,
    resolve_text_provider,
    response_content,
)
from recipe import BLOCK_IDS, get_block_definitions, normalize_recipe, recipe_hash


PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "text_edit.md"
MAX_INSTRUCTION_CHARS = 2_000
_PREVIEW_SIGNING_KEY = secrets.token_bytes(32)
_BLOCK_LABELS = {
    definition["id"]: definition["label"] for definition in get_block_definitions()
}


@dataclass
class AIEditError(Exception):
    message: str
    code: str = "ai_edit_error"
    status: int = 500

    def __str__(self) -> str:
        return self.message


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _instruction(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AIEditError("请输入要修改的中文指令", "invalid_instruction", 400)
    normalized = value.strip()
    if len(normalized) > MAX_INSTRUCTION_CHARS or any(
        ord(character) < 32 and character not in "\t\n\r" for character in normalized
    ):
        raise AIEditError("中文修改指令无效或过长", "invalid_instruction", 400)
    return normalized


def _map_prompt_error(error: PromptEngineError) -> AIEditError:
    return AIEditError(error.message, error.code, error.status)


def _validate_changes(payload: dict, recipe: dict) -> list[dict]:
    if set(payload) != {"changes"} or not isinstance(payload["changes"], list):
        raise AIEditError("AI 修改结果结构无效", "invalid_ai_edit_output", 502)
    raw_changes = payload["changes"]
    if not 1 <= len(raw_changes) <= len(BLOCK_IDS):
        raise AIEditError("AI 没有返回有效修改项", "invalid_ai_edit_output", 502)
    block_map = {item["id"]: item for item in recipe["blocks"]}
    seen: set[str] = set()
    changes: list[dict] = []
    for raw in raw_changes:
        if not isinstance(raw, dict) or set(raw) != {
            "blockId",
            "en",
            "zh",
            "reason",
        }:
            raise AIEditError("AI 修改项包含缺失或未知字段", "invalid_ai_edit_output", 502)
        block_id = raw.get("blockId")
        if block_id not in block_map or block_id in seen:
            raise AIEditError("AI 返回了未知或重复结构块", "invalid_ai_edit_output", 502)
        values = {
            key: raw.get(key).strip() if isinstance(raw.get(key), str) else ""
            for key in ("en", "zh", "reason")
        }
        if not all(values.values()):
            raise AIEditError("AI 修改内容或原因不能为空", "invalid_ai_edit_output", 502)
        before = block_map[block_id]
        if values["en"] == before["en"] and values["zh"] == before["zh"]:
            raise AIEditError("AI 没有产生实际修改", "ai_edit_no_change", 422)
        seen.add(block_id)
        changes.append({"blockId": block_id, **values})
    return changes


def propose_ai_edit(
    instruction: str,
    recipe: dict,
    settings: dict,
    provider: str = "local",
    target_model: str = "anima",
    transport=None,
    timeout: float = 120,
) -> dict:
    normalized_instruction = _instruction(instruction)
    normalized_recipe = normalize_recipe(recipe)
    if str(target_model or "").strip().lower() != "anima":
        raise AIEditError("暂不支持该出图模型", "unsupported_model", 400)
    try:
        config = resolve_text_provider(settings, provider)
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
    except PromptEngineError as error:
        raise _map_prompt_error(error) from error
    except OSError as error:
        raise AIEditError("AI 局部修改提示模板无法读取", "prompt_module_error") from error
    user_payload = {
        "instruction": normalized_instruction,
        "targetModel": "anima",
        "blocks": [
            {"id": item["id"], "en": item["en"], "zh": item["zh"]}
            for item in normalized_recipe["blocks"]
        ],
    }
    body = apply_provider_request_options(
        config,
        {
            "model": config["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _canonical(user_payload)},
            ],
            "temperature": 0.2,
            "max_tokens": 2200,
            "stream": False,
        },
    )
    if provider == "local":
        body["messages"][0]["content"] = "/no_think\n" + body["messages"][0]["content"]
    try:
        raw_response = (transport or default_transport)(
            config["url"], body, config["headers"], timeout
        )
        payload = parse_model_json(response_content(raw_response))
    except PromptEngineError as error:
        raise _map_prompt_error(error) from error
    return {"changes": _validate_changes(payload, normalized_recipe)}


def _preview_security_view(preview: dict) -> dict:
    return {
        key: preview.get(key)
        for key in (
            "schemaVersion",
            "instruction",
            "baseHash",
            "resultHash",
            "affectedIds",
            "lockedIds",
            "diffs",
            "ready",
        )
    }


def _sign_preview(preview: dict) -> str:
    return hmac.new(
        _PREVIEW_SIGNING_KEY,
        _canonical(_preview_security_view(preview)).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def create_ai_edit_preview(
    instruction: str,
    recipe: dict,
    base_hash: str,
    settings: dict,
    provider: str = "local",
    target_model: str = "anima",
    transport=None,
) -> dict:
    normalized_recipe = normalize_recipe(recipe)
    actual_hash = recipe_hash(normalized_recipe)
    if not isinstance(base_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", base_hash):
        raise AIEditError("配方基线 Hash 无效", "invalid_base_hash", 400)
    if not hmac.compare_digest(actual_hash, base_hash.casefold()):
        raise AIEditError("配方已变化，请重新生成修改预览", "base_hash_mismatch", 409)
    proposal = propose_ai_edit(
        instruction,
        normalized_recipe,
        settings,
        provider,
        target_model,
        transport=transport,
    )
    block_map = {item["id"]: item for item in normalized_recipe["blocks"]}
    affected = [item["blockId"] for item in proposal["changes"]]
    diffs = []
    result = copy.deepcopy(normalized_recipe)
    result_map = {item["id"]: item for item in result["blocks"]}
    for change in proposal["changes"]:
        block_id = change["blockId"]
        after = copy.deepcopy(block_map[block_id])
        after["en"] = change["en"]
        after["zh"] = change["zh"]
        after["source"] = "AI 局部修改"
        result_map[block_id].update(after)
        diffs.append(
            {
                "id": block_id,
                "label": _BLOCK_LABELS[block_id],
                "before": copy.deepcopy(block_map[block_id]),
                "after": after,
                "reason": change["reason"],
            }
        )
    preview = {
        "schemaVersion": "ai-edit-v1",
        "instruction": _instruction(instruction),
        "recipeHash": actual_hash,
        "baseHash": actual_hash,
        "resultHash": recipe_hash(result),
        "affectedIds": affected,
        "lockedIds": [item for item in BLOCK_IDS if item not in set(affected)],
        "conflicts": [],
        "diffs": diffs,
        "ready": True,
    }
    preview["previewHash"] = _sign_preview(preview)
    return preview


def apply_ai_edit_preview(
    recipe: dict, preview: dict, parent_version: Any, new_version: Any | None = None
) -> dict:
    normalized_recipe = normalize_recipe(recipe)
    if not isinstance(preview, dict):
        raise AIEditError("修改预览无效", "invalid_preview", 400)
    signature = preview.get("previewHash")
    if (
        not isinstance(signature, str)
        or not re.fullmatch(r"[0-9a-f]{64}", signature)
        or not hmac.compare_digest(signature, _sign_preview(preview))
    ):
        raise AIEditError("修改预览已被更改", "preview_tampered", 409)
    if not preview.get("ready"):
        raise AIEditError("修改预览尚不可确认", "preview_not_ready", 409)
    if not hmac.compare_digest(recipe_hash(normalized_recipe), preview["baseHash"]):
        raise AIEditError("配方已变化，请重新生成修改预览", "base_hash_mismatch", 409)
    affected = preview.get("affectedIds")
    diffs = preview.get("diffs")
    if not isinstance(affected, list) or not isinstance(diffs, list):
        raise AIEditError("修改预览结构无效", "preview_tampered", 409)
    if affected != [item.get("id") for item in diffs]:
        raise AIEditError("修改预览结构无效", "preview_tampered", 409)
    result = copy.deepcopy(normalized_recipe)
    by_id = {item["id"]: index for index, item in enumerate(result["blocks"])}
    for diff in diffs:
        block_id = diff["id"]
        if block_id not in by_id:
            raise AIEditError("修改预览包含未知结构块", "preview_tampered", 409)
        result["blocks"][by_id[block_id]] = copy.deepcopy(diff["after"])
    if not hmac.compare_digest(recipe_hash(result), preview["resultHash"]):
        raise AIEditError("修改预览结果校验失败", "preview_tampered", 409)
    change = {
        "type": "ai_local_edit",
        "instruction": preview["instruction"],
        "affectedIds": copy.deepcopy(affected),
        "reasons": {item["id"]: item["reason"] for item in diffs},
        "baseHash": preview["baseHash"],
        "resultHash": preview["resultHash"],
    }
    value = {"parentVersion": parent_version, "recipe": result, "change": change}
    if new_version is not None:
        value["version"] = new_version
    return value


__all__ = [
    "AIEditError",
    "apply_ai_edit_preview",
    "create_ai_edit_preview",
    "propose_ai_edit",
]
