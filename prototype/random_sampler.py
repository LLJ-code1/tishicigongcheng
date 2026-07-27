"""Deterministic, model-free random-wordlist planning.

The checked-in v1 catalog is deliberately *not* release-ready.  Callers must
opt into ``experimental=True`` while ``runtimeReady`` is false or
``semanticReviewRequired`` is true.  This module never calls a text model and
never accepts client-provided entry text, mappings, conflict rules, or paths.

``sha256-counter-v1`` frames each category stream as::

    b"anima-random-plan\0sha256-counter-v1\0"
    + seed_bytes + b"\0" + category_id.encode("ascii") + b"\0"
    + counter.to_bytes(8, "big")

Counters start at zero independently for every category.  A SHA-256 digest is
interpreted as an unsigned 256-bit integer.  Bounded draws use rejection
sampling, never ``value % bound`` alone, so modulo bias is eliminated.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import unicodedata
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Sequence


DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parent / "data" / "random-wordlists" / "v1.json"
)
CATALOG_ARCHIVE_DIRECTORY = DEFAULT_CATALOG_PATH.parent / "catalogs"
_CATALOG_VERSION_RE = re.compile(r"^v1-[0-9a-f]{16}$")

SCHEMA_VERSION = 1
SAMPLER_VERSION = "sha256-counter-v1"
MAPPING_VERSION = "thirteen-block-v1"
LEGACY_MAPPING_VERSIONS = frozenset({"ten-block-v1"})
PROFILE = "adult-character-v1"
CONTENT_HASH_ALGORITHM = "sha256-canonical-json-without-version-fields-v1"
VERSION_PREFIX = "v1"
MAX_CATALOG_BYTES = 4 * 1024 * 1024
MAX_REQUEST_ENTRY_IDS = 128
MAX_CATEGORIES = 64
MAX_CATEGORY_ENTRIES = 10_000
MAX_SAMPLING_ATTEMPTS_FACTOR = 64
MIN_SAMPLING_ATTEMPTS = 1_024
MAX_PROBABILITY_DENOMINATOR = 1_000_000
_UINT256_RANGE = 1 << 256
_MAX_COUNTER = (1 << 64) - 1
_STREAM_PREFIX = b"anima-random-plan\0sha256-counter-v1\0"
_SEED_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_CATEGORY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_BLOCK_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RULE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
_ENTRY_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SELF_REFERENTIAL_FIELDS = frozenset({"version", "contentSha256"})
_REQUEST_REQUIRED_FIELDS = frozenset(
    {
        "librarySeed",
        "catalogVersion",
        "catalogContentSha256",
        "samplerVersion",
        "mappingVersion",
        "profile",
    }
)
_REQUEST_OPTIONAL_FIELDS = frozenset(
    {"lockedEntryIds", "rerollEntryIds", "drawCounts", "scopeCategoryIds"}
)


class RandomSamplerError(ValueError):
    """Base class for safe, expected sampler failures."""

    code = "random_sampler_error"


class CatalogValidationError(RandomSamplerError):
    code = "invalid_random_catalog"


class CatalogBoundaryError(RandomSamplerError):
    code = "random_catalog_not_release_ready"


class PlanRequestError(RandomSamplerError):
    code = "invalid_random_plan_request"


class LockedConflictError(RandomSamplerError):
    """Raised when two user-locked items have a hard conflict."""

    code = "locked_random_plan_conflict"

    def __init__(
        self,
        message: str,
        *,
        conflicts: Sequence[Mapping[str, Any]],
        locks: Sequence[Mapping[str, Any]],
    ) -> None:
        super().__init__(message)
        self.conflicts = tuple(dict(item) for item in conflicts)
        self.locks = tuple(dict(item) for item in locks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "conflicts": [dict(item) for item in self.conflicts],
            "locks": [dict(item) for item in self.locks],
        }


class SamplingExhaustedError(RandomSamplerError):
    code = "random_sampling_exhausted"

    def __init__(self, message: str, *, trace: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.trace = dict(trace)


@dataclass(frozen=True)
class DrawRule:
    kind: str
    without_replacement: bool
    count: int | None = None
    minimum: int | None = None
    maximum: int | None = None
    probability_numerator: int | None = None
    probability_denominator: int | None = None
    success_count: int | None = None
    failure_count: int | None = None

    @property
    def allowed_counts(self) -> tuple[int, ...]:
        if self.kind == "fixed":
            return (int(self.count),)
        if self.kind == "uniform-count":
            return tuple(range(int(self.minimum), int(self.maximum) + 1))
        return tuple(sorted({int(self.failure_count), int(self.success_count)}))

    @property
    def maximum_count(self) -> int:
        return max(self.allowed_counts)


@dataclass(frozen=True)
class CatalogEntry:
    entry_id: str
    text: str
    category_id: str
    source_file: str
    source_line: int
    primary_block: str
    allowed_blocks: tuple[str, ...]
    catalog_order: int
    category_order: int


@dataclass(frozen=True)
class CatalogCategory:
    category_id: str
    source_file: str
    draw_rule: DrawRule
    primary_block: str
    allowed_blocks: tuple[str, ...]
    entries: tuple[CatalogEntry, ...]
    order: int


@dataclass(frozen=True)
class ValidatedCatalog:
    schema_version: int
    sampler_version: str
    mapping_version: str
    profile: str
    version: str
    content_sha256: str
    runtime_ready: bool
    semantic_review_required: bool
    categories: tuple[CatalogCategory, ...]
    entries_by_id: Mapping[str, CatalogEntry]
    categories_by_id: Mapping[str, CatalogCategory]
    experimental_mode: bool


@dataclass(frozen=True)
class ConflictRule:
    """Server-owned pairwise compatibility rule.

    Conflict rules are deliberately not accepted in ``resolve_random_plan``'s
    request mapping.  The HTTP layer should load reviewed rules and pass them
    through this separate parameter.
    """

    rule_id: str
    left_entry_id: str
    right_entry_id: str
    severity: str
    reason: str


@dataclass(frozen=True)
class _ValidatedRequest:
    library_seed: str
    locked_entry_ids: tuple[str, ...]
    reroll_entry_ids: tuple[str, ...]
    draw_counts: Mapping[str, int]
    scope_category_ids: tuple[str, ...]


def normalize_library_seed(value: Any) -> str:
    """Validate a 128-bit hexadecimal Seed and return lowercase form."""

    if not isinstance(value, str) or _SEED_RE.fullmatch(value) is None:
        raise PlanRequestError(
            "librarySeed must be exactly 32 hexadecimal characters (128 bits)"
        )
    return value.lower()


def generate_library_seed(
    token_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> str:
    """Generate a lowercase 128-bit Seed.

    ``token_bytes`` is injectable only for deterministic unit tests; API
    request data must never select it.
    """

    value = token_bytes(16)
    if not isinstance(value, bytes) or len(value) != 16:
        raise RuntimeError("the Seed entropy source must return exactly 16 bytes")
    return value.hex()


def _canonical_catalog_hash(catalog: Mapping[str, Any]) -> str:
    scoped = {
        key: value
        for key, value in catalog.items()
        if key not in _SELF_REFERENTIAL_FIELDS
    }
    try:
        encoded = json.dumps(
            scoped,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise CatalogValidationError(
            f"catalog cannot be encoded as canonical JSON: {error}"
        ) from error
    return hashlib.sha256(encoded).hexdigest()


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CatalogValidationError(f"catalog contains duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise CatalogValidationError(f"catalog contains non-finite JSON number: {value}")


def load_catalog(
    *, experimental: bool = False, catalog_version: str | None = None
) -> ValidatedCatalog:
    """Load the server-owned catalog path and validate every identity field.

    There is intentionally no request-controlled path parameter.  A future
    immutable history loader should map an allowlisted version to a path on the
    server, then call ``validate_catalog`` on the parsed object.
    """

    path = DEFAULT_CATALOG_PATH
    if catalog_version is not None:
        if not isinstance(catalog_version, str) or _CATALOG_VERSION_RE.fullmatch(catalog_version) is None:
            raise CatalogValidationError("catalog version is invalid")
        current = load_catalog(experimental=experimental) if catalog_version else None
        if current is None or catalog_version != current.version:
            path = CATALOG_ARCHIVE_DIRECTORY / f"{catalog_version}.json"
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise CatalogValidationError(
            f"server catalog is unavailable: {path.name}"
        ) from error
    if len(payload) > MAX_CATALOG_BYTES:
        raise CatalogValidationError(
            f"catalog exceeds the {MAX_CATALOG_BYTES}-byte limit"
        )
    if payload.startswith(b"\xef\xbb\xbf"):
        raise CatalogValidationError("catalog must not contain a UTF-8 BOM")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise CatalogValidationError("catalog is not strict UTF-8") from error
    try:
        raw = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except CatalogValidationError:
        raise
    except json.JSONDecodeError as error:
        raise CatalogValidationError(f"catalog is not valid JSON: {error}") from error
    catalog = validate_catalog(raw, experimental=experimental)
    if catalog_version is not None and catalog.version != catalog_version:
        raise CatalogValidationError("catalog archive identity does not match requested version")
    return catalog


def _require_exact_type(value: Any, expected: type, field: str) -> Any:
    if type(value) is not expected:
        raise CatalogValidationError(f"{field} must be {expected.__name__}")
    return value


def _require_nonnegative_int(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise CatalogValidationError(f"{field} must be a non-negative integer")
    return value


def _safe_source_file(value: Any, field: str) -> str:
    _require_exact_type(value, str, field)
    if (
        not value
        or len(value) > 128
        or value in {".", ".."}
        or "\\" in value
        or not value.endswith(".txt")
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise CatalogValidationError(f"{field} must be a safe relative .txt path")
    return value


def _parse_draw_rule(raw: Any, field: str, entry_count: int) -> DrawRule:
    if not isinstance(raw, dict):
        raise CatalogValidationError(f"{field} must be an object")
    kind = raw.get("kind")
    if kind not in {"fixed", "uniform-count", "bernoulli"}:
        raise CatalogValidationError(f"{field}.kind is unsupported")
    if raw.get("withoutReplacement") is not True:
        raise CatalogValidationError(
            f"{field}.withoutReplacement must be true for sampler v1"
        )
    if kind == "fixed":
        expected = {"kind", "count", "withoutReplacement"}
        if set(raw) != expected:
            raise CatalogValidationError(f"{field} has unexpected fixed-rule fields")
        count = _require_nonnegative_int(raw["count"], f"{field}.count")
        if count > entry_count:
            raise CatalogValidationError(f"{field}.count exceeds category size")
        return DrawRule(kind=kind, count=count, without_replacement=True)

    if kind == "uniform-count":
        expected = {"kind", "minimum", "maximum", "withoutReplacement"}
        if set(raw) != expected:
            raise CatalogValidationError(f"{field} has unexpected uniform-rule fields")
        minimum = _require_nonnegative_int(raw["minimum"], f"{field}.minimum")
        maximum = _require_nonnegative_int(raw["maximum"], f"{field}.maximum")
        if minimum > maximum or maximum > entry_count:
            raise CatalogValidationError(f"{field} has an invalid count range")
        return DrawRule(
            kind=kind,
            minimum=minimum,
            maximum=maximum,
            without_replacement=True,
        )

    expected = {
        "kind",
        "probability",
        "successCount",
        "failureCount",
        "withoutReplacement",
    }
    if set(raw) != expected:
        raise CatalogValidationError(f"{field} has unexpected Bernoulli fields")
    probability = raw["probability"]
    if type(probability) not in {int, float} or not math.isfinite(probability):
        raise CatalogValidationError(f"{field}.probability must be finite")
    fraction = Fraction(str(probability))
    if fraction < 0 or fraction > 1:
        raise CatalogValidationError(f"{field}.probability must be between 0 and 1")
    if fraction.denominator > MAX_PROBABILITY_DENOMINATOR:
        raise CatalogValidationError(f"{field}.probability is too precise")
    success_count = _require_nonnegative_int(
        raw["successCount"], f"{field}.successCount"
    )
    failure_count = _require_nonnegative_int(
        raw["failureCount"], f"{field}.failureCount"
    )
    if max(success_count, failure_count) > entry_count:
        raise CatalogValidationError(f"{field} count exceeds category size")
    return DrawRule(
        kind=kind,
        probability_numerator=fraction.numerator,
        probability_denominator=fraction.denominator,
        success_count=success_count,
        failure_count=failure_count,
        without_replacement=True,
    )


def _stable_entry_id(category_id: str, text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = " ".join(normalized.split())
    identity = f"{category_id}\0{normalized}".encode("utf-8")
    return f"{category_id}:{hashlib.sha256(identity).hexdigest()}"


def validate_catalog(
    raw: Mapping[str, Any], *, experimental: bool = False
) -> ValidatedCatalog:
    """Validate a parsed server-owned catalog and freeze its lookup indexes."""

    if not isinstance(raw, dict):
        raise CatalogValidationError("catalog root must be an object")
    digest = _canonical_catalog_hash(raw)
    content_sha256 = raw.get("contentSha256")
    if type(content_sha256) is not str or content_sha256 != digest:
        raise CatalogValidationError("catalog contentSha256 does not match its content")
    expected_version = f"{VERSION_PREFIX}-{digest[:16]}"
    if raw.get("version") != expected_version:
        raise CatalogValidationError("catalog version does not match its content hash")
    fixed_values = {
        "schemaVersion": SCHEMA_VERSION,
        "samplerVersion": SAMPLER_VERSION,
        "profile": PROFILE,
        "contentHashAlgorithm": CONTENT_HASH_ALGORITHM,
    }
    for field, expected in fixed_values.items():
        if raw.get(field) != expected:
            raise CatalogValidationError(
                f"catalog {field} must be {expected!r}, found {raw.get(field)!r}"
            )
    mapping_version = raw.get("mappingVersion")
    if mapping_version not in {MAPPING_VERSION, *LEGACY_MAPPING_VERSIONS}:
        raise CatalogValidationError("catalog mappingVersion is unsupported")
    runtime_ready = raw.get("runtimeReady")
    semantic_review_required = raw.get("semanticReviewRequired")
    if type(runtime_ready) is not bool or type(semantic_review_required) is not bool:
        raise CatalogValidationError(
            "runtimeReady and semanticReviewRequired must be booleans"
        )
    if (not runtime_ready or semantic_review_required) and not experimental:
        raise CatalogBoundaryError(
            "catalog is not release-ready; pass experimental=True only for explicit testing"
        )
    normalization_version = raw.get("normalizationUnicodeVersion")
    if type(normalization_version) is not str or not normalization_version:
        raise CatalogValidationError("normalizationUnicodeVersion must be present")
    categories_raw = raw.get("categories")
    if not isinstance(categories_raw, list) or not categories_raw:
        raise CatalogValidationError("catalog categories must be a non-empty array")
    if len(categories_raw) > MAX_CATEGORIES:
        raise CatalogValidationError("catalog has too many categories")
    if raw.get("categoryCount") != len(categories_raw):
        raise CatalogValidationError("categoryCount does not match categories")

    categories: list[CatalogCategory] = []
    entries_by_id: dict[str, CatalogEntry] = {}
    categories_by_id: dict[str, CatalogCategory] = {}
    normalized_texts: set[str] = set()
    catalog_order = 0
    for category_order, category_raw in enumerate(categories_raw):
        field = f"categories[{category_order}]"
        if not isinstance(category_raw, dict):
            raise CatalogValidationError(f"{field} must be an object")
        category_id = category_raw.get("id")
        if type(category_id) is not str or _CATEGORY_RE.fullmatch(category_id) is None:
            raise CatalogValidationError(f"{field}.id is invalid")
        if category_id in categories_by_id:
            raise CatalogValidationError(f"duplicate category id: {category_id}")
        source_file = _safe_source_file(category_raw.get("sourceFile"), f"{field}.sourceFile")
        primary_block = category_raw.get("primaryBlock")
        if type(primary_block) is not str or _BLOCK_RE.fullmatch(primary_block) is None:
            raise CatalogValidationError(f"{field}.primaryBlock is invalid")
        allowed_raw = category_raw.get("allowedBlocks")
        if not isinstance(allowed_raw, list) or not allowed_raw:
            raise CatalogValidationError(f"{field}.allowedBlocks must be non-empty")
        allowed_blocks: list[str] = []
        for block in allowed_raw:
            if type(block) is not str or _BLOCK_RE.fullmatch(block) is None:
                raise CatalogValidationError(f"{field}.allowedBlocks contains invalid block")
            if block in allowed_blocks:
                raise CatalogValidationError(f"{field}.allowedBlocks contains duplicates")
            allowed_blocks.append(block)
        if primary_block not in allowed_blocks:
            raise CatalogValidationError(
                f"{field}.primaryBlock must appear in allowedBlocks"
            )
        entries_raw = category_raw.get("entries")
        if not isinstance(entries_raw, list) or not entries_raw:
            raise CatalogValidationError(f"{field}.entries must be non-empty")
        if len(entries_raw) > MAX_CATEGORY_ENTRIES:
            raise CatalogValidationError(f"{field} has too many entries")
        if category_raw.get("expectedCount") != len(entries_raw):
            raise CatalogValidationError(f"{field}.expectedCount does not match entries")
        draw_rule = _parse_draw_rule(
            category_raw.get("drawRule"), f"{field}.drawRule", len(entries_raw)
        )
        entries: list[CatalogEntry] = []
        source_lines: set[int] = set()
        for category_index, entry_raw in enumerate(entries_raw):
            entry_field = f"{field}.entries[{category_index}]"
            if not isinstance(entry_raw, dict):
                raise CatalogValidationError(f"{entry_field} must be an object")
            text = entry_raw.get("text")
            if type(text) is not str or not text or text != text.strip():
                raise CatalogValidationError(f"{entry_field}.text is invalid")
            if any(ord(character) < 0x20 or ord(character) > 0x7E for character in text):
                raise CatalogValidationError(
                    f"{entry_field}.text must use printable ASCII"
                )
            normalized = " ".join(
                unicodedata.normalize("NFKC", text).casefold().split()
            )
            if normalized in normalized_texts:
                raise CatalogValidationError("catalog contains normalized duplicate text")
            normalized_texts.add(normalized)
            entry_id = entry_raw.get("entryId")
            expected_entry_id = _stable_entry_id(category_id, text)
            if entry_id != expected_entry_id:
                raise CatalogValidationError(f"{entry_field}.entryId is invalid")
            prefix, _, entry_digest = entry_id.partition(":")
            if prefix != category_id or _ENTRY_DIGEST_RE.fullmatch(entry_digest) is None:
                raise CatalogValidationError(f"{entry_field}.entryId has invalid syntax")
            if entry_id in entries_by_id:
                raise CatalogValidationError(f"duplicate entryId: {entry_id}")
            if entry_raw.get("sourceFile") != source_file:
                raise CatalogValidationError(
                    f"{entry_field}.sourceFile must match its category"
                )
            source_line = entry_raw.get("sourceLine")
            if type(source_line) is not int or source_line < 1:
                raise CatalogValidationError(f"{entry_field}.sourceLine is invalid")
            if source_line in source_lines:
                raise CatalogValidationError(f"{field} contains duplicate sourceLine")
            source_lines.add(source_line)
            entry = CatalogEntry(
                entry_id=entry_id,
                text=text,
                category_id=category_id,
                source_file=source_file,
                source_line=source_line,
                primary_block=primary_block,
                allowed_blocks=tuple(allowed_blocks),
                catalog_order=catalog_order,
                category_order=category_index,
            )
            entries.append(entry)
            entries_by_id[entry_id] = entry
            catalog_order += 1
        category = CatalogCategory(
            category_id=category_id,
            source_file=source_file,
            draw_rule=draw_rule,
            primary_block=primary_block,
            allowed_blocks=tuple(allowed_blocks),
            entries=tuple(entries),
            order=category_order,
        )
        categories.append(category)
        categories_by_id[category_id] = category

    if raw.get("entryCount") != len(entries_by_id):
        raise CatalogValidationError("entryCount does not match category entries")
    return ValidatedCatalog(
        schema_version=SCHEMA_VERSION,
        sampler_version=SAMPLER_VERSION,
        mapping_version=mapping_version,
        profile=PROFILE,
        version=expected_version,
        content_sha256=digest,
        runtime_ready=runtime_ready,
        semantic_review_required=semantic_review_required,
        categories=tuple(categories),
        entries_by_id=MappingProxyType(entries_by_id),
        categories_by_id=MappingProxyType(categories_by_id),
        experimental_mode=bool(
            experimental and (not runtime_ready or semantic_review_required)
        ),
    )


def _unbiased_index(value: int, upper_bound: int) -> int | None:
    """Return an unbiased bounded index, or ``None`` when value must reroll."""

    if type(value) is not int or value < 0 or value >= _UINT256_RANGE:
        raise ValueError("value must be an unsigned 256-bit integer")
    if type(upper_bound) is not int or upper_bound <= 0 or upper_bound > _UINT256_RANGE:
        raise ValueError("upper_bound must be between 1 and 2**256")
    acceptance_limit = _UINT256_RANGE - (_UINT256_RANGE % upper_bound)
    if value >= acceptance_limit:
        return None
    return value % upper_bound


class _CategoryHashStream:
    def __init__(self, library_seed: str, category_id: str) -> None:
        self.seed = bytes.fromhex(normalize_library_seed(library_seed))
        if _CATEGORY_RE.fullmatch(category_id) is None:
            raise ValueError("invalid category stream id")
        self.category_id = category_id
        self.counter = 0

    def _digest_at(self, counter: int) -> bytes:
        frame = (
            _STREAM_PREFIX
            + self.seed
            + b"\0"
            + self.category_id.encode("ascii")
            + b"\0"
            + counter.to_bytes(8, "big")
        )
        return hashlib.sha256(frame).digest()

    def draw_bounded(
        self, upper_bound: int, *, purpose: str
    ) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
        rejections: list[dict[str, Any]] = []
        while True:
            if self.counter > _MAX_COUNTER:
                raise SamplingExhaustedError(
                    "category hash counter overflowed",
                    trace={"categoryId": self.category_id, "counter": self.counter},
                )
            counter = self.counter
            digest = self._digest_at(counter)
            self.counter += 1
            value = int.from_bytes(digest, "big", signed=False)
            index = _unbiased_index(value, upper_bound)
            evidence = {
                "categoryId": self.category_id,
                "purpose": purpose,
                "counter": counter,
                "digestHex": digest.hex(),
                "upperBound": upper_bound,
            }
            if index is not None:
                return index, {**evidence, "index": index}, rejections
            rejections.append(
                {
                    **evidence,
                    "kind": "hash_rejected",
                    "reason": "modulo_bias_guard",
                }
            )


def _validate_id_array(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise PlanRequestError(f"{field} must be an array of entryId strings")
    if len(value) > MAX_REQUEST_ENTRY_IDS:
        raise PlanRequestError(
            f"{field} exceeds the {MAX_REQUEST_ENTRY_IDS}-item limit"
        )
    result: list[str] = []
    seen: set[str] = set()
    for entry_id in value:
        if type(entry_id) is not str or not entry_id or len(entry_id) > 192:
            raise PlanRequestError(f"{field} contains an invalid entryId")
        if entry_id in seen:
            raise PlanRequestError(f"{field} must not contain duplicate entryIds")
        result.append(entry_id)
        seen.add(entry_id)
    return tuple(result)


def _validate_plan_request(
    request: Mapping[str, Any], catalog: ValidatedCatalog
) -> _ValidatedRequest:
    if not isinstance(request, dict):
        raise PlanRequestError("random plan request must be a JSON object")
    keys = set(request)
    missing = _REQUEST_REQUIRED_FIELDS - keys
    unexpected = keys - _REQUEST_REQUIRED_FIELDS - _REQUEST_OPTIONAL_FIELDS
    if missing:
        raise PlanRequestError(
            "random plan request is missing: " + ", ".join(sorted(missing))
        )
    if unexpected:
        raise PlanRequestError(
            "random plan request contains unsupported fields: "
            + ", ".join(sorted(unexpected))
        )
    expected_values = {
        "catalogVersion": catalog.version,
        "catalogContentSha256": catalog.content_sha256,
        "samplerVersion": catalog.sampler_version,
        "mappingVersion": catalog.mapping_version,
        "profile": catalog.profile,
    }
    for field, expected in expected_values.items():
        if request.get(field) != expected:
            raise PlanRequestError(f"{field} does not match the server catalog")
    library_seed = normalize_library_seed(request.get("librarySeed"))
    locked = _validate_id_array(request.get("lockedEntryIds"), "lockedEntryIds")
    reroll = _validate_id_array(request.get("rerollEntryIds"), "rerollEntryIds")
    overlap = set(locked) & set(reroll)
    if overlap:
        raise PlanRequestError(
            "an entryId cannot be both locked and requested for reroll"
        )
    unknown = sorted((set(locked) | set(reroll)) - set(catalog.entries_by_id))
    if unknown:
        raise PlanRequestError(f"unknown entryId: {unknown[0]}")

    draw_counts_raw = request.get("drawCounts", {})
    if not isinstance(draw_counts_raw, dict):
        raise PlanRequestError("drawCounts must be an object")
    draw_counts: dict[str, int] = {}
    for category_id, count in draw_counts_raw.items():
        if type(category_id) is not str or category_id not in catalog.categories_by_id:
            raise PlanRequestError(f"drawCounts contains unknown category: {category_id!r}")
        if type(count) is not int:
            raise PlanRequestError(f"drawCounts.{category_id} must be an integer")
        allowed = catalog.categories_by_id[category_id].draw_rule.allowed_counts
        if count not in allowed:
            raise PlanRequestError(
                f"drawCounts.{category_id} must be one of {list(allowed)}"
            )
        draw_counts[category_id] = count
    draw_counts = {
        category.category_id: draw_counts[category.category_id]
        for category in catalog.categories
        if category.category_id in draw_counts
    }
    raw_scope = request.get("scopeCategoryIds")
    if raw_scope is None:
        scope = tuple(category.category_id for category in catalog.categories)
    else:
        if not isinstance(raw_scope, list) or not raw_scope:
            raise PlanRequestError("scopeCategoryIds must be a non-empty array")
        if any(type(category_id) is not str for category_id in raw_scope):
            raise PlanRequestError("scopeCategoryIds must contain category IDs")
        if len(raw_scope) != len(set(raw_scope)):
            raise PlanRequestError("scopeCategoryIds must not contain duplicates")
        unknown_scope = sorted(set(raw_scope) - set(catalog.categories_by_id))
        if unknown_scope:
            raise PlanRequestError(f"scopeCategoryIds contains unknown category: {unknown_scope[0]}")
        scope = tuple(
            category.category_id for category in catalog.categories if category.category_id in raw_scope
        )
    out_of_scope = {
        entry.category_id for entry_id in (*locked, *reroll)
        for entry in (catalog.entries_by_id[entry_id],)
        if entry.category_id not in scope
    } | (set(draw_counts) - set(scope))
    if out_of_scope:
        raise PlanRequestError("locks, rerolls, and draw counts must stay within scopeCategoryIds")
    return _ValidatedRequest(
        library_seed=library_seed,
        locked_entry_ids=locked,
        reroll_entry_ids=reroll,
        draw_counts=MappingProxyType(draw_counts),
        scope_category_ids=scope,
    )


def _validate_conflict_rules(
    catalog: ValidatedCatalog, rules: Sequence[ConflictRule]
) -> tuple[ConflictRule, ...]:
    if isinstance(rules, (str, bytes)) or not isinstance(rules, Sequence):
        raise TypeError("conflict_rules must be a server-owned sequence")
    result: list[ConflictRule] = []
    pairs: set[frozenset[str]] = set()
    for rule in rules:
        if not isinstance(rule, ConflictRule):
            raise TypeError("conflict_rules must contain ConflictRule instances")
        if _RULE_ID_RE.fullmatch(rule.rule_id) is None:
            raise ValueError("conflict rule id is invalid")
        if rule.severity not in {"hard", "soft"}:
            raise ValueError("conflict rule severity must be hard or soft")
        if (
            rule.left_entry_id not in catalog.entries_by_id
            or rule.right_entry_id not in catalog.entries_by_id
        ):
            raise ValueError("conflict rule references an unknown entryId")
        if rule.left_entry_id == rule.right_entry_id:
            raise ValueError("conflict rule cannot reference one item twice")
        if type(rule.reason) is not str or not rule.reason.strip() or len(rule.reason) > 512:
            raise ValueError("conflict rule reason is invalid")
        pair = frozenset({rule.left_entry_id, rule.right_entry_id})
        if pair in pairs:
            raise ValueError("duplicate conflict pair")
        pairs.add(pair)
        result.append(rule)
    return tuple(result)


def _rules_index(
    rules: Sequence[ConflictRule],
) -> Mapping[frozenset[str], ConflictRule]:
    return MappingProxyType(
        {
            frozenset({rule.left_entry_id, rule.right_entry_id}): rule
            for rule in rules
        }
    )


def _conflict_rules_version(rules: Sequence[ConflictRule]) -> str:
    canonical_rules = sorted(
        (
            {
                "ruleId": rule.rule_id,
                "entryIds": sorted([rule.left_entry_id, rule.right_entry_id]),
                "severity": rule.severity,
                "reason": rule.reason,
            }
            for rule in rules
        ),
        key=lambda item: (item["entryIds"], item["ruleId"]),
    )
    encoded = json.dumps(
        canonical_rules,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"server-entry-pairs-v1-{digest[:16]}"


def _entry_reference(entry: CatalogEntry) -> dict[str, Any]:
    return {
        "entryId": entry.entry_id,
        "text": entry.text,
        "categoryId": entry.category_id,
        "sourceFile": entry.source_file,
        "sourceLine": entry.source_line,
    }


def _lock_reference(entry: CatalogEntry) -> dict[str, Any]:
    return {
        "entryId": entry.entry_id,
        "categoryId": entry.category_id,
        "resolvedBy": "server_catalog_entry_id",
    }


def _conflicts_for(
    candidate: CatalogEntry,
    selected: Iterable[CatalogEntry],
    rules_by_pair: Mapping[frozenset[str], ConflictRule],
) -> list[tuple[CatalogEntry, ConflictRule]]:
    conflicts: list[tuple[CatalogEntry, ConflictRule]] = []
    for other in selected:
        rule = rules_by_pair.get(frozenset({candidate.entry_id, other.entry_id}))
        if rule is not None:
            conflicts.append((other, rule))
    return conflicts


def _conflict_trace(
    candidate: CatalogEntry,
    other: CatalogEntry,
    rule: ConflictRule,
    *,
    decision: str,
    locked: bool,
) -> dict[str, Any]:
    return {
        "ruleId": rule.rule_id,
        "severity": rule.severity,
        "reason": rule.reason,
        "entryIds": [candidate.entry_id, other.entry_id],
        "candidateEntryId": candidate.entry_id,
        "againstEntryId": other.entry_id,
        "lockedConflict": locked,
        "decision": decision,
    }


def _resolve_count(
    category: CatalogCategory,
    stream: _CategoryHashStream,
    override: int | None,
) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
    rule = category.draw_rule
    if override is not None:
        return (
            override,
            {
                "source": "validated_draw_override",
                "count": override,
                "hash": None,
            },
            [],
        )
    if rule.kind == "fixed":
        return (
            int(rule.count),
            {"source": "catalog_fixed", "count": int(rule.count), "hash": None},
            [],
        )
    if rule.kind == "uniform-count":
        width = int(rule.maximum) - int(rule.minimum) + 1
        index, evidence, rejections = stream.draw_bounded(width, purpose="draw_count")
        count = int(rule.minimum) + index
        return (
            count,
            {
                "source": "catalog_uniform_count",
                "minimum": int(rule.minimum),
                "maximum": int(rule.maximum),
                "count": count,
                "hash": evidence,
            },
            rejections,
        )
    denominator = int(rule.probability_denominator)
    numerator = int(rule.probability_numerator)
    index, evidence, rejections = stream.draw_bounded(
        denominator, purpose="draw_count"
    )
    succeeded = index < numerator
    count = int(rule.success_count if succeeded else rule.failure_count)
    return (
        count,
        {
            "source": "catalog_bernoulli",
            "probabilityNumerator": numerator,
            "probabilityDenominator": denominator,
            "succeeded": succeeded,
            "count": count,
            "hash": evidence,
        },
        rejections,
    )


def _selected_item(
    entry: CatalogEntry,
    catalog: ValidatedCatalog,
    *,
    locked: bool,
) -> dict[str, Any]:
    return {
        "entryId": entry.entry_id,
        "text": entry.text,
        "categoryId": entry.category_id,
        "locked": locked,
        "source": {
            "catalogVersion": catalog.version,
            "sourceFile": entry.source_file,
            "sourceLine": entry.source_line,
        },
        "binding": {
            "mappingVersion": catalog.mapping_version,
            "blockId": entry.primary_block,
            "allowedBlocks": list(entry.allowed_blocks),
        },
    }


def resolve_random_plan(
    request: Mapping[str, Any],
    *,
    catalog: ValidatedCatalog,
    conflict_rules: Sequence[ConflictRule] = (),
) -> dict[str, Any]:
    """Resolve a deterministic random plan without using any model.

    Request data is fail-closed: unknown fields (including text, mappings, and
    paths) are rejected.  Locks and rerolls contain only stable ``entryId``
    values, which are resolved again against the validated server catalog.
    """

    if not isinstance(catalog, ValidatedCatalog):
        raise TypeError("catalog must be returned by validate_catalog/load_catalog")
    if (
        (not catalog.runtime_ready or catalog.semantic_review_required)
        and not catalog.experimental_mode
    ):
        raise CatalogBoundaryError("catalog is not enabled for runtime use")
    normalized = _validate_plan_request(request, catalog)
    scoped_categories = tuple(
        category for category in catalog.categories if category.category_id in normalized.scope_category_ids
    )
    rules = _validate_conflict_rules(catalog, conflict_rules)
    rules_by_pair = _rules_index(rules)
    conflict_rules_version = _conflict_rules_version(rules)
    locked_set = set(normalized.locked_entry_ids)
    reroll_set = set(normalized.reroll_entry_ids)
    ordered_locks = sorted(
        (catalog.entries_by_id[entry_id] for entry_id in locked_set),
        key=lambda entry: entry.catalog_order,
    )
    ordered_rerolls = sorted(
        (catalog.entries_by_id[entry_id] for entry_id in reroll_set),
        key=lambda entry: entry.catalog_order,
    )
    lock_trace = [_lock_reference(entry) for entry in ordered_locks]
    reroll_trace = [
        {
            "entryId": entry.entry_id,
            "categoryId": entry.category_id,
            "resolvedBy": "server_catalog_entry_id",
            "decision": "exclude_then_deterministically_reroll",
        }
        for entry in ordered_rerolls
    ]

    locked_by_category: dict[str, list[CatalogEntry]] = {
        category.category_id: [] for category in scoped_categories
    }
    for entry in ordered_locks:
        locked_by_category[entry.category_id].append(entry)
    for category in scoped_categories:
        count = len(locked_by_category[category.category_id])
        if count > category.draw_rule.maximum_count:
            raise PlanRequestError(
                f"lockedEntryIds exceeds {category.category_id}'s maximum draw count"
            )
        override = normalized.draw_counts.get(category.category_id)
        if override is not None and count > override:
            raise PlanRequestError(
                f"drawCounts.{category.category_id} is lower than its locked item count"
            )

    conflicts_trace: list[dict[str, Any]] = []
    globally_selected: list[CatalogEntry] = []
    locked_conflicts: list[dict[str, Any]] = []
    for entry in ordered_locks:
        for other, rule in _conflicts_for(entry, globally_selected, rules_by_pair):
            if rule.severity == "hard":
                record = _conflict_trace(
                    entry,
                    other,
                    rule,
                    decision="user_resolution_required",
                    locked=True,
                )
                locked_conflicts.append(record)
                conflicts_trace.append(record)
            else:
                conflicts_trace.append(
                    _conflict_trace(
                        entry,
                        other,
                        rule,
                        decision="retained_soft_conflict",
                        locked=True,
                    )
                )
        globally_selected.append(entry)
    if locked_conflicts:
        raise LockedConflictError(
            "locked entries contain a hard conflict; automatic replacement is forbidden",
            conflicts=locked_conflicts,
            locks=lock_trace,
        )

    selected_by_category: dict[str, list[CatalogEntry]] = {
        category.category_id: list(locked_by_category[category.category_id])
        for category in scoped_categories
    }
    category_streams: list[dict[str, Any]] = []
    rejections_trace: list[dict[str, Any]] = []
    selection_trace: list[dict[str, Any]] = [
        {
            "entryId": entry.entry_id,
            "categoryId": entry.category_id,
            "origin": "locked",
            "counter": None,
        }
        for entry in ordered_locks
    ]

    for category in scoped_categories:
        stream = _CategoryHashStream(normalized.library_seed, category.category_id)
        resolved_count, count_decision, count_rejections = _resolve_count(
            category,
            stream,
            normalized.draw_counts.get(category.category_id),
        )
        for record in count_rejections:
            rejections_trace.append(record)
        lock_count = len(locked_by_category[category.category_id])
        effective_count = max(resolved_count, lock_count)
        count_decision["resolvedCountBeforeLocks"] = resolved_count
        count_decision["lockCount"] = lock_count
        count_decision["effectiveCount"] = effective_count
        count_decision["raisedByLocks"] = effective_count != resolved_count
        category_rerolls = {
            entry.entry_id
            for entry in ordered_rerolls
            if entry.category_id == category.category_id
        }
        available_count = len(category.entries) - len(category_rerolls)
        if available_count < effective_count:
            raise PlanRequestError(
                f"rerollEntryIds leaves too few entries in {category.category_id}"
            )
        events: list[dict[str, Any]] = []
        attempts = 0
        max_attempts = max(
            MIN_SAMPLING_ATTEMPTS,
            len(category.entries) * MAX_SAMPLING_ATTEMPTS_FACTOR,
        )
        while len(selected_by_category[category.category_id]) < effective_count:
            attempts += 1
            if attempts > max_attempts:
                partial_trace = {
                    "categoryId": category.category_id,
                    "counter": stream.counter,
                    "targetCount": effective_count,
                    "selectedEntryIds": [
                        entry.entry_id
                        for entry in selected_by_category[category.category_id]
                    ],
                    "events": events,
                }
                raise SamplingExhaustedError(
                    f"deterministic sampling exhausted for {category.category_id}",
                    trace=partial_trace,
                )
            index, evidence, modulo_rejections = stream.draw_bounded(
                len(category.entries), purpose="entry"
            )
            for record in modulo_rejections:
                events.append(record)
                rejections_trace.append(record)
            candidate = category.entries[index]
            reasons: list[str] = []
            if candidate.entry_id in reroll_set:
                reasons.append("reroll_requested")
            if any(
                current.entry_id == candidate.entry_id
                for current in selected_by_category[category.category_id]
            ):
                reasons.append("duplicate_without_replacement")
            candidate_conflicts = _conflicts_for(
                candidate, globally_selected, rules_by_pair
            )
            hard_conflicts = [
                (other, rule)
                for other, rule in candidate_conflicts
                if rule.severity == "hard"
            ]
            if hard_conflicts:
                reasons.append("hard_conflict")
                for other, rule in hard_conflicts:
                    conflicts_trace.append(
                        _conflict_trace(
                            candidate,
                            other,
                            rule,
                            decision="deterministic_reroll",
                            locked=False,
                        )
                    )
            if reasons:
                rejection = {
                    **evidence,
                    "kind": "candidate_rejected",
                    "candidate": _entry_reference(candidate),
                    "reasons": reasons,
                }
                events.append(rejection)
                rejections_trace.append(rejection)
                continue
            for other, rule in candidate_conflicts:
                if rule.severity == "soft":
                    conflicts_trace.append(
                        _conflict_trace(
                            candidate,
                            other,
                            rule,
                            decision="retained_soft_conflict",
                            locked=False,
                        )
                    )
            selected_by_category[category.category_id].append(candidate)
            globally_selected.append(candidate)
            selected_event = {
                **evidence,
                "kind": "selected",
                "entryId": candidate.entry_id,
            }
            events.append(selected_event)
            selection_trace.append(
                {
                    "entryId": candidate.entry_id,
                    "categoryId": candidate.category_id,
                    "origin": "sampled",
                    "counter": evidence["counter"],
                    "digestHex": evidence["digestHex"],
                }
            )
        category_streams.append(
            {
                "categoryId": category.category_id,
                "counterStart": 0,
                "counterEndExclusive": stream.counter,
                "countDecision": count_decision,
                "events": events,
            }
        )

    ordered_selected: list[CatalogEntry] = []
    for category in scoped_categories:
        ordered_selected.extend(selected_by_category[category.category_id])
    items = [
        _selected_item(entry, catalog, locked=entry.entry_id in locked_set)
        for entry in ordered_selected
    ]
    mapping_trace = [
        {
            "entryId": entry.entry_id,
            "categoryId": entry.category_id,
            "blockId": entry.primary_block,
            "allowedBlocks": list(entry.allowed_blocks),
            "mappingVersion": catalog.mapping_version,
            "decision": "catalog_primary_block",
        }
        for entry in ordered_selected
    ]
    return {
        "status": "ok",
        "experimental": catalog.experimental_mode,
        "librarySeed": normalized.library_seed,
        "catalog": {
            "schemaVersion": catalog.schema_version,
            "version": catalog.version,
            "contentSha256": catalog.content_sha256,
            "samplerVersion": catalog.sampler_version,
            "mappingVersion": catalog.mapping_version,
            "profile": catalog.profile,
            "runtimeReady": catalog.runtime_ready,
            "semanticReviewRequired": catalog.semantic_review_required,
        },
        "configuration": {
            "scopeCategoryIds": list(normalized.scope_category_ids),
            "drawCounts": dict(normalized.draw_counts),
            "lockedEntryIds": [entry.entry_id for entry in ordered_locks],
            "rerollEntryIds": [entry.entry_id for entry in ordered_rerolls],
            "conflictRulesVersion": conflict_rules_version,
        },
        "items": items,
        "trace": {
            "algorithm": {
                "samplerVersion": catalog.sampler_version,
                "mappingVersion": catalog.mapping_version,
                "conflictPolicyVersion": "server-entry-pairs-v1",
                "conflictRulesVersion": conflict_rules_version,
                "categoryStreamsIndependent": True,
                "boundedDraw": "uint256-rejection-sampling-v1",
            },
            "releaseBoundary": {
                "experimentalMode": catalog.experimental_mode,
                "runtimeReady": catalog.runtime_ready,
                "semanticReviewRequired": catalog.semantic_review_required,
            },
            "categoryStreams": category_streams,
            "selected": selection_trace,
            "rejections": rejections_trace,
            "conflicts": conflicts_trace,
            "locks": lock_trace,
            "rerolls": reroll_trace,
            "mapping": mapping_trace,
        },
    }


def plan_request_template(
    catalog: ValidatedCatalog, library_seed: str
) -> dict[str, Any]:
    """Build the exact version-pinned request envelope for an API client."""

    return {
        "librarySeed": normalize_library_seed(library_seed),
        "catalogVersion": catalog.version,
        "catalogContentSha256": catalog.content_sha256,
        "samplerVersion": catalog.sampler_version,
        "mappingVersion": catalog.mapping_version,
        "profile": catalog.profile,
        "lockedEntryIds": [],
        "rerollEntryIds": [],
        "drawCounts": {},
    }


__all__ = [
    "CatalogBoundaryError",
    "CatalogValidationError",
    "ConflictRule",
    "LockedConflictError",
    "PlanRequestError",
    "RandomSamplerError",
    "SamplingExhaustedError",
    "ValidatedCatalog",
    "generate_library_seed",
    "load_catalog",
    "normalize_library_seed",
    "plan_request_template",
    "resolve_random_plan",
    "validate_catalog",
]
