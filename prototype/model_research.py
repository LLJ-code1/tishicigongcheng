"""Evidence contracts and adapter-only parsing for official model research.

This module deliberately contains no network access. Fetch policy and transport
are layered on top of these contracts so an unsupported URL can never become an
unrestricted fetch request.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlsplit, urlunsplit


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
_CIVITAI_PATH = re.compile(
    r"/models/[1-9][0-9]*(?:/[A-Za-z0-9._~-]+)?/?"
)
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", _required_text(self.url, "url", maximum=2048))
        object.__setattr__(self, "headers", _freeze_headers(self.headers))


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
class EvidenceClaim:
    claim_id: str
    field_path: str
    value: Any
    evidence_class: str
    evidence_refs: tuple[str, ...]
    rationale: str
    verification_status: str
    application_status: str


def normalize_claim(value: Mapping[str, Any] | EvidenceClaim) -> EvidenceClaim:
    if isinstance(value, EvidenceClaim):
        value = claim_to_dict(value)
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

    claim_id = _required_text(value["claimId"], "claimId")
    field_path = value["fieldPath"]
    if field_path not in ALLOWED_CLAIM_PATHS:
        raise ResearchError("unsupported_field", "claim field is not allowlisted")
    evidence_class = value["evidenceClass"]
    if evidence_class not in EVIDENCE_CLASSES:
        raise ResearchError("invalid_evidence_class", "unknown evidence class")
    refs = value["evidenceRefs"]
    if not isinstance(refs, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in refs
    ):
        raise ResearchError("invalid_evidence", "evidenceRefs must contain snapshot IDs")
    evidence_refs = tuple(dict.fromkeys(item.strip() for item in refs))
    if evidence_class in {"original_source", "supplemental_source", "local_validation"}:
        if not evidence_refs:
            raise ResearchError("missing_evidence", "recorded evidence requires a reference")
    verification_status = value["verificationStatus"]
    if verification_status not in VERIFICATION_STATUSES:
        raise ResearchError("invalid_verification_status", "unknown verification status")
    application_status = value["applicationStatus"]
    if application_status not in APPLICATION_STATUSES:
        raise ResearchError("invalid_application_status", "unknown application status")
    rationale = _required_text(value["rationale"], "rationale")
    return EvidenceClaim(
        claim_id=claim_id,
        field_path=field_path,
        value=value["value"],
        evidence_class=evidence_class,
        evidence_refs=evidence_refs,
        rationale=rationale,
        verification_status=verification_status,
        application_status=application_status,
    )


def claim_to_dict(claim: EvidenceClaim) -> dict[str, Any]:
    return {
        "claimId": claim.claim_id,
        "fieldPath": claim.field_path,
        "value": claim.value,
        "evidenceClass": claim.evidence_class,
        "evidenceRefs": list(claim.evidence_refs),
        "rationale": claim.rationale,
        "verificationStatus": claim.verification_status,
        "applicationStatus": claim.application_status,
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
        or parsed.query
        or parsed.fragment
        or not _CIVITAI_PATH.fullmatch(parsed.path)
    ):
        return None
    return urlunsplit(("https", "civitai.com", parsed.path, "", ""))


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
        if snapshot.fetch_status != "succeeded" or not snapshot.extracted_text:
            return []
        payload = _extract_json_payload(snapshot.extracted_text)
        if payload is None:
            return []
        values: list[tuple[str, Any]] = []
        if isinstance(payload.get("name"), str) and payload["name"].strip():
            values.append(("displayName", payload["name"].strip()))
        versions = payload.get("modelVersions")
        if isinstance(versions, list) and versions and isinstance(versions[0], Mapping):
            version = versions[0]
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


def default_adapter_registry() -> AdapterRegistry:
    return AdapterRegistry((CivitaiModelPageAdapter(),))


__all__ = [
    "ALLOWED_CLAIM_PATHS",
    "AdapterRegistry",
    "EvidenceClaim",
    "FetchRequest",
    "FetchResponse",
    "ResearchError",
    "ResearchRequest",
    "SourceAdapter",
    "SourceSnapshot",
    "claim_to_dict",
    "default_adapter_registry",
    "normalize_claim",
    "snapshot_to_dict",
]
