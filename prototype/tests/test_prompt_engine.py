import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prompt_engine import (  # noqa: E402
    PromptEngineError,
    build_text_decompose_system_prompt,
    build_user_message,
    build_text_expand_system_prompt,
    compile_normalized_blocks,
    decompose_text_prompt,
    expand_text_prompt,
    generate_random_text_prompt,
    normalize_text_decomposition,
    normalize_text_expansion,
    probe_text_provider,
    response_content,
    parse_model_json,
    prompt_item_key,
    resolve_text_provider,
    regenerate_prompt_block,
    regenerate_prompt_blocks,
    translate_pending_items,
    validate_expansion_quality,
)
from unicode15_data import (  # noqa: E402
    UNICODE15_VERSION,
    is_unicode15_assigned,
    lowercase_unicode15,
)


REQUIRED_BLOCKS = [
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
]


def valid_model_result():
    return {
        "positiveEn": "masterpiece, best quality, score_7, 1girl",
        "positiveZh": "杰作，最佳质量，一名女性角色",
        "negativeEn": "worst quality, low quality",
        "negativeZh": "最差质量，低质量",
        "relationEn": "She stands in the foreground beneath the rain.",
        "relationZh": "她站在雨中的前景。",
        "blocks": [
            {
                "id": block_id,
                "label": block_id,
                "en": "" if block_id == "artist" else f"{block_id} en",
                "zh": "" if block_id == "artist" else f"{block_id} zh",
                "weight": 75,
                "locked": True,
                "source": "model",
                "confidence": 77,
            }
            for block_id in REQUIRED_BLOCKS
        ],
        "checks": {
            "preservedUserIntent": True,
            "bilingualAligned": True,
            "conflicts": [],
            "assumptions": [],
        },
    }


def rich_random_model_result():
    payload = valid_model_result()
    values = {
        "subject": "1girl, adult woman, original character",
        "appearance": (
            "long silver hair, amber eyes, navy embroidered coat, ivory blouse, "
            "layered pleated skirt, leather gloves, bronze earrings, calm expression"
        ),
        "pose": (
            "walking down stone steps, looking over one shoulder, left hand on railing, "
            "right hand holding a folded letter, wind-swept posture, balanced stride"
        ),
        "scene": (
            "mountain observatory, blue hour, open brass telescope, stone terrace, "
            "distant snowy peaks, drifting clouds, old maps on a table, layered background"
        ),
        "composition": (
            "medium full shot, low angle, off-center subject, diagonal stairs, "
            "foreground railing, deep perspective"
        ),
        "lighting": (
            "cool twilight ambience, warm lantern key light, thin rim light, "
            "soft facial highlights, brass reflections, deep blue shadows"
        ),
        "effects": (
            "wind-blown paper, floating dust, faint breath vapor, moving coat hem, "
            "subtle lens bloom, distant cloud motion"
        ),
    }
    for block in payload["blocks"]:
        if block["id"] in values:
            block["en"] = values[block["id"]]
            block["zh"] = values[block["id"]] + " 中文"
    return payload


class PromptAssemblyTests(unittest.TestCase):
    def test_prompt_item_key_matches_frontend_nfkc_deduplication(self):
        self.assertEqual(prompt_item_key("Straße"), "strasse")
        self.assertEqual(prompt_item_key("Éclair"), prompt_item_key("e\u0301clair"))
        self.assertEqual(prompt_item_key("ＣＡＴ"), "cat")
        self.assertEqual(prompt_item_key("  red\t dress "), "red dress")
        self.assertEqual(prompt_item_key("red\ufeffdress"), "red dress")
        self.assertEqual(prompt_item_key("red\u0085dress"), "red dress")
        self.assertEqual(prompt_item_key("red\u200bdress"), "red\u200bdress")
        self.assertEqual(prompt_item_key("\u001cred\u001c"), "\u001cred\u001c")
        self.assertEqual(UNICODE15_VERSION, "15.0.0")
        self.assertTrue(is_unicode15_assigned(0x41))
        self.assertFalse(is_unicode15_assigned(0x1C89))
        self.assertEqual(lowercase_unicode15("AΣẞ"), "aσß")
        self.assertEqual(prompt_item_key("\u1c89"), "\u1c89")
        self.assertEqual(prompt_item_key("\ua7f1"), "\ua7f1")
        self.assertEqual(prompt_item_key("A\u0295Σ"), "a\u0295σ")
        self.assertEqual(prompt_item_key("AΣ\u0295"), "aσ\u0295")

        compiled = compile_normalized_blocks(
            [
                {
                    "id": "scene",
                    "en": "Straße, Éclair, red  dress, \ufeffblue sky\ufeff",
                    "zh": "ＣＡＴ",
                },
                {
                    "id": "effects",
                    "en": "STRASSE, e\u0301clair, red dress, blue sky",
                    "zh": "CAT",
                },
                {"id": "negative", "en": "bad", "zh": "差"},
            ],
            "",
            "",
        )

        self.assertEqual(
            compiled["positiveEn"],
            "Straße, Éclair, red  dress, blue sky",
        )
        self.assertEqual(compiled["positiveZh"], "ＣＡＴ")

    def test_random_generation_uses_two_model_calls_and_creative_density(self):
        calls = []

        def fake_transport(url, body, headers, timeout):
            calls.append(body)
            content = (
                {
                    "concept": "astronomer leaving a mountain observatory",
                    "subject": ["adult woman", "original character"],
                    "appearance": ["silver hair", "blue eyes"],
                    "outfit": ["embroidered coat", "leather boots"],
                    "expression": ["focused gaze"],
                    "pose": ["walking down steps"],
                    "interaction": ["holding a letter"],
                    "scene": ["mountain observatory", "blue hour"],
                    "composition": ["low angle", "diagonal stairs"],
                    "lighting": ["lantern key light", "cool twilight"],
                    "effects": ["wind-blown paper", "breath vapor"],
                    "relation": "She descends from the observatory into the wind.",
                    "assumptions": ["随机原创角色"],
                }
                if len(calls) == 1
                else rich_random_model_result()
            )
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(content, ensure_ascii=False)
                        }
                    }
                ]
            }

        result = generate_random_text_prompt(
            {
                "apiTextUrl": "https://api.deepseek.com",
                "apiTextModel": "deepseek-v4-flash",
                "apiTextKey": "secret",
            },
            provider="api",
            transport=fake_transport,
        )

        self.assertEqual(len(calls), 2)
        self.assertIn("random_visual_blueprint", calls[0]["messages"][1]["content"])
        self.assertIn(
            "compile_random_anima_prompt",
            calls[1]["messages"][1]["content"],
        )
        self.assertEqual(calls[1]["max_tokens"], 3000)
        self.assertEqual(calls[1]["thinking"], {"type": "disabled"})
        self.assertEqual(len(result["blocks"]), 13)
        self.assertGreater(len(result["positiveEn"]), 350)

    def test_decomposition_prompt_forbids_rewriting_or_adding_content(self):
        prompt = build_text_decompose_system_prompt()

        self.assertIn("不得补充、删减、润色或改写", prompt)
        self.assertIn("source", prompt)
        self.assertNotIn("视觉扩写", prompt)

    def test_normalizes_decomposition_with_exact_mixed_language_sources(self):
        result = normalize_text_decomposition(
            {
                "items": [
                    {
                        "source": "1girl",
                        "blockId": "subject",
                        "en": "one female character",
                        "zh": "一名女性角色",
                    },
                    {
                        "source": "站在雨中",
                        "blockId": "pose",
                        "en": "standing in the rain",
                        "zh": "站立在雨中",
                    },
                    {
                        "source": "mysterious_token",
                        "blockId": "unclassified",
                        "en": "mysterious token",
                        "zh": "神秘标记",
                    },
                ]
            },
            "1girl, 站在雨中, mysterious_token",
            provider="local",
        )

        block_map = {block["id"]: block for block in result["blocks"]}
        self.assertEqual(block_map["subject"]["en"], "1girl")
        self.assertEqual(block_map["pose"]["zh"], "站在雨中")
        self.assertEqual(block_map["unclassified"]["en"], "mysterious_token")
        self.assertEqual(result["checks"]["sourceCoverage"], 100)
        self.assertEqual(result["checks"]["assumptions"], [])

    def test_normalizes_compact_indexed_decomposition_without_repeating_source(self):
        result = normalize_text_decomposition(
            {
                "items": [
                    {
                        "index": 0,
                        "blockId": "composition",
                        "translation": "极端面部特写",
                    },
                    {
                        "index": 1,
                        "blockId": "lighting",
                        "translation": "dramatic moonlight",
                    },
                ]
            },
            "extreme close-up of face, 戏剧性的月光",
            provider="local",
        )

        block_map = {block["id"]: block for block in result["blocks"]}
        self.assertEqual(
            block_map["composition"]["en"],
            "extreme close-up of face",
        )
        self.assertEqual(block_map["composition"]["zh"], "极端面部特写")
        self.assertEqual(block_map["lighting"]["zh"], "戏剧性的月光")
        self.assertEqual(block_map["lighting"]["en"], "dramatic moonlight")

    def test_rejects_decomposition_when_a_source_fragment_is_missing(self):
        with self.assertRaises(PromptEngineError) as context:
            normalize_text_decomposition(
                {
                    "items": [
                        {
                            "source": "1girl",
                            "blockId": "subject",
                            "en": "1girl",
                            "zh": "一名女性角色",
                        }
                    ]
                },
                "1girl, raining",
            )

        self.assertEqual(context.exception.code, "source_coverage_failed")

    def test_decomposition_calls_provider_without_expansion_profile(self):
        captured = {}

        def fake_transport(url, body, headers, timeout):
            captured.update({"url": url, "body": body})
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "items": [
                                        {
                                            "source": "1girl",
                                            "blockId": "subject",
                                            "en": "1girl",
                                            "zh": "一名女性角色",
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

        result = decompose_text_prompt(
            "1girl",
            {
                "localTextUrl": "http://127.0.0.1:8080/v1",
                "localTextModel": "local-model",
            },
            transport=fake_transport,
        )

        self.assertEqual(result["blocks"][2]["en"], "1girl")
        request_text = json.dumps(captured["body"], ensure_ascii=False)
        self.assertIn("decompose_existing_prompt", request_text)
        self.assertNotIn("expansionLevel", request_text)

    def test_decomposition_retries_once_after_malformed_json(self):
        calls = []

        def fake_transport(url, body, headers, timeout):
            calls.append(body)
            content = (
                '{"items":[{"index":0'
                if len(calls) == 1
                else json.dumps(
                    {
                        "items": [
                            {
                                "index": 0,
                                "blockId": "subject",
                                "translation": "一名女性角色",
                            },
                            {
                                "index": 1,
                                "blockId": "scene",
                                "translation": "下雨",
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            )
            return {"choices": [{"message": {"content": content}}]}

        result = decompose_text_prompt(
            "1girl, raining",
            {
                "localTextUrl": "http://127.0.0.1:8080/v1",
                "localTextModel": "local-model",
            },
            transport=fake_transport,
        )

        self.assertEqual(len(calls), 2)
        self.assertIn("repair_decomposition_json", calls[1]["messages"][1]["content"])
        self.assertEqual(result["checks"]["sourceCoverage"], 100)

    def test_decomposition_splits_long_prompts_into_small_batches(self):
        calls = []

        def fake_transport(url, body, headers, timeout):
            request = json.loads(
                body["messages"][1]["content"].removeprefix("/no_think\n")
            )
            source_items = request["sourceItems"]
            calls.append(source_items)
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "items": [
                                        {
                                            "index": item["index"],
                                            "blockId": "unclassified",
                                            "translation": f"翻译 {item['index']}",
                                        }
                                        for item in source_items
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

        result = decompose_text_prompt(
            ", ".join(f"tag-{index}" for index in range(82)),
            {
                "localTextUrl": "http://127.0.0.1:8080/v1",
                "localTextModel": "local-model",
            },
            transport=fake_transport,
        )

        self.assertEqual(len(calls), 4)
        self.assertTrue(all(len(batch) <= 24 for batch in calls))
        self.assertEqual(
            [item["index"] for batch in calls for item in batch],
            list(range(82)),
        )
        self.assertEqual(result["checks"]["sourceCoverage"], 100)

    def test_builds_anima_prompt_from_five_modules_in_order(self):
        prompt = build_text_expand_system_prompt("anima")

        headings = [
            "# 核心视觉扩写规则",
            "# 拓展已有提示词模式",
            "# Tags 与自然语言混合编译",
            "# Anima 模型配置",
            "# 双语结构化输出",
        ]
        positions = [prompt.index(heading) for heading in headings]

        self.assertEqual(positions, sorted(positions))
        self.assertIn("中文和英文必须表达同一组视觉事实", prompt)
        self.assertIn("默认禁止使用 `(tag:1.2)`", prompt)
        self.assertNotIn("{{user_input}}", prompt)
        self.assertNotIn("{{target_model}}", prompt)
        self.assertNotIn("透明雨伞", prompt)
        self.assertNotIn("雨夜城市街道", prompt)
        self.assertLess(len(prompt), 7000)
        self.assertIn("30-50", prompt)
        self.assertIn("50-75", prompt)

    def test_user_message_carries_the_selected_expansion_level(self):
        message = json.loads(
            build_user_message(
                "1girl, raining",
                "anima",
                "",
                expansion_level="creative",
            )
        )

        self.assertEqual(message["expansionLevel"], "creative")

    def test_sparse_balanced_input_disables_unanchored_identity_and_location(self):
        message = json.loads(
            build_user_message(
                "1girl, raining",
                "anima",
                "",
                expansion_level="balanced",
            )
        )

        self.assertTrue(message["inputProfile"]["sparse"])
        self.assertFalse(
            message["inferencePolicy"]["allowStableAppearanceInvention"]
        )
        self.assertFalse(
            message["inferencePolicy"]["allowSpecificLocationInvention"]
        )
        self.assertIn(
            "open outdoor setting",
            message["inferencePolicy"]["neutralSceneScaffold"],
        )

    def test_compiled_review_file_matches_runtime_system_prompt(self):
        compiled_path = (
            Path(__file__).resolve().parents[1]
            / "prompts"
            / "compiled"
            / "text_expand_anima.md"
        )
        compiled = compiled_path.read_text(encoding="utf-8")
        runtime_prompt = build_text_expand_system_prompt("anima")

        self.assertEqual(
            compiled[compiled.index("# 核心视觉扩写规则") :].strip(),
            runtime_prompt,
        )

    def test_review_cases_cover_all_expansion_levels(self):
        cases_path = (
            Path(__file__).resolve().parents[1]
            / "prompts"
            / "review_cases"
            / "text_expand_cases.json"
        )
        cases = json.loads(cases_path.read_text(encoding="utf-8"))

        self.assertEqual(len(cases), 3)
        self.assertEqual(
            {case["expansionLevel"] for case in cases},
            {"strict", "balanced", "creative"},
        )
        self.assertTrue(all(case["acceptance"] for case in cases))

    def test_rejects_unknown_model_profile(self):
        with self.assertRaises(PromptEngineError) as context:
            build_text_expand_system_prompt("../../other")

        self.assertEqual(context.exception.code, "unsupported_model")

    def test_integrated_master_covers_detailed_source_rules(self):
        prompt = build_text_expand_system_prompt("anima")

        required_rules = [
            "题材识别与领域路由",
            "信息来源与优先级",
            "角色属性归属表",
            "左右手",
            "前景、中景、背景",
            "物理接触",
            "光源方向",
            "AI 补充项",
            "逐项自检",
        ]
        for rule in required_rules:
            self.assertIn(rule, prompt)


class ProviderConfigTests(unittest.TestCase):
    def test_strips_empty_or_populated_think_blocks_from_content(self):
        content = response_content(
            {
                "choices": [
                    {
                        "message": {
                            "content": "<think>hidden</think>\n\nOK"
                        }
                    }
                ]
            }
        )

        self.assertEqual(content, "OK")

    def test_extracts_a_json_object_from_a_small_text_wrapper(self):
        payload = parse_model_json('Result follows:\n{"ok": true}\nDone.')

        self.assertEqual(payload, {"ok": True})

    def test_removes_combining_low_line_noise_from_json_structure(self):
        payload = parse_model_json(
            '{"items":[{"index":64,\u0332"translation":"volumetric fog"\u0332}]}'
        )

        self.assertEqual(
            payload,
            {
                "items": [
                    {
                        "index": 64,
                        "translation": "volumetric fog",
                    }
                ]
            },
        )

    def test_resolves_local_openai_compatible_provider(self):
        config = resolve_text_provider(
            {
                "localTextUrl": "http://127.0.0.1:8080/v1/",
                "localTextModel": "local-model",
            },
            "local",
        )

        self.assertEqual(
            config["url"], "http://127.0.0.1:8080/v1/chat/completions"
        )
        self.assertEqual(config["model"], "local-model")
        self.assertNotIn("Authorization", config["headers"])

    def test_external_provider_requires_url_model_and_key(self):
        with self.assertRaises(PromptEngineError) as context:
            resolve_text_provider(
                {
                    "apiTextUrl": "https://example.com/v1",
                    "apiTextModel": "example-model",
                },
                "api",
            )

        self.assertEqual(context.exception.code, "provider_not_configured")

    def test_probes_an_openai_compatible_provider_with_a_tiny_request(self):
        captured = {}

        def fake_transport(url, body, headers, timeout):
            captured.update({"url": url, "body": body, "headers": headers})
            return {"choices": [{"message": {"content": "OK"}}]}

        result = probe_text_provider(
            {
                "localTextUrl": "http://127.0.0.1:8080/v1",
                "localTextModel": "prompt-local-14b",
            },
            "local",
            transport=fake_transport,
        )

        self.assertTrue(result["connected"])
        self.assertEqual(result["content"], "OK")
        self.assertEqual(captured["body"]["max_tokens"], 8)
        self.assertEqual(captured["body"]["temperature"], 0)
        self.assertTrue(
            captured["body"]["messages"][0]["content"].startswith(
                "/no_think\n"
            )
        )

    def test_disables_thinking_for_deepseek_provider_probe(self):
        captured = {}

        def fake_transport(url, body, headers, timeout):
            captured.update(body)
            return {"choices": [{"message": {"content": "OK"}}]}

        probe_text_provider(
            {
                "apiTextUrl": "https://api.deepseek.com",
                "apiTextModel": "deepseek-v4-flash",
                "apiTextKey": "secret",
            },
            "api",
            transport=fake_transport,
        )

        self.assertEqual(
            captured["thinking"],
            {"type": "disabled"},
        )


class PendingTranslationTests(unittest.TestCase):
    def test_translates_every_item_with_the_local_provider(self):
        captured = {}

        def fake_transport(url, body, headers, timeout):
            captured.update({"url": url, "body": body, "headers": headers})
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "translations": [
                                        {"id": "subject", "zh": "初音未来"},
                                        {
                                            "id": "appearance",
                                            "zh": "青绿色双马尾，青绿色眼睛",
                                        },
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

        result = translate_pending_items(
            [
                {"id": "subject", "en": "Hatsune Miku"},
                {
                    "id": "appearance",
                    "en": "aqua twintails, aqua eyes",
                },
            ],
            {
                "localTextUrl": "http://127.0.0.1:8080/v1",
                "localTextModel": "local-model",
            },
            transport=fake_transport,
        )

        self.assertEqual(result["translations"][0]["zh"], "初音未来")
        self.assertEqual(len(result["translations"]), 2)
        self.assertEqual(
            captured["url"],
            "http://127.0.0.1:8080/v1/chat/completions",
        )
        self.assertEqual(captured["body"]["model"], "local-model")
        request_text = captured["body"]["messages"][1]["content"]
        self.assertIn("translate_pending_prompt_blocks", request_text)

    def test_normalizes_common_generation_tags_when_model_keeps_them_in_english(self):
        result = translate_pending_items(
            [{"id": "subject", "en": "1girl, multiple girls"}],
            {
                "localTextUrl": "http://127.0.0.1:8080/v1",
                "localTextModel": "local-model",
            },
            transport=lambda *args: {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "translations": [
                                        {
                                            "id": "subject",
                                            "zh": "1girl, multiple girls",
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )

        self.assertEqual(
            result["translations"][0]["zh"],
            "一名女性角色, 多名女性角色",
        )

    def test_retries_pending_translation_when_first_result_copies_english(self):
        calls = []

        def fake_transport(url, body, headers, timeout):
            calls.append(body)
            payload = (
                {
                    "translations": [
                        {
                            "id": "appearance",
                            "zh": "short hair, blonde hair, green eyes",
                        }
                    ]
                }
                if len(calls) == 1
                else {
                    "translations": [
                        {
                            "id": "appearance",
                            "zh": "短发，金色头发，绿色眼睛",
                        }
                    ]
                }
            )
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                payload,
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

        result = translate_pending_items(
            [
                {
                    "id": "appearance",
                    "en": "short hair, blonde hair, green eyes",
                }
            ],
            {
                "localTextUrl": "http://127.0.0.1:8080/v1",
                "localTextModel": "local-model",
            },
            transport=fake_transport,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(
            result["translations"][0]["zh"],
            "短发，金色头发，绿色眼睛",
        )
        self.assertIn(
            "repair_pending_prompt_translation",
            calls[1]["messages"][1]["content"],
        )

    def test_rejects_missing_duplicate_empty_or_non_chinese_results(self):
        invalid_results = [
            {"translations": [{"id": "subject", "zh": "初音未来"}]},
            {
                "translations": [
                    {"id": "subject", "zh": "初音未来"},
                    {"id": "subject", "zh": "初音未来"},
                ]
            },
            {
                "translations": [
                    {"id": "subject", "zh": ""},
                    {"id": "appearance", "zh": "青绿色双马尾"},
                ]
            },
            {
                "translations": [
                    {"id": "subject", "zh": "Hatsune Miku"},
                    {"id": "appearance", "zh": "aqua twintails"},
                ]
            },
        ]

        for payload in invalid_results:
            with self.subTest(payload=payload):
                with self.assertRaises(PromptEngineError):
                    translate_pending_items(
                        [
                            {"id": "subject", "en": "Hatsune Miku"},
                            {
                                "id": "appearance",
                                "en": "aqua twintails",
                            },
                        ],
                        {
                            "localTextUrl": "http://127.0.0.1:8080/v1",
                            "localTextModel": "local-model",
                        },
                        transport=lambda *args, payload=payload: {
                            "choices": [
                                {
                                    "message": {
                                        "content": json.dumps(
                                            payload,
                                            ensure_ascii=False,
                                        )
                                    }
                                }
                            ]
                        },
                    )


class ResultNormalizationTests(unittest.TestCase):
    def test_creative_appearance_allows_visible_anatomical_details(self):
        payload = rich_random_model_result()
        appearance = next(
            block for block in payload["blocks"] if block["id"] == "appearance"
        )
        appearance["en"] += (
            ", polished mechanical forearms, articulated hands, silver leg armor"
        )
        appearance["zh"] += "，抛光机械前臂，关节手部，银色腿甲"

        result = normalize_text_expansion(payload)

        validate_expansion_quality(
            result,
            "creative",
            user_input="original mechanical adult character in an observatory",
        )

    def test_normalizes_labels_sources_weights_and_lock_state(self):
        result = normalize_text_expansion(valid_model_result())

        self.assertEqual(
            [block["id"] for block in result["blocks"]], REQUIRED_BLOCKS
        )
        self.assertEqual(result["blocks"][2]["label"], "主体与角色")
        self.assertEqual(result["blocks"][2]["source"], "expanded")
        self.assertEqual(result["blocks"][2]["weight"], 100)
        self.assertFalse(result["blocks"][2]["locked"])
        self.assertTrue(result["checks"]["bilingualAligned"])

    def test_rejects_missing_structure_blocks(self):
        payload = valid_model_result()
        payload["blocks"] = payload["blocks"][:-1]

        with self.assertRaises(PromptEngineError) as context:
            normalize_text_expansion(payload)

        self.assertEqual(context.exception.code, "invalid_model_output")

    def test_enforces_anima_profile_and_recompiles_model_output(self):
        payload = valid_model_result()
        payload["positiveEn"] = "model supplied total that must be ignored"
        payload["positiveZh"] = "模型自行提供、但必须被忽略的总提示词"
        payload["negativeEn"] = "wrong negative total"
        payload["negativeZh"] = "错误的负面总提示词"
        quality = next(
            block for block in payload["blocks"] if block["id"] == "quality"
        )
        negative = next(
            block for block in payload["blocks"] if block["id"] == "negative"
        )
        quality["en"] = "score_9_up, ultra quality"
        quality["zh"] = "错误质量词"
        negative["en"] = "bad hands, worst quality"
        negative["zh"] = "手部错误，最差质量"

        result = normalize_text_expansion(payload)

        self.assertEqual(
            result["blocks"][0]["en"],
            "masterpiece, best quality, score_7",
        )
        self.assertEqual(
            result["blocks"][-1]["en"],
            (
                "worst quality, low quality, score_1, score_2, score_3, "
                "artist name, blurry, jpeg artifacts, lowres, censor, bad hands"
            ),
        )
        self.assertTrue(
            result["positiveEn"].startswith(
                "masterpiece, best quality, score_7"
            )
        )
        self.assertNotIn("model supplied total", result["positiveEn"])
        self.assertEqual(result["negativeEn"], result["blocks"][-1]["en"])
        self.assertEqual(result["negativeZh"], result["blocks"][-1]["zh"])

    def test_rejects_a_structure_block_with_only_one_language(self):
        payload = valid_model_result()
        subject = next(
            block for block in payload["blocks"] if block["id"] == "subject"
        )
        subject["zh"] = ""

        with self.assertRaises(PromptEngineError) as context:
            normalize_text_expansion(payload)

        self.assertEqual(context.exception.code, "invalid_model_output")

    def test_rejects_a_balanced_result_that_is_structurally_valid_but_thin(self):
        result = normalize_text_expansion(valid_model_result())

        with self.assertRaises(PromptEngineError) as context:
            validate_expansion_quality(result, "balanced")

        self.assertEqual(context.exception.code, "insufficient_detail")

    def test_accepts_a_balanced_result_with_enough_visual_information(self):
        payload = valid_model_result()
        details = {
            "subject": "1girl",
            "appearance": (
                "long black hair, gray eyes, white blouse, dark pleated skirt, "
                "wet hair strands, damp fabric, calm expression"
            ),
            "pose": (
                "standing, body turned slightly left, head tilted down, "
                "looking toward the puddles, relaxed hands, balanced stance"
            ),
            "scene": (
                "outdoor walkway, overcast afternoon, steady rain, wet pavement, "
                "shallow puddles, blurred trees, distant railing, layered background"
            ),
            "composition": (
                "medium full shot, eye level, centered subject, visible ground, "
                "shallow depth of field"
            ),
            "lighting": (
                "soft overcast daylight, diffused top light, cool ambient light, "
                "subtle facial highlights, soft ground reflections"
            ),
            "effects": "fine rain streaks, water droplets, small splashes, light mist",
        }
        for block in payload["blocks"]:
            if block["id"] in details:
                block["en"] = details[block["id"]]
                block["zh"] = details[block["id"]] + " 中文"

        result = normalize_text_expansion(payload)

        validate_expansion_quality(result, "balanced")

    def test_rejects_visual_details_placed_in_the_wrong_blocks(self):
        payload = valid_model_result()
        values = {
            "subject": "1girl",
            "appearance": (
                "long black hair, gray eyes, white blouse, dark skirt, "
                "wet hair, damp fabric, calm expression"
            ),
            "pose": (
                "standing, head tilted, looking down, relaxed hands, "
                "balanced stance"
            ),
            "scene": (
                "outdoor walkway, overcast afternoon, steady rain, wet pavement, "
                "shallow puddles, distant trees"
            ),
            "composition": (
                "soft lighting, cool palette, gray tones, diffused light"
            ),
            "lighting": (
                "rainy atmosphere, open space, wet surroundings, falling rain"
            ),
            "effects": "rain streaks, droplets, small splashes",
        }
        for block in payload["blocks"]:
            if block["id"] in values:
                block["en"] = values[block["id"]]
                block["zh"] = values[block["id"]] + " 中文"

        result = normalize_text_expansion(payload)

        with self.assertRaises(PromptEngineError) as context:
            validate_expansion_quality(result, "balanced")

        self.assertEqual(context.exception.code, "insufficient_detail")
        self.assertIn("构图块", context.exception.message)
        self.assertIn("光影块", context.exception.message)

    def test_rejects_unanchored_clothing_and_location_for_sparse_balanced_input(self):
        payload = valid_model_result()
        values = {
            "subject": "1girl",
            "appearance": (
                "long brown hair, blue eyes, light blue dress, wet hair, "
                "damp fabric"
            ),
            "pose": "standing, looking upward, relaxed arms, balanced stance",
            "scene": (
                "urban street, street lamps, steady rain, wet pavement, "
                "shallow puddles"
            ),
            "composition": (
                "medium shot, eye level, centered subject, shallow depth of field"
            ),
            "lighting": (
                "soft overcast light, cool ambient color, subtle highlights, "
                "wet ground reflections"
            ),
            "effects": "fine rain streaks, droplets, small splashes",
        }
        for block in payload["blocks"]:
            if block["id"] in values:
                block["en"] = values[block["id"]]
                block["zh"] = values[block["id"]] + " 中文"

        result = normalize_text_expansion(payload)

        with self.assertRaises(PromptEngineError) as context:
            validate_expansion_quality(
                result,
                "balanced",
                user_input="1girl, raining",
            )

        self.assertEqual(context.exception.code, "insufficient_detail")
        self.assertIn("无依据的稳定外貌或服装", context.exception.message)
        self.assertIn("无依据的具体地点或道具", context.exception.message)

    def test_rejects_pose_terms_in_appearance_and_missing_relation(self):
        payload = valid_model_result()
        values = {
            "subject": "1girl",
            "appearance": "wet hair, damp clothing, slightly hunched posture",
            "pose": "standing, looking upward, relaxed arms, balanced stance",
            "scene": (
                "open outdoor setting, overcast sky, steady rain, wet ground, "
                "shallow puddles"
            ),
            "composition": (
                "medium shot, eye level, centered subject, moderate depth of field"
            ),
            "lighting": (
                "soft overcast light, cool ambient color, subtle highlights, "
                "wet ground reflections"
            ),
            "effects": "fine rain streaks, droplets, small splashes",
        }
        for block in payload["blocks"]:
            if block["id"] in values:
                block["en"] = values[block["id"]]
                block["zh"] = values[block["id"]] + " 中文"
        payload["relationEn"] = ""
        payload["relationZh"] = ""

        result = normalize_text_expansion(payload)

        with self.assertRaises(PromptEngineError) as context:
            validate_expansion_quality(result, "balanced")

        self.assertIn("外观块混入动作", context.exception.message)
        self.assertIn("缺少关系描述", context.exception.message)

    def test_detailed_input_uses_an_adaptive_density_threshold(self):
        payload = valid_model_result()
        values = {
            "subject": "adult woman",
            "appearance": "short black hair, deep green tweed coat",
            "pose": (
                "sitting by the window, head turned toward the window, "
                "calm expression"
            ),
            "scene": "old train interior, snow-covered landscape, dusk",
            "composition": (
                "medium shot, eye level, woman in focus, window framing"
            ),
            "lighting": (
                "golden dusk light, soft shadows, warm reflected glow"
            ),
            "effects": "falling snow, window reflections",
        }
        for block in payload["blocks"]:
            if block["id"] in values:
                block["en"] = values[block["id"]]
                block["zh"] = values[block["id"]] + " 中文"

        result = normalize_text_expansion(payload)

        validate_expansion_quality(
            result,
            "balanced",
            user_input=(
                "成年女性，黑色短发，穿深绿色粗花呢外套，坐在老式火车靠窗座位，"
                "侧头看向窗外的雪景，神情平静，黄昏"
            ),
        )

    def test_rejects_age_drift_from_adult_to_middle_aged(self):
        payload = valid_model_result()
        values = {
            "subject": "middle-aged woman",
            "appearance": "short black hair, deep green tweed coat",
            "pose": (
                "sitting by the window, head turned toward the window, "
                "calm expression"
            ),
            "scene": "old train interior, snow-covered landscape, dusk",
            "composition": (
                "medium shot, eye level, woman in focus, window framing"
            ),
            "lighting": (
                "golden dusk light, soft shadows, warm reflected glow"
            ),
            "effects": "falling snow, window reflections",
        }
        for block in payload["blocks"]:
            if block["id"] in values:
                block["en"] = values[block["id"]]
                block["zh"] = values[block["id"]] + " 中文"

        result = normalize_text_expansion(payload)

        with self.assertRaises(PromptEngineError) as context:
            validate_expansion_quality(
                result,
                "balanced",
                user_input="成年女性坐在火车里看雪景",
            )

        self.assertIn("年龄层发生漂移", context.exception.message)


class ExpansionCallTests(unittest.TestCase):
    def test_regenerates_multiple_blocks_as_one_coordinated_variant(self):
        calls = []

        def fake_transport(url, body, headers, timeout):
            calls.append(body)
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "items": [
                                        {
                                            "id": "scene",
                                            "en": "covered station platform, wet rails",
                                            "zh": "有顶棚的车站站台，湿润铁轨",
                                        },
                                        {
                                            "id": "lighting",
                                            "en": "warm train windows, cool rainy ambient light",
                                            "zh": "暖色车窗灯光，冷色雨天环境光",
                                        },
                                    ],
                                    "assumptions": ["联合改为雨中车站方案"],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

        result = regenerate_prompt_blocks(
            target_block_ids=["scene", "lighting"],
            blocks=[
                {"id": "subject", "en": "1girl", "zh": "一名女孩"},
                {"id": "pose", "en": "standing", "zh": "站立"},
                {
                    "id": "scene",
                    "en": "open outdoor setting",
                    "zh": "开阔户外",
                },
                {
                    "id": "lighting",
                    "en": "soft overcast light",
                    "zh": "阴天柔光",
                },
            ],
            settings={
                "apiTextUrl": "https://api.deepseek.com",
                "apiTextModel": "deepseek-v4-flash",
                "apiTextKey": "secret",
            },
            provider="api",
            expansion_level="creative",
            transport=fake_transport,
        )

        self.assertEqual([item["id"] for item in result["items"]], ["scene", "lighting"])
        self.assertEqual(len(calls), 2)
        self.assertIn("selectedBlocks", calls[0]["messages"][1]["content"])
        self.assertIn("immutableBlocks", calls[1]["messages"][1]["content"])
        self.assertEqual(result["items"][0]["source"], "外部 API · 联合随机")

    def test_multi_block_variant_rejects_locked_or_unsupported_blocks(self):
        with self.assertRaises(PromptEngineError) as locked:
            regenerate_prompt_blocks(
                target_block_ids=["scene"],
                blocks=[{"id": "scene", "locked": True}],
                settings={},
            )
        self.assertEqual(locked.exception.code, "block_locked")

        with self.assertRaises(PromptEngineError) as unsupported:
            regenerate_prompt_blocks(
                target_block_ids=["quality"],
                blocks=[{"id": "quality", "locked": False}],
                settings={},
            )
        self.assertEqual(unsupported.exception.code, "unsupported_block")

    def test_multi_block_variant_retries_once_when_review_keeps_old_content(self):
        calls = []
        changed = {
            "items": [
                {
                    "id": "scene",
                    "en": "covered station platform",
                    "zh": "有顶棚的车站站台",
                },
                {
                    "id": "lighting",
                    "en": "warm windows, cool ambient rain light",
                    "zh": "暖色车窗，冷色雨天环境光",
                },
            ]
        }
        unchanged = {
            "items": [
                {
                    "id": "scene",
                    "en": "rainy city street",
                    "zh": "雨夜城市街道",
                },
                {
                    "id": "lighting",
                    "en": "soft neon light",
                    "zh": "柔和霓虹光",
                },
            ]
        }

        def fake_transport(url, body, headers, timeout):
            calls.append(body)
            payloads = [changed, unchanged, changed, changed]
            payload = payloads[len(calls) - 1]
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(payload, ensure_ascii=False)
                        }
                    }
                ]
            }

        result = regenerate_prompt_blocks(
            target_block_ids=["scene", "lighting"],
            blocks=[
                {
                    "id": "scene",
                    "en": "rainy city street",
                    "zh": "雨夜城市街道",
                },
                {
                    "id": "lighting",
                    "en": "soft neon light",
                    "zh": "柔和霓虹光",
                },
            ],
            settings={
                "apiTextUrl": "https://api.deepseek.com",
                "apiTextModel": "deepseek-v4-flash",
                "apiTextKey": "secret",
            },
            provider="api",
            transport=fake_transport,
        )

        self.assertEqual(len(calls), 4)
        self.assertEqual(result["items"][0]["en"], "covered station platform")

    def test_multi_block_variant_rejects_duplicate_returned_ids(self):
        def fake_transport(url, body, headers, timeout):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "items": [
                                        {
                                            "id": "scene",
                                            "en": "station platform",
                                            "zh": "车站站台",
                                        },
                                        {
                                            "id": "scene",
                                            "en": "forest clearing",
                                            "zh": "森林空地",
                                        },
                                        {
                                            "id": "lighting",
                                            "en": "warm sunset light",
                                            "zh": "暖色夕阳光",
                                        },
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

        with self.assertRaises(PromptEngineError) as context:
            regenerate_prompt_blocks(
                target_block_ids=["scene", "lighting"],
                blocks=[
                    {"id": "scene", "en": "rainy city", "zh": "雨夜城市"},
                    {"id": "lighting", "en": "neon light", "zh": "霓虹光"},
                ],
                settings={
                    "apiTextUrl": "https://api.deepseek.com",
                    "apiTextModel": "deepseek-v4-flash",
                    "apiTextKey": "secret",
                },
                provider="api",
                transport=fake_transport,
            )

        self.assertEqual(context.exception.code, "invalid_model_output")

    def test_regenerates_one_block_with_other_blocks_as_context(self):
        calls = []

        def fake_transport(url, body, headers, timeout):
            calls.append(body)
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "en": (
                                        "covered station platform, rain beyond "
                                        "the canopy, wet rails, distant train"
                                    ),
                                    "zh": (
                                        "有顶棚的车站站台，雨幕落在顶棚外，"
                                        "湿润铁轨，远处列车"
                                    ),
                                    "assumptions": ["随机变体：改为有顶棚的雨中站台"],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

        result = regenerate_prompt_block(
            target_block_id="scene",
            blocks=[
                {"id": "subject", "en": "1girl", "zh": "一名女孩"},
                {"id": "pose", "en": "standing", "zh": "站立"},
                {
                    "id": "scene",
                    "en": "open outdoor setting, wet ground",
                    "zh": "开阔户外，湿地面",
                },
                {
                    "id": "lighting",
                    "en": "soft overcast light",
                    "zh": "阴天柔光",
                },
            ],
            settings={
                "apiTextUrl": "https://api.deepseek.com",
                "apiTextModel": "deepseek-v4-flash",
                "apiTextKey": "secret",
            },
            provider="api",
            expansion_level="balanced",
            transport=fake_transport,
        )

        self.assertEqual(result["id"], "scene")
        self.assertIn("station platform", result["en"])
        self.assertEqual(len(calls), 2)
        self.assertIn("currentBlock", calls[0]["messages"][1]["content"])
        self.assertIn("soft overcast light", calls[0]["messages"][1]["content"])
        self.assertIn("immutableBlocks", calls[1]["messages"][1]["content"])
        self.assertEqual(calls[1]["thinking"], {"type": "disabled"})
        self.assertEqual(
            calls[1]["response_format"],
            {"type": "json_object"},
        )

    def test_rejects_random_variants_for_model_managed_blocks(self):
        with self.assertRaises(PromptEngineError) as context:
            regenerate_prompt_block(
                target_block_id="quality",
                blocks=[],
                settings={},
            )

        self.assertEqual(context.exception.code, "unsupported_block")

    def test_rejects_a_random_variant_identical_to_the_current_block(self):
        def fake_transport(url, body, headers, timeout):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "en": "wet ground",
                                    "zh": "湿地面",
                                    "assumptions": [],
                                }
                            )
                        }
                    }
                ]
            }

        with self.assertRaises(PromptEngineError) as context:
            regenerate_prompt_block(
                target_block_id="scene",
                blocks=[
                    {"id": "scene", "en": "wet ground", "zh": "湿地面"}
                ],
                settings={
                    "localTextUrl": "http://127.0.0.1:8080/v1",
                    "localTextModel": "local-model",
                },
                transport=fake_transport,
            )

        self.assertEqual(context.exception.code, "unchanged_variant")

    def test_deepseek_expansion_disables_thinking_and_requests_json(self):
        captured = {}

        def fake_transport(url, body, headers, timeout):
            captured.update(body)
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                valid_model_result(), ensure_ascii=False
                            )
                        }
                    }
                ]
            }

        expand_text_prompt(
            "1girl",
            {
                "apiTextUrl": "https://api.deepseek.com",
                "apiTextModel": "deepseek-v4-flash",
                "apiTextKey": "secret",
            },
            provider="api",
            expansion_level="strict",
            transport=fake_transport,
        )

        self.assertEqual(captured["thinking"], {"type": "disabled"})
        self.assertEqual(
            captured["response_format"],
            {"type": "json_object"},
        )
    def test_sends_system_rules_and_user_input_as_separate_messages(self):
        captured = {}

        def fake_transport(url, body, headers, timeout):
            captured.update(
                {"url": url, "body": body, "headers": headers, "timeout": timeout}
            )
            content = "```json\n" + json.dumps(
                valid_model_result(), ensure_ascii=False
            ) + "\n```"
            return {
                "choices": [
                    {
                        "message": {
                            "content": content,
                        }
                    }
                ]
            }

        result = expand_text_prompt(
            "1girl, raining，霓虹街道",
            {
                "localTextUrl": "http://127.0.0.1:8080/v1",
                "localTextModel": "anima-helper",
            },
            provider="local",
            target_model="anima",
            context="用户锁定成年角色",
            expansion_level="strict",
            transport=fake_transport,
        )

        self.assertEqual(result["blocks"][2]["id"], "subject")
        self.assertEqual(captured["body"]["model"], "anima-helper")
        self.assertEqual(captured["body"]["messages"][0]["role"], "system")
        self.assertEqual(captured["body"]["messages"][1]["role"], "user")
        self.assertTrue(
            captured["body"]["messages"][1]["content"].startswith(
                "/no_think\n"
            )
        )
        self.assertIn(
            "1girl, raining，霓虹街道",
            captured["body"]["messages"][1]["content"],
        )
        self.assertNotIn(
            "1girl, raining，霓虹街道",
            captured["body"]["messages"][0]["content"],
        )
        self.assertEqual(captured["body"]["max_tokens"], 1800)
        self.assertEqual(captured["body"]["temperature"], 0.3)

    def test_retries_once_when_model_returns_invalid_json(self):
        calls = []

        def fake_transport(url, body, headers, timeout):
            calls.append(body)
            if len(calls) == 1:
                return {"choices": [{"message": {"content": "not-json"}}]}
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                valid_model_result(), ensure_ascii=False
                            )
                        }
                    }
                ]
            }

        result = expand_text_prompt(
            "1girl, raining",
            {
                "localTextUrl": "http://127.0.0.1:8080/v1",
                "localTextModel": "local-model",
            },
            expansion_level="strict",
            transport=fake_transport,
        )

        self.assertEqual(len(calls), 2)
        self.assertIn("修复为严格 JSON", calls[1]["messages"][-1]["content"])
        self.assertNotIn(
            "assistant",
            [message["role"] for message in calls[1]["messages"]],
        )
        self.assertEqual(result["blocks"][0]["id"], "quality")

    def test_retries_once_when_balanced_output_is_too_thin(self):
        calls = []
        rich = valid_model_result()
        rich_values = {
            "subject": "1girl",
            "appearance": (
                "wet hair strands, damp fabric, raindrops on the face, "
                "clothes darkened by moisture, clinging wet fabric, calm expression"
            ),
            "pose": (
                "standing, body turned slightly left, head tilted down, "
                "looking toward the puddles, relaxed hands, balanced stance"
            ),
            "scene": (
                "outdoor walkway, overcast afternoon, steady rain, wet pavement, "
                "shallow puddles, blurred trees, distant railing, layered background"
            ),
            "composition": (
                "medium full shot, eye level, centered subject, visible ground, "
                "shallow depth of field"
            ),
            "lighting": (
                "soft overcast daylight, diffused top light, cool ambient light, "
                "subtle facial highlights, soft ground reflections"
            ),
            "effects": "fine rain streaks, water droplets, small splashes, light mist",
        }
        for block in rich["blocks"]:
            if block["id"] in rich_values:
                block["en"] = rich_values[block["id"]]
                block["zh"] = rich_values[block["id"]] + " 中文"

        def fake_transport(url, body, headers, timeout):
            calls.append(body)
            payload = valid_model_result() if len(calls) == 1 else rich
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(payload, ensure_ascii=False)
                        }
                    }
                ]
            }

        result = expand_text_prompt(
            "1girl, raining",
            {
                "localTextUrl": "http://127.0.0.1:8080/v1",
                "localTextModel": "local-model",
            },
            expansion_level="balanced",
            transport=fake_transport,
        )

        self.assertEqual(len(calls), 2)
        self.assertIn("内容密度不足", calls[1]["messages"][-1]["content"])
        self.assertNotIn(
            "assistant",
            [message["role"] for message in calls[1]["messages"]],
        )
        self.assertIn("wet pavement", result["positiveEn"])
        self.assertEqual(calls[0]["max_tokens"], 2400)
        self.assertEqual(calls[0]["temperature"], 0.5)

    def test_creative_mode_builds_a_blueprint_before_compiling(self):
        calls = []
        rich = valid_model_result()
        values = {
            "subject": "1girl, original character",
            "appearance": (
                "long silver hair, amber eyes, translucent raincoat, layered dress, "
                "wet hair strands, reflective fabric, delicate earrings"
            ),
            "pose": (
                "walking through rain, looking over her shoulder, left hand raised, "
                "right hand holding the coat edge, wind-swept posture, focused gaze"
            ),
            "scene": (
                "narrow riverside market, dusk, heavy rain, wet stone pavement, "
                "closed stalls, hanging paper signs, distant bridge, layered background"
            ),
            "composition": (
                "medium full shot, low angle, off-center subject, leading lines, "
                "foreground rain curtain"
            ),
            "lighting": (
                "warm stall lights, cool dusk ambience, rim light, wet reflections, "
                "soft facial highlights, deep blue shadows"
            ),
            "effects": (
                "dense rain streaks, water splashes, drifting mist, fabric motion, "
                "glowing reflections"
            ),
        }
        for block in rich["blocks"]:
            if block["id"] in values:
                block["en"] = values[block["id"]]
                block["zh"] = values[block["id"]] + " 中文"

        def fake_transport(url, body, headers, timeout):
            calls.append(body)
            if len(calls) == 1:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "concept": "rainy riverside market",
                                        "visualFacts": [
                                            "translucent raincoat",
                                            "wet stone pavement",
                                            "warm stall lights",
                                        ],
                                    }
                                )
                            }
                        }
                    ]
                }
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(rich, ensure_ascii=False)
                        }
                    }
                ]
            }

        result = expand_text_prompt(
            "1girl, raining",
            {
                "localTextUrl": "http://127.0.0.1:8080/v1",
                "localTextModel": "local-model",
            },
            expansion_level="creative",
            transport=fake_transport,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["max_tokens"], 1200)
        self.assertEqual(
            calls[0]["response_format"],
            {"type": "json_object"},
        )
        self.assertIn(
            "creative_visual_blueprint",
            calls[0]["messages"][1]["content"],
        )
        self.assertIn(
            "rainy riverside market",
            calls[1]["messages"][1]["content"],
        )
        self.assertIn("translucent raincoat", result["positiveEn"])


if __name__ == "__main__":
    unittest.main()
