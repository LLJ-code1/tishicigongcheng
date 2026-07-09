from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


DIRECTIVE_PATTERN = re.compile(
    r"(你是|你的任务|请(?:根据|分析|将|生成|输出|结合)|将以下|按照以下|"
    r"要求如下|输出要求|生成要求|必须|只(?:输出|生成)|不要(?:输出|生成)|"
    r"提示词(?:生成|优化|反推|扩写|提纯|标题)|"
    r"analyze the image|your task|generate (?:a|an)|output only|"
    r"do not output|you are (?:a|an))",
    re.IGNORECASE,
)

ADULT_TITLE_PATTERN = re.compile(
    r"(NSFW|R18|成人|擦边|explicit|可以NSFW)", re.IGNORECASE
)
SAFE_TITLE_PATTERN = re.compile(r"(SAFE|SFW)", re.IGNORECASE)
MIXED_SCALE_PATTERN = re.compile(
    r"(safe.{0,20}sensitive.{0,20}nsfw.{0,20}explicit|"
    r"软色情|性暗示|裸露|性行为|限制级|成人内容|R18)",
    re.IGNORECASE | re.DOTALL,
)

PROMPT_ASSISTANT_ZIP = "ComfyUI-Prompt-Assistant-main.zip"
PROMPT_ASSISTANT_SYSTEM_ENTRY = (
    "ComfyUI-Prompt-Assistant-main/config/system_prompts_template.json"
)
PROMPT_ASSISTANT_KONTEXT_ENTRY = (
    "ComfyUI-Prompt-Assistant-main/config/kontext_presets_template.json"
)
DANBOORU_ZIP = "ComfyUI-Danbooru-Gallery-main.zip"
DANBOORU_SWAP_ENTRY = (
    "ComfyUI-Danbooru-Gallery-main/py/character_feature_swap/"
    "character_feature_swap.py"
)
PROMPT_GALLERY_ZIP = "ComfyUI-Prompt-Gallery-master.zip"
PROMPT_GALLERY_FILTER_ENTRY = (
    "ComfyUI-Prompt-Gallery-master/web/components/CustomFilterEditDialog.js"
)


@dataclass(frozen=True)
class PromptArtifact:
    text: str
    source_kind: str
    source_path: str
    source_title: str
    group: str
    source_detail: str = ""

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def iter_nested(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from iter_nested(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_nested(child)


def iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from iter_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_strings(child)


def is_prompt_framework(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 80:
        return False
    if stripped.startswith('[{"id"') or stripped.startswith("[{'id'"):
        return False
    if re.match(r"^https?://", stripped, re.IGNORECASE):
        return False
    if "/api/view?filename=" in stripped:
        return False
    return bool(DIRECTIVE_PATTERN.search(stripped))


def relative_source_path(path: Path, source_root: Path) -> str:
    try:
        return str(path.relative_to(source_root))
    except ValueError:
        return str(path)


def extract_workflow_prompts(
    workflow_path: Path, source_root: Path
) -> list[PromptArtifact]:
    data = json.loads(workflow_path.read_text(encoding="utf-8-sig"))
    artifacts: list[PromptArtifact] = []

    for value in iter_nested(data):
        if not isinstance(value, dict):
            continue
        if "widgets_values" not in value:
            continue

        node_type = str(value.get("type", ""))
        title = str(
            value.get("title")
            or value.get("properties", {}).get("Node name for S&R")
            or node_type
            or "未命名节点"
        )
        for text in iter_strings(value.get("widgets_values")):
            if not is_prompt_framework(text):
                continue
            artifacts.append(
                PromptArtifact(
                    text=text,
                    source_kind="workflow_json",
                    source_path=relative_source_path(workflow_path, source_root),
                    source_title=title,
                    source_detail=node_type,
                    group="01_Anima工作流",
                )
            )

    return artifacts


def read_zip_text(zip_path: Path, entry_name: str) -> str:
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        resolved_name = entry_name
        if resolved_name not in names:
            suffix = "/".join(entry_name.split("/")[1:])
            matches = [name for name in names if name.endswith(suffix)]
            if len(matches) != 1:
                raise KeyError(
                    f"ZIP entry not found or ambiguous: {entry_name}"
                )
            resolved_name = matches[0]
        with archive.open(resolved_name) as entry:
            return entry.read().decode("utf-8-sig")


def extract_prompt_assistant_system_prompts(
    zip_path: Path,
) -> list[PromptArtifact]:
    raw = read_zip_text(zip_path, PROMPT_ASSISTANT_SYSTEM_ENTRY)
    data = json.loads(raw)
    artifacts: list[PromptArtifact] = []

    for section_name, section in data.items():
        if section_name.startswith("__") or not isinstance(section, dict):
            continue
        for key, item in section.items():
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, str) or not content:
                continue
            artifacts.append(
                PromptArtifact(
                    text=content,
                    source_kind="plugin_json",
                    source_path=(
                        f"{zip_path.name}!/{PROMPT_ASSISTANT_SYSTEM_ENTRY}"
                    ),
                    source_title=str(item.get("name") or key),
                    source_detail=f"{section_name}/{key}",
                    group="02_Prompt Assistant系统提示词",
                )
            )

    return artifacts


def extract_prompt_assistant_kontext_prompts(
    zip_path: Path,
) -> list[PromptArtifact]:
    raw = read_zip_text(zip_path, PROMPT_ASSISTANT_KONTEXT_ENTRY)
    data = json.loads(raw)
    source_path = f"{zip_path.name}!/{PROMPT_ASSISTANT_KONTEXT_ENTRY}"
    artifacts: list[PromptArtifact] = []

    for key, title in (
        ("kontext_prefix", "Kontext公共前缀"),
        ("kontext_suffix", "Kontext公共后缀"),
    ):
        content = data.get(key)
        if isinstance(content, str) and content:
            artifacts.append(
                PromptArtifact(
                    text=content,
                    source_kind="plugin_json_fragment",
                    source_path=source_path,
                    source_title=title,
                    source_detail=key,
                    group="03_Kontext图像编辑预设",
                )
            )

    presets = data.get("kontext_presets", {})
    if isinstance(presets, dict):
        for key, item in presets.items():
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, str) or not content:
                continue
            artifacts.append(
                PromptArtifact(
                    text=content,
                    source_kind="plugin_json",
                    source_path=source_path,
                    source_title=str(item.get("name") or key),
                    source_detail=key,
                    group="03_Kontext图像编辑预设",
                )
            )

    return artifacts


def extract_python_dict_string(
    source: str, key_name: str
) -> str | None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == key_name
            ):
                try:
                    result = ast.literal_eval(value)
                except (ValueError, TypeError):
                    continue
                if isinstance(result, str):
                    return result
    return None


def extract_danbooru_character_swap_prompt(
    zip_path: Path,
) -> list[PromptArtifact]:
    source = read_zip_text(zip_path, DANBOORU_SWAP_ENTRY)
    prompt = extract_python_dict_string(source, "custom_prompt")
    if not prompt:
        return []
    return [
        PromptArtifact(
            text=prompt,
            source_kind="plugin_python",
            source_path=f"{zip_path.name}!/{DANBOORU_SWAP_ENTRY}",
            source_title="角色特征替换",
            source_detail="custom_prompt",
            group="05_其他插件工具",
        )
    ]


def extract_javascript_template_literal(
    source: str, constant_name: str
) -> str | None:
    pattern = re.compile(
        rf"const\s+{re.escape(constant_name)}\s*=\s*`(.*?)`\s*;",
        re.DOTALL,
    )
    match = pattern.search(source)
    if not match:
        return None
    return match.group(1).replace(r"\`", "`").replace(r"\\", "\\")


def extract_prompt_gallery_filter_prompt(
    zip_path: Path,
) -> list[PromptArtifact]:
    source = read_zip_text(zip_path, PROMPT_GALLERY_FILTER_ENTRY)
    prompt = extract_javascript_template_literal(source, "AI_SYSTEM_PROMPT")
    if not prompt:
        return []
    return [
        PromptArtifact(
            text=prompt,
            source_kind="plugin_javascript",
            source_path=f"{zip_path.name}!/{PROMPT_GALLERY_FILTER_ENTRY}",
            source_title="图库筛查项代码生成器",
            source_detail="AI_SYSTEM_PROMPT",
            group="05_其他插件工具",
        )
    ]


def extract_standalone_rules(
    source_root: Path,
) -> list[PromptArtifact]:
    rules_dir = source_root / "提示词规则"
    artifacts: list[PromptArtifact] = []
    if not rules_dir.exists():
        return artifacts

    for path in sorted(rules_dir.glob("*.txt")):
        text = path.read_text(encoding="utf-8-sig")
        if not text:
            continue
        artifacts.append(
            PromptArtifact(
                text=text,
                source_kind="standalone_text",
                source_path=relative_source_path(path, source_root),
                source_title=path.stem,
                group="04_专项规则",
            )
        )
    return artifacts


def collect_artifacts(source_root: Path) -> list[PromptArtifact]:
    artifacts: list[PromptArtifact] = []
    workflow_root = source_root / "Anima工作流"
    if workflow_root.exists():
        for path in sorted(workflow_root.rglob("*.json")):
            artifacts.extend(extract_workflow_prompts(path, source_root))

    artifacts.extend(extract_standalone_rules(source_root))

    plugin_root = source_root / "工作流相关插件"
    prompt_assistant = plugin_root / PROMPT_ASSISTANT_ZIP
    if prompt_assistant.exists():
        artifacts.extend(
            extract_prompt_assistant_system_prompts(prompt_assistant)
        )
        artifacts.extend(
            extract_prompt_assistant_kontext_prompts(prompt_assistant)
        )

    danbooru = plugin_root / DANBOORU_ZIP
    if danbooru.exists():
        artifacts.extend(extract_danbooru_character_swap_prompt(danbooru))

    prompt_gallery = plugin_root / PROMPT_GALLERY_ZIP
    if prompt_gallery.exists():
        artifacts.extend(
            extract_prompt_gallery_filter_prompt(prompt_gallery)
        )

    return artifacts


def classify_safety(artifact: PromptArtifact) -> str:
    title = artifact.source_title
    if SAFE_TITLE_PATTERN.search(title) and not ADULT_TITLE_PATTERN.search(title):
        return "普通"
    if ADULT_TITLE_PATTERN.search(title):
        return "成人向或混合尺度"
    if MIXED_SCALE_PATTERN.search(artifact.text):
        return "成人向或混合尺度"
    return "普通"


def safe_name(value: str, limit: int = 72) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    return (cleaned or "未命名")[:limit]


def unique_records(
    artifacts: Iterable[PromptArtifact],
) -> list[tuple[PromptArtifact, list[PromptArtifact]]]:
    grouped: dict[str, list[PromptArtifact]] = defaultdict(list)
    for artifact in artifacts:
        grouped[artifact.sha256].append(artifact)

    records = []
    for items in grouped.values():
        items.sort(
            key=lambda item: (
                item.group,
                item.source_path,
                item.source_title,
                item.source_detail,
            )
        )
        records.append((items[0], items))
    records.sort(
        key=lambda record: (
            record[0].group,
            classify_safety(record[0]),
            record[0].source_title,
            record[0].sha256,
        )
    )
    return records


def build_archive(
    artifacts: list[PromptArtifact], output_root: Path
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    reading_root = output_root / "去重阅读版"
    source_root = output_root / "按来源完整副本"
    reading_root.mkdir(parents=True, exist_ok=True)
    source_root.mkdir(parents=True, exist_ok=True)

    records = unique_records(artifacts)
    manifest_items: list[dict[str, Any]] = []
    source_occurrences: list[dict[str, Any]] = []
    group_counters: dict[tuple[str, str], int] = defaultdict(int)

    for representative, sources in records:
        safety = classify_safety(representative)
        counter_key = (safety, representative.group)
        group_counters[counter_key] += 1
        number = group_counters[counter_key]
        filename = (
            f"{number:03d}_{safe_name(representative.source_title)}_"
            f"{representative.sha256[:8]}.txt"
        )
        reading_path = (
            reading_root / safety / representative.group / filename
        )
        reading_path.parent.mkdir(parents=True, exist_ok=True)
        reading_path.write_text(representative.text, encoding="utf-8")

        source_entries = []
        for occurrence_number, artifact in enumerate(sources, start=1):
            source_folder = safe_name(
                Path(artifact.source_path.split("!/", 1)[0]).stem
            )
            source_filename = (
                f"{occurrence_number:03d}_{safe_name(artifact.source_title)}_"
                f"{artifact.sha256[:8]}.txt"
            )
            source_path = (
                source_root
                / artifact.group
                / source_folder
                / source_filename
            )
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(artifact.text, encoding="utf-8")

            entry = {
                **asdict(artifact),
                "sha256": artifact.sha256,
                "archive_file": str(source_path.relative_to(output_root)),
            }
            source_entries.append(entry)
            source_occurrences.append(entry)

        manifest_items.append(
            {
                "sha256": representative.sha256,
                "safety": safety,
                "group": representative.group,
                "title": representative.source_title,
                "reading_file": str(reading_path.relative_to(output_root)),
                "source_count": len(source_entries),
                "sources": source_entries,
            }
        )

    safety_counts: dict[str, int] = defaultdict(int)
    group_counts: dict[str, int] = defaultdict(int)
    for item in manifest_items:
        safety_counts[item["safety"]] += 1
        group_counts[item["group"]] += 1

    manifest = {
        "summary": {
            "unique_prompts": len(manifest_items),
            "source_occurrences": len(artifacts),
            "safety_counts": dict(sorted(safety_counts.items())),
            "group_counts": dict(sorted(group_counts.items())),
        },
        "items": manifest_items,
        "source_occurrences": source_occurrences,
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_readme(output_root, manifest)
    return manifest


def write_readme(output_root: Path, manifest: dict[str, Any]) -> None:
    summary = manifest["summary"]
    group_lines = "\n".join(
        f"- `{group}`：{count} 份不重复原文"
        for group, count in summary["group_counts"].items()
    )
    safety_lines = "\n".join(
        f"- `{safety}`：{count} 份不重复原文"
        for safety, count in summary["safety_counts"].items()
    )
    text = f"""# 提示词原文归档

本目录由 `scripts/extract_prompt_frameworks.py` 从本地素材自动提取。

## 数量

- 不重复提示词框架：{summary["unique_prompts"]} 份
- 按来源统计的原文实例：{summary["source_occurrences"]} 份

## 按来源类型

{group_lines}

## 按内容尺度

{safety_lines}

## 目录说明

- `去重阅读版`：适合逐个查看，相同原文只保留一次。
- `按来源完整副本`：保留每一次出现的位置，重复内容也不会省略。
- `manifest.json`：记录 SHA-256、来源文件、节点标题和重复关系。

## 本次未收录

成品绘图提示词、质量词、负面词、角色词、画师词、LoRA 配方、
ComfyUI 采样参数和节点连线不属于本次“LLM 提示词框架”整理范围。

## 原文说明

正文未润色、未翻译、未纠错。JSON 和代码中的转义字符按实际运行文本还原。
成人向或支持混合尺度生成的框架已独立放置。
"""
    (output_root / "README.md").write_text(text, encoding="utf-8")


def verify_archive(output_root: Path, manifest: dict[str, Any]) -> None:
    for item in manifest["items"]:
        reading_path = output_root / item["reading_file"]
        text = reading_path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != item["sha256"]:
            raise RuntimeError(f"阅读版哈希校验失败: {reading_path}")

        for source in item["sources"]:
            source_path = output_root / source["archive_file"]
            source_text = source_path.read_text(encoding="utf-8")
            source_digest = hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest()
            if source_digest != source["sha256"]:
                raise RuntimeError(f"来源副本哈希校验失败: {source_path}")


def clean_output(output_root: Path, workspace_root: Path) -> None:
    resolved_output = output_root.resolve()
    resolved_workspace = workspace_root.resolve()
    if resolved_output.parent != resolved_workspace:
        raise RuntimeError("输出目录必须直接位于项目根目录下")
    if resolved_output.name != "提示词原文归档":
        raise RuntimeError("拒绝清理非预期输出目录")
    if resolved_output.exists():
        shutil.rmtree(resolved_output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="提取本地 LLM 提示词框架原文")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path.cwd() / "提示词原文归档"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_root = args.source.resolve()
    output_root = args.output.resolve()
    if not source_root.exists():
        raise SystemExit(f"来源目录不存在: {source_root}")

    clean_output(output_root, Path.cwd())
    artifacts = collect_artifacts(source_root)
    manifest = build_archive(artifacts, output_root)
    verify_archive(output_root, manifest)
    print(
        json.dumps(
            {
                "output": str(output_root),
                **manifest["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
