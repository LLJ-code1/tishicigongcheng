"""Minimal, reproducible literal inline variants for Prompt Studio recipes."""

from __future__ import annotations

from typing import Any, Mapping


class InlineVariantError(ValueError):
    """Raised when a prompt uses syntax outside the supported literal subset."""


MAX_VARIANTS_PER_FIELD = 64
MAX_CANDIDATES_PER_VARIANT = 32


def _selection_list(value: object, field: str) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_VARIANTS_PER_FIELD:
        raise InlineVariantError(f"{field} selections must be an array of at most 64 indexes")
    result: list[int] = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise InlineVariantError(f"{field} selections[{index}] must be a non-negative integer")
        result.append(item)
    return result


def resolve_inline_variants(
    text: str,
    *,
    selections: object = None,
    field: str = "prompt",
) -> tuple[str, list[dict[str, Any]]]:
    """Resolve only flat ``{a|b|c}`` literals and retain their exact evidence."""

    if not isinstance(text, str):
        raise InlineVariantError(f"{field} must be a string")
    chosen = _selection_list(selections, field)
    resolved: list[str] = []
    variants: list[dict[str, Any]] = []
    position = 0
    while position < len(text):
        character = text[position]
        if character == "}":
            raise InlineVariantError(f"{field} contains an unmatched closing brace")
        if character != "{":
            resolved.append(character)
            position += 1
            continue
        close = text.find("}", position + 1)
        if close < 0:
            raise InlineVariantError(f"{field} contains an unclosed inline variant")
        inner = text[position + 1 : close]
        if "{" in inner or "}" in inner:
            raise InlineVariantError(f"{field} inline variants cannot be nested")
        candidates = inner.split("|")
        if (
            len(candidates) < 2
            or len(candidates) > MAX_CANDIDATES_PER_VARIANT
            or any(not candidate.strip() for candidate in candidates)
        ):
            raise InlineVariantError(f"{field} inline variant must contain 2-32 non-empty literals")
        variant_index = len(variants)
        selected_index = chosen[variant_index] if variant_index < len(chosen) else 0
        if selected_index >= len(candidates):
            raise InlineVariantError(f"{field} selection {variant_index} is outside its candidate range")
        variants.append(
            {
                "candidates": candidates,
                "selectedIndex": selected_index,
                "resolved": candidates[selected_index],
            }
        )
        if len(variants) > MAX_VARIANTS_PER_FIELD:
            raise InlineVariantError(f"{field} contains more than 64 inline variants")
        resolved.append(candidates[selected_index])
        position = close + 1
    if len(chosen) > len(variants):
        raise InlineVariantError(f"{field} selections exceed the inline variant count")
    return "".join(resolved), variants


def resolve_prompt_variants(
    prompts: Mapping[str, object], selections: object = None
) -> tuple[dict[str, str], dict[str, Any]]:
    """Resolve the four Recipe prompt fields and return compact audit metadata."""

    if not isinstance(prompts, Mapping):
        raise InlineVariantError("prompts must be an object")
    selection_map = selections or {}
    if not isinstance(selection_map, Mapping):
        raise InlineVariantError("inlineVariantSelections must be an object")
    allowed = {"positiveEn", "positiveZh", "negativeEn", "negativeZh"}
    unknown = sorted(set(selection_map) - allowed)
    if unknown:
        raise InlineVariantError("inlineVariantSelections contains unsupported fields: " + ", ".join(unknown))
    resolved: dict[str, str] = {}
    evidence: dict[str, Any] = {}
    for field in sorted(allowed):
        value = prompts.get(field, "")
        result, variants = resolve_inline_variants(
            value,
            selections=selection_map.get(field),
            field=field,
        )
        resolved[field] = result
        if variants:
            evidence[field] = {
                "template": value,
                "variants": variants,
                "resolved": result,
            }
    return resolved, {"schemaVersion": 1, "fields": evidence}
