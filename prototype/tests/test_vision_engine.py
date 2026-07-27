import tempfile
import threading
import unittest
import json
from pathlib import Path
from unittest.mock import patch


import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prompt_engine import PromptEngineError  # noqa: E402
from vision_engine import (  # noqa: E402
    VisionEngineError,
    analyze_image_bytes,
    extract_composition_reference,
    normalize_analyzer_results,
    run_analyzer,
    vision_model_status,
)


class VisionEngineTests(unittest.TestCase):
    def test_composition_reference_extracts_only_the_five_allowed_fields(self):
        result = extract_composition_reference(
            {
                "wd14": "1girl, medium shot, low angle, centered subject, shallow depth of field, rim light",
                "florence": "a red-haired woman in a medium shot, rim light",
            }
        )

        self.assertEqual(result["mode"], "advisory_reference")
        self.assertEqual(
            set(result["fields"]),
            {"shotScale", "viewAngle", "subjectPosition", "depthOfField", "lighting"},
        )
        self.assertEqual(result["fields"]["shotScale"][0]["value"], "medium shot")
        self.assertEqual(result["fields"]["shotScale"][0]["sources"], ["wd14", "florence"])
        self.assertNotIn("1girl", json.dumps(result))
        self.assertNotIn("red-haired", json.dumps(result))

    def test_composition_reference_does_not_guess_from_generic_caption_text(self):
        result = extract_composition_reference(
            {"florence": "a woman standing beside a neon shop"}
        )

        self.assertTrue(all(not values for values in result["fields"].values()))

    def test_normalizes_confirmed_uncertain_and_catalog_conflicts(self):
        catalog = {
            "schemaVersion": 1,
            "version": "fixture-semantic-v1",
            "sourceReview": {
                "licenseReviewed": True,
                "redistributionApproved": True,
            },
            "policy": {"lowFrequencyPostCount": 10},
            "tags": [
                {
                    "canonical": "day",
                    "aliases": ["daytime"],
                    "implications": [],
                    "postCount": 100,
                    "category": "time",
                },
                {
                    "canonical": "night",
                    "aliases": [],
                    "implications": [],
                    "postCount": 100,
                    "category": "time",
                },
            ],
            "conflictRules": [
                {"left": "day", "right": "night", "decision": "manual_decision"}
            ],
        }
        with patch("vision_engine.load_catalog", return_value=catalog):
            result = normalize_analyzer_results(
                {
                    "wd14": "daytime, blue hair",
                    "florence": "day, night",
                    "qwenvl": "night",
                }
            )

        items = {item["tag"]: item for item in result["items"]}
        self.assertEqual(result["catalogVersion"], "fixture-semantic-v1")
        self.assertEqual(items["day"]["status"], "conflict")
        self.assertEqual(items["day"]["sources"], ["wd14", "florence"])
        self.assertEqual(items["night"]["status"], "conflict")
        self.assertEqual(items["blue hair"]["status"], "uncertain")

    def test_normalization_fails_closed_without_semantic_catalog(self):
        with patch("vision_engine.load_catalog", return_value=None):
            result = normalize_analyzer_results(
                {"wd14": "blue hair", "florence": "blue hair"}
            )

        self.assertEqual(result["catalogStatus"], "unavailable")
        self.assertEqual(result["items"][0]["status"], "confirmed")

    def test_cancellation_terminates_the_active_worker_process(self):
        class RunningProcess:
            returncode = None

            def __init__(self):
                self.terminated = False

            def poll(self):
                return None

            def terminate(self):
                self.terminated = True
                self.returncode = -15

            def communicate(self, timeout=None):
                return "", ""

        process = RunningProcess()
        cancel_event = threading.Event()
        cancel_event.set()
        with patch(
            "vision_engine.vision_model_status",
            return_value={"wd14": {"installed": True}},
        ), patch("vision_engine.subprocess.Popen", return_value=process):
            with self.assertRaises(VisionEngineError) as raised:
                run_analyzer("wd14", "input.png", 1, cancel_event=cancel_event)

        self.assertEqual(raised.exception.code, "vision_cancelled")
        self.assertTrue(process.terminated)

    def test_real_image_bytes_flow_from_florence_into_local_decomposer(self):
        captured = {}

        def fake_runner(image_path, timeout):
            captured["image_bytes"] = Path(image_path).read_bytes()
            captured["suffix"] = Path(image_path).suffix
            return "multiple girls, pink hair, green hair, brown dresses"

        def fake_decomposer(raw_prompt, settings, provider, timeout):
            captured["raw_prompt"] = raw_prompt
            captured["provider"] = provider
            return {
                "positiveEn": raw_prompt,
                "positiveZh": "多名女性角色，粉色头发，绿色头发，棕色连衣裙",
                "negativeEn": "",
                "negativeZh": "",
                "relationEn": "",
                "relationZh": "",
                "blocks": [
                    {
                        "id": "subject",
                        "en": "multiple girls",
                        "zh": "多名女性角色",
                    }
                ],
                "checks": {},
            }

        result = analyze_image_bytes(
            b"real-image-content",
            "reference.webp",
            ["florence"],
            {"localTextModel": "local-model"},
            runner=fake_runner,
            decomposer=fake_decomposer,
        )

        self.assertEqual(captured["image_bytes"], b"real-image-content")
        self.assertEqual(captured["suffix"], ".webp")
        self.assertEqual(captured["provider"], "local")
        self.assertEqual(
            captured["raw_prompt"],
            "multiple girls, pink hair, green hair, brown dresses",
        )
        self.assertEqual(result["rawResults"]["florence"], captured["raw_prompt"])
        self.assertIn("multiple girls", result["positiveEn"])
        self.assertEqual(result["blocks"][0]["source"], "Florence + 本地 LLM")

    def test_repairs_non_chinese_block_translations_before_compiling_output(self):
        def fake_decomposer(raw_prompt, settings, provider, timeout):
            return {
                "positiveEn": raw_prompt,
                "positiveZh": raw_prompt,
                "negativeEn": "",
                "negativeZh": "",
                "relationEn": "",
                "relationZh": "",
                "blocks": [
                    {
                        "id": "subject",
                        "en": "1girl",
                        "zh": "1girl",
                    },
                    {
                        "id": "scene",
                        "en": "library, book",
                        "zh": "library, book",
                    },
                ],
                "checks": {},
            }

        def fake_translator(items, settings):
            self.assertEqual(
                items,
                [
                    {"id": "subject", "en": "1girl"},
                    {"id": "scene", "en": "library, book"},
                ],
            )
            return {
                "translations": [
                    {"id": "subject", "zh": "一名女性角色"},
                    {"id": "scene", "zh": "图书馆，书本"},
                ]
            }

        result = analyze_image_bytes(
            b"image",
            "reference.png",
            ["florence"],
            {},
            runner=lambda *args: "1girl, library, book",
            decomposer=fake_decomposer,
            translator=fake_translator,
        )

        self.assertEqual(result["blocks"][0]["zh"], "一名女性角色")
        self.assertEqual(result["blocks"][1]["zh"], "图书馆，书本")
        self.assertEqual(result["positiveZh"], "一名女性角色；图书馆，书本")

    def test_rejects_analysis_without_an_installed_supported_model(self):
        with self.assertRaises(VisionEngineError) as context:
            analyze_image_bytes(
                b"image",
                "reference.png",
                ["unknown-model"],
                {},
                runner=lambda *args: "unused",
                decomposer=lambda *args, **kwargs: {},
            )

        self.assertEqual(context.exception.code, "vision_model_unavailable")

    def test_runs_selected_analyzers_in_declared_order(self):
        calls = []

        def fake_runner(analyzer_id, image_path, timeout):
            calls.append(analyzer_id)
            return f"{analyzer_id} output"

        result = analyze_image_bytes(
            b"image",
            "reference.png",
            ["qwenvl", "wd14", "florence"],
            {},
            runner=fake_runner,
            decomposer=lambda prompt, *args, **kwargs: {
                "positiveEn": prompt,
                "positiveZh": "测试",
                "negativeEn": "",
                "negativeZh": "",
                "relationEn": "",
                "relationZh": "",
                "blocks": [],
                "checks": {},
            },
        )

        self.assertEqual(calls, ["wd14", "florence", "qwenvl"])
        self.assertEqual(result["rawResults"]["wd14"], "wd14 output")
        self.assertEqual(result["rawResults"]["qwenvl"], "qwenvl output")

    def test_keeps_successful_results_when_one_analyzer_fails(self):
        def fake_runner(analyzer_id, image_path, timeout):
            if analyzer_id == "joycaption":
                raise VisionEngineError(
                    "CUDA out of memory",
                    code="vision_worker_failed",
                    status=502,
                )
            return f"{analyzer_id} output"

        result = analyze_image_bytes(
            b"image",
            "reference.png",
            ["wd14", "joycaption"],
            {},
            runner=fake_runner,
            decomposer=lambda prompt, *args, **kwargs: {
                "positiveEn": prompt,
                "positiveZh": "测试",
                "negativeEn": "",
                "negativeZh": "",
                "relationEn": "",
                "relationZh": "",
                "blocks": [],
                "checks": {},
            },
        )

        statuses = {item["id"]: item["status"] for item in result["analyzers"]}
        self.assertEqual(statuses["wd14"], "ready")
        self.assertEqual(statuses["joycaption"], "error")
        self.assertIn("CUDA", result["analyzers"][1]["error"])

    def test_model_status_reports_all_local_analyzers(self):
        status = vision_model_status()
        self.assertEqual(
            set(status),
            {"florence", "wd14", "joycaption", "qwenvl", "external"},
        )
        for analyzer_id in ("florence", "wd14", "joycaption", "qwenvl"):
            self.assertIn("installed", status[analyzer_id])
            self.assertIn("label", status[analyzer_id])
            self.assertIn("missing", status[analyzer_id])

    def test_translation_repair_failure_keeps_vision_results(self):
        def fake_decomposer(raw_prompt, settings, provider, timeout):
            return {
                "positiveEn": raw_prompt,
                "positiveZh": raw_prompt,
                "negativeEn": "",
                "negativeZh": "",
                "relationEn": "",
                "relationZh": "",
                "blocks": [
                    {"id": "subject", "en": "1girl", "zh": "1girl"},
                ],
                "checks": {},
            }

        def failing_translator(items, settings):
            raise PromptEngineError(
                "模型返回了缺失、重复或无效的中文翻译",
                code="invalid_model_output",
                status=502,
            )

        result = analyze_image_bytes(
            b"image",
            "reference.png",
            ["wd14"],
            {},
            runner=lambda *args: "1girl, book",
            decomposer=fake_decomposer,
            translator=failing_translator,
        )

        self.assertEqual(result["rawResults"]["wd14"], "1girl, book")
        self.assertEqual(result["blocks"][0]["zh"], "1girl")
        self.assertIn("translationWarning", result["checks"])

    def test_translation_repair_retries_each_block_after_batch_failure(self):
        calls = []

        def fake_translator(items, settings):
            calls.append([item["id"] for item in items])
            if len(items) > 1:
                raise PromptEngineError(
                    "batch invalid",
                    code="invalid_model_output",
                    status=502,
                )
            item = items[0]
            return {
                "translations": [
                    {"id": item["id"], "zh": f"中文-{item['id']}"},
                ]
            }

        result = analyze_image_bytes(
            b"image",
            "reference.png",
            ["wd14"],
            {},
            runner=lambda *args: "1girl, library",
            decomposer=lambda *args, **kwargs: {
                "positiveEn": "1girl, library",
                "positiveZh": "1girl, library",
                "negativeEn": "",
                "negativeZh": "",
                "relationEn": "",
                "relationZh": "",
                "blocks": [
                    {"id": "subject", "en": "1girl", "zh": "1girl"},
                    {"id": "scene", "en": "library", "zh": "library"},
                ],
                "checks": {},
            },
            translator=fake_translator,
        )

        self.assertEqual(calls, [["subject", "scene"], ["subject"], ["scene"]])
        self.assertEqual(result["blocks"][0]["zh"], "中文-subject")
        self.assertEqual(result["blocks"][1]["zh"], "中文-scene")
        self.assertNotIn("translationWarning", result["checks"])


if __name__ == "__main__":
    unittest.main()
