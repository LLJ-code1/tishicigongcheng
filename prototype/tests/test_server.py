import sys
import tempfile
import unittest
import base64
import concurrent.futures
import hashlib
import io
import json
import socket
import sqlite3
import threading
import time
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
from unittest.mock import patch

from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import (  # noqa: E402
    MAX_JSON_BODY_BYTES,
    PROMPT_TEMPLATES,
    PromptStudioHandler,
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
import db  # noqa: E402


def make_png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), color=(12, 34, 56)).save(output, format="PNG")
    return output.getvalue()


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
        image_bytes = make_png_bytes()

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
                "filename": "reference.png",
                "mimeType": "image/png",
                "dataBase64": base64.b64encode(image_bytes).decode("ascii"),
                "analyzerIds": ["florence"],
            },
            settings_payload={"localTextModel": "local-model"},
            engine=fake_engine,
        )

        self.assertEqual(result["positiveEn"], "multiple girls")
        self.assertEqual(captured["bytes"], image_bytes)
        self.assertEqual(captured["filename"], "reference.png")
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

    def test_vision_analysis_rejects_non_image_bytes_with_a_forged_mime_type(self):
        payload = {
            "filename": "disguised.png",
            "mimeType": "image/png",
            "dataBase64": base64.b64encode(b"not actually an image").decode("ascii"),
            "analyzerIds": ["florence"],
        }

        with self.assertRaises(ValueError):
            process_vision_analyze_request(
                payload,
                settings_payload={},
                engine=lambda *args, **kwargs: {},
            )

    def test_vision_analysis_rejects_truncated_signature_only_images(self):
        cases = (
            ("truncated.png", "image/png", b"\x89PNG\r\n\x1a\n"),
            ("truncated.jpg", "image/jpeg", b"\xff\xd8\xff"),
            ("truncated.webp", "image/webp", b"RIFF\x04\x00\x00\x00WEBP"),
        )
        for filename, mime_type, image_bytes in cases:
            with self.subTest(filename=filename):
                payload = {
                    "filename": filename,
                    "mimeType": mime_type,
                    "dataBase64": base64.b64encode(image_bytes).decode("ascii"),
                    "analyzerIds": ["florence"],
                }

                with self.assertRaisesRegex(ValueError, "损坏|解码"):
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


class HttpBoundaryAdversarialTests(unittest.TestCase):
    """Exercise malformed requests and direct static-file access at the HTTP boundary."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "index.html").write_text("safe home", encoding="utf-8")
        (self.root / "unicode15-data.js").write_text(
            "// Unicode 15.0.0\n", encoding="utf-8"
        )
        private_data = self.root / "data"
        private_data.mkdir()
        (private_data / "prompt_studio.db").write_bytes(b"secret sqlite content")
        (self.root / ".env").write_text("API_KEY=do-not-leak", encoding="utf-8")
        (self.root / "server.py").write_text("private source", encoding="utf-8")
        self.root_patch = patch("server.ROOT", self.root)
        self.root_patch.start()
        self.api_db_path = self.root / "data" / "api-test.db"
        self.db_path_patch = patch("db.DEFAULT_DB_PATH", self.api_db_path)
        self.db_path_patch.start()
        db.init_db(self.api_db_path)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), PromptStudioHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.thread.join(timeout=2)
        self.httpd.server_close()
        self.db_path_patch.stop()
        self.root_patch.stop()
        self.tempdir.cleanup()

    def raw_request(
        self,
        request: bytes,
        *,
        shutdown_write: bool = True,
    ) -> tuple[int, bytes, bytes]:
        with socket.create_connection(
            ("127.0.0.1", self.httpd.server_port),
            timeout=2,
        ) as connection:
            connection.settimeout(3)
            connection.sendall(request)
            if shutdown_write:
                connection.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = connection.recv(64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        response = b"".join(chunks)
        header, _, body = response.partition(b"\r\n\r\n")
        status = int(header.split(b" ", 2)[1])
        return status, header, body

    def assert_http_error_json(
        self,
        request: Request,
        expected_status: int,
    ) -> dict:
        with self.assertRaises(HTTPError) as context:
            urlopen(request)
        self.assertEqual(context.exception.code, expected_status)
        self.assertEqual(
            context.exception.headers.get_content_type(),
            "application/json",
        )
        return json.loads(context.exception.read().decode("utf-8"))

    def json_request(
        self,
        path: str,
        payload: dict,
        *,
        method: str = "POST",
    ) -> tuple[int, dict]:
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_static_handler_serves_only_public_assets(self):
        with urlopen(f"{self.base_url}/index.html") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"safe home")

        with urlopen(f"{self.base_url}/unicode15-data.js") as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"Unicode 15.0.0", response.read())

        with self.assertRaises(HTTPError) as context:
            urlopen(f"{self.base_url}/data/prompt_studio.db")

        self.assertEqual(context.exception.code, 404)

        with self.assertRaises(HTTPError) as context:
            urlopen(f"{self.base_url}/assets/%2e%2e/data/prompt_studio.db")

        self.assertEqual(context.exception.code, 404)

    def test_head_uses_the_same_static_allowlist_and_returns_no_body(self):
        request = Request(f"{self.base_url}/index.html", method="HEAD")
        with urlopen(request) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

        for path in ("/.env", "/server.py", "/data/prompt_studio.db"):
            with self.subTest(path=path):
                with self.assertRaises(HTTPError) as context:
                    urlopen(Request(f"{self.base_url}{path}", method="HEAD"))
                self.assertEqual(context.exception.code, 404)

    def test_untrusted_or_ambiguous_host_is_rejected_before_settings_access(self):
        sentinel = "TEST-KEY-MUST-NOT-LEAK"
        cases = (
            b"Host: evil.example:%d\r\n" % self.httpd.server_port,
            b"",
            (
                b"Host: 127.0.0.1:%d\r\n"
                b"Host: evil.example:%d\r\n"
            ) % (self.httpd.server_port, self.httpd.server_port),
        )
        with patch("server.get_settings", return_value={"apiTextKey": sentinel}) as get:
            for host_headers in cases:
                with self.subTest(host_headers=host_headers):
                    status, _, body = self.raw_request(
                        b"GET /api/settings HTTP/1.1\r\n"
                        + host_headers
                        + b"Connection: close\r\n\r\n"
                    )
                    self.assertEqual(status, 421)
                    self.assertNotIn(sentinel.encode("utf-8"), body)
        get.assert_not_called()

    def test_settings_response_redacts_keys_and_reports_configuration(self):
        with patch(
            "server.get_settings",
            return_value={
                "apiTextKey": "TEST-API-SECRET",
                "localTextKey": "TEST-LOCAL-SECRET",
                "apiTextModel": "example-model",
            },
        ):
            with urlopen(f"{self.base_url}/api/settings") as response:
                raw_body = response.read()
                payload = json.loads(raw_body.decode("utf-8"))

        self.assertNotIn(b"TEST-API-SECRET", raw_body)
        self.assertNotIn(b"TEST-LOCAL-SECRET", raw_body)
        self.assertEqual(payload["settings"]["apiTextKey"], "")
        self.assertEqual(payload["settings"]["localTextKey"], "")
        self.assertTrue(payload["settings"]["apiTextKeyConfigured"])
        self.assertTrue(payload["settings"]["localTextKeyConfigured"])

    def test_settings_put_preserves_blank_or_omitted_saved_keys(self):
        captured = {}

        def fake_put_settings(payload):
            captured.update(payload)
            return {
                **payload,
                "apiTextKey": "SAVED-API-SECRET",
                "localTextKey": "SAVED-LOCAL-SECRET",
            }

        request = Request(
            f"{self.base_url}/api/settings",
            data=json.dumps(
                {
                    "apiTextKey": "   ",
                    "apiTextModel": "new-model",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with patch("server.put_settings", side_effect=fake_put_settings):
            with urlopen(request) as response:
                raw_body = response.read()
                payload = json.loads(raw_body.decode("utf-8"))

        self.assertEqual(captured, {"apiTextModel": "new-model"})
        self.assertNotIn(b"SAVED-API-SECRET", raw_body)
        self.assertEqual(payload["settings"]["apiTextKey"], "")
        self.assertTrue(payload["settings"]["apiTextKeyConfigured"])

    def test_settings_put_rejects_unknown_fields_and_wrong_types(self):
        invalid_payloads = (
            {"apiTextKeyConfigured": True},
            {"unknownSecret": "must-not-store"},
            {"autoCombine": "yes"},
            {"textProvider": "unknown"},
            {"apiTextModel": {"nested": "model"}},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                request = Request(
                    f"{self.base_url}/api/settings",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="PUT",
                )
                self.assert_http_error_json(request, 400)

    def test_cross_site_or_non_json_writes_are_rejected_before_side_effects(self):
        cases = (
            ({"Origin": "https://evil.example"}, 403),
            ({"Sec-Fetch-Site": "cross-site"}, 403),
            ({"Content-Type": "text/plain"}, 415),
        )
        with patch("server.start_local_llm") as start:
            for extra_headers, expected_status in cases:
                headers = {"Content-Type": "application/json", **extra_headers}
                with self.subTest(headers=headers):
                    payload = self.assert_http_error_json(
                        Request(
                            f"{self.base_url}/api/local-llm/start",
                            data=b"{}",
                            headers=headers,
                            method="POST",
                        ),
                        expected_status,
                    )
                    self.assertIn("error", payload)
        start.assert_not_called()

    def test_same_origin_json_write_and_bodyless_json_delete_are_allowed(self):
        with patch("server.start_local_llm", return_value={"running": True}):
            request = Request(
                f"{self.base_url}/api/local-llm/start",
                data=b"{}",
                headers={
                    "Content-Type": "application/json; charset=UTF-8",
                    "Origin": self.base_url,
                    "Sec-Fetch-Site": "same-origin",
                },
                method="POST",
            )
            with urlopen(request) as response:
                self.assertEqual(response.status, 200)

        with patch("server.delete_favorite", return_value=True) as delete:
            request = Request(
                f"{self.base_url}/api/favorites/favorite-1",
                headers={"Content-Type": "application/json"},
                method="DELETE",
            )
            with urlopen(request) as response:
                self.assertEqual(response.status, 200)
        delete.assert_called_once_with("favorite-1")

    def test_request_framing_rejects_smuggling_and_invalid_lengths(self):
        port = self.httpd.server_port
        prefix = (
            b"POST /api/text/expand HTTP/1.1\r\n"
            + b"Host: 127.0.0.1:%d\r\n" % port
            + b"Content-Type: application/json\r\n"
        )
        cases = (
            (prefix + b"Connection: close\r\n\r\n", 411),
            (prefix + b"Content-Length: -1\r\nConnection: close\r\n\r\n", 400),
            (
                prefix
                + b"Content-Length: 2\r\nContent-Length: 2\r\n"
                + b"Connection: close\r\n\r\n{}",
                400,
            ),
            (
                prefix
                + b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
                + b"2\r\n{}\r\n0\r\n\r\n",
                400,
            ),
            (
                prefix
                + b"Content-Length: 10\r\nConnection: close\r\n\r\n{}",
                400,
            ),
            (
                prefix
                + f"Content-Length: {MAX_JSON_BODY_BYTES + 1}\r\n".encode("ascii")
                + b"Connection: close\r\n\r\n",
                413,
            ),
        )
        for request, expected_status in cases:
            with self.subTest(expected_status=expected_status, request=request[:100]):
                status, _, body = self.raw_request(request)
                self.assertEqual(status, expected_status)
                self.assertIn(b"error", body)

    def test_partial_request_body_times_out_instead_of_holding_a_thread(self):
        port = self.httpd.server_port
        request = (
            b"POST /api/text/expand HTTP/1.1\r\n"
            + b"Host: 127.0.0.1:%d\r\n" % port
            + b"Content-Type: application/json\r\n"
            + b"Content-Length: 100\r\nConnection: close\r\n\r\n{"
        )
        started = time.monotonic()
        with patch("server.REQUEST_BODY_TIMEOUT_SECONDS", 0.1):
            status, _, body = self.raw_request(request, shutdown_write=False)
        self.assertEqual(status, 400)
        self.assertIn(b"error", body)
        self.assertLess(time.monotonic() - started, 1.5)

    def test_slow_drip_body_cannot_extend_the_total_request_deadline(self):
        port = self.httpd.server_port
        header = (
            b"POST /api/text/expand HTTP/1.1\r\n"
            + b"Host: 127.0.0.1:%d\r\n" % port
            + b"Content-Type: application/json\r\n"
            + b"Content-Length: 100\r\nConnection: close\r\n\r\n"
        )
        started = time.monotonic()
        with patch("server.REQUEST_BODY_TIMEOUT_SECONDS", 0.2):
            with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
                connection.settimeout(2)
                connection.sendall(header + b"{")
                time.sleep(0.08)
                connection.sendall(b'"')
                time.sleep(0.08)
                connection.sendall(b"i")
                response_chunks = []
                while True:
                    try:
                        chunk = connection.recv(64 * 1024)
                    except (OSError, socket.timeout):
                        break
                    if not chunk:
                        break
                    response_chunks.append(chunk)
        response = b"".join(response_chunks)
        self.assertIn(b" 400 ", response.partition(b"\r\n")[0])
        self.assertIn(b"error", response)
        self.assertLess(time.monotonic() - started, 0.32)

    def test_incomplete_headers_are_closed_by_connection_timeout(self):
        started = time.monotonic()
        with patch("server.CONNECTION_TIMEOUT_SECONDS", 0.1):
            with socket.create_connection(
                ("127.0.0.1", self.httpd.server_port), timeout=2
            ) as connection:
                connection.settimeout(1)
                connection.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1")
                try:
                    response = connection.recv(64 * 1024)
                except OSError:
                    response = b""
        self.assertNotIn(b" 200 ", response.partition(b"\r\n")[0])
        self.assertLess(time.monotonic() - started, 1.5)

    def test_slow_drip_headers_cannot_extend_the_total_header_deadline(self):
        started = time.monotonic()
        with patch("server.CONNECTION_TIMEOUT_SECONDS", 0.2):
            with socket.create_connection(
                ("127.0.0.1", self.httpd.server_port), timeout=2
            ) as connection:
                connection.settimeout(1)
                connection.sendall(b"GET /api/settings HTTP/1.1\r\nHost: 127")
                time.sleep(0.08)
                connection.sendall(b".")
                time.sleep(0.08)
                connection.sendall(b"0")
                time.sleep(0.08)
                try:
                    connection.sendall(
                        b".0.1:%d\r\nConnection: close\r\n\r\n"
                        % self.httpd.server_port
                    )
                except OSError:
                    pass
                response_chunks = []
                while True:
                    try:
                        chunk = connection.recv(64 * 1024)
                    except OSError:
                        break
                    if not chunk:
                        break
                    response_chunks.append(chunk)
        response = b"".join(response_chunks)
        self.assertNotIn(b" 200 ", response.partition(b"\r\n")[0])
        self.assertNotIn(b'"settings"', response)
        self.assertLess(time.monotonic() - started, 0.4)

    def test_vision_body_uses_a_separate_larger_total_deadline(self):
        port = self.httpd.server_port
        request = (
            b"POST /api/vision/analyze HTTP/1.1\r\n"
            + b"Host: 127.0.0.1:%d\r\n" % port
            + b"Content-Type: application/json\r\n"
            + b"Content-Length: 100\r\nConnection: close\r\n\r\n{"
        )
        started = time.monotonic()
        with (
            patch("server.REQUEST_BODY_TIMEOUT_SECONDS", 0.02),
            patch("server.VISION_REQUEST_BODY_TIMEOUT_SECONDS", 0.15),
        ):
            status, _, body = self.raw_request(request, shutdown_write=False)
        elapsed = time.monotonic() - started
        self.assertEqual(status, 400)
        self.assertIn(b"error", body)
        self.assertGreater(elapsed, 0.08)
        self.assertLess(elapsed, 1.0)

    def test_local_llm_actions_validate_complete_empty_json_before_side_effects(self):
        port = self.httpd.server_port
        for action in ("start", "stop"):
            with self.subTest(action=action), patch(f"server.{action}_local_llm") as side_effect:
                self.assert_http_error_json(
                    Request(
                        f"{self.base_url}/api/local-llm/{action}",
                        data=b'{"unexpected":true}',
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    400,
                )
                request = (
                    f"POST /api/local-llm/{action} HTTP/1.1\r\n".encode("ascii")
                    + b"Host: 127.0.0.1:%d\r\n" % port
                    + b"Content-Type: application/json\r\n"
                    + b"Content-Length: 100\r\nConnection: close\r\n\r\n{"
                )
                with patch("server.REQUEST_BODY_TIMEOUT_SECONDS", 0.1):
                    status, _, body = self.raw_request(
                        request,
                        shutdown_write=False,
                    )
                self.assertEqual(status, 400)
                self.assertIn(b"error", body)
                side_effect.assert_not_called()

    def test_json_parser_rejects_non_finite_numbers_and_duplicate_keys(self):
        bodies = (
            b'{"cfg":NaN}',
            b'{"cfg":Infinity}',
            b'{"cfg":-Infinity}',
            b'{"cfg":1e400}',
            b'{"input":"one","input":"two"}',
        )
        for body in bodies:
            with self.subTest(body=body):
                payload = self.assert_http_error_json(
                    Request(
                        f"{self.base_url}/api/text/expand",
                        data=body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    400,
                )
                self.assertIn("error", payload)

    def test_database_and_unexpected_errors_have_stable_json_responses(self):
        request = Request(
            f"{self.base_url}/api/projects",
            data=b'{"id":"duplicate","name":"Duplicate"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with patch(
            "server.create_project",
            side_effect=sqlite3.IntegrityError("do not expose SQL details"),
        ):
            payload = self.assert_http_error_json(request, 409)
        self.assertNotIn("SQL", payload["error"])

        with patch("server.list_projects", side_effect=RuntimeError("private detail")):
            payload = self.assert_http_error_json(
                Request(f"{self.base_url}/api/projects"),
                500,
            )
        self.assertEqual(payload["error"], "服务器内部错误")

    def test_project_schema_rejects_pollution_and_reports_conflicts(self):
        invalid_payloads = (
            {"id": "slash/id"},
            {"name": {"nested": "name"}},
            {"mode": ["text"]},
            {"metadata": []},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                error = self.assert_http_error_json(
                    Request(
                        f"{self.base_url}/api/projects",
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    400,
                )
                self.assertIn("error", error)

        status, created = self.json_request(
            "/api/projects",
            {
                "id": "project-fixed",
                "name": "Safe project",
                "metadata": {
                    "settings": {
                        "apiTextKey": "TEST-NESTED-SECRET",
                        "textProvider": "api",
                    }
                },
            },
        )
        self.assertEqual(status, 201)
        self.assertNotIn(
            "apiTextKey",
            created["item"]["metadata"]["settings"],
        )

        duplicate = Request(
            f"{self.base_url}/api/projects",
            data=b'{"id":"project-fixed"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        self.assert_http_error_json(duplicate, 409)

        with urlopen(f"{self.base_url}/api/projects") as response:
            items = json.loads(response.read().decode("utf-8"))["items"]
        self.assertEqual([item["id"] for item in items], ["project-fixed"])

    def test_version_schema_is_server_numbered_and_blocks_are_typed(self):
        _, created = self.json_request(
            "/api/projects",
            {"id": "project-versions", "name": "Versions"},
        )
        invalid_payloads = (
            {"version": 2_000_000},
            {"blocks": "not-an-array"},
            {"blocks": [{"id": "scene", "weight": 121}]},
            {"blocks": [{"id": "scene", "weight": 10**400}]},
            {"metadata": []},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                request = Request(
                    f"{self.base_url}/api/projects/project-versions/versions",
                    data=json.dumps(
                        {
                            "baseUpdatedAt": created["item"]["updatedAt"],
                            **payload,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                self.assert_http_error_json(request, 400)

        missing_precondition = Request(
            f"{self.base_url}/api/projects/project-versions/versions",
            data=b'{"blocks":[]}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        missing_payload = self.assert_http_error_json(missing_precondition, 428)
        self.assertEqual(
            missing_payload["code"],
            "project_precondition_required",
        )

        first_status, first = self.json_request(
            "/api/projects/project-versions/versions",
            {
                "baseUpdatedAt": created["item"]["updatedAt"],
                "blocks": [{"id": "scene", "weight": 120}],
            },
        )
        second_status, second = self.json_request(
            "/api/projects/project-versions/versions",
            {
                "baseUpdatedAt": first["item"]["projectUpdatedAt"],
                "blocks": [],
            },
        )
        self.assertEqual((first_status, second_status), (201, 201))
        self.assertEqual(
            [first["item"]["version"], second["item"]["version"]],
            [1, 2],
        )

    def test_project_can_be_listed_renamed_reopened_and_continued(self):
        created_status, created = self.json_request(
            "/api/projects",
            {
                "id": "project-reopen",
                "name": "初始名称",
                "mode": "text",
                "metadata": {"draftInput": "一位夜行旅人"},
            },
        )
        self.assertEqual(created_status, 201)
        self.assertEqual(created["item"]["versions"], [])

        first_status, first = self.json_request(
            "/api/projects/project-reopen/versions",
            {
                "baseVersion": 0,
                "baseUpdatedAt": created["item"]["updatedAt"],
                "positiveEn": "adult traveler, night city",
                "positiveZh": "成年旅人，夜色城市",
                "blocks": [{"id": "subject", "weight": 100}],
                "metadata": {"draftInput": "一位夜行旅人"},
            },
        )
        self.assertEqual(first_status, 201)
        self.assertEqual(first["item"]["version"], 1)

        with urlopen(f"{self.base_url}/api/projects") as response:
            projects = json.loads(response.read().decode("utf-8"))["items"]
        summary = next(item for item in projects if item["id"] == "project-reopen")
        self.assertEqual(summary["versionCount"], 1)
        self.assertEqual(summary["latestVersion"], 1)

        update_status, updated = self.json_request(
            "/api/projects/project-reopen",
            {
                "baseUpdatedAt": first["item"]["projectUpdatedAt"],
                "name": "夜行旅人",
                "mode": "text",
                "status": "draft",
                "metadata": {"draftInput": "加入雨夜"},
            },
            method="PUT",
        )
        self.assertEqual(update_status, 200)
        self.assertEqual(updated["item"]["name"], "夜行旅人")
        self.assertEqual(updated["item"]["metadata"]["draftInput"], "加入雨夜")

        second_status, second = self.json_request(
            "/api/projects/project-reopen/versions",
            {
                "baseVersion": 1,
                "baseUpdatedAt": updated["item"]["updatedAt"],
                "positiveEn": "adult traveler, rainy night city",
                "positiveZh": "成年旅人，雨夜城市",
                "blocks": [{"id": "scene", "weight": 100}],
                "metadata": {"restoredFromVersion": 1},
            },
        )
        self.assertEqual(second_status, 201)
        self.assertEqual(second["item"]["version"], 2)

        with urlopen(
            f"{self.base_url}/api/projects/project-reopen"
        ) as response:
            reopened = json.loads(response.read().decode("utf-8"))["item"]
        self.assertEqual(reopened["name"], "夜行旅人")
        self.assertEqual(
            [item["version"] for item in reopened["versions"]],
            [1, 2],
        )
        self.assertEqual(
            reopened["versions"][-1]["positiveEn"],
            "adult traveler, rainy night city",
        )

    def test_stale_base_version_returns_conflict_without_writing(self):
        _, created = self.json_request(
            "/api/projects",
            {"id": "project-conflict", "name": "Conflict"},
        )
        self.json_request(
            "/api/projects/project-conflict/versions",
            {
                "baseVersion": 0,
                "baseUpdatedAt": created["item"]["updatedAt"],
                "positiveEn": "first",
            },
        )

        request = Request(
            f"{self.base_url}/api/projects/project-conflict/versions",
            data=json.dumps(
                {
                    "baseVersion": 0,
                    "baseUpdatedAt": created["item"]["updatedAt"],
                    "positiveEn": "stale",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        conflict = self.assert_http_error_json(request, 409)
        self.assertEqual(conflict["code"], "version_conflict")
        self.assertEqual(conflict["expectedVersion"], 0)
        self.assertEqual(conflict["currentVersion"], 1)

        with urlopen(
            f"{self.base_url}/api/projects/project-conflict"
        ) as response:
            project = json.loads(response.read().decode("utf-8"))["item"]
        self.assertEqual(len(project["versions"]), 1)
        self.assertEqual(project["versions"][0]["positiveEn"], "first")

    def test_project_update_rejects_unknown_fields_and_exact_path_mismatches(self):
        _, created = self.json_request(
            "/api/projects",
            {"id": "project-update", "name": "Update"},
        )
        unknown = Request(
            f"{self.base_url}/api/projects/project-update",
            data=json.dumps(
                {
                    "baseUpdatedAt": created["item"]["updatedAt"],
                    "createdAt": "forged",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        self.assert_http_error_json(unknown, 400)

        missing_precondition = Request(
            f"{self.base_url}/api/projects/project-update",
            data=b'{"name":"forged"}',
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        missing_payload = self.assert_http_error_json(missing_precondition, 428)
        self.assertEqual(
            missing_payload["code"],
            "project_precondition_required",
        )

        for path, expected_status in (
            ("/api/projects/project-update/extra", 404),
            ("/api/projects/project-update;extra", 400),
            ("/api/projects//", 404),
        ):
            with self.subTest(path=path, expected_status=expected_status):
                request = Request(
                    f"{self.base_url}{path}",
                    data=b'{"name":"forged"}',
                    headers={"Content-Type": "application/json"},
                    method="PUT",
                )
                self.assert_http_error_json(request, expected_status)

    def test_concurrent_project_puts_with_same_base_allow_only_one(self):
        _, created = self.json_request(
            "/api/projects",
            {"id": "project-put-cas", "name": "Original"},
        )
        base_updated_at = created["item"]["updatedAt"]
        barrier = threading.Barrier(2)

        def update(index):
            barrier.wait(timeout=2)
            request = Request(
                f"{self.base_url}/api/projects/project-put-cas",
                data=json.dumps(
                    {
                        "baseUpdatedAt": base_updated_at,
                        "name": f"Writer {index}",
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="PUT",
            )
            try:
                with urlopen(request) as response:
                    return response.status, json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                return error.code, json.loads(error.read().decode("utf-8"))

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(update, range(2)))

        self.assertEqual(sorted(status for status, _ in results), [200, 409])
        success = next(payload for status, payload in results if status == 200)
        conflict = next(payload for status, payload in results if status == 409)
        self.assertEqual(conflict["code"], "project_conflict")
        self.assertEqual(conflict["expected"], base_updated_at)
        self.assertEqual(conflict["expectedUpdatedAt"], base_updated_at)
        self.assertEqual(conflict["actual"], success["item"]["updatedAt"])
        self.assertEqual(conflict["actualUpdatedAt"], success["item"]["updatedAt"])

        with urlopen(f"{self.base_url}/api/projects/project-put-cas") as response:
            stored = json.loads(response.read().decode("utf-8"))["item"]
        self.assertEqual(stored["name"], success["item"]["name"])
        self.assertEqual(stored["updatedAt"], success["item"]["updatedAt"])

    def test_version_response_timestamp_can_guard_followup_project_put(self):
        _, created = self.json_request(
            "/api/projects",
            {"id": "project-version-put", "name": "Before"},
        )
        version_status, version = self.json_request(
            "/api/projects/project-version-put/versions",
            {
                "baseVersion": 0,
                "baseUpdatedAt": created["item"]["updatedAt"],
                "positiveEn": "first",
            },
        )

        update_status, updated = self.json_request(
            "/api/projects/project-version-put",
            {
                "baseUpdatedAt": version["item"]["projectUpdatedAt"],
                "name": "After version",
            },
            method="PUT",
        )

        self.assertEqual(version_status, 201)
        self.assertEqual(update_status, 200)
        self.assertEqual(updated["item"]["name"], "After version")
        self.assertGreater(
            updated["item"]["updatedAt"],
            version["item"]["projectUpdatedAt"],
        )

    def test_header_update_invalidates_stale_version_request(self):
        _, created = self.json_request(
            "/api/projects",
            {
                "id": "project-header-before-version",
                "metadata": {"draftInput": "original"},
            },
        )
        stale_updated_at = created["item"]["updatedAt"]
        update_status, updated = self.json_request(
            "/api/projects/project-header-before-version",
            {
                "baseUpdatedAt": stale_updated_at,
                "metadata": {"draftInput": "writer-b"},
            },
            method="PUT",
        )
        self.assertEqual(update_status, 200)

        request = Request(
            f"{self.base_url}/api/projects/project-header-before-version/versions",
            data=json.dumps(
                {
                    "baseVersion": 0,
                    "baseUpdatedAt": stale_updated_at,
                    "positiveEn": "writer-a",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        conflict = self.assert_http_error_json(request, 409)
        self.assertEqual(conflict["code"], "project_conflict")
        self.assertEqual(
            conflict["actualUpdatedAt"],
            updated["item"]["updatedAt"],
        )

        with urlopen(
            f"{self.base_url}/api/projects/project-header-before-version"
        ) as response:
            stored = json.loads(response.read().decode("utf-8"))["item"]
        self.assertEqual(stored["metadata"]["draftInput"], "writer-b")
        self.assertEqual(stored["versions"], [])

    def test_all_http_methods_reject_url_path_parameters_before_routing(self):
        cases = (
            Request(
                f"{self.base_url}/api/projects;extra/project-1",
                method="GET",
            ),
            Request(
                f"{self.base_url}/api/projects/project-1;extra/versions",
                method="HEAD",
            ),
            Request(
                f"{self.base_url}/api/projects;extra/project-1",
                data=b'{"id":"must-not-create"}',
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            Request(
                f"{self.base_url}/api/projects;extra/project-1",
                data=b'{"autoCombine":true}',
                headers={"Content-Type": "application/json"},
                method="PUT",
            ),
            Request(
                f"{self.base_url}/api/favorites;extra/favorite-1",
                headers={"Content-Type": "application/json"},
                method="DELETE",
            ),
        )
        with (
            patch("server.list_projects") as list_projects_mock,
            patch("server.create_project") as create_project_mock,
            patch("server.put_settings") as put_settings_mock,
            patch("server.delete_favorite") as delete_favorite_mock,
        ):
            for request in cases:
                with self.subTest(method=request.method, path=request.full_url):
                    if request.method == "HEAD":
                        with self.assertRaises(HTTPError) as context:
                            urlopen(request)
                        self.assertEqual(context.exception.code, 400)
                        continue
                    payload = self.assert_http_error_json(request, 400)
                    self.assertIn("路径参数", payload["error"])
        list_projects_mock.assert_not_called()
        create_project_mock.assert_not_called()
        put_settings_mock.assert_not_called()
        delete_favorite_mock.assert_not_called()

    def test_request_handlers_do_not_run_schema_migrations(self):
        with patch("server.init_db", side_effect=AssertionError("unexpected migration")) as init:
            with urlopen(f"{self.base_url}/api/projects") as response:
                self.assertEqual(response.status, 200)
        init.assert_not_called()

    def test_legacy_encoded_project_id_can_be_loaded_and_versioned(self):
        project_id = "旧 项目/k-da;legacy"
        timestamp = db.now_iso()
        db.init_db(self.api_db_path)
        with db.database(self.api_db_path) as connection:
            connection.execute(
                """
                INSERT INTO projects (
                    id, name, mode, status, metadata_json, created_at, updated_at
                ) VALUES (?, ?, 'text', 'draft', '{}', ?, ?)
                """,
                (project_id, "Legacy", timestamp, timestamp),
            )
        encoded_id = quote(project_id, safe="")

        with urlopen(f"{self.base_url}/api/projects/{encoded_id}") as response:
            loaded = json.loads(response.read().decode("utf-8"))["item"]
        version_status, version = self.json_request(
            f"/api/projects/{encoded_id}/versions",
            {"baseUpdatedAt": loaded["updatedAt"]},
        )

        self.assertEqual(loaded["id"], project_id)
        self.assertEqual(version_status, 201)
        self.assertEqual(version["item"]["projectId"], project_id)

        with urlopen(f"{self.base_url}/api/projects?q=a;b") as response:
            self.assertEqual(response.status, 200)

    def test_thumbnail_proxy_preserves_encoded_slashes_in_animadex_slugs(self):
        slug = "k/da_ahri"
        with patch(
            "server.fetch_upstream",
            return_value=(b"thumbnail", "image/webp"),
        ) as fetch:
            with urlopen(
                f"{self.base_url}/api/animadex/thumb/characters/{quote(slug, safe='')}"
            ) as response:
                self.assertEqual(response.read(), b"thumbnail")
                self.assertEqual(response.headers.get_content_type(), "image/webp")

        fetch.assert_called_once_with("/thumb/characters/k%2Fda_ahri")

    def test_complex_favorite_id_uses_safe_server_route_id(self):
        resource_id = "animadex-characters-k/da_(series)#100%:variant[alt]'s"
        status, saved = self.json_request(
            "/api/favorites",
            {
                "id": resource_id,
                "type": "characters",
                "name": "Complex slug",
            },
        )
        favorite_id = saved["item"]["favoriteId"]

        self.assertEqual(status, 201)
        self.assertEqual(saved["item"]["id"], resource_id)
        self.assertRegex(favorite_id, r"^favorite-[a-f0-9]{24}$")

        delete_request = Request(
            f"{self.base_url}/api/favorites/{favorite_id}",
            headers={"Content-Type": "application/json"},
            method="DELETE",
        )
        with urlopen(delete_request) as response:
            deleted = json.loads(response.read().decode("utf-8"))
        self.assertTrue(deleted["deleted"])

    def test_version_creation_route_requires_an_exact_path_shape(self):
        cases = (
            ("/api/projects/project-1/extra/versions", 404),
            ("/api/projects/project-1/versions/extra", 404),
            ("/api/projects/project-1/versions;extra", 400),
            ("/api/projects//versions", 404),
        )
        with patch("server.get_project") as get_project_mock:
            for path, expected_status in cases:
                with self.subTest(path=path, expected_status=expected_status):
                    payload = self.assert_http_error_json(
                        Request(
                            f"{self.base_url}{path}",
                            data=b"{}",
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        ),
                        expected_status,
                    )
                    self.assertIn("error", payload)
        get_project_mock.assert_not_called()

    def test_json_endpoints_reject_non_object_and_non_utf8_bodies(self):
        for body in (b"[]", b'"not an object"', b"\xff"):
            with self.subTest(body=body):
                with self.assertRaises(HTTPError) as context:
                    request = Request(
                        f"{self.base_url}/api/text/expand",
                        data=body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    urlopen(request)

                self.assertEqual(context.exception.code, 400)
                payload = json.loads(context.exception.read().decode("utf-8"))
                self.assertIn("error", payload)

    def test_idempotency_header_replays_create_and_rejects_changed_body(self):
        payload = {"id": "project-http-idem", "name": "Stable"}
        request = lambda body: Request(
            f"{self.base_url}/api/projects",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": "http-create-1",
            },
            method="POST",
        )
        with urlopen(request(payload)) as response:
            first = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 201)
        with urlopen(request({"name": "Stable", "id": "project-http-idem"})) as response:
            replay = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 201)
        self.assertEqual(replay, first)
        error = self.assert_http_error_json(
            request({"id": "project-http-other", "name": "Changed"}), 409
        )
        self.assertEqual(error["code"], "idempotency_conflict")
        self.assertEqual(len(db.list_projects(self.api_db_path)), 1)

    def test_duplicate_idempotency_header_is_rejected_before_write(self):
        body = b'{"id":"project-duplicate-header"}'
        request = (
            b"POST /api/projects HTTP/1.1\r\n"
            + f"Host: 127.0.0.1:{self.httpd.server_port}\r\n".encode()
            + b"Content-Type: application/json\r\n"
            + b"Idempotency-Key: one\r\n"
            + b"Idempotency-Key: two\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        status, _, response_body = self.raw_request(request)
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(response_body.decode("utf-8")))
        self.assertIsNone(db.get_project("project-duplicate-header", self.api_db_path))

    def test_atomic_workspace_commit_persists_complete_recipe_and_replays(self):
        block_ids = [
            "quality", "artist", "subject", "appearance", "outfit",
            "expression", "pose", "interaction", "scene", "composition",
            "lighting", "effects", "negative",
        ]
        payload = {
            "operationId": "save-http-atomic",
            "createProject": True,
            "project": {
                "id": "project-http-atomic",
                "name": "Atomic Recipe",
                "metadata": {"workspaceBaseVersion": 1},
            },
            "version": {
                "id": "version-http-atomic",
                "baseVersion": 0,
                "positiveEn": "masterpiece, best quality, score_7",
                "positiveZh": "杰作，最佳质量，score_7",
                "negativeEn": "low quality",
                "negativeZh": "低质量",
                "blocks": [
                    {"id": block_id, "en": "", "zh": ""}
                    for block_id in block_ids
                ],
                "metadata": {},
            },
        }
        request = Request(
            f"{self.base_url}/api/workspace/commit",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": payload["operationId"],
            },
            method="POST",
        )
        with urlopen(request) as response:
            first = json.loads(response.read().decode("utf-8"))["item"]
            self.assertEqual(response.status, 201)
        with urlopen(request) as response:
            replay = json.loads(response.read().decode("utf-8"))["item"]
        self.assertEqual(replay, first)
        recipe = first["version"]["metadata"]["recipe"]
        self.assertEqual(len(recipe["blocks"]), 13)
        self.assertEqual(recipe["model"]["versionId"], 3004063)
        self.assertNotIn("safe", recipe["prompts"]["positiveEn"].split(", "))

    def test_profile_and_random_catalog_are_readable_but_runtime_is_guarded(self):
        with urlopen(f"{self.base_url}/api/model-profiles/anima-1.1-v1") as response:
            profile = json.loads(response.read().decode("utf-8"))["item"]
        self.assertEqual(profile["model"]["versionId"], 3004063)
        self.assertFalse(profile["generationReady"])
        with urlopen(f"{self.base_url}/api/text/random-catalog") as response:
            catalog = json.loads(response.read().decode("utf-8"))["item"]
        self.assertFalse(catalog["runtimeReady"])
        self.assertTrue(catalog["semanticReviewRequired"])
        error = self.assert_http_error_json(
            Request(
                f"{self.base_url}/api/text/random-plan",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            409,
        )
        self.assertEqual(error["code"], "random_catalog_not_release_ready")

    def test_backup_export_inspect_and_isolated_restore_flow(self):
        project = db.create_project(
            {"id": "project-backup-http", "name": "HTTP backup"},
            self.api_db_path,
        )
        db.create_prompt_version(
            project["id"],
            {"id": "version-backup-http", "positiveEn": "1girl"},
            self.api_db_path,
        )

        with urlopen(
            f"{self.base_url}/api/backups/export?scope=project&projectId={project['id']}"
        ) as response:
            archive = response.read()
            self.assertEqual(response.headers.get_content_type(), "application/zip")
            self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(archive)))

        with urlopen(
            Request(
                f"{self.base_url}/api/backups/inspect",
                data=archive,
                headers={"Content-Type": "application/zip"},
                method="POST",
            )
        ) as response:
            inspection = json.loads(response.read().decode("utf-8"))["item"]
        self.assertEqual(inspection["counts"]["projects"], 1)
        self.assertEqual(inspection["counts"]["versions"], 1)

        with urlopen(
            Request(
                f"{self.base_url}/api/backups/stage-restore?conflict=rename",
                data=archive,
                headers={"Content-Type": "application/octet-stream"},
                method="POST",
            )
        ) as response:
            self.assertEqual(response.status, 201)
            report = json.loads(response.read().decode("utf-8"))["item"]
        self.assertFalse(report["activated"])
        staging = Path(report["stagingDatabase"])
        self.assertTrue(staging.is_file())
        self.assertEqual(staging.parent, db.recovery_directory(self.api_db_path))
        renamed = report["renamedProjects"][0]["to"]
        self.assertEqual(db.get_project(project["id"], staging)["name"], "HTTP backup")
        self.assertEqual(db.get_project(renamed, staging)["name"], "HTTP backup")

    def test_backup_attacks_fail_before_current_or_staging_database_changes(self):
        project = db.create_project(
            {"id": "project-backup-protected", "name": "Protected"},
            self.api_db_path,
        )
        before = hashlib.sha256(self.api_db_path.read_bytes()).hexdigest()
        recovery = db.recovery_directory(self.api_db_path)
        before_staging = set(recovery.glob("restore-preview-*.db")) if recovery.exists() else set()

        invalid = self.assert_http_error_json(
            Request(
                f"{self.base_url}/api/backups/inspect",
                data=b"not a backup",
                headers={"Content-Type": "application/octet-stream"},
                method="POST",
            ),
            400,
        )
        self.assertEqual(invalid["code"], "invalid_backup")

        zip_slip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_slip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", b"{}")
            archive.writestr("../projects.json", b"{}")
        slip = self.assert_http_error_json(
            Request(
                f"{self.base_url}/api/backups/stage-restore?conflict=rename",
                data=zip_slip_buffer.getvalue(),
                headers={"Content-Type": "application/zip"},
                method="POST",
            ),
            400,
        )
        self.assertEqual(slip["code"], "invalid_backup")

        media = self.assert_http_error_json(
            Request(
                f"{self.base_url}/api/backups/inspect",
                data=b"x",
                headers={"Content-Type": "text/plain"},
                method="POST",
            ),
            415,
        )
        self.assertIn("error", media)
        duplicate_query = self.assert_http_error_json(
            Request(
                f"{self.base_url}/api/backups/export?scope=full&scope=project"
            ),
            400,
        )
        self.assertIn("error", duplicate_query)

        self.assertEqual(hashlib.sha256(self.api_db_path.read_bytes()).hexdigest(), before)
        after_staging = set(recovery.glob("restore-preview-*.db")) if recovery.exists() else set()
        self.assertEqual(after_staging, before_staging)
        self.assertEqual(db.get_project(project["id"], self.api_db_path)["name"], "Protected")

    def test_edit_preview_hash_survives_http_browser_wire_format(self):
        block_ids = [
            "quality", "artist", "subject", "appearance", "outfit",
            "expression", "pose", "interaction", "scene", "composition",
            "lighting", "effects", "negative",
        ]

        def post(path, payload):
            with urlopen(
                Request(
                    f"{self.base_url}{path}",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
            ) as response:
                return json.loads(response.read().decode("utf-8"))["item"]

        resolved = post(
            "/api/recipe/resolve",
            {
                "blocks": [
                    {
                        "id": block_id,
                        "en": "blue dress" if block_id == "outfit" else "",
                        "zh": "蓝色连衣裙" if block_id == "outfit" else "",
                    }
                    for block_id in block_ids
                ],
                "prompts": {
                    "positiveEn": "masterpiece, best quality, score_7, blue dress",
                    "positiveZh": "杰作，最佳质量，蓝色连衣裙",
                    "negativeEn": "low quality",
                    "negativeZh": "低质量",
                },
            },
        )
        preview = post(
            "/api/text/edit-preview",
            {
                "instruction": "只把服装换成红色连衣裙",
                "recipe": resolved["recipe"],
                "baseRecipeHash": resolved["recipeHash"],
            },
        )
        self.assertIs(type(preview["parse"]["confidence"]), int)
        # Browser JSON.parse/JSON.stringify cannot preserve a distinction
        # between 1.0 and 1, so the wire contract intentionally uses integers.
        transported = json.loads(json.dumps(preview, ensure_ascii=False))
        applied = post(
            "/api/text/edit-apply",
            {
                "recipe": resolved["recipe"],
                "preview": transported,
                "parentVersion": 1,
                "newVersion": 2,
            },
        )
        outfit = next(
            item for item in applied["workbenchBlocks"] if item["id"] == "outfit"
        )
        self.assertEqual(outfit["en"], "red dress")


if __name__ == "__main__":
    unittest.main()
