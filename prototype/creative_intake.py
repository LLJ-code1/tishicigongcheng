from collections.abc import Mapping


SCHEMA_VERSION = 1
STAGES = (
    "intake",
    "direction_selected",
    "brief_draft",
    "brief_confirmed",
    "model_selected",
    "decomposition_draft",
    "decomposition_confirmed",
)


class CreativeIntakeValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def empty_creative_intake() -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "revision": 0,
        "stage": "intake",
        "inputs": {"text": "", "images": []},
        "directions": [],
        "selectedDirectionId": None,
        "brief": None,
        "selectedModelProfileId": None,
        "decomposition": None,
        "recipeStatus": "missing",
        "conflicts": [],
    }


_RECIPE_STATUSES = {"missing", "stale", "ready"}
_SOURCE_TYPES = {"user", "image", "ai", "model_rule"}
_DECOMPOSITION_STATUSES = {"draft", "confirmed"}
_CONFLICT_STATUSES = {"open", "resolved"}


def _error(code: str, message: str) -> None:
    raise CreativeIntakeValidationError(code, message)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _error("invalid_object", f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        _error("invalid_object_key", f"{label} object keys must be strings")
    return value


def _reject_unknown_keys(
    value: Mapping[str, object], allowed: set[str], label: str
) -> None:
    unknown = set(value) - allowed
    if unknown:
        _error("unsupported_fields", f"{label} has unsupported fields: {sorted(unknown)}")


def _text(
    value: object, label: str, *, maximum: int, allow_empty: bool = True
) -> str:
    if not isinstance(value, str):
        _error("invalid_text", f"{label} must be text")
    if not allow_empty and not value:
        _error("invalid_text", f"{label} must not be empty")
    if len(value) > maximum:
        _error("text_too_long", f"{label} exceeds {maximum} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise CreativeIntakeValidationError(
            "invalid_unicode", f"{label} contains invalid Unicode"
        ) from error
    return value


def _identifier(value: object, label: str) -> str:
    identifier = _text(value, label, maximum=128, allow_empty=False)
    if any(character.isspace() for character in identifier):
        _error("invalid_identifier", f"{label} must not contain whitespace")
    return identifier


def _array(value: object, label: str, *, maximum: int) -> list:
    if not isinstance(value, list):
        _error("invalid_array", f"{label} must be an array")
    if len(value) > maximum:
        _error("array_too_long", f"{label} exceeds {maximum} entries")
    return value


def _unique_identifiers(items: list[dict], label: str) -> None:
    identifiers = [item["id"] for item in items]
    if len(identifiers) != len(set(identifiers)):
        _error("duplicate_id", f"{label} contains duplicate IDs")


def _unique_text_array(value: object, label: str, *, maximum: int) -> list[str]:
    entries = _array(value, label, maximum=maximum)
    normalized = [
        _text(entry, f"{label}[{index}]", maximum=4_096, allow_empty=False)
        for index, entry in enumerate(entries)
    ]
    if len(normalized) != len(set(normalized)):
        _error("duplicate_value", f"{label} contains duplicate values")
    return normalized


def _source(value: object, image_ids: set[str], label: str) -> dict:
    source = _mapping(value, label)
    _reject_unknown_keys(source, {"type", "refId"}, label)
    source_type = _text(source.get("type"), f"{label}.type", maximum=64, allow_empty=False)
    if source_type not in _SOURCE_TYPES:
        _error("unsupported_source", f"{label}.type is an unsupported source type")
    ref_id = source.get("refId")
    if source_type in {"user", "ai"}:
        if ref_id is not None:
            _error("invalid_source", f"{label}.refId must be null for {source_type}")
    else:
        ref_id = _identifier(ref_id, f"{label}.refId")
        if source_type == "image" and ref_id not in image_ids:
            _error("unknown_image_reference", f"{label} references an unknown image")
    return {"type": source_type, "refId": ref_id}


def _image_name(value: object, label: str) -> str:
    name = _text(value, label, maximum=512, allow_empty=False)
    if "/" in name or "\\" in name or ":" in name or name in {".", ".."}:
        _error("invalid_image_name", f"{label} must be a plain filename, not a path")
    return name


def _image(value: object, label: str) -> dict:
    image = _mapping(value, label)
    _reject_unknown_keys(
        image,
        {"id", "name", "mimeType", "status", "requestedUses"},
        label,
    )
    requested_uses = _unique_text_array(
        image.get("requestedUses"), f"{label}.requestedUses", maximum=32
    )
    return {
        "id": _identifier(image.get("id"), f"{label}.id"),
        "name": _image_name(image.get("name"), f"{label}.name"),
        "mimeType": _text(
            image.get("mimeType"), f"{label}.mimeType", maximum=128, allow_empty=False
        ),
        "status": _text(image.get("status"), f"{label}.status", maximum=128, allow_empty=False),
        "requestedUses": requested_uses,
    }


def _brief(value: object, image_ids: set[str]) -> dict | None:
    if value is None:
        return None
    brief = _mapping(value, "brief")
    _reject_unknown_keys(
        brief,
        {"status", "summary", "items", "aiAdditions", "openQuestions"},
        "brief",
    )
    status = _text(brief.get("status"), "brief.status", maximum=32, allow_empty=False)
    if status not in {"draft", "confirmed"}:
        _error("unsupported_brief_status", "brief.status is unsupported")
    raw_items = _array(brief.get("items"), "brief.items", maximum=200)
    items = []
    for index, value in enumerate(raw_items):
        item = _mapping(value, f"brief.items[{index}]")
        _reject_unknown_keys(item, {"id", "category", "text", "source", "locked"}, f"brief.items[{index}]")
        locked = item.get("locked")
        if not isinstance(locked, bool):
            _error("invalid_boolean", f"brief.items[{index}].locked must be boolean")
        items.append(
            {
                "id": _identifier(item.get("id"), f"brief.items[{index}].id"),
                "category": _text(item.get("category"), f"brief.items[{index}].category", maximum=128, allow_empty=False),
                "text": _text(item.get("text"), f"brief.items[{index}].text", maximum=200_000),
                "source": _source(item.get("source"), image_ids, f"brief.items[{index}].source"),
                "locked": locked,
            }
        )
    _unique_identifiers(items, "brief.items")
    return {
        "status": status,
        "summary": _text(brief.get("summary"), "brief.summary", maximum=200_000),
        "items": items,
        "aiAdditions": _unique_text_array(brief.get("aiAdditions"), "brief.aiAdditions", maximum=200),
        "openQuestions": _unique_text_array(brief.get("openQuestions"), "brief.openQuestions", maximum=200),
    }


def _decomposition(value: object, image_ids: set[str]) -> dict | None:
    if value is None:
        return None
    decomposition = _mapping(value, "decomposition")
    _reject_unknown_keys(decomposition, {"status", "blocks"}, "decomposition")
    status = _text(
        decomposition.get("status"), "decomposition.status", maximum=32, allow_empty=False
    )
    if status not in _DECOMPOSITION_STATUSES:
        _error("unsupported_decomposition_status", "decomposition.status is unsupported")
    raw_blocks = _array(decomposition.get("blocks"), "decomposition.blocks", maximum=200)
    blocks = []
    for index, value in enumerate(raw_blocks):
        block = _mapping(value, f"decomposition.blocks[{index}]")
        _reject_unknown_keys(
            block,
            {"id", "category", "zh", "en", "source", "locked", "approved", "reason", "risks"},
            f"decomposition.blocks[{index}]",
        )
        locked = block.get("locked")
        approved = block.get("approved")
        if not isinstance(locked, bool) or not isinstance(approved, bool):
            _error("invalid_boolean", f"decomposition.blocks[{index}] locks and approval must be boolean")
        blocks.append(
            {
                "id": _identifier(block.get("id"), f"decomposition.blocks[{index}].id"),
                "category": _text(block.get("category"), f"decomposition.blocks[{index}].category", maximum=128, allow_empty=False),
                "zh": _text(block.get("zh"), f"decomposition.blocks[{index}].zh", maximum=200_000),
                "en": _text(block.get("en"), f"decomposition.blocks[{index}].en", maximum=200_000),
                "source": _source(block.get("source"), image_ids, f"decomposition.blocks[{index}].source"),
                "locked": locked,
                "approved": approved,
                "reason": _text(block.get("reason"), f"decomposition.blocks[{index}].reason", maximum=200_000),
                "risks": _unique_text_array(block.get("risks"), f"decomposition.blocks[{index}].risks", maximum=100),
            }
        )
    _unique_identifiers(blocks, "decomposition.blocks")
    return {"status": status, "blocks": blocks}


def _conflicts(value: object) -> list[dict]:
    raw_conflicts = _array(value, "conflicts", maximum=100)
    conflicts = []
    for index, value in enumerate(raw_conflicts):
        conflict = _mapping(value, f"conflicts[{index}]")
        _reject_unknown_keys(
            conflict, {"id", "code", "message", "status", "itemIds"}, f"conflicts[{index}]"
        )
        status = _text(conflict.get("status"), f"conflicts[{index}].status", maximum=32, allow_empty=False)
        if status not in _CONFLICT_STATUSES:
            _error("unsupported_conflict_status", f"conflicts[{index}].status is unsupported")
        item_ids = [
            _identifier(item_id, f"conflicts[{index}].itemIds[{item_index}]")
            for item_index, item_id in enumerate(_array(conflict.get("itemIds"), f"conflicts[{index}].itemIds", maximum=200))
        ]
        if len(item_ids) != len(set(item_ids)):
            _error("duplicate_id", f"conflicts[{index}].itemIds contains duplicate IDs")
        conflicts.append(
            {
                "id": _identifier(conflict.get("id"), f"conflicts[{index}].id"),
                "code": _text(conflict.get("code"), f"conflicts[{index}].code", maximum=128, allow_empty=False),
                "message": _text(conflict.get("message"), f"conflicts[{index}].message", maximum=200_000),
                "status": status,
                "itemIds": item_ids,
            }
        )
    _unique_identifiers(conflicts, "conflicts")
    return conflicts


def normalize_creative_intake(value: object) -> dict:
    """Return the canonical Creative-Intake v1 state or raise a typed error."""

    intake = _mapping(value, "creative intake")
    _reject_unknown_keys(
        intake,
        {
            "schemaVersion", "revision", "stage", "inputs", "directions",
            "selectedDirectionId", "brief", "selectedModelProfileId", "decomposition",
            "recipeStatus", "conflicts",
        },
        "creative intake",
    )
    schema_version = intake.get("schemaVersion")
    if isinstance(schema_version, bool) or schema_version != SCHEMA_VERSION:
        _error("unsupported_schema_version", "creative intake schemaVersion is unsupported")
    revision = intake.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        _error("invalid_revision", "revision must be a non-negative integer")
    stage = _text(intake.get("stage"), "stage", maximum=64, allow_empty=False)
    if stage not in STAGES:
        _error("unsupported_stage", "stage is unsupported")
    inputs = _mapping(intake.get("inputs"), "inputs")
    _reject_unknown_keys(inputs, {"text", "images"}, "inputs")
    images = [_image(item, f"inputs.images[{index}]") for index, item in enumerate(_array(inputs.get("images"), "inputs.images", maximum=8))]
    _unique_identifiers(images, "inputs.images")
    image_ids = {item["id"] for item in images}
    directions = []
    for index, value in enumerate(_array(intake.get("directions"), "directions", maximum=3)):
        direction = _mapping(value, f"directions[{index}]")
        _reject_unknown_keys(direction, {"id", "label", "summary"}, f"directions[{index}]")
        directions.append(
            {
                "id": _identifier(direction.get("id"), f"directions[{index}].id"),
                "label": _text(direction.get("label"), f"directions[{index}].label", maximum=512, allow_empty=False),
                "summary": _text(direction.get("summary"), f"directions[{index}].summary", maximum=200_000),
            }
        )
    _unique_identifiers(directions, "directions")
    selected_direction_id = intake.get("selectedDirectionId")
    if selected_direction_id is not None:
        selected_direction_id = _identifier(selected_direction_id, "selectedDirectionId")
        if selected_direction_id not in {item["id"] for item in directions}:
            _error("unknown_direction", "selectedDirectionId does not reference a direction")
    brief = _brief(intake.get("brief"), image_ids)
    selected_model_profile_id = intake.get("selectedModelProfileId")
    if selected_model_profile_id is not None:
        selected_model_profile_id = _identifier(selected_model_profile_id, "selectedModelProfileId")
    decomposition = _decomposition(intake.get("decomposition"), image_ids)
    recipe_status = _text(intake.get("recipeStatus"), "recipeStatus", maximum=32, allow_empty=False)
    if recipe_status not in _RECIPE_STATUSES:
        _error("unsupported_recipe_status", "recipeStatus is unsupported")
    conflicts = _conflicts(intake.get("conflicts"))

    if brief is not None and brief["status"] == "confirmed" and STAGES.index(stage) < STAGES.index("brief_confirmed"):
        _error("brief_stage_mismatch", "a confirmed brief requires stage brief_confirmed or later")
    if decomposition is not None and STAGES.index(stage) < STAGES.index("model_selected"):
        _error("decomposition_stage_mismatch", "decomposition requires stage model_selected or later")

    return {
        "schemaVersion": SCHEMA_VERSION,
        "revision": revision,
        "stage": stage,
        "inputs": {
            "text": _text(inputs.get("text"), "inputs.text", maximum=200_000),
            "images": images,
        },
        "directions": directions,
        "selectedDirectionId": selected_direction_id,
        "brief": brief,
        "selectedModelProfileId": selected_model_profile_id,
        "decomposition": decomposition,
        "recipeStatus": recipe_status,
        "conflicts": conflicts,
    }


__all__ = [
    "CreativeIntakeValidationError",
    "SCHEMA_VERSION",
    "STAGES",
    "empty_creative_intake",
    "normalize_creative_intake",
]
