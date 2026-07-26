"""Strict LoRA-lite profiles stored in the existing resources table."""

from __future__ import annotations

import math
import re
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

import db


RESOURCE_TYPE = "lora_profile"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
_FIELDS = {
    "id",
    "name",
    "version",
    "sourceUrl",
    "triggerWords",
    "suggestedWeight",
    "notes",
    "compatibleModelProfileIds",
    "conflictLoraProfileIds",
    "compatibilityNotes",
}


class LoraProfileConflictError(RuntimeError):
    def __init__(self, expected: str | None, actual: str | None):
        self.expected = expected
        self.actual = actual
        super().__init__("LoRA 档案已变化，请刷新后重试")


def _text(value: object, label: str, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} 必须是字符串")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} 包含无效 Unicode") from error
    value = value.strip()
    if not empty and not value:
        raise ValueError(f"{label} 不能为空")
    if len(value) > maximum:
        raise ValueError(f"{label} 超出长度限制")
    return value


def _identifier(value: object, label: str) -> str:
    value = _text(value, label, 128)
    if not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{label} 无效")
    return value


def _unique_strings(
    value: object, label: str, maximum_items: int, maximum_length: int
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ValueError(f"{label} 必须是有界数组")
    result = [_text(item, f"{label}[{index}]", maximum_length) for index, item in enumerate(value)]
    if len(set(result)) != len(result):
        raise ValueError(f"{label} 不能包含重复项")
    return result


def _safe_original_url(value: object) -> str:
    value = _text(value, "sourceUrl", 2_048)
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("sourceUrl 无效") from error
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        raise ValueError("sourceUrl 必须是无凭据、无片段的 HTTPS 原始链接")
    return value


def normalize_lora_profile_payload(
    payload: object, *, profile_id: str | None = None
) -> dict:
    if not isinstance(payload, Mapping):
        raise ValueError("LoRA 档案必须是对象")
    unknown = sorted(set(payload) - _FIELDS)
    if unknown:
        raise ValueError("LoRA 档案包含不允许的字段：" + ", ".join(unknown))
    supplied_id = payload.get("id")
    if profile_id is None:
        normalized_id = (
            _identifier(supplied_id, "id")
            if supplied_id is not None
            else db.new_id("lora")
        )
    else:
        normalized_id = _identifier(profile_id, "id")
        if supplied_id is not None and _identifier(supplied_id, "id") != normalized_id:
            raise ValueError("LoRA 档案 ID 不可修改")

    trigger_words = _unique_strings(
        payload.get("triggerWords"), "triggerWords", 64, 256
    )
    compatible_ids = [
        _identifier(item, f"compatibleModelProfileIds[{index}]")
        for index, item in enumerate(
            _unique_strings(
                payload.get("compatibleModelProfileIds"),
                "compatibleModelProfileIds",
                64,
                128,
            )
        )
    ]
    conflict_ids = [
        _identifier(item, f"conflictLoraProfileIds[{index}]")
        for index, item in enumerate(
            _unique_strings(
                payload.get("conflictLoraProfileIds"),
                "conflictLoraProfileIds",
                64,
                128,
            )
        )
    ]
    if normalized_id in conflict_ids:
        raise ValueError("LoRA 档案不能与自身冲突")
    weight = payload.get("suggestedWeight")
    if isinstance(weight, bool) or not isinstance(weight, (int, float)):
        raise ValueError("suggestedWeight 必须是数字")
    weight = float(weight)
    if not math.isfinite(weight) or not 0 <= weight <= 2:
        raise ValueError("suggestedWeight 必须在 0 到 2 之间")
    return {
        "id": normalized_id,
        "name": _text(payload.get("name"), "name", 512),
        "version": _text(payload.get("version"), "version", 128),
        "sourceUrl": _safe_original_url(payload.get("sourceUrl")),
        "triggerWords": trigger_words,
        "suggestedWeight": weight,
        "notes": _text(payload.get("notes", ""), "notes", 4_096, empty=True),
        "compatibleModelProfileIds": compatible_ids,
        "conflictLoraProfileIds": conflict_ids,
        "compatibilityNotes": _text(
            payload.get("compatibilityNotes", ""),
            "compatibilityNotes",
            4_096,
            empty=True,
        ),
    }


def _row_to_profile(row) -> dict:
    metadata = db.json_loads_typed(row["metadata_json"], dict, {})
    payload = {
        "id": row["id"],
        "name": row["name"],
        "version": metadata.get("version"),
        "sourceUrl": metadata.get("sourceUrl"),
        "triggerWords": metadata.get("triggerWords"),
        "suggestedWeight": metadata.get("suggestedWeight"),
        "notes": metadata.get("notes", ""),
        "compatibleModelProfileIds": metadata.get(
            "compatibleModelProfileIds", []
        ),
        "conflictLoraProfileIds": metadata.get("conflictLoraProfileIds", []),
        "compatibilityNotes": metadata.get("compatibilityNotes", ""),
    }
    return {
        **normalize_lora_profile_payload(payload),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _metadata(profile: Mapping[str, object]) -> dict:
    return {
        key: deepcopy(profile[key])
        for key in (
            "version",
            "sourceUrl",
            "triggerWords",
            "suggestedWeight",
            "notes",
            "compatibleModelProfileIds",
            "conflictLoraProfileIds",
            "compatibilityNotes",
        )
    }


def list_lora_profiles(*, db_path: Path | str | None = None) -> list[dict]:
    db.init_db(db_path)
    with db.database(db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM resources WHERE type = ? ORDER BY name COLLATE NOCASE, id",
            (RESOURCE_TYPE,),
        ).fetchall()
    return [_row_to_profile(row) for row in rows]


def get_lora_profile(
    profile_id: str, *, db_path: Path | str | None = None
) -> dict | None:
    profile_id = _identifier(profile_id, "id")
    db.init_db(db_path)
    with db.database(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM resources WHERE id = ? AND type = ?",
            (profile_id, RESOURCE_TYPE),
        ).fetchone()
    return _row_to_profile(row) if row is not None else None


def create_lora_profile(
    payload: object, *, db_path: Path | str | None = None
) -> dict:
    profile = normalize_lora_profile_payload(payload)
    db.init_db(db_path)
    timestamp = db.now_iso()
    try:
        with db.database(db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO resources (
                    id, type, name, meta, target_block, en, zh,
                    blocks_json, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, '', '', '', '[]', ?, ?, ?)
                """,
                (
                    profile["id"],
                    RESOURCE_TYPE,
                    profile["name"],
                    profile["version"],
                    db.json_dumps(_metadata(profile)),
                    timestamp,
                    timestamp,
                ),
            )
    except sqlite3.IntegrityError as error:
        raise LoraProfileConflictError(None, profile["id"]) from error
    result = get_lora_profile(profile["id"], db_path=db_path)
    assert result is not None
    return result


def update_lora_profile(
    profile_id: str,
    payload: object,
    *,
    db_path: Path | str | None = None,
) -> dict | None:
    if not isinstance(payload, Mapping):
        raise ValueError("LoRA 档案必须是对象")
    allowed = _FIELDS | {"baseUpdatedAt"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError("LoRA 档案包含不允许的字段：" + ", ".join(unknown))
    base_updated_at = _text(payload.get("baseUpdatedAt"), "baseUpdatedAt", 64)
    profile = normalize_lora_profile_payload(
        {key: value for key, value in payload.items() if key != "baseUpdatedAt"},
        profile_id=profile_id,
    )
    db.init_db(db_path)
    with db.database(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT updated_at FROM resources WHERE id = ? AND type = ?",
            (profile["id"], RESOURCE_TYPE),
        ).fetchone()
        if row is None:
            return None
        if row["updated_at"] != base_updated_at:
            raise LoraProfileConflictError(base_updated_at, row["updated_at"])
        timestamp = db.now_iso()
        connection.execute(
            """
            UPDATE resources
            SET name = ?, meta = ?, metadata_json = ?, updated_at = ?
            WHERE id = ? AND type = ?
            """,
            (
                profile["name"],
                profile["version"],
                db.json_dumps(_metadata(profile)),
                timestamp,
                profile["id"],
                RESOURCE_TYPE,
            ),
        )
    return get_lora_profile(profile["id"], db_path=db_path)


def delete_lora_profile(
    profile_id: str,
    *,
    base_updated_at: str,
    db_path: Path | str | None = None,
) -> bool:
    profile_id = _identifier(profile_id, "id")
    base_updated_at = _text(base_updated_at, "baseUpdatedAt", 64)
    db.init_db(db_path)
    with db.database(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT updated_at FROM resources WHERE id = ? AND type = ?",
            (profile_id, RESOURCE_TYPE),
        ).fetchone()
        if row is None:
            return False
        if row["updated_at"] != base_updated_at:
            raise LoraProfileConflictError(base_updated_at, row["updated_at"])
        connection.execute(
            "DELETE FROM resources WHERE id = ? AND type = ?",
            (profile_id, RESOURCE_TYPE),
        )
    return True


__all__ = [
    "LoraProfileConflictError",
    "create_lora_profile",
    "delete_lora_profile",
    "get_lora_profile",
    "list_lora_profiles",
    "normalize_lora_profile_payload",
    "update_lora_profile",
]
