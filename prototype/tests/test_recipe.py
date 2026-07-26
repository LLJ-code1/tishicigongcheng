import copy
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import model_profiles  # noqa: E402
import recipe  # noqa: E402


def complete_recipe():
    profile = model_profiles.load_model_profile()
    parameters = recipe.resolve_parameters(
        model_default=model_profiles.profile_default_parameters(profile),
        task_preset={
            "resolution": {"width": 1024, "height": 1024},
            "generationSeed": 123456,
        },
        required_keys=recipe.REQUIRED_PARAMETER_KEYS,
    )
    return {
        "kind": recipe.RECIPE_KIND,
        "schemaVersion": recipe.RECIPE_SCHEMA_VERSION,
        "model": model_profiles.model_reference(profile),
        "prompts": {
            "positiveEn": "masterpiece, best quality, score_7, 1girl",
            "positiveZh": "杰作、最佳质量、一名女性",
            "negativeEn": "worst quality, low quality",
            "negativeZh": "最差质量、低质量",
        },
        "blocks": [
            {
                "id": "identity",
                "en": "1girl, adult woman",
                "zh": "一名成年女性",
                "locked": True,
                "weight": 100,
                "source": "user",
                "confidence": 100,
            },
            {
                "id": "clothing",
                "en": "blue dress",
                "zh": "蓝色连衣裙",
                "locked": False,
                "weight": 90,
                "source": "task",
                "confidence": 90,
            },
            {
                "id": "negative",
                "en": "worst quality",
                "zh": "最差质量",
                "locked": False,
                "weight": 100,
                "source": "model",
                "confidence": 100,
            },
        ],
        "loras": [],
        "parameters": parameters,
        "randomPlan": None,
        "instructionHistory": [],
        "imageRefs": [],
        "sourceRefs": [],
        "metadata": {"purpose": "test", "nested": {"b": 2, "a": 1}},
    }


class RecipeBlockTests(unittest.TestCase):
    def test_schema_defines_the_product_required_thirteen_blocks(self):
        self.assertEqual(
            recipe.BLOCK_IDS,
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
        definitions = recipe.get_block_definitions()
        self.assertEqual(len(definitions), 13)
        self.assertTrue(definitions[-1]["negative"])
        definitions[0]["label"] = "mutated"
        self.assertEqual(recipe.get_block_definitions()[0]["label"], "人物身份")

    def test_normalization_fills_missing_blocks_and_uses_canonical_order(self):
        normalized = recipe.normalize_blocks(
            [
                {"id": "negative", "en": "bad hands"},
                {"id": "identity", "en": "1girl", "locked": True},
            ]
        )
        self.assertEqual([item["id"] for item in normalized], list(recipe.BLOCK_IDS))
        self.assertEqual(normalized[0]["en"], "1girl")
        self.assertTrue(normalized[0]["locked"])
        self.assertEqual(normalized[1]["en"], "")
        self.assertEqual(normalized[-1]["en"], "bad hands")

    def test_unknown_duplicate_and_invalid_block_values_fail_closed(self):
        with self.assertRaisesRegex(recipe.RecipeValidationError, "unsupported block"):
            recipe.normalize_blocks([{"id": "not-a-real-block"}])
        with self.assertRaisesRegex(recipe.RecipeValidationError, "duplicate block"):
            recipe.normalize_blocks([{"id": "scene"}, {"id": "scene"}])
        with self.assertRaisesRegex(recipe.RecipeValidationError, "finite number"):
            recipe.normalize_blocks([{"id": "scene", "weight": float("nan")}])
        with self.assertRaisesRegex(recipe.RecipeValidationError, "boolean"):
            recipe.normalize_blocks([{"id": "scene", "locked": "false"}])

    def test_legacy_inflation_is_explicit_about_irrecoverable_combined_fields(self):
        normalized = recipe.inflate_legacy_blocks(
            [
                {
                    "id": "appearance",
                    "en": "silver hair, blue dress",
                    "zh": "银发、蓝色连衣裙",
                },
                {"id": "pose", "en": "smiling, running"},
            ]
        )
        by_id = {item["id"]: item for item in normalized}
        self.assertEqual(by_id["appearance"]["en"], "silver hair, blue dress")
        self.assertEqual(by_id["clothing"]["en"], "")
        self.assertEqual(by_id["action"]["en"], "smiling, running")
        self.assertEqual(
            by_id["appearance"]["metadata"]["compatibilitySource"],
            "legacy-ten-block-v1",
        )

    def test_workbench_thirteen_ids_round_trip_without_merging_content(self):
        workbench = [
            {
                "id": block_id,
                "en": f"{block_id}-en",
                "zh": f"{block_id}-zh",
                "locked": block_id == "outfit",
            }
            for block_id in recipe.WORKBENCH_BLOCK_ORDER
        ]
        canonical = recipe.inflate_workbench_blocks(workbench)
        by_id = {item["id"]: item for item in canonical}
        self.assertEqual(by_id["identity"]["en"], "subject-en")
        self.assertEqual(by_id["clothing"]["en"], "outfit-en")
        self.assertEqual(by_id["action"]["en"], "pose-en")
        self.assertEqual(by_id["camera"]["en"], "composition-en")
        self.assertTrue(by_id["clothing"]["locked"])

        projected = recipe.project_blocks_to_workbench(canonical)
        self.assertEqual(
            [item["id"] for item in projected], list(recipe.WORKBENCH_BLOCK_ORDER)
        )
        self.assertEqual(
            [item["en"] for item in projected], [item["en"] for item in workbench]
        )


class ParameterResolutionTests(unittest.TestCase):
    def test_priority_and_every_shadowed_value_are_visible(self):
        resolved = recipe.resolve_parameters(
            model_default={"cfg": 5.5, "steps": 30},
            lora_requirement={"cfg": 5.0, "steps": 28},
            task_preset={"cfg": 4.5, "steps": 32},
            manual_override={"cfg": 6.0},
        )
        self.assertEqual(resolved["cfg"]["value"], 6.0)
        self.assertEqual(resolved["cfg"]["source"], "manual_override")
        self.assertEqual(
            [item["source"] for item in resolved["cfg"]["overridden"]],
            ["task_preset", "lora_requirement", "model_default"],
        )
        self.assertEqual(resolved["steps"]["value"], 32)
        self.assertEqual(resolved["steps"]["source"], "task_preset")

    def test_equal_shadowed_value_is_still_reported(self):
        resolved = recipe.resolve_parameters(
            model_default={"sampler": "Euler"},
            manual_override={"sampler": "Euler"},
        )
        self.assertEqual(resolved["sampler"]["source"], "manual_override")
        self.assertTrue(resolved["sampler"]["overridden"][0]["sameValue"])

    def test_invalid_explicit_override_does_not_fall_through(self):
        with self.assertRaisesRegex(recipe.RecipeValidationError, "must be a string"):
            recipe.resolve_parameters(
                model_default={"sampler": "Euler"},
                manual_override={"sampler": None},
            )
        with self.assertRaisesRegex(recipe.RecipeValidationError, "multiples of 8"):
            recipe.resolve_parameters(
                manual_override={"resolution": {"width": 513, "height": 768}}
            )

    def test_required_parameters_must_resolve(self):
        with self.assertRaisesRegex(recipe.RecipeValidationError, "missing required"):
            recipe.resolve_parameters(
                model_default={"sampler": "Euler"},
                required_keys=("sampler", "scheduler"),
            )

    def test_empty_wrong_layer_type_is_not_silently_treated_as_missing(self):
        with self.assertRaisesRegex(recipe.RecipeValidationError, "must be an object"):
            recipe.resolve_parameters(manual_override=[])


class RecipeNormalizationTests(unittest.TestCase):
    def test_builder_produces_an_immediately_valid_canonical_recipe(self):
        payload = complete_recipe()
        built = recipe.build_recipe(
            model=payload["model"],
            prompts=payload["prompts"],
            blocks=payload["blocks"],
            parameters=payload["parameters"],
            metadata=payload["metadata"],
        )
        self.assertEqual(built, recipe.normalize_recipe(payload))

    def test_complete_recipe_normalizes_and_round_trips(self):
        normalized = recipe.normalize_recipe(complete_recipe())
        self.assertEqual(normalized["schemaVersion"], 1)
        self.assertEqual(len(normalized["blocks"]), 13)
        self.assertEqual(normalized["model"]["versionId"], 3004063)
        self.assertIsNone(normalized["model"]["profileVersionId"])
        self.assertIsNone(normalized["model"]["profileContentSha256"])
        self.assertEqual(normalized["parameters"]["steps"]["value"], 30)
        self.assertEqual(
            normalized["parameters"]["resolution"]["source"], "task_preset"
        )

    def test_exact_profile_lineage_is_paired_normalized_and_hashed(self):
        first = complete_recipe()
        first["model"].update(
            {
                "profileVersionId": "profile-version-7",
                "profileContentSha256": "A" * 64,
            }
        )
        normalized = recipe.normalize_recipe(first)
        self.assertEqual(
            normalized["model"]["profileVersionId"], "profile-version-7"
        )
        self.assertEqual(
            normalized["model"]["profileContentSha256"], "a" * 64
        )

        second = copy.deepcopy(first)
        second["model"]["profileContentSha256"] = "b" + "A" * 63
        self.assertNotEqual(recipe.recipe_hash(first), recipe.recipe_hash(second))

        for missing in ("profileVersionId", "profileContentSha256"):
            with self.subTest(missing=missing):
                invalid = copy.deepcopy(first)
                invalid["model"].pop(missing)
                with self.assertRaisesRegex(
                    recipe.RecipeValidationError, "must be present together"
                ):
                    recipe.normalize_recipe(invalid)

    def test_hash_is_stable_for_key_and_block_input_order(self):
        first = complete_recipe()
        second = copy.deepcopy(first)
        second["blocks"] = list(reversed(second["blocks"]))
        second["metadata"] = {"nested": {"a": 1, "b": 2}, "purpose": "test"}
        self.assertEqual(recipe.recipe_hash(first), recipe.recipe_hash(second))
        self.assertEqual(len(recipe.recipe_hash(first)), 64)

    def test_hash_canonicalizes_overridden_parameter_source_order(self):
        first = complete_recipe()
        first["parameters"]["cfg"] = {
            "value": 6.0,
            "source": "manual_override",
            "overridden": [
                {"source": "task_preset", "value": 5.8},
                {"source": "model_default", "value": 5.5},
            ],
        }
        second = copy.deepcopy(first)
        second["parameters"]["cfg"]["overridden"].reverse()
        self.assertEqual(recipe.recipe_hash(first), recipe.recipe_hash(second))

    def test_hash_changes_when_reproducible_content_changes(self):
        first = complete_recipe()
        second = copy.deepcopy(first)
        second["parameters"]["generationSeed"]["value"] += 1
        self.assertNotEqual(recipe.recipe_hash(first), recipe.recipe_hash(second))

    def test_unknown_fields_and_incomplete_parameters_are_rejected(self):
        payload = complete_recipe()
        payload["typo"] = True
        with self.assertRaisesRegex(recipe.RecipeValidationError, "unsupported fields"):
            recipe.normalize_recipe(payload)

        payload = complete_recipe()
        del payload["parameters"]["generationSeed"]
        with self.assertRaisesRegex(recipe.RecipeValidationError, "missing required"):
            recipe.normalize_recipe(payload)

    def test_lora_requires_exact_asset_identity_and_strict_hash(self):
        payload = complete_recipe()
        payload["loras"] = [
            {
                "assetId": "local:lora-1",
                "name": "Line Style",
                "versionId": None,
                "filename": None,
                "sha256": None,
                "weight": 0.8,
                "triggerWords": ["line art", "line art"],
                "enabled": True,
                "compatibilityStatus": "candidate",
                "sourceRefs": [],
            }
        ]
        normalized = recipe.normalize_recipe(payload)
        self.assertEqual(normalized["loras"][0]["triggerWords"], ["line art"])

        payload["loras"][0]["sha256"] = "not-a-hash"
        with self.assertRaisesRegex(recipe.RecipeValidationError, "SHA-256"):
            recipe.normalize_recipe(payload)


class CompatibilityProjectionTests(unittest.TestCase):
    def test_projection_represents_every_v1_block_in_the_ten_block_editor(self):
        blocks = [
            {
                "id": block_id,
                "en": f"{block_id}-en",
                "zh": f"{block_id}-zh",
                "locked": block_id == "clothing",
            }
            for block_id in recipe.BLOCK_IDS
        ]
        projected = recipe.project_blocks_to_legacy(blocks)
        self.assertEqual([item["id"] for item in projected], list(recipe.LEGACY_BLOCK_ORDER))
        by_id = {item["id"]: item for item in projected}
        self.assertIn("appearance-en", by_id["appearance"]["en"])
        self.assertIn("clothing-en", by_id["appearance"]["en"])
        self.assertTrue(by_id["appearance"]["locked"])
        self.assertIn("expression-en", by_id["pose"]["en"])
        self.assertIn("action-en", by_id["pose"]["en"])
        self.assertIn("interaction-en", by_id["pose"]["en"])
        represented = {
            block_id
            for item in projected
            for block_id in item["metadata"]["recipeBlockIds"]
        }
        self.assertEqual(represented, set(recipe.BLOCK_IDS))

    def test_prompt_version_projection_embeds_hash_verified_canonical_recipe(self):
        original = complete_recipe()
        projected = recipe.project_recipe_to_prompt_version(
            original,
            source="manual",
            base_version=4,
            base_updated_at="2026-07-17T00:00:00+00:00",
            metadata={"instruction": "只改服装"},
        )
        self.assertEqual(projected["baseVersion"], 4)
        self.assertEqual(len(projected["blocks"]), 10)
        restored = recipe.extract_recipe_from_prompt_version(projected)
        self.assertEqual(restored, recipe.normalize_recipe(original))
        self.assertEqual(
            projected["metadata"]["recipeHash"], recipe.recipe_hash(original)
        )

    def test_prompt_version_can_target_workbench_thirteen_without_changing_hash(self):
        original = complete_recipe()
        projected = recipe.project_recipe_to_prompt_version(
            original, block_projection="workbench-thirteen"
        )
        self.assertEqual(
            [item["id"] for item in projected["blocks"]],
            list(recipe.WORKBENCH_BLOCK_ORDER),
        )
        self.assertEqual(
            recipe.extract_recipe_from_prompt_version(projected),
            recipe.normalize_recipe(original),
        )

        with self.assertRaisesRegex(recipe.RecipeValidationError, "unsupported block projection"):
            recipe.project_recipe_to_prompt_version(
                original, block_projection="made-up-format"
            )

    def test_embedded_recipe_tampering_is_detected(self):
        projected = recipe.project_recipe_to_prompt_version(complete_recipe())
        projected["metadata"]["recipe"]["prompts"]["positiveEn"] = "tampered"
        with self.assertRaisesRegex(recipe.RecipeValidationError, "hash does not match"):
            recipe.extract_recipe_from_prompt_version(projected)

    def test_projection_metadata_cannot_replace_integrity_fields(self):
        with self.assertRaisesRegex(recipe.RecipeValidationError, "reserved"):
            recipe.project_recipe_to_prompt_version(
                complete_recipe(), metadata={"recipeHash": "attacker-controlled"}
            )
        with self.assertRaisesRegex(recipe.RecipeValidationError, "must be an object"):
            recipe.project_recipe_to_prompt_version(complete_recipe(), metadata=[])


if __name__ == "__main__":
    unittest.main()
