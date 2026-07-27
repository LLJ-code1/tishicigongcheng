"""Validation for review-only random-wordlist addition proposals."""

from __future__ import annotations

import re
from typing import Any, Mapping

from random_sampler import load_catalog


class WordlistProposalError(ValueError):
    pass


_PROPOSAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
_ASCII_ENTRY = re.compile(r"^[\x21-\x7e](?:[\x20-\x7e]{0,510}[\x21-\x7e])?$")
_STATUSES = {"submitted", "published", "rejected"}
_MAX_PROPOSALS = 200


def normalize_wordlist_proposals(
    value: object,
    *,
    project_id: str,
    submitted_at: str,
) -> list[dict[str, Any]]:
    """Normalize metadata proposals without ever writing a wordlist source file."""

    if value is None:
        return []
    if not isinstance(value, list) or len(value) > _MAX_PROPOSALS:
        raise WordlistProposalError("wordlistProposals must contain at most 200 items")
    catalog = load_catalog(experimental=True)
    categories = {item.category_id: item for item in catalog.categories}
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise WordlistProposalError(f"wordlistProposals[{index}] must be an object")
        proposal_id = raw.get("id")
        category_id = raw.get("categoryId")
        target_block = raw.get("targetBlock")
        text = raw.get("text")
        if not isinstance(proposal_id, str) or not _PROPOSAL_ID.fullmatch(proposal_id):
            raise WordlistProposalError(f"wordlistProposals[{index}].id is invalid")
        if proposal_id in seen_ids:
            raise WordlistProposalError("wordlistProposals IDs must be unique")
        seen_ids.add(proposal_id)
        category = categories.get(category_id)
        if category is None:
            raise WordlistProposalError(f"wordlistProposals[{index}].categoryId is unknown")
        if target_block not in category.allowed_blocks:
            raise WordlistProposalError(f"wordlistProposals[{index}].targetBlock is not allowed")
        if (
            not isinstance(text, str)
            or not _ASCII_ENTRY.fullmatch(text)
            or "," in text
            or ";" in text
        ):
            raise WordlistProposalError(
                f"wordlistProposals[{index}].text must be one printable ASCII prompt literal"
            )
        status = raw.get("status", "submitted")
        if status not in _STATUSES:
            raise WordlistProposalError(f"wordlistProposals[{index}].status is invalid")
        timestamp = raw.get("submittedAt") or submitted_at
        if not isinstance(timestamp, str) or not timestamp or len(timestamp) > 64:
            raise WordlistProposalError(f"wordlistProposals[{index}].submittedAt is invalid")
        item = {
            "id": proposal_id,
            "categoryId": category_id,
            "targetBlock": target_block,
            "text": text,
            "projectId": project_id,
            "submittedAt": timestamp,
            "status": status,
        }
        if status == "published":
            version = raw.get("publishedCatalogVersion")
            if not isinstance(version, str) or not version:
                raise WordlistProposalError(
                    f"wordlistProposals[{index}].publishedCatalogVersion is required"
                )
            item["publishedCatalogVersion"] = version
        result.append(item)
    return result
