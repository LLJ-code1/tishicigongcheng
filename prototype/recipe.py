"""Versioned, JSON-native recipe domain model for Anima Prompt Studio.

The module deliberately has no database or HTTP dependencies.  It gives the
current ten-block UI a lossless compatibility projection while keeping the
canonical recipe on the product-required thirteen-block structure.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


RECIPE_SCHEMA_VERSION = 1
RECIPE_KIND = "anima_prompt_recipe"

PARAMETER_SOURCE_PRIORITY = (
    "manual_override",
    "task_preset",
    "lora_requirement",
    "model_default",
)
PARAMETER_KEYS = (
    "sampler",
    "scheduler",
    "steps",
    "cfg",
    "resolution",
    "generationSeed",
    "denoiseStrength",
)
REQUIRED_PARAMETER_KEYS = (
    "sampler",
    "scheduler",
    "steps",
    "cfg",
    "resolution",
    "generationSeed",
)

_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_TEXT_LENGTH = 200_000
_MAX_GENERIC_DEPTH = 24
_MAX_GENERIC_NODES = 50_000


@dataclass(frozen=True)
class BlockDefinition:
    id: str
    label: str
    hint: str
    legacy_id: str
    negative: bool = False


BLOCK_DEFINITIONS = (
    BlockDefinition("identity", "人物身份", "人数、年龄表达、身份与角色定位", "subject"),
    BlockDefinition("appearance", "身体与外观特征", "体貌、发型、肤色与可见特征", "appearance"),
    BlockDefinition("clothing", "服装与配饰", "服装、材质、鞋履与配饰", "appearance"),
    BlockDefinition("expression", "表情", "表情、视线与情绪外显", "pose"),
    BlockDefinition("action", "姿势与动作", "姿势、动作、朝向与身体动态", "pose"),
    BlockDefinition("interaction", "人物互动关系", "接触、视线与人物之间的动作关系", "pose"),
    BlockDefinition("scene", "场景与环境", "地点、环境、时间与空间关系", "scene"),
    BlockDefinition("camera", "构图与镜头", "景别、视角、透视与视觉重点", "composition"),
    BlockDefinition("lighting", "光影与色彩", "光源、色彩、氛围与反射关系", "lighting"),
    BlockDefinition("style", "风格与艺术家", "媒介、画风、艺术家标签与视觉语言", "artist"),
    BlockDefinition("effects", "道具与特效", "道具、粒子、天气效果与视觉特效", "effects"),
    BlockDefinition("quality", "质量增强", "质量、细节和清晰度增强词", "quality"),
    BlockDefinition("negative", "负向约束", "排除不希望出现的内容", "negative", True),
)
BLOCK_IDS = tuple(item.id for item in BLOCK_DEFINITIONS)
BLOCK_DEFINITION_BY_ID = {item.id: item for item in BLOCK_DEFINITIONS}

LEGACY_BLOCK_ORDER = (
    "quality",
    "artist",
    "subject",
    "appearance",
    "pose",
    "scene",
    "effects",
    "composition",
    "lighting",
    "negative",
)
LEGACY_TO_PRIMARY_BLOCK = {
    "quality": "quality",
    "artist": "style",
    "subject": "identity",
    "appearance": "appearance",
    "pose": "action",
    "scene": "scene",
    "effects": "effects",
    "composition": "camera",
    "lighting": "lighting",
    "negative": "negative",
}

# The prompt engine/workbench is moving to thirteen fields while retaining its
# historical public IDs.  Keep that transport vocabulary out of the canonical
# Recipe schema and provide a one-to-one adapter instead.
WORKBENCH_BLOCK_ORDER = (
    "quality",
    "artist",
    "subject",
    "appearance",
    "outfit",
    "expression",
    "pose",
    "interaction",
    "scene",
    "composition",
    "lighting",
    "effects",
    "negative",
)
WORKBENCH_TO_RECIPE_BLOCK = {
    "quality": "quality",
    "artist": "style",
    "subject": "identity",
    "appearance": "appearance",
    "outfit": "clothing",
    "expression": "expression",
    "pose": "action",
    "interaction": "interaction",
    "scene": "scene",
    "composition": "camera",
    "lighting": "lighting",
    "effects": "effects",
    "negative": "negative",
}
RECIPE_TO_WORKBENCH_BLOCK = {
    recipe_id: workbench_id
    for workbench_id, recipe_id in WORKBENCH_TO_RECIPE_BLOCK.items()
}


class RecipeValidationError(ValueError):
    """Raised when a recipe cannot be represented by schema v1."""


def get_block_definitions() -> list[dict[str, Any]]:
    """Return the thirteen definitions as a fresh JSON-compatible list."""

    return [
        {
            "id": item.id,
            "label": item.label,
            "hint": item.hint,
            "legacyId": item.legacy_id,
            "negative": item.negative,
        }
        for item in BLOCK_DEFINITIONS
    ]


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RecipeValidationError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise RecipeValidationError(f"{label} keys must be strings")
    return value


def _reject_unknown_keys(
    value: Mapping[str, Any], allowed: Iterable[str], label: str
) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise RecipeValidationError(
            f"{label} contains unsupported fields: {', '.join(unknown)}"
        )


def _text(
    value: object,
    label: str,
    *,
    allow_empty: bool = True,
    max_length: int = _MAX_TEXT_LENGTH,
) -> str:
    if not isinstance(value, str):
        raise RecipeValidationError(f"{label} must be a string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise RecipeValidationError(f"{label} contains invalid Unicode") from error
    if len(value) > max_length:
        raise RecipeValidationError(f"{label} exceeds the length limit")
    if not allow_empty and not value.strip():
        raise RecipeValidationError(f"{label} cannot be empty")
    return value


def _nullable_text(
    value: object, label: str, *, max_length: int = 1_024
) -> str | None:
    if value is None:
        return None
    return _text(value, label, allow_empty=False, max_length=max_length)


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise RecipeValidationError(f"{label} must be a boolean")
    return value


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RecipeValidationError(f"{label} must be an integer")
    if value < minimum or value > maximum:
        raise RecipeValidationError(f"{label} is outside the supported range")
    return value


def _number(
    value: object, label: str, minimum: float, maximum: float
) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecipeValidationError(f"{label} must be a finite number")
    if not math.isfinite(float(value)):
        raise RecipeValidationError(f"{label} must be a finite number")
    if float(value) < minimum or float(value) > maximum:
        raise RecipeValidationError(f"{label} is outside the supported range")
    return value if isinstance(value, int) else float(value)


def _json_copy(value: object, label: str) -> Any:
    nodes = 0

    def visit(item: object, depth: int, path: str) -> Any:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_GENERIC_NODES:
            raise RecipeValidationError(f"{label} is too large")
        if depth > _MAX_GENERIC_DEPTH:
            raise RecipeValidationError(f"{label} is nested too deeply")
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise RecipeValidationError(f"{path} must be a finite number")
            return item
        if isinstance(item, str):
            return _text(item, path)
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            for key, child in item.items():
                if not isinstance(key, str):
                    raise RecipeValidationError(f"{path} keys must be strings")
                normalized_key = _text(
                    key, f"{path} key", allow_empty=False, max_length=256
                )
                result[normalized_key] = visit(
                    child, depth + 1, f"{path}.{normalized_key}"
                )
            return result
        if isinstance(item, (list, tuple)):
            return [
                visit(child, depth + 1, f"{path}[{index}]")
                for index, child in enumerate(item)
            ]
        raise RecipeValidationError(f"{path} is not strict JSON data")

    return visit(value, 0, label)


def _normalize_string_list(
    value: object, label: str, *, max_items: int = 256
) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise RecipeValidationError(f"{label} must be an array")
    if len(value) > max_items:
        raise RecipeValidationError(f"{label} has too many items")
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        text = _text(
            item, f"{label}[{index}]", allow_empty=False, max_length=1_024
        ).strip()
        if text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _empty_block(definition: BlockDefinition) -> dict[str, Any]:
    return {
        "id": definition.id,
        "label": definition.label,
        "hint": definition.hint,
        "locked": False,
        "weight": 100,
        "en": "",
        "zh": "",
        "source": "",
        "confidence": None,
        "metadata": {},
    }


def normalize_blocks(blocks: object) -> list[dict[str, Any]]:
    """Validate blocks and return all thirteen in stable canonical order.

    Missing blocks are represented explicitly as empty blocks. Unknown or
    duplicate IDs are rejected so content cannot disappear silently.
    """

    if not isinstance(blocks, (list, tuple)):
        raise RecipeValidationError("blocks must be an array")
    if len(blocks) > len(BLOCK_DEFINITIONS):
        raise RecipeValidationError("blocks contains too many items")
    supplied: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(blocks):
        item = _mapping(raw, f"blocks[{index}]")
        _reject_unknown_keys(
            item,
            {
                "id",
                "label",
                "hint",
                "locked",
                "weight",
                "en",
                "zh",
                "source",
                "confidence",
                "metadata",
            },
            f"blocks[{index}]",
        )
        block_id = _text(
            item.get("id"), f"blocks[{index}].id", allow_empty=False, max_length=64
        )
        if block_id not in BLOCK_DEFINITION_BY_ID:
            raise RecipeValidationError(f"unsupported block id: {block_id}")
        if block_id in supplied:
            raise RecipeValidationError(f"duplicate block id: {block_id}")
        definition = BLOCK_DEFINITION_BY_ID[block_id]
        confidence = item.get("confidence")
        if confidence is not None:
            confidence = _number(
                confidence, f"blocks[{index}].confidence", 0, 100
            )
        supplied[block_id] = {
            "id": block_id,
            "label": definition.label,
            "hint": definition.hint,
            "locked": _boolean(
                item.get("locked", False), f"blocks[{index}].locked"
            ),
            "weight": _number(
                item.get("weight", 100), f"blocks[{index}].weight", 0, 120
            ),
            "en": _text(item.get("en", ""), f"blocks[{index}].en"),
            "zh": _text(item.get("zh", ""), f"blocks[{index}].zh"),
            "source": _text(
                item.get("source", ""),
                f"blocks[{index}].source",
                max_length=1_024,
            ),
            "confidence": confidence,
            "metadata": _json_copy(
                item.get("metadata", {}), f"blocks[{index}].metadata"
            ),
        }
    return [
        supplied.get(definition.id, _empty_block(definition))
        for definition in BLOCK_DEFINITIONS
    ]


def inflate_legacy_blocks(blocks: object) -> list[dict[str, Any]]:
    """Best-effort migration of the old ten blocks into the v1 structure.

    Old combined fields cannot be split reliably. They are placed in a single
    primary v1 block and the other new blocks remain explicitly empty.
    """

    if not isinstance(blocks, (list, tuple)):
        raise RecipeValidationError("legacy blocks must be an array")
    converted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(blocks):
        item = _mapping(raw, f"legacyBlocks[{index}]")
        legacy_id = _text(
            item.get("id"),
            f"legacyBlocks[{index}].id",
            allow_empty=False,
            max_length=64,
        )
        if legacy_id not in LEGACY_TO_PRIMARY_BLOCK:
            raise RecipeValidationError(f"unsupported legacy block id: {legacy_id}")
        if legacy_id in seen:
            raise RecipeValidationError(f"duplicate legacy block id: {legacy_id}")
        seen.add(legacy_id)
        target_id = LEGACY_TO_PRIMARY_BLOCK[legacy_id]
        converted.append(
            {
                "id": target_id,
                "locked": item.get("locked", False),
                "weight": item.get("weight", 100),
                "en": item.get("en", ""),
                "zh": item.get("zh", ""),
                "source": item.get("source", "legacy-ten-block"),
                "confidence": item.get("confidence"),
                "metadata": {
                    "compatibilitySource": "legacy-ten-block-v1",
                    "legacyBlockId": legacy_id,
                },
            }
        )
    return normalize_blocks(converted)


def inflate_workbench_blocks(blocks: object) -> list[dict[str, Any]]:
    """Convert the workbench/prompt-engine thirteen IDs to Recipe v1 IDs."""

    if not isinstance(blocks, (list, tuple)):
        raise RecipeValidationError("workbench blocks must be an array")
    if len(blocks) > len(WORKBENCH_BLOCK_ORDER):
        raise RecipeValidationError("workbench blocks contains too many items")
    converted: list[dict[str, Any]] = []
    seen: set[str] = set()
    allowed = {
        "id",
        "label",
        "hint",
        "locked",
        "weight",
        "en",
        "zh",
        "source",
        "confidence",
        "metadata",
    }
    for index, raw in enumerate(blocks):
        item = _mapping(raw, f"workbenchBlocks[{index}]")
        _reject_unknown_keys(item, allowed, f"workbenchBlocks[{index}]")
        workbench_id = _text(
            item.get("id"),
            f"workbenchBlocks[{index}].id",
            allow_empty=False,
            max_length=64,
        )
        if workbench_id not in WORKBENCH_TO_RECIPE_BLOCK:
            raise RecipeValidationError(
                f"unsupported workbench block id: {workbench_id}"
            )
        if workbench_id in seen:
            raise RecipeValidationError(
                f"duplicate workbench block id: {workbench_id}"
            )
        seen.add(workbench_id)
        metadata = item.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise RecipeValidationError(
                f"workbenchBlocks[{index}].metadata must be an object"
            )
        converted.append(
            {
                "id": WORKBENCH_TO_RECIPE_BLOCK[workbench_id],
                "locked": item.get("locked", False),
                "weight": item.get("weight", 100),
                "en": item.get("en", ""),
                "zh": item.get("zh", ""),
                "source": item.get("source", ""),
                "confidence": item.get("confidence"),
                "metadata": {
                    **_json_copy(
                        metadata, f"workbenchBlocks[{index}].metadata"
                    ),
                    "compatibilitySource": "workbench-thirteen-v1",
                    "workbenchBlockId": workbench_id,
                },
            }
        )
    return normalize_blocks(converted)


def _normalize_parameter_value(key: str, value: object, label: str) -> Any:
    if key in {"sampler", "scheduler"}:
        return _text(value, label, allow_empty=False, max_length=128).strip()
    if key == "steps":
        return _integer(value, label, 1, 1_000)
    if key == "cfg":
        return _number(value, label, 0, 100)
    if key == "generationSeed":
        return _integer(value, label, 0, 2**63 - 1)
    if key == "denoiseStrength":
        if value is None:
            return None
        return _number(value, label, 0, 1)
    if key == "resolution":
        resolution = _mapping(value, label)
        _reject_unknown_keys(resolution, {"width", "height"}, label)
        width = _integer(resolution.get("width"), f"{label}.width", 64, 8_192)
        height = _integer(
            resolution.get("height"), f"{label}.height", 64, 8_192
        )
        if width % 8 or height % 8:
            raise RecipeValidationError(
                f"{label} dimensions must be multiples of 8"
            )
        return {"width": width, "height": height}
    raise RecipeValidationError(f"unsupported parameter: {key}")


def resolve_parameters(
    *,
    model_default: Mapping[str, Any] | None = None,
    lora_requirement: Mapping[str, Any] | None = None,
    task_preset: Mapping[str, Any] | None = None,
    manual_override: Mapping[str, Any] | None = None,
    required_keys: Iterable[str] = (),
) -> dict[str, dict[str, Any]]:
    """Resolve parameter layers and retain every shadowed candidate.

    Presence of a key is significant. An explicit invalid/null value does not
    silently fall through to a lower-priority layer.
    """

    layers = {
        "model_default": _mapping(
            {} if model_default is None else model_default, "model_default"
        ),
        "lora_requirement": _mapping(
            {} if lora_requirement is None else lora_requirement,
            "lora_requirement",
        ),
        "task_preset": _mapping(
            {} if task_preset is None else task_preset, "task_preset"
        ),
        "manual_override": _mapping(
            {} if manual_override is None else manual_override, "manual_override"
        ),
    }
    for source, layer in layers.items():
        _reject_unknown_keys(layer, PARAMETER_KEYS, source)

    result: dict[str, dict[str, Any]] = {}
    for key in PARAMETER_KEYS:
        candidates: list[tuple[str, Any]] = []
        for source in PARAMETER_SOURCE_PRIORITY:
            if key in layers[source]:
                value = _normalize_parameter_value(
                    key, layers[source][key], f"{source}.{key}"
                )
                candidates.append((source, value))
        if not candidates:
            continue
        winning_source, winning_value = candidates[0]
        result[key] = {
            "value": winning_value,
            "source": winning_source,
            "overridden": [
                {
                    "source": source,
                    "value": value,
                    "sameValue": value == winning_value,
                }
                for source, value in candidates[1:]
            ],
        }

    required = tuple(required_keys)
    unknown_required = sorted(set(required) - set(PARAMETER_KEYS))
    if unknown_required:
        raise RecipeValidationError(
            "required_keys contains unsupported parameters: "
            + ", ".join(unknown_required)
        )
    missing = [key for key in required if key not in result]
    if missing:
        raise RecipeValidationError(
            "missing required parameters: " + ", ".join(missing)
        )
    return result


def _normalize_resolved_parameters(value: object) -> dict[str, dict[str, Any]]:
    parameters = _mapping(value, "parameters")
    _reject_unknown_keys(parameters, PARAMETER_KEYS, "parameters")
    missing = [key for key in REQUIRED_PARAMETER_KEYS if key not in parameters]
    if missing:
        raise RecipeValidationError(
            "parameters is missing required fields: " + ", ".join(missing)
        )

    result: dict[str, dict[str, Any]] = {}
    for key in PARAMETER_KEYS:
        if key not in parameters:
            continue
        raw = _mapping(parameters[key], f"parameters.{key}")
        _reject_unknown_keys(
            raw, {"value", "source", "overridden"}, f"parameters.{key}"
        )
        if "value" not in raw:
            raise RecipeValidationError(f"parameters.{key}.value is required")
        source = _text(
            raw.get("source"),
            f"parameters.{key}.source",
            allow_empty=False,
            max_length=64,
        )
        if source not in PARAMETER_SOURCE_PRIORITY:
            raise RecipeValidationError(
                f"parameters.{key}.source is unsupported"
            )
        overridden_raw = raw.get("overridden", [])
        if not isinstance(overridden_raw, (list, tuple)):
            raise RecipeValidationError(
                f"parameters.{key}.overridden must be an array"
            )
        overridden: list[dict[str, Any]] = []
        seen_sources: set[str] = set()
        winning_priority = PARAMETER_SOURCE_PRIORITY.index(source)
        for index, candidate_raw in enumerate(overridden_raw):
            candidate = _mapping(
                candidate_raw, f"parameters.{key}.overridden[{index}]"
            )
            _reject_unknown_keys(
                candidate,
                {"source", "value", "sameValue"},
                f"parameters.{key}.overridden[{index}]",
            )
            candidate_source = _text(
                candidate.get("source"),
                f"parameters.{key}.overridden[{index}].source",
                allow_empty=False,
                max_length=64,
            )
            if (
                candidate_source not in PARAMETER_SOURCE_PRIORITY
                or PARAMETER_SOURCE_PRIORITY.index(candidate_source)
                <= winning_priority
            ):
                raise RecipeValidationError(
                    f"parameters.{key}.overridden[{index}] is not lower priority"
                )
            if candidate_source in seen_sources:
                raise RecipeValidationError(
                    f"parameters.{key}.overridden repeats {candidate_source}"
                )
            seen_sources.add(candidate_source)
            candidate_value = _normalize_parameter_value(
                key,
                candidate.get("value"),
                f"parameters.{key}.overridden[{index}].value",
            )
            winning_value = _normalize_parameter_value(
                key, raw["value"], f"parameters.{key}.value"
            )
            overridden.append(
                {
                    "source": candidate_source,
                    "value": candidate_value,
                    "sameValue": candidate_value == winning_value,
                }
            )
        result[key] = {
            "value": _normalize_parameter_value(
                key, raw["value"], f"parameters.{key}.value"
            ),
            "source": source,
            "overridden": sorted(
                overridden,
                key=lambda item: PARAMETER_SOURCE_PRIORITY.index(item["source"]),
            ),
        }
    return result


def _normalize_model(value: object) -> dict[str, Any]:
    model = _mapping(value, "model")
    _reject_unknown_keys(
        model,
        {
            "profileId",
            "profileVersionId",
            "profileContentSha256",
            "displayName",
            "versionName",
            "versionId",
            "baseModel",
            "checkpoint",
        },
        "model",
    )
    profile_id = _text(
        model.get("profileId"), "model.profileId", allow_empty=False, max_length=128
    )
    if not _SAFE_ID_PATTERN.fullmatch(profile_id):
        raise RecipeValidationError("model.profileId is invalid")
    has_profile_version = "profileVersionId" in model
    has_profile_hash = "profileContentSha256" in model
    if has_profile_version != has_profile_hash:
        raise RecipeValidationError(
            "model.profileVersionId and model.profileContentSha256 must be present together"
        )
    raw_profile_version = model.get("profileVersionId")
    raw_profile_hash = model.get("profileContentSha256")
    if (raw_profile_version is None) != (raw_profile_hash is None):
        raise RecipeValidationError(
            "model.profileVersionId and model.profileContentSha256 must be present together"
        )
    if raw_profile_version is None:
        profile_version_id = None
        profile_content_sha256 = None
    else:
        profile_version_id = _text(
            raw_profile_version,
            "model.profileVersionId",
            allow_empty=False,
            max_length=128,
        )
        if not _SAFE_ID_PATTERN.fullmatch(profile_version_id):
            raise RecipeValidationError("model.profileVersionId is invalid")
        profile_content_sha256 = _text(
            raw_profile_hash,
            "model.profileContentSha256",
            allow_empty=False,
            max_length=64,
        ).lower()
        if not _SHA256_PATTERN.fullmatch(profile_content_sha256):
            raise RecipeValidationError(
                "model.profileContentSha256 must be SHA-256"
            )
    checkpoint = _mapping(model.get("checkpoint"), "model.checkpoint")
    _reject_unknown_keys(
        checkpoint,
        {"filename", "sha256", "verificationStatus"},
        "model.checkpoint",
    )
    checksum = _nullable_text(
        checkpoint.get("sha256"), "model.checkpoint.sha256", max_length=64
    )
    if checksum is not None:
        checksum = checksum.lower()
        if not _SHA256_PATTERN.fullmatch(checksum):
            raise RecipeValidationError("model.checkpoint.sha256 must be SHA-256")
    verification = _text(
        checkpoint.get("verificationStatus"),
        "model.checkpoint.verificationStatus",
        allow_empty=False,
        max_length=64,
    )
    if verification not in {
        "unverified",
        "candidate",
        "hash_verified",
        "locally_validated",
        "missing",
    }:
        raise RecipeValidationError(
            "model.checkpoint.verificationStatus is unsupported"
        )
    return {
        "profileId": profile_id,
        "profileVersionId": profile_version_id,
        "profileContentSha256": profile_content_sha256,
        "displayName": _text(
            model.get("displayName"),
            "model.displayName",
            allow_empty=False,
            max_length=256,
        ),
        "versionName": _text(
            model.get("versionName"),
            "model.versionName",
            allow_empty=False,
            max_length=128,
        ),
        "versionId": _integer(
            model.get("versionId"), "model.versionId", 1, 2**63 - 1
        ),
        "baseModel": _text(
            model.get("baseModel"),
            "model.baseModel",
            allow_empty=False,
            max_length=128,
        ),
        "checkpoint": {
            "filename": _nullable_text(
                checkpoint.get("filename"),
                "model.checkpoint.filename",
                max_length=1_024,
            ),
            "sha256": checksum,
            "verificationStatus": verification,
        },
    }


def _normalize_prompts(value: object) -> dict[str, str]:
    prompts = _mapping(value, "prompts")
    keys = {"positiveEn", "positiveZh", "negativeEn", "negativeZh"}
    _reject_unknown_keys(prompts, keys, "prompts")
    missing = sorted(keys - set(prompts))
    if missing:
        raise RecipeValidationError(
            "prompts is missing required fields: " + ", ".join(missing)
        )
    return {key: _text(prompts[key], f"prompts.{key}") for key in sorted(keys)}


def _normalize_loras(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise RecipeValidationError("loras must be an array")
    if len(value) > 256:
        raise RecipeValidationError("loras has too many items")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        item = _mapping(raw, f"loras[{index}]")
        _reject_unknown_keys(
            item,
            {
                "assetId",
                "name",
                "versionId",
                "filename",
                "sha256",
                "weight",
                "triggerWords",
                "enabled",
                "compatibilityStatus",
                "sourceRefs",
            },
            f"loras[{index}]",
        )
        asset_id = _text(
            item.get("assetId"),
            f"loras[{index}].assetId",
            allow_empty=False,
            max_length=1_024,
        )
        if asset_id in seen:
            raise RecipeValidationError(f"duplicate LoRA assetId: {asset_id}")
        seen.add(asset_id)
        version_id = item.get("versionId")
        if version_id is not None:
            if isinstance(version_id, bool) or not isinstance(version_id, (int, str)):
                raise RecipeValidationError(
                    f"loras[{index}].versionId must be an integer, string, or null"
                )
            if isinstance(version_id, int):
                version_id = _integer(
                    version_id, f"loras[{index}].versionId", 1, 2**63 - 1
                )
            else:
                version_id = _text(
                    version_id,
                    f"loras[{index}].versionId",
                    allow_empty=False,
                    max_length=256,
                )
        checksum = _nullable_text(
            item.get("sha256"), f"loras[{index}].sha256", max_length=64
        )
        if checksum is not None:
            checksum = checksum.lower()
            if not _SHA256_PATTERN.fullmatch(checksum):
                raise RecipeValidationError(
                    f"loras[{index}].sha256 must be SHA-256"
                )
        compatibility = _text(
            item.get("compatibilityStatus", "unknown"),
            f"loras[{index}].compatibilityStatus",
            allow_empty=False,
            max_length=64,
        )
        if compatibility not in {
            "unknown",
            "candidate",
            "compatible",
            "validated",
            "conflict",
            "missing",
        }:
            raise RecipeValidationError(
                f"loras[{index}].compatibilityStatus is unsupported"
            )
        source_refs = item.get("sourceRefs", [])
        if not isinstance(source_refs, (list, tuple)):
            raise RecipeValidationError(f"loras[{index}].sourceRefs must be an array")
        result.append(
            {
                "assetId": asset_id,
                "name": _text(
                    item.get("name"),
                    f"loras[{index}].name",
                    allow_empty=False,
                    max_length=512,
                ),
                "versionId": version_id,
                "filename": _nullable_text(
                    item.get("filename"),
                    f"loras[{index}].filename",
                    max_length=1_024,
                ),
                "sha256": checksum,
                "weight": _number(
                    item.get("weight", 1.0), f"loras[{index}].weight", -10, 10
                ),
                "triggerWords": _normalize_string_list(
                    item.get("triggerWords", []), f"loras[{index}].triggerWords"
                ),
                "enabled": _boolean(
                    item.get("enabled", True), f"loras[{index}].enabled"
                ),
                "compatibilityStatus": compatibility,
                "sourceRefs": [
                    _json_copy(reference, f"loras[{index}].sourceRefs[{ref_index}]")
                    for ref_index, reference in enumerate(source_refs)
                    if isinstance(reference, Mapping)
                ],
            }
        )
        if len(result[-1]["sourceRefs"]) != len(source_refs):
            raise RecipeValidationError(
                f"loras[{index}].sourceRefs items must be objects"
            )
    return result


def _normalize_object_list(value: object, label: str, maximum: int) -> list[dict]:
    if not isinstance(value, (list, tuple)):
        raise RecipeValidationError(f"{label} must be an array")
    if len(value) > maximum:
        raise RecipeValidationError(f"{label} has too many items")
    result = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise RecipeValidationError(f"{label}[{index}] must be an object")
        result.append(_json_copy(raw, f"{label}[{index}]"))
    return result


def normalize_recipe(value: object) -> dict[str, Any]:
    """Validate and canonicalize one complete Recipe schema-v1 value."""

    recipe = _mapping(value, "recipe")
    allowed = {
        "kind",
        "schemaVersion",
        "model",
        "prompts",
        "blocks",
        "loras",
        "parameters",
        "randomPlan",
        "instructionHistory",
        "imageRefs",
        "sourceRefs",
        "metadata",
    }
    _reject_unknown_keys(recipe, allowed, "recipe")
    kind = recipe.get("kind", RECIPE_KIND)
    if kind != RECIPE_KIND:
        raise RecipeValidationError(f"unsupported recipe kind: {kind}")
    version = recipe.get("schemaVersion")
    if isinstance(version, bool) or version != RECIPE_SCHEMA_VERSION:
        raise RecipeValidationError(
            f"unsupported recipe schemaVersion: {version}"
        )
    random_plan = recipe.get("randomPlan")
    if random_plan is not None and not isinstance(random_plan, Mapping):
        raise RecipeValidationError("randomPlan must be an object or null")
    metadata = recipe.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise RecipeValidationError("metadata must be an object")
    source_refs = recipe.get("sourceRefs", [])
    return {
        "kind": RECIPE_KIND,
        "schemaVersion": RECIPE_SCHEMA_VERSION,
        "model": _normalize_model(recipe.get("model")),
        "prompts": _normalize_prompts(recipe.get("prompts")),
        "blocks": normalize_blocks(recipe.get("blocks")),
        "loras": _normalize_loras(recipe.get("loras", [])),
        "parameters": _normalize_resolved_parameters(recipe.get("parameters")),
        "randomPlan": (
            _json_copy(random_plan, "randomPlan") if random_plan is not None else None
        ),
        "instructionHistory": _normalize_object_list(
            recipe.get("instructionHistory", []), "instructionHistory", 10_000
        ),
        "imageRefs": _normalize_object_list(
            recipe.get("imageRefs", []), "imageRefs", 10_000
        ),
        "sourceRefs": _normalize_object_list(source_refs, "sourceRefs", 10_000),
        "metadata": _json_copy(metadata, "metadata"),
    }


def build_recipe(
    *,
    model: Mapping[str, Any],
    prompts: Mapping[str, Any],
    blocks: Sequence[Mapping[str, Any]],
    parameters: Mapping[str, Any],
    loras: Sequence[Mapping[str, Any]] = (),
    random_plan: Mapping[str, Any] | None = None,
    instruction_history: Sequence[Mapping[str, Any]] = (),
    image_refs: Sequence[Mapping[str, Any]] = (),
    source_refs: Sequence[Mapping[str, Any]] = (),
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct and immediately validate a canonical Recipe v1 value."""

    return normalize_recipe(
        {
            "kind": RECIPE_KIND,
            "schemaVersion": RECIPE_SCHEMA_VERSION,
            "model": model,
            "prompts": prompts,
            "blocks": list(blocks),
            "loras": list(loras),
            "parameters": parameters,
            "randomPlan": random_plan,
            "instructionHistory": list(instruction_history),
            "imageRefs": list(image_refs),
            "sourceRefs": list(source_refs),
            "metadata": {} if metadata is None else metadata,
        }
    )


def canonical_recipe_json(value: object) -> str:
    """Serialize normalized recipe data deterministically for hashing/storage."""

    return json.dumps(
        normalize_recipe(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def recipe_hash(value: object) -> str:
    return hashlib.sha256(canonical_recipe_json(value).encode("utf-8")).hexdigest()


def _join_fragments(values: Sequence[str], separator: str) -> str:
    return separator.join(value.strip() for value in values if value.strip())


def project_blocks_to_legacy(blocks: object) -> list[dict[str, Any]]:
    """Project thirteen blocks onto the existing ten-block editor.

    Every v1 block is represented. Combined legacy blocks are conservatively
    locked if any constituent is locked, preventing an old UI from silently
    changing a locked new block.
    """

    normalized = normalize_blocks(blocks)
    grouped: dict[str, list[dict[str, Any]]] = {
        legacy_id: [] for legacy_id in LEGACY_BLOCK_ORDER
    }
    for block in normalized:
        definition = BLOCK_DEFINITION_BY_ID[block["id"]]
        grouped[definition.legacy_id].append(block)

    result: list[dict[str, Any]] = []
    for legacy_id in LEGACY_BLOCK_ORDER:
        members = grouped[legacy_id]
        sources = []
        for member in members:
            if member["source"] and member["source"] not in sources:
                sources.append(member["source"])
        confidences = [
            member["confidence"]
            for member in members
            if member["confidence"] is not None
        ]
        result.append(
            {
                "id": legacy_id,
                "locked": any(member["locked"] for member in members),
                "weight": max((member["weight"] for member in members), default=100),
                "en": _join_fragments([member["en"] for member in members], ", "),
                "zh": _join_fragments([member["zh"] for member in members], "；"),
                "source": " + ".join(sources),
                "confidence": min(confidences) if confidences else None,
                "metadata": {
                    "compatibilityProjection": "recipe-v1-to-legacy-ten-block-v1",
                    "recipeBlockIds": [member["id"] for member in members],
                },
            }
        )
    return result


def project_blocks_to_workbench(blocks: object) -> list[dict[str, Any]]:
    """Losslessly map canonical Recipe v1 blocks to current workbench IDs."""

    normalized = normalize_blocks(blocks)
    by_id = {item["id"]: item for item in normalized}
    result: list[dict[str, Any]] = []
    for workbench_id in WORKBENCH_BLOCK_ORDER:
        recipe_id = WORKBENCH_TO_RECIPE_BLOCK[workbench_id]
        block = deepcopy(by_id[recipe_id])
        metadata = block.pop("metadata")
        block["id"] = workbench_id
        block["metadata"] = {
            **metadata,
            "compatibilityProjection": "recipe-v1-to-workbench-thirteen-v1",
            "recipeBlockId": recipe_id,
        }
        result.append(block)
    return result


def project_recipe_to_prompt_version(
    value: object,
    *,
    source: str = "manual",
    base_version: int | None = None,
    base_updated_at: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    block_projection: str = "legacy-ten",
) -> dict[str, Any]:
    """Build the current prompt-version API shape without losing Recipe v1."""

    normalized = normalize_recipe(value)
    projection_metadata = _json_copy(
        _mapping(
            {} if metadata is None else metadata,
            "projection metadata",
        ),
        "projection metadata",
    )
    for reserved in ("recipe", "recipeHash", "recipeSchemaVersion"):
        if reserved in projection_metadata:
            raise RecipeValidationError(
                f"projection metadata field is reserved: {reserved}"
            )
    projection_metadata.update(
        {
            "recipeSchemaVersion": RECIPE_SCHEMA_VERSION,
            "recipeHash": recipe_hash(normalized),
            "recipe": deepcopy(normalized),
        }
    )
    prompts = normalized["prompts"]
    if block_projection == "legacy-ten":
        projected_blocks = project_blocks_to_legacy(normalized["blocks"])
    elif block_projection == "workbench-thirteen":
        projected_blocks = project_blocks_to_workbench(normalized["blocks"])
    elif block_projection == "canonical-thirteen":
        projected_blocks = deepcopy(normalized["blocks"])
    else:
        raise RecipeValidationError(
            f"unsupported block projection: {block_projection}"
        )
    payload: dict[str, Any] = {
        "source": _text(source, "source", allow_empty=False, max_length=64),
        "positiveEn": prompts["positiveEn"],
        "positiveZh": prompts["positiveZh"],
        "negativeEn": prompts["negativeEn"],
        "negativeZh": prompts["negativeZh"],
        "blocks": projected_blocks,
        "metadata": projection_metadata,
    }
    if base_version is not None:
        payload["baseVersion"] = _integer(
            base_version, "base_version", 0, 2**63 - 1
        )
    if base_updated_at is not None:
        payload["baseUpdatedAt"] = _text(
            base_updated_at,
            "base_updated_at",
            allow_empty=False,
            max_length=128,
        )
    return payload


def extract_recipe_from_prompt_version(
    value: object, *, verify_hash: bool = True
) -> dict[str, Any]:
    """Recover the canonical recipe embedded by the compatibility projection."""

    version = _mapping(value, "prompt version")
    metadata = _mapping(version.get("metadata"), "prompt version metadata")
    if metadata.get("recipeSchemaVersion") != RECIPE_SCHEMA_VERSION:
        raise RecipeValidationError("prompt version has no supported embedded recipe")
    normalized = normalize_recipe(metadata.get("recipe"))
    if verify_hash:
        expected = _text(
            metadata.get("recipeHash"),
            "prompt version metadata.recipeHash",
            allow_empty=False,
            max_length=64,
        ).lower()
        actual = recipe_hash(normalized)
        if expected != actual:
            raise RecipeValidationError("embedded recipe hash does not match")
    return normalized


__all__ = [
    "BLOCK_DEFINITIONS",
    "BLOCK_IDS",
    "LEGACY_BLOCK_ORDER",
    "PARAMETER_KEYS",
    "PARAMETER_SOURCE_PRIORITY",
    "RECIPE_KIND",
    "RECIPE_SCHEMA_VERSION",
    "REQUIRED_PARAMETER_KEYS",
    "WORKBENCH_BLOCK_ORDER",
    "WORKBENCH_TO_RECIPE_BLOCK",
    "RecipeValidationError",
    "build_recipe",
    "canonical_recipe_json",
    "extract_recipe_from_prompt_version",
    "get_block_definitions",
    "inflate_legacy_blocks",
    "inflate_workbench_blocks",
    "normalize_blocks",
    "normalize_recipe",
    "project_blocks_to_legacy",
    "project_blocks_to_workbench",
    "project_recipe_to_prompt_version",
    "recipe_hash",
    "resolve_parameters",
]
