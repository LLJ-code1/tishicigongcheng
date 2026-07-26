"""Read-only, versioned model profile loading and validation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping


PROFILE_SCHEMA_VERSION = 1
PROFILE_KIND = "anima_model_profile"
DEFAULT_PROFILE_ID = "anima-1.1-v1"
DEFAULT_PROFILE_DIRECTORY = Path(__file__).resolve().parent / "data" / "model-profiles"

_SAFE_PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_PROFILE_BYTES = 1_000_000
_RESEARCH_CLAIM_PATHS = frozenset({
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
})


class ModelProfileError(ValueError):
    """Raised for malformed, unsupported, or unsafe model profiles."""


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ModelProfileError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ModelProfileError(f"{label} keys must be strings")
    return value


def _unknown(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    fields = sorted(set(value) - allowed)
    if fields:
        raise ModelProfileError(
            f"{label} contains unsupported fields: {', '.join(fields)}"
        )


def _text(
    value: object, label: str, *, nullable: bool = False, maximum: int = 2_048
) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ModelProfileError(f"{label} must be a string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ModelProfileError(f"{label} contains invalid Unicode") from error
    if not value.strip():
        raise ModelProfileError(f"{label} cannot be empty")
    if len(value) > maximum:
        raise ModelProfileError(f"{label} exceeds the length limit")
    return value


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelProfileError(f"{label} must be an integer")
    if value < minimum or value > maximum:
        raise ModelProfileError(f"{label} is outside the supported range")
    return value


def _number(
    value: object, label: str, minimum: float, maximum: float
) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelProfileError(f"{label} must be a finite number")
    if not math.isfinite(float(value)):
        raise ModelProfileError(f"{label} must be a finite number")
    if float(value) < minimum or float(value) > maximum:
        raise ModelProfileError(f"{label} is outside the supported range")
    return value if isinstance(value, int) else float(value)


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ModelProfileError(f"{label} must be a boolean")
    return value


def _string_list(value: object, label: str, maximum: int = 256) -> list[str]:
    if not isinstance(value, list):
        raise ModelProfileError(f"{label} must be an array")
    if len(value) > maximum:
        raise ModelProfileError(f"{label} has too many items")
    result: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        item = _text(raw, f"{label}[{index}]", maximum=1_024)
        assert isinstance(item, str)
        item = item.strip()
        folded = item.casefold()
        if folded in seen:
            raise ModelProfileError(f"{label} contains a duplicate item: {item}")
        seen.add(folded)
        result.append(item)
    return result


def _strict_json_copy(value: object, label: str, depth: int = 0) -> Any:
    if depth > 24:
        raise ModelProfileError(f"{label} is nested too deeply")
    if value is None or isinstance(value, (bool, int, str)):
        if isinstance(value, str):
            _text(value, label, maximum=200_000)
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ModelProfileError(f"{label} must be a finite number")
        return value
    if isinstance(value, Mapping):
        result = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ModelProfileError(f"{label} keys must be strings")
            _text(key, f"{label} key", maximum=256)
            result[key] = _strict_json_copy(child, f"{label}.{key}", depth + 1)
        return result
    if isinstance(value, list):
        return [
            _strict_json_copy(child, f"{label}[{index}]", depth + 1)
            for index, child in enumerate(value)
        ]
    raise ModelProfileError(f"{label} is not strict JSON data")


def _normalize_checkpoint(value: object) -> dict[str, Any]:
    checkpoint = _mapping(value, "model.checkpoint")
    _unknown(
        checkpoint,
        {"filename", "sha256", "verificationStatus"},
        "model.checkpoint",
    )
    filename = _text(
        checkpoint.get("filename"),
        "model.checkpoint.filename",
        nullable=True,
        maximum=1_024,
    )
    checksum = _text(
        checkpoint.get("sha256"),
        "model.checkpoint.sha256",
        nullable=True,
        maximum=64,
    )
    if isinstance(checksum, str):
        checksum = checksum.lower()
        if not _SHA256.fullmatch(checksum):
            raise ModelProfileError("model.checkpoint.sha256 must be SHA-256")
    status = _text(
        checkpoint.get("verificationStatus"),
        "model.checkpoint.verificationStatus",
        maximum=64,
    )
    assert isinstance(status, str)
    allowed = {
        "unverified",
        "candidate",
        "hash_verified",
        "locally_validated",
        "missing",
    }
    if status not in allowed:
        raise ModelProfileError(
            "model.checkpoint.verificationStatus is unsupported"
        )
    if status in {"hash_verified", "locally_validated"} and not checksum:
        raise ModelProfileError(
            "a verified checkpoint must include its SHA-256 checksum"
        )
    if status == "locally_validated" and not filename:
        raise ModelProfileError(
            "a locally validated checkpoint must include its filename"
        )
    return {
        "filename": filename,
        "sha256": checksum,
        "verificationStatus": status,
    }


def _normalize_model(value: object) -> dict[str, Any]:
    model = _mapping(value, "model")
    _unknown(
        model,
        {
            "family",
            "branch",
            "versionName",
            "versionId",
            "baseModel",
            "checkpoint",
        },
        "model",
    )
    return {
        "family": _text(model.get("family"), "model.family", maximum=256),
        "branch": _text(model.get("branch"), "model.branch", maximum=128),
        "versionName": _text(
            model.get("versionName"), "model.versionName", maximum=128
        ),
        "versionId": _integer(
            model.get("versionId"), "model.versionId", 1, 2**63 - 1
        ),
        "baseModel": _text(
            model.get("baseModel"), "model.baseModel", maximum=128
        ),
        "checkpoint": _normalize_checkpoint(model.get("checkpoint")),
    }


def _normalize_prompting(value: object) -> dict[str, list[str]]:
    prompting = _mapping(value, "prompting")
    allowed = {
        "positivePrefix",
        "optionalEnhancements",
        "positiveSuffix",
        "negativeDefault",
    }
    _unknown(prompting, allowed, "prompting")
    missing = sorted(allowed - set(prompting))
    if missing:
        raise ModelProfileError(
            "prompting is missing required fields: " + ", ".join(missing)
        )
    result = {
        key: _string_list(prompting[key], f"prompting.{key}")
        for key in sorted(allowed)
    }
    if any(item.casefold() == "safe" for item in result["positivePrefix"]):
        raise ModelProfileError("prompting.positivePrefix must not add safe")
    overlap = {
        item.casefold() for item in result["positivePrefix"]
    } & {item.casefold() for item in result["optionalEnhancements"]}
    if overlap:
        raise ModelProfileError(
            "optional enhancements must not duplicate the fixed prefix"
        )
    return result


def _normalize_parameters(value: object) -> dict[str, Any]:
    parameters = _mapping(value, "parameters")
    _unknown(
        parameters,
        {
            "defaults",
            "recommendedRanges",
            "samplerSchedulerPairs",
            "verificationStatus",
        },
        "parameters",
    )
    defaults = _mapping(parameters.get("defaults"), "parameters.defaults")
    _unknown(defaults, {"sampler", "scheduler", "steps", "cfg"}, "parameters.defaults")
    if set(defaults) != {"sampler", "scheduler", "steps", "cfg"}:
        raise ModelProfileError(
            "parameters.defaults must define sampler, scheduler, steps, and cfg"
        )
    normalized_defaults = {
        "sampler": _text(
            defaults["sampler"], "parameters.defaults.sampler", maximum=128
        ),
        "scheduler": _text(
            defaults["scheduler"], "parameters.defaults.scheduler", maximum=128
        ),
        "steps": _integer(defaults["steps"], "parameters.defaults.steps", 1, 1_000),
        "cfg": _number(defaults["cfg"], "parameters.defaults.cfg", 0, 100),
    }

    ranges = _mapping(
        parameters.get("recommendedRanges"), "parameters.recommendedRanges"
    )
    _unknown(ranges, {"cfg"}, "parameters.recommendedRanges")
    cfg_range = _mapping(ranges.get("cfg"), "parameters.recommendedRanges.cfg")
    _unknown(cfg_range, {"min", "max"}, "parameters.recommendedRanges.cfg")
    cfg_min = _number(cfg_range.get("min"), "parameters.recommendedRanges.cfg.min", 0, 100)
    cfg_max = _number(cfg_range.get("max"), "parameters.recommendedRanges.cfg.max", 0, 100)
    if float(cfg_min) > float(cfg_max):
        raise ModelProfileError("recommended CFG minimum exceeds maximum")
    if not float(cfg_min) <= float(normalized_defaults["cfg"]) <= float(cfg_max):
        raise ModelProfileError("default CFG is outside its recommended range")

    pairs_raw = parameters.get("samplerSchedulerPairs")
    if not isinstance(pairs_raw, list) or not pairs_raw:
        raise ModelProfileError("parameters.samplerSchedulerPairs must be non-empty")
    pairs: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for index, raw in enumerate(pairs_raw):
        pair = _mapping(raw, f"parameters.samplerSchedulerPairs[{index}]")
        _unknown(
            pair,
            {"sampler", "scheduler"},
            f"parameters.samplerSchedulerPairs[{index}]",
        )
        sampler = _text(
            pair.get("sampler"),
            f"parameters.samplerSchedulerPairs[{index}].sampler",
            maximum=128,
        )
        scheduler = _text(
            pair.get("scheduler"),
            f"parameters.samplerSchedulerPairs[{index}].scheduler",
            maximum=128,
        )
        assert isinstance(sampler, str) and isinstance(scheduler, str)
        pair_key = (sampler, scheduler)
        if pair_key in seen_pairs:
            raise ModelProfileError("sampler/scheduler pair is duplicated")
        seen_pairs.add(pair_key)
        pairs.append({"sampler": sampler, "scheduler": scheduler})
    if (
        str(normalized_defaults["sampler"]),
        str(normalized_defaults["scheduler"]),
    ) not in seen_pairs:
        raise ModelProfileError("default sampler/scheduler pair is not allowed")

    verification = _text(
        parameters.get("verificationStatus"),
        "parameters.verificationStatus",
        maximum=64,
    )
    assert isinstance(verification, str)
    if verification not in {"author_reported", "locally_validated", "unverified"}:
        raise ModelProfileError("parameters.verificationStatus is unsupported")
    return {
        "defaults": normalized_defaults,
        "recommendedRanges": {"cfg": {"min": cfg_min, "max": cfg_max}},
        "samplerSchedulerPairs": pairs,
        "verificationStatus": verification,
    }


def _normalize_resolution_preset(
    value: object, label: str, *, validated_collection: bool
) -> dict[str, Any]:
    preset = _mapping(value, label)
    _unknown(
        preset,
        {
            "id",
            "width",
            "height",
            "label",
            "verificationStatus",
            "evidenceRef",
            "autoRecommend",
        },
        label,
    )
    preset_id = _text(preset.get("id"), f"{label}.id", maximum=128)
    assert isinstance(preset_id, str)
    if not _SAFE_PROFILE_ID.fullmatch(preset_id):
        raise ModelProfileError(f"{label}.id is invalid")
    width = _integer(preset.get("width"), f"{label}.width", 64, 8_192)
    height = _integer(preset.get("height"), f"{label}.height", 64, 8_192)
    if width % 8 or height % 8:
        raise ModelProfileError(f"{label} dimensions must be multiples of 8")
    verification = _text(
        preset.get("verificationStatus"), f"{label}.verificationStatus", maximum=64
    )
    assert isinstance(verification, str)
    if verification not in {
        "author_reported_pending_local_validation",
        "project_candidate_pending_local_validation",
        "locally_validated",
    }:
        raise ModelProfileError(f"{label}.verificationStatus is unsupported")
    auto_recommend = _boolean(
        preset.get("autoRecommend"), f"{label}.autoRecommend"
    )
    if validated_collection:
        if verification != "locally_validated" or not auto_recommend:
            raise ModelProfileError(
                f"{label} must be locally validated and auto-recommendable"
            )
    elif auto_recommend:
        raise ModelProfileError(
            f"{label} cannot be auto-recommended from the candidate collection"
        )
    return {
        "id": preset_id,
        "width": width,
        "height": height,
        "label": _text(preset.get("label"), f"{label}.label", maximum=256),
        "verificationStatus": verification,
        "evidenceRef": _text(
            preset.get("evidenceRef"), f"{label}.evidenceRef", maximum=128
        ),
        "autoRecommend": auto_recommend,
    }


def _normalize_resolutions(value: object) -> dict[str, Any]:
    resolutions = _mapping(value, "resolutions")
    _unknown(
        resolutions,
        {"validatedPresets", "candidatePresets", "policy"},
        "resolutions",
    )
    validated_raw = resolutions.get("validatedPresets")
    candidates_raw = resolutions.get("candidatePresets")
    if not isinstance(validated_raw, list) or not isinstance(candidates_raw, list):
        raise ModelProfileError("resolution preset collections must be arrays")
    validated = [
        _normalize_resolution_preset(
            raw, f"resolutions.validatedPresets[{index}]", validated_collection=True
        )
        for index, raw in enumerate(validated_raw)
    ]
    candidates = [
        _normalize_resolution_preset(
            raw,
            f"resolutions.candidatePresets[{index}]",
            validated_collection=False,
        )
        for index, raw in enumerate(candidates_raw)
    ]
    identifiers = [item["id"] for item in validated + candidates]
    if len(identifiers) != len(set(identifiers)):
        raise ModelProfileError("resolution preset IDs must be unique")
    return {
        "validatedPresets": validated,
        "candidatePresets": candidates,
        "policy": _text(resolutions.get("policy"), "resolutions.policy", maximum=1_024),
    }


def _normalize_evidence(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ModelProfileError("evidence must be a non-empty array")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        item = _mapping(raw, f"evidence[{index}]")
        _unknown(
            item,
            {
                "id",
                "kind",
                "title",
                "url",
                "retrievedAt",
                "claimScope",
                "verificationStatus",
            },
            f"evidence[{index}]",
        )
        evidence_id = _text(item.get("id"), f"evidence[{index}].id", maximum=128)
        assert isinstance(evidence_id, str)
        if not _SAFE_PROFILE_ID.fullmatch(evidence_id) or evidence_id in seen:
            raise ModelProfileError(f"evidence[{index}].id is invalid or duplicated")
        seen.add(evidence_id)
        result.append(
            {
                "id": evidence_id,
                "kind": _text(item.get("kind"), f"evidence[{index}].kind", maximum=64),
                "title": _text(item.get("title"), f"evidence[{index}].title", maximum=512),
                "url": _text(
                    item.get("url"),
                    f"evidence[{index}].url",
                    nullable=True,
                    maximum=2_048,
                ),
                "retrievedAt": _text(
                    item.get("retrievedAt"),
                    f"evidence[{index}].retrievedAt",
                    maximum=64,
                ),
                "claimScope": _text(
                    item.get("claimScope"),
                    f"evidence[{index}].claimScope",
                    maximum=2_048,
                ),
                "verificationStatus": _text(
                    item.get("verificationStatus"),
                    f"evidence[{index}].verificationStatus",
                    maximum=128,
                ),
            }
        )
    return result


def validate_model_profile(value: object) -> dict[str, Any]:
    """Return a normalized profile or raise ``ModelProfileError``."""

    profile = _mapping(value, "profile")
    allowed = {
        "kind",
        "schemaVersion",
        "profileId",
        "displayName",
        "validationStatus",
        "model",
        "prompting",
        "parameters",
        "resolutions",
        "compatibility",
        "evidence",
        "metadata",
    }
    _unknown(profile, allowed, "profile")
    if profile.get("kind") != PROFILE_KIND:
        raise ModelProfileError("unsupported profile kind")
    version = profile.get("schemaVersion")
    if isinstance(version, bool) or version != PROFILE_SCHEMA_VERSION:
        raise ModelProfileError(f"unsupported profile schemaVersion: {version}")
    profile_id = _text(profile.get("profileId"), "profile.profileId", maximum=128)
    assert isinstance(profile_id, str)
    if not _SAFE_PROFILE_ID.fullmatch(profile_id):
        raise ModelProfileError("profile.profileId is invalid")
    validation_status = _text(
        profile.get("validationStatus"), "profile.validationStatus", maximum=64
    )
    assert isinstance(validation_status, str)
    if validation_status not in {
        "pending_local_validation",
        "locally_validated",
        "deprecated",
    }:
        raise ModelProfileError("profile.validationStatus is unsupported")

    model = _normalize_model(profile.get("model"))
    prompting = _normalize_prompting(profile.get("prompting"))
    parameters = _normalize_parameters(profile.get("parameters"))
    resolutions = _normalize_resolutions(profile.get("resolutions"))
    evidence = _normalize_evidence(profile.get("evidence"))
    evidence_ids = {item["id"] for item in evidence}
    for preset in resolutions["validatedPresets"] + resolutions["candidatePresets"]:
        if preset["evidenceRef"] not in evidence_ids:
            raise ModelProfileError(
                f"resolution preset references unknown evidence: {preset['evidenceRef']}"
            )
    if validation_status == "locally_validated":
        if model["checkpoint"]["verificationStatus"] != "locally_validated":
            raise ModelProfileError(
                "a locally validated profile needs a locally validated checkpoint"
            )
        if parameters["verificationStatus"] != "locally_validated":
            raise ModelProfileError(
                "a locally validated profile needs locally validated parameters"
            )

    compatibility = profile.get("compatibility", {})
    metadata = profile.get("metadata", {})
    if not isinstance(compatibility, Mapping) or not isinstance(metadata, Mapping):
        raise ModelProfileError("compatibility and metadata must be objects")
    return {
        "kind": PROFILE_KIND,
        "schemaVersion": PROFILE_SCHEMA_VERSION,
        "profileId": profile_id,
        "displayName": _text(
            profile.get("displayName"), "profile.displayName", maximum=256
        ),
        "validationStatus": validation_status,
        "model": model,
        "prompting": prompting,
        "parameters": parameters,
        "resolutions": resolutions,
        "compatibility": _strict_json_copy(compatibility, "compatibility"),
        "evidence": evidence,
        "metadata": _strict_json_copy(metadata, "metadata"),
    }


def validate_researched_model_profile(value: object) -> dict[str, Any]:
    """Validate a sparse, evidence-gated research draft without inventing defaults."""

    profile = _mapping(value, "profile")
    allowed = {
        "kind", "schemaVersion", "profileId", "displayName", "validationStatus",
        "model", "prompting", "parameters", "resolutions", "compatibility",
        "evidence", "metadata",
    }
    _unknown(profile, allowed, "profile")
    if profile.get("kind") != PROFILE_KIND or profile.get("schemaVersion") != PROFILE_SCHEMA_VERSION:
        raise ModelProfileError("unsupported researched profile schema")
    profile_id = _text(profile.get("profileId"), "profile.profileId", maximum=128)
    assert isinstance(profile_id, str)
    if not _SAFE_PROFILE_ID.fullmatch(profile_id):
        raise ModelProfileError("profile.profileId is invalid")
    status = _text(profile.get("validationStatus"), "profile.validationStatus", maximum=64)
    if status not in {"pending_local_validation", "locally_validated", "deprecated"}:
        raise ModelProfileError("profile.validationStatus is unsupported")

    model = _mapping(profile.get("model"), "model")
    _unknown(model, {"family", "branch", "versionName", "versionId", "baseModel", "checkpoint"}, "model")
    normalized_model: dict[str, Any] = {}
    for field in ("family", "branch", "versionName", "baseModel"):
        raw = model.get(field)
        normalized_model[field] = (
            _text(raw, f"model.{field}", maximum=256) if raw is not None else None
        )
    version_id = model.get("versionId")
    normalized_model["versionId"] = (
        _integer(version_id, "model.versionId", 1, 2**63 - 1)
        if version_id is not None
        else None
    )
    normalized_model["checkpoint"] = _normalize_checkpoint(
        model.get("checkpoint", {"filename": None, "sha256": None, "verificationStatus": "unverified"})
    )

    prompting = _mapping(profile.get("prompting"), "prompting")
    _unknown(prompting, {"positivePrefix", "optionalEnhancements", "positiveSuffix", "negativeDefault", "notRecommended"}, "prompting")
    normalized_prompting = {
        key: _string_list(prompting.get(key, []), f"prompting.{key}")
        for key in ("positivePrefix", "optionalEnhancements", "positiveSuffix", "negativeDefault", "notRecommended")
    }
    if any(item.casefold() == "safe" for item in normalized_prompting["positivePrefix"]):
        raise ModelProfileError("prompting.positivePrefix must not add safe")

    parameters = _mapping(profile.get("parameters"), "parameters")
    _unknown(parameters, {"defaults", "recommendedRanges", "samplerSchedulerPairs", "verificationStatus"}, "parameters")
    defaults = _mapping(parameters.get("defaults", {}), "parameters.defaults")
    _unknown(defaults, {"sampler", "scheduler", "steps", "cfg"}, "parameters.defaults")
    normalized_defaults: dict[str, Any] = {}
    if "sampler" in defaults:
        normalized_defaults["sampler"] = _text(defaults["sampler"], "parameters.defaults.sampler", maximum=128)
    if "scheduler" in defaults:
        normalized_defaults["scheduler"] = _text(defaults["scheduler"], "parameters.defaults.scheduler", maximum=128)
    if "steps" in defaults:
        normalized_defaults["steps"] = _integer(defaults["steps"], "parameters.defaults.steps", 1, 1_000)
    if "cfg" in defaults:
        normalized_defaults["cfg"] = _number(defaults["cfg"], "parameters.defaults.cfg", 0, 100)
    ranges = _strict_json_copy(parameters.get("recommendedRanges", {}), "parameters.recommendedRanges")
    pairs = _strict_json_copy(parameters.get("samplerSchedulerPairs", []), "parameters.samplerSchedulerPairs")
    verification = parameters.get("verificationStatus", "unverified")
    if verification not in {"author_reported", "locally_validated", "unverified"}:
        raise ModelProfileError("parameters.verificationStatus is unsupported")

    resolutions = _mapping(profile.get("resolutions"), "resolutions")
    _unknown(resolutions, {"validatedPresets", "candidatePresets", "policy"}, "resolutions")
    validated_raw = resolutions.get("validatedPresets", [])
    candidates_raw = resolutions.get("candidatePresets", [])
    if not isinstance(validated_raw, list) or not isinstance(candidates_raw, list):
        raise ModelProfileError("resolution preset collections must be arrays")
    validated = [
        _normalize_resolution_preset(
            item, f"resolutions.validatedPresets[{index}]", validated_collection=True
        )
        for index, item in enumerate(validated_raw)
    ]
    candidates = [
        _normalize_resolution_preset(
            item, f"resolutions.candidatePresets[{index}]", validated_collection=False
        )
        for index, item in enumerate(candidates_raw)
    ]
    if validated and status != "locally_validated":
        raise ModelProfileError("validated presets require local validation evidence")

    evidence = profile.get("evidence", [])
    if not isinstance(evidence, list):
        raise ModelProfileError("evidence must be an array")
    normalized_evidence = []
    evidence_classes_by_id: dict[str, str] = {}
    for index, raw_evidence in enumerate(evidence):
        item = _mapping(raw_evidence, f"evidence[{index}]")
        _unknown(
            item,
            {
                "snapshotId", "sourceClass", "requestedUrl", "finalUrl",
                "retrievedAt", "fetchStatus", "bodySha256", "errorCode",
            },
            f"evidence[{index}]",
        )
        snapshot_id = _text(item.get("snapshotId"), f"evidence[{index}].snapshotId", maximum=128)
        assert isinstance(snapshot_id, str)
        if snapshot_id in evidence_classes_by_id:
            raise ModelProfileError("evidence snapshotId is duplicated")
        source_class = item.get("sourceClass")
        if source_class not in {
            "original_source", "supplemental_source", "ai_inference", "local_validation"
        }:
            raise ModelProfileError(f"evidence[{index}].sourceClass is unsupported")
        evidence_classes_by_id[snapshot_id] = source_class
        normalized_evidence.append(_strict_json_copy(item, f"evidence[{index}]"))
    metadata = _mapping(profile.get("metadata"), "metadata")
    _unknown(metadata, {"research", "strengths", "weaknesses", "limitations"}, "metadata")
    research = _mapping(metadata.get("research"), "metadata.research")
    _unknown(research, {"sourceUrl", "researchRunId", "researchStatus", "warnings", "claimAudit"}, "metadata.research")
    if research.get("researchStatus") not in {"pending_verification", "reviewed"}:
        raise ModelProfileError("metadata.research.researchStatus is unsupported")
    warnings = _string_list(research.get("warnings", []), "metadata.research.warnings")
    audit = research.get("claimAudit", [])
    if not isinstance(audit, list):
        raise ModelProfileError("metadata.research.claimAudit must be an array")
    normalized_audit = []
    seen_claim_ids: set[str] = set()
    for index, raw_audit in enumerate(audit):
        item = _mapping(raw_audit, f"metadata.research.claimAudit[{index}]")
        _unknown(
            item,
            {"claimId", "fieldPath", "evidenceClass", "evidenceRefs", "applicationStatus"},
            f"metadata.research.claimAudit[{index}]",
        )
        claim_id = _text(item.get("claimId"), f"metadata.research.claimAudit[{index}].claimId", maximum=128)
        assert isinstance(claim_id, str)
        if claim_id in seen_claim_ids:
            raise ModelProfileError("metadata.research.claimAudit contains duplicate claimId")
        seen_claim_ids.add(claim_id)
        evidence_class = item.get("evidenceClass")
        if evidence_class not in {
            "original_source", "supplemental_source", "ai_inference",
            "local_validation", "user_supplied",
        }:
            raise ModelProfileError(f"metadata.research.claimAudit[{index}].evidenceClass is unsupported")
        refs = _string_list(item.get("evidenceRefs", []), f"metadata.research.claimAudit[{index}].evidenceRefs")
        if evidence_class in {"original_source", "supplemental_source", "local_validation"} and not refs:
            raise ModelProfileError("recorded research evidence needs evidenceRefs")
        application_status = item.get("applicationStatus")
        if application_status not in {"proposed", "approved", "rejected"}:
            raise ModelProfileError("claim audit applicationStatus is unsupported")
        field_path = _text(item.get("fieldPath"), f"metadata.research.claimAudit[{index}].fieldPath", maximum=256)
        if field_path not in _RESEARCH_CLAIM_PATHS:
            raise ModelProfileError("claim audit fieldPath is unsupported")
        for evidence_ref in refs:
            if evidence_ref not in evidence_classes_by_id:
                raise ModelProfileError("claim audit evidenceRef is unknown")
            if evidence_class in {
                "original_source", "supplemental_source", "local_validation"
            } and evidence_classes_by_id[evidence_ref] != evidence_class:
                raise ModelProfileError("claim audit evidenceClass does not match snapshot")
        normalized_audit.append({
            "claimId": claim_id,
            "fieldPath": field_path,
            "evidenceClass": evidence_class,
            "evidenceRefs": refs,
            "applicationStatus": application_status,
        })

    if status == "locally_validated":
        if normalized_model["checkpoint"]["verificationStatus"] != "locally_validated":
            raise ModelProfileError("locally validated profile needs local validation evidence")
        if verification != "locally_validated":
            raise ModelProfileError("locally validated profile needs local validation evidence")
        approved_local_paths = {
            item["fieldPath"]
            for item in normalized_audit
            if item["evidenceClass"] == "local_validation"
            and item["applicationStatus"] == "approved"
        }
        if not (
            any(path.startswith("model.checkpoint") for path in approved_local_paths)
            and any(path.startswith("parameters.") for path in approved_local_paths)
        ):
            raise ModelProfileError(
                "locally validated profile needs approved local_validation evidence for checkpoint and parameters"
            )
    evidence_ids = set(evidence_classes_by_id)
    for preset in validated + candidates:
        if preset["evidenceRef"] not in evidence_ids:
            raise ModelProfileError(
                f"resolution preset references unknown evidence: {preset['evidenceRef']}"
            )

    return {
        "kind": PROFILE_KIND,
        "schemaVersion": PROFILE_SCHEMA_VERSION,
        "profileId": profile_id,
        "displayName": _text(profile.get("displayName"), "profile.displayName", maximum=256),
        "validationStatus": status,
        "model": normalized_model,
        "prompting": normalized_prompting,
        "parameters": {
            "defaults": normalized_defaults,
            "recommendedRanges": ranges,
            "samplerSchedulerPairs": pairs,
            "verificationStatus": verification,
        },
        "resolutions": {
            "validatedPresets": validated,
            "candidatePresets": candidates,
            "policy": str(resolutions.get("policy", "")),
        },
        "compatibility": _strict_json_copy(profile.get("compatibility", {}), "compatibility"),
        "evidence": normalized_evidence,
        "metadata": {
            "strengths": _string_list(metadata.get("strengths", []), "metadata.strengths"),
            "weaknesses": _string_list(metadata.get("weaknesses", []), "metadata.weaknesses"),
            "limitations": _string_list(metadata.get("limitations", []), "metadata.limitations"),
            "research": {
                "sourceUrl": _text(research.get("sourceUrl"), "metadata.research.sourceUrl", maximum=2_048),
                "researchRunId": _text(research.get("researchRunId"), "metadata.research.researchRunId", maximum=128),
                "researchStatus": research["researchStatus"],
                "warnings": warnings,
                "claimAudit": normalized_audit,
            }
        },
    }


def _validate_for_helpers(profile: object) -> dict[str, Any]:
    if isinstance(profile, Mapping) and isinstance(profile.get("metadata"), Mapping) and "research" in profile["metadata"]:
        return validate_researched_model_profile(profile)
    return validate_model_profile(profile)


def _reject_constant(token: str) -> None:
    raise ModelProfileError(f"invalid JSON constant: {token}")


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ModelProfileError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_model_profile(
    profile_id: str = DEFAULT_PROFILE_ID,
    *,
    directory: str | Path | None = None,
) -> dict[str, Any]:
    """Load one bundled (or test-directory) profile without path traversal."""

    if not isinstance(profile_id, str) or not _SAFE_PROFILE_ID.fullmatch(profile_id):
        raise ModelProfileError("profile_id is invalid")
    root = Path(directory or DEFAULT_PROFILE_DIRECTORY).resolve()
    candidate = (root / f"{profile_id}.json").resolve()
    if candidate.parent != root:
        raise ModelProfileError("profile path escapes its directory")
    try:
        raw = candidate.read_bytes()
    except FileNotFoundError as error:
        raise ModelProfileError(f"model profile not found: {profile_id}") from error
    if len(raw) > _MAX_PROFILE_BYTES:
        raise ModelProfileError("model profile exceeds the size limit")
    try:
        text = raw.decode("utf-8")
        parsed = json.loads(
            text,
            object_pairs_hook=_strict_object_pairs,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as error:
        raise ModelProfileError("model profile is not UTF-8") from error
    except json.JSONDecodeError as error:
        raise ModelProfileError("model profile is not valid JSON") from error
    normalized = validate_model_profile(parsed)
    if normalized["profileId"] != profile_id:
        raise ModelProfileError("profile filename does not match profileId")
    return normalized


def list_model_profiles(
    *, directory: str | Path | None = None
) -> list[dict[str, Any]]:
    root = Path(directory or DEFAULT_PROFILE_DIRECTORY).resolve()
    if not root.exists():
        return []
    result = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.name.casefold()):
        profile_id = path.stem
        if not _SAFE_PROFILE_ID.fullmatch(profile_id):
            continue
        result.append(load_model_profile(profile_id, directory=root))
    return result


def model_reference(profile: object) -> dict[str, Any]:
    """Snapshot the exact model identity fields required by Recipe v1."""

    normalized = _validate_for_helpers(profile)
    model = normalized["model"]
    return {
        "profileId": normalized["profileId"],
        "displayName": normalized["displayName"],
        "versionName": model["versionName"],
        "versionId": model["versionId"],
        "baseModel": model["baseModel"],
        "checkpoint": deepcopy(model["checkpoint"]),
    }


def profile_default_parameters(profile: object) -> dict[str, Any]:
    """Return only genuine model defaults, never a speculative size or seed."""

    normalized = _validate_for_helpers(profile)
    return deepcopy(normalized["parameters"]["defaults"])


def validated_resolution_presets(profile: object) -> list[dict[str, Any]]:
    """Return only locally validated presets eligible for auto recommendation."""

    normalized = _validate_for_helpers(profile)
    return deepcopy(normalized["resolutions"]["validatedPresets"])


def candidate_resolution_presets(profile: object) -> list[dict[str, Any]]:
    normalized = _validate_for_helpers(profile)
    return deepcopy(normalized["resolutions"]["candidatePresets"])


def compile_positive_prefix(
    profile: object, *, optional_enhancements: list[str] | tuple[str, ...] = ()
) -> str:
    """Compile the fixed prefix plus explicitly selected allowed enhancements."""

    normalized = _validate_for_helpers(profile)
    available = normalized["prompting"]["optionalEnhancements"]
    selected = _string_list(list(optional_enhancements), "optional_enhancements")
    unknown = [item for item in selected if item not in available]
    if unknown:
        raise ModelProfileError(
            "unknown optional prompt enhancements: " + ", ".join(unknown)
        )
    return ", ".join(normalized["prompting"]["positivePrefix"] + selected)


def canonical_model_profile_json(profile: object) -> str:
    return json.dumps(
        _validate_for_helpers(profile),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def model_profile_hash(profile: object) -> str:
    return hashlib.sha256(
        canonical_model_profile_json(profile).encode("utf-8")
    ).hexdigest()


__all__ = [
    "DEFAULT_PROFILE_DIRECTORY",
    "DEFAULT_PROFILE_ID",
    "ModelProfileError",
    "PROFILE_KIND",
    "PROFILE_SCHEMA_VERSION",
    "candidate_resolution_presets",
    "canonical_model_profile_json",
    "compile_positive_prefix",
    "list_model_profiles",
    "load_model_profile",
    "model_profile_hash",
    "model_reference",
    "profile_default_parameters",
    "validate_model_profile",
    "validate_researched_model_profile",
    "validated_resolution_presets",
]
