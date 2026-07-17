"""Safe logical export and restore for Prompt Studio SQLite data.

The archive is deliberately logical: it contains JSON records only.  It never
reads or packages model, LoRA, image, or other binary files referenced by the
database.  Restore requires an explicit non-default database path so callers
can validate the workflow against a staging database before any production
swap is considered.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import db


FORMAT_ID = "anima-prompt-studio-logical-backup"
FORMAT_VERSION = 1

MANIFEST_PATH = "manifest.json"
PROJECTS_PATH = "projects.json"
RESOURCES_PATH = "resources.json"
FAVORITES_PATH = "favorites.json"
SETTINGS_PATH = "settings.json"

PROJECTS_SCHEMA = "prompt-studio/projects-v1"
RESOURCES_SCHEMA = "prompt-studio/resources-v1"
FAVORITES_SCHEMA = "prompt-studio/favorites-v1"
SETTINGS_SCHEMA = "prompt-studio/settings-v1"

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_FILES = 16
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_JSON_DEPTH = 32

# A positive allowlist is intentional.  URLs can contain embedded credentials,
# so endpoint settings are excluded alongside API keys.  New setting names are
# not exported until reviewed.
SAFE_SETTING_KEYS = frozenset(
    {
        "textProvider",
        "localTextModel",
        "apiTextModel",
        "expansionLevel",
        "visionProvider",
        "residency",
        "autoCombine",
    }
)

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class BackupError(RuntimeError):
    """Base class for logical backup failures."""


class BackupValidationError(BackupError):
    """Raised before restore when an archive or target is invalid."""


class BackupConflictError(BackupError):
    """Raised when conflict="reject" finds existing records."""

    def __init__(self, conflicts: list[str]):
        self.conflicts = tuple(conflicts)
        super().__init__("restore conflicts: " + ", ".join(conflicts))


def _json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as error:
        raise BackupValidationError("value is not strict UTF-8 JSON") from error


def _reject_json_constant(token: str) -> None:
    raise ValueError(f"invalid JSON constant: {token}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_json_bytes(raw: bytes, label: str) -> object:
    if len(raw) > MAX_FILE_BYTES:
        raise BackupValidationError(f"{label} exceeds the per-file size limit")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_object,
        )
        _validate_json_tree(value, label)
        return value
    except BackupValidationError:
        raise
    except (UnicodeDecodeError, UnicodeEncodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise BackupValidationError(f"{label} is not strict UTF-8 JSON") from error


def _validate_json_tree(value: object, label: str, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise BackupValidationError(f"{label} exceeds the JSON nesting limit")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BackupValidationError(f"{label} contains a non-finite number")
        return
    if isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as error:
            raise BackupValidationError(f"{label} contains invalid Unicode") from error
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_tree(item, f"{label}[{index}]", depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise BackupValidationError(f"{label} contains a non-string key")
            _validate_json_tree(key, f"{label}.key", depth + 1)
            _validate_json_tree(item, f"{label}.{key}", depth + 1)
        return
    raise BackupValidationError(f"{label} contains an unsupported JSON value")


def _strict_stored_json(raw: str, expected_type: type, label: str) -> object:
    value = _strict_json_bytes(raw.encode("utf-8"), label)
    if not isinstance(value, expected_type):
        raise BackupValidationError(f"{label} has the wrong JSON type")
    return value


def _scrub_sensitive(value: object) -> object:
    """Return a JSON-safe copy with secret-looking dictionary keys removed."""

    if isinstance(value, dict):
        return {
            str(key): _scrub_sensitive(child)
            for key, child in value.items()
            if not db.is_sensitive_key(key)
        }
    if isinstance(value, list):
        return [_scrub_sensitive(child) for child in value]
    return value


def _assert_no_sensitive_keys(value: object, label: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if db.is_sensitive_key(key):
                raise BackupValidationError(f"{label} contains a sensitive key")
            _assert_no_sensitive_keys(child, f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_sensitive_keys(child, f"{label}[{index}]")


def _read_only_connection(path: Path) -> sqlite3.Connection:
    status = db.get_database_status(path)
    if not status.get("initialized") or not status.get("compatible"):
        raise BackupValidationError("source database is not a current Prompt Studio database")
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _project_record(row: sqlite3.Row, versions: list[sqlite3.Row]) -> dict:
    metadata = _strict_stored_json(row["metadata_json"], dict, "project metadata")
    return {
        "id": row["id"],
        "name": row["name"],
        "mode": row["mode"],
        "status": row["status"],
        "metadata": _scrub_sensitive(metadata),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "versions": [_version_record(version) for version in versions],
    }


def _version_record(row: sqlite3.Row) -> dict:
    blocks = _strict_stored_json(row["blocks_json"], list, "version blocks")
    metadata = _strict_stored_json(row["metadata_json"], dict, "version metadata")
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "version": row["version"],
        "source": row["source"],
        "positiveEn": row["positive_en"],
        "positiveZh": row["positive_zh"],
        "negativeEn": row["negative_en"],
        "negativeZh": row["negative_zh"],
        "blocks": blocks,
        "metadata": _scrub_sensitive(metadata),
        "createdAt": row["created_at"],
    }


def _resource_record(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "type": row["type"],
        "name": row["name"],
        "meta": row["meta"],
        "targetBlock": row["target_block"],
        "en": row["en"],
        "zh": row["zh"],
        "blocks": _strict_stored_json(row["blocks_json"], list, "resource blocks"),
        "metadata": _scrub_sensitive(
            _strict_stored_json(row["metadata_json"], dict, "resource metadata")
        ),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _favorite_record(row: sqlite3.Row) -> dict:
    thumb_url = row["thumb_url"]
    if isinstance(thumb_url, str) and thumb_url.casefold().startswith("data:"):
        thumb_url = ""
    return {
        "storageId": row["id"],
        "resourceId": row["resource_id"],
        "type": row["type"],
        "name": row["name"],
        "meta": row["meta"],
        "source": row["source"],
        "sourceId": row["source_id"],
        "targetBlock": row["target_block"],
        "en": row["en"],
        "zh": row["zh"],
        "thumbUrl": thumb_url,
        "hasImage": bool(row["has_image"]),
        "blocks": _strict_stored_json(row["blocks_json"], list, "favorite blocks"),
        "metadata": _scrub_sensitive(
            _strict_stored_json(row["metadata_json"], dict, "favorite metadata")
        ),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _collect_files(db_path: Path, project_id: str | None) -> tuple[str, dict[str, object]]:
    connection = _read_only_connection(db_path)
    try:
        if project_id is not None:
            try:
                project_id = db.validate_resource_id(project_id, "project ID")
            except ValueError as error:
                raise BackupValidationError("project ID is invalid") from error
            project_rows = connection.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchall()
            if not project_rows:
                raise BackupValidationError("project does not exist")
            scope = "project"
        else:
            project_rows = connection.execute(
                "SELECT * FROM projects ORDER BY id"
            ).fetchall()
            scope = "full"

        projects = []
        for row in project_rows:
            versions = connection.execute(
                "SELECT * FROM prompt_versions WHERE project_id = ? ORDER BY version, id",
                (row["id"],),
            ).fetchall()
            projects.append(_project_record(row, versions))

        files: dict[str, object] = {
            PROJECTS_PATH: {"schema": PROJECTS_SCHEMA, "items": projects}
        }
        if scope == "full":
            resources = [
                _resource_record(row)
                for row in connection.execute("SELECT * FROM resources ORDER BY id")
            ]
            favorites = [
                _favorite_record(row)
                for row in connection.execute("SELECT * FROM favorites ORDER BY id")
            ]
            settings = []
            rows = connection.execute(
                "SELECT * FROM settings ORDER BY key"
            ).fetchall()
            for row in rows:
                key = row["key"]
                if key not in SAFE_SETTING_KEYS or key.startswith(db.INTERNAL_SETTINGS_PREFIX):
                    continue
                value = _strict_stored_json(row["value_json"], object, "setting value")
                value = _scrub_sensitive(value)
                settings.append(
                    {"key": key, "value": value, "updatedAt": row["updated_at"]}
                )
            files.update(
                {
                    RESOURCES_PATH: {"schema": RESOURCES_SCHEMA, "items": resources},
                    FAVORITES_PATH: {"schema": FAVORITES_SCHEMA, "items": favorites},
                    SETTINGS_PATH: {"schema": SETTINGS_SCHEMA, "items": settings},
                }
            )
        return scope, files
    finally:
        connection.close()


def _build_manifest(scope: str, files: dict[str, object], project_id: str | None) -> dict:
    entries = []
    for path in sorted(files):
        raw = _json_bytes(files[path])
        if len(raw) > MAX_FILE_BYTES:
            raise BackupValidationError(f"{path} exceeds the per-file size limit")
        entries.append(
            {
                "path": path,
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    manifest = {
        "format": FORMAT_ID,
        "formatVersion": FORMAT_VERSION,
        "databaseSchemaVersion": db.SCHEMA_VERSION,
        "scope": scope,
        "createdAt": db.now_iso(),
        "files": entries,
        "exclusions": {
            "sensitiveSettings": True,
            "binaryAssets": True,
        },
    }
    if project_id is not None:
        manifest["projectId"] = project_id
    return manifest


def _atomic_write(path: Path, writer: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        writer(temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def export_backup(
    destination: Path | str,
    *,
    db_path: Path | str,
    project_id: str | None = None,
    archive_format: str | None = None,
) -> dict:
    """Export one project or the full logical database as JSON or ZIP.

    ``project_id=None`` selects the full logical database.  The output format is
    inferred from ``.zip`` unless ``archive_format`` is supplied.
    """

    destination_path = Path(destination)
    source_path = Path(db_path)
    if destination_path.resolve(strict=False) == source_path.resolve(strict=False):
        raise BackupValidationError("backup destination cannot replace the source database")
    selected_format = (archive_format or ("zip" if destination_path.suffix.casefold() == ".zip" else "json")).casefold()
    if selected_format not in {"json", "zip"}:
        raise BackupValidationError("archive_format must be json or zip")

    scope, files = _collect_files(source_path, project_id)
    manifest = _build_manifest(scope, files, project_id)
    _validate_package(manifest, files)

    if selected_format == "json":
        raw = _json_bytes({"manifest": manifest, "files": files})
        if len(raw) > MAX_ARCHIVE_BYTES:
            raise BackupValidationError("JSON backup exceeds the archive size limit")
        _atomic_write(destination_path, lambda temporary: temporary.write_bytes(raw))
    else:
        raw_files = {path: _json_bytes(value) for path, value in files.items()}

        def write_zip(temporary: Path) -> None:
            with zipfile.ZipFile(
                temporary,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as archive:
                archive.writestr(MANIFEST_PATH, _json_bytes(manifest))
                for path in sorted(raw_files):
                    archive.writestr(path, raw_files[path])

        _atomic_write(destination_path, write_zip)
        if destination_path.stat().st_size > MAX_ARCHIVE_BYTES:
            destination_path.unlink(missing_ok=True)
            raise BackupValidationError("ZIP backup exceeds the archive size limit")
    return copy.deepcopy(manifest)


def export_project(
    destination: Path | str,
    project_id: str,
    *,
    db_path: Path | str,
    archive_format: str | None = None,
) -> dict:
    return export_backup(
        destination,
        db_path=db_path,
        project_id=project_id,
        archive_format=archive_format,
    )


def export_database(
    destination: Path | str,
    *,
    db_path: Path | str,
    archive_format: str | None = None,
) -> dict:
    return export_backup(
        destination,
        db_path=db_path,
        project_id=None,
        archive_format=archive_format,
    )


def _safe_member_path(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise BackupValidationError("ZIP contains an unsafe path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BackupValidationError("ZIP contains an unsafe path")
    if len(path.parts) != 1 or ":" in name:
        raise BackupValidationError("ZIP contains an unsafe path")
    return path.as_posix()


def _read_zip(path: Path) -> tuple[dict, dict[str, object]]:
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise BackupValidationError("ZIP backup exceeds the archive size limit")
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            if not 1 <= len(infos) <= MAX_ARCHIVE_FILES:
                raise BackupValidationError("ZIP file count is outside the allowed range")
            names: set[str] = set()
            casefolded: set[str] = set()
            total_size = 0
            members: dict[str, bytes] = {}
            for info in infos:
                name = _safe_member_path(info.filename)
                folded = name.casefold()
                if name in names or folded in casefolded:
                    raise BackupValidationError("ZIP contains duplicate paths")
                names.add(name)
                casefolded.add(folded)
                if info.is_dir() or info.flag_bits & 0x1:
                    raise BackupValidationError("ZIP contains a directory or encrypted member")
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                if unix_mode and stat.S_IFMT(unix_mode) not in {0, stat.S_IFREG}:
                    raise BackupValidationError("ZIP contains a non-regular member")
                if info.file_size > MAX_FILE_BYTES:
                    raise BackupValidationError(f"{name} exceeds the per-file size limit")
                total_size += info.file_size
                if total_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
                    raise BackupValidationError("ZIP exceeds the total uncompressed size limit")
                if info.file_size and info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO:
                    raise BackupValidationError("ZIP member exceeds the compression-ratio limit")
                raw = archive.read(info)
                if len(raw) != info.file_size:
                    raise BackupValidationError("ZIP member size does not match its directory entry")
                members[name] = raw
    except BackupValidationError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise BackupValidationError("invalid ZIP backup") from error

    if MANIFEST_PATH not in members:
        raise BackupValidationError("ZIP backup is missing manifest.json")
    manifest = _strict_json_bytes(members.pop(MANIFEST_PATH), MANIFEST_PATH)
    files = {
        name: _strict_json_bytes(raw, name)
        for name, raw in members.items()
    }
    if not isinstance(manifest, dict):
        raise BackupValidationError("manifest must be an object")
    # ZIP hashes cover the exact member bytes, not a reparsed representation.
    _validate_manifest(manifest, set(files), {name: members[name] for name in files})
    return manifest, files


def _read_json_bundle(path: Path) -> tuple[dict, dict[str, object]]:
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise BackupValidationError("JSON backup exceeds the archive size limit")
    bundle = _strict_json_bytes(path.read_bytes(), "backup JSON")
    if not isinstance(bundle, dict) or set(bundle) != {"manifest", "files"}:
        raise BackupValidationError("JSON backup must contain only manifest and files")
    manifest = bundle["manifest"]
    files = bundle["files"]
    if not isinstance(manifest, dict) or not isinstance(files, dict):
        raise BackupValidationError("JSON backup manifest/files have the wrong type")
    if len(files) > MAX_ARCHIVE_FILES - 1:
        raise BackupValidationError("JSON backup contains too many logical files")
    normalized_files: dict[str, object] = {}
    folded: set[str] = set()
    total = 0
    for name, value in files.items():
        safe_name = _safe_member_path(name)
        if safe_name.casefold() in folded:
            raise BackupValidationError("JSON backup contains duplicate logical paths")
        folded.add(safe_name.casefold())
        raw = _json_bytes(value)
        total += len(raw)
        if len(raw) > MAX_FILE_BYTES or total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise BackupValidationError("JSON backup exceeds logical file size limits")
        normalized_files[safe_name] = value
    _validate_manifest(
        manifest,
        set(normalized_files),
        {name: _json_bytes(value) for name, value in normalized_files.items()},
    )
    return manifest, normalized_files


def _read_package(source: Path | str) -> tuple[dict, dict[str, object]]:
    path = Path(source)
    if not path.is_file():
        raise BackupValidationError("backup source is not a file")
    try:
        if zipfile.is_zipfile(path):
            return _read_zip(path)
        return _read_json_bundle(path)
    except OSError as error:
        raise BackupValidationError("backup source cannot be read") from error


def _exact_keys(value: object, required: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != required:
        raise BackupValidationError(f"{label} has an invalid schema")
    return value


def _string(value: object, label: str, max_length: int, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > max_length or (nonempty and not value):
        raise BackupValidationError(f"{label} must be a valid string")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise BackupValidationError(f"{label} contains invalid Unicode") from error
    return value


def _timestamp(value: object, label: str) -> str:
    result = _string(value, label, 64, nonempty=True)
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as error:
        raise BackupValidationError(f"{label} is not an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise BackupValidationError(f"{label} must include a timezone")
    return result


def _metadata(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise BackupValidationError(f"{label} must be an object")
    _validate_json_tree(value, label)
    _assert_no_sensitive_keys(value, label)
    try:
        sanitized = db.sanitize_metadata(value)
    except ValueError as error:
        raise BackupValidationError(f"{label} is invalid") from error
    if sanitized != value:
        raise BackupValidationError(f"{label} is not in canonical safe form")
    return value


def _blocks(value: object, label: str) -> list[dict]:
    try:
        normalized = db.normalize_blocks(value)
    except ValueError as error:
        raise BackupValidationError(f"{label} is invalid") from error
    if normalized != value:
        raise BackupValidationError(f"{label} is not in canonical form")
    return normalized


def _validate_project_items(value: object) -> list[dict]:
    root = _exact_keys(value, {"schema", "items"}, PROJECTS_PATH)
    if root["schema"] != PROJECTS_SCHEMA or not isinstance(root["items"], list):
        raise BackupValidationError("projects.json has an invalid schema")
    project_ids: set[str] = set()
    version_ids: set[str] = set()
    result: list[dict] = []
    project_keys = {"id", "name", "mode", "status", "metadata", "createdAt", "updatedAt", "versions"}
    version_keys = {"id", "projectId", "version", "source", "positiveEn", "positiveZh", "negativeEn", "negativeZh", "blocks", "metadata", "createdAt"}
    for index, item in enumerate(root["items"]):
        project = _exact_keys(item, project_keys, f"projects[{index}]")
        try:
            project_id = db.validate_id(project["id"], "project ID")
        except ValueError as error:
            raise BackupValidationError(f"projects[{index}].id is invalid") from error
        if project_id in project_ids:
            raise BackupValidationError("projects.json contains duplicate project IDs")
        project_ids.add(project_id)
        _string(project["name"], "project name", 200)
        if project["mode"] not in db.PROJECT_MODES:
            raise BackupValidationError("project mode is invalid")
        status = _string(project["status"], "project status", 32, nonempty=True)
        if not db.SAFE_STATUS_PATTERN.fullmatch(status):
            raise BackupValidationError("project status is invalid")
        _metadata(project["metadata"], "project metadata")
        _timestamp(project["createdAt"], "project createdAt")
        _timestamp(project["updatedAt"], "project updatedAt")
        if not isinstance(project["versions"], list):
            raise BackupValidationError("project versions must be an array")
        numbers: set[int] = set()
        for version_index, child in enumerate(project["versions"]):
            version = _exact_keys(child, version_keys, f"versions[{version_index}]")
            try:
                version_id = db.validate_id(version["id"], "version ID")
            except ValueError as error:
                raise BackupValidationError("version ID is invalid") from error
            if version_id in version_ids:
                raise BackupValidationError("projects.json contains duplicate version IDs")
            version_ids.add(version_id)
            if version["projectId"] != project_id:
                raise BackupValidationError("version projectId does not match its project")
            number = version["version"]
            if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 9_223_372_036_854_775_807:
                raise BackupValidationError("version number is invalid")
            if number in numbers:
                raise BackupValidationError("project contains duplicate version numbers")
            numbers.add(number)
            _string(version["source"], "version source", 64)
            _string(version["positiveEn"], "positiveEn", 200_000)
            _string(version["positiveZh"], "positiveZh", 200_000)
            _string(version["negativeEn"], "negativeEn", 100_000)
            _string(version["negativeZh"], "negativeZh", 100_000)
            _blocks(version["blocks"], "version blocks")
            _metadata(version["metadata"], "version metadata")
            _timestamp(version["createdAt"], "version createdAt")
        result.append(project)
    return result


def _validate_resource_items(value: object) -> list[dict]:
    root = _exact_keys(value, {"schema", "items"}, RESOURCES_PATH)
    if root["schema"] != RESOURCES_SCHEMA or not isinstance(root["items"], list):
        raise BackupValidationError("resources.json has an invalid schema")
    keys = {"id", "type", "name", "meta", "targetBlock", "en", "zh", "blocks", "metadata", "createdAt", "updatedAt"}
    ids: set[str] = set()
    for index, item in enumerate(root["items"]):
        record = _exact_keys(item, keys, f"resources[{index}]")
        try:
            identifier = db.validate_resource_id(record["id"], "resource ID")
        except ValueError as error:
            raise BackupValidationError("resource ID is invalid") from error
        if identifier in ids:
            raise BackupValidationError("resources.json contains duplicate IDs")
        ids.add(identifier)
        for key, limit in {"type": 32, "name": 500, "meta": 20_000, "targetBlock": 128, "en": 200_000, "zh": 200_000}.items():
            _string(record[key], f"resource {key}", limit)
        _blocks(record["blocks"], "resource blocks")
        _metadata(record["metadata"], "resource metadata")
        _timestamp(record["createdAt"], "resource createdAt")
        _timestamp(record["updatedAt"], "resource updatedAt")
    return root["items"]


def _validate_favorite_items(value: object) -> list[dict]:
    root = _exact_keys(value, {"schema", "items"}, FAVORITES_PATH)
    if root["schema"] != FAVORITES_SCHEMA or not isinstance(root["items"], list):
        raise BackupValidationError("favorites.json has an invalid schema")
    keys = {"storageId", "resourceId", "type", "name", "meta", "source", "sourceId", "targetBlock", "en", "zh", "thumbUrl", "hasImage", "blocks", "metadata", "createdAt", "updatedAt"}
    storage_ids: set[str] = set()
    resource_ids: set[str] = set()
    for index, item in enumerate(root["items"]):
        record = _exact_keys(item, keys, f"favorites[{index}]")
        try:
            storage_id = db.validate_resource_id(record["storageId"], "favorite storage ID")
            resource_id = db.validate_resource_id(record["resourceId"], "favorite resource ID")
        except ValueError as error:
            raise BackupValidationError("favorite ID is invalid") from error
        if storage_id in storage_ids or resource_id in resource_ids:
            raise BackupValidationError("favorites.json contains duplicate IDs")
        storage_ids.add(storage_id)
        resource_ids.add(resource_id)
        if record["type"] not in db.FAVORITE_TYPES:
            raise BackupValidationError("favorite type is invalid")
        for key, limit in {"name": 500, "meta": 20_000, "source": 200, "sourceId": 500, "targetBlock": 128, "en": 200_000, "zh": 200_000, "thumbUrl": 4_096}.items():
            _string(record[key], f"favorite {key}", limit)
        if not isinstance(record["hasImage"], bool):
            raise BackupValidationError("favorite hasImage must be a boolean")
        _blocks(record["blocks"], "favorite blocks")
        _metadata(record["metadata"], "favorite metadata")
        _timestamp(record["createdAt"], "favorite createdAt")
        _timestamp(record["updatedAt"], "favorite updatedAt")
    return root["items"]


def _validate_setting_items(value: object) -> list[dict]:
    root = _exact_keys(value, {"schema", "items"}, SETTINGS_PATH)
    if root["schema"] != SETTINGS_SCHEMA or not isinstance(root["items"], list):
        raise BackupValidationError("settings.json has an invalid schema")
    keys: set[str] = set()
    for index, item in enumerate(root["items"]):
        record = _exact_keys(item, {"key", "value", "updatedAt"}, f"settings[{index}]")
        key = _string(record["key"], "setting key", 128, nonempty=True)
        if key not in SAFE_SETTING_KEYS or db.is_sensitive_key(key) or key.startswith(db.INTERNAL_SETTINGS_PREFIX):
            raise BackupValidationError("settings.json contains a non-exportable setting")
        if key in keys:
            raise BackupValidationError("settings.json contains duplicate keys")
        keys.add(key)
        _validate_json_tree(record["value"], "setting value")
        _assert_no_sensitive_keys(record["value"], "setting value")
        _timestamp(record["updatedAt"], "setting updatedAt")
    return root["items"]


def _validate_manifest(
    manifest: dict,
    actual_names: set[str],
    raw_files: dict[str, bytes],
) -> None:
    required = {"format", "formatVersion", "databaseSchemaVersion", "scope", "createdAt", "files", "exclusions"}
    allowed = required | {"projectId"}
    if set(manifest) - allowed or not required.issubset(manifest):
        raise BackupValidationError("manifest has an invalid schema")
    if manifest["format"] != FORMAT_ID or manifest["formatVersion"] != FORMAT_VERSION:
        raise BackupValidationError("backup format/version is unsupported")
    if manifest["databaseSchemaVersion"] != db.SCHEMA_VERSION:
        raise BackupValidationError("backup database schema version is unsupported")
    if manifest["scope"] not in {"project", "full"}:
        raise BackupValidationError("manifest scope is invalid")
    if manifest["scope"] == "project":
        try:
            db.validate_resource_id(manifest.get("projectId"), "project ID")
        except ValueError as error:
            raise BackupValidationError("project backup is missing a valid projectId") from error
    elif "projectId" in manifest:
        raise BackupValidationError("full backup cannot declare projectId")
    _timestamp(manifest["createdAt"], "manifest createdAt")
    if manifest["exclusions"] != {"sensitiveSettings": True, "binaryAssets": True}:
        raise BackupValidationError("manifest exclusions are invalid")
    entries = manifest["files"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_ARCHIVE_FILES - 1:
        raise BackupValidationError("manifest files list is invalid")
    listed: set[str] = set()
    folded: set[str] = set()
    for index, item in enumerate(entries):
        entry = _exact_keys(item, {"path", "size", "sha256"}, f"manifest.files[{index}]")
        path = _safe_member_path(entry["path"])
        if path in listed or path.casefold() in folded:
            raise BackupValidationError("manifest contains duplicate file paths")
        listed.add(path)
        folded.add(path.casefold())
        size = entry["size"]
        digest = entry["sha256"]
        if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= MAX_FILE_BYTES:
            raise BackupValidationError("manifest file size is invalid")
        if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
            raise BackupValidationError("manifest SHA-256 is invalid")
        raw = raw_files.get(path)
        if raw is None or len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
            raise BackupValidationError(f"hash or size mismatch for {path}")
    if listed != actual_names:
        raise BackupValidationError("manifest file list does not match archive members")


def _validate_package(manifest: dict, files: dict[str, object]) -> dict:
    _validate_manifest(
        manifest,
        set(files),
        {path: _json_bytes(value) for path, value in files.items()},
    )
    expected = {PROJECTS_PATH}
    if manifest["scope"] == "full":
        expected |= {RESOURCES_PATH, FAVORITES_PATH, SETTINGS_PATH}
    if set(files) != expected:
        raise BackupValidationError("logical files do not match backup scope")
    projects = _validate_project_items(files[PROJECTS_PATH])
    if manifest["scope"] == "project":
        if len(projects) != 1 or projects[0]["id"] != manifest["projectId"]:
            raise BackupValidationError("project backup contents do not match manifest projectId")
        resources: list[dict] = []
        favorites: list[dict] = []
        settings: list[dict] = []
    else:
        resources = _validate_resource_items(files[RESOURCES_PATH])
        favorites = _validate_favorite_items(files[FAVORITES_PATH])
        settings = _validate_setting_items(files[SETTINGS_PATH])
    return {
        "manifest": manifest,
        "projects": projects,
        "resources": resources,
        "favorites": favorites,
        "settings": settings,
    }


def inspect_backup(source: Path | str) -> dict:
    """Validate an archive without touching any database."""

    manifest, files = _read_package(source)
    data = _validate_package(manifest, files)
    return {
        "manifest": copy.deepcopy(manifest),
        "counts": {
            "projects": len(data["projects"]),
            "versions": sum(len(item["versions"]) for item in data["projects"]),
            "resources": len(data["resources"]),
            "favorites": len(data["favorites"]),
            "settings": len(data["settings"]),
        },
    }


def _renamed_identifier(original: str, occupied: set[str], max_length: int) -> str:
    for number in range(1, 1_000_001):
        suffix = "-imported" if number == 1 else f"-imported-{number}"
        candidate = original[: max_length - len(suffix)] + suffix
        if candidate not in occupied:
            occupied.add(candidate)
            return candidate
    raise BackupConflictError([f"cannot allocate a replacement ID for {original}"])


def _existing_values(connection: sqlite3.Connection, query: str) -> set[str]:
    return {str(row[0]) for row in connection.execute(query).fetchall()}


def _prepare_restore(connection: sqlite3.Connection, data: dict, conflict: str) -> tuple[dict, dict]:
    prepared = copy.deepcopy(data)
    existing_projects = _existing_values(connection, "SELECT id FROM projects")
    existing_versions = _existing_values(connection, "SELECT id FROM prompt_versions")
    existing_resources = _existing_values(connection, "SELECT id FROM resources")
    existing_favorite_storage = _existing_values(connection, "SELECT id FROM favorites")
    existing_favorite_resources = _existing_values(connection, "SELECT resource_id FROM favorites")
    existing_settings = _existing_values(connection, "SELECT key FROM settings")

    imported_project_ids = {item["id"] for item in prepared["projects"]}
    imported_version_ids = {version["id"] for item in prepared["projects"] for version in item["versions"]}
    imported_resource_ids = {item["id"] for item in prepared["resources"]}
    imported_favorite_storage = {item["storageId"] for item in prepared["favorites"]}
    imported_favorite_resources = {item["resourceId"] for item in prepared["favorites"]}
    imported_settings = {item["key"] for item in prepared["settings"]}

    conflicts = [f"project:{value}" for value in sorted(imported_project_ids & existing_projects)]
    conflicts += [f"version:{value}" for value in sorted(imported_version_ids & existing_versions)]
    conflicts += [f"resource:{value}" for value in sorted(imported_resource_ids & existing_resources)]
    conflicts += [f"favorite:{value}" for value in sorted(imported_favorite_storage & existing_favorite_storage)]
    conflicts += [f"favorite-resource:{value}" for value in sorted(imported_favorite_resources & existing_favorite_resources)]
    conflicts += [f"setting:{value}" for value in sorted(imported_settings & existing_settings)]
    if conflict == "reject" and conflicts:
        raise BackupConflictError(conflicts)

    report: dict[str, object] = {
        "renamedProjects": [],
        "renamedVersions": [],
        "renamedResources": [],
        "renamedFavorites": [],
        "skippedSettings": [],
    }
    if conflict == "reject":
        return prepared, report

    project_map: dict[str, str] = {}
    occupied = existing_projects | imported_project_ids
    for project in prepared["projects"]:
        old = project["id"]
        new = old
        if old in existing_projects:
            new = _renamed_identifier(old, occupied, 128)
            report["renamedProjects"].append({"from": old, "to": new})
        project_map[old] = new
        project["id"] = new
        for version in project["versions"]:
            version["projectId"] = new

    occupied_versions = existing_versions | imported_version_ids
    for project in prepared["projects"]:
        for version in project["versions"]:
            old = version["id"]
            if old in existing_versions:
                new = _renamed_identifier(old, occupied_versions, 128)
                version["id"] = new
                report["renamedVersions"].append({"from": old, "to": new})

    resource_map: dict[str, str] = {}
    occupied_resources = existing_resources | imported_resource_ids
    for resource in prepared["resources"]:
        old = resource["id"]
        new = old
        if old in existing_resources:
            new = _renamed_identifier(old, occupied_resources, 1_024)
            resource["id"] = new
            report["renamedResources"].append({"from": old, "to": new})
        resource_map[old] = new

    occupied_favorite_resources = existing_favorite_resources | imported_favorite_resources
    occupied_favorite_storage = existing_favorite_storage | imported_favorite_storage
    for favorite in prepared["favorites"]:
        old_resource = favorite["resourceId"]
        mapped = resource_map.get(old_resource, old_resource)
        if mapped in existing_favorite_resources:
            mapped = _renamed_identifier(mapped, occupied_favorite_resources, 1_024)
        old_storage = favorite["storageId"]
        storage = old_storage
        if mapped != old_resource:
            storage = db.favorite_route_id(mapped)
        if storage in existing_favorite_storage:
            storage = _renamed_identifier(storage, occupied_favorite_storage, 1_024)
        favorite["resourceId"] = mapped
        favorite["storageId"] = storage
        if mapped != old_resource or storage != old_storage:
            report["renamedFavorites"].append(
                {"from": old_resource, "to": mapped, "storageId": storage}
            )

    retained_settings = []
    for setting in prepared["settings"]:
        if setting["key"] in existing_settings:
            report["skippedSettings"].append(setting["key"])
        else:
            retained_settings.append(setting)
    prepared["settings"] = retained_settings
    return prepared, report


def _insert_restore(connection: sqlite3.Connection, data: dict) -> None:
    for project in data["projects"]:
        connection.execute(
            """
            INSERT INTO projects (
                id, name, mode, status, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project["id"], project["name"], project["mode"], project["status"],
                db.json_dumps(project["metadata"]), project["createdAt"], project["updatedAt"],
            ),
        )
        for version in project["versions"]:
            connection.execute(
                """
                INSERT INTO prompt_versions (
                    id, project_id, version, source,
                    positive_en, positive_zh, negative_en, negative_zh,
                    blocks_json, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version["id"], version["projectId"], version["version"], version["source"],
                    version["positiveEn"], version["positiveZh"], version["negativeEn"], version["negativeZh"],
                    db.json_dumps(version["blocks"]), db.json_dumps(version["metadata"]), version["createdAt"],
                ),
            )
    for resource in data["resources"]:
        connection.execute(
            """
            INSERT INTO resources (
                id, type, name, meta, target_block, en, zh,
                blocks_json, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resource["id"], resource["type"], resource["name"], resource["meta"],
                resource["targetBlock"], resource["en"], resource["zh"],
                db.json_dumps(resource["blocks"]), db.json_dumps(resource["metadata"]),
                resource["createdAt"], resource["updatedAt"],
            ),
        )
    for favorite in data["favorites"]:
        connection.execute(
            """
            INSERT INTO favorites (
                id, resource_id, type, name, meta, source, source_id,
                target_block, en, zh, thumb_url, has_image,
                blocks_json, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                favorite["storageId"], favorite["resourceId"], favorite["type"], favorite["name"],
                favorite["meta"], favorite["source"], favorite["sourceId"], favorite["targetBlock"],
                favorite["en"], favorite["zh"], favorite["thumbUrl"], 1 if favorite["hasImage"] else 0,
                db.json_dumps(favorite["blocks"]), db.json_dumps(favorite["metadata"]),
                favorite["createdAt"], favorite["updatedAt"],
            ),
        )
    for setting in data["settings"]:
        connection.execute(
            "INSERT INTO settings (key, value_json, updated_at) VALUES (?, ?, ?)",
            (setting["key"], db.json_dumps(setting["value"]), setting["updatedAt"]),
        )


def _restore_into(path: Path, data: dict, conflict: str) -> tuple[dict, dict]:
    connection = db.connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        prepared, report = _prepare_restore(connection, data, conflict)
        _insert_restore(connection, prepared)
        integrity = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity:
            raise BackupValidationError("restored data violates database foreign keys")
        connection.commit()
        return prepared, report
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def restore_backup(
    source: Path | str,
    target_db_path: Path | str,
    *,
    conflict: str = "reject",
) -> dict:
    """Validate and restore into an explicit staging/current database.

    Validation is completed before the target is opened or created.  Existing
    targets are changed in one transaction.  Missing targets are built in a
    sibling temporary file and atomically moved into place only after success.
    The configured default database path is always refused.
    """

    if conflict not in {"reject", "rename"}:
        raise BackupValidationError("conflict must be reject or rename")
    manifest, files = _read_package(source)
    data = _validate_package(manifest, files)

    target = Path(target_db_path)
    if target.resolve(strict=False) == Path(db.DEFAULT_DB_PATH).resolve(strict=False):
        raise BackupValidationError("restore to the default Prompt Studio database is forbidden")
    if target.exists() and not target.is_file():
        raise BackupValidationError("restore target is not a file")

    created_target = not target.exists()
    if created_target:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.restore-", suffix=".db", dir=target.parent
        )
        os.close(descriptor)
        staging = Path(temporary_name)
        try:
            db.init_db(staging)
            prepared, report = _restore_into(staging, data, conflict)
            os.replace(staging, target)
        finally:
            staging.unlink(missing_ok=True)
    else:
        status = db.get_database_status(target)
        if not status.get("initialized") or not status.get("compatible"):
            raise BackupValidationError("restore target is not a current Prompt Studio database")
        prepared, report = _restore_into(target, data, conflict)

    return {
        "manifest": copy.deepcopy(manifest),
        "target": str(target),
        "createdTarget": created_target,
        "conflictPolicy": conflict,
        "counts": {
            "projects": len(prepared["projects"]),
            "versions": sum(len(item["versions"]) for item in prepared["projects"]),
            "resources": len(prepared["resources"]),
            "favorites": len(prepared["favorites"]),
            "settings": len(prepared["settings"]),
        },
        **report,
    }


__all__ = [
    "BackupConflictError",
    "BackupError",
    "BackupValidationError",
    "FORMAT_ID",
    "FORMAT_VERSION",
    "export_backup",
    "export_database",
    "export_project",
    "inspect_backup",
    "restore_backup",
]
