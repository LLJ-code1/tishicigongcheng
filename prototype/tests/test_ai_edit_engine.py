import copy
import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_edit_engine import (  # noqa: E402
    AIEditError,
    apply_ai_edit_preview,
    create_ai_edit_preview,
    propose_ai_edit,
)
from recipe import recipe_hash  # noqa: E402
from test_edit_engine import complete_recipe  # noqa: E402


SETTINGS = {
    "localTextUrl": "http://127.0.0.1:8080/v1",
    "localTextModel": "local-model",
}


def response(changes):
    return {
        "choices": [
            {"message": {"content": json.dumps({"changes": changes}, ensure_ascii=False)}}
        ]
    }


def valid_changes():
    return [
        {
            "blockId": "scene",
            "en": "a shattered castle in ruins under heavy rain",
            "zh": "暴雨中的破碎城堡废墟",
            "reason": "用户要求替换环境，并加入破碎状态与暴雨天气。",
        },
        {
            "blockId": "lighting",
            "en": "cold storm light, lightning-lit clouds",
            "zh": "冷冽暴雨天光，闪电照亮云层",
            "reason": "暴雨环境需要对应的阴冷光线。",
        },
        {
            "blockId": "effects",
            "en": "heavy rain, wind-blown mist",
            "zh": "倾盆大雨，风吹雨雾",
            "reason": "将大雨落实为可绘制的天气特效。",
        },
    ]


class AIProposalTests(unittest.TestCase):
    def test_returns_complete_related_block_changes(self):
        result = propose_ai_edit(
            "环境改成破碎城堡，下着大雨",
            complete_recipe(),
            SETTINGS,
            transport=lambda *_: response(valid_changes()),
        )

        self.assertEqual(
            [item["blockId"] for item in result["changes"]],
            ["scene", "lighting", "effects"],
        )
        self.assertIn("破碎城堡", result["changes"][0]["zh"])

    def test_rejects_invalid_change_contracts(self):
        cases = [
            {"changes": [{**valid_changes()[0], "extra": "no"}]},
            {"changes": [{**valid_changes()[0], "blockId": "unknown"}]},
            {"changes": [valid_changes()[0], valid_changes()[0]]},
            {"changes": [{**valid_changes()[0], "zh": "  "}]},
            {"changes": [{**valid_changes()[0], "reason": ""}]},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(AIEditError) as raised:
                    propose_ai_edit(
                        "修改环境",
                        complete_recipe(),
                        SETTINGS,
                        transport=lambda *_args, payload=payload: response(
                            payload["changes"]
                        ),
                    )
                self.assertEqual(raised.exception.code, "invalid_ai_edit_output")

    def test_rejects_unchanged_block(self):
        recipe = complete_recipe()
        scene = next(item for item in recipe["blocks"] if item["id"] == "scene")
        unchanged = {
            "blockId": "scene",
            "en": scene["en"],
            "zh": scene["zh"],
            "reason": "没有真正修改",
        }
        with self.assertRaises(AIEditError) as raised:
            propose_ai_edit(
                "修改环境",
                recipe,
                SETTINGS,
                transport=lambda *_: response([unchanged]),
            )
        self.assertEqual(raised.exception.code, "ai_edit_no_change")

    def test_provider_configuration_error_is_preserved(self):
        with self.assertRaises(AIEditError) as raised:
            propose_ai_edit("修改环境", complete_recipe(), {})
        self.assertEqual(raised.exception.code, "provider_not_configured")


class AIPreviewLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.recipe = complete_recipe()
        self.preview = create_ai_edit_preview(
            "环境改成破碎城堡，下着大雨",
            self.recipe,
            recipe_hash(self.recipe),
            SETTINGS,
            "local",
            "anima",
            transport=lambda *_: response(valid_changes()),
        )

    def test_preview_exposes_signed_chinese_diffs_and_locks_other_blocks(self):
        self.assertTrue(self.preview["ready"])
        self.assertEqual(
            self.preview["affectedIds"], ["scene", "lighting", "effects"]
        )
        self.assertEqual(len(self.preview["lockedIds"]), 10)
        self.assertEqual(self.preview["diffs"][0]["before"]["zh"], "古老天文台")
        self.assertIn("破碎城堡", self.preview["diffs"][0]["after"]["zh"])
        self.assertTrue(self.preview["diffs"][0]["reason"])
        self.assertRegex(self.preview["previewHash"], r"^[0-9a-f]{64}$")

    def test_apply_merges_only_signed_affected_blocks(self):
        result = apply_ai_edit_preview(self.recipe, self.preview, parent_version=7)
        by_id = {item["id"]: item for item in result["recipe"]["blocks"]}
        self.assertIn("破碎城堡", by_id["scene"]["zh"])
        self.assertEqual(by_id["identity"]["zh"], "成年女魔法师")
        self.assertEqual(result["parentVersion"], 7)

    def test_apply_rejects_tampering_and_stale_recipe(self):
        for mutation in ("after", "reason", "affected"):
            tampered = copy.deepcopy(self.preview)
            if mutation == "after":
                tampered["diffs"][0]["after"]["zh"] = "普通城堡"
            elif mutation == "reason":
                tampered["diffs"][0]["reason"] = "伪造原因"
            else:
                tampered["affectedIds"].append("camera")
            with self.subTest(mutation=mutation):
                with self.assertRaises(AIEditError) as raised:
                    apply_ai_edit_preview(self.recipe, tampered, parent_version=7)
                self.assertEqual(raised.exception.code, "preview_tampered")

        stale = copy.deepcopy(self.recipe)
        stale["blocks"][0]["zh"] = "已变化"
        with self.assertRaises(AIEditError) as raised:
            apply_ai_edit_preview(stale, self.preview, parent_version=7)
        self.assertEqual(raised.exception.code, "base_hash_mismatch")


if __name__ == "__main__":
    unittest.main()
