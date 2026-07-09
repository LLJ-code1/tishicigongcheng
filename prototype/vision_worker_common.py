"""Shared helpers for standalone local vision workers."""

from __future__ import annotations

import json
from pathlib import Path


SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def validate_image_path(path: str | Path) -> Path:
    image_path = Path(path)
    if not image_path.is_file():
        raise FileNotFoundError(f"image not found: {image_path}")
    if image_path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        raise ValueError(f"unsupported image format: {image_path.suffix}")
    return image_path


def encode_worker_result(
    analyzer_id: str,
    result: str,
    elapsed: float,
) -> str:
    cleaned = str(result or "").strip()
    if not cleaned:
        raise ValueError("empty analyzer result")
    return json.dumps(
        {
            "analyzer": analyzer_id,
            "result": cleaned,
            "elapsed": round(float(elapsed), 3),
        },
        ensure_ascii=False,
    )
