import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import prompt_diagnostics as diagnostics  # noqa: E402


class PromptDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        diagnostics._load_local_tokenizer.cache_clear()

    def tearDown(self):
        diagnostics._load_local_tokenizer.cache_clear()

    def test_does_not_claim_a_token_count_without_a_configured_tokenizer(self):
        with patch.dict(os.environ, {}, clear=True):
            count, status = diagnostics.real_token_count("1girl, library")

        self.assertIsNone(count)
        self.assertEqual(status, "unavailable_without_model_tokenizer")

    def test_uses_the_configured_local_tokenizer_exactly(self):
        class Tokenizer:
            def encode(self, prompt, *, add_special_tokens):
                self.prompt = prompt
                self.add_special_tokens = add_special_tokens
                return [11, 12, 13]

        tokenizer = Tokenizer()
        with patch.dict(os.environ, {"PROMPT_STUDIO_TOKENIZER_PATH": "C:/models/tokenizer"}), patch.object(
            diagnostics, "_load_local_tokenizer", return_value=tokenizer
        ) as loader:
            count, status = diagnostics.real_token_count("1girl, library")

        self.assertEqual((count, status), (3, "ready"))
        loader.assert_called_once_with("C:/models/tokenizer")
        self.assertFalse(tokenizer.add_special_tokens)

    def test_unavailable_local_tokenizer_never_falls_back_to_tag_count(self):
        with patch.dict(os.environ, {"PROMPT_STUDIO_TOKENIZER_PATH": "C:/missing"}), patch.object(
            diagnostics, "_load_local_tokenizer", side_effect=OSError("missing")
        ):
            result = diagnostics.prompt_diagnostics(
                {"prompts": {"positiveEn": "1girl, library"}, "blocks": []}
            )

        self.assertIsNone(result["tokenCount"])
        self.assertEqual(result["tokenCountStatus"], "configured_tokenizer_unavailable")
        self.assertEqual(result["tagCount"], 2)

    def test_subject_position_uses_a_unique_subject_anchor_and_local_tokenizer(self):
        class Tokenizer:
            def encode(self, prompt, *, add_special_tokens):
                self.prompts = getattr(self, "prompts", []) + [prompt]
                return list(range(len(prompt.split())))

        tokenizer = Tokenizer()
        recipe = {
            "prompts": {"positiveEn": "masterpiece, 1girl, library"},
            "blocks": [{"id": "subject", "en": "1girl"}],
        }
        with patch.dict(os.environ, {"PROMPT_STUDIO_TOKENIZER_PATH": "C:/models/tokenizer"}), patch.object(
            diagnostics, "_load_local_tokenizer", return_value=tokenizer
        ):
            result = diagnostics.prompt_diagnostics(recipe)

        self.assertEqual(result["subjectPosition"], {
            "status": "ready",
            "tokenStart": 1,
            "anchorTag": "1girl",
        })
        self.assertEqual(tokenizer.prompts, ["masterpiece, 1girl, library", "masterpiece, "])

    def test_subject_position_refuses_an_ambiguous_anchor(self):
        result = diagnostics.prompt_diagnostics(
            {
                "prompts": {"positiveEn": "1girl, library, 1girl"},
                "blocks": [{"id": "subject", "en": "1girl"}],
            }
        )

        self.assertEqual(result["subjectPosition"]["status"], "subject_anchor_not_unique")
        self.assertIsNone(result["subjectPosition"]["tokenStart"])
