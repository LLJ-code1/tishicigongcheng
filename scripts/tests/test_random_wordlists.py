import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.build_random_wordlists import (
    CATEGORY_SPECS,
    DEFAULT_OUTPUT,
    CatalogValidationError,
    build_catalog,
    catalog_content_sha256,
    generate_catalog,
    render_catalog,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "词库原稿"
SCRIPT = ROOT / "scripts" / "build_random_wordlists.py"


def write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


class RandomWordlistBuildTests(unittest.TestCase):
    def copy_source(self, parent: Path) -> Path:
        destination = parent / "wordlists"
        shutil.copytree(SOURCE, destination)
        return destination

    def test_real_sources_build_fixed_review_only_catalog(self):
        catalog = build_catalog(SOURCE)

        self.assertEqual(catalog["schemaVersion"], 1)
        self.assertEqual(catalog["samplerVersion"], "sha256-counter-v1")
        self.assertEqual(catalog["mappingVersion"], "thirteen-block-v1")
        self.assertEqual(catalog["profile"], "adult-character-v1")
        self.assertFalse(catalog["runtimeReady"])
        self.assertTrue(catalog["semanticReviewRequired"])
        self.assertEqual(catalog["categoryCount"], 9)
        self.assertEqual(
            catalog["entryCount"],
            sum(
                len((SOURCE / spec["sourceFile"]).read_text(encoding="utf-8").splitlines())
                for spec in CATEGORY_SPECS
            ),
        )
        self.assertRegex(catalog["normalizationUnicodeVersion"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(len(catalog["categories"]), len(CATEGORY_SPECS))
        self.assertEqual(
            [len(category["entries"]) for category in catalog["categories"]],
            [
                len((SOURCE / spec["sourceFile"]).read_text(encoding="utf-8").splitlines())
                for spec in CATEGORY_SPECS
            ],
        )
        self.assertTrue(
            all(category["drawRule"] for category in catalog["categories"])
        )
        self.assertTrue(
            all(category["primaryBlock"] for category in catalog["categories"])
        )
        self.assertTrue(
            all(category["allowedBlocks"] for category in catalog["categories"])
        )
        self.assertEqual(
            catalog["contentSha256"], catalog_content_sha256(catalog)
        )
        self.assertEqual(
            catalog["version"], f"v1-{catalog['contentSha256'][:16]}"
        )

    def test_rejects_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self.copy_source(Path(temp_dir))
            (source / "blocks" / "scene" / "theme_mood.txt").write_bytes(b"\xff\xfeinvalid")

            with self.assertRaisesRegex(CatalogValidationError, "strict UTF-8"):
                build_catalog(source)

    def test_source_count_changes_build_and_check_detects_unpublished_catalog(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self.copy_source(Path(temp_dir))
            path = source / "blocks" / "scene" / "scene_environment.txt"
            lines = path.read_text(encoding="utf-8").splitlines()
            baseline = Path(temp_dir) / "catalog.json"
            baseline.write_text(render_catalog(build_catalog(source)), encoding="utf-8")
            lines.append("new deterministic scene")
            write_lines(path, lines)

            self.assertEqual(build_catalog(source)["entryCount"], 472)
            with self.assertRaisesRegex(CatalogValidationError, "stale"):
                generate_catalog(source, baseline, check=True)

    def test_rejects_normalized_duplicate_across_categories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self.copy_source(Path(temp_dir))
            scene_path = source / "blocks" / "scene" / "scene_environment.txt"
            duplicate = scene_path.read_text(encoding="utf-8").splitlines()[0]
            theme_path = source / "blocks" / "scene" / "theme_mood.txt"
            theme_lines = theme_path.read_text(encoding="utf-8").splitlines()
            theme_lines[0] = duplicate.upper()
            write_lines(theme_path, theme_lines)

            with self.assertRaisesRegex(CatalogValidationError, "normalized duplicate"):
                build_catalog(source)

    def test_rejects_empty_or_surrounding_whitespace(self):
        for replacement, message in (("", "must not be empty"), (" padded ", "whitespace")):
            with self.subTest(replacement=repr(replacement)):
                with tempfile.TemporaryDirectory() as temp_dir:
                    source = self.copy_source(Path(temp_dir))
                    path = source / "blocks" / "scene" / "theme_mood.txt"
                    lines = path.read_text(encoding="utf-8").splitlines()
                    lines[0] = replacement
                    write_lines(path, lines)

                    with self.assertRaisesRegex(CatalogValidationError, message):
                        build_catalog(source)

    def test_rejects_prompt_separator_pollution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self.copy_source(Path(temp_dir))
            path = source / "blocks" / "scene" / "theme_mood.txt"
            lines = path.read_text(encoding="utf-8").splitlines()
            lines[0] = f"{lines[0]}, extra item"
            write_lines(path, lines)

            with self.assertRaisesRegex(CatalogValidationError, "forbidden prompt separator"):
                build_catalog(source)

    def test_rejects_control_characters_inside_an_entry(self):
        for control_character in (
            "\t",
            "\u0085",
            "\u200b",
            "\u202e",
            "\ufeff",
            "\u00ad",
            "\ue000",
            "\ufe0f",
            "\u034f",
        ):
            with self.subTest(codepoint=f"U+{ord(control_character):04X}"):
                with tempfile.TemporaryDirectory() as temp_dir:
                    source = self.copy_source(Path(temp_dir))
                    path = source / "blocks" / "scene" / "theme_mood.txt"
                    lines = path.read_text(encoding="utf-8").splitlines()
                    lines[0] = f"{lines[0]}{control_character}fragment"
                    write_lines(path, lines)

                    with self.assertRaisesRegex(
                        CatalogValidationError,
                        "forbidden Unicode character|printable ASCII",
                    ):
                        build_catalog(source)

    def test_rejects_oversized_source_and_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self.copy_source(Path(temp_dir))
            path = source / "blocks" / "scene" / "theme_mood.txt"
            lines = path.read_text(encoding="utf-8").splitlines()
            lines[0] = "x" * 513
            write_lines(path, lines)
            with self.assertRaisesRegex(CatalogValidationError, "entry limit"):
                build_catalog(source)

        with tempfile.TemporaryDirectory() as temp_dir:
            source = self.copy_source(Path(temp_dir))
            path = source / "blocks" / "scene" / "theme_mood.txt"
            path.write_bytes(b"x" * (128 * 1024 + 1))
            with self.assertRaisesRegex(CatalogValidationError, "source limit"):
                build_catalog(source)

    def test_entry_id_is_stable_when_source_line_moves(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self.copy_source(Path(temp_dir))
            before = build_catalog(source)
            before_entries = before["categories"][0]["entries"]
            target_text = before_entries[0]["text"]
            before_entry = next(
                item for item in before_entries if item["text"] == target_text
            )

            path = source / "blocks" / "scene" / "theme_mood.txt"
            lines = path.read_text(encoding="utf-8").splitlines()
            lines[0], lines[1] = lines[1], lines[0]
            write_lines(path, lines)
            after_entries = build_catalog(source)["categories"][0]["entries"]
            after_entry = next(
                item for item in after_entries if item["text"] == target_text
            )

        self.assertEqual(before_entry["entryId"], after_entry["entryId"])
        self.assertNotEqual(before_entry["sourceLine"], after_entry["sourceLine"])

    def test_rendering_is_byte_deterministic(self):
        first = render_catalog(build_catalog(SOURCE)).encode("utf-8")
        second = render_catalog(build_catalog(SOURCE)).encode("utf-8")

        self.assertEqual(first, second)

    def test_cli_check_is_read_only_and_detects_stale_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self.copy_source(root)
            output = root / "generated" / "v1.json"
            command = [
                sys.executable,
                str(SCRIPT),
                "--source-dir",
                str(source),
                "--output",
                str(output),
            ]
            subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
            subprocess.run(
                [*command, "--check"],
                cwd=ROOT,
                check=True,
                capture_output=True,
            )

            stale = output.read_bytes() + b"stale\n"
            output.write_bytes(stale)
            result = subprocess.run(
                [*command, "--check"],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("stale", result.stderr)
            self.assertEqual(output.read_bytes(), stale)

    def test_cli_check_accepts_windows_crlf_checkout_without_writing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self.copy_source(root)
            output = root / "generated" / "v1.json"
            command = [
                sys.executable,
                str(SCRIPT),
                "--source-dir",
                str(source),
                "--output",
                str(output),
            ]
            subprocess.run(command, cwd=ROOT, check=True, capture_output=True)

            crlf_output = output.read_bytes().replace(b"\n", b"\r\n")
            self.assertIn(b"\r\n", crlf_output)
            output.write_bytes(crlf_output)

            subprocess.run(
                [*command, "--check"],
                cwd=ROOT,
                check=True,
                capture_output=True,
            )
            self.assertEqual(output.read_bytes(), crlf_output)

    def test_committed_catalog_is_current(self):
        subprocess.run(
            [sys.executable, str(SCRIPT), "--output", str(DEFAULT_OUTPUT), "--check"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
