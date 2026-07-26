"""Evidence-backed semantic and model-adaptation decomposition boundary."""

from __future__ import annotations

import json
import re
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Callable, Mapping, Sequence

from creative_intake import canonical_brief_sha256
from recipe import BLOCK_DEFINITIONS, BLOCK_IDS


_PROMPT_PATH = (
    Path(__file__).resolve().parent
    / "prompts"
    / "text"
    / "model_adapted_decomposition.md"
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BLOCK_LABELS = {definition.id: definition.label for definition in BLOCK_DEFINITIONS}
_CATEGORY_TO_BLOCK = {
    "identity": "identity",
    "subject": "identity",
    "appearance": "appearance",
    "clothing": "clothing",
    "outfit": "clothing",
    "expression": "expression",
    "emotion": "expression",
    "action": "action",
    "pose": "action",
    "interaction": "interaction",
    "scene": "scene",
    "environment": "scene",
    "camera": "camera",
    "composition": "camera",
    "lighting": "lighting",
    "style": "style",
    "artist": "style",
    "effects": "effects",
    "props": "effects",
    "quality": "quality",
    "negative": "negative",
    "人物身份": "identity",
    "身体与外观特征": "appearance",
    "服装与配饰": "clothing",
    "表情": "expression",
    "姿势与动作": "action",
    "人物互动关系": "interaction",
    "场景与环境": "scene",
    "构图与镜头": "camera",
    "光影与色彩": "lighting",
    "风格与艺术家": "style",
    "道具与特效": "effects",
    "质量增强": "quality",
    "负向约束": "negative",
}
_BLOCK_FIELDS = {
    "id",
    "category",
    "zh",
    "en",
    "source",
    "locked",
    "approved",
    "reason",
    "risks",
    "ruleRefs",
    "semanticItemIds",
}
_REPAIRABLE_CODES = {"invalid_provider_output", "invalid_blocks"}
_GENERIC_FALLBACK_RISK = "generic_fallback_no_approved_model_rule"


class DecompositionError(ValueError):
    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise DecompositionError(message, code=code)


def _normalized_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value)


def _safe_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        _fail("invalid_provider_output", f"{label} must be a safe identifier")
    return value


def _text(value: object, label: str, *, maximum: int = 200_000) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        _fail("invalid_provider_output", f"{label} must be text")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        _fail("invalid_provider_output", f"{label} must be valid Unicode")
    return value


def _source(value: object, label: str) -> dict:
    if not isinstance(value, Mapping) or set(value) != {"type", "refId"}:
        _fail("invalid_provider_output", f"{label} is invalid")
    source_type = value.get("type")
    if source_type not in {"user", "image", "ai"}:
        _fail("invalid_provider_output", f"{label}.type is invalid")
    ref_id = value.get("refId")
    if ref_id is not None:
        ref_id = _safe_identifier(ref_id, f"{label}.refId")
    if source_type == "image" and ref_id is None:
        _fail("invalid_provider_output", f"{label}.refId is required")
    if source_type != "image" and ref_id is not None:
        _fail("invalid_provider_output", f"{label}.refId must be null")
    return {"type": source_type, "refId": ref_id}


def _trusted_rules(
    approved_rules: Sequence[Mapping[str, object]],
) -> dict[str, dict]:
    if not isinstance(approved_rules, Sequence) or isinstance(
        approved_rules, (str, bytes)
    ):
        _fail("invalid_provider_output", "approvedRules must be an array")
    trusted = {}
    for index, raw in enumerate(approved_rules):
        if not isinstance(raw, Mapping):
            _fail("invalid_provider_output", f"approvedRules[{index}] is invalid")
        claim_id = _safe_identifier(raw.get("claimId"), "approvedRules.claimId")
        if claim_id in trusted:
            _fail("invalid_provider_output", "approvedRules contains duplicate claims")
        field_path = _text(
            raw.get("fieldPath"), "approvedRules.fieldPath", maximum=512
        )
        raw_refs = raw.get("evidenceRefs")
        if not isinstance(raw_refs, list) or not raw_refs:
            _fail(
                "invalid_provider_output",
                "approvedRules.evidenceRefs must be a non-empty array",
            )
        evidence_refs = [
            _safe_identifier(ref, "approvedRules.evidenceRefs") for ref in raw_refs
        ]
        if len(evidence_refs) != len(set(evidence_refs)):
            _fail(
                "invalid_provider_output",
                "approvedRules.evidenceRefs contains duplicates",
            )
        trusted[claim_id] = {
            "claimId": claim_id,
            "fieldPath": field_path,
            "evidenceRefs": evidence_refs,
        }
    return trusted


def _semantic_items(brief: Mapping[str, object]) -> list[dict]:
    raw_items = brief.get("items")
    if not isinstance(raw_items, list):
        _fail("invalid_provider_output", "brief.items must be an array")
    items = []
    seen = set()
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, Mapping):
            _fail("invalid_provider_output", f"brief.items[{index}] is invalid")
        item_id = _safe_identifier(raw.get("id"), f"brief.items[{index}].id")
        if item_id in seen:
            _fail("invalid_provider_output", "brief item IDs must be unique")
        category = raw.get("category")
        if not isinstance(category, str) or category not in _CATEGORY_TO_BLOCK:
            _fail(
                "semantic_violation",
                f"brief item {item_id} has no canonical semantic category",
            )
        source = _source(raw.get("source"), f"brief.items[{index}].source")
        locked = raw.get("locked")
        if not isinstance(locked, bool):
            _fail("invalid_provider_output", f"brief.items[{index}].locked is invalid")
        items.append(
            {
                "id": item_id,
                "category": category,
                "text": _text(raw.get("text"), f"brief.items[{index}].text"),
                "source": source,
                "locked": locked,
            }
        )
        seen.add(item_id)
    additions = brief.get("aiAdditions")
    if not isinstance(additions, list):
        _fail("invalid_provider_output", "brief.aiAdditions must be an array")
    for index, addition in enumerate(additions):
        item_id = f"ai-addition-{index}"
        if item_id in seen:
            _fail("invalid_provider_output", "semantic item IDs must be unique")
        items.append(
            {
                "id": item_id,
                "category": None,
                "text": _text(addition, f"brief.aiAdditions[{index}]"),
                "source": {"type": "ai", "refId": None},
                "locked": False,
            }
        )
        seen.add(item_id)
    return items


def build_decomposition_messages(
    brief: Mapping[str, object],
    approved_rules: Sequence[Mapping[str, object]],
    warnings: Sequence[Mapping[str, object]],
) -> list[dict]:
    """Build messages with approved rules and warnings in separate namespaces."""
    if not isinstance(brief, Mapping):
        _fail("invalid_provider_output", "brief must be an object")
    if not isinstance(warnings, Sequence) or isinstance(warnings, (str, bytes)):
        _fail("invalid_provider_output", "warnings must be an array")
    payload = {
        "task": "generate_model_adapted_decomposition",
        "brief": deepcopy(dict(brief)),
        "semanticItems": _semantic_items(brief),
        "approvedRules": deepcopy(list(approved_rules)),
        "warnings": deepcopy(list(warnings)),
        "blockDefinitions": [
            {"id": definition.id, "category": definition.label}
            for definition in BLOCK_DEFINITIONS
        ],
    }
    return [
        {"role": "system", "content": _PROMPT_PATH.read_text(encoding="utf-8")},
        {
            "role": "user",
            "content": json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
        },
    ]


def _provider_object(value: object) -> Mapping[str, object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            _fail("invalid_provider_output", "provider did not return valid JSON")
    if not isinstance(value, Mapping):
        _fail("invalid_provider_output", "provider output must be one JSON object")
    return value


def _validate_context(
    intake: Mapping[str, object], profile_snapshot: Mapping[str, object]
) -> tuple[Mapping[str, object], dict[str, dict]]:
    if not isinstance(intake, Mapping) or intake.get("stage") != "model_selected":
        _fail("invalid_provider_output", "intake must be at model_selected")
    brief = intake.get("brief")
    if not isinstance(brief, Mapping) or brief.get("status") != "confirmed":
        _fail("invalid_provider_output", "intake brief must be confirmed")
    if intake.get("selectedModelProfileId") != profile_snapshot.get("profileId"):
        _fail("invalid_provider_output", "selected profile does not match snapshot")
    version_id = profile_snapshot.get("profileVersionId")
    content_hash = profile_snapshot.get("profileContentSha256")
    _safe_identifier(version_id, "profileVersionId")
    if not isinstance(content_hash, str) or not _SHA256.fullmatch(content_hash):
        _fail("invalid_provider_output", "profileContentSha256 is invalid")
    rules = _trusted_rules(profile_snapshot.get("approvedRules", []))
    return brief, rules


def _model_terms(profile_snapshot: Mapping[str, object]) -> set[str]:
    profile = profile_snapshot.get("profile")
    values = [profile_snapshot.get("profileId")]
    if isinstance(profile, Mapping):
        values.extend(
            profile.get(key)
            for key in ("id", "displayName", "baseModel", "versionName")
        )
    return {
        _normalized_text(value).casefold()
        for value in values
        if isinstance(value, str) and len(value.strip()) >= 3
    }


def _semantic_content(value: str) -> str:
    return "".join(
        character
        for character in _normalized_text(value).casefold()
        if character.isalnum()
    )


def normalize_decomposition_output(
    value: object,
    *,
    intake: Mapping[str, object],
    profile_snapshot: Mapping[str, object],
) -> dict:
    """Validate provider output and rebuild lineage from trusted arguments."""
    brief, trusted_rules = _validate_context(intake, profile_snapshot)
    raw = _provider_object(value)
    raw_blocks = raw.get("blocks")
    if not isinstance(raw_blocks, list):
        _fail("invalid_provider_output", "blocks must be an array")
    raw_ids = [
        block.get("id") if isinstance(block, Mapping) else None
        for block in raw_blocks
    ]
    if tuple(raw_ids) != BLOCK_IDS:
        _fail(
            "invalid_blocks",
            "provider must return exactly thirteen canonical blocks in order",
        )

    semantic_items = _semantic_items(brief)
    items_by_id = {item["id"]: item for item in semantic_items}

    normalized_blocks = []
    semantic_locations: dict[str, list[str]] = {}
    forbidden_model_terms = _model_terms(profile_snapshot)
    for index, raw_block in enumerate(raw_blocks):
        if not isinstance(raw_block, Mapping) or set(raw_block) != _BLOCK_FIELDS:
            _fail(
                "invalid_provider_output",
                f"blocks[{index}] fields do not match the output contract",
            )
        block_id = raw_ids[index]
        if raw_block.get("category") != _BLOCK_LABELS[block_id]:
            _fail("invalid_blocks", f"{block_id} category does not match")
        semantic_ids = raw_block.get("semanticItemIds")
        if not isinstance(semantic_ids, list):
            _fail("invalid_provider_output", "semanticItemIds must be an array")
        normalized_semantic_ids = [
            _safe_identifier(item_id, "semanticItemIds") for item_id in semantic_ids
        ]
        if len(normalized_semantic_ids) != len(set(normalized_semantic_ids)):
            _fail("locked_fact_changed", "semantic item occurs more than once in a block")
        for item_id in normalized_semantic_ids:
            if item_id not in items_by_id:
                _fail("invalid_provider_output", "semanticItemIds contains unknown item")
            semantic_locations.setdefault(item_id, []).append(block_id)

        rule_refs = raw_block.get("ruleRefs")
        if not isinstance(rule_refs, list):
            _fail("invalid_provider_output", "ruleRefs must be an array")
        normalized_refs = []
        seen_claims = set()
        for raw_ref in rule_refs:
            if (
                not isinstance(raw_ref, Mapping)
                or set(raw_ref) != {"claimId", "fieldPath", "evidenceRefs"}
            ):
                _fail("unapproved_rule", "rule reference shape is invalid")
            claim_id = raw_ref.get("claimId")
            trusted = trusted_rules.get(claim_id)
            if (
                trusted is None
                or claim_id in seen_claims
                or raw_ref.get("fieldPath") != trusted["fieldPath"]
                or raw_ref.get("evidenceRefs") != trusted["evidenceRefs"]
            ):
                _fail(
                    "unapproved_rule",
                    "rule reference is not an exact approved rule",
                )
            seen_claims.add(claim_id)
            normalized_refs.append(deepcopy(trusted))

        risks = raw_block.get("risks")
        if (
            not isinstance(risks, list)
            or len(risks) > 100
            or any(
                not isinstance(risk, str) or not risk for risk in risks
            )
        ):
            _fail("invalid_provider_output", "risks must contain non-empty text")
        if len(risks) != len(set(risks)):
            _fail("invalid_provider_output", "risks must not contain duplicates")
        zh = _text(raw_block.get("zh"), f"blocks[{index}].zh")
        normalized_zh = _normalized_text(zh).casefold()
        if any(term in normalized_zh for term in forbidden_model_terms):
            _fail(
                "semantic_violation",
                "semantic zh must not contain target-model terminology",
            )
        en = _text(raw_block.get("en"), f"blocks[{index}].en")
        reason = _text(raw_block.get("reason"), f"blocks[{index}].reason")
        normalized_risks = [
            risk for risk in risks if risk != _GENERIC_FALLBACK_RISK
        ]
        if en and not normalized_refs:
            if len(normalized_risks) >= 100:
                _fail(
                    "invalid_provider_output",
                    "generic fallback risk exceeds the 100-item limit",
                )
            normalized_risks.append(_GENERIC_FALLBACK_RISK)
        locked = raw_block.get("locked")
        approved = raw_block.get("approved")
        if not isinstance(locked, bool) or approved is not False:
            _fail(
                "invalid_provider_output",
                "locked must be boolean and approved must be false",
            )
        normalized_blocks.append(
            {
                "id": block_id,
                "category": _BLOCK_LABELS[block_id],
                "zh": zh,
                "en": en,
                "source": _source(raw_block.get("source"), f"blocks[{index}].source"),
                "locked": locked,
                "approved": False,
                "reason": reason,
                "risks": normalized_risks,
                "ruleRefs": normalized_refs,
                "semanticItems": [
                    {
                        "id": item_id,
                        "text": items_by_id[item_id]["text"],
                        "source": deepcopy(items_by_id[item_id]["source"]),
                        "locked": items_by_id[item_id]["locked"],
                    }
                    for item_id in normalized_semantic_ids
                ],
            }
        )

    for item_id, item in items_by_id.items():
        expected_block = _CATEGORY_TO_BLOCK.get(item.get("category"))
        locations = semantic_locations.get(item_id, [])
        fact_text = item.get("text")
        wrong_location = (
            locations != [expected_block]
            if expected_block is not None
            else len(locations) != 1
        )
        if wrong_location or not isinstance(fact_text, str):
            _fail(
                "locked_fact_changed" if item["locked"] else "semantic_violation",
                f"confirmed brief fact {item_id} was omitted, duplicated, or relocated",
            )
        target_id = locations[0]
        target = normalized_blocks[BLOCK_IDS.index(target_id)]
        if _normalized_text(fact_text) not in _normalized_text(target["zh"]):
            _fail(
                "locked_fact_changed" if item["locked"] else "semantic_violation",
                f"confirmed brief fact {item_id} was not preserved verbatim",
            )

    for block, raw_block in zip(normalized_blocks, raw_blocks):
        semantic_ids = raw_block["semanticItemIds"]
        referenced = [items_by_id[item_id] for item_id in semantic_ids]
        expected_content = "".join(_semantic_content(item["text"]) for item in referenced)
        if _semantic_content(block["zh"]) != expected_content:
            _fail(
                "semantic_violation",
                f"{block['id']} semantic zh contains unconfirmed meaning",
            )
        expected_source = (
            referenced[0]["source"]
            if referenced
            else {"type": "ai", "refId": None}
        )
        expected_locked = any(item["locked"] for item in referenced)
        if block["source"] != expected_source or block["locked"] != expected_locked:
            code = "locked_fact_changed" if expected_locked else "semantic_violation"
            _fail(code, f"{block['id']} semantic provenance or lock is invalid")

    return {
        "status": "draft",
        "briefContentSha256": canonical_brief_sha256(brief),
        "profileVersionId": profile_snapshot["profileVersionId"],
        "profileContentSha256": profile_snapshot["profileContentSha256"],
        "blocks": normalized_blocks,
    }


def generate_model_adapted_decomposition(
    *,
    intake: Mapping[str, object],
    profile_snapshot: Mapping[str, object],
    provider: Callable[[list[dict]], object],
) -> dict:
    """Generate a preview with at most one repair call for malformed output."""
    brief, _ = _validate_context(intake, profile_snapshot)
    messages = build_decomposition_messages(
        brief,
        profile_snapshot.get("approvedRules", []),
        profile_snapshot.get("warnings", []),
    )
    try:
        first = provider(deepcopy(messages))
    except Exception as exc:
        raise DecompositionError("model adaptation provider failed", code="provider_failed") from exc
    try:
        return normalize_decomposition_output(
            first, intake=intake, profile_snapshot=profile_snapshot
        )
    except DecompositionError as first_error:
        if first_error.code not in _REPAIRABLE_CODES:
            raise

    repair = {
        "task": "repair_model_adapted_decomposition_json",
        "errorCode": "invalid_provider_output",
        "instruction": "Return one corrected JSON object; do not change semantic facts.",
    }
    repair_messages = [
        *messages,
        {
            "role": "user",
            "content": json.dumps(
                repair, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
        },
    ]
    try:
        repaired = provider(repair_messages)
    except Exception as exc:
        raise DecompositionError("model adaptation provider failed", code="provider_failed") from exc
    try:
        return normalize_decomposition_output(
            repaired, intake=intake, profile_snapshot=profile_snapshot
        )
    except DecompositionError as repaired_error:
        if repaired_error.code in _REPAIRABLE_CODES:
            raise DecompositionError(
                "provider did not return a valid decomposition",
                code="invalid_provider_output",
            ) from repaired_error
        raise


__all__ = [
    "DecompositionError",
    "build_decomposition_messages",
    "generate_model_adapted_decomposition",
    "normalize_decomposition_output",
]
