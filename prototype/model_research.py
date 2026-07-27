"""Evidence contracts and bounded fetching for official model research."""

from __future__ import annotations

import hashlib
import html
import http.client
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit


ALLOWED_CLAIM_PATHS = frozenset(
    {
        "displayName",
        "model.family",
        "model.versionName",
        "model.versionId",
        "model.baseModel",
        "prompting.positivePrefix",
        "prompting.positiveSuffix",
        "prompting.negativeDefault",
        "prompting.notRecommended",
        "parameters.defaults.sampler",
        "parameters.defaults.scheduler",
        "parameters.defaults.steps",
        "parameters.defaults.cfg",
        "parameters.recommendedRanges.cfg",
        "resolutions.candidatePresets",
        "metadata.strengths",
        "metadata.weaknesses",
        "metadata.limitations",
    }
)
EVIDENCE_CLASSES = frozenset(
    {"original_source", "supplemental_source", "ai_inference", "local_validation"}
)
APPLICATION_STATUSES = frozenset({"proposed", "approved", "rejected"})
VERIFICATION_STATUSES = frozenset(
    {
        "unverified",
        "source_recorded",
        "pending_verification",
        "locally_validated",
        "invalidated",
    }
)
SOURCE_CLASSES = EVIDENCE_CLASSES
FETCH_STATUSES = frozenset({"succeeded", "failed"})
MAX_CLAIM_VALUE_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_DECODED_TEXT_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
REQUEST_TIMEOUT_SECONDS = 15
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_CIVITAI_PATH = re.compile(
    r"/models/[1-9][0-9]*(?:/[A-Za-z0-9._~-]+)?/?"
)
_CIVITAI_HASH_PATH = re.compile(r"/api/v1/model-versions/by-hash/[0-9a-fA-F]{64}")
_JSON_SCRIPT = re.compile(
    r"<script\b[^>]*\btype=[\"']application/json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


class ResearchError(ValueError):
    """Stable, user-safe research validation failure."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _required_text(value: Any, field: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchError("invalid_contract", f"{field} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ResearchError("invalid_contract", f"{field} is too long")
    return normalized


def _freeze_headers(headers: Mapping[str, str]) -> Mapping[str, str]:
    if not isinstance(headers, Mapping):
        raise ResearchError("invalid_contract", "headers must be a mapping")
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(value, str):
            raise ResearchError("invalid_contract", "headers must contain string pairs")
        normalized[key.strip()] = value.strip()
    return MappingProxyType(normalized)


@dataclass(frozen=True)
class ResearchRequest:
    source_url: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_url", _required_text(self.source_url, "source_url", maximum=2048)
        )


@dataclass(frozen=True)
class FetchRequest:
    url: str
    headers: Mapping[str, str]
    resolved_addresses: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", _required_text(self.url, "url", maximum=2048))
        object.__setattr__(self, "headers", _freeze_headers(self.headers))
        if not isinstance(self.resolved_addresses, (tuple, list)):
            raise ResearchError(
                "invalid_contract", "resolved_addresses must be a sequence"
            )
        normalized_addresses: list[str] = []
        for address in self.resolved_addresses:
            if not isinstance(address, str):
                raise ResearchError(
                    "invalid_contract", "resolved_addresses must contain strings"
                )
            try:
                normalized_addresses.append(str(ipaddress.ip_address(address)))
            except ValueError as exc:
                raise ResearchError(
                    "invalid_contract", "resolved_addresses contains an invalid IP"
                ) from exc
        object.__setattr__(
            self, "resolved_addresses", tuple(dict.fromkeys(normalized_addresses))
        )


@dataclass(frozen=True)
class FetchResponse:
    url: str
    status: int
    headers: Mapping[str, str]
    body: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", _required_text(self.url, "url", maximum=2048))
        if isinstance(self.status, bool) or not isinstance(self.status, int):
            raise ResearchError("invalid_contract", "status must be an integer")
        object.__setattr__(self, "headers", _freeze_headers(self.headers))
        if not isinstance(self.body, bytes):
            raise ResearchError("invalid_contract", "body must be bytes")


@dataclass(frozen=True)
class SourceSnapshot:
    snapshot_id: str
    source_class: str
    requested_url: str
    final_url: str
    retrieved_at: str
    content_type: str | None
    body_sha256: str
    extracted_text: str
    fetch_status: str
    error_code: str | None

    def __post_init__(self) -> None:
        for field in (
            "snapshot_id",
            "requested_url",
            "final_url",
            "retrieved_at",
        ):
            object.__setattr__(
                self, field, _required_text(getattr(self, field), field, maximum=4096)
            )
        if self.source_class not in SOURCE_CLASSES:
            raise ResearchError("invalid_contract", "unknown source_class")
        if self.fetch_status not in FETCH_STATUSES:
            raise ResearchError("invalid_contract", "unknown fetch_status")
        if not isinstance(self.extracted_text, str):
            raise ResearchError("invalid_contract", "extracted_text must be a string")
        if self.body_sha256 and not re.fullmatch(r"[0-9a-f]{64}", self.body_sha256):
            raise ResearchError("invalid_contract", "body_sha256 must be lowercase sha256")
        if self.content_type is not None and not isinstance(self.content_type, str):
            raise ResearchError("invalid_contract", "content_type must be a string or null")
        if self.error_code is not None and not isinstance(self.error_code, str):
            raise ResearchError("invalid_contract", "error_code must be a string or null")


@dataclass(frozen=True)
class ResearchResult:
    snapshot: SourceSnapshot
    claims: tuple["EvidenceClaim", ...]


@dataclass(frozen=True)
class ValidatedFetchTarget:
    url: str
    hostname: str
    addresses: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceClaim:
    claim_id: str
    field_path: str
    value: Any
    evidence_class: str
    evidence_refs: tuple[str, ...]
    rationale: str
    verification_status: str
    application_status: str

    def __post_init__(self) -> None:
        normalized = _validate_claim_parts(
            claim_id=self.claim_id,
            field_path=self.field_path,
            value=self.value,
            evidence_class=self.evidence_class,
            evidence_refs=self.evidence_refs,
            rationale=self.rationale,
            verification_status=self.verification_status,
            application_status=self.application_status,
        )
        for field, field_value in normalized.items():
            object.__setattr__(self, field, field_value)


def _validate_json_value(value: Any, *, depth: int = 0) -> None:
    if depth > 32:
        raise ResearchError("invalid_claim_value", "claim value nesting is too deep")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ResearchError("invalid_claim_value", "claim value must be finite")
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ResearchError(
                "invalid_claim_value", "claim object keys must be strings"
            )
        for item in value.values():
            _validate_json_value(item, depth=depth + 1)
        return
    raise ResearchError("invalid_claim_value", "claim value must be JSON-safe")


def _deep_freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _deep_freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze_json(item) for item in value)
    return value


def _deep_thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _deep_thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_deep_thaw_json(item) for item in value]
    return value


def _validate_claim_value(value: Any) -> None:
    _validate_json_value(value)
    try:
        encoded = json.dumps(
            _deep_thaw_json(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ResearchError("invalid_claim_value", "claim value must be JSON-safe") from exc
    if len(encoded) > MAX_CLAIM_VALUE_BYTES:
        raise ResearchError("invalid_claim_value", "claim value is too large")


def _validate_claim_parts(
    *,
    claim_id: Any,
    field_path: Any,
    value: Any,
    evidence_class: Any,
    evidence_refs: Any,
    rationale: Any,
    verification_status: Any,
    application_status: Any,
) -> dict[str, Any]:
    normalized_claim_id = _required_text(claim_id, "claimId")
    if field_path not in ALLOWED_CLAIM_PATHS:
        raise ResearchError("unsupported_field", "claim field is not allowlisted")
    if evidence_class not in EVIDENCE_CLASSES:
        raise ResearchError("invalid_evidence_class", "unknown evidence class")
    if not isinstance(evidence_refs, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in evidence_refs
    ):
        raise ResearchError("invalid_evidence", "evidenceRefs must contain snapshot IDs")
    normalized_refs = tuple(dict.fromkeys(item.strip() for item in evidence_refs))
    if evidence_class in {"original_source", "supplemental_source", "local_validation"}:
        if not normalized_refs:
            raise ResearchError("missing_evidence", "recorded evidence requires a reference")
    if verification_status not in VERIFICATION_STATUSES:
        raise ResearchError("invalid_verification_status", "unknown verification status")
    if application_status not in APPLICATION_STATUSES:
        raise ResearchError("invalid_application_status", "unknown application status")
    normalized_rationale = _required_text(rationale, "rationale")
    _validate_claim_value(value)
    return {
        "claim_id": normalized_claim_id,
        "field_path": field_path,
        "value": _deep_freeze_json(value),
        "evidence_class": evidence_class,
        "evidence_refs": normalized_refs,
        "rationale": normalized_rationale,
        "verification_status": verification_status,
        "application_status": application_status,
    }


def normalize_claim(value: Mapping[str, Any] | EvidenceClaim) -> EvidenceClaim:
    if isinstance(value, EvidenceClaim):
        value = {
            "claimId": value.claim_id,
            "fieldPath": value.field_path,
            "value": value.value,
            "evidenceClass": value.evidence_class,
            "evidenceRefs": value.evidence_refs,
            "rationale": value.rationale,
            "verificationStatus": value.verification_status,
            "applicationStatus": value.application_status,
        }
    if not isinstance(value, Mapping):
        raise ResearchError("invalid_claim", "claim must be an object")
    expected = {
        "claimId",
        "fieldPath",
        "value",
        "evidenceClass",
        "evidenceRefs",
        "rationale",
        "verificationStatus",
        "applicationStatus",
    }
    if set(value) != expected:
        raise ResearchError("invalid_claim", "claim fields do not match the contract")

    normalized = _validate_claim_parts(
        claim_id=value["claimId"],
        field_path=value["fieldPath"],
        value=value["value"],
        evidence_class=value["evidenceClass"],
        evidence_refs=value["evidenceRefs"],
        rationale=value["rationale"],
        verification_status=value["verificationStatus"],
        application_status=value["applicationStatus"],
    )
    return EvidenceClaim(**normalized)


def claim_to_dict(claim: EvidenceClaim) -> dict[str, Any]:
    if not isinstance(claim, EvidenceClaim):
        raise ResearchError("invalid_claim", "claim must be an EvidenceClaim")
    normalized = _validate_claim_parts(
        claim_id=claim.claim_id,
        field_path=claim.field_path,
        value=claim.value,
        evidence_class=claim.evidence_class,
        evidence_refs=claim.evidence_refs,
        rationale=claim.rationale,
        verification_status=claim.verification_status,
        application_status=claim.application_status,
    )
    return {
        "claimId": normalized["claim_id"],
        "fieldPath": normalized["field_path"],
        "value": _deep_thaw_json(normalized["value"]),
        "evidenceClass": normalized["evidence_class"],
        "evidenceRefs": list(normalized["evidence_refs"]),
        "rationale": normalized["rationale"],
        "verificationStatus": normalized["verification_status"],
        "applicationStatus": normalized["application_status"],
    }


def snapshot_to_dict(snapshot: SourceSnapshot) -> dict[str, Any]:
    return {
        "snapshotId": snapshot.snapshot_id,
        "sourceClass": snapshot.source_class,
        "requestedUrl": snapshot.requested_url,
        "finalUrl": snapshot.final_url,
        "retrievedAt": snapshot.retrieved_at,
        "contentType": snapshot.content_type,
        "bodySha256": snapshot.body_sha256,
        "extractedText": snapshot.extracted_text,
        "fetchStatus": snapshot.fetch_status,
        "errorCode": snapshot.error_code,
    }


class SourceAdapter(Protocol):
    adapter_id: str

    def supports(self, url: str) -> bool: ...

    def request_for(self, url: str) -> FetchRequest: ...

    def parse(self, snapshot: SourceSnapshot) -> list[EvidenceClaim]: ...


Resolver = Callable[[str], Sequence[str]]
Fetcher = Callable[[FetchRequest], FetchResponse]


class AdapterRegistry:
    def __init__(self, adapters: Sequence[SourceAdapter]):
        self._adapters = tuple(adapters)
        if not self._adapters:
            raise ResearchError("invalid_registry", "at least one adapter is required")
        adapter_ids = [adapter.adapter_id for adapter in self._adapters]
        if len(adapter_ids) != len(set(adapter_ids)):
            raise ResearchError("invalid_registry", "adapter IDs must be unique")

    def resolve(self, url: str) -> SourceAdapter:
        for adapter in self._adapters:
            if adapter.supports(url):
                return adapter
        raise ResearchError("unsupported_source", "URL is not a registered original page")


def _canonical_civitai_page(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() != "civitai.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
        or not _CIVITAI_PATH.fullmatch(parsed.path)
    ):
        return None
    query_items = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    if not query_items:
        query = ""
    elif (
        len(query_items) == 1
        and query_items[0][0] == "modelVersionId"
        and query_items[0][1].isdigit()
        and int(query_items[0][1]) > 0
    ):
        query = f"modelVersionId={int(query_items[0][1])}"
    else:
        return None
    return urlunsplit(("https", "civitai.com", parsed.path, query, ""))


def _civitai_version_id(url: str) -> int | None:
    """Return the only accepted optional Civitai version pin."""

    canonical = _canonical_civitai_page(url)
    if canonical is None:
        return None
    query = urlsplit(canonical).query
    return int(query.split("=", 1)[1]) if query else None


def _canonical_civitai_hash(url: str) -> str | None:
    """Accept one exact Civitai by-hash endpoint and no query parameters."""

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() != "civitai.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or not _CIVITAI_HASH_PATH.fullmatch(parsed.path)
    ):
        return None
    hash_value = parsed.path.rsplit("/", 1)[1].lower()
    return f"https://civitai.com/api/v1/model-versions/by-hash/{hash_value}"


def civitai_hash_source_url(sha256: str) -> str:
    """Build the only research URL accepted for a local SHA-256 lookup."""

    normalized = str(sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ResearchError("invalid_hash", "sha256 must be 64 lowercase-or-uppercase hex characters")
    return f"https://civitai.com/api/v1/model-versions/by-hash/{normalized}"


def _extract_json_payload(text: str) -> Mapping[str, Any] | None:
    candidates = [text]
    candidates.extend(html.unescape(match) for match in _JSON_SCRIPT.findall(text))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(parsed, Mapping):
            continue
        if isinstance(parsed.get("props"), Mapping):
            page_props = parsed["props"].get("pageProps")
            if isinstance(page_props, Mapping):
                for key in ("model", "data"):
                    if isinstance(page_props.get(key), Mapping):
                        return page_props[key]
                return page_props
        return parsed
    return None


def _claim_id(snapshot_id: str, field_path: str, value: Any) -> str:
    material = json.dumps(
        [snapshot_id, field_path, value],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "claim-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


class CivitaiModelPageAdapter:
    adapter_id = "civitai-model-page-v1"

    def supports(self, url: str) -> bool:
        return _canonical_civitai_page(url) is not None

    def request_for(self, url: str) -> FetchRequest:
        canonical = _canonical_civitai_page(url)
        if canonical is None:
            raise ResearchError("unsupported_source", "not a Civitai model page")
        return FetchRequest(
            url=canonical,
            headers={"Accept": "application/json, text/html; q=0.9"},
        )

    def parse(self, snapshot: SourceSnapshot) -> list[EvidenceClaim]:
        if (
            snapshot.source_class != "original_source"
            or not self.supports(snapshot.requested_url)
            or not self.supports(snapshot.final_url)
        ):
            raise ResearchError(
                "source_mismatch",
                "snapshot source and URLs must belong to this original-page adapter",
            )
        if snapshot.fetch_status != "succeeded" or not snapshot.extracted_text:
            return []
        payload = _extract_json_payload(snapshot.extracted_text)
        if payload is None:
            return []
        values: list[tuple[str, Any]] = []
        if isinstance(payload.get("name"), str) and payload["name"].strip():
            values.append(("displayName", payload["name"].strip()))
        versions = payload.get("modelVersions")
        requested_version_id = _civitai_version_id(snapshot.requested_url)
        version = None
        if isinstance(versions, list):
            if requested_version_id is None:
                version = next(
                    (item for item in versions if isinstance(item, Mapping)), None
                )
            else:
                version = next(
                    (
                        item
                        for item in versions
                        if isinstance(item, Mapping)
                        and item.get("id") == requested_version_id
                    ),
                    None,
                )
        if version is not None:
            if isinstance(version.get("id"), int) and not isinstance(
                version.get("id"), bool
            ):
                values.append(("model.versionId", version["id"]))
            if isinstance(version.get("name"), str) and version["name"].strip():
                values.append(("model.versionName", version["name"].strip()))
            if isinstance(version.get("baseModel"), str) and version[
                "baseModel"
            ].strip():
                values.append(("model.baseModel", version["baseModel"].strip()))

        claims = [
            normalize_claim(
                {
                    "claimId": _claim_id(snapshot.snapshot_id, field_path, field_value),
                    "fieldPath": field_path,
                    "value": field_value,
                    "evidenceClass": "original_source",
                    "evidenceRefs": [snapshot.snapshot_id],
                    "rationale": "Deterministically extracted from an allowlisted source field.",
                    "verificationStatus": "source_recorded",
                    "applicationStatus": "proposed",
                }
            )
            for field_path, field_value in values
        ]
        return sorted(claims, key=lambda claim: (claim.field_path, claim.claim_id))


class CivitaiHashAdapter:
    """Read-only evidence adapter for a user-supplied local checkpoint hash.

    The snapshot keeps `trainedWords`, file hashes, metadata and any AIR value as
    external evidence.  Only profile fields in the existing allowlist become
    reviewable claims; AIR is never used as an internal profile identifier.
    """

    adapter_id = "civitai-by-hash-v1"

    def supports(self, url: str) -> bool:
        return _canonical_civitai_hash(url) is not None

    def request_for(self, url: str) -> FetchRequest:
        canonical = _canonical_civitai_hash(url)
        if canonical is None:
            raise ResearchError("unsupported_source", "not a Civitai by-hash endpoint")
        return FetchRequest(url=canonical, headers={"Accept": "application/json"})

    def parse(self, snapshot: SourceSnapshot) -> list[EvidenceClaim]:
        if (
            snapshot.source_class != "original_source"
            or not self.supports(snapshot.requested_url)
            or not self.supports(snapshot.final_url)
        ):
            raise ResearchError("source_mismatch", "snapshot is not a Civitai by-hash result")
        if snapshot.fetch_status != "succeeded" or not snapshot.extracted_text:
            return []
        payload = _extract_json_payload(snapshot.extracted_text)
        if payload is None:
            return []
        values: list[tuple[str, Any]] = []
        if isinstance(payload.get("id"), int) and not isinstance(payload["id"], bool):
            values.append(("model.versionId", payload["id"]))
        if isinstance(payload.get("name"), str) and payload["name"].strip():
            values.append(("model.versionName", payload["name"].strip()))
        if isinstance(payload.get("baseModel"), str) and payload["baseModel"].strip():
            values.append(("model.baseModel", payload["baseModel"].strip()))
        return sorted(
            [
                normalize_claim(
                    {
                        "claimId": _claim_id(snapshot.snapshot_id, field_path, value),
                        "fieldPath": field_path,
                        "value": value,
                        "evidenceClass": "original_source",
                        "evidenceRefs": [snapshot.snapshot_id],
                        "rationale": "Extracted from Civitai by-hash evidence; requires review.",
                        "verificationStatus": "source_recorded",
                        "applicationStatus": "proposed",
                    }
                )
                for field_path, value in values
            ],
            key=lambda claim: (claim.field_path, claim.claim_id),
        )


def civitai_hash_model_page(snapshot: SourceSnapshot) -> str:
    """Derive the internal research source from a successful by-hash snapshot."""

    if snapshot.fetch_status != "succeeded" or not snapshot.extracted_text:
        raise ResearchError("by_hash_unavailable", "by-hash source did not return evidence")
    payload = _extract_json_payload(snapshot.extracted_text)
    if payload is None:
        raise ResearchError("by_hash_invalid_response", "by-hash source did not return JSON")
    model_id = payload.get("modelId")
    version_id = payload.get("id")
    if (
        isinstance(model_id, bool)
        or not isinstance(model_id, int)
        or model_id <= 0
        or isinstance(version_id, bool)
        or not isinstance(version_id, int)
        or version_id <= 0
    ):
        raise ResearchError("by_hash_invalid_response", "by-hash evidence is missing model or version ID")
    return f"https://civitai.com/models/{model_id}?modelVersionId={version_id}"


def default_adapter_registry() -> AdapterRegistry:
    return AdapterRegistry((CivitaiHashAdapter(), CivitaiModelPageAdapter()))


def default_resolver(host: str) -> tuple[str, ...]:
    """Resolve a hostname without exposing socket result details to callers."""
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ResearchError("dns_failed", "source hostname could not be resolved") from exc
    return tuple(dict.fromkeys(answer[4][0] for answer in answers))


def _validate_fetch_target(url: str, resolver: Resolver) -> ValidatedFetchTarget:
    """Resolve and validate one outbound HTTPS hop as one atomic policy step."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ResearchError("invalid_url", "source URL is malformed") from exc
    host = parsed.hostname
    if parsed.scheme.casefold() != "https":
        raise ResearchError("https_required", "source URL must use HTTPS")
    if not host:
        raise ResearchError("invalid_url", "source URL must have a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ResearchError("credentials_forbidden", "URL credentials are forbidden")
    if parsed.fragment:
        raise ResearchError("fragment_forbidden", "URL fragments are forbidden")
    if port is not None and port != 443:
        raise ResearchError("port_forbidden", "only the default HTTPS port is allowed")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ResearchError("ip_literal_forbidden", "IP-literal hosts are forbidden")
    if host.casefold() == "localhost" or host.casefold().endswith(".localhost"):
        raise ResearchError("unsafe_address", "localhost names are forbidden")
    try:
        addresses = tuple(resolver(host))
    except ResearchError:
        raise
    except Exception as exc:
        raise ResearchError("dns_failed", "source hostname could not be resolved") from exc
    if not addresses:
        raise ResearchError("dns_failed", "source hostname returned no addresses")
    normalized_addresses: list[str] = []
    for address_text in addresses:
        try:
            address = ipaddress.ip_address(address_text)
        except (TypeError, ValueError) as exc:
            raise ResearchError("dns_failed", "resolver returned an invalid address") from exc
        if (
            not address.is_global
            or address.is_multicast
            or address.is_reserved
            or address.is_loopback
            or address.is_link_local
            or address.is_private
            or address.is_unspecified
        ):
            raise ResearchError("unsafe_address", "source hostname resolved unsafely")
        normalized_addresses.append(str(address))
    hostname = host.casefold()
    authority = hostname if port is None else f"{hostname}:{port}"
    return ValidatedFetchTarget(
        url=urlunsplit(("https", authority, parsed.path or "/", parsed.query, "")),
        hostname=hostname,
        addresses=tuple(dict.fromkeys(normalized_addresses)),
    )


def validate_fetch_url(url: str, resolver: Resolver) -> str:
    """Validate one outbound HTTPS hop and return its canonical URL."""
    return _validate_fetch_target(url, resolver).url


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.casefold()
    for key, value in headers.items():
        if key.casefold() == wanted:
            return value
    return None


def _snapshot_id(requested_url: str, final_url: str, retrieved_at: str, body: bytes) -> str:
    digest = hashlib.sha256()
    for value in (requested_url.encode(), final_url.encode(), retrieved_at.encode(), body):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return "snap-" + digest.hexdigest()[:24]


def _failed_snapshot(requested_url: str, retrieved_at: str) -> SourceSnapshot:
    return SourceSnapshot(
        snapshot_id=_snapshot_id(requested_url, requested_url, retrieved_at, b""),
        source_class="original_source",
        requested_url=requested_url,
        final_url=requested_url,
        retrieved_at=retrieved_at,
        content_type=None,
        body_sha256="",
        extracted_text="",
        fetch_status="failed",
        error_code="fetch_failed",
    )


def fetch_snapshot(
    request: FetchRequest,
    *,
    adapter: SourceAdapter,
    fetcher: Fetcher,
    resolver: Resolver,
    clock: Callable[[], str],
) -> SourceSnapshot:
    """Fetch an adapter-owned page with validation before every network hop."""
    if not adapter.supports(request.url):
        raise ResearchError("unsupported_source", "request is not owned by its adapter")
    target = _validate_fetch_target(request.url, resolver)
    requested_url = target.url
    current_url = requested_url
    headers = dict(request.headers)
    retrieved_at = _required_text(clock(), "retrieved_at")

    for redirect_count in range(MAX_REDIRECTS + 1):
        if not adapter.supports(current_url):
            raise ResearchError("unsupported_source", "redirect left the source adapter")
        try:
            response = fetcher(
                FetchRequest(current_url, headers, target.addresses)
            )
        except ResearchError:
            raise
        except Exception:
            return _failed_snapshot(requested_url, retrieved_at)
        if not isinstance(response, FetchResponse):
            return _failed_snapshot(requested_url, retrieved_at)
        if response.url != current_url:
            raise ResearchError("response_url_mismatch", "transport changed the fetched URL")

        if response.status in _REDIRECT_STATUSES:
            location = _header(response.headers, "Location")
            if not location:
                raise ResearchError("invalid_redirect", "redirect has no Location")
            if redirect_count >= MAX_REDIRECTS:
                raise ResearchError("too_many_redirects", "redirect limit exceeded")
            target = urljoin(current_url, location)
            if not adapter.supports(target):
                raise ResearchError("unsupported_source", "redirect left the source adapter")
            validated_target = _validate_fetch_target(target, resolver)
            current_url = validated_target.url
            target = validated_target
            continue
        if not 200 <= response.status <= 299:
            raise ResearchError("unexpected_status", "source returned a non-success status")
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise ResearchError("response_too_large", "source response exceeds 2 MiB")

        content_type_header = _header(response.headers, "Content-Type") or ""
        parts = [part.strip() for part in content_type_header.split(";")]
        content_type = parts[0].casefold()
        if content_type not in {"text/html", "application/json"}:
            raise ResearchError(
                "unsupported_content_type", "source must be UTF-8 HTML or JSON"
            )
        charset = None
        for parameter in parts[1:]:
            if "=" in parameter:
                key, value = parameter.split("=", 1)
                if key.strip().casefold() == "charset":
                    charset = value.strip().strip("\"'").casefold()
        if charset not in (None, "utf-8", "utf8"):
            raise ResearchError("unsupported_encoding", "source must use UTF-8")
        try:
            text = response.body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ResearchError("invalid_utf8", "source body is not valid UTF-8") from exc
        if len(text.encode("utf-8")) > MAX_DECODED_TEXT_BYTES:
            raise ResearchError("response_too_large", "decoded source exceeds 2 MiB")
        return SourceSnapshot(
            snapshot_id=_snapshot_id(
                requested_url, current_url, retrieved_at, response.body
            ),
            source_class="original_source",
            requested_url=requested_url,
            final_url=current_url,
            retrieved_at=retrieved_at,
            content_type=content_type,
            body_sha256=hashlib.sha256(response.body).hexdigest(),
            extracted_text=text,
            fetch_status="succeeded",
            error_code=None,
        )
    raise ResearchError("too_many_redirects", "redirect limit exceeded")


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection whose TCP peer is an audited numeric address."""

    def __init__(
        self,
        hostname: str,
        pinned_address: str,
        port: int,
        timeout: float,
    ):
        self._pinned_address = pinned_address
        super().__init__(hostname, port=port, timeout=timeout)

    def connect(self) -> None:
        if self._tunnel_host:
            raise ResearchError("proxy_forbidden", "proxy tunnels are forbidden")
        address = ipaddress.ip_address(self._pinned_address)
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        raw_socket = socket.socket(family, socket.SOCK_STREAM)
        try:
            raw_socket.settimeout(self.timeout)
            if self.source_address:
                raw_socket.bind(self.source_address)
            raw_socket.connect((str(address), self.port))
            self.sock = self._context.wrap_socket(
                raw_socket,
                server_hostname=self.host,
            )
        except BaseException:
            raw_socket.close()
            raise


def production_fetcher(
    request: FetchRequest,
    *,
    connection_factory: Callable[
        [str, str, int, float], http.client.HTTPSConnection
    ] = _PinnedHTTPSConnection,
) -> FetchResponse:
    """Perform exactly one HTTPS request; redirects remain caller-controlled."""
    parsed = urlsplit(request.url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ResearchError("https_required", "transport accepts HTTPS only")
    if not request.resolved_addresses:
        raise ResearchError(
            "missing_validated_address",
            "transport requires an address from the URL policy",
        )
    for address_text in request.resolved_addresses:
        address = ipaddress.ip_address(address_text)
        if (
            not address.is_global
            or address.is_multicast
            or address.is_reserved
            or address.is_loopback
            or address.is_link_local
            or address.is_private
            or address.is_unspecified
        ):
            raise ResearchError(
                "unsafe_address", "transport received an unsafe address"
            )
    forbidden = {
        "accept-encoding",
        "authorization",
        "cookie",
        "proxy-authorization",
        "proxy-connection",
    }
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.casefold() not in forbidden
    }
    headers["Accept-Encoding"] = "identity"
    connection = connection_factory(
        parsed.hostname,
        request.resolved_addresses[0],
        parsed.port or 443,
        REQUEST_TIMEOUT_SECONDS,
    )
    try:
        target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        connection.request("GET", target, headers=headers)
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
        return FetchResponse(
            request.url,
            response.status,
            {key: value for key, value in response.getheaders()},
            body,
        )
    finally:
        connection.close()


def research_source(
    request: ResearchRequest,
    *,
    registry: AdapterRegistry,
    fetcher: Fetcher,
    resolver: Resolver,
    clock: Callable[[], str],
) -> ResearchResult:
    adapter = registry.resolve(request.source_url)
    snapshot = fetch_snapshot(
        adapter.request_for(request.source_url),
        adapter=adapter,
        fetcher=fetcher,
        resolver=resolver,
        clock=clock,
    )
    return ResearchResult(snapshot=snapshot, claims=tuple(adapter.parse(snapshot)))


def _research_identity(source_url: str, version_id: object = None) -> str:
    canonical = _canonical_civitai_page(source_url)
    if canonical is None:
        raise ResearchError("unsupported_source", "no adapter accepts this source URL")
    model_match = re.search(r"/models/([1-9][0-9]*)", urlsplit(canonical).path)
    assert model_match is not None
    prefix = f"civitai-model-page-v1-{model_match.group(1)}"
    if isinstance(version_id, int) and not isinstance(version_id, bool) and version_id > 0:
        return f"{prefix}-v{version_id}"
    suffix = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-pending-{suffix}"


def _research_run_id(source_url: str, snapshots: Sequence[SourceSnapshot]) -> str:
    material = source_url + "\n" + "\n".join(
        f"{item.snapshot_id}:{item.body_sha256}:{item.fetch_status}" for item in snapshots
    )
    return "research-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _set_projected_field(profile: dict[str, Any], path: str, value: Any, claim: EvidenceClaim | None) -> None:
    value = _deep_thaw_json(value)
    if path == "resolutions.candidatePresets":
        if not isinstance(value, list):
            raise ResearchError("invalid_projection", "candidate presets must be an array")
        evidence_ref = (
            claim.evidence_refs[0] if claim is not None and claim.evidence_refs else
            (claim.claim_id if claim is not None else "user-supplied")
        )
        candidates = []
        for index, raw in enumerate(value):
            if not isinstance(raw, Mapping):
                raise ResearchError("invalid_projection", "candidate preset must be an object")
            width = raw.get("width")
            height = raw.get("height")
            if (
                isinstance(width, bool) or not isinstance(width, int) or
                isinstance(height, bool) or not isinstance(height, int) or
                width < 64 or height < 64 or width > 8192 or height > 8192 or
                width % 8 or height % 8
            ):
                raise ResearchError("invalid_projection", "candidate dimensions are invalid")
            raw_id = raw.get("id")
            if not isinstance(raw_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,127}", raw_id):
                raise ResearchError("invalid_projection", "candidate preset ID is invalid")
            label = raw.get("label") or f"{width} x {height}"
            if not isinstance(label, str) or not label.strip():
                raise ResearchError("invalid_projection", "candidate preset label is invalid")
            candidates.append({
                "id": raw_id,
                "width": width,
                "height": height,
                "label": label.strip(),
                "verificationStatus": "author_reported_pending_local_validation"
                if claim is not None and claim.evidence_class == "original_source"
                else "project_candidate_pending_local_validation",
                "evidenceRef": evidence_ref,
                "autoRecommend": False,
            })
        profile["resolutions"]["candidatePresets"] = candidates
        return
    target: dict[str, Any] = profile
    parts = path.split(".")
    for part in parts[:-1]:
        child = target.get(part)
        if not isinstance(child, dict):
            raise ResearchError("unsupported_field", f"{path} cannot be projected")
        target = child
    if parts[-1] not in target and path not in {
        "model.family", "model.versionName", "model.versionId", "model.baseModel",
        "parameters.defaults.sampler", "parameters.defaults.scheduler",
        "parameters.defaults.steps", "parameters.defaults.cfg",
        "parameters.recommendedRanges.cfg",
    }:
        raise ResearchError("unsupported_field", f"{path} cannot be projected")
    target[parts[-1]] = value


def _clear_projected_field(profile: dict[str, Any], path: str) -> None:
    if path == "displayName":
        profile["displayName"] = "Pending model research"
    elif path in {"model.family", "model.versionName", "model.versionId", "model.baseModel"}:
        profile["model"][path.rsplit(".", 1)[1]] = None
    elif path.startswith("prompting."):
        profile["prompting"][path.rsplit(".", 1)[1]] = []
    elif path.startswith("parameters.defaults."):
        profile["parameters"]["defaults"].pop(path.rsplit(".", 1)[1], None)
    elif path == "parameters.recommendedRanges.cfg":
        profile["parameters"]["recommendedRanges"].pop("cfg", None)
    elif path == "resolutions.candidatePresets":
        profile["resolutions"]["candidatePresets"] = []
    elif path in {"metadata.strengths", "metadata.weaknesses", "metadata.limitations"}:
        profile["metadata"][path.rsplit(".", 1)[1]] = []


def _audit_item(
    claim_id: str,
    field_path: str,
    evidence_class: str,
    evidence_refs: Sequence[str],
    status: str,
) -> dict[str, Any]:
    return {
        "claimId": claim_id,
        "fieldPath": field_path,
        "evidenceClass": evidence_class,
        "evidenceRefs": list(evidence_refs),
        "applicationStatus": status,
    }


def apply_claim_decisions(
    profile: Mapping[str, Any],
    claims: Sequence[EvidenceClaim | Mapping[str, Any]],
    decisions: Mapping[str, str],
    manual_fields: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Project reviewed facts while retaining a complete, honest claim audit."""

    if not isinstance(profile, Mapping) or not isinstance(decisions, Mapping):
        raise ResearchError("invalid_contract", "profile and decisions must be objects")
    result = json.loads(json.dumps(profile, ensure_ascii=False, allow_nan=False))
    normalized_claims = [normalize_claim(item) for item in claims]
    known_ids = {item.claim_id for item in normalized_claims}
    if any(key not in known_ids for key in decisions):
        raise ResearchError("unknown_claim", "decision references an unknown claim")
    previous_audit = (
        result.get("metadata", {}).get("research", {}).get("claimAudit", [])
    )
    controlled_paths = {
        item.get("fieldPath")
        for item in previous_audit
        if isinstance(item, Mapping) and item.get("fieldPath") in ALLOWED_CLAIM_PATHS
    }
    controlled_paths.update(item.field_path for item in normalized_claims)
    if isinstance(manual_fields, Mapping):
        controlled_paths.update(manual_fields.keys())
    for controlled_path in controlled_paths:
        _clear_projected_field(result, controlled_path)

    audit: list[dict[str, Any]] = []
    applied_paths: set[str] = set()
    for claim in normalized_claims:
        status = decisions.get(claim.claim_id, claim.application_status)
        if status not in APPLICATION_STATUSES:
            raise ResearchError("invalid_application_status", "unknown application status")
        audit.append(_audit_item(
            claim.claim_id, claim.field_path, claim.evidence_class,
            claim.evidence_refs, status,
        ))
        if status != "approved":
            continue
        if claim.field_path in applied_paths:
            raise ResearchError("conflicting_claims", "multiple approved claims target one field")
        applied_paths.add(claim.field_path)
        _set_projected_field(result, claim.field_path, claim.value, claim)

    manual = manual_fields or {}
    if not isinstance(manual, Mapping):
        raise ResearchError("invalid_contract", "manual_fields must be an object")
    for path, value in manual.items():
        if path not in ALLOWED_CLAIM_PATHS:
            raise ResearchError("unsupported_field", "manual field is not allowlisted")
        _validate_claim_value(value)
        _set_projected_field(result, path, value, None)
        manual_id = "manual-" + hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]
        audit.append(_audit_item(manual_id, path, "user_supplied", (), "approved"))

    version_id = result.get("model", {}).get("versionId")
    source_url = result.get("metadata", {}).get("research", {}).get("sourceUrl")
    result["profileId"] = _research_identity(source_url, version_id)
    warnings = result["metadata"]["research"]["warnings"]
    warnings = [item for item in warnings if item != "missing_exact_version"]
    if not isinstance(version_id, int) or isinstance(version_id, bool):
        warnings.append("missing_exact_version")
    result["metadata"]["research"]["warnings"] = list(dict.fromkeys(warnings))
    result["metadata"]["research"]["claimAudit"] = audit
    return result, audit


def build_pending_profile(
    source_url: str,
    snapshots: Sequence[SourceSnapshot],
    claims: Sequence[EvidenceClaim | Mapping[str, Any]],
    manual_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a usable but deliberately sparse pending-verification profile."""

    canonical = _canonical_civitai_page(source_url)
    if canonical is None:
        raise ResearchError("unsupported_source", "no adapter accepts this source URL")
    if not isinstance(snapshots, Sequence) or any(
        not isinstance(item, SourceSnapshot) for item in snapshots
    ):
        raise ResearchError("invalid_contract", "snapshots must contain SourceSnapshot values")
    warnings = []
    if any(item.fetch_status == "failed" for item in snapshots):
        warnings.append("research_fetch_failed")
    profile = {
        "kind": "anima_model_profile",
        "schemaVersion": 1,
        "profileId": _research_identity(canonical),
        "displayName": "Pending model research",
        "validationStatus": "pending_local_validation",
        "model": {
            "family": None,
            "branch": None,
            "versionName": None,
            "versionId": None,
            "baseModel": None,
            "checkpoint": {
                "filename": None,
                "sha256": None,
                "verificationStatus": "unverified",
            },
        },
        "prompting": {
            "positivePrefix": [],
            "optionalEnhancements": [],
            "positiveSuffix": [],
            "negativeDefault": [],
            "notRecommended": [],
        },
        "parameters": {
            "defaults": {},
            "recommendedRanges": {},
            "samplerSchedulerPairs": [],
            "verificationStatus": "unverified",
        },
        "resolutions": {
            "validatedPresets": [],
            "candidatePresets": [],
            "policy": "",
        },
        "compatibility": {},
        "evidence": [
            {
                "snapshotId": item.snapshot_id,
                "sourceClass": item.source_class,
                "requestedUrl": item.requested_url,
                "finalUrl": item.final_url,
                "retrievedAt": item.retrieved_at,
                "fetchStatus": item.fetch_status,
                **({"bodySha256": item.body_sha256} if item.body_sha256 else {}),
                **({"errorCode": item.error_code} if item.error_code else {}),
            }
            for item in snapshots
        ],
        "metadata": {
            "strengths": [],
            "weaknesses": [],
            "limitations": [],
            "research": {
                "sourceUrl": canonical,
                "researchRunId": _research_run_id(canonical, snapshots),
                "researchStatus": "pending_verification",
                "warnings": warnings + ["missing_exact_version"],
                "claimAudit": [],
            }
        },
    }
    decisions = {
        normalize_claim(item).claim_id: normalize_claim(item).application_status
        for item in claims
    }
    projected, _ = apply_claim_decisions(
        profile, claims, decisions, manual_fields or {}
    )
    return projected


__all__ = [
    "ALLOWED_CLAIM_PATHS",
    "AdapterRegistry",
    "EvidenceClaim",
    "FetchRequest",
    "FetchResponse",
    "Fetcher",
    "MAX_RESPONSE_BYTES",
    "ResearchResult",
    "ResearchError",
    "ResearchRequest",
    "Resolver",
    "SourceAdapter",
    "SourceSnapshot",
    "claim_to_dict",
    "civitai_hash_model_page",
    "civitai_hash_source_url",
    "apply_claim_decisions",
    "build_pending_profile",
    "default_adapter_registry",
    "default_resolver",
    "fetch_snapshot",
    "normalize_claim",
    "production_fetcher",
    "research_source",
    "snapshot_to_dict",
    "validate_fetch_url",
]
