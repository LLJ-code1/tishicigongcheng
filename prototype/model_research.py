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
from urllib.parse import urljoin, urlsplit, urlunsplit


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
class ResearchResult:
    snapshot: SourceSnapshot
    claims: tuple["EvidenceClaim", ...]


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


def default_resolver(host: str) -> tuple[str, ...]:
    """Resolve a hostname without exposing socket result details to callers."""
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ResearchError("dns_failed", "source hostname could not be resolved") from exc
    return tuple(dict.fromkeys(answer[4][0] for answer in answers))


def validate_fetch_url(url: str, resolver: Resolver) -> str:
    """Validate one outbound HTTPS hop and all of its DNS answers."""
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
    for address_text in addresses:
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError as exc:
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
    hostname = host.casefold()
    authority = hostname if port is None else f"{hostname}:{port}"
    return urlunsplit(("https", authority, parsed.path or "/", parsed.query, ""))


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
    requested_url = validate_fetch_url(request.url, resolver)
    current_url = requested_url
    headers = dict(request.headers)
    retrieved_at = _required_text(clock(), "retrieved_at")

    for redirect_count in range(MAX_REDIRECTS + 1):
        if not adapter.supports(current_url):
            raise ResearchError("unsupported_source", "redirect left the source adapter")
        try:
            response = fetcher(FetchRequest(current_url, headers))
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
            current_url = validate_fetch_url(target, resolver)
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


def production_fetcher(
    request: FetchRequest,
    *,
    connection_factory: Callable[..., http.client.HTTPSConnection] = http.client.HTTPSConnection,
) -> FetchResponse:
    """Perform exactly one HTTPS request; redirects remain caller-controlled."""
    parsed = urlsplit(request.url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ResearchError("https_required", "transport accepts HTTPS only")
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
        parsed.hostname, parsed.port or 443, timeout=REQUEST_TIMEOUT_SECONDS
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
    "default_adapter_registry",
    "default_resolver",
    "fetch_snapshot",
    "normalize_claim",
    "production_fetcher",
    "research_source",
    "snapshot_to_dict",
    "validate_fetch_url",
]
