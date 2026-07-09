import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.extract_prompt_frameworks import (
    PromptArtifact,
    build_archive,
    extract_prompt_assistant_system_prompts,
    extract_workflow_prompts,
)


class ExtractPromptFrameworksTests(unittest.TestCase):
    def test_workflow_prompts_are_deduplicated_without_losing_sources(self):
        prompt = (
            "你是一位专业的提示词生成器。你的任务是根据用户输入扩写画面，"
            "必须完整保留主体、动作、服装、场景、构图、光影和色彩信息，"
            "只输出最终英文提示词，不要解释生成过程，也不要输出 Markdown。"
        )
        workflow = {
            "nodes": [
                {
                    "type": "ZML_TextInput",
                    "title": "扩写一",
                    "widgets_values": [prompt],
                },
                {
                    "type": "ZML_TextInput",
                    "title": "扩写二",
                    "widgets_values": [prompt],
                },
                {
                    "type": "CLIPTextEncode",
                    "title": "成品提示词",
                    "widgets_values": [
                        "masterpiece, best quality, 1girl, rain, city street"
                    ],
                },
            ]
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "workflow.json"
            source.write_text(
                json.dumps(workflow, ensure_ascii=False), encoding="utf-8"
            )
            artifacts = extract_workflow_prompts(source, Path(temp_dir))

        self.assertEqual(2, len(artifacts))
        self.assertEqual(prompt, artifacts[0].text)
        self.assertEqual(prompt, artifacts[1].text)
        self.assertNotEqual(artifacts[0].source_title, artifacts[1].source_title)

    def test_archive_preserves_exact_text_and_separates_adult_content(self):
        normal = PromptArtifact(
            text="You are a prompt editor.\nOutput only the revised prompt.",
            source_kind="test",
            source_path="normal.json",
            source_title="Normal",
            group="测试",
        )
        adult = PromptArtifact(
            text="你是一位 NSFW 提示词生成器，必须输出 explicit 内容。",
            source_kind="test",
            source_path="adult.json",
            source_title="随机NSFW提示词",
            group="测试",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "archive"
            manifest = build_archive([normal, normal, adult], output)
            text_files = list((output / "去重阅读版").rglob("*.txt"))

            self.assertEqual(2, manifest["summary"]["unique_prompts"])
            self.assertEqual(3, manifest["summary"]["source_occurrences"])
            self.assertEqual(2, len(text_files))

            contents = {path.read_text(encoding="utf-8") for path in text_files}
            self.assertEqual({normal.text, adult.text}, contents)
            adult_files = list(
                (output / "去重阅读版" / "成人向或混合尺度").rglob("*.txt")
            )
            self.assertEqual(1, len(adult_files))

    def test_prompt_assistant_system_prompts_are_extracted_from_zip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "plugin.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(
                    "renamed-plugin/config/system_prompts_template.json",
                    json.dumps(
                        {
                            "vision_prompts": {
                                "one": {
                                    "name": "视觉分析",
                                    "role": "system",
                                    "content": "You are a visual analyst. Your task is to describe the image.",
                                }
                            }
                        }
                    ),
                )
            artifacts = extract_prompt_assistant_system_prompts(archive)

        self.assertEqual(1, len(artifacts))
        self.assertEqual("视觉分析", artifacts[0].source_title)
        self.assertEqual(
            "You are a visual analyst. Your task is to describe the image.",
            artifacts[0].text,
        )


if __name__ == "__main__":
    unittest.main()
