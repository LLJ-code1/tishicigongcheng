import sys
import unittest
import math
import copy
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import creative_intake  # noqa: E402
from recipe import BLOCK_IDS  # noqa: E402


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

    def test_normalize_migrates_legacy_confirmed_brief_items_to_locked(self):
        value = state_for_stage("brief_confirmed")
        value["revision"] = 17
        value["brief"]["summary"] = "Keep this legacy confirmed brief."
        value["brief"]["items"] = [
            brief_item("item-1"),
            brief_item("item-2"),
        ]

        normalized = creative_intake.normalize_creative_intake(value)

        self.assertEqual(normalized["revision"], 17)
        self.assertEqual(normalized["stage"], "brief_confirmed")
        self.assertEqual(
            normalized["brief"]["summary"],
            "Keep this legacy confirmed brief.",
        )
        self.assertEqual(
            [item["id"] for item in normalized["brief"]["items"]],
            ["item-1", "item-2"],
        )
        self.assertTrue(all(item["locked"] for item in normalized["brief"]["items"]))

    def test_normalize_preserves_draft_brief_item_lock_values(self):
        value = state_for_stage("brief_draft")
        value["brief"]["items"] = [
            brief_item("item-unlocked"),
            {**brief_item("item-locked"), "locked": True},
        ]

        normalized = creative_intake.normalize_creative_intake(value)

        self.assertEqual(
            [item["locked"] for item in normalized["brief"]["items"]],
            [False, True],
        )

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

        confirmed_with_open_question = state_for_stage("brief_confirmed")
        confirmed_with_open_question["brief"]["openQuestions"] = [
            "Which time of day?"
        ]

        confirmed_with_open_conflict = state_for_stage("brief_confirmed")
        confirmed_with_open_conflict["conflicts"] = [conflict("conflict-1")]

        confirmed_with_unapproved_block = state_for_stage(
            "decomposition_confirmed"
        )
        confirmed_with_unapproved_block["decomposition"]["blocks"][0][
            "approved"
        ] = False

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
            ("confirmed brief with open question", confirmed_with_open_question),
            ("confirmed brief with open conflict", confirmed_with_open_conflict),
            (
                "confirmed decomposition with unapproved block",
                confirmed_with_unapproved_block,
            ),
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

    def test_versioned_decomposition_normalizes_exact_recipe_order_and_lineage(self):
        value = state_with_decomposition()

        normalized = creative_intake.normalize_creative_intake(value)

        decomposition = normalized["decomposition"]
        self.assertEqual(tuple(block["id"] for block in decomposition["blocks"]), BLOCK_IDS)
        self.assertEqual(
            decomposition["briefContentSha256"],
            creative_intake.canonical_brief_sha256(normalized["brief"]),
        )
        self.assertEqual(decomposition["profileVersionId"], "profile-version-7")
        self.assertEqual(decomposition["profileContentSha256"], "a" * 64)
        self.assertEqual(decomposition["blocks"][0]["ruleRefs"], [])
        self.assertEqual(
            decomposition["blocks"][1]["ruleRefs"],
            [{
                "claimId": "claim-prompt-language",
                "fieldPath": "prompting.language",
                "evidenceRefs": ["snapshot-official"],
            }],
        )

    def test_decomposition_preserves_fact_level_multi_source_lineage(self):
        value = state_with_decomposition()
        value["inputs"]["images"] = [image("image-action")]
        value["brief"]["items"].append(
            {
                "id": "item-image-action",
                "category": "action",
                "text": "hold umbrella above right shoulder",
                "source": {"type": "image", "refId": "image-action"},
                "locked": True,
            }
        )
        value["decomposition"]["briefContentSha256"] = (
            creative_intake.canonical_brief_sha256(value["brief"])
        )
        value["decomposition"]["blocks"][0]["semanticItems"] = [
            {
                "id": "item-1",
                "text": "run",
                "source": {"type": "user", "refId": None},
                "locked": True,
            },
            {
                "id": "item-image-action",
                "text": "hold umbrella above right shoulder",
                "source": {"type": "image", "refId": "image-action"},
                "locked": True,
            },
        ]

        normalized = creative_intake.normalize_creative_intake(value)

        self.assertEqual(
            normalized["decomposition"]["blocks"][0]["semanticItems"][1]["source"],
            {"type": "image", "refId": "image-action"},
        )

    def test_empty_block_rejects_generic_fallback_marker(self):
        value = state_with_decomposition()
        block = value["decomposition"]["blocks"][1]
        block["zh"] = ""
        block["en"] = ""
        block["ruleRefs"] = []
        block["risks"] = ["generic_fallback_no_approved_model_rule"]

        with self.assertRaisesRegex(
            creative_intake.CreativeIntakeValidationError,
            "generic fallback",
        ):
            creative_intake.normalize_creative_intake(value)

    def test_versioned_decomposition_rejects_bad_lineage_order_and_rule_refs(self):
        cases = []
        bad = state_with_decomposition()
        bad["decomposition"]["blocks"].reverse()
        cases.append(("canonical order", bad))
        bad = state_with_decomposition()
        bad["decomposition"]["briefContentSha256"] = "0" * 64
        cases.append(("brief", bad))
        bad = state_with_decomposition()
        bad["decomposition"]["profileVersionId"] = ""
        cases.append(("profileVersionId", bad))
        bad = state_with_decomposition()
        bad["decomposition"]["profileContentSha256"] = "A" * 64
        cases.append(("profileContentSha256", bad))
        bad = state_with_decomposition()
        bad["decomposition"]["blocks"][0]["ruleRefs"] = [
            rule_ref("claim-one"),
            rule_ref("claim-one"),
        ]
        cases.append(("duplicate", bad))
        bad = state_with_decomposition()
        bad["decomposition"]["blocks"][0]["ruleRefs"] = [
            {**rule_ref("claim-one"), "surprise": True}
        ]
        cases.append(("unsupported fields", bad))
        bad = state_with_decomposition()
        bad["decomposition"]["blocks"][0]["ruleRefs"] = [
            {**rule_ref("claim-one"), "evidenceRefs": ["unsafe ref"]}
        ]
        cases.append(("safe identifier", bad))
        bad = state_with_decomposition()
        bad["decomposition"]["blocks"][0].pop("semanticItems")
        cases.append(("semanticItems", bad))
        bad = state_with_decomposition()
        bad["decomposition"]["blocks"][0]["semanticItems"][0]["text"] = "changed"
        cases.append(("semanticItems", bad))
        bad = state_with_decomposition()
        bad["decomposition"]["blocks"][0]["semanticItems"][0]["surprise"] = True
        cases.append(("unsupported fields", bad))
        bad = state_with_decomposition()
        bad["decomposition"]["blocks"][0]["risks"] = []
        cases.append(("generic fallback", bad))

        for message, value in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                creative_intake.CreativeIntakeValidationError, message
            ):
                creative_intake.normalize_creative_intake(value)

    def test_locked_decomposition_block_must_retain_confirmed_brief_source(self):
        state = state_at_model_selected()
        decomposition = approved_decomposition(state["brief"])
        decomposition["blocks"][0]["locked"] = True
        decomposition["blocks"][0]["source"] = {"type": "ai", "refId": None}

        with self.assertRaisesRegex(
            creative_intake.CreativeIntakeValidationError, "locked.*source"
        ):
            creative_intake.apply_creative_intake_transition(
                state,
                {"type": "set_decomposition_draft", "decomposition": decomposition},
            )

    def test_draft_approval_can_change_but_confirmation_requires_all_thirteen(self):
        state = state_at_model_selected()
        decomposition = approved_decomposition(state["brief"])
        decomposition["blocks"][-1]["approved"] = False
        draft = creative_intake.apply_creative_intake_transition(
            state,
            {"type": "set_decomposition_draft", "decomposition": decomposition},
        )
        self.assertFalse(draft["decomposition"]["blocks"][-1]["approved"])
        with self.assertRaisesRegex(
            creative_intake.CreativeIntakeValidationError, "approved"
        ):
            creative_intake.apply_creative_intake_transition(
                draft, {"type": "confirm_decomposition"}
            )

    def test_decomposition_redraft_only_allows_approval_and_wording_changes(self):
        state = state_at_model_selected()
        draft = creative_intake.apply_creative_intake_transition(
            state,
            {
                "type": "set_decomposition_draft",
                "decomposition": approved_decomposition(state["brief"]),
            },
        )
        changed = copy.deepcopy(draft["decomposition"])
        changed["blocks"][0]["approved"] = False
        changed["blocks"][0]["en"] = "adult runner"
        changed["blocks"][0]["reason"] = "Clearer model-compatible wording."

        updated = creative_intake.apply_creative_intake_transition(
            draft,
            {"type": "set_decomposition_draft", "decomposition": changed},
        )

        self.assertFalse(updated["decomposition"]["blocks"][0]["approved"])
        self.assertEqual(updated["decomposition"]["blocks"][0]["en"], "adult runner")
        self.assertEqual(
            updated["decomposition"]["blocks"][0]["reason"],
            "Clearer model-compatible wording.",
        )

    def test_decomposition_redraft_rejects_semantic_or_lineage_tampering(self):
        state = state_at_model_selected()
        draft = creative_intake.apply_creative_intake_transition(
            state,
            {
                "type": "set_decomposition_draft",
                "decomposition": approved_decomposition(state["brief"]),
            },
        )
        mutations = (
            ("brief hash", lambda value: value.__setitem__("briefContentSha256", "b" * 64)),
            ("profile version", lambda value: value.__setitem__("profileVersionId", "other-version")),
            ("profile hash", lambda value: value.__setitem__("profileContentSha256", "b" * 64)),
            ("block id", lambda value: value["blocks"][0].__setitem__("id", "different")),
            ("category", lambda value: value["blocks"][0].__setitem__("category", "other")),
            ("zh", lambda value: value["blocks"][0].__setitem__("zh", "篡改")),
            ("source", lambda value: value["blocks"][0].__setitem__("source", {"type": "ai", "refId": None})),
            ("locked", lambda value: value["blocks"][0].__setitem__("locked", False)),
            (
                "semantic item source",
                lambda value: value["blocks"][0]["semanticItems"][0].__setitem__(
                    "source", {"type": "ai", "refId": None}
                ),
            ),
            ("rule ref", lambda value: value["blocks"][0]["ruleRefs"].append(rule_ref("fabricated"))),
            ("risks", lambda value: value["blocks"][0]["risks"].append("fabricated")),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                changed = copy.deepcopy(draft["decomposition"])
                mutate(changed)
                with self.assertRaises(
                    creative_intake.CreativeIntakeValidationError
                ):
                    creative_intake.apply_creative_intake_transition(
                        draft,
                        {"type": "set_decomposition_draft", "decomposition": changed},
                    )

    def test_decomposition_regeneration_replaces_only_adaptation_and_resets_approval(self):
        state = state_at_model_selected()
        state = creative_intake.apply_creative_intake_transition(
            state,
            {
                "type": "set_decomposition_draft",
                "decomposition": approved_decomposition(state["brief"]),
            },
        )
        regenerated = copy.deepcopy(state["decomposition"])
        regenerated["blocks"][0]["en"] = "regenerated runner wording"
        regenerated["blocks"][0]["reason"] = "Updated for the activated model."
        regenerated["blocks"][0]["ruleRefs"] = [rule_ref("claim-regenerated")]
        regenerated["blocks"][0]["risks"] = ["capability_not_verified"]

        result = creative_intake.apply_creative_intake_transition(
            state,
            {
                "type": "regenerate_decomposition_draft",
                "decomposition": regenerated,
            },
        )

        self.assertEqual(result["stage"], "decomposition_draft")
        self.assertEqual(
            result["decomposition"]["blocks"][0]["en"],
            "regenerated runner wording",
        )
        self.assertTrue(
            all(not block["approved"] for block in result["decomposition"]["blocks"])
        )
        for before, after in zip(
            state["decomposition"]["blocks"],
            result["decomposition"]["blocks"],
            strict=True,
        ):
            for field in ("id", "category", "zh", "source", "locked", "semanticItems"):
                self.assertEqual(after[field], before[field])

    def test_decomposition_regeneration_rejects_semantic_or_root_lineage_changes(self):
        state = state_with_decomposition()
        mutations = (
            lambda value: value.__setitem__("profileVersionId", "other-version"),
            lambda value: value["blocks"][0].__setitem__("zh", "changed semantic"),
            lambda value: value["blocks"][0].__setitem__("semanticItems", []),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                candidate = copy.deepcopy(state["decomposition"])
                mutate(candidate)
                with self.assertRaisesRegex(
                    creative_intake.CreativeIntakeValidationError,
                    "decomposition",
                ):
                    creative_intake.apply_creative_intake_transition(
                        state,
                        {
                            "type": "regenerate_decomposition_draft",
                            "decomposition": candidate,
                        },
                    )

    def test_rule_and_evidence_refs_require_safe_identifiers(self):
        unsafe_identifiers = (
            "../claim",
            r"claim\escape",
            "claim:field",
            "claim/control\x00",
        )
        for identifier in unsafe_identifiers:
            with self.subTest(identifier=identifier):
                value = state_with_decomposition()
                value["decomposition"]["blocks"][0]["ruleRefs"] = [
                    rule_ref(identifier)
                ]
                with self.assertRaisesRegex(
                    creative_intake.CreativeIntakeValidationError,
                    "safe identifier",
                ):
                    creative_intake.normalize_creative_intake(value)

                value = state_with_decomposition()
                value["decomposition"]["blocks"][0]["ruleRefs"] = [
                    {
                        **rule_ref("claim.valid_1~x"),
                        "evidenceRefs": [identifier],
                    }
                ]
                with self.assertRaisesRegex(
                    creative_intake.CreativeIntakeValidationError,
                    "safe identifier",
                ):
                    creative_intake.normalize_creative_intake(value)

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


def rule_ref(claim_id):
    return {
        "claimId": claim_id,
        "fieldPath": "prompting.language",
        "evidenceRefs": ["snapshot-official"],
    }


def decomposition_block(
    identifier, *, source=None, locked=False, semantic_items=None
):
    semantic_items = semantic_items or []
    return {
        "id": identifier,
        "category": "action",
        "zh": "奔跑",
        "en": "running",
        "source": source or {"type": "ai", "refId": None},
        "locked": locked,
        "approved": False,
        "reason": "From the brief.",
        "risks": (
            ["generic_fallback_no_approved_model_rule"]
            if identifier == BLOCK_IDS[0]
            else []
        ),
        "ruleRefs": [] if identifier == BLOCK_IDS[0] else [rule_ref("claim-prompt-language")],
        "semanticItems": semantic_items,
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
    value["decomposition"] = approved_decomposition(value["brief"])
    value["decomposition"]["status"] = "draft"
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


def approved_decomposition(brief=None):
    brief = brief or confirmed_brief()
    semantic_items = [
        {
            "id": item["id"],
            "text": item["text"],
            "source": item["source"],
            "locked": item["locked"],
        }
        for item in brief["items"]
    ]
    semantic_items.extend(
        {
            "id": f"ai-addition-{index}",
            "text": text,
            "source": {"type": "ai", "refId": None},
            "locked": False,
        }
        for index, text in enumerate(brief["aiAdditions"])
    )
    blocks = []
    for index, identifier in enumerate(BLOCK_IDS):
        block_semantic_items = semantic_items if index == 0 else []
        source = (
            block_semantic_items[0]["source"]
            if block_semantic_items
            else {"type": "ai", "refId": None}
        )
        blocks.append({
            **decomposition_block(
                identifier,
                source=source,
                locked=any(item["locked"] for item in block_semantic_items),
                semantic_items=block_semantic_items,
            ),
            "approved": True,
        })
    return {
        "status": "draft",
        "briefContentSha256": creative_intake.canonical_brief_sha256(brief),
        "profileVersionId": "profile-version-7",
        "profileContentSha256": "a" * 64,
        "blocks": blocks,
    }


def draft_decomposition():
    value = approved_decomposition()
    for block in value["blocks"]:
        block["approved"] = False
    return value


def confirmed_brief():
    value = draft_brief(locked=True)
    value["status"] = "confirmed"
    return value


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
