"""SQLite persistence for the local Prompt Studio workspace."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = Path(
    os.environ.get("PROMPT_STUDIO_DB", ROOT / "data" / "prompt_studio.db")
)

SCHEMA_VERSION = 1
APPLICATION_ID = 0x41505354  # "APST"
RECOVERY_DIRECTORY_NAME = ".prompt-studio-recovery"
_INIT_LOCK = threading.Lock()

SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")
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
INTERNAL_SETTINGS_PREFIX = "__prompt_studio_internal_v1__:"
IDEMPOTENCY_LEDGER_PREFIX = f"{INTERNAL_SETTINGS_PREFIX}idempotency:"


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

CREATE TABLE IF NOT EXISTS model_research_runs (
    id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    status TEXT NOT NULL,
    error_code TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS model_evidence_snapshots (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES model_research_runs(id),
    source_class TEXT NOT NULL,
    requested_url TEXT NOT NULL,
    final_url TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    content_type TEXT,
    body_sha256 TEXT,
    extracted_text TEXT NOT NULL,
    fetch_status TEXT NOT NULL,
    error_code TEXT
);

CREATE TABLE IF NOT EXISTS model_evidence_claims (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES model_research_runs(id),
    field_path TEXT NOT NULL,
    value_json TEXT NOT NULL,
    evidence_class TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    rationale TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    application_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_profile_versions (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    parent_version_id TEXT REFERENCES model_profile_versions(id),
    research_run_id TEXT REFERENCES model_research_runs(id),
    lifecycle_status TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    claim_decisions_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    review_note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    activated_at TEXT,
    UNIQUE(profile_id, revision)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_model_profile_one_active
ON model_profile_versions(profile_id)
WHERE lifecycle_status = 'active';
"""


EXPECTED_SCHEMA_COLUMNS = {
    "projects": (
        ("id", "TEXT", 0, 1),
        ("name", "TEXT", 1, 0),
        ("mode", "TEXT", 1, 0),
        ("status", "TEXT", 1, 0),
        ("metadata_json", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ),
    "prompt_versions": (
        ("id", "TEXT", 0, 1),
        ("project_id", "TEXT", 1, 0),
        ("version", "INTEGER", 1, 0),
        ("source", "TEXT", 1, 0),
        ("positive_en", "TEXT", 1, 0),
        ("positive_zh", "TEXT", 1, 0),
        ("negative_en", "TEXT", 1, 0),
        ("negative_zh", "TEXT", 1, 0),
        ("blocks_json", "TEXT", 1, 0),
        ("metadata_json", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
    ),
    "resources": (
        ("id", "TEXT", 0, 1),
        ("type", "TEXT", 1, 0),
        ("name", "TEXT", 1, 0),
        ("meta", "TEXT", 1, 0),
        ("target_block", "TEXT", 1, 0),
        ("en", "TEXT", 1, 0),
        ("zh", "TEXT", 1, 0),
        ("blocks_json", "TEXT", 1, 0),
        ("metadata_json", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ),
    "favorites": (
        ("id", "TEXT", 0, 1),
        ("resource_id", "TEXT", 1, 0),
        ("type", "TEXT", 1, 0),
        ("name", "TEXT", 1, 0),
        ("meta", "TEXT", 1, 0),
        ("source", "TEXT", 1, 0),
        ("source_id", "TEXT", 1, 0),
        ("target_block", "TEXT", 1, 0),
        ("en", "TEXT", 1, 0),
        ("zh", "TEXT", 1, 0),
        ("thumb_url", "TEXT", 1, 0),
        ("has_image", "INTEGER", 1, 0),
        ("blocks_json", "TEXT", 1, 0),
        ("metadata_json", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ),
    "settings": (
        ("key", "TEXT", 0, 1),
        ("value_json", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ),
    "model_research_runs": (
        ("id", "TEXT", 0, 1),
        ("source_url", "TEXT", 1, 0),
        ("status", "TEXT", 1, 0),
        ("error_code", "TEXT", 0, 0),
        ("created_at", "TEXT", 1, 0),
        ("completed_at", "TEXT", 0, 0),
    ),
    "model_evidence_snapshots": (
        ("id", "TEXT", 0, 1),
        ("run_id", "TEXT", 1, 0),
        ("source_class", "TEXT", 1, 0),
        ("requested_url", "TEXT", 1, 0),
        ("final_url", "TEXT", 1, 0),
        ("retrieved_at", "TEXT", 1, 0),
        ("content_type", "TEXT", 0, 0),
        ("body_sha256", "TEXT", 0, 0),
        ("extracted_text", "TEXT", 1, 0),
        ("fetch_status", "TEXT", 1, 0),
        ("error_code", "TEXT", 0, 0),
    ),
    "model_evidence_claims": (
        ("id", "TEXT", 0, 1),
        ("run_id", "TEXT", 1, 0),
        ("field_path", "TEXT", 1, 0),
        ("value_json", "TEXT", 1, 0),
        ("evidence_class", "TEXT", 1, 0),
        ("evidence_refs_json", "TEXT", 1, 0),
        ("rationale", "TEXT", 1, 0),
        ("verification_status", "TEXT", 1, 0),
        ("application_status", "TEXT", 1, 0),
    ),
    "model_profile_versions": (
        ("id", "TEXT", 0, 1),
        ("profile_id", "TEXT", 1, 0),
        ("revision", "INTEGER", 1, 0),
        ("parent_version_id", "TEXT", 0, 0),
        ("research_run_id", "TEXT", 0, 0),
        ("lifecycle_status", "TEXT", 1, 0),
        ("profile_json", "TEXT", 1, 0),
        ("claim_decisions_json", "TEXT", 1, 0),
        ("content_sha256", "TEXT", 1, 0),
        ("review_note", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
        ("reviewed_at", "TEXT", 0, 0),
        ("activated_at", "TEXT", 0, 0),
    ),
}

EXPECTED_INDEX_COLUMNS = {
    "idx_prompt_versions_project": ("project_id", "version"),
    "idx_resources_type": ("type",),
    "idx_favorites_type_created": ("type", "created_at"),
    "idx_model_profile_one_active": ("profile_id",),
}


class DatabaseSchemaError(RuntimeError):
    """Raised when a database is corrupt or does not match a supported schema."""


class UnsupportedDatabaseVersionError(DatabaseSchemaError):
    """Raised rather than silently downgrading a database from a newer app."""


class VersionConflictError(RuntimeError):
    """Raised when a new version is based on a stale project revision."""

    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        self.expected_base_version = expected
        self.current_version = actual
        super().__init__(
            f"版本基线冲突：期望版本 {expected}，当前最新版本为 {actual}"
        )


class ProjectConflictError(RuntimeError):
    """Raised when project metadata is based on a stale update timestamp."""

    def __init__(self, expected: str, actual: str):
        self.expected = expected
        self.actual = actual
        self.expected_updated_at = expected
        self.current_updated_at = actual
        super().__init__(
            f"作品基线冲突：期望更新时间 {expected}，当前更新时间为 {actual}"
        )


class IdempotencyConflictError(RuntimeError):
    """Raised when an idempotency key is reused for a different request."""

    def __init__(self, operation: str):
        self.operation = operation
        super().__init__("Idempotency-Key 已用于不同的请求内容")


class IdempotencyLedgerError(RuntimeError):
    """Raised when a persisted replay record cannot be trusted."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _parse_iso_timestamp(value: str) -> datetime:
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _next_project_updated_at(previous: str) -> str:
    """Return a UTC timestamp strictly newer than this project's previous value."""

    candidate = now_iso()
    try:
        previous_time = _parse_iso_timestamp(previous)
        candidate_time = _parse_iso_timestamp(candidate)
    except (TypeError, ValueError):
        # Existing application rows use ISO timestamps. If a hand-edited legacy row
        # does not, still avoid reusing the exact CAS token.
        if candidate == previous:
            return (
                datetime.now(timezone.utc) + timedelta(microseconds=1)
            ).isoformat(timespec="microseconds")
        return candidate
    if candidate_time <= previous_time:
        candidate_time = previous_time + timedelta(microseconds=1)
        return candidate_time.isoformat(timespec="microseconds")
    return candidate


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


def canonical_json_hash(value: Any) -> str:
    """Return a stable SHA-256 fingerprint for strict JSON-compatible data."""

    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        encoded = serialized.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as error:
        raise ValueError("请求必须是可序列化的严格 UTF-8 JSON") from error
    return hashlib.sha256(encoded).hexdigest()


def validate_idempotency_key(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not IDEMPOTENCY_KEY_PATTERN.fullmatch(value):
        raise ValueError(
            "Idempotency-Key 必须为 1-128 位字母、数字、点、下划线、波浪线或连字符"
        )
    return value


def _idempotency_storage_key(operation: str, scope: str, key: str) -> str:
    digest = hashlib.sha256(
        f"{operation}\0{scope}\0{key}".encode("utf-8")
    ).hexdigest()
    return f"{IDEMPOTENCY_LEDGER_PREFIX}{digest}"


def _idempotency_descriptor(
    operation: str,
    scope: str,
    payload: dict,
    idempotency_key: str | None,
) -> tuple[str, str] | None:
    key = validate_idempotency_key(idempotency_key)
    if key is None:
        return None
    return (
        _idempotency_storage_key(operation, scope, key),
        canonical_json_hash(payload),
    )


def _read_idempotency_result(
    connection: sqlite3.Connection,
    operation: str,
    descriptor: tuple[str, str] | None,
) -> dict | None:
    if descriptor is None:
        return None
    storage_key, request_hash = descriptor
    row = connection.execute(
        "SELECT value_json FROM settings WHERE key = ?",
        (storage_key,),
    ).fetchone()
    if row is None:
        return None
    try:
        record = json.loads(
            row["value_json"],
            parse_constant=reject_json_constant,
            parse_float=strict_json_float,
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise IdempotencyLedgerError("幂等账本记录损坏") from error
    if (
        not isinstance(record, dict)
        or record.get("recordVersion") != 1
        or record.get("operation") != operation
        or not isinstance(record.get("requestHash"), str)
        or not isinstance(record.get("result"), dict)
    ):
        raise IdempotencyLedgerError("幂等账本记录格式无效")
    if record["requestHash"] != request_hash:
        raise IdempotencyConflictError(operation)
    return record["result"]


def _write_idempotency_result(
    connection: sqlite3.Connection,
    operation: str,
    descriptor: tuple[str, str] | None,
    result: dict,
) -> None:
    if descriptor is None:
        return
    storage_key, request_hash = descriptor
    record = {
        "recordVersion": 1,
        "operation": operation,
        "requestHash": request_hash,
        "result": result,
        "createdAt": now_iso(),
    }
    connection.execute(
        "INSERT INTO settings (key, value_json, updated_at) VALUES (?, ?, ?)",
        (storage_key, json_dumps(record), now_iso()),
    )


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
            if confidence is None:
                item.pop("confidence")
            elif (
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
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection
    except BaseException:
        connection.close()
        raise


def _schema_statements() -> list[str]:
    statements: list[str] = []
    buffer = ""
    for line in SCHEMA.splitlines():
        buffer = f"{buffer}\n{line}" if buffer else line
        if not sqlite3.complete_statement(buffer):
            continue
        statement = buffer.strip()
        buffer = ""
        if statement and not statement.upper().startswith("PRAGMA FOREIGN_KEYS"):
            statements.append(statement)
    if buffer.strip():
        raise DatabaseSchemaError("内置数据库结构包含不完整 SQL")
    return statements


def _user_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
    }


def _index_columns(
    connection: sqlite3.Connection,
    index_name: str,
) -> tuple[str, ...]:
    rows = connection.execute(f'PRAGMA index_info("{index_name}")').fetchall()
    return tuple(row["name"] for row in rows)


def _assert_database_integrity(connection: sqlite3.Connection) -> None:
    result = [row[0] for row in connection.execute("PRAGMA quick_check").fetchall()]
    if result != ["ok"]:
        raise DatabaseSchemaError("数据库完整性检查未通过")


def _validate_schema(connection: sqlite3.Connection) -> None:
    executable_schema_objects = connection.execute(
        """
        SELECT type, name FROM sqlite_master
        WHERE type IN ('trigger', 'view') AND name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    if executable_schema_objects:
        labels = ", ".join(
            f"{row['type']} {row['name']}" for row in executable_schema_objects
        )
        raise DatabaseSchemaError(f"数据库包含不允许的触发器或视图：{labels}")

    tables = _user_tables(connection)
    expected_tables = set(EXPECTED_SCHEMA_COLUMNS)
    if tables != expected_tables:
        missing = sorted(expected_tables - tables)
        extra = sorted(tables - expected_tables)
        details = []
        if missing:
            details.append(f"缺少表：{', '.join(missing)}")
        if extra:
            details.append(f"未知表：{', '.join(extra)}")
        raise DatabaseSchemaError("数据库结构不兼容（" + "；".join(details) + "）")

    for table_name, expected_columns in EXPECTED_SCHEMA_COLUMNS.items():
        rows = connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        actual_columns = tuple(
            (
                row["name"],
                str(row["type"]).upper(),
                int(row["notnull"]),
                int(row["pk"]),
            )
            for row in rows
        )
        if actual_columns != expected_columns:
            raise DatabaseSchemaError(f"数据库表 {table_name} 的字段结构不兼容")

    for index_name, expected_columns in EXPECTED_INDEX_COLUMNS.items():
        index_row = connection.execute(
            "SELECT tbl_name FROM sqlite_master WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        if (
            index_row is None
            or _index_columns(connection, index_name) != expected_columns
        ):
            raise DatabaseSchemaError(f"数据库索引 {index_name} 缺失或不兼容")

    active_index = connection.execute(
        """
        SELECT "unique", partial
        FROM pragma_index_list('model_profile_versions')
        WHERE name = 'idx_model_profile_one_active'
        """
    ).fetchone()
    active_index_sql_row = connection.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'index' AND name = 'idx_model_profile_one_active'
        """
    ).fetchone()
    active_index_sql = re.sub(
        r"\s+",
        " ",
        active_index_sql_row["sql"] if active_index_sql_row else "",
    ).casefold()
    if (
        active_index is None
        or int(active_index["unique"]) != 1
        or int(active_index["partial"]) != 1
        or "where lifecycle_status = 'active'" not in active_index_sql
    ):
        raise DatabaseSchemaError("model active-version index is incompatible")

    unique_version_index = False
    for row in connection.execute('PRAGMA index_list("prompt_versions")').fetchall():
        if int(row["unique"]) and _index_columns(connection, row["name"]) == (
            "project_id",
            "version",
        ):
            unique_version_index = True
            break
    if not unique_version_index:
        raise DatabaseSchemaError("提示词版本唯一约束缺失")

    foreign_keys = connection.execute(
        'PRAGMA foreign_key_list("prompt_versions")'
    ).fetchall()
    if not any(
        row["table"] == "projects"
        and row["from"] == "project_id"
        and row["to"] == "id"
        and str(row["on_delete"]).upper() == "CASCADE"
        for row in foreign_keys
    ):
        raise DatabaseSchemaError("提示词版本的作品外键缺失或不兼容")

    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise DatabaseSchemaError("数据库包含失效的外键引用")


def _classify_database(connection: sqlite3.Connection) -> dict:
    _assert_database_integrity(connection)
    schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])

    if schema_version > SCHEMA_VERSION:
        raise UnsupportedDatabaseVersionError(
            f"数据库版本 {schema_version} 高于当前支持版本 {SCHEMA_VERSION}"
        )
    if schema_version not in {0, SCHEMA_VERSION}:
        raise UnsupportedDatabaseVersionError(f"不支持数据库版本 {schema_version}")
    if application_id not in {0, APPLICATION_ID}:
        raise DatabaseSchemaError("数据库不是 Anima Prompt Studio 数据库")

    tables = _user_tables(connection)
    if not tables:
        if schema_version == 0 and application_id == 0:
            return {
                "kind": "empty",
                "schemaVersion": schema_version,
                "applicationId": application_id,
            }
        raise DatabaseSchemaError("数据库已标记版本，但缺少业务表")

    _validate_schema(connection)
    if schema_version == 0:
        return {
            "kind": "legacy",
            "schemaVersion": schema_version,
            "applicationId": application_id,
        }
    if application_id != APPLICATION_ID:
        raise DatabaseSchemaError("数据库缺少正确的应用标识")
    return {
        "kind": "current",
        "schemaVersion": schema_version,
        "applicationId": application_id,
    }


def _inspect_path(path: Path) -> dict:
    if path.exists() and not path.is_file():
        raise DatabaseSchemaError("数据库路径不是文件")
    if not path.exists() or path.stat().st_size == 0:
        return {"kind": "empty", "schemaVersion": 0, "applicationId": 0}
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect_read_only(path)
        return _classify_database(connection)
    except sqlite3.DatabaseError as error:
        raise DatabaseSchemaError("数据库文件损坏或无法读取") from error
    finally:
        if connection is not None:
            connection.close()


def recovery_directory(db_path: Path | str | None = None) -> Path:
    return get_db_path(db_path).parent / RECOVERY_DIRECTORY_NAME


def _create_recovery_snapshot(path: Path) -> Path:
    target_directory = recovery_directory(path)
    target_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = target_directory / (
        f"{path.stem}.pre-v0-to-v{SCHEMA_VERSION}.{timestamp}.{uuid.uuid4().hex[:8]}.db"
    )
    temporary = target.with_name(f"{target.name}.part")
    source: sqlite3.Connection | None = None
    destination: sqlite3.Connection | None = None
    try:
        source = _connect_read_only(path)
        destination = sqlite3.connect(temporary)
        destination.row_factory = sqlite3.Row
        destination.execute("PRAGMA foreign_keys = ON")
        source.backup(destination)
        snapshot_state = _classify_database(destination)
        if snapshot_state["kind"] != "legacy":
            raise DatabaseSchemaError("迁移前回滚快照版本不正确")
        destination.close()
        destination = None
        source.close()
        source = None
        with temporary.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        return target
    except sqlite3.DatabaseError as error:
        raise DatabaseSchemaError("无法创建有效的迁移前回滚快照") from error
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
        temporary.unlink(missing_ok=True)


@contextmanager
def database(db_path: Path | str | None = None):
    connection = connect(db_path)
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db(db_path: Path | str | None = None) -> Path:
    path = get_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _INIT_LOCK:
        state = _inspect_path(path)
        if state["kind"] == "current":
            return path

        connection = connect(path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            state = _classify_database(connection)
            if state["kind"] == "current":
                connection.rollback()
                return path
            if state["kind"] == "legacy":
                _create_recovery_snapshot(path)
            elif state["kind"] == "empty":
                for statement in _schema_statements():
                    connection.execute(statement)
            else:  # Defensive guard for future state types.
                raise DatabaseSchemaError("数据库迁移状态无效")

            connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            migrated = _classify_database(connection)
            if migrated["kind"] != "current":
                raise DatabaseSchemaError("数据库迁移没有达到目标版本")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
    return path


def get_database_status(db_path: Path | str | None = None) -> dict:
    """Return migration/schema status without creating or modifying the database."""

    path = get_db_path(db_path)
    path_exists = path.exists()
    exists = path.is_file()
    size_bytes = path.stat().st_size if exists else 0
    status = {
        "path": str(path),
        "exists": exists,
        "sizeBytes": size_bytes,
        "schemaVersion": 0,
        "supportedSchemaVersion": SCHEMA_VERSION,
        "applicationId": 0,
        "initialized": False,
        "needsMigration": True,
        "compatible": True,
        "integrity": "new" if not exists or size_bytes == 0 else "unknown",
        "state": "new",
    }
    if path_exists and not exists:
        status["compatible"] = False
        status["needsMigration"] = False
        status["integrity"] = "error"
        status["state"] = "error"
        status["error"] = "数据库路径不是文件"
        return status
    if not exists or size_bytes == 0:
        return status

    connection: sqlite3.Connection | None = None
    try:
        connection = _connect_read_only(path)
        status["schemaVersion"] = int(
            connection.execute("PRAGMA user_version").fetchone()[0]
        )
        status["applicationId"] = int(
            connection.execute("PRAGMA application_id").fetchone()[0]
        )
        state = _classify_database(connection)
        status["initialized"] = state["kind"] == "current"
        status["needsMigration"] = state["kind"] in {"legacy", "empty"}
        status["integrity"] = "ok"
        status["state"] = state["kind"]
    except (DatabaseSchemaError, sqlite3.DatabaseError) as error:
        status["compatible"] = False
        status["needsMigration"] = False
        status["integrity"] = "error"
        status["state"] = "error"
        status["error"] = (
            str(error)
            if isinstance(error, DatabaseSchemaError)
            else "数据库文件损坏或无法读取"
        )
    finally:
        if connection is not None:
            connection.close()
    return status


# Model research/profile SQL remains here so all SQLite access has one audited
# boundary. model_profile_store owns lifecycle validation and orchestration.
def research_run_row(connection: sqlite3.Connection, run_id: str):
    return connection.execute(
        "SELECT * FROM model_research_runs WHERE id = ?", (run_id,)
    ).fetchone()


def insert_research_run_row(
    connection: sqlite3.Connection, values: tuple[Any, ...]
) -> None:
    connection.execute(
        """
        INSERT INTO model_research_runs
            (id, source_url, status, error_code, created_at, completed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        values,
    )


def complete_research_run_row(
    connection: sqlite3.Connection, run_id: str, completed_at: str
) -> None:
    connection.execute(
        """
        UPDATE model_research_runs
        SET status = 'completed', completed_at = ?
        WHERE id = ? AND status = 'pending'
        """,
        (completed_at, run_id),
    )


def insert_evidence_snapshot_row(
    connection: sqlite3.Connection, values: tuple[Any, ...]
) -> None:
    connection.execute(
        """
        INSERT INTO model_evidence_snapshots (
            id, run_id, source_class, requested_url, final_url, retrieved_at,
            content_type, body_sha256, extracted_text, fetch_status, error_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )


def insert_evidence_claim_row(
    connection: sqlite3.Connection, values: tuple[Any, ...]
) -> None:
    connection.execute(
        """
        INSERT INTO model_evidence_claims (
            id, run_id, field_path, value_json, evidence_class,
            evidence_refs_json, rationale, verification_status,
            application_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )


def evidence_snapshot_rows(connection: sqlite3.Connection, run_id: str):
    return connection.execute(
        """
        SELECT * FROM model_evidence_snapshots
        WHERE run_id = ? ORDER BY id
        """,
        (run_id,),
    ).fetchall()


def evidence_claim_rows(connection: sqlite3.Connection, run_id: str):
    return connection.execute(
        """
        SELECT * FROM model_evidence_claims
        WHERE run_id = ? ORDER BY field_path, id
        """,
        (run_id,),
    ).fetchall()


def profile_version_row(connection: sqlite3.Connection, version_id: str):
    return connection.execute(
        "SELECT * FROM model_profile_versions WHERE id = ?", (version_id,)
    ).fetchone()


def next_profile_revision(connection: sqlite3.Connection, profile_id: str) -> int:
    return int(
        connection.execute(
            """
            SELECT COALESCE(MAX(revision), 0) + 1
            FROM model_profile_versions WHERE profile_id = ?
            """,
            (profile_id,),
        ).fetchone()[0]
    )


def insert_profile_version_row(
    connection: sqlite3.Connection, values: tuple[Any, ...]
) -> None:
    connection.execute(
        """
        INSERT INTO model_profile_versions (
            id, profile_id, revision, parent_version_id, research_run_id,
            lifecycle_status, profile_json, claim_decisions_json,
            content_sha256, review_note, created_at, reviewed_at, activated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )


def active_profile_version_row(
    connection: sqlite3.Connection, profile_id: str
):
    return connection.execute(
        """
        SELECT * FROM model_profile_versions
        WHERE profile_id = ? AND lifecycle_status = 'active'
        """,
        (profile_id,),
    ).fetchone()


def supersede_profile_version_row(
    connection: sqlite3.Connection, version_id: str
) -> None:
    connection.execute(
        """
        UPDATE model_profile_versions
        SET lifecycle_status = 'superseded'
        WHERE id = ? AND lifecycle_status = 'active'
        """,
        (version_id,),
    )


def activate_profile_version_row(
    connection: sqlite3.Connection, version_id: str, activated_at: str
) -> None:
    connection.execute(
        """
        UPDATE model_profile_versions
        SET lifecycle_status = 'active', activated_at = ?
        WHERE id = ? AND lifecycle_status = 'reviewed'
        """,
        (activated_at, version_id),
    )


def active_profile_version_rows(connection: sqlite3.Connection):
    return connection.execute(
        """
        SELECT * FROM model_profile_versions
        WHERE lifecycle_status = 'active'
        ORDER BY profile_id
        """
    ).fetchall()


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
            SELECT
                projects.*,
                COUNT(prompt_versions.id) AS version_count,
                MAX(prompt_versions.version) AS latest_version
            FROM projects
            LEFT JOIN prompt_versions
                ON prompt_versions.project_id = projects.id
            GROUP BY projects.id
            ORDER BY projects.updated_at DESC, projects.created_at DESC
            """
        ).fetchall()
    items = []
    for row in rows:
        item = _project_from_row(row)
        item["versionCount"] = int(row["version_count"])
        item["latestVersion"] = (
            int(row["latest_version"])
            if row["latest_version"] is not None
            else None
        )
        items.append(item)
    return items


def _legacy_create_project(payload: dict, db_path: Path | str | None = None) -> dict:
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


def _legacy_update_project(
    project_id: str,
    payload: dict,
    db_path: Path | str | None = None,
) -> dict | None:
    if not isinstance(payload, dict):
        raise ValueError("作品更新数据必须是对象")
    project_id = validate_resource_id(project_id, "作品 ID")
    allowed_keys = {"name", "mode", "status", "metadata", "baseUpdatedAt"}
    unknown_keys = [key for key in payload if key not in allowed_keys]
    if unknown_keys:
        labels = ", ".join(sorted(str(key) for key in unknown_keys))
        raise ValueError(f"作品更新包含不允许的字段：{labels}")
    has_base_updated_at = "baseUpdatedAt" in payload
    base_updated_at = None
    if has_base_updated_at:
        base_updated_at = require_string(
            payload,
            "baseUpdatedAt",
            max_length=64,
        )
        if not base_updated_at:
            raise ValueError("baseUpdatedAt 不能为空")

    values: dict[str, str] = {}
    if "name" in payload:
        values["name"] = (
            require_string(payload, "name", max_length=200).strip() or "未命名作品"
        )
    if "mode" in payload:
        mode = require_string(payload, "mode", max_length=16)
        if mode not in PROJECT_MODES:
            raise ValueError("mode 只允许 text 或 image")
        values["mode"] = mode
    if "status" in payload:
        status = require_string(payload, "status", max_length=32)
        if not SAFE_STATUS_PATTERN.fullmatch(status):
            raise ValueError("status 无效")
        values["status"] = status
    if "metadata" in payload:
        values["metadata_json"] = json_dumps(sanitize_metadata(payload["metadata"]))

    if not values:
        raise ValueError("作品更新至少需要一个可修改字段")

    assignments = [f"{column} = ?" for column in values]
    parameters: list[object] = list(values.values())
    assignments.append("updated_at = ?")
    with database(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT updated_at FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if row is None:
            return None
        current_updated_at = row["updated_at"]
        if has_base_updated_at and base_updated_at != current_updated_at:
            raise ProjectConflictError(base_updated_at, current_updated_at)
        timestamp = _next_project_updated_at(current_updated_at)
        parameters.extend([timestamp, project_id])
        cursor = connection.execute(
            f"UPDATE projects SET {', '.join(assignments)} WHERE id = ?",
            parameters,
        )
        if cursor.rowcount == 0:
            return None
    return get_project(project_id, db_path)


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


def _legacy_create_prompt_version(
    project_id: str,
    payload: dict,
    db_path: Path | str | None = None,
) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("版本数据必须是对象")
    project_id = validate_resource_id(project_id, "作品 ID")
    version_id = (
        validate_id(payload["id"], "版本 ID")
        if "id" in payload
        else new_id("version")
    )
    if "version" in payload:
        raise ValueError("version 由服务端自动分配")
    has_base_version = "baseVersion" in payload
    base_version = payload.get("baseVersion")
    if has_base_version and (
        isinstance(base_version, bool)
        or not isinstance(base_version, int)
        or not 0 <= base_version <= 9_223_372_036_854_775_807
    ):
        raise ValueError("baseVersion 必须是非负整数")
    has_base_updated_at = "baseUpdatedAt" in payload
    base_updated_at = None
    if has_base_updated_at:
        base_updated_at = require_string(
            payload,
            "baseUpdatedAt",
            max_length=64,
        )
        if not base_updated_at:
            raise ValueError("baseUpdatedAt 不能为空")
    source = require_string(payload, "source", "manual", max_length=64)
    positive_en = require_string(payload, "positiveEn", "", max_length=200_000)
    positive_zh = require_string(payload, "positiveZh", "", max_length=200_000)
    negative_en = require_string(payload, "negativeEn", "", max_length=100_000)
    negative_zh = require_string(payload, "negativeZh", "", max_length=100_000)
    blocks = normalize_blocks(payload.get("blocks"))
    metadata = sanitize_metadata(payload.get("metadata", {}))
    with database(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        project_row = connection.execute(
            "SELECT updated_at FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if project_row is None:
            raise ValueError("作品不存在")
        current = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM prompt_versions WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
        if has_base_version and base_version != current:
            raise VersionConflictError(base_version, current)
        if (
            has_base_updated_at
            and base_updated_at != project_row["updated_at"]
        ):
            raise ProjectConflictError(
                base_updated_at,
                project_row["updated_at"],
            )
        timestamp = _next_project_updated_at(project_row["updated_at"])
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
    item = _version_from_row(row)
    item["projectUpdatedAt"] = timestamp
    return item


def _project_with_versions_from_connection(
    connection: sqlite3.Connection,
    project_id: str,
) -> dict | None:
    project = connection.execute(
        "SELECT * FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    if project is None:
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


def create_project(
    payload: dict,
    db_path: Path | str | None = None,
    *,
    idempotency_key: str | None = None,
) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("作品数据必须是对象")
    descriptor = _idempotency_descriptor(
        "project.create", "global", payload, idempotency_key
    )
    with database(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        replay = _read_idempotency_result(
            connection, "project.create", descriptor
        )
        if replay is not None:
            replay_id = replay.get("id")
            if not isinstance(replay_id, str) or connection.execute(
                "SELECT 1 FROM projects WHERE id = ?", (replay_id,)
            ).fetchone() is None:
                raise IdempotencyLedgerError("幂等账本引用的作品不存在")
            return replay

        timestamp = now_iso()
        project_id = (
            validate_id(payload["id"], "作品 ID")
            if "id" in payload
            else new_id("project")
        )
        name = require_string(
            payload, "name", "未命名作品", max_length=200
        ).strip() or "未命名作品"
        mode = require_string(payload, "mode", "text", max_length=16)
        if mode not in PROJECT_MODES:
            raise ValueError("mode 只允许 text 或 image")
        status = require_string(payload, "status", "draft", max_length=32)
        if not SAFE_STATUS_PATTERN.fullmatch(status):
            raise ValueError("status 无效")
        metadata = sanitize_metadata(payload.get("metadata", {}))
        connection.execute(
            """
            INSERT INTO projects (
                id, name, mode, status, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                name,
                mode,
                status,
                json_dumps(metadata),
                timestamp,
                timestamp,
            ),
        )
        result = _project_with_versions_from_connection(connection, project_id)
        if result is None:  # pragma: no cover - defensive invariant
            raise IdempotencyLedgerError("作品写入后不可读取")
        _write_idempotency_result(
            connection, "project.create", descriptor, result
        )
        return result


def update_project(
    project_id: str,
    payload: dict,
    db_path: Path | str | None = None,
    *,
    idempotency_key: str | None = None,
) -> dict | None:
    if not isinstance(payload, dict):
        raise ValueError("作品更新数据必须是对象")
    project_id = validate_resource_id(project_id, "作品 ID")
    descriptor = _idempotency_descriptor(
        "project.update", project_id, payload, idempotency_key
    )
    with database(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        replay = _read_idempotency_result(
            connection, "project.update", descriptor
        )
        if replay is not None:
            if replay.get("id") != project_id or connection.execute(
                "SELECT 1 FROM projects WHERE id = ?", (project_id,)
            ).fetchone() is None:
                raise IdempotencyLedgerError("幂等账本引用的作品不存在")
            return replay

        allowed_keys = {"name", "mode", "status", "metadata", "baseUpdatedAt"}
        unknown_keys = [key for key in payload if key not in allowed_keys]
        if unknown_keys:
            labels = ", ".join(sorted(str(key) for key in unknown_keys))
            raise ValueError(f"作品更新包含不允许的字段：{labels}")
        has_base_updated_at = "baseUpdatedAt" in payload
        base_updated_at = None
        if has_base_updated_at:
            base_updated_at = require_string(
                payload, "baseUpdatedAt", max_length=64
            )
            if not base_updated_at:
                raise ValueError("baseUpdatedAt 不能为空")

        values: dict[str, str] = {}
        if "name" in payload:
            values["name"] = (
                require_string(payload, "name", max_length=200).strip()
                or "未命名作品"
            )
        if "mode" in payload:
            mode = require_string(payload, "mode", max_length=16)
            if mode not in PROJECT_MODES:
                raise ValueError("mode 只允许 text 或 image")
            values["mode"] = mode
        if "status" in payload:
            status = require_string(payload, "status", max_length=32)
            if not SAFE_STATUS_PATTERN.fullmatch(status):
                raise ValueError("status 无效")
            values["status"] = status
        if "metadata" in payload:
            values["metadata_json"] = json_dumps(
                sanitize_metadata(payload["metadata"])
            )
        if not values:
            raise ValueError("作品更新至少需要一个可修改字段")

        row = connection.execute(
            "SELECT updated_at FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            return None
        current_updated_at = row["updated_at"]
        if has_base_updated_at and base_updated_at != current_updated_at:
            raise ProjectConflictError(base_updated_at, current_updated_at)
        timestamp = _next_project_updated_at(current_updated_at)
        assignments = [f"{column} = ?" for column in values]
        assignments.append("updated_at = ?")
        parameters: list[object] = [*values.values(), timestamp, project_id]
        connection.execute(
            f"UPDATE projects SET {', '.join(assignments)} WHERE id = ?",
            parameters,
        )
        result = _project_with_versions_from_connection(connection, project_id)
        if result is None:  # pragma: no cover - defensive invariant
            raise IdempotencyLedgerError("作品更新后不可读取")
        _write_idempotency_result(
            connection, "project.update", descriptor, result
        )
        return result


def create_prompt_version(
    project_id: str,
    payload: dict,
    db_path: Path | str | None = None,
    *,
    idempotency_key: str | None = None,
) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("版本数据必须是对象")
    project_id = validate_resource_id(project_id, "作品 ID")
    descriptor = _idempotency_descriptor(
        "version.create", project_id, payload, idempotency_key
    )
    with database(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        replay = _read_idempotency_result(
            connection, "version.create", descriptor
        )
        if replay is not None:
            replay_id = replay.get("id")
            row = (
                connection.execute(
                    "SELECT project_id FROM prompt_versions WHERE id = ?",
                    (replay_id,),
                ).fetchone()
                if isinstance(replay_id, str)
                else None
            )
            if row is None or row["project_id"] != project_id:
                raise IdempotencyLedgerError("幂等账本引用的版本不存在")
            return replay

        version_id = (
            validate_id(payload["id"], "版本 ID")
            if "id" in payload
            else new_id("version")
        )
        if "version" in payload:
            raise ValueError("version 由服务端自动分配")
        has_base_version = "baseVersion" in payload
        base_version = payload.get("baseVersion")
        if has_base_version and (
            isinstance(base_version, bool)
            or not isinstance(base_version, int)
            or not 0 <= base_version <= 9_223_372_036_854_775_807
        ):
            raise ValueError("baseVersion 必须是非负整数")
        has_base_updated_at = "baseUpdatedAt" in payload
        base_updated_at = None
        if has_base_updated_at:
            base_updated_at = require_string(
                payload, "baseUpdatedAt", max_length=64
            )
            if not base_updated_at:
                raise ValueError("baseUpdatedAt 不能为空")
        source = require_string(payload, "source", "manual", max_length=64)
        positive_en = require_string(
            payload, "positiveEn", "", max_length=200_000
        )
        positive_zh = require_string(
            payload, "positiveZh", "", max_length=200_000
        )
        negative_en = require_string(
            payload, "negativeEn", "", max_length=100_000
        )
        negative_zh = require_string(
            payload, "negativeZh", "", max_length=100_000
        )
        blocks = normalize_blocks(payload.get("blocks"))
        metadata = sanitize_metadata(payload.get("metadata", {}))

        project_row = connection.execute(
            "SELECT updated_at FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if project_row is None:
            raise ValueError("作品不存在")
        current = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM prompt_versions WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
        if has_base_version and base_version != current:
            raise VersionConflictError(base_version, current)
        if has_base_updated_at and base_updated_at != project_row["updated_at"]:
            raise ProjectConflictError(base_updated_at, project_row["updated_at"])
        timestamp = _next_project_updated_at(project_row["updated_at"])
        version_number = current + 1
        connection.execute(
            """
            INSERT INTO prompt_versions (
                id, project_id, version, source,
                positive_en, positive_zh, negative_en, negative_zh,
                blocks_json, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            "SELECT * FROM prompt_versions WHERE id = ?", (version_id,)
        ).fetchone()
        if row is None:  # pragma: no cover - defensive invariant
            raise IdempotencyLedgerError("版本写入后不可读取")
        result = _version_from_row(row)
        result["projectUpdatedAt"] = timestamp
        _write_idempotency_result(
            connection, "version.create", descriptor, result
        )
        return result


def commit_workspace(
    payload: dict,
    db_path: Path | str | None = None,
    *,
    idempotency_key: str | None = None,
) -> dict:
    """Atomically create/update a project and optionally append one full version."""

    if not isinstance(payload, dict):
        raise ValueError("工作区提交必须是对象")
    allowed_top = {"operationId", "createProject", "project", "version"}
    unknown_top = [key for key in payload if key not in allowed_top]
    if unknown_top:
        raise ValueError(
            "工作区提交包含不允许的字段：" + ", ".join(sorted(unknown_top))
        )
    operation_id = validate_id(payload.get("operationId"), "operationId")
    header_key = validate_idempotency_key(idempotency_key)
    if header_key is not None and header_key != operation_id:
        raise ValueError("Idempotency-Key 必须与 operationId 相同")
    project_payload = payload.get("project")
    if not isinstance(project_payload, dict):
        raise ValueError("project 必须是对象")
    project_id = validate_id(project_payload.get("id"), "作品 ID")
    create_new = payload.get("createProject", False)
    if not isinstance(create_new, bool):
        raise ValueError("createProject 必须是布尔值")
    descriptor = _idempotency_descriptor(
        "workspace.commit", project_id, payload, operation_id
    )

    allowed_project = {
        "id",
        "name",
        "mode",
        "status",
        "metadata",
        "baseUpdatedAt",
    }
    unknown_project = [key for key in project_payload if key not in allowed_project]
    if unknown_project:
        raise ValueError(
            "project 包含不允许的字段：" + ", ".join(sorted(unknown_project))
        )
    name = require_string(
        project_payload, "name", "未命名作品", max_length=200
    ).strip() or "未命名作品"
    mode = require_string(project_payload, "mode", "text", max_length=16)
    if mode not in PROJECT_MODES:
        raise ValueError("mode 只允许 text 或 image")
    status = require_string(project_payload, "status", "draft", max_length=32)
    if not SAFE_STATUS_PATTERN.fullmatch(status):
        raise ValueError("status 无效")
    project_metadata = sanitize_metadata(project_payload.get("metadata", {}))
    base_updated_at = project_payload.get("baseUpdatedAt")
    if create_new:
        if base_updated_at not in (None, ""):
            raise ValueError("新建作品不能提供 baseUpdatedAt")
    else:
        if not isinstance(base_updated_at, str) or not base_updated_at:
            raise ValueError("更新作品必须提供 baseUpdatedAt")
        ensure_utf8_text(base_updated_at, "baseUpdatedAt")
        if len(base_updated_at) > 64:
            raise ValueError("baseUpdatedAt 超过长度限制")

    version_payload = payload.get("version")
    if version_payload is not None and not isinstance(version_payload, dict):
        raise ValueError("version 必须是对象或 null")
    normalized_version: dict | None = None
    if version_payload is not None:
        allowed_version = {
            "id",
            "baseVersion",
            "source",
            "positiveEn",
            "positiveZh",
            "negativeEn",
            "negativeZh",
            "blocks",
            "metadata",
        }
        unknown_version = [key for key in version_payload if key not in allowed_version]
        if unknown_version:
            raise ValueError(
                "version 包含不允许的字段：" + ", ".join(sorted(unknown_version))
            )
        version_id = validate_id(version_payload.get("id"), "版本 ID")
        base_version = version_payload.get("baseVersion")
        if (
            isinstance(base_version, bool)
            or not isinstance(base_version, int)
            or not 0 <= base_version <= 9_223_372_036_854_775_807
        ):
            raise ValueError("baseVersion 必须是非负整数")
        normalized_version = {
            "id": version_id,
            "baseVersion": base_version,
            "source": require_string(
                version_payload, "source", "manual", max_length=64
            ),
            "positiveEn": require_string(
                version_payload, "positiveEn", "", max_length=200_000
            ),
            "positiveZh": require_string(
                version_payload, "positiveZh", "", max_length=200_000
            ),
            "negativeEn": require_string(
                version_payload, "negativeEn", "", max_length=100_000
            ),
            "negativeZh": require_string(
                version_payload, "negativeZh", "", max_length=100_000
            ),
            "blocks": normalize_blocks(version_payload.get("blocks")),
            "metadata": sanitize_metadata(version_payload.get("metadata", {})),
        }

    with database(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        replay = _read_idempotency_result(
            connection, "workspace.commit", descriptor
        )
        if replay is not None:
            replay_project = replay.get("project")
            replay_version = replay.get("version")
            if (
                not isinstance(replay_project, dict)
                or replay_project.get("id") != project_id
                or connection.execute(
                    "SELECT 1 FROM projects WHERE id = ?", (project_id,)
                ).fetchone()
                is None
            ):
                raise IdempotencyLedgerError("幂等账本引用的作品不存在")
            if replay_version is not None:
                if not isinstance(replay_version, dict) or connection.execute(
                    "SELECT 1 FROM prompt_versions WHERE id = ? AND project_id = ?",
                    (replay_version.get("id"), project_id),
                ).fetchone() is None:
                    raise IdempotencyLedgerError("幂等账本引用的版本不存在")
            return replay

        existing = connection.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        initial_timestamp = now_iso()
        if create_new:
            if existing is not None:
                raise sqlite3.IntegrityError("作品 ID 已存在")
            connection.execute(
                """
                INSERT INTO projects (
                    id, name, mode, status, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    name,
                    mode,
                    status,
                    json_dumps(project_metadata),
                    initial_timestamp,
                    initial_timestamp,
                ),
            )
            current_updated_at = initial_timestamp
        else:
            if existing is None:
                raise ValueError("作品不存在")
            current_updated_at = existing["updated_at"]
            if base_updated_at != current_updated_at:
                raise ProjectConflictError(base_updated_at, current_updated_at)

        current_version = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM prompt_versions WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
        version_result = None
        if normalized_version is not None:
            if normalized_version["baseVersion"] != current_version:
                raise VersionConflictError(
                    normalized_version["baseVersion"], current_version
                )
            timestamp = _next_project_updated_at(current_updated_at)
            version_number = current_version + 1
            connection.execute(
                """
                INSERT INTO prompt_versions (
                    id, project_id, version, source,
                    positive_en, positive_zh, negative_en, negative_zh,
                    blocks_json, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_version["id"],
                    project_id,
                    version_number,
                    normalized_version["source"],
                    normalized_version["positiveEn"],
                    normalized_version["positiveZh"],
                    normalized_version["negativeEn"],
                    normalized_version["negativeZh"],
                    json_dumps(normalized_version["blocks"]),
                    json_dumps(normalized_version["metadata"]),
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM prompt_versions WHERE id = ?",
                (normalized_version["id"],),
            ).fetchone()
            if row is None:  # pragma: no cover - defensive invariant
                raise IdempotencyLedgerError("版本写入后不可读取")
            version_result = _version_from_row(row)
            version_result["projectUpdatedAt"] = timestamp
        else:
            timestamp = _next_project_updated_at(current_updated_at)

        connection.execute(
            """
            UPDATE projects
            SET name = ?, mode = ?, status = ?, metadata_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                name,
                mode,
                status,
                json_dumps(project_metadata),
                timestamp,
                project_id,
            ),
        )
        project_result = _project_with_versions_from_connection(
            connection, project_id
        )
        if project_result is None:  # pragma: no cover - defensive invariant
            raise IdempotencyLedgerError("作品提交后不可读取")
        result = {
            "operationId": operation_id,
            "createdProject": create_new,
            "project": project_result,
            "version": version_result,
        }
        _write_idempotency_result(
            connection, "workspace.commit", descriptor, result
        )
        return result


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
        rows = connection.execute(
            "SELECT * FROM settings WHERE substr(key, 1, ?) <> ? ORDER BY key",
            (len(INTERNAL_SETTINGS_PREFIX), INTERNAL_SETTINGS_PREFIX),
        ).fetchall()
    return {row["key"]: json_loads(row["value_json"], None) for row in rows}


def put_settings(
    payload: dict,
    db_path: Path | str | None = None,
    *,
    reset_keys: set[str] | tuple[str, ...] | list[str] = (),
) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("设置必须是对象")
    if not isinstance(reset_keys, (set, tuple, list)):
        raise ValueError("重置设置键必须是集合")
    normalized_reset_keys = set(reset_keys)
    if set(payload) & normalized_reset_keys:
        raise ValueError("同一个设置键不能同时更新和重置")
    for key in normalized_reset_keys:
        if isinstance(key, str) and key.startswith(INTERNAL_SETTINGS_PREFIX):
            raise ValueError("设置键使用了保留的内部前缀")
        if not isinstance(key, str) or not key or len(key) > 128:
            raise ValueError("设置键无效")
    timestamp = now_iso()
    with database(db_path) as connection:
        connection.executemany(
            "DELETE FROM settings WHERE key = ?",
            [(key,) for key in sorted(normalized_reset_keys)],
        )
        for key, value in payload.items():
            if isinstance(key, str) and key.startswith(INTERNAL_SETTINGS_PREFIX):
                raise ValueError("设置键使用了保留的内部前缀")
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
