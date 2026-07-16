"""SQLite persistence for the local Prompt Studio workspace."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = Path(
    os.environ.get("PROMPT_STUDIO_DB", ROOT / "data" / "prompt_studio.db")
)

SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
SAFE_STATUS_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
PROJECT_MODES = {"text", "image"}
FAVORITE_TYPES = {"characters", "artists", "references", "snippets"}
SENSITIVE_METADATA_KEYS = {
    "apikey",
    "apitextkey",
    "authorization",
    "localtextkey",
    "password",
    "secret",
    "token",
}


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'text',
    status TEXT NOT NULL DEFAULT 'draft',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_versions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    positive_en TEXT NOT NULL DEFAULT '',
    positive_zh TEXT NOT NULL DEFAULT '',
    negative_en TEXT NOT NULL DEFAULT '',
    negative_zh TEXT NOT NULL DEFAULT '',
    blocks_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE (project_id, version)
);

CREATE TABLE IF NOT EXISTS resources (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '',
    target_block TEXT NOT NULL DEFAULT '',
    en TEXT NOT NULL DEFAULT '',
    zh TEXT NOT NULL DEFAULT '',
    blocks_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS favorites (
    id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '',
    target_block TEXT NOT NULL DEFAULT '',
    en TEXT NOT NULL DEFAULT '',
    zh TEXT NOT NULL DEFAULT '',
    thumb_url TEXT NOT NULL DEFAULT '',
    has_image INTEGER NOT NULL DEFAULT 1,
    blocks_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_prompt_versions_project
    ON prompt_versions(project_id, version);

CREATE INDEX IF NOT EXISTS idx_resources_type
    ON resources(type);

CREATE INDEX IF NOT EXISTS idx_favorites_type_created
    ON favorites(type, created_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def json_dumps(value: Any) -> str:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
        )
        serialized.encode("utf-8")
        return serialized
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ValueError("数据不是可保存的严格 UTF-8 JSON") from error


def reject_json_constant(token: str) -> None:
    raise ValueError(f"invalid JSON constant: {token}")


def strict_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("JSON number must be finite")
    return parsed


def json_loads(value: str, fallback: Any) -> Any:
    try:
        return (
            json.loads(
                value,
                parse_constant=reject_json_constant,
                parse_float=strict_json_float,
            )
            if value
            else fallback
        )
    except (TypeError, ValueError, RecursionError):
        return fallback


def json_loads_typed(value: str, expected_type: type, fallback: Any) -> Any:
    parsed = json_loads(value, fallback)
    return parsed if isinstance(parsed, expected_type) else fallback


def validate_id(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label}无效")
    ensure_utf8_text(value, label)
    if not SAFE_ID_PATTERN.fullmatch(value):
        raise ValueError(f"{label}无效")
    return value


def validate_resource_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1_024:
        raise ValueError(f"{label}无效")
    ensure_utf8_text(value, label)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label}无效")
    return value


def ensure_utf8_text(value: str, label: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label}包含无效 Unicode") from error
    return value


def favorite_route_id(resource_id: str) -> str:
    digest = hashlib.sha256(resource_id.encode("utf-8")).hexdigest()[:24]
    return f"favorite-{digest}"


def require_string(
    payload: dict,
    key: str,
    default: str = "",
    *,
    max_length: int,
) -> str:
    value = payload[key] if key in payload else default
    if not isinstance(value, str):
        raise ValueError(f"{key} 必须是字符串")
    ensure_utf8_text(value, key)
    if len(value) > max_length:
        raise ValueError(f"{key} 超过长度限制")
    return value


def sanitize_metadata(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("metadata 必须是对象")

    def sanitize(item: object) -> object:
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                key_text = ensure_utf8_text(str(key), "metadata key")
                if not is_sensitive_key(key_text):
                    result[key_text] = sanitize(child)
            return result
        if isinstance(item, list):
            return [sanitize(child) for child in item]
        if isinstance(item, str):
            return ensure_utf8_text(item, "metadata value")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("metadata 不能包含 NaN 或 Infinity")
        return item

    return sanitize(value)  # type: ignore[return-value]


def is_sensitive_key(value: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(value).casefold())
    high_signal_markers = (
        "authorization",
        "bearertoken",
        "clientsecret",
        "privatekey",
        "credential",
    )
    generic_markers = ("apikey", "token", "secret", "password")
    return (
        normalized in SENSITIVE_METADATA_KEYS
        or any(marker in normalized for marker in high_signal_markers)
        or normalized.startswith(generic_markers)
        or normalized.endswith(generic_markers)
    )


def normalize_blocks(value: object) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("blocks 必须是对象数组")
    if len(value) > 64:
        raise ValueError("blocks 最多允许 64 项")
    normalized: list[dict] = []
    for source in value:
        allowed_keys = {
            "id",
            "label",
            "hint",
            "source",
            "en",
            "zh",
            "locked",
            "weight",
            "confidence",
        }
        item = {key: child for key, child in source.items() if key in allowed_keys}
        for key, max_length in {
            "id": 128,
            "label": 500,
            "hint": 2_000,
            "source": 500,
            "en": 200_000,
            "zh": 200_000,
        }.items():
            if key in item and (
                not isinstance(item[key], str) or len(item[key]) > max_length
            ):
                raise ValueError(f"blocks.{key} 必须是字符串")
            if key in item:
                ensure_utf8_text(item[key], f"blocks.{key}")
        if "locked" in item and not isinstance(item["locked"], bool):
            raise ValueError("blocks.locked 必须是布尔值")
        if "weight" in item:
            weight = item["weight"]
            if (
                isinstance(weight, bool)
                or not isinstance(weight, (int, float))
                or (isinstance(weight, float) and not math.isfinite(weight))
                or not 0 <= weight <= 120
            ):
                raise ValueError("blocks.weight 必须是 0 到 120 的有限数字")
        if "confidence" in item:
            confidence = item["confidence"]
            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or (isinstance(confidence, float) and not math.isfinite(confidence))
                or not 0 <= confidence <= 100
            ):
                raise ValueError("blocks.confidence 必须是 0 到 100 的有限数字")
        normalized.append(item)
    return normalized


def load_stored_blocks(value: str) -> list[dict]:
    parsed = json_loads(value, [])
    if not isinstance(parsed, list):
        return []
    safe_items: list[dict] = []
    for item in parsed[:64]:
        try:
            safe_items.extend(normalize_blocks([item]))
        except ValueError:
            continue
    return safe_items


def load_stored_metadata(value: str) -> dict:
    parsed = json_loads_typed(value, dict, {})
    try:
        return sanitize_metadata(parsed)
    except ValueError:
        return {}


def get_db_path(db_path: Path | str | None = None) -> Path:
    return Path(db_path) if db_path else DEFAULT_DB_PATH


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = get_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def database(db_path: Path | str | None = None):
    connection = connect(db_path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db(db_path: Path | str | None = None) -> Path:
    path = get_db_path(db_path)
    with database(path) as connection:
        connection.executescript(SCHEMA)
    return path


def _project_from_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "mode": row["mode"],
        "status": row["status"],
        "metadata": load_stored_metadata(row["metadata_json"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _version_from_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "version": row["version"],
        "source": row["source"],
        "positiveEn": row["positive_en"],
        "positiveZh": row["positive_zh"],
        "negativeEn": row["negative_en"],
        "negativeZh": row["negative_zh"],
        "blocks": load_stored_blocks(row["blocks_json"]),
        "metadata": load_stored_metadata(row["metadata_json"]),
        "createdAt": row["created_at"],
    }


def _favorite_from_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["resource_id"],
        "favoriteId": favorite_route_id(row["resource_id"]),
        "sourceId": row["source_id"],
        "source": row["source"],
        "type": row["type"],
        "name": row["name"],
        "meta": row["meta"],
        "targetBlock": row["target_block"],
        "en": row["en"],
        "zh": row["zh"],
        "blocks": load_stored_blocks(row["blocks_json"]),
        "thumbUrl": row["thumb_url"],
        "hasImage": bool(row["has_image"]),
        "favoritedAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "metadata": load_stored_metadata(row["metadata_json"]),
    }


def list_projects(db_path: Path | str | None = None) -> list[dict]:
    with database(db_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM projects
            ORDER BY updated_at DESC, created_at DESC
            """
        ).fetchall()
    return [_project_from_row(row) for row in rows]


def create_project(payload: dict, db_path: Path | str | None = None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("作品数据必须是对象")
    timestamp = now_iso()
    project_id = (
        validate_id(payload["id"], "作品 ID")
        if "id" in payload
        else new_id("project")
    )
    name = require_string(
        payload,
        "name",
        "未命名作品",
        max_length=200,
    ).strip() or "未命名作品"
    mode = require_string(payload, "mode", "text", max_length=16)
    if mode not in PROJECT_MODES:
        raise ValueError("mode 只允许 text 或 image")
    status = require_string(payload, "status", "draft", max_length=32)
    if not SAFE_STATUS_PATTERN.fullmatch(status):
        raise ValueError("status 无效")
    metadata = sanitize_metadata(payload.get("metadata", {}))
    with database(db_path) as connection:
        connection.execute(
            """
            INSERT INTO projects (
                id, name, mode, status, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, name, mode, status, json_dumps(metadata), timestamp, timestamp),
        )
    return get_project(project_id, db_path) or {}


def get_project(project_id: str, db_path: Path | str | None = None) -> dict | None:
    project_id = validate_resource_id(project_id, "作品 ID")
    with database(db_path) as connection:
        project = connection.execute(
            "SELECT * FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if not project:
            return None
        versions = connection.execute(
            """
            SELECT * FROM prompt_versions
            WHERE project_id = ?
            ORDER BY version ASC
            """,
            (project_id,),
        ).fetchall()
    result = _project_from_row(project)
    result["versions"] = [_version_from_row(row) for row in versions]
    return result


def create_prompt_version(
    project_id: str,
    payload: dict,
    db_path: Path | str | None = None,
) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("版本数据必须是对象")
    project_id = validate_resource_id(project_id, "作品 ID")
    timestamp = now_iso()
    version_id = (
        validate_id(payload["id"], "版本 ID")
        if "id" in payload
        else new_id("version")
    )
    if "version" in payload:
        raise ValueError("version 由服务端自动分配")
    source = require_string(payload, "source", "manual", max_length=64)
    positive_en = require_string(payload, "positiveEn", "", max_length=200_000)
    positive_zh = require_string(payload, "positiveZh", "", max_length=200_000)
    negative_en = require_string(payload, "negativeEn", "", max_length=100_000)
    negative_zh = require_string(payload, "negativeZh", "", max_length=100_000)
    blocks = normalize_blocks(payload.get("blocks"))
    metadata = sanitize_metadata(payload.get("metadata", {}))
    with database(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM prompt_versions WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
        version_number = current + 1
        connection.execute(
            """
            INSERT INTO prompt_versions (
                id, project_id, version, source,
                positive_en, positive_zh, negative_en, negative_zh,
                blocks_json, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                project_id,
                version_number,
                source,
                positive_en,
                positive_zh,
                negative_en,
                negative_zh,
                json_dumps(blocks),
                json_dumps(metadata),
                timestamp,
            ),
        )
        connection.execute(
            "UPDATE projects SET updated_at = ? WHERE id = ?",
            (timestamp, project_id),
        )
        row = connection.execute(
            "SELECT * FROM prompt_versions WHERE id = ?",
            (version_id,),
        ).fetchone()
    return _version_from_row(row)


def list_favorites(
    favorite_type: str = "",
    query: str = "",
    db_path: Path | str | None = None,
) -> list[dict]:
    clauses: list[str] = []
    params: list[str] = []
    if favorite_type:
        clauses.append("type = ?")
        params.append(favorite_type)
    if query:
        clauses.append(
            "(name LIKE ? OR meta LIKE ? OR en LIKE ? OR zh LIKE ? OR source LIKE ?)"
        )
        needle = f"%{query}%"
        params.extend([needle, needle, needle, needle, needle])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with database(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM favorites
            {where}
            ORDER BY created_at DESC
            """,
            params,
        ).fetchall()
    return [_favorite_from_row(row) for row in rows]


def upsert_favorite(resource: dict, db_path: Path | str | None = None) -> dict:
    if not isinstance(resource, dict):
        raise ValueError("收藏数据必须是对象")
    timestamp = now_iso()
    raw_resource_id = resource.get("id") or resource.get("resourceId")
    if raw_resource_id is None:
        raise ValueError("favorite resource id is required")
    resource_id = validate_resource_id(raw_resource_id, "资源 ID")
    favorite_id = favorite_route_id(resource_id)
    favorite_type = require_string(resource, "type", "", max_length=32).strip()
    if favorite_type not in FAVORITE_TYPES:
        raise ValueError("unsupported favorite type")
    blocks = normalize_blocks(resource.get("blocks"))
    if "hasImage" in resource and not isinstance(resource["hasImage"], bool):
        raise ValueError("hasImage 必须是布尔值")
    string_fields = {
        key: require_string(resource, key, "", max_length=max_length)
        for key, max_length in {
            "name": 500,
            "meta": 20_000,
            "source": 200,
            "sourceId": 500,
            "targetBlock": 128,
            "en": 200_000,
            "zh": 200_000,
            "thumbUrl": 4_096,
        }.items()
    }
    metadata = sanitize_metadata({
        key: value
        for key, value in resource.items()
        if key
        not in {
            "id",
            "favoriteId",
            "resourceId",
            "sourceId",
            "source",
            "type",
            "name",
            "meta",
            "targetBlock",
            "en",
            "zh",
            "blocks",
            "thumbUrl",
            "hasImage",
            "favoritedAt",
            "updatedAt",
        }
    })
    favorited_at = resource.get("favoritedAt")
    if favorited_at is not None and (
        not isinstance(favorited_at, str) or len(favorited_at) > 64
    ):
        raise ValueError("favoritedAt 必须是短字符串")
    if isinstance(favorited_at, str):
        ensure_utf8_text(favorited_at, "favoritedAt")
    with database(db_path) as connection:
        existing = connection.execute(
            """
            SELECT id, created_at FROM favorites
            WHERE resource_id = ? OR id = ?
            ORDER BY CASE WHEN resource_id = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (resource_id, favorite_id, resource_id),
        ).fetchone()
        storage_id = existing["id"] if existing else favorite_id
        created_at = (
            str(favorited_at or existing["created_at"])
            if existing
            else str(favorited_at or timestamp)
        )
        connection.execute(
            """
            INSERT INTO favorites (
                id, resource_id, type, name, meta, source, source_id,
                target_block, en, zh, thumb_url, has_image,
                blocks_json, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                resource_id = excluded.resource_id,
                type = excluded.type,
                name = excluded.name,
                meta = excluded.meta,
                source = excluded.source,
                source_id = excluded.source_id,
                target_block = excluded.target_block,
                en = excluded.en,
                zh = excluded.zh,
                thumb_url = excluded.thumb_url,
                has_image = excluded.has_image,
                blocks_json = excluded.blocks_json,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                storage_id,
                resource_id,
                favorite_type,
                string_fields["name"],
                string_fields["meta"],
                string_fields["source"],
                string_fields["sourceId"],
                string_fields["targetBlock"],
                string_fields["en"],
                string_fields["zh"],
                string_fields["thumbUrl"],
                1 if resource.get("hasImage", True) else 0,
                json_dumps(blocks),
                json_dumps(metadata),
                created_at,
                timestamp,
            ),
        )
        row = connection.execute(
            "SELECT * FROM favorites WHERE id = ?",
            (storage_id,),
        ).fetchone()
    return _favorite_from_row(row)


def delete_favorite(favorite_id: str, db_path: Path | str | None = None) -> bool:
    favorite_id = validate_resource_id(favorite_id, "收藏 ID")
    with database(db_path) as connection:
        direct = connection.execute(
            "DELETE FROM favorites WHERE id = ? OR resource_id = ?",
            (favorite_id, favorite_id),
        )
        deleted_count = direct.rowcount
        rows = connection.execute(
            "SELECT id, resource_id FROM favorites"
        ).fetchall()
        matching_ids = [
            row["id"]
            for row in rows
            if favorite_id
            in {
                row["id"],
                row["resource_id"],
                favorite_route_id(row["resource_id"]),
            }
        ]
        if not matching_ids:
            return deleted_count > 0
        connection.executemany(
            "DELETE FROM favorites WHERE id = ?",
            [(item_id,) for item_id in matching_ids],
        )
        deleted_count += len(matching_ids)
    return deleted_count > 0


def get_settings(db_path: Path | str | None = None) -> dict:
    with database(db_path) as connection:
        rows = connection.execute("SELECT * FROM settings ORDER BY key").fetchall()
    return {row["key"]: json_loads(row["value_json"], None) for row in rows}


def put_settings(payload: dict, db_path: Path | str | None = None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("设置必须是对象")
    timestamp = now_iso()
    with database(db_path) as connection:
        for key, value in payload.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ValueError("设置键无效")
            connection.execute(
                """
                INSERT INTO settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (str(key), json_dumps(value), timestamp),
            )
    return get_settings(db_path)
