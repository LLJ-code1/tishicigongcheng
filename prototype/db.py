"""SQLite persistence for the local Prompt Studio workspace."""

from __future__ import annotations

import json
import os
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
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def json_loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


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
        "metadata": json_loads(row["metadata_json"], {}),
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
        "blocks": json_loads(row["blocks_json"], []),
        "metadata": json_loads(row["metadata_json"], {}),
        "createdAt": row["created_at"],
    }


def _favorite_from_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["resource_id"],
        "favoriteId": row["id"],
        "sourceId": row["source_id"],
        "source": row["source"],
        "type": row["type"],
        "name": row["name"],
        "meta": row["meta"],
        "targetBlock": row["target_block"],
        "en": row["en"],
        "zh": row["zh"],
        "blocks": json_loads(row["blocks_json"], []),
        "thumbUrl": row["thumb_url"],
        "hasImage": bool(row["has_image"]),
        "favoritedAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "metadata": json_loads(row["metadata_json"], {}),
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
    timestamp = now_iso()
    project_id = payload.get("id") or new_id("project")
    name = str(payload.get("name") or "未命名作品").strip() or "未命名作品"
    mode = str(payload.get("mode") or "text")
    status = str(payload.get("status") or "draft")
    metadata = payload.get("metadata") or {}
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
    timestamp = now_iso()
    version_id = payload.get("id") or new_id("version")
    with database(db_path) as connection:
        current = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM prompt_versions WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
        version_number = int(payload.get("version") or current + 1)
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
                str(payload.get("source") or "manual"),
                str(payload.get("positiveEn") or ""),
                str(payload.get("positiveZh") or ""),
                str(payload.get("negativeEn") or ""),
                str(payload.get("negativeZh") or ""),
                json_dumps(payload.get("blocks") or []),
                json_dumps(payload.get("metadata") or {}),
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
    timestamp = now_iso()
    resource_id = str(resource.get("id") or resource.get("resourceId") or "").strip()
    if not resource_id:
        raise ValueError("favorite resource id is required")
    favorite_id = str(resource.get("favoriteId") or resource_id)
    favorite_type = str(resource.get("type") or "").strip()
    if favorite_type not in {"characters", "artists", "references", "snippets"}:
        raise ValueError("unsupported favorite type")
    blocks = resource.get("blocks") or []
    metadata = {
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
    }
    with database(db_path) as connection:
        existing = connection.execute(
            "SELECT created_at FROM favorites WHERE id = ?",
            (favorite_id,),
        ).fetchone()
        created_at = (
            str(resource.get("favoritedAt") or existing["created_at"])
            if existing
            else str(resource.get("favoritedAt") or timestamp)
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
                favorite_id,
                resource_id,
                favorite_type,
                str(resource.get("name") or ""),
                str(resource.get("meta") or ""),
                str(resource.get("source") or ""),
                str(resource.get("sourceId") or ""),
                str(resource.get("targetBlock") or ""),
                str(resource.get("en") or ""),
                str(resource.get("zh") or ""),
                str(resource.get("thumbUrl") or ""),
                1 if resource.get("hasImage", True) else 0,
                json_dumps(blocks),
                json_dumps(metadata),
                created_at,
                timestamp,
            ),
        )
        row = connection.execute(
            "SELECT * FROM favorites WHERE id = ?",
            (favorite_id,),
        ).fetchone()
    return _favorite_from_row(row)


def delete_favorite(favorite_id: str, db_path: Path | str | None = None) -> bool:
    with database(db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM favorites WHERE id = ? OR resource_id = ?",
            (favorite_id, favorite_id),
        )
    return cursor.rowcount > 0


def get_settings(db_path: Path | str | None = None) -> dict:
    with database(db_path) as connection:
        rows = connection.execute("SELECT * FROM settings ORDER BY key").fetchall()
    return {row["key"]: json_loads(row["value_json"], None) for row in rows}


def put_settings(payload: dict, db_path: Path | str | None = None) -> dict:
    timestamp = now_iso()
    with database(db_path) as connection:
        for key, value in payload.items():
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
