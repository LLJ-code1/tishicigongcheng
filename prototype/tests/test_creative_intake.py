import sys
import unittest
import math
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import creative_intake  # noqa: E402


class PromptStudioCreativeIntakeTests(unittest.TestCase):
    def test_empty_creative_intake_is_canonical(self):
        self.assertEqual(
            creative_intake.empty_creative_intake(),
            {
                "schemaVersion": 1,
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
            },
        )

    def test_normalize_rejects_unknown_top_level_key(self):
        value = creative_intake.empty_creative_intake()
        value["surprise"] = True
        with self.assertRaisesRegex(
            creative_intake.CreativeIntakeValidationError,
            "unsupported fields",
        ):
            creative_intake.normalize_creative_intake(value)

    def test_normalize_preserves_source_and_lock_provenance(self):
        value = creative_intake.empty_creative_intake()
        value["inputs"]["images"] = [image("image-1")]
        value["directions"] = [direction("main")]
        value["selectedDirectionId"] = "main"
        value["brief"] = {
            "status": "draft",
            "summary": "红裙女孩在雨夜奔跑",
            "items": [{
                "id": "item-action",
                "category": "action",
                "text": "奔跑",
                "source": {"type": "image", "refId": "image-1"},
                "locked": True,
            }],
            "aiAdditions": [],
            "openQuestions": [],
        }
        value["stage"] = "brief_draft"
        normalized = creative_intake.normalize_creative_intake(value)
        self.assertEqual(
            normalized["brief"]["items"][0]["source"],
            {"type": "image", "refId": "image-1"},
        )
        self.assertTrue(normalized["brief"]["items"][0]["locked"])

    def test_normalize_rejects_boolean_revision_and_oversized_text(self):
        value = creative_intake.empty_creative_intake()
        value["revision"] = True
        with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, "revision"):
            creative_intake.normalize_creative_intake(value)

        value = creative_intake.empty_creative_intake()
        value["inputs"]["text"] = "x" * 200_001
        with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, "200000"):
            creative_intake.normalize_creative_intake(value)

    def test_normalize_rejects_collection_limits_and_duplicate_ids(self):
        cases = (
            ("images", [image(f"image-{index}") for index in range(9)], "images"),
            ("directions", [direction(f"direction-{index}") for index in range(4)], "directions"),
            ("conflicts", [conflict(f"conflict-{index}") for index in range(101)], "conflicts"),
        )
        for key, entries, label in cases:
            with self.subTest(key=key):
                value = creative_intake.empty_creative_intake()
                value[key] = entries
                with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, label):
                    creative_intake.normalize_creative_intake(value)

        value = creative_intake.empty_creative_intake()
        value["inputs"]["images"] = [image("image-1"), image("image-1")]
        with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, "duplicate"):
            creative_intake.normalize_creative_intake(value)

        value = creative_intake.empty_creative_intake()
        value["directions"] = [direction("direction-1"), direction("direction-1")]
        with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, "duplicate"):
            creative_intake.normalize_creative_intake(value)

        value = state_with_brief()
        value["brief"]["items"] = [brief_item("item-1"), brief_item("item-1")]
        with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, "duplicate"):
            creative_intake.normalize_creative_intake(value)

        value = state_with_decomposition()
        value["decomposition"]["blocks"] = [decomposition_block("block-1"), decomposition_block("block-1")]
        with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, "duplicate"):
            creative_intake.normalize_creative_intake(value)

    def test_normalize_rejects_unresolved_image_source_and_invalid_stage_gates(self):
        value = state_with_brief()
        value["brief"]["items"] = [
            brief_item("item-1", source={"type": "image", "refId": "missing"})
        ]
        with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, "image"):
            creative_intake.normalize_creative_intake(value)

        value = state_with_brief()
        value["brief"]["status"] = "confirmed"
        value["stage"] = "brief_draft"
        with self.assertRaisesRegex(
            creative_intake.CreativeIntakeValidationError,
            "requires a draft brief",
        ):
            creative_intake.normalize_creative_intake(value)

        value = state_with_decomposition()
        value["stage"] = "brief_confirmed"
        value["selectedModelProfileId"] = None
        with self.assertRaisesRegex(
            creative_intake.CreativeIntakeValidationError,
            "does not allow a decomposition",
        ):
            creative_intake.normalize_creative_intake(value)

    def test_normalize_requires_exact_cross_field_state_for_each_stage(self):
        intake_with_selection = state_for_stage("intake")
        intake_with_selection["directions"] = [direction("main")]
        intake_with_selection["selectedDirectionId"] = "main"

        direction_with_brief = state_for_stage("direction_selected")
        direction_with_brief["brief"] = draft_brief()

        brief_without_draft = state_for_stage("brief_draft")
        brief_without_draft["brief"] = None

        confirmed_with_draft = state_for_stage("brief_confirmed")
        confirmed_with_draft["brief"]["status"] = "draft"

        model_without_selection = state_for_stage("model_selected")
        model_without_selection["selectedModelProfileId"] = None

        model_with_decomposition = state_for_stage("model_selected")
        model_with_decomposition["decomposition"] = approved_decomposition()

        decomposition_without_draft = state_for_stage("decomposition_draft")
        decomposition_without_draft["decomposition"] = None

        confirmed_with_draft_decomposition = state_for_stage(
            "decomposition_confirmed"
        )
        confirmed_with_draft_decomposition["decomposition"]["status"] = "draft"

        confirmed_with_stale_recipe = state_for_stage("decomposition_confirmed")
        confirmed_with_stale_recipe["recipeStatus"] = "stale"

        confirmed_with_unlocked_item = state_for_stage("brief_confirmed")
        confirmed_with_unlocked_item["brief"]["items"][0]["locked"] = False

        cases = (
            ("intake selected direction", intake_with_selection),
            ("direction selected with brief", direction_with_brief),
            ("brief draft without brief", brief_without_draft),
            ("brief confirmed with draft status", confirmed_with_draft),
            ("model selected without model", model_without_selection),
            ("model selected with decomposition", model_with_decomposition),
            ("decomposition draft without decomposition", decomposition_without_draft),
            (
                "decomposition confirmed with draft status",
                confirmed_with_draft_decomposition,
            ),
            ("decomposition confirmed with stale recipe", confirmed_with_stale_recipe),
            ("confirmed brief with unlocked item", confirmed_with_unlocked_item),
        )
        for label, value in cases:
            with self.subTest(label=label), self.assertRaises(
                creative_intake.CreativeIntakeValidationError
            ):
                creative_intake.normalize_creative_intake(value)

    def test_normalize_rejects_unknown_enums_keys_nonfinite_numbers_and_invalid_unicode(self):
        cases = (
            (lambda value: value.__setitem__("stage", "unknown"), "stage"),
            (lambda value: value.__setitem__("recipeStatus", "unknown"), "recipeStatus"),
            (lambda value: value["inputs"].__setitem__("extra", "no"), "unsupported fields"),
            (lambda value: value.__setitem__("revision", math.nan), "revision"),
            (lambda value: value["inputs"].__setitem__("text", "bad\ud800"), "Unicode"),
        )
        for change, message in cases:
            with self.subTest(message=message):
                value = creative_intake.empty_creative_intake()
                change(value)
                with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, message):
                    creative_intake.normalize_creative_intake(value)

        value = state_with_brief()
        value["brief"]["items"][0]["source"] = {"type": "unknown", "refId": None}
        with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, "source"):
            creative_intake.normalize_creative_intake(value)

    def test_normalize_rejects_nested_limits_and_unknown_object_keys(self):
        value = state_with_brief()
        value["brief"]["items"] = [brief_item(f"item-{index}") for index in range(201)]
        with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, "items"):
            creative_intake.normalize_creative_intake(value)

        value = state_with_decomposition()
        value["decomposition"]["blocks"] = [
            decomposition_block(f"block-{index}") for index in range(201)
        ]
        with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, "blocks"):
            creative_intake.normalize_creative_intake(value)

        value = state_with_brief()
        value["brief"]["items"][0]["metadata"] = {}
        with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, "unsupported fields"):
            creative_intake.normalize_creative_intake(value)

    def test_normalize_rejects_image_paths_and_sensitive_image_fields(self):
        cases = (
            ("windows path", lambda value: value.__setitem__("name", r"C:\\references\\image.png")),
            ("posix path", lambda value: value.__setitem__("name", "/tmp/image.png")),
            ("directory traversal", lambda value: value.__setitem__("name", "../image.png")),
            ("image bytes", lambda value: value.__setitem__("bytes", "not-allowed")),
            ("filesystem path", lambda value: value.__setitem__("path", "reference.png")),
            ("api key", lambda value: value.__setitem__("apiKey", "secret")),
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                value = creative_intake.empty_creative_intake()
                item = image("image-1")
                mutate(item)
                value["inputs"]["images"] = [item]
                with self.assertRaises(creative_intake.CreativeIntakeValidationError):
                    creative_intake.normalize_creative_intake(value)

    def test_transitions_progress_through_confirmed_decomposition(self):
        state = creative_intake.empty_creative_intake()
        state = creative_intake.apply_creative_intake_transition(
            state, {"type": "replace_inputs", "text": "red-haired runner", "images": []}
        )
        state = creative_intake.apply_creative_intake_transition(
            state,
            {
                "type": "set_directions",
                "directions": [
                    {"id": "main", "label": "Main", "summary": "Rainy-night run."},
                    {"id": "alt-1", "label": "Alt one", "summary": "Daytime run."},
                    {"id": "alt-2", "label": "Alt two", "summary": "Station run."},
                ],
            },
        )
        state = creative_intake.apply_creative_intake_transition(
            state, {"type": "select_direction", "directionId": "main"}
        )
        self.assertEqual(state["stage"], "direction_selected")
        self.assertEqual(state["revision"], 3)

        state = creative_intake.apply_creative_intake_transition(
            state, {"type": "set_brief_draft", "brief": draft_brief()}
        )
        self.assertEqual(state["stage"], "brief_draft")
        state = creative_intake.apply_creative_intake_transition(
            state, {"type": "confirm_brief"}
        )
        self.assertEqual(state["stage"], "brief_confirmed")
        self.assertEqual(state["brief"]["status"], "confirmed")
        state = creative_intake.apply_creative_intake_transition(
            state, {"type": "select_model", "modelProfileId": "anima-1.1-v1"}
        )
        self.assertEqual(state["stage"], "model_selected")
        state = creative_intake.apply_creative_intake_transition(
            state, {"type": "set_decomposition_draft", "decomposition": approved_decomposition()}
        )
        self.assertEqual(state["stage"], "decomposition_draft")
        state = creative_intake.apply_creative_intake_transition(
            state, {"type": "confirm_decomposition"}
        )
        self.assertEqual(state["stage"], "decomposition_confirmed")
        self.assertEqual(state["decomposition"]["status"], "confirmed")
        self.assertEqual(state["recipeStatus"], "ready")
        self.assertEqual(state["revision"], 8)

    def test_set_brief_draft_requires_one_time_approval_for_locked_item_change(self):
        state = creative_intake.empty_creative_intake()
        state = creative_intake.apply_creative_intake_transition(
            state, {"type": "replace_inputs", "text": "runner", "images": []}
        )
        state = creative_intake.apply_creative_intake_transition(
            state, {"type": "set_directions", "directions": [direction("main")]}
        )
        state = creative_intake.apply_creative_intake_transition(
            state, {"type": "select_direction", "directionId": "main"}
        )
        locked_brief = draft_brief(locked=True)
        brief_state = creative_intake.apply_creative_intake_transition(
            state, {"type": "set_brief_draft", "brief": locked_brief}
        )
        changed_brief = draft_brief(locked=True)
        changed_brief["items"][0]["text"] = "sprint"

        with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, "locked item"):
            creative_intake.apply_creative_intake_transition(
                brief_state,
                {
                    "type": "set_brief_draft",
                    "brief": changed_brief,
                    "approvedLockedItemIds": [],
                },
            )

        updated = creative_intake.apply_creative_intake_transition(
            brief_state,
            {
                "type": "set_brief_draft",
                "brief": changed_brief,
                "approvedLockedItemIds": ["item-1"],
            },
        )
        self.assertEqual(updated["brief"]["items"][0]["text"], "sprint")
        self.assertNotIn("approvedLockedItemIds", updated)

    def test_locked_item_cannot_be_downgraded_then_changed_without_approval(self):
        locked_state = state_at_brief_draft()
        locked_state["brief"]["items"][0]["locked"] = True

        def attempt_two_step_bypass():
            downgraded = creative_intake.apply_creative_intake_transition(
                locked_state,
                {
                    "type": "set_brief_draft",
                    "brief": draft_brief(locked=False),
                    "approvedLockedItemIds": [],
                },
            )
            changed = draft_brief(locked=False)
            changed["items"][0]["text"] = "silently changed"
            return creative_intake.apply_creative_intake_transition(
                downgraded,
                {
                    "type": "set_brief_draft",
                    "brief": changed,
                    "approvedLockedItemIds": [],
                },
            )

        with self.assertRaisesRegex(
            creative_intake.CreativeIntakeValidationError,
            "locked item",
        ):
            attempt_two_step_bypass()

        approved_downgrade = creative_intake.apply_creative_intake_transition(
            locked_state,
            {
                "type": "set_brief_draft",
                "brief": draft_brief(locked=False),
                "approvedLockedItemIds": ["item-1"],
            },
        )
        self.assertFalse(approved_downgrade["brief"]["items"][0]["locked"])

    def test_confirm_brief_locks_every_confirmed_item(self):
        state = state_at_brief_draft()
        state["brief"]["items"].append(brief_item("item-2"))

        try:
            confirmed = creative_intake.apply_creative_intake_transition(
                state, {"type": "confirm_brief"}
            )
        except creative_intake.CreativeIntakeValidationError as error:
            self.fail(f"confirm_brief rejected an unlocked draft: {error}")

        self.assertEqual(confirmed["brief"]["status"], "confirmed")
        self.assertTrue(all(item["locked"] for item in confirmed["brief"]["items"]))

    def test_transitions_reject_invalid_gates_and_unresolved_confirmation(self):
        intake = creative_intake.empty_creative_intake()
        with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, "brief confirmation"):
            creative_intake.apply_creative_intake_transition(
                intake, {"type": "select_model", "modelProfileId": "anima-1.1-v1"}
            )
        with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, "model selection"):
            creative_intake.apply_creative_intake_transition(
                intake,
                {"type": "set_decomposition_draft", "decomposition": approved_decomposition()},
            )
        intake["stage"] = "direction_selected"
        with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, "selected direction"):
            creative_intake.apply_creative_intake_transition(
                intake, {"type": "set_brief_draft", "brief": draft_brief()}
            )

        state = state_at_brief_draft()
        state["brief"]["openQuestions"] = ["What time of day?"]
        with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, "open questions"):
            creative_intake.apply_creative_intake_transition(state, {"type": "confirm_brief"})
        state["brief"]["openQuestions"] = []
        state["conflicts"] = [conflict("conflict-1")]
        with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, "unresolved conflicts"):
            creative_intake.apply_creative_intake_transition(state, {"type": "confirm_brief"})

        state = state_at_model_selected()
        state = creative_intake.apply_creative_intake_transition(
            state, {"type": "set_decomposition_draft", "decomposition": draft_decomposition()}
        )
        with self.assertRaisesRegex(creative_intake.CreativeIntakeValidationError, "approved"):
            creative_intake.apply_creative_intake_transition(state, {"type": "confirm_decomposition"})

    def test_confirm_decomposition_rejects_incomplete_draft_stage(self):
        state = creative_intake.empty_creative_intake()
        state["stage"] = "decomposition_draft"
        state["directions"] = [direction("main")]
        state["selectedDirectionId"] = "main"
        state["decomposition"] = approved_decomposition()

        with self.assertRaisesRegex(
            creative_intake.CreativeIntakeValidationError,
            "confirmed brief",
        ):
            creative_intake.apply_creative_intake_transition(
                state, {"type": "confirm_decomposition"}
            )

    def test_reopen_and_model_change_invalidate_downstream_data(self):
        state = state_at_model_selected()
        state = creative_intake.apply_creative_intake_transition(
            state, {"type": "set_decomposition_draft", "decomposition": approved_decomposition()}
        )
        state = creative_intake.apply_creative_intake_transition(
            state, {"type": "confirm_decomposition"}
        )
        reopened = creative_intake.apply_creative_intake_transition(state, {"type": "reopen_brief"})
        self.assertEqual(reopened["stage"], "brief_draft")
        self.assertEqual(reopened["brief"], {**state["brief"], "status": "draft"})
        self.assertEqual(reopened["inputs"], state["inputs"])
        self.assertEqual(reopened["directions"], state["directions"])
        self.assertEqual(reopened["selectedDirectionId"], state["selectedDirectionId"])
        self.assertIsNone(reopened["selectedModelProfileId"])
        self.assertIsNone(reopened["decomposition"])
        self.assertEqual(reopened["recipeStatus"], "stale")

        selected = creative_intake.apply_creative_intake_transition(
            state, {"type": "select_model", "modelProfileId": "anima-2.0-v1"}
        )
        self.assertEqual(selected["brief"], state["brief"])
        self.assertEqual(selected["selectedModelProfileId"], "anima-2.0-v1")
        self.assertIsNone(selected["decomposition"])
        self.assertEqual(selected["recipeStatus"], "stale")

    def test_failed_transition_leaves_source_untouched(self):
        state = creative_intake.empty_creative_intake()
        snapshot = creative_intake.normalize_creative_intake(state)
        with self.assertRaises(creative_intake.CreativeIntakeValidationError):
            creative_intake.apply_creative_intake_transition(
                state, {"type": "select_direction", "directionId": "missing"}
            )
        self.assertEqual(state, snapshot)


def image(identifier):
    return {
        "id": identifier,
        "name": "reference.png",
        "mimeType": "image/png",
        "status": "local_reference_not_embedded",
        "requestedUses": ["action"],
    }


def direction(identifier):
    return {"id": identifier, "label": "Rainy night", "summary": "A woman runs."}


def brief_item(identifier, source=None):
    return {
        "id": identifier,
        "category": "action",
        "text": "run",
        "source": source or {"type": "user", "refId": None},
        "locked": False,
    }


def conflict(identifier):
    return {
        "id": identifier,
        "code": "contradiction",
        "message": "Choose one action.",
        "status": "open",
        "itemIds": [],
    }


def decomposition_block(identifier):
    return {
        "id": identifier,
        "category": "action",
        "zh": "奔跑",
        "en": "running",
        "source": {"type": "user", "refId": None},
        "locked": False,
        "approved": False,
        "reason": "From the brief.",
        "risks": [],
    }


def state_with_brief():
    value = creative_intake.empty_creative_intake()
    value["stage"] = "brief_draft"
    value["directions"] = [direction("main")]
    value["selectedDirectionId"] = "main"
    value["brief"] = {
        "status": "draft",
        "summary": "A woman runs in rain.",
        "items": [brief_item("item-1")],
        "aiAdditions": [],
        "openQuestions": [],
    }
    return value


def state_with_decomposition():
    value = state_with_brief()
    value["stage"] = "decomposition_draft"
    value["brief"]["status"] = "confirmed"
    value["brief"]["items"][0]["locked"] = True
    value["selectedModelProfileId"] = "anima-1.1-v1"
    value["decomposition"] = {"status": "draft", "blocks": [decomposition_block("block-1")]}
    value["recipeStatus"] = "stale"
    return value


def state_for_stage(stage):
    value = creative_intake.empty_creative_intake()
    value["stage"] = stage
    if stage == "intake":
        return value

    value["directions"] = [direction("main")]
    value["selectedDirectionId"] = "main"
    if stage == "direction_selected":
        return value

    value["brief"] = draft_brief()
    if stage == "brief_draft":
        return value

    value["brief"]["status"] = "confirmed"
    for item in value["brief"]["items"]:
        item["locked"] = True
    if stage == "brief_confirmed":
        return value

    value["selectedModelProfileId"] = "anima-1.1-v1"
    value["recipeStatus"] = "stale"
    if stage == "model_selected":
        return value

    value["decomposition"] = approved_decomposition()
    if stage == "decomposition_draft":
        return value

    value["decomposition"]["status"] = "confirmed"
    value["recipeStatus"] = "ready"
    return value


def draft_brief(locked=False):
    return {
        "status": "draft",
        "summary": "A runner crosses a rainy street.",
        "items": [{**brief_item("item-1"), "locked": locked}],
        "aiAdditions": [],
        "openQuestions": [],
    }


def approved_decomposition():
    return {
        "status": "draft",
        "blocks": [{**decomposition_block("block-1"), "approved": True}],
    }


def draft_decomposition():
    return {"status": "draft", "blocks": [decomposition_block("block-1")]}


def state_at_brief_draft():
    state = creative_intake.empty_creative_intake()
    state = creative_intake.apply_creative_intake_transition(
        state, {"type": "replace_inputs", "text": "runner", "images": []}
    )
    state = creative_intake.apply_creative_intake_transition(
        state, {"type": "set_directions", "directions": [direction("main")]}
    )
    state = creative_intake.apply_creative_intake_transition(
        state, {"type": "select_direction", "directionId": "main"}
    )
    return creative_intake.apply_creative_intake_transition(
        state, {"type": "set_brief_draft", "brief": draft_brief()}
    )


def state_at_model_selected():
    state = state_at_brief_draft()
    state = creative_intake.apply_creative_intake_transition(
        state, {"type": "confirm_brief"}
    )
    return creative_intake.apply_creative_intake_transition(
        state, {"type": "select_model", "modelProfileId": "anima-1.1-v1"}
    )


if __name__ == "__main__":
    unittest.main()
