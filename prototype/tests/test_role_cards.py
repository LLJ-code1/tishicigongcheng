import copy
import unittest
from pathlib import Path

import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import recipe  # noqa: E402
import model_profiles  # noqa: E402
from role_cards import (  # noqa: E402
    RoleCardError,
    preview_local_role_edit,
    preview_relationship_edit,
)


def role_cards():
    return {
        "schemaVersion": 1,
        "roles": [
            {"id": "role-a", "name": "A", "tags": ["red hair"], "locked": False},
            {"id": "role-b", "name": "B", "tags": ["blue hair"], "locked": False},
        ],
        "relationships": [
            {
                "id": "relation-a-b",
                "fromRoleId": "role-a",
                "toRoleId": "role-b",
                "kind": "friends",
                "description": "standing together",
                "locked": True,
            }
        ],
    }


def complete_recipe():
    profile = model_profiles.load_model_profile()
    return recipe.build_recipe(
        model=model_profiles.model_reference(profile),
        prompts={"positiveEn": "1girl", "positiveZh": "女孩", "negativeEn": "", "negativeZh": ""},
        blocks=[],
        parameters=recipe.resolve_parameters(
            model_default=model_profiles.profile_default_parameters(profile),
            task_preset={"resolution": {"width": 1024, "height": 1024}, "generationSeed": 1},
            required_keys=recipe.REQUIRED_PARAMETER_KEYS,
        ),
    )


class RoleCardTests(unittest.TestCase):
    def test_recipe_v1_is_preserved_and_v2_requires_explicit_role_cards(self):
        legacy = recipe.normalize_recipe(complete_recipe())
        self.assertEqual(legacy["schemaVersion"], 1)
        self.assertNotIn("roleCards", legacy)
        v2 = copy.deepcopy(legacy)
        v2["schemaVersion"] = 2
        v2["roleCards"] = role_cards()
        normalized_v2 = recipe.normalize_recipe(v2)
        self.assertEqual(normalized_v2["roleCards"]["roles"][0]["id"], "role-a")
        projected = recipe.project_recipe_to_prompt_version(
            normalized_v2, block_projection="canonical-thirteen"
        )
        self.assertEqual(projected["metadata"]["recipeSchemaVersion"], 2)
        self.assertEqual(recipe.extract_recipe_from_prompt_version(projected), normalized_v2)
        legacy["roleCards"] = role_cards()
        with self.assertRaises(recipe.RecipeValidationError):
            recipe.normalize_recipe(legacy)

    def test_local_role_edit_requires_confirmation_and_never_changes_other_roles(self):
        preview = preview_local_role_edit(
            role_cards(), "role-a", {"tags": ["green hair"]}
        )
        self.assertFalse(preview["applied"])
        self.assertTrue(preview["requiresConfirmation"])
        self.assertEqual(preview["lockedRoleIds"], ["role-b"])
        applied = preview_local_role_edit(
            role_cards(), "role-a", {"tags": ["green hair"]}, confirm_affected=True
        )
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["roleCards"]["roles"][1]["tags"], ["blue hair"])
        self.assertEqual(applied["roleCards"]["relationships"][0]["description"], "standing together")

    def test_rejects_locked_target_and_relationships_to_missing_roles(self):
        cards = role_cards()
        cards["roles"][0]["locked"] = True
        with self.assertRaises(RoleCardError):
            preview_local_role_edit(cards, "role-a", {"name": "Changed"})

    def test_relationship_edit_requires_confirmation_and_never_changes_roles(self):
        cards = role_cards()
        cards["relationships"][0]["locked"] = False
        preview = preview_relationship_edit(
            cards, "relation-a-b", {"description": "walking together"}
        )
        self.assertFalse(preview["applied"])
        self.assertEqual(preview["affectedRoleIds"], ["role-a", "role-b"])
        applied = preview_relationship_edit(
            cards,
            "relation-a-b",
            {"description": "walking together"},
            confirm_affected=True,
        )
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["roleCards"]["roles"], cards["roles"])
        self.assertEqual(
            applied["roleCards"]["relationships"][0]["description"],
            "walking together",
        )
        cards = role_cards()
        cards["relationships"][0]["toRoleId"] = "missing"
        with self.assertRaises(RoleCardError):
            preview_local_role_edit(cards, "role-a", {"name": "Changed"})
