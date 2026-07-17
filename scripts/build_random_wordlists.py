"""Validate and compile the frozen random-wordlist catalog.

The generated catalog is deliberately marked as not ready for runtime use.  It
is a deterministic, reviewable build artifact for the later seeded sampler.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = ROOT / "词库原稿"
DEFAULT_OUTPUT = (
    ROOT / "prototype" / "data" / "random-wordlists" / "v1.json"
)

SCHEMA_VERSION = 1
SAMPLER_VERSION = "sha256-counter-v1"
MAPPING_VERSION = "ten-block-v1"
PROFILE = "adult-character-v1"
VERSION_PREFIX = "v1"
EXPECTED_TOTAL = 471
MAX_SOURCE_FILE_BYTES = 128 * 1024
MAX_ENTRY_UTF8_BYTES = 512
FORBIDDEN_ITEM_SEPARATORS = (",", "，", ";", "；", "\r", "\n")
SELF_REFERENTIAL_FIELDS = {"version", "contentSha256"}
FORBIDDEN_UNICODE_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}


def fixed_draw(count: int) -> dict[str, Any]:
    return {
        "kind": "fixed",
        "count": count,
        "withoutReplacement": True,
    }


CATEGORY_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "theme_mood",
        "sourceFile": "theme_mood.txt",
        "expectedCount": 50,
        "drawRule": fixed_draw(1),
        "primaryBlock": "scene",
        "allowedBlocks": [
            "subject",
            "appearance",
            "pose",
            "scene",
            "composition",
            "lighting",
            "effects",
        ],
    },
    {
        "id": "scene_environment",
        "sourceFile": "scene_environment.txt",
        "expectedCount": 70,
        "drawRule": fixed_draw(1),
        "primaryBlock": "scene",
        "allowedBlocks": ["scene"],
    },
    {
        "id": "pose_action",
        "sourceFile": "pose_action.txt",
        "expectedCount": 60,
        "drawRule": fixed_draw(1),
        "primaryBlock": "pose",
        "allowedBlocks": ["pose"],
    },
    {
        "id": "clothing_outfit",
        "sourceFile": "clothing_outfit.txt",
        "expectedCount": 60,
        "drawRule": fixed_draw(1),
        "primaryBlock": "appearance",
        "allowedBlocks": ["appearance"],
    },
    {
        "id": "composition_camera",
        "sourceFile": "composition_camera.txt",
        "expectedCount": 45,
        "drawRule": fixed_draw(1),
        "primaryBlock": "composition",
        "allowedBlocks": ["composition"],
    },
    {
        "id": "lighting_color",
        "sourceFile": "lighting_color.txt",
        "expectedCount": 50,
        "drawRule": fixed_draw(1),
        "primaryBlock": "lighting",
        "allowedBlocks": ["lighting"],
    },
    {
        "id": "effects_props",
        "sourceFile": "effects_props.txt",
        "expectedCount": 56,
        "drawRule": {
            "kind": "uniform-count",
            "minimum": 1,
            "maximum": 2,
            "withoutReplacement": True,
        },
        "primaryBlock": "effects",
        "allowedBlocks": ["effects", "lighting"],
    },
    {
        "id": "weather_time",
        "sourceFile": "weather_time.txt",
        "expectedCount": 30,
        "drawRule": {
            "kind": "bernoulli",
            "probability": 0.5,
            "successCount": 1,
            "failureCount": 0,
            "withoutReplacement": True,
        },
        "primaryBlock": "scene",
        "allowedBlocks": ["scene", "lighting", "effects"],
    },
    {
        "id": "appearance_traits",
        "sourceFile": "appearance_traits.txt",
        "expectedCount": 50,
        "drawRule": {
            "kind": "uniform-count",
            "minimum": 0,
            "maximum": 2,
            "withoutReplacement": True,
        },
        "primaryBlock": "appearance",
        "allowedBlocks": ["appearance", "pose"],
    },
)


class CatalogValidationError(ValueError):
    """Raised when source wordlists cannot produce a trustworthy catalog."""


def normalize_entry_text(value: str) -> str:
    """Return the catalog identity form used for duplicate checks and IDs."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def stable_entry_id(category_id: str, text: str) -> str:
    normalized = normalize_entry_text(text)
    identity = f"{category_id}\0{normalized}".encode("utf-8")
    return f"{category_id}:{hashlib.sha256(identity).hexdigest()}"


def _read_strict_utf8(path: Path) -> str:
    if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
        raise CatalogValidationError(
            f"{path.name} exceeds the {MAX_SOURCE_FILE_BYTES}-byte source limit"
        )
    try:
        decoded = path.read_bytes().decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise CatalogValidationError(
            f"{path.name} is not strict UTF-8: {error}"
        ) from error
    if decoded.startswith("\ufeff"):
        raise CatalogValidationError(f"{path.name} must not contain a UTF-8 BOM")
    for index, character in enumerate(decoded):
        if (
            unicodedata.category(character) in FORBIDDEN_UNICODE_CATEGORIES
            and character not in {"\r", "\n"}
        ):
            source_line = decoded.count("\n", 0, index) + 1
            raise CatalogValidationError(
                f"{path.name}:{source_line} contains a forbidden Unicode character"
            )
    return decoded


def _validate_source_files(source_dir: Path) -> None:
    expected = {spec["sourceFile"] for spec in CATEGORY_SPECS}
    if not source_dir.is_dir():
        raise CatalogValidationError(f"wordlist source directory is missing: {source_dir}")
    actual = {path.name for path in source_dir.glob("*.txt") if path.is_file()}
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected: {', '.join(unexpected)}")
        raise CatalogValidationError(
            "wordlist files do not match the fixed nine-file manifest ("
            + "; ".join(details)
            + ")"
        )


def _load_category(
    source_dir: Path,
    spec: dict[str, Any],
    globally_seen: dict[str, tuple[str, int]],
) -> dict[str, Any]:
    source_file = str(spec["sourceFile"])
    path = source_dir / source_file
    lines = _read_strict_utf8(path).splitlines()
    expected_count = int(spec["expectedCount"])
    if len(lines) != expected_count:
        raise CatalogValidationError(
            f"{source_file} must contain exactly {expected_count} lines, "
            f"found {len(lines)}"
        )

    entries = []
    for source_line, text in enumerate(lines, start=1):
        location = f"{source_file}:{source_line}"
        if not text:
            raise CatalogValidationError(f"{location} must not be empty")
        if text != text.strip():
            raise CatalogValidationError(
                f"{location} has leading or trailing whitespace"
            )
        if len(text.encode("utf-8")) > MAX_ENTRY_UTF8_BYTES:
            raise CatalogValidationError(
                f"{location} exceeds the {MAX_ENTRY_UTF8_BYTES}-byte entry limit"
            )
        if any(ord(character) < 0x20 or ord(character) > 0x7E for character in text):
            raise CatalogValidationError(
                f"{location} must use printable ASCII for the v1 prompt profile"
            )
        if any(
            unicodedata.category(character) in FORBIDDEN_UNICODE_CATEGORIES
            for character in text
        ):
            raise CatalogValidationError(
                f"{location} contains a forbidden Unicode character"
            )
        separator = next(
            (item for item in FORBIDDEN_ITEM_SEPARATORS if item in text),
            None,
        )
        if separator is not None:
            raise CatalogValidationError(
                f"{location} contains a forbidden prompt separator"
            )
        normalized = normalize_entry_text(text)
        if not normalized:
            raise CatalogValidationError(
                f"{location} is empty after normalization"
            )
        previous = globally_seen.get(normalized)
        if previous is not None:
            previous_file, previous_line = previous
            raise CatalogValidationError(
                "normalized duplicate across wordlists: "
                f"{previous_file}:{previous_line} and {location}"
            )
        globally_seen[normalized] = (source_file, source_line)
        entries.append(
            {
                "entryId": stable_entry_id(str(spec["id"]), text),
                "text": text,
                "sourceFile": source_file,
                "sourceLine": source_line,
            }
        )

    return {
        "id": spec["id"],
        "sourceFile": source_file,
        "expectedCount": expected_count,
        "drawRule": spec["drawRule"],
        "primaryBlock": spec["primaryBlock"],
        "allowedBlocks": spec["allowedBlocks"],
        "entries": entries,
    }


def _hash_scope(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in catalog.items()
        if key not in SELF_REFERENTIAL_FIELDS
    }


def catalog_content_sha256(catalog: dict[str, Any]) -> str:
    """Hash canonical catalog content without the hash and version fields."""

    canonical = json.dumps(
        _hash_scope(catalog),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_catalog(source_dir: Path = DEFAULT_SOURCE_DIR) -> dict[str, Any]:
    source_dir = Path(source_dir)
    _validate_source_files(source_dir)
    globally_seen: dict[str, tuple[str, int]] = {}
    categories = [
        _load_category(source_dir, spec, globally_seen)
        for spec in CATEGORY_SPECS
    ]
    entry_count = sum(len(category["entries"]) for category in categories)
    if entry_count != EXPECTED_TOTAL:
        raise CatalogValidationError(
            f"catalog must contain exactly {EXPECTED_TOTAL} entries, found {entry_count}"
        )

    content: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "samplerVersion": SAMPLER_VERSION,
        "mappingVersion": MAPPING_VERSION,
        "profile": PROFILE,
        "runtimeReady": False,
        "semanticReviewRequired": True,
        "contentHashAlgorithm": "sha256-canonical-json-without-version-fields-v1",
        "normalizationUnicodeVersion": unicodedata.unidata_version,
        "sourceDirectory": "词库原稿",
        "categoryCount": len(categories),
        "entryCount": entry_count,
        "categories": categories,
    }
    digest = catalog_content_sha256(content)
    return {
        **content,
        "version": f"{VERSION_PREFIX}-{digest[:16]}",
        "contentSha256": digest,
    }


def render_catalog(catalog: dict[str, Any]) -> str:
    return json.dumps(
        catalog,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"


def expected_catalog_bytes(source_dir: Path = DEFAULT_SOURCE_DIR) -> bytes:
    return render_catalog(build_catalog(source_dir)).encode("utf-8")


def _normalize_checkout_newlines(value: bytes) -> bytes:
    """Ignore only Git's Windows CRLF conversion during freshness checks."""

    return value.replace(b"\r\n", b"\n")


def generate_catalog(
    source_dir: Path = DEFAULT_SOURCE_DIR,
    output: Path = DEFAULT_OUTPUT,
    *,
    check: bool = False,
) -> Path:
    output = Path(output)
    expected = expected_catalog_bytes(Path(source_dir))
    if check:
        try:
            actual = output.read_bytes()
        except FileNotFoundError as error:
            raise CatalogValidationError(
                f"generated random-wordlist catalog is missing: {output}"
            ) from error
        if _normalize_checkout_newlines(actual) != expected:
            raise CatalogValidationError(
                f"generated random-wordlist catalog is stale: {output}"
            )
        return output

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(expected)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and compile the frozen random-wordlist catalog."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail without writing when the generated catalog is stale",
    )
    args = parser.parse_args()
    try:
        path = generate_catalog(
            args.source_dir,
            args.output,
            check=args.check,
        )
    except (CatalogValidationError, OSError) as error:
        raise SystemExit(f"random-wordlist build failed: {error}") from error
    if not args.check:
        print(path.resolve())


if __name__ == "__main__":
    main()
