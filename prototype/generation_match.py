"""Advisory literal comparison between a prompt and local image analysis."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_SPLIT = re.compile(r"[,;\n\r，；]+")


def _tags(prompt: str) -> list[str]:
    values = []
    seen = set()
    for part in _SPLIT.split(prompt):
        tag = part.strip().strip("() ")
        key = tag.casefold()
        if not tag or len(tag) > 160 or key in seen:
            continue
        seen.add(key)
        values.append(tag)
    return values


def prompt_match_diagnostics(prompt: str, consensus: Mapping[str, Any]) -> dict:
    """Compare literal tags only; visual evidence never changes the prompt."""

    if not isinstance(prompt, str) or len(prompt) > 20_000:
        raise ValueError("expected prompt is invalid")
    items = consensus.get("items") if isinstance(consensus, Mapping) else None
    if not isinstance(items, list):
        raise ValueError("analysis consensus is invalid")
    observed = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        tag = item.get("tag")
        status = item.get("status")
        if isinstance(tag, str) and isinstance(status, str):
            observed[tag.casefold()] = status
    expected = _tags(prompt)
    groups = {"confirmed": [], "uncertain": [], "conflict": [], "unobserved": []}
    for tag in expected:
        status = observed.get(tag.casefold())
        if status in groups and status != "unobserved":
            groups[status].append(tag)
        else:
            groups["unobserved"].append(tag)
    count = len(expected)
    return {
        "advisory": True,
        "comparison": "literal_tag_only",
        "expectedTagCount": count,
        "confirmed": groups["confirmed"],
        "uncertain": groups["uncertain"],
        "conflict": groups["conflict"],
        "unobserved": groups["unobserved"],
        "confirmedMatchRate": len(groups["confirmed"]) / count if count else None,
        "possibleMatchRate": (
            (len(groups["confirmed"]) + len(groups["uncertain"])) / count
            if count
            else None
        ),
    }


__all__ = ["prompt_match_diagnostics"]
