"""Idempotently install the local WD14 and Qwen3-VL model files."""

from __future__ import annotations

import sys
from pathlib import Path

from huggingface_hub import hf_hub_download


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "prototype"))

from vision_engine import (  # noqa: E402
    JOYCAPTION_GGUF_MMPROJ,
    JOYCAPTION_GGUF_MODEL,
    JOYCAPTION_CANDIDATES,
    QWENVL_DIR,
    QWENVL_MMPROJ,
    QWENVL_MODEL,
    WD14_DIR,
    WD14_MODEL,
    WD14_TAGS,
)
from vision_worker_joycaption import resolve_joycaption_model  # noqa: E402


def download(repo_id: str, filename: str, destination: Path) -> None:
    if destination.is_file() and destination.stat().st_size > 0:
        print(f"REUSE {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    print(f"DOWNLOAD {repo_id}/{filename} -> {destination}")
    hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=str(destination.parent),
    )
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError(f"download did not create {destination}")
    print(f"READY {destination} ({destination.stat().st_size / 1024**3:.2f} GB)")


def main() -> int:
    WD14_DIR.mkdir(parents=True, exist_ok=True)
    QWENVL_DIR.mkdir(parents=True, exist_ok=True)
    download(
        "SmilingWolf/wd-v1-4-convnextv2-tagger-v2",
        "model.onnx",
        WD14_MODEL,
    )
    download(
        "SmilingWolf/wd-v1-4-convnextv2-tagger-v2",
        "selected_tags.csv",
        WD14_TAGS,
    )
    download(
        "concedo/llama-joycaption-beta-one-hf-llava-mmproj-gguf",
        JOYCAPTION_GGUF_MODEL.name,
        JOYCAPTION_GGUF_MODEL,
    )
    download(
        "concedo/llama-joycaption-beta-one-hf-llava-mmproj-gguf",
        JOYCAPTION_GGUF_MMPROJ.name,
        JOYCAPTION_GGUF_MMPROJ,
    )
    download(
        "Qwen/Qwen3-VL-4B-Instruct-GGUF",
        QWENVL_MODEL.name,
        QWENVL_MODEL,
    )
    download(
        "Qwen/Qwen3-VL-4B-Instruct-GGUF",
        QWENVL_MMPROJ.name,
        QWENVL_MMPROJ,
    )
    joy_model = resolve_joycaption_model(JOYCAPTION_CANDIDATES)
    if joy_model is None:
        raise RuntimeError("existing JoyCaption model was not found")
    print(f"REUSE {joy_model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
