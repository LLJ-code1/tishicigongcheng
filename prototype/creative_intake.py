import copy
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
    if status == "confirmed":
        for item in items:
            item["locked"] = True
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


def _validate_stage_invariants(
    stage: str,
    selected_direction_id: str | None,
    brief: dict | None,
    selected_model_profile_id: str | None,
    decomposition: dict | None,
    recipe_status: str,
    conflicts: list[dict],
) -> None:
    stage_index = STAGES.index(stage)
    direction_required = stage_index >= STAGES.index("direction_selected")
    if (selected_direction_id is not None) != direction_required:
        requirement = "requires" if direction_required else "does not allow"
        _error(
            "direction_stage_mismatch",
            f"stage {stage} {requirement} a selected direction",
        )

    if stage_index < STAGES.index("brief_draft"):
        expected_brief_status = None
    elif stage == "brief_draft":
        expected_brief_status = "draft"
    else:
        expected_brief_status = "confirmed"
    if expected_brief_status is None:
        if brief is not None:
            _error("brief_stage_mismatch", f"stage {stage} does not allow a brief")
    elif brief is None or brief["status"] != expected_brief_status:
        _error(
            "brief_stage_mismatch",
            f"stage {stage} requires a {expected_brief_status} brief",
        )
    elif expected_brief_status == "confirmed" and any(
        not item["locked"] for item in brief["items"]
    ):
        _error(
            "brief_lock_mismatch",
            f"stage {stage} requires every confirmed brief item to be locked",
        )
    elif expected_brief_status == "confirmed" and (
        brief["openQuestions"]
        or any(conflict["status"] == "open" for conflict in conflicts)
    ):
        _error(
            "brief_confirmation_mismatch",
            f"stage {stage} requires a brief with no open questions or conflicts",
        )

    model_required = stage_index >= STAGES.index("model_selected")
    if (selected_model_profile_id is not None) != model_required:
        requirement = "requires" if model_required else "does not allow"
        _error(
            "model_stage_mismatch",
            f"stage {stage} {requirement} a selected model",
        )

    if stage_index < STAGES.index("decomposition_draft"):
        expected_decomposition_status = None
    elif stage == "decomposition_draft":
        expected_decomposition_status = "draft"
    else:
        expected_decomposition_status = "confirmed"
    if expected_decomposition_status is None:
        if decomposition is not None:
            _error(
                "decomposition_stage_mismatch",
                f"stage {stage} does not allow a decomposition",
            )
    elif (
        decomposition is None
        or decomposition["status"] != expected_decomposition_status
    ):
        _error(
            "decomposition_stage_mismatch",
            f"stage {stage} requires a {expected_decomposition_status} decomposition",
        )
    elif expected_decomposition_status == "confirmed" and any(
        not block["approved"] for block in decomposition["blocks"]
    ):
        _error(
            "decomposition_approval_mismatch",
            f"stage {stage} requires every decomposition block to be approved",
        )

    if stage_index <= STAGES.index("direction_selected"):
        allowed_recipe_statuses = {"missing"}
    elif stage_index <= STAGES.index("brief_confirmed"):
        allowed_recipe_statuses = {"missing", "stale"}
    elif stage_index <= STAGES.index("decomposition_draft"):
        allowed_recipe_statuses = {"stale"}
    else:
        allowed_recipe_statuses = {"ready"}
    if recipe_status not in allowed_recipe_statuses:
        _error(
            "recipe_stage_mismatch",
            f"recipeStatus {recipe_status} is invalid for stage {stage}",
        )


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

    _validate_stage_invariants(
        stage,
        selected_direction_id,
        brief,
        selected_model_profile_id,
        decomposition,
        recipe_status,
        conflicts,
    )

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


def _transition_requires(state: dict, stages: set[str], message: str) -> None:
    if state["stage"] not in stages:
        _error("invalid_transition", message)


def _transition_images(value: object) -> list[dict]:
    images = [_image(item, f"action.images[{index}]") for index, item in enumerate(
        _array(value, "action.images", maximum=8)
    )]
    _unique_identifiers(images, "action.images")
    return images


def _transition_directions(value: object) -> list[dict]:
    directions = []
    for index, value in enumerate(_array(value, "action.directions", maximum=3)):
        direction = _mapping(value, f"action.directions[{index}]")
        _reject_unknown_keys(direction, {"id", "label", "summary"}, f"action.directions[{index}]")
        directions.append(
            {
                "id": _identifier(direction.get("id"), f"action.directions[{index}].id"),
                "label": _text(
                    direction.get("label"),
                    f"action.directions[{index}].label",
                    maximum=512,
                    allow_empty=False,
                ),
                "summary": _text(
                    direction.get("summary"),
                    f"action.directions[{index}].summary",
                    maximum=200_000,
                ),
            }
        )
    _unique_identifiers(directions, "action.directions")
    return directions


def _approved_locked_item_ids(command: Mapping[str, object]) -> set[str]:
    value = command.get("approvedLockedItemIds", [])
    identifiers = [
        _identifier(item_id, f"action.approvedLockedItemIds[{index}]")
        for index, item_id in enumerate(
            _array(value, "action.approvedLockedItemIds", maximum=200)
        )
    ]
    if len(identifiers) != len(set(identifiers)):
        _error("duplicate_id", "action.approvedLockedItemIds contains duplicate IDs")
    return set(identifiers)


def _locked_brief_item_value(item: dict) -> tuple:
    source = item["source"]
    return (
        item["id"],
        item["category"],
        item["text"],
        source["type"],
        source["refId"],
        item["locked"],
    )


def _require_locked_brief_items_approved(
    current: dict | None, updated: dict, approved_ids: set[str]
) -> None:
    if current is None:
        return
    updated_by_id = {item["id"]: item for item in updated["items"]}
    for item in current["items"]:
        if not item["locked"]:
            continue
        replacement = updated_by_id.get(item["id"])
        if replacement is None or _locked_brief_item_value(item) != _locked_brief_item_value(replacement):
            if item["id"] not in approved_ids:
                _error("locked_item", f"locked item {item['id']} requires approval")


def _replace_inputs(state: dict, command: Mapping[str, object]) -> dict:
    _reject_unknown_keys(command, {"type", "text", "images"}, "action")
    state["inputs"] = {
        "text": _text(command.get("text"), "action.text", maximum=200_000),
        "images": _transition_images(command.get("images")),
    }
    state["stage"] = "intake"
    state["directions"] = []
    state["selectedDirectionId"] = None
    state["brief"] = None
    state["selectedModelProfileId"] = None
    state["decomposition"] = None
    state["recipeStatus"] = "missing"
    state["conflicts"] = []
    return state


def _set_directions(state: dict, command: Mapping[str, object]) -> dict:
    _transition_requires(state, {"intake"}, "inputs are required before setting directions")
    _reject_unknown_keys(command, {"type", "directions"}, "action")
    state["directions"] = _transition_directions(command.get("directions"))
    state["selectedDirectionId"] = None
    return state


def _select_direction(state: dict, command: Mapping[str, object]) -> dict:
    _transition_requires(state, {"intake"}, "directions are required before selecting a direction")
    _reject_unknown_keys(command, {"type", "directionId"}, "action")
    direction_id = _identifier(command.get("directionId"), "action.directionId")
    if direction_id not in {direction["id"] for direction in state["directions"]}:
        _error("unknown_direction", "action.directionId does not reference a direction")
    state["selectedDirectionId"] = direction_id
    state["stage"] = "direction_selected"
    return state


def _set_brief_draft(state: dict, command: Mapping[str, object]) -> dict:
    _transition_requires(
        state,
        {"direction_selected", "brief_draft"},
        "a selected direction is required before setting a brief draft",
    )
    _reject_unknown_keys(
        command,
        {"type", "brief", "conflicts", "approvedLockedItemIds"},
        "action",
    )
    if state["selectedDirectionId"] is None:
        _error("missing_direction", "a selected direction is required before setting a brief draft")
    image_ids = {image["id"] for image in state["inputs"]["images"]}
    brief = _brief(command.get("brief"), image_ids)
    if brief is None:
        _error("invalid_brief", "action.brief must be an object")
    if brief["status"] != "draft":
        _error("invalid_brief_status", "action.brief must have draft status")
    _require_locked_brief_items_approved(
        state["brief"], brief, _approved_locked_item_ids(command)
    )
    state["brief"] = brief
    state["conflicts"] = _conflicts(command.get("conflicts", state["conflicts"]))
    state["stage"] = "brief_draft"
    return state


def _confirm_brief(state: dict, command: Mapping[str, object]) -> dict:
    _transition_requires(state, {"brief_draft"}, "a brief draft is required before brief confirmation")
    _reject_unknown_keys(command, {"type"}, "action")
    if state["brief"] is None:
        _error("missing_brief", "a brief draft is required before brief confirmation")
    if state["brief"]["openQuestions"]:
        _error("open_questions", "brief confirmation requires no open questions")
    if any(conflict["status"] == "open" for conflict in state["conflicts"]):
        _error("unresolved_conflicts", "brief confirmation requires no unresolved conflicts")
    for item in state["brief"]["items"]:
        item["locked"] = True
    state["brief"]["status"] = "confirmed"
    state["stage"] = "brief_confirmed"
    return state


def _select_model(state: dict, command: Mapping[str, object]) -> dict:
    _transition_requires(
        state,
        {"brief_confirmed", "model_selected", "decomposition_draft", "decomposition_confirmed"},
        "brief confirmation is required before model selection",
    )
    _reject_unknown_keys(command, {"type", "modelProfileId"}, "action")
    if state["brief"] is None or state["brief"]["status"] != "confirmed":
        _error("missing_brief_confirmation", "brief confirmation is required before model selection")
    state["selectedModelProfileId"] = _identifier(
        command.get("modelProfileId"), "action.modelProfileId"
    )
    state["decomposition"] = None
    state["recipeStatus"] = "stale"
    state["stage"] = "model_selected"
    return state


def _set_decomposition_draft(state: dict, command: Mapping[str, object]) -> dict:
    _transition_requires(
        state,
        {"model_selected", "decomposition_draft"},
        "model selection is required before setting a decomposition draft",
    )
    _reject_unknown_keys(command, {"type", "decomposition"}, "action")
    if state["selectedModelProfileId"] is None:
        _error("missing_model", "model selection is required before setting a decomposition draft")
    image_ids = {image["id"] for image in state["inputs"]["images"]}
    decomposition = _decomposition(command.get("decomposition"), image_ids)
    if decomposition is None:
        _error("invalid_decomposition", "action.decomposition must be an object")
    if decomposition["status"] != "draft":
        _error("invalid_decomposition_status", "action.decomposition must have draft status")
    state["decomposition"] = decomposition
    state["recipeStatus"] = "stale"
    state["stage"] = "decomposition_draft"
    return state


def _confirm_decomposition(state: dict, command: Mapping[str, object]) -> dict:
    _transition_requires(
        state,
        {"decomposition_draft"},
        "a decomposition draft is required before decomposition confirmation",
    )
    _reject_unknown_keys(command, {"type"}, "action")
    if state["brief"] is None or state["brief"]["status"] != "confirmed":
        _error(
            "missing_brief_confirmation",
            "a confirmed brief is required before decomposition confirmation",
        )
    if state["selectedModelProfileId"] is None:
        _error(
            "missing_model",
            "model selection is required before decomposition confirmation",
        )
    if (
        state["decomposition"] is None
        or state["decomposition"]["status"] != "draft"
    ):
        _error("missing_decomposition", "a decomposition draft is required before decomposition confirmation")
    if any(not block["approved"] for block in state["decomposition"]["blocks"]):
        _error("unapproved_decomposition", "all decomposition blocks must be approved")
    state["decomposition"]["status"] = "confirmed"
    state["recipeStatus"] = "ready"
    state["stage"] = "decomposition_confirmed"
    return state


def _reopen_brief(state: dict, command: Mapping[str, object]) -> dict:
    _transition_requires(
        state,
        {"brief_confirmed", "model_selected", "decomposition_draft", "decomposition_confirmed"},
        "brief confirmation is required before reopening a brief",
    )
    _reject_unknown_keys(command, {"type"}, "action")
    if state["brief"] is None:
        _error("missing_brief", "brief confirmation is required before reopening a brief")
    state["brief"]["status"] = "draft"
    state["selectedModelProfileId"] = None
    state["decomposition"] = None
    state["recipeStatus"] = "stale"
    state["stage"] = "brief_draft"
    return state


_TRANSITIONS = {
    "replace_inputs": _replace_inputs,
    "set_directions": _set_directions,
    "select_direction": _select_direction,
    "set_brief_draft": _set_brief_draft,
    "confirm_brief": _confirm_brief,
    "select_model": _select_model,
    "set_decomposition_draft": _set_decomposition_draft,
    "confirm_decomposition": _confirm_decomposition,
    "reopen_brief": _reopen_brief,
}


def apply_creative_intake_transition(current: object, action: object) -> dict:
    state = normalize_creative_intake(current)
    command = _mapping(action, "action")
    action_type = _text(
        command.get("type"), "action.type", maximum=64, allow_empty=False
    )
    handler = _TRANSITIONS.get(action_type)
    if handler is None:
        _error("unsupported_action", f"unsupported creative-intake action: {action_type}")
    updated = handler(copy.deepcopy(state), command)
    updated["revision"] = state["revision"] + 1
    return normalize_creative_intake(updated)


__all__ = [
    "CreativeIntakeValidationError",
    "SCHEMA_VERSION",
    "STAGES",
    "apply_creative_intake_transition",
    "empty_creative_intake",
    "normalize_creative_intake",
]
