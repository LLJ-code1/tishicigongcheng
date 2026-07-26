import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import creative_intake  # noqa: E402
from recipe import BLOCK_DEFINITIONS, BLOCK_IDS  # noqa: E402


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "model_adapted_decomposition"
    / "approved_profile.json"
)


def confirmed_intake():
    brief = {
        "status": "confirmed",
        "summary": "一名成年女性侦探在雨夜拾级而上。",
        "items": [
            {
                "id": "brief-action",
                "category": "action",
                "text": "右手握伞，左脚踏上台阶",
                "source": {"type": "user", "refId": None},
                "locked": True,
            }
        ],
        "aiAdditions": [],
        "openQuestions": [],
    }
    return {
        "stage": "model_selected",
        "brief": brief,
        "selectedModelProfileId": "profile-anima",
    }


def confirmed_intake_with_all_semantic_sources():
    value = confirmed_intake()
    value["brief"]["items"].extend(
        [
            {
                "id": "brief-clothing",
                "category": "clothing",
                "text": "穿深蓝色防水风衣",
                "source": {"type": "user", "refId": None},
                "locked": False,
            },
            {
                "id": "brief-scene",
                "category": "environment",
                "text": "雨夜的石阶街道",
                "source": {"type": "image", "refId": "image-scene"},
                "locked": False,
            },
        ]
    )
    value["brief"]["aiAdditions"] = ["伞沿连续滴水"]
    return value


def profile_snapshot():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def provider_output(*, use_rule=True):
    blocks = []
    for definition in BLOCK_DEFINITIONS:
        is_action = definition.id == "action"
        blocks.append(
            {
                "id": definition.id,
                "category": definition.label,
                "zh": "右手握伞，左脚踏上台阶" if is_action else "",
                "en": (
                    "umbrella in right hand, left foot stepping onto stairs"
                    if is_action
                    else ""
                ),
                "source": (
                    {"type": "user", "refId": None}
                    if is_action
                    else {"type": "ai", "refId": None}
                ),
                "locked": is_action,
                "approved": False,
                "reason": "按模型支持的简洁英文标签表达。" if is_action else "无已确认语义。",
                "risks": [] if use_rule else ["未找到已批准的专用规则，使用通用表达。"],
                "ruleRefs": (
                    [
                        {
                            "claimId": "claim-language",
                            "fieldPath": "prompting.language",
                            "evidenceRefs": ["snapshot-official"],
                        }
                    ]
                    if is_action and use_rule
                    else []
                ),
                "semanticItemIds": ["brief-action"] if is_action else [],
            }
        )
    return {"blocks": blocks}


class ModelAdaptedDecompositionTests(unittest.TestCase):
    def import_subject(self):
        import model_adapted_decomposition

        return model_adapted_decomposition

    def test_generates_canonical_blocks_and_trusted_lineage(self):
        subject = self.import_subject()
        calls = []

        def provider(messages):
            calls.append(messages)
            value = provider_output()
            value.update(
                {
                    "status": "confirmed",
                    "briefContentSha256": "f" * 64,
                    "profileVersionId": "fabricated",
                    "profileContentSha256": "b" * 64,
                }
            )
            return value

        intake = confirmed_intake()
        result = subject.generate_model_adapted_decomposition(
            intake=intake,
            profile_snapshot=profile_snapshot(),
            provider=provider,
        )

        self.assertEqual(tuple(block["id"] for block in result["blocks"]), BLOCK_IDS)
        self.assertEqual(
            [block["category"] for block in result["blocks"]],
            [definition.label for definition in BLOCK_DEFINITIONS],
        )
        self.assertEqual(result["status"], "draft")
        self.assertEqual(
            result["briefContentSha256"],
            creative_intake.canonical_brief_sha256(intake["brief"]),
        )
        self.assertEqual(result["profileVersionId"], "profile-version-7")
        self.assertEqual(result["profileContentSha256"], "a" * 64)
        self.assertNotIn("semanticItemIds", result["blocks"][4])
        self.assertEqual(len(calls), 1)

    def test_messages_expose_only_approved_rules_as_applicable_rules(self):
        subject = self.import_subject()
        messages = subject.build_decomposition_messages(
            confirmed_intake()["brief"],
            profile_snapshot()["approvedRules"],
            profile_snapshot()["warnings"],
        )
        payload = json.loads(messages[-1]["content"])

        self.assertEqual(
            [rule["claimId"] for rule in payload["approvedRules"]],
            ["claim-language"],
        )
        self.assertEqual(
            {warning["claimId"] for warning in payload["warnings"]},
            {"claim-proposed-cfg", "claim-rejected-artist"},
        )
        self.assertNotIn("approvedRules", payload["warnings"][0])

    def test_messages_include_exact_thirteen_id_to_category_contract(self):
        subject = self.import_subject()
        messages = subject.build_decomposition_messages(
            confirmed_intake_with_all_semantic_sources()["brief"],
            profile_snapshot()["approvedRules"],
            profile_snapshot()["warnings"],
        )
        payload = json.loads(messages[-1]["content"])

        self.assertEqual(
            payload["blockDefinitions"],
            [
                {"id": "identity", "category": "人物身份"},
                {"id": "appearance", "category": "身体与外观特征"},
                {"id": "clothing", "category": "服装与配饰"},
                {"id": "expression", "category": "表情"},
                {"id": "action", "category": "姿势与动作"},
                {"id": "interaction", "category": "人物互动关系"},
                {"id": "scene", "category": "场景与环境"},
                {"id": "camera", "category": "构图与镜头"},
                {"id": "lighting", "category": "光影与色彩"},
                {"id": "style", "category": "风格与艺术家"},
                {"id": "effects", "category": "道具与特效"},
                {"id": "quality", "category": "质量增强"},
                {"id": "negative", "category": "负向约束"},
            ],
        )
        self.assertEqual(
            payload["semanticItems"][-1],
            {
                "id": "ai-addition-0",
                "category": None,
                "text": "伞沿连续滴水",
                "source": {"type": "ai", "refId": None},
                "locked": False,
            },
        )

    def test_normalizes_exact_approved_rule_lineage(self):
        subject = self.import_subject()
        result = subject.normalize_decomposition_output(
            provider_output(),
            intake=confirmed_intake(),
            profile_snapshot=profile_snapshot(),
        )

        self.assertEqual(
            result["blocks"][4]["ruleRefs"],
            [
                {
                    "claimId": "claim-language",
                    "fieldPath": "prompting.language",
                    "evidenceRefs": ["snapshot-official"],
                }
            ],
        )

    def test_allows_visible_generic_fallback_without_rule_refs(self):
        subject = self.import_subject()
        result = subject.normalize_decomposition_output(
            provider_output(use_rule=False),
            intake=confirmed_intake(),
            profile_snapshot=profile_snapshot(),
        )

        self.assertEqual(result["blocks"][4]["ruleRefs"], [])
        self.assertIn(
            "generic_fallback_no_approved_model_rule",
            result["blocks"][4]["risks"],
        )

    def test_server_appends_exact_generic_fallback_marker(self):
        subject = self.import_subject()
        output = provider_output(use_rule=False)
        output["blocks"][4]["risks"] = ["无风险"]

        result = subject.normalize_decomposition_output(
            output,
            intake=confirmed_intake(),
            profile_snapshot=profile_snapshot(),
        )

        self.assertEqual(
            result["blocks"][4]["risks"],
            ["无风险", "generic_fallback_no_approved_model_rule"],
        )

    def test_empty_blocks_do_not_receive_generic_fallback_marker(self):
        subject = self.import_subject()

        result = subject.normalize_decomposition_output(
            provider_output(),
            intake=confirmed_intake(),
            profile_snapshot=profile_snapshot(),
        )

        self.assertEqual(result["blocks"][0]["semanticItems"], [])
        self.assertEqual(result["blocks"][0]["zh"], "")
        self.assertEqual(result["blocks"][0]["en"], "")
        self.assertNotIn(
            "generic_fallback_no_approved_model_rule",
            result["blocks"][0]["risks"],
        )

    def test_generic_fallback_reserves_one_of_one_hundred_risk_slots(self):
        subject = self.import_subject()
        output = provider_output(use_rule=False)
        output["blocks"][4]["risks"] = [f"risk-{index}" for index in range(99)]

        result = subject.normalize_decomposition_output(
            output,
            intake=confirmed_intake(),
            profile_snapshot=profile_snapshot(),
        )

        self.assertEqual(len(result["blocks"][4]["risks"]), 100)
        self.assertEqual(
            result["blocks"][4]["risks"][-1],
            "generic_fallback_no_approved_model_rule",
        )

    def test_full_risk_array_needing_fallback_gets_one_repair_attempt(self):
        subject = self.import_subject()
        calls = []

        def provider(messages):
            calls.append(messages)
            output = provider_output(use_rule=False)
            if len(calls) == 1:
                output["blocks"][4]["risks"] = [
                    f"risk-{index}" for index in range(100)
                ]
            return output

        result = subject.generate_model_adapted_decomposition(
            intake=confirmed_intake(),
            profile_snapshot=profile_snapshot(),
            provider=provider,
        )

        self.assertEqual(result["status"], "draft")
        self.assertEqual(len(calls), 2)

    def test_rejects_unapproved_or_mismatched_rule_lineage(self):
        subject = self.import_subject()
        mutations = (
            ("claimId", "claim-proposed-cfg"),
            ("claimId", "claim-rejected-artist"),
            ("claimId", "claim-fabricated"),
            ("fieldPath", "prompting.negative"),
            ("evidenceRefs", ["snapshot-fabricated"]),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                output = provider_output()
                output["blocks"][4]["ruleRefs"][0][field] = value
                with self.assertRaises(subject.DecompositionError) as caught:
                    subject.normalize_decomposition_output(
                        output,
                        intake=confirmed_intake(),
                        profile_snapshot=profile_snapshot(),
                    )
                self.assertEqual(caught.exception.code, "unapproved_rule")

    def test_rejects_missing_extra_duplicate_or_reordered_blocks(self):
        subject = self.import_subject()
        values = []
        missing = provider_output()
        missing["blocks"].pop()
        values.append(missing)
        extra = provider_output()
        extra["blocks"].append(copy.deepcopy(extra["blocks"][-1]))
        extra["blocks"][-1]["id"] = "other"
        values.append(extra)
        duplicate = provider_output()
        duplicate["blocks"][1] = copy.deepcopy(duplicate["blocks"][0])
        values.append(duplicate)
        reordered = provider_output()
        reordered["blocks"][0], reordered["blocks"][1] = (
            reordered["blocks"][1],
            reordered["blocks"][0],
        )
        values.append(reordered)

        for value in values:
            with self.subTest(ids=[block["id"] for block in value["blocks"]]):
                with self.assertRaises(subject.DecompositionError) as caught:
                    subject.normalize_decomposition_output(
                        value,
                        intake=confirmed_intake(),
                        profile_snapshot=profile_snapshot(),
                    )
                self.assertEqual(caught.exception.code, "invalid_blocks")

    def test_locked_fact_must_be_verbatim_in_exactly_its_semantic_category(self):
        subject = self.import_subject()
        changed = provider_output()
        changed["blocks"][4]["zh"] = "左手握伞，右脚踏上台阶"
        removed = provider_output()
        removed["blocks"][4]["zh"] = "左脚踏上台阶"
        moved = provider_output()
        moved["blocks"][4]["semanticItemIds"] = []
        moved["blocks"][6]["semanticItemIds"] = ["brief-action"]
        moved["blocks"][6]["zh"] = "右手握伞，左脚踏上台阶"

        for value in (changed, removed, moved):
            with self.subTest(value=value):
                with self.assertRaises(subject.DecompositionError) as caught:
                    subject.normalize_decomposition_output(
                        value,
                        intake=confirmed_intake(),
                        profile_snapshot=profile_snapshot(),
                    )
                self.assertEqual(caught.exception.code, "locked_fact_changed")

    def test_all_confirmed_items_and_ai_additions_form_a_closed_semantic_set(self):
        subject = self.import_subject()
        intake = confirmed_intake_with_all_semantic_sources()
        output = provider_output()
        output["blocks"][2].update(
            {
                "zh": "穿深蓝色防水风衣",
                "en": "dark blue waterproof trench coat",
                "source": {"type": "user", "refId": None},
                "risks": ["通用表达，未使用模型专用规则。"],
                "semanticItemIds": ["brief-clothing"],
            }
        )
        output["blocks"][6].update(
            {
                "zh": "雨夜的石阶街道",
                "en": "rainy stone stair street at night",
                "source": {"type": "image", "refId": "image-scene"},
                "risks": ["通用表达，未使用模型专用规则。"],
                "semanticItemIds": ["brief-scene"],
            }
        )
        output["blocks"][10].update(
            {
                "zh": "伞沿连续滴水",
                "en": "water dripping continuously from umbrella edge",
                "source": {"type": "ai", "refId": None},
                "risks": ["通用表达，未使用模型专用规则。"],
                "semanticItemIds": ["ai-addition-0"],
            }
        )

        result = subject.normalize_decomposition_output(
            output,
            intake=intake,
            profile_snapshot=profile_snapshot(),
        )

        self.assertEqual(result["blocks"][2]["zh"], "穿深蓝色防水风衣")
        self.assertEqual(
            result["blocks"][6]["source"],
            {"type": "image", "refId": "image-scene"},
        )
        self.assertEqual(result["blocks"][10]["zh"], "伞沿连续滴水")

    def test_canonical_block_preserves_multiple_fact_level_sources(self):
        subject = self.import_subject()
        intake = confirmed_intake()
        intake["brief"]["items"].append(
            {
                "id": "brief-image-action",
                "category": "action",
                "text": "黑伞高举过右肩",
                "source": {"type": "image", "refId": "image-action"},
                "locked": True,
            }
        )
        output = provider_output()
        output["blocks"][4]["zh"] = "右手握伞，左脚踏上台阶；黑伞高举过右肩"
        output["blocks"][4]["semanticItemIds"] = [
            "brief-action",
            "brief-image-action",
        ]

        result = subject.normalize_decomposition_output(
            output,
            intake=intake,
            profile_snapshot=profile_snapshot(),
        )

        self.assertEqual(
            result["blocks"][4]["semanticItems"],
            [
                {
                    "id": "brief-action",
                    "text": "右手握伞，左脚踏上台阶",
                    "source": {"type": "user", "refId": None},
                    "locked": True,
                },
                {
                    "id": "brief-image-action",
                    "text": "黑伞高举过右肩",
                    "source": {"type": "image", "refId": "image-action"},
                    "locked": True,
                },
            ],
        )
        self.assertEqual(
            result["blocks"][4]["source"],
            {"type": "user", "refId": None},
        )

    def test_rejects_missing_unlocked_item_ai_addition_or_unconfirmed_semantics(self):
        subject = self.import_subject()
        intake = confirmed_intake_with_all_semantic_sources()
        missing = provider_output()
        invented = provider_output()
        invented["blocks"][4]["zh"] += "，身后跟着一名儿童"

        for value in (missing, invented):
            with self.subTest(value=value):
                with self.assertRaises(subject.DecompositionError) as caught:
                    subject.normalize_decomposition_output(
                        value,
                        intake=intake,
                        profile_snapshot=profile_snapshot(),
                    )
                self.assertEqual(caught.exception.code, "semantic_violation")

    def test_unlocked_semantic_item_keeps_category_and_source_mapping(self):
        subject = self.import_subject()
        intake = confirmed_intake()
        intake["brief"]["items"] = [
            {
                "id": "brief-clothing",
                "category": "clothing",
                "text": "穿深蓝色防水风衣",
                "source": {"type": "image", "refId": "image-clothing"},
                "locked": False,
            }
        ]
        output = provider_output()
        output["blocks"][4].update(
            {
                "zh": "",
                "en": "",
                "source": {"type": "ai", "refId": None},
                "locked": False,
                "reason": "无已确认语义。",
                "ruleRefs": [],
                "semanticItemIds": [],
            }
        )
        output["blocks"][2].update(
            {
                "zh": "穿深蓝色防水风衣",
                "en": "dark blue waterproof trench coat",
                "source": {"type": "user", "refId": None},
                "risks": ["通用表达，未使用模型专用规则。"],
                "semanticItemIds": ["brief-clothing"],
            }
        )

        with self.assertRaises(subject.DecompositionError) as caught:
            subject.normalize_decomposition_output(
                output,
                intake=intake,
                profile_snapshot=profile_snapshot(),
            )

        self.assertEqual(caught.exception.code, "semantic_violation")

    def test_semantic_chinese_rejects_target_model_terminology(self):
        subject = self.import_subject()
        output = provider_output()
        output["blocks"][4]["zh"] = "Anima 模型适配：右手握伞，左脚踏上台阶"

        with self.assertRaises(subject.DecompositionError) as caught:
            subject.normalize_decomposition_output(
                output,
                intake=confirmed_intake(),
                profile_snapshot=profile_snapshot(),
            )

        self.assertEqual(caught.exception.code, "semantic_violation")

    def test_semantic_violation_is_not_retried(self):
        subject = self.import_subject()
        calls = []

        def provider(messages):
            calls.append(messages)
            output = provider_output()
            output["blocks"][4]["zh"] += "，身后跟着一名儿童"
            return output

        with self.assertRaises(subject.DecompositionError) as caught:
            subject.generate_model_adapted_decomposition(
                intake=confirmed_intake(),
                profile_snapshot=profile_snapshot(),
                provider=provider,
            )

        self.assertEqual(caught.exception.code, "semantic_violation")
        self.assertEqual(len(calls), 1)

    def test_invalid_json_gets_exactly_one_bounded_repair(self):
        subject = self.import_subject()
        calls = []

        def provider(messages):
            calls.append(messages)
            return "not-json" if len(calls) == 1 else json.dumps(provider_output())

        result = subject.generate_model_adapted_decomposition(
            intake=confirmed_intake(),
            profile_snapshot=profile_snapshot(),
            provider=provider,
        )

        self.assertEqual(result["status"], "draft")
        self.assertEqual(len(calls), 2)
        repair = json.loads(calls[1][-1]["content"])
        self.assertEqual(repair["task"], "repair_model_adapted_decomposition_json")
        self.assertEqual(repair["errorCode"], "invalid_provider_output")

    def test_second_malformed_response_raises_without_third_call(self):
        subject = self.import_subject()
        calls = []

        def provider(messages):
            calls.append(messages)
            return "still-not-json"

        with self.assertRaises(subject.DecompositionError) as caught:
            subject.generate_model_adapted_decomposition(
                intake=confirmed_intake(),
                profile_snapshot=profile_snapshot(),
                provider=provider,
            )

        self.assertEqual(caught.exception.code, "invalid_provider_output")
        self.assertEqual(len(calls), 2)

    def test_locked_fact_violation_is_not_retried(self):
        subject = self.import_subject()
        calls = []

        def provider(messages):
            calls.append(messages)
            value = provider_output()
            value["blocks"][4]["zh"] = "左手握伞，右脚踏上台阶"
            return value

        with self.assertRaises(subject.DecompositionError) as caught:
            subject.generate_model_adapted_decomposition(
                intake=confirmed_intake(),
                profile_snapshot=profile_snapshot(),
                provider=provider,
            )

        self.assertEqual(caught.exception.code, "locked_fact_changed")
        self.assertEqual(len(calls), 1)

    def test_provider_exception_is_wrapped_without_retry(self):
        subject = self.import_subject()
        calls = 0

        def provider(messages):
            nonlocal calls
            calls += 1
            raise TimeoutError("offline")

        with self.assertRaises(subject.DecompositionError) as caught:
            subject.generate_model_adapted_decomposition(
                intake=confirmed_intake(),
                profile_snapshot=profile_snapshot(),
                provider=provider,
            )

        self.assertEqual(caught.exception.code, "provider_failed")
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
