"""Deterministic, advisory prompt diagnostics.

These checks deliberately never rewrite a Recipe.  They make simple prompt
quality risks visible while keeping the user's chosen text authoritative.
"""

from __future__ import annotations

import re
import os
from collections.abc import Mapping
from functools import lru_cache

from tag_semantics import diagnose_tags, load_catalog


DIAGNOSTIC_SCHEMA_VERSION = 1
COMPOSITION_RULES_VERSION = "composition-rules-v1"
TAG_DENSITY_RANGE = (30, 50)
_TAG_SPLIT = re.compile(r"[,，\n;；]+")
_ABSTRACT_TERMS = frozenset(
    {
        "amazing",
        "beautiful",
        "cinematic atmosphere",
        "perfect composition",
    }
)
_COMPOSITION_GROUPS = {
    "shotScale": frozenset(
        {"close-up", "portrait", "upper body", "cowboy shot", "full body"}
    ),
    "viewAngle": frozenset(
        {"from above", "from below", "bird's-eye view", "worm's-eye view"}
    ),
    "facing": frozenset({"front view", "side view", "back view"}),
}


def split_tags(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    return [item.strip() for item in _TAG_SPLIT.split(value) if item.strip()]


@lru_cache(maxsize=1)
def _load_local_tokenizer(tokenizer_path: str):
    """Load only an explicitly configured local tokenizer, never from network."""

    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(
        tokenizer_path,
        local_files_only=True,
        trust_remote_code=False,
    )


def real_token_count(prompt: str) -> tuple[int | None, str]:
    """Return an exact count only when a local tokenizer can prove it."""

    tokenizer_path = os.environ.get("PROMPT_STUDIO_TOKENIZER_PATH", "").strip()
    if not tokenizer_path:
        return None, "unavailable_without_model_tokenizer"
    try:
        tokenizer = _load_local_tokenizer(tokenizer_path)
        token_ids = tokenizer.encode(prompt, add_special_tokens=False)
    except (ImportError, OSError, ValueError, TypeError):
        return None, "configured_tokenizer_unavailable"
    if not isinstance(token_ids, list) or not all(isinstance(item, int) for item in token_ids):
        return None, "configured_tokenizer_invalid_output"
    return len(token_ids), "ready"


def subject_token_position(recipe: Mapping[str, object]) -> dict:
    """Locate a unique subject tag using the configured local tokenizer only."""

    prompts = recipe.get("prompts")
    blocks = recipe.get("blocks")
    positive_en = prompts.get("positiveEn", "") if isinstance(prompts, Mapping) else ""
    subject = next(
        (
            block
            for block in blocks if isinstance(block, Mapping) and block.get("id") == "subject"
        ),
        {},
    ) if isinstance(blocks, list) else {}
    subject_tags = split_tags(subject.get("en") if isinstance(subject, Mapping) else "")
    if not subject_tags:
        return {"status": "subject_missing", "tokenStart": None, "anchorTag": None}

    positive_lower = positive_en.lower()
    anchor = next(
        (
            tag
            for tag in subject_tags
            if positive_lower.count(tag.lower()) == 1
        ),
        None,
    )
    if not anchor:
        return {
            "status": "subject_anchor_not_unique",
            "tokenStart": None,
            "anchorTag": None,
        }
    token_start, status = real_token_count(positive_en[:positive_lower.index(anchor.lower())])
    if status != "ready":
        return {"status": status, "tokenStart": None, "anchorTag": anchor}
    return {"status": "ready", "tokenStart": token_start, "anchorTag": anchor}


def prompt_diagnostics(recipe: Mapping[str, object]) -> dict:
    """Return stable, non-blocking diagnostics for a normalized Recipe."""

    prompts = recipe.get("prompts")
    blocks = recipe.get("blocks")
    positive = split_tags(prompts.get("positiveEn") if isinstance(prompts, Mapping) else "")
    camera = next(
        (
            block
            for block in blocks if isinstance(block, Mapping) and block.get("id") == "camera"
        ),
        {},
    ) if isinstance(blocks, list) else {}
    camera_tags = split_tags(camera.get("en") if isinstance(camera, Mapping) else "")
    normalized_camera = {item.casefold(): item for item in camera_tags}
    conflicts = []
    for group, members in _COMPOSITION_GROUPS.items():
        selected = [normalized_camera[item] for item in sorted(members) if item in normalized_camera]
        if len(selected) > 1:
            conflicts.append(
                {
                    "group": group,
                    "tags": selected,
                    "message": f"{group} contains mutually exclusive composition tags",
                }
            )
    abstract = [item for item in positive if item.casefold() in _ABSTRACT_TERMS]
    tag_count = len(positive)
    semantic = diagnose_tags(positive, load_catalog())
    token_count, token_count_status = real_token_count(
        prompts.get("positiveEn", "") if isinstance(prompts, Mapping) else ""
    )
    return {
        "schemaVersion": DIAGNOSTIC_SCHEMA_VERSION,
        "compositionRulesVersion": COMPOSITION_RULES_VERSION,
        "tagCount": tag_count,
        "tagDensity": {
            "minimum": TAG_DENSITY_RANGE[0],
            "maximum": TAG_DENSITY_RANGE[1],
            "status": (
                "low" if tag_count < TAG_DENSITY_RANGE[0]
                else "high" if tag_count > TAG_DENSITY_RANGE[1]
                else "balanced"
            ),
        },
        "compositionConflicts": conflicts,
        "abstractTerms": abstract,
        "tagSemantics": semantic,
        "tokenCount": token_count,
        "tokenCountStatus": token_count_status,
        "subjectPosition": subject_token_position(recipe),
    }


__all__ = [
    "COMPOSITION_RULES_VERSION",
    "DIAGNOSTIC_SCHEMA_VERSION",
    "TAG_DENSITY_RANGE",
    "prompt_diagnostics",
    "real_token_count",
    "split_tags",
    "subject_token_position",
]
