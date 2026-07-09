import sys
import unittest
import base64
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import (  # noqa: E402
    PROMPT_TEMPLATES,
    map_artist,
    map_character,
    normalize_search_result,
    process_text_decompose_request,
    process_text_provider_test,
    process_text_expand_request,
    process_text_random_request,
    process_text_regenerate_block_request,
    process_text_regenerate_blocks_request,
    process_text_translate_pending_request,
    process_vision_analyze_request,
    read_prompt_template,
)


class AnimaDexAdapterTests(unittest.TestCase):
    def test_character_maps_to_subject_and_appearance(self):
        item = map_character(
            {
                "slug": "hatsune_miku",
                "name": "Hatsune Miku",
                "copyright_name": "Vocaloid",
                "trigger": "hatsune miku, vocaloid",
                "tags": ["1girl", "aqua hair", "twintails", "aqua eyes"],
                "has_image": True,
            }
        )

        self.assertEqual(item["source"], "AnimaDex")
        self.assertEqual([block["id"] for block in item["blocks"]], ["subject", "appearance"])
        self.assertEqual(item["blocks"][1]["en"], "1girl, aqua hair, twintails, aqua eyes")
        self.assertEqual(item["thumbUrl"], "/api/animadex/thumb/characters/hatsune_miku")

    def test_artist_maps_to_artist_block(self):
        item = map_artist(
            {
                "slug": "mika_pikazo",
                "name": "Mika Pikazo",
                "trigger": "@mika pikazo",
                "score": 4.8,
            }
        )

        self.assertEqual(item["targetBlock"], "artist")
        self.assertEqual(item["blocks"][0]["en"], "@mika pikazo")

    def test_search_result_is_normalized_for_the_workbench(self):
        result = normalize_search_result(
            "characters",
            {
                "total": 1,
                "page": 1,
                "pages": 1,
                "page_size": 72,
                "results": [
                    {
                        "slug": "sample",
                        "name": "Sample",
                        "trigger": "sample character",
                        "tags": "1girl",
                    }
                ],
            },
        )

        self.assertTrue(result["connected"])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["name"], "Sample")

    def test_prompt_templates_are_whitelisted_and_readable(self):
        text_template = read_prompt_template("text_expand")
        image_template = read_prompt_template("image_analyze")
        missing_template = read_prompt_template("../server")

        self.assertIn("文生图", text_template["content"])
        self.assertIn("图生图", image_template["content"])
        self.assertIsNone(missing_template)

    def test_basic_text_modules_are_whitelisted_and_readable(self):
        expected = {
            "text_core_visual",
            "text_mode_expand",
            "text_format_hybrid",
            "text_model_anima",
            "text_schema_bilingual",
        }

        self.assertTrue(expected.issubset(PROMPT_TEMPLATES))
        for template_id in expected:
            item = read_prompt_template(template_id)
            self.assertEqual(item["kind"], "text")
            self.assertTrue(item["content"].startswith("# "))

    def test_text_expand_request_uses_saved_provider_configuration(self):
        captured = {}

        def fake_engine(
            user_input,
            settings,
            provider,
            target_model,
            context,
            expansion_level,
        ):
            captured.update(
                {
                    "input": user_input,
                    "settings": settings,
                    "provider": provider,
                    "targetModel": target_model,
                    "context": context,
                    "expansionLevel": expansion_level,
                }
            )
            return {"positiveEn": "ok", "blocks": []}

        result = process_text_expand_request(
            {
                "input": "1girl, raining",
                "provider": "local",
                "targetModel": "anima",
                "context": {"lockedBlocks": ["subject"]},
                "expansionLevel": "strict",
            },
            settings_payload={
                "textProvider": "api",
                "localTextUrl": "http://127.0.0.1:8080/v1",
                "localTextModel": "local-model",
            },
            engine=fake_engine,
        )

        self.assertEqual(result["positiveEn"], "ok")
        self.assertEqual(captured["provider"], "local")
        self.assertEqual(captured["targetModel"], "anima")
        self.assertIn("lockedBlocks", captured["context"])
        self.assertEqual(captured["expansionLevel"], "strict")

    def test_text_random_request_uses_saved_external_provider_without_input(self):
        captured = {}

        def fake_engine(settings, provider, target_model):
            captured.update(
                {
                    "settings": settings,
                    "provider": provider,
                    "targetModel": target_model,
                }
            )
            return {"positiveEn": "dense random prompt", "blocks": []}

        result = process_text_random_request(
            {"provider": "api"},
            settings_payload={
                "textProvider": "api",
                "apiTextUrl": "https://api.deepseek.com",
                "apiTextModel": "deepseek-v4-flash",
                "apiTextKey": "secret",
            },
            engine=fake_engine,
        )

        self.assertEqual(result["positiveEn"], "dense random prompt")
        self.assertEqual(captured["provider"], "api")
        self.assertEqual(captured["targetModel"], "anima")
        self.assertEqual(captured["settings"]["apiTextModel"], "deepseek-v4-flash")

    def test_text_expand_request_has_first_run_local_defaults(self):
        captured = {}

        def fake_engine(
            user_input,
            settings,
            provider,
            target_model,
            context,
            expansion_level,
        ):
            captured.update(settings)
            captured["expansionLevel"] = expansion_level
            return {"positiveEn": "ok", "blocks": []}

        process_text_expand_request(
            {"input": "1girl"},
            settings_payload={},
            engine=fake_engine,
        )

        self.assertEqual(
            captured["localTextUrl"], "http://127.0.0.1:8080/v1"
        )
        self.assertEqual(
            captured["localTextModel"],
            "Huihui-Qwen3-14B-abliterated-v2.Q4_K_M.gguf",
        )
        self.assertEqual(captured["expansionLevel"], "balanced")

    def test_text_expand_request_rejects_blank_input(self):
        with self.assertRaises(ValueError):
            process_text_expand_request(
                {"input": "   "},
                settings_payload={"textProvider": "local"},
                engine=lambda *args, **kwargs: {},
            )

    def test_text_expand_request_rejects_unknown_provider(self):
        with self.assertRaises(ValueError):
            process_text_expand_request(
                {"input": "1girl", "provider": "unknown"},
                settings_payload={"textProvider": "local"},
                engine=lambda *args, **kwargs: {},
            )

    def test_provider_test_uses_saved_external_configuration(self):
        captured = {}

        def fake_probe(settings, provider):
            captured.update({"settings": settings, "provider": provider})
            return {"connected": True, "provider": provider, "content": "OK"}

        result = process_text_provider_test(
            {"provider": "api"},
            settings_payload={
                "apiTextUrl": "https://example.com/v1",
                "apiTextModel": "example-model",
                "apiTextKey": "secret",
            },
            probe=fake_probe,
        )

        self.assertTrue(result["connected"])
        self.assertEqual(captured["provider"], "api")
        self.assertEqual(captured["settings"]["apiTextModel"], "example-model")

    def test_regenerate_block_request_uses_saved_provider_and_blocks(self):
        captured = {}

        def fake_engine(
            target_block_id,
            blocks,
            settings,
            provider,
            expansion_level,
        ):
            captured.update(
                {
                    "target": target_block_id,
                    "blocks": blocks,
                    "provider": provider,
                    "level": expansion_level,
                    "model": settings["apiTextModel"],
                }
            )
            return {"id": target_block_id, "en": "variant", "zh": "变体"}

        result = process_text_regenerate_block_request(
            {
                "targetBlockId": "scene",
                "blocks": [{"id": "scene", "en": "rain", "zh": "雨"}],
                "provider": "api",
                "expansionLevel": "creative",
            },
            settings_payload={
                "apiTextUrl": "https://api.deepseek.com",
                "apiTextModel": "deepseek-v4-flash",
                "apiTextKey": "secret",
            },
            engine=fake_engine,
        )

        self.assertEqual(result["id"], "scene")
        self.assertEqual(captured["provider"], "api")
        self.assertEqual(captured["level"], "creative")
        self.assertEqual(captured["model"], "deepseek-v4-flash")

    def test_regenerate_blocks_request_uses_selected_ids_and_provider(self):
        captured = {}

        def fake_engine(
            target_block_ids,
            blocks,
            settings,
            provider,
            expansion_level,
        ):
            captured.update(
                {
                    "targets": target_block_ids,
                    "blocks": blocks,
                    "provider": provider,
                    "level": expansion_level,
                    "model": settings["apiTextModel"],
                }
            )
            return {"items": [{"id": "scene", "en": "variant", "zh": "变体"}]}

        result = process_text_regenerate_blocks_request(
            {
                "targetBlockIds": ["scene", "lighting"],
                "blocks": [{"id": "scene"}, {"id": "lighting"}],
                "provider": "api",
                "expansionLevel": "creative",
            },
            settings_payload={
                "apiTextUrl": "https://api.deepseek.com",
                "apiTextModel": "deepseek-v4-flash",
                "apiTextKey": "secret",
            },
            engine=fake_engine,
        )

        self.assertEqual(result["items"][0]["id"], "scene")
        self.assertEqual(captured["targets"], ["scene", "lighting"])
        self.assertEqual(captured["provider"], "api")
        self.assertEqual(captured["level"], "creative")

    def test_text_decompose_request_uses_saved_provider_configuration(self):
        captured = {}

        def fake_engine(user_input, settings, provider):
            captured.update(
                {
                    "input": user_input,
                    "provider": provider,
                    "model": settings["apiTextModel"],
                }
            )
            return {"positiveEn": "1girl", "blocks": []}

        result = process_text_decompose_request(
            {"input": "1girl", "provider": "api"},
            settings_payload={
                "apiTextUrl": "https://api.deepseek.com",
                "apiTextModel": "deepseek-v4-flash",
                "apiTextKey": "secret",
            },
            engine=fake_engine,
        )

        self.assertEqual(result["positiveEn"], "1girl")
        self.assertEqual(captured["provider"], "api")
        self.assertEqual(captured["model"], "deepseek-v4-flash")

    def test_text_decompose_request_rejects_blank_input(self):
        with self.assertRaises(ValueError):
            process_text_decompose_request(
                {"input": "   "},
                settings_payload={},
                engine=lambda *args, **kwargs: {},
            )

    def test_pending_translation_request_always_uses_saved_local_settings(self):
        captured = {}

        def fake_engine(items, settings):
            captured.update(
                {
                    "items": items,
                    "model": settings["localTextModel"],
                    "url": settings["localTextUrl"],
                }
            )
            return {
                "translations": [
                    {"id": "subject", "zh": "初音未来"}
                ]
            }

        result = process_text_translate_pending_request(
            {
                "items": [
                    {"id": "subject", "en": "Hatsune Miku"}
                ]
            },
            settings_payload={
                "textProvider": "api",
                "localTextUrl": "http://127.0.0.1:8080/v1",
                "localTextModel": "local-model",
                "apiTextModel": "external-model",
            },
            engine=fake_engine,
        )

        self.assertEqual(result["translations"][0]["zh"], "初音未来")
        self.assertEqual(captured["model"], "local-model")
        self.assertEqual(
            captured["url"],
            "http://127.0.0.1:8080/v1",
        )

    def test_pending_translation_request_rejects_empty_items(self):
        with self.assertRaises(ValueError):
            process_text_translate_pending_request(
                {"items": []},
                settings_payload={},
                engine=lambda *args, **kwargs: {},
            )

    def test_vision_analysis_decodes_uploaded_image_and_uses_local_settings(self):
        captured = {}

        def fake_engine(image_bytes, filename, analyzer_ids, settings):
            captured.update(
                {
                    "bytes": image_bytes,
                    "filename": filename,
                    "analyzers": analyzer_ids,
                    "model": settings["localTextModel"],
                }
            )
            return {"positiveEn": "multiple girls", "blocks": []}

        result = process_vision_analyze_request(
            {
                "filename": "reference.webp",
                "mimeType": "image/webp",
                "dataBase64": base64.b64encode(b"actual-image").decode("ascii"),
                "analyzerIds": ["florence"],
            },
            settings_payload={"localTextModel": "local-model"},
            engine=fake_engine,
        )

        self.assertEqual(result["positiveEn"], "multiple girls")
        self.assertEqual(captured["bytes"], b"actual-image")
        self.assertEqual(captured["filename"], "reference.webp")
        self.assertEqual(captured["analyzers"], ["florence"])
        self.assertEqual(captured["model"], "local-model")

    def test_vision_analysis_rejects_invalid_or_empty_image_data(self):
        for payload in (
            {},
            {
                "filename": "reference.webp",
                "mimeType": "image/webp",
                "dataBase64": "not-base64!",
                "analyzerIds": ["florence"],
            },
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    process_vision_analyze_request(
                        payload,
                        settings_payload={},
                        engine=lambda *args, **kwargs: {},
                    )

    def test_local_llm_routes_are_declared(self):
        source = (Path(__file__).resolve().parents[1] / "server.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('"/api/local-llm/status"', source)
        self.assertIn('"/api/local-llm/start"', source)
        self.assertIn('"/api/local-llm/stop"', source)
        self.assertIn('"/api/text/regenerate-block"', source)
        self.assertIn('"/api/text/regenerate-blocks"', source)
        self.assertIn('"/api/text/decompose"', source)
        self.assertIn('"/api/text/random"', source)
        self.assertIn('"/api/text/translate-pending"', source)
        self.assertIn('"/api/vision/analyze"', source)


if __name__ == "__main__":
    unittest.main()
