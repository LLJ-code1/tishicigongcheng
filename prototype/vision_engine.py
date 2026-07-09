"""Real local multi-model image-analysis pipeline for Prompt Studio."""

from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from prompt_engine import (
    PromptEngineError,
    decompose_text_prompt,
    translate_pending_items,
)
from vision_worker_joycaption import resolve_joycaption_model
from vision_worker_qwenvl import qwen_model_status


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
DEFAULT_COMFY_ROOT = Path(
    r"H:\conmfui\ComfyUI-aki(1)\ComfyUI-aki-v3\ComfyUI-aki-v3"
)
COMFY_MODELS = DEFAULT_COMFY_ROOT / "ComfyUI" / "models"
VISION_MODELS = Path(
    os.environ.get(
        "PROMPT_STUDIO_VISION_MODELS",
        str(PROJECT_ROOT / "models" / "vision"),
    )
)
VISION_PYTHON = Path(
    os.environ.get(
        "PROMPT_STUDIO_VISION_PYTHON",
        str(DEFAULT_COMFY_ROOT / "python" / "python.exe"),
    )
)
FLORENCE_MODEL = Path(
    os.environ.get(
        "PROMPT_STUDIO_FLORENCE_MODEL",
        str(COMFY_MODELS / "LLM" / "Florence-2-base-PromptGen-v2.0"),
    )
)
WD14_DIR = VISION_MODELS / "wd14" / "wd-v1-4-convnextv2-tagger-v2"
WD14_MODEL = WD14_DIR / "model.onnx"
WD14_TAGS = WD14_DIR / "selected_tags.csv"
JOYCAPTION_CANDIDATES = [
    COMFY_MODELS / "LLM" / "llama-joycaption-beta-one-hf-llava-FP8-Dynamic",
    COMFY_MODELS / "LLM" / "llama-joycaption-alpha-two",
]
JOYCAPTION_GGUF_DIR = VISION_MODELS / "joycaption"
JOYCAPTION_GGUF_MODEL = (
    JOYCAPTION_GGUF_DIR / "Llama-Joycaption-Beta-One-Hf-Llava-Q4_K.gguf"
)
JOYCAPTION_GGUF_MMPROJ = (
    JOYCAPTION_GGUF_DIR
    / "llama-joycaption-beta-one-llava-mmproj-model-f16.gguf"
)
QWENVL_DIR = VISION_MODELS / "qwenvl" / "Qwen3-VL-4B-Instruct-GGUF"
QWENVL_MODEL = QWENVL_DIR / "Qwen3VL-4B-Instruct-Q4_K_M.gguf"
QWENVL_MMPROJ = QWENVL_DIR / "mmproj-Qwen3VL-4B-Instruct-F16.gguf"
LLAMA_SERVER = Path(
    os.environ.get(
        "PROMPT_STUDIO_LLAMA_SERVER",
        r"H:\AI\apps\llama.cpp\b9442-cuda12.4\llama-server.exe",
    )
)
ANALYZER_ORDER = ("wd14", "florence", "joycaption", "qwenvl")
ANALYZER_LABELS = {
    "wd14": "WD14",
    "florence": "Florence",
    "joycaption": "JoyCaption",
    "qwenvl": "Qwen3-VL",
}


@dataclass(frozen=True)
class AnalyzerSpec:
    worker: Path
    timeout: float


ANALYZERS = {
    "wd14": AnalyzerSpec(ROOT / "vision_worker_wd14.py", 180),
    "florence": AnalyzerSpec(ROOT / "vision_worker.py", 300),
    "joycaption": AnalyzerSpec(ROOT / "vision_worker_joycaption.py", 600),
    "qwenvl": AnalyzerSpec(ROOT / "vision_worker_qwenvl.py", 600),
}


class VisionEngineError(RuntimeError):
    def __init__(self, message: str, code: str, status: int = 500):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def _status(installed: bool, missing: list[str], model_path: str) -> dict:
    return {
        "installed": installed,
        "label": "已安装" if installed else "未安装",
        "missing": missing,
        "modelPath": model_path,
    }


def vision_model_status() -> dict:
    florence_missing = [
        str(path)
        for path in (
            VISION_PYTHON,
            ANALYZERS["florence"].worker,
            FLORENCE_MODEL / "model.safetensors",
        )
        if not path.is_file()
    ]
    wd14_missing = [
        str(path)
        for path in (
            VISION_PYTHON,
            ANALYZERS["wd14"].worker,
            WD14_MODEL,
            WD14_TAGS,
        )
        if not path.is_file()
    ]
    joy_missing = [
        str(path)
        for path in (
            VISION_PYTHON,
            ANALYZERS["joycaption"].worker,
            LLAMA_SERVER,
            JOYCAPTION_GGUF_MODEL,
            JOYCAPTION_GGUF_MMPROJ,
        )
        if not path.is_file()
    ]
    qwen_files = qwen_model_status(QWENVL_MODEL, QWENVL_MMPROJ)
    qwen_missing = [
        str(path)
        for path in (VISION_PYTHON, ANALYZERS["qwenvl"].worker, LLAMA_SERVER)
        if not path.is_file()
    ] + qwen_files["missing"]
    return {
        "florence": _status(
            not florence_missing,
            florence_missing,
            str(FLORENCE_MODEL),
        ),
        "wd14": _status(not wd14_missing, wd14_missing, str(WD14_DIR)),
        "joycaption": _status(
            not joy_missing,
            joy_missing,
            str(JOYCAPTION_GGUF_MODEL),
        ),
        "qwenvl": _status(
            not qwen_missing,
            qwen_missing,
            str(QWENVL_DIR),
        ),
        "external": {
            "installed": False,
            "label": "尚未配置",
            "missing": ["external vision API configuration"],
            "modelPath": "",
        },
    }


def _worker_command(analyzer_id: str, image_path: str | Path) -> list[str]:
    spec = ANALYZERS[analyzer_id]
    command = [
        str(VISION_PYTHON),
        str(spec.worker),
        "--image",
        str(image_path),
    ]
    if analyzer_id == "florence":
        command += ["--model", str(FLORENCE_MODEL)]
    elif analyzer_id == "wd14":
        command += [
            "--model",
            str(WD14_MODEL),
            "--tags",
            str(WD14_TAGS),
        ]
    elif analyzer_id == "joycaption":
        command += [
            "--model",
            str(JOYCAPTION_GGUF_MODEL),
            "--mmproj",
            str(JOYCAPTION_GGUF_MMPROJ),
            "--server",
            str(LLAMA_SERVER),
        ]
    elif analyzer_id == "qwenvl":
        command += [
            "--model",
            str(QWENVL_MODEL),
            "--mmproj",
            str(QWENVL_MMPROJ),
            "--server",
            str(LLAMA_SERVER),
        ]
    return command


def run_analyzer(
    analyzer_id: str,
    image_path: str | Path,
    timeout: float | None = None,
) -> str:
    if analyzer_id not in ANALYZERS:
        raise VisionEngineError(
            f"未知识图模型：{analyzer_id}",
            "vision_model_unavailable",
            400,
        )
    status = vision_model_status()[analyzer_id]
    if not status["installed"]:
        raise VisionEngineError(
            f"{analyzer_id} 未安装：{', '.join(status['missing'])}",
            "vision_model_unavailable",
            503,
        )
    spec = ANALYZERS[analyzer_id]
    failed = []
    try:
        completed = subprocess.run(
            _worker_command(analyzer_id, image_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or spec.timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise VisionEngineError(
            f"{analyzer_id} 识图超时",
            "vision_worker_timeout",
            504,
        ) from error
    except OSError as error:
        raise VisionEngineError(
            f"{analyzer_id} 识图进程无法启动",
            "vision_worker_failed",
            503,
        ) from error
    if completed.returncode != 0:
        detail = completed.stderr.strip()[-1200:] or "未知错误"
        if "out of memory" in detail.casefold():
            detail = "CUDA 显存不足；请关闭 ComfyUI 或本地文本 LLM 后重试"
        raise VisionEngineError(
            f"{analyzer_id} 识图失败：{detail}",
            "vision_worker_failed",
            502,
        )
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        result = str(payload["result"]).strip()
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise VisionEngineError(
            f"{analyzer_id} 没有返回有效 JSON",
            "vision_invalid_output",
            502,
        ) from error
    if not result:
        raise VisionEngineError(
            f"{analyzer_id} 返回空结果",
            "vision_invalid_output",
            502,
        )
    return result


def run_florence(image_path: str | Path, timeout: float = 300) -> str:
    return run_analyzer("florence", image_path, timeout)


def _call_runner(
    runner: Callable,
    analyzer_id: str,
    image_path: Path,
    timeout: float,
) -> str:
    parameters = inspect.signature(runner).parameters.values()
    positional = [
        parameter
        for parameter in parameters
        if parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    has_varargs = any(
        parameter.kind == inspect.Parameter.VAR_POSITIONAL
        for parameter in parameters
    )
    if has_varargs or len(positional) >= 3:
        return runner(analyzer_id, image_path, timeout)
    return runner(image_path, timeout)


def _repair_translations(
    structured: dict,
    settings: dict,
    translator: Callable,
) -> None:
    untranslated = [
        {"id": block["id"], "en": block.get("en", "")}
        for block in structured.get("blocks", [])
        if block.get("en")
        and not re.search(r"[\u3400-\u9fff]", str(block.get("zh") or ""))
    ]
    if not untranslated:
        return
    failed = []
    try:
        translated = translator(untranslated, settings)
        translations = {
            item["id"]: item["zh"]
            for item in translated.get("translations", [])
        }
    except PromptEngineError as batch_error:
        translations = {}
        for item in untranslated:
            try:
                translated = translator([item], settings)
                for result in translated.get("translations", []):
                    translations[result["id"]] = result["zh"]
            except PromptEngineError:
                failed.append(item["id"])
        if not translations:
            raise batch_error
    for block in structured.get("blocks", []):
        if block["id"] in translations:
            block["zh"] = translations[block["id"]]
    positive_zh = "；".join(
        str(block.get("zh") or "").strip()
        for block in structured.get("blocks", [])
        if block.get("id") != "negative" and str(block.get("zh") or "").strip()
    )
    relation_zh = str(structured.get("relationZh") or "").strip()
    structured["positiveZh"] = "。".join(
        value for value in (positive_zh, relation_zh) if value
    )
    negative = next(
        (
            block
            for block in structured.get("blocks", [])
            if block.get("id") == "negative"
        ),
        None,
    )
    structured["negativeZh"] = (
        str(negative.get("zh") or "").strip() if negative else ""
    )
    if failed:
        raise PromptEngineError(
            f"以下结构块翻译失败：{', '.join(failed)}",
            code="invalid_model_output",
            status=502,
        )


def analyze_image_bytes(
    image_bytes: bytes,
    filename: str,
    analyzer_ids: list[str],
    settings: dict,
    runner: Callable = run_analyzer,
    decomposer: Callable = decompose_text_prompt,
    translator: Callable = translate_pending_items,
) -> dict:
    requested = {
        str(item).strip()
        for item in analyzer_ids
        if str(item).strip()
    }
    unknown = requested.difference(ANALYZERS)
    if unknown:
        raise VisionEngineError(
            f"未知识图模型：{', '.join(sorted(unknown))}",
            "vision_model_unavailable",
            400,
        )
    selected = [item for item in ANALYZER_ORDER if item in requested]
    if not selected:
        raise VisionEngineError(
            "请至少选择一个已安装的本地识图模型",
            "vision_model_unavailable",
            400,
        )
    suffix = Path(filename).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".png"
    raw_results = {}
    analyzer_states = []
    with tempfile.TemporaryDirectory(prefix="prompt-studio-vision-") as folder:
        image_path = Path(folder) / f"input{suffix}"
        image_path.write_bytes(image_bytes)
        for analyzer_id in selected:
            try:
                raw = _call_runner(
                    runner,
                    analyzer_id,
                    image_path,
                    ANALYZERS[analyzer_id].timeout,
                )
                raw_results[analyzer_id] = raw
                analyzer_states.append(
                    {"id": analyzer_id, "status": "ready", "raw": raw}
                )
            except VisionEngineError as error:
                analyzer_states.append(
                    {
                        "id": analyzer_id,
                        "status": "error",
                        "error": error.message,
                    }
                )
    if not raw_results:
        details = "；".join(
            item.get("error", "") for item in analyzer_states if item.get("error")
        )
        raise VisionEngineError(
            details or "所有识图模型均执行失败",
            "vision_worker_failed",
            502,
        )
    if len(raw_results) == 1:
        merged_prompt = next(iter(raw_results.values()))
    else:
        merged_prompt = "\n\n".join(
            raw_results[analyzer_id]
            for analyzer_id in ANALYZER_ORDER
            if analyzer_id in raw_results
        )
    structured = decomposer(merged_prompt, settings, "local", timeout=120)
    source_label = (
        " + ".join(ANALYZER_LABELS[item] for item in raw_results)
        + " + 本地 LLM"
    )
    for block in structured.get("blocks", []):
        block["source"] = source_label
    try:
        _repair_translations(structured, settings, translator)
    except PromptEngineError as error:
        checks = structured.setdefault("checks", {})
        checks["translationWarning"] = error.message
    return {
        **structured,
        "rawResults": raw_results,
        "analyzers": analyzer_states,
    }
