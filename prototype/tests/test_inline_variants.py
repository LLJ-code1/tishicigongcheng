import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inline_variants import InlineVariantError, resolve_prompt_variants  # noqa: E402


class InlineVariantTests(unittest.TestCase):
    def test_flat_literals_are_reproducible_with_explicit_selection(self):
        resolved, metadata = resolve_prompt_variants(
            {
                "positiveEn": "{red|blue} dress, {smile|frown}",
                "positiveZh": "",
                "negativeEn": "",
                "negativeZh": "",
            },
            {"positiveEn": [1, 0]},
        )

        self.assertEqual(resolved["positiveEn"], "blue dress, smile")
        field = metadata["fields"]["positiveEn"]
        self.assertEqual(field["template"], "{red|blue} dress, {smile|frown}")
        self.assertEqual(field["variants"][0]["selectedIndex"], 1)
        self.assertEqual(field["resolved"], "blue dress, smile")

    def test_nested_empty_and_out_of_range_syntax_fails_closed(self):
        for value, selections in (
            ("{a|{b|c}}", None),
            ("{a|}", None),
            ("{a|b}", [2]),
        ):
            with self.subTest(value=value):
                with self.assertRaises(InlineVariantError):
                    resolve_prompt_variants(
                        {
                            "positiveEn": value,
                            "positiveZh": "",
                            "negativeEn": "",
                            "negativeZh": "",
                        },
                        {"positiveEn": selections} if selections is not None else {},
                    )
