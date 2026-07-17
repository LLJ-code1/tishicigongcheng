import copy
import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import edit_engine  # noqa: E402
import model_profiles  # noqa: E402
import recipe as recipe_domain  # noqa: E402
from edit_engine import (  # noqa: E402
    BLOCK_IDS,
    EditEngineError,
    apply_edit_preview,
    create_undo_version,
    parse_instruction,
    preview_edit,
    recipe_hash,
)


def complete_recipe() -> dict:
    values = {
        "identity": ("adult female mage", "成年女魔法师"),
        "appearance": ("long silver hair, blue eyes", "银色长发，蓝色眼睛"),
        "clothing": ("navy mage robe, leather boots", "深蓝魔法师长袍，皮靴"),
        "expression": ("calm expression", "平静表情"),
        "action": ("standing, holding a staff", "站立，手持法杖"),
        "interaction": ("", ""),
        "scene": ("ancient observatory", "古老天文台"),
        "camera": ("medium full shot", "中全景"),
        "lighting": ("soft moonlight", "柔和月光"),
        "style": ("anime style", "动漫风格"),
        "effects": ("magical particles", "魔法粒子"),
        "quality": ("masterpiece, best quality, score_7", "杰作，最佳质量，score_7"),
        "negative": ("low quality, blurry", "低质量，模糊"),
    }
    profile = model_profiles.load_model_profile()
    parameters = recipe_domain.resolve_parameters(
        model_default=model_profiles.profile_default_parameters(profile),
        task_preset={
            "resolution": {"width": 1024, "height": 1024},
            "generationSeed": 42,
        },
        required_keys=recipe_domain.REQUIRED_PARAMETER_KEYS,
    )
    return recipe_domain.build_recipe(
        model=model_profiles.model_reference(profile),
        prompts={
            "positiveEn": "adult female mage in an ancient observatory",
            "positiveZh": "古老天文台中的成年女魔法师",
            "negativeEn": "low quality, blurry",
            "negativeZh": "低质量，模糊",
        },
        blocks=[
            {
                "id": block_id,
                "en": values[block_id][0],
                "zh": values[block_id][1],
                "locked": False,
                "source": "fixture",
            }
            for block_id in BLOCK_IDS
        ],
        parameters=parameters,
    )


def block(recipe: dict, block_id: str) -> dict:
    return next(item for item in recipe["blocks"] if item["id"] == block_id)


class RecipeHashTests(unittest.TestCase):
    def test_thirteen_block_contract(self):
        self.assertEqual(len(BLOCK_IDS), 13)
        self.assertEqual(
            BLOCK_IDS,
            (
                "identity",
                "appearance",
                "clothing",
                "expression",
                "action",
                "interaction",
                "scene",
                "camera",
                "lighting",
                "style",
                "effects",
                "quality",
                "negative",
            ),
        )

    def test_hash_is_stable_across_dictionary_key_order(self):
        original = complete_recipe()
        reordered = dict(reversed(list(original.items())))
        reordered["model"] = dict(reversed(list(original["model"].items())))
        reordered["parameters"] = dict(
            reversed(list(original["parameters"].items()))
        )
        reordered["blocks"] = [
            dict(reversed(list(item.items()))) for item in original["blocks"]
        ]
        self.assertEqual(recipe_hash(original), recipe_hash(reordered))
        self.assertRegex(recipe_hash(original), r"^[0-9a-f]{64}$")
        self.assertIs(edit_engine.recipe_hash, recipe_domain.recipe_hash)

    def test_hash_covers_every_recipe_field(self):
        original = complete_recipe()
        changed = copy.deepcopy(original)
        changed["parameters"]["generationSeed"]["value"] = 43
        self.assertNotEqual(recipe_hash(original), recipe_hash(changed))

    def test_hash_rejects_non_json_and_non_finite_values(self):
        recipe = complete_recipe()
        recipe["metadata"]["bad"] = float("nan")
        with self.assertRaisesRegex(recipe_domain.RecipeValidationError, "finite"):
            recipe_hash(recipe)


class InstructionParsingTests(unittest.TestCase):
    def test_known_common_edit_terms_map_to_expected_blocks(self):
        cases = {
            "把服装换成红色连衣裙": "clothing",
            "把表情改成微笑": "expression",
            "把动作改成挥手": "action",
            "把场景换成森林": "scene",
            "把镜头换成俯拍": "camera",
            "把灯光改成暖光": "lighting",
            "把画风改成水彩": "style",
            "负面提示词加上水印": "negative",
        }
        for instruction, expected_id in cases.items():
            with self.subTest(instruction=instruction):
                parsed = parse_instruction(instruction)
                self.assertEqual(parsed["affectedIds"], [expected_id])
                self.assertEqual(parsed["status"], "resolved")
                self.assertFalse(parsed["requiresModel"])

    def test_free_text_is_explicitly_unresolved(self):
        parsed = parse_instruction("让整张图更有电影感和张力")
        self.assertEqual(parsed["affectedIds"], [])
        self.assertEqual(parsed["status"], "unresolved")
        self.assertTrue(parsed["requiresModel"])
        self.assertEqual(parsed["unresolved"][0]["code"], "no_deterministic_mapping")

    def test_named_block_with_unknown_value_requires_model(self):
        parsed = parse_instruction("把服装换成星河编织的礼服")
        self.assertEqual(parsed["affectedIds"], ["clothing"])
        self.assertEqual(parsed["status"], "unresolved")
        self.assertEqual(
            parsed["unresolved"][0]["code"], "replacement_requires_model"
        )

    def test_only_change_scope_can_be_followed_by_value_clause(self):
        parsed = parse_instruction("只改服装，换成红色连衣裙")
        self.assertEqual(parsed["affectedIds"], ["clothing"])
        self.assertEqual(parsed["status"], "resolved")

    def test_unknown_tail_clause_is_not_silently_ignored(self):
        parsed = parse_instruction("把场景换成森林，再增加会飞的机械雪花")
        self.assertEqual(parsed["affectedIds"], ["scene"])
        self.assertEqual(parsed["status"], "unresolved")
        self.assertIn(
            "clause_requires_model",
            {item["code"] for item in parsed["unresolved"]},
        )

    def test_do_not_modify_is_preservation_not_an_edit_target(self):
        parsed = parse_instruction("不要修改人物，镜头换成俯拍")
        self.assertEqual(parsed["affectedIds"], ["camera"])
        self.assertEqual(parsed["status"], "resolved")
        self.assertEqual(
            set(parsed["preservedIds"]), set(edit_engine.PERSON_BLOCK_IDS)
        )

    def test_longer_clothing_value_does_not_trigger_camera_full_body(self):
        parsed = parse_instruction("把服装换成全身盔甲")
        self.assertEqual(parsed["affectedIds"], ["clothing"])
        self.assertEqual(parsed["status"], "resolved")

    def test_ambiguous_non_negative_do_not_is_not_guessed(self):
        parsed = parse_instruction("不要红色连衣裙")
        self.assertEqual(parsed["status"], "unresolved")
        self.assertEqual(parsed["unresolved"][0]["code"], "ambiguous_negation")

    def test_instruction_size_control_character_and_unicode_bounds(self):
        for value, code in (
            ("改" * 2_001, "instruction_too_long"),
            ("改服装\x00", "invalid_instruction"),
            ("\ud800", "invalid_instruction"),
        ):
            with self.subTest(code=code):
                with self.assertRaises(EditEngineError) as raised:
                    parse_instruction(value)
                self.assertEqual(raised.exception.code, code)


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.recipe = complete_recipe()
        self.base_hash = recipe_hash(self.recipe)

    def test_clothing_preview_locks_all_twelve_unnamed_blocks(self):
        preview = preview_edit(
            "只把服装换成红色连衣裙", self.recipe, self.base_hash
        )
        self.assertTrue(preview["ready"])
        self.assertEqual(preview["affectedIds"], ["clothing"])
        self.assertEqual(len(preview["lockedIds"]), 12)
        self.assertNotIn("clothing", preview["lockedIds"])
        self.assertEqual(preview["recipeHash"], self.base_hash)
        self.assertEqual(preview["baseHash"], self.base_hash)
        self.assertRegex(preview["previewHash"], r"^[0-9a-f]{64}$")
        self.assertEqual(len(preview["diffs"]), 1)
        diff = preview["diffs"][0]
        self.assertEqual(diff["id"], "clothing")
        self.assertEqual(diff["before"]["en"], "navy mage robe, leather boots")
        self.assertEqual(diff["after"]["en"], "red dress")
        self.assertEqual(diff["after"]["zh"], "红色连衣裙")
        self.assertEqual(diff["changedFields"], ["en", "zh"])
        self.assertEqual(self.recipe, complete_recipe())

    def test_preview_hash_is_deterministic(self):
        first = preview_edit("把表情改成微笑", self.recipe, self.base_hash)
        second = preview_edit("把表情改成微笑", self.recipe, self.base_hash)
        self.assertEqual(first, second)
        self.assertEqual(first["previewHash"], second["previewHash"])

    def test_people_unchanged_overhead_only_affects_camera(self):
        preview = preview_edit("人物不变，换成俯拍", self.recipe, self.base_hash)
        self.assertTrue(preview["ready"])
        self.assertEqual(preview["affectedIds"], ["camera"])
        self.assertEqual(preview["diffs"][0]["after"]["en"], "overhead shot")
        for person_id in edit_engine.PERSON_BLOCK_IDS:
            self.assertIn(person_id, preview["lockedIds"])
            self.assertIn(person_id, preview["parse"]["preservedIds"])

    def test_keep_camera_strengthen_night_affects_scene_and_lighting(self):
        preview = preview_edit(
            "保留构图，加强夜景氛围", self.recipe, self.base_hash
        )
        self.assertTrue(preview["ready"])
        self.assertEqual(preview["affectedIds"], ["scene", "lighting"])
        self.assertIn("camera", preview["lockedIds"])
        self.assertIn("camera", preview["parse"]["preservedIds"])
        diffs = {item["id"]: item for item in preview["diffs"]}
        self.assertIn("nighttime setting", diffs["scene"]["after"]["en"])
        self.assertIn("moody night lighting", diffs["lighting"]["after"]["en"])

    def test_only_scope_narrows_night_atmosphere_to_named_block(self):
        preview = preview_edit(
            "只改灯光，加强夜景氛围", self.recipe, self.base_hash
        )
        self.assertTrue(preview["ready"])
        self.assertEqual(preview["affectedIds"], ["lighting"])
        self.assertIn("scene", preview["lockedIds"])
        self.assertEqual(preview["parse"]["exclusiveTargetIds"], ["lighting"])

    def test_only_scope_and_second_explicit_target_conflict(self):
        preview = preview_edit(
            "只改服装，把场景换成森林", self.recipe, self.base_hash
        )
        self.assertFalse(preview["ready"])
        self.assertIn(
            "outside_exclusive_scope",
            {item["code"] for item in preview["conflicts"]},
        )
        self.assertNotIn("scene", preview["affectedIds"])

    def test_negative_remove_is_exact_and_deterministic(self):
        preview = preview_edit(
            "从负面提示词去掉模糊", self.recipe, self.base_hash
        )
        self.assertTrue(preview["ready"])
        self.assertEqual(preview["affectedIds"], ["negative"])
        self.assertEqual(preview["diffs"][0]["after"]["en"], "low quality")
        self.assertEqual(preview["diffs"][0]["after"]["zh"], "低质量")

    def test_unknown_replacement_has_scope_but_no_fake_change(self):
        preview = preview_edit(
            "把服装换成星河编织的礼服", self.recipe, self.base_hash
        )
        self.assertFalse(preview["ready"])
        self.assertEqual(preview["affectedIds"], ["clothing"])
        self.assertTrue(preview["parse"]["requiresModel"])
        self.assertEqual(preview["diffs"][0]["before"], preview["diffs"][0]["after"])

    def test_stale_or_malformed_base_hash_is_rejected(self):
        with self.assertRaises(EditEngineError) as malformed:
            preview_edit("把表情改成微笑", self.recipe, "not-a-hash")
        self.assertEqual(malformed.exception.code, "invalid_base_hash")
        with self.assertRaises(EditEngineError) as stale:
            preview_edit("把表情改成微笑", self.recipe, "0" * 64)
        self.assertEqual(stale.exception.code, "base_hash_mismatch")
        self.assertEqual(stale.exception.status, 409)

    def test_missing_target_block_is_normalized_to_explicit_empty_block(self):
        partial = complete_recipe()
        partial["blocks"] = [
            item for item in partial["blocks"] if item["id"] != "clothing"
        ]
        preview = preview_edit(
            "把服装换成红色连衣裙", partial, recipe_hash(partial)
        )
        self.assertTrue(preview["ready"])
        self.assertEqual(preview["affectedIds"], ["clothing"])
        self.assertEqual(preview["diffs"][0]["before"]["en"], "")
        self.assertEqual(preview["diffs"][0]["after"]["en"], "red dress")
        self.assertNotIn("clothing", {item["id"] for item in partial["blocks"]})

    def test_explicitly_locked_target_is_a_blocking_conflict(self):
        block(self.recipe, "clothing")["locked"] = True
        preview = preview_edit(
            "把服装换成红色连衣裙", self.recipe, recipe_hash(self.recipe)
        )
        self.assertFalse(preview["ready"])
        self.assertEqual(preview["conflicts"][0]["code"], "target_already_locked")

    def test_preserve_and_change_same_block_is_a_conflict(self):
        preview = preview_edit(
            "人物不变，把服装换成红色连衣裙", self.recipe, self.base_hash
        )
        self.assertFalse(preview["ready"])
        self.assertIn(
            "preserved_block_targeted",
            {item["code"] for item in preview["conflicts"]},
        )

    def test_locked_dependency_conflict_is_reported_without_action_drift(self):
        block(self.recipe, "action")["en"] = "low-angle stance, holding a staff"
        base = recipe_hash(self.recipe)
        before_action = copy.deepcopy(block(self.recipe, "action"))
        preview = preview_edit("人物不变，换成俯拍", self.recipe, base)
        self.assertFalse(preview["ready"])
        self.assertIn(
            "locked_block_conflict", {item["code"] for item in preview["conflicts"]}
        )
        self.assertEqual(block(self.recipe, "action"), before_action)

    def test_multiple_values_for_atomic_replacement_are_not_silently_chosen(self):
        preview = preview_edit(
            "把服装换成红色连衣裙和白色连衣裙", self.recipe, self.base_hash
        )
        self.assertFalse(preview["ready"])
        self.assertIn(
            "multiple_replacements", {item["code"] for item in preview["conflicts"]}
        )

    def test_thirteen_block_boundary_rejects_unknown_or_duplicate_ids(self):
        invalid = complete_recipe()
        invalid["blocks"][0]["id"] = "arbitrary"
        with self.assertRaises(EditEngineError) as unknown:
            preview_edit("把服装换成红色连衣裙", invalid, "0" * 64)
        self.assertEqual(unknown.exception.code, "invalid_recipe")

        duplicate = complete_recipe()
        duplicate["blocks"][1]["id"] = "identity"
        with self.assertRaises(EditEngineError) as repeated:
            preview_edit("把服装换成红色连衣裙", duplicate, "0" * 64)
        self.assertEqual(repeated.exception.code, "invalid_recipe")


class ApplyAndUndoTests(unittest.TestCase):
    def setUp(self):
        self.recipe = complete_recipe()
        self.base_hash = recipe_hash(self.recipe)
        self.preview = preview_edit(
            "把服装换成红色连衣裙", self.recipe, self.base_hash
        )

    def test_apply_changes_only_affected_block_and_records_parent(self):
        original = copy.deepcopy(self.recipe)
        version = apply_edit_preview(
            self.recipe, self.preview, parent_version=7, new_version=8
        )
        self.assertEqual(version["parentVersion"], 7)
        self.assertEqual(version["version"], 8)
        self.assertEqual(version["change"]["type"], "instruction")
        self.assertEqual(version["change"]["affectedIds"], ["clothing"])
        self.assertEqual(version["change"]["baseHash"], self.base_hash)
        self.assertEqual(
            version["change"]["resultHash"], recipe_hash(version["recipe"])
        )
        self.assertEqual(block(version["recipe"], "clothing")["en"], "red dress")
        for block_id in BLOCK_IDS:
            if block_id != "clothing":
                self.assertEqual(
                    block(version["recipe"], block_id), block(original, block_id)
                )
        self.assertEqual(version["recipe"]["parameters"], original["parameters"])
        self.assertEqual(self.recipe, original)

    def test_preview_hash_survives_browser_json_number_round_trip(self):
        self.assertIs(type(self.preview["parse"]["confidence"]), int)
        transported = json.loads(json.dumps(self.preview, ensure_ascii=False))

        version = apply_edit_preview(
            self.recipe,
            transported,
            parent_version=7,
            new_version=8,
        )

        self.assertEqual(block(version["recipe"], "clothing")["en"], "red dress")

    def test_tampered_after_value_is_rejected_even_with_recomputed_preview_hash(self):
        tampered = copy.deepcopy(self.preview)
        tampered["diffs"][0]["after"]["en"] = "attacker controlled prompt"
        tampered["previewHash"] = edit_engine._compute_preview_hash(tampered)
        with self.assertRaises(EditEngineError) as raised:
            apply_edit_preview(self.recipe, tampered, parent_version=7)
        self.assertEqual(raised.exception.code, "preview_tampered")

    def test_out_of_scope_diff_is_rejected_even_with_recomputed_preview_hash(self):
        tampered = copy.deepcopy(self.preview)
        action_before = copy.deepcopy(block(self.recipe, "action"))
        action_after = copy.deepcopy(action_before)
        action_after["en"] = "silently changed action"
        tampered["diffs"].append(
            {
                "id": "action",
                "label": "姿势与动作",
                "before": action_before,
                "after": action_after,
                "changed": True,
                "changedFields": ["en"],
            }
        )
        tampered["previewHash"] = edit_engine._compute_preview_hash(tampered)
        with self.assertRaises(EditEngineError) as raised:
            apply_edit_preview(self.recipe, tampered, parent_version=7)
        self.assertEqual(raised.exception.code, "preview_tampered")

    def test_invalid_preview_hash_is_rejected_before_apply(self):
        tampered = copy.deepcopy(self.preview)
        tampered["previewHash"] = "0" * 64
        with self.assertRaises(EditEngineError) as raised:
            apply_edit_preview(self.recipe, tampered, parent_version=7)
        self.assertEqual(raised.exception.code, "preview_tampered")

    def test_unresolved_preview_cannot_be_confirmed(self):
        preview = preview_edit(
            "把服装换成星河编织的礼服", self.recipe, self.base_hash
        )
        with self.assertRaises(EditEngineError) as raised:
            apply_edit_preview(self.recipe, preview, parent_version=7)
        self.assertEqual(raised.exception.code, "preview_not_ready")

    def test_recipe_change_after_preview_is_a_conflict(self):
        changed = copy.deepcopy(self.recipe)
        block(changed, "scene")["en"] = "changed elsewhere"
        with self.assertRaises(EditEngineError) as raised:
            apply_edit_preview(changed, self.preview, parent_version=7)
        self.assertEqual(raised.exception.code, "base_hash_mismatch")

    def test_undo_copies_parent_recipe_into_new_append_only_version(self):
        parent_recipe = complete_recipe()
        edited = apply_edit_preview(
            parent_recipe,
            preview_edit(
                "把服装换成红色连衣裙",
                parent_recipe,
                recipe_hash(parent_recipe),
            ),
            parent_version=7,
            new_version=8,
        )["recipe"]
        edited["parameters"]["generationSeed"]["value"] = 999
        parent_before = copy.deepcopy(parent_recipe)
        edited_before = copy.deepcopy(edited)

        undo = create_undo_version(
            edited,
            parent_recipe,
            current_version=8,
            parent_version=7,
            new_version=9,
        )

        self.assertEqual(undo["version"], 9)
        self.assertEqual(undo["parentVersion"], 8)
        self.assertEqual(undo["recipe"], parent_recipe)
        self.assertIsNot(undo["recipe"], parent_recipe)
        self.assertEqual(undo["change"]["type"], "undo")
        self.assertEqual(undo["change"]["undoneVersion"], 8)
        self.assertEqual(undo["change"]["restoredFromVersion"], 7)
        self.assertEqual(undo["change"]["historyPolicy"], "append-only")
        self.assertEqual(undo["change"]["affectedIds"], ["clothing"])
        self.assertIn("parameters", undo["change"]["topLevelChangedFields"])
        self.assertEqual(parent_recipe, parent_before)
        self.assertEqual(edited, edited_before)


if __name__ == "__main__":
    unittest.main()
