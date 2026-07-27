"""Offline, version-pinned tag semantics for advisory prompt diagnostics.

No runtime network access is permitted.  A catalog is accepted only when its
source license and redistribution review have both been recorded locally.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping


SEMANTIC_SCHEMA_VERSION = 1
DEFAULT_LOW_FREQUENCY_POST_COUNT = 100
_TAG_RE = re.compile(r"^.{1,512}$", re.DOTALL)


class TagSemanticError(ValueError):
    pass


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _TAG_RE.fullmatch(value.strip()):
        raise TagSemanticError(f"{label} is invalid")
    return value.strip()


def normalize_catalog(value: Any) -> dict:
    if not isinstance(value, Mapping) or set(value) != {
        "schemaVersion", "version", "sourceReview", "policy", "tags", "conflictRules"
    }:
        raise TagSemanticError("semantic catalog fields are invalid")
    if value["schemaVersion"] != SEMANTIC_SCHEMA_VERSION:
        raise TagSemanticError("semantic catalog schemaVersion is unsupported")
    review = value["sourceReview"]
    if not isinstance(review, Mapping) or set(review) != {
        "licenseReviewed", "redistributionApproved"
    } or review["licenseReviewed"] is not True or review["redistributionApproved"] is not True:
        raise TagSemanticError("semantic catalog source review is incomplete")
    policy = value["policy"]
    if not isinstance(policy, Mapping) or set(policy) != {"lowFrequencyPostCount"}:
        raise TagSemanticError("semantic catalog policy is invalid")
    low_count = policy["lowFrequencyPostCount"]
    if isinstance(low_count, bool) or not isinstance(low_count, int) or low_count < 0:
        raise TagSemanticError("semantic catalog lowFrequencyPostCount is invalid")
    tags = value["tags"]
    if not isinstance(tags, list) or len(tags) > 200_000:
        raise TagSemanticError("semantic catalog tags are invalid")
    normalized_tags = []
    canonical_seen = set()
    aliases_seen = set()
    for raw in tags:
        if not isinstance(raw, Mapping) or set(raw) != {
            "canonical", "aliases", "implications", "postCount", "category"
        }:
            raise TagSemanticError("semantic catalog tag fields are invalid")
        canonical = _text(raw["canonical"], "canonical tag").casefold()
        if canonical in canonical_seen or canonical in aliases_seen:
            raise TagSemanticError("semantic catalog canonical tag is duplicated")
        aliases = raw["aliases"]
        implications = raw["implications"]
        if not isinstance(aliases, list) or not isinstance(implications, list):
            raise TagSemanticError("semantic catalog aliases or implications are invalid")
        aliases_normalized = [_text(item, "alias").casefold() for item in aliases]
        if len(set(aliases_normalized)) != len(aliases_normalized):
            raise TagSemanticError("semantic catalog alias is duplicated")
        if canonical in aliases_normalized or any(alias in aliases_seen for alias in aliases_normalized):
            raise TagSemanticError("semantic catalog alias conflicts with canonical tag")
        implication_normalized = [_text(item, "implication").casefold() for item in implications]
        if len(set(implication_normalized)) != len(implication_normalized):
            raise TagSemanticError("semantic catalog implication is duplicated")
        post_count = raw["postCount"]
        if isinstance(post_count, bool) or not isinstance(post_count, int) or post_count < 0:
            raise TagSemanticError("semantic catalog postCount is invalid")
        canonical_seen.add(canonical)
        aliases_seen.update(aliases_normalized)
        normalized_tags.append(
            {
                "canonical": canonical,
                "aliases": aliases_normalized,
                "implications": implication_normalized,
                "postCount": post_count,
                "category": _text(raw["category"], "category"),
            }
        )
    conflicts = value["conflictRules"]
    if not isinstance(conflicts, list) or len(conflicts) > 100_000:
        raise TagSemanticError("semantic catalog conflictRules are invalid")
    normalized_conflicts = []
    for raw in conflicts:
        if not isinstance(raw, Mapping) or set(raw) != {"left", "right", "decision"}:
            raise TagSemanticError("semantic conflict rule fields are invalid")
        left = _text(raw["left"], "conflict left").casefold()
        right = _text(raw["right"], "conflict right").casefold()
        if left == right:
            raise TagSemanticError("semantic conflict rule cannot reference itself")
        decision = _text(raw["decision"], "conflict decision")
        normalized_conflicts.append({"left": left, "right": right, "decision": decision})
    return {
        "schemaVersion": SEMANTIC_SCHEMA_VERSION,
        "version": _text(value["version"], "semantic catalog version"),
        "sourceReview": {"licenseReviewed": True, "redistributionApproved": True},
        "policy": {"lowFrequencyPostCount": low_count},
        "tags": normalized_tags,
        "conflictRules": normalized_conflicts,
    }


def load_catalog(path: Path | str | None = None) -> dict | None:
    candidate = path or os.environ.get("PROMPT_STUDIO_SEMANTIC_CATALOG")
    if not candidate:
        return None
    try:
        raw = json.loads(Path(candidate).read_text(encoding="utf-8"))
        return normalize_catalog(raw)
    except (OSError, json.JSONDecodeError, TagSemanticError):
        return None


def diagnose_tags(tags: list[str], catalog: Mapping[str, Any] | None = None) -> dict:
    """Classify tags and return advisory alias/implication/conflict suggestions."""

    if catalog is None:
        return {
            "catalogStatus": "unavailable",
            "catalogVersion": None,
            "tags": [{"tag": item, "status": "unclassified"} for item in tags],
            "suggestions": [],
        }
    normalized = normalize_catalog(catalog)
    aliases: dict[str, str] = {}
    records: dict[str, dict] = {}
    for item in normalized["tags"]:
        records[item["canonical"]] = item
        aliases[item["canonical"]] = item["canonical"]
        aliases.update({alias: item["canonical"] for alias in item["aliases"]})
    canonical_for_input = [aliases.get(item.casefold()) for item in tags]
    selected = {item for item in canonical_for_input if item}
    low_count = normalized["policy"]["lowFrequencyPostCount"]
    classified = []
    suggestions = []
    for original, canonical in zip(tags, canonical_for_input):
        if canonical is None:
            classified.append({"tag": original, "status": "unknown"})
            continue
        record = records[canonical]
        status = "low_frequency" if record["postCount"] < low_count else "normal"
        classified.append(
            {
                "tag": original,
                "canonical": canonical,
                "status": status,
                "postCount": record["postCount"],
                "category": record["category"],
            }
        )
        if original.casefold() != canonical:
            suggestions.append({"kind": "alias_merge", "from": original, "to": canonical, "action": "merge"})
        for implied in record["implications"]:
            if implied in selected:
                suggestions.append({"kind": "implication_duplicate", "from": canonical, "to": implied, "action": "manual_decision"})
    for rule in normalized["conflictRules"]:
        if rule["left"] in selected and rule["right"] in selected:
            suggestions.append({"kind": "semantic_conflict", **rule, "action": "manual_decision"})
    return {
        "catalogStatus": "ready",
        "catalogVersion": normalized["version"],
        "tags": classified,
        "suggestions": suggestions,
    }


__all__ = ["DEFAULT_LOW_FREQUENCY_POST_COUNT", "TagSemanticError", "diagnose_tags", "load_catalog", "normalize_catalog"]
