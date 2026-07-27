"""Read-only PNG generation metadata inspection and A1111 infotext export."""

from __future__ import annotations

import json
import re
import struct
import zlib
from collections.abc import Mapping
from typing import Any

from recipe import normalize_recipe


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_TEXT_CHUNK_BYTES = 2 * 1024 * 1024
_A1111_FIELDS = {
    "Steps": ("steps", int),
    "Sampler": ("sampler", str),
    "Schedule type": ("scheduler", str),
    "Scheduler": ("scheduler", str),
    "CFG scale": ("cfg", float),
    "Seed": ("generationSeed", int),
    "Size": ("resolution", str),
    "Denoising strength": ("denoiseStrength", float),
    "Model hash": ("checkpointHash", str),
    "Model": ("checkpointName", str),
}
_A1111_FIELD_PATTERN = re.compile(r"(?:^|, )([^:,]+): (.*?)(?=, [^:,]+: |$)")


class GenerationMetadataError(ValueError):
    pass


def read_png_text_chunks(image_bytes: bytes) -> dict[str, str]:
    """Read bounded PNG text chunks without decoding or writing the image."""

    if not isinstance(image_bytes, bytes) or not image_bytes.startswith(PNG_SIGNATURE):
        raise GenerationMetadataError("image is not a PNG")
    offset = len(PNG_SIGNATURE)
    values: dict[str, str] = {}
    while offset < len(image_bytes):
        if offset + 12 > len(image_bytes):
            raise GenerationMetadataError("PNG chunk is truncated")
        length = struct.unpack(">I", image_bytes[offset : offset + 4])[0]
        chunk_type = image_bytes[offset + 4 : offset + 8]
        payload_start = offset + 8
        payload_end = payload_start + length
        if length > MAX_TEXT_CHUNK_BYTES or payload_end + 4 > len(image_bytes):
            raise GenerationMetadataError("PNG text chunk is invalid or too large")
        payload = image_bytes[payload_start:payload_end]
        if chunk_type == b"tEXt":
            key, separator, value = payload.partition(b"\0")
            if separator:
                values[key.decode("latin-1")] = value.decode("utf-8", "replace")
        elif chunk_type == b"zTXt":
            key, separator, rest = payload.partition(b"\0")
            if separator and len(rest) > 1 and rest[0] == 0:
                try:
                    value = zlib.decompress(rest[1:])
                except zlib.error as error:
                    raise GenerationMetadataError("PNG compressed text is invalid") from error
                if len(value) > MAX_TEXT_CHUNK_BYTES:
                    raise GenerationMetadataError("PNG compressed text is too large")
                values[key.decode("latin-1")] = value.decode("utf-8", "replace")
        elif chunk_type == b"iTXt":
            key, separator, rest = payload.partition(b"\0")
            if separator and len(rest) >= 2:
                compressed = rest[0]
                method = rest[1]
                language_start = rest[2:]
                _, _, translated_and_text = language_start.partition(b"\0")
                _, _, text = translated_and_text.partition(b"\0")
                if compressed:
                    if method != 0:
                        raise GenerationMetadataError("PNG text compression method is invalid")
                    try:
                        text = zlib.decompress(text)
                    except zlib.error as error:
                        raise GenerationMetadataError("PNG compressed text is invalid") from error
                if len(text) > MAX_TEXT_CHUNK_BYTES:
                    raise GenerationMetadataError("PNG text is too large")
                values[key.decode("latin-1")] = text.decode("utf-8", "replace")
        offset = payload_end + 4
        if chunk_type == b"IEND":
            break
    return values


def parse_a1111_infotext(value: str) -> dict[str, Any]:
    lines = value.replace("\r\n", "\n").split("\n")
    negative_index = next(
        (index for index, line in enumerate(lines) if line.startswith("Negative prompt: ")),
        None,
    )
    parameter_index = next(
        (index for index, line in enumerate(lines) if line.startswith("Steps: ")),
        None,
    )
    if parameter_index is None:
        return {}
    positive_lines = lines[: negative_index if negative_index is not None else parameter_index]
    negative_lines = (
        [lines[negative_index][len("Negative prompt: ") :], *lines[negative_index + 1 : parameter_index]]
        if negative_index is not None
        else []
    )
    fields: dict[str, Any] = {
        "positivePrompt": "\n".join(positive_lines).strip(),
        "negativePrompt": "\n".join(negative_lines).strip(),
    }
    for label, raw_value in _A1111_FIELD_PATTERN.findall(lines[parameter_index]):
        rule = _A1111_FIELDS.get(label.strip())
        if rule is None:
            continue
        key, converter = rule
        try:
            parsed = converter(raw_value.strip())
        except ValueError:
            continue
        if key == "resolution" and re.fullmatch(r"\d+x\d+", str(parsed)):
            width, height = str(parsed).split("x")
            fields[key] = {"width": int(width), "height": int(height)}
        else:
            fields[key] = parsed
    return {key: value for key, value in fields.items() if value not in ("", None)}


def _walk_comfy(value: object, fields: dict[str, Any]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {"ckpt_name", "sampler_name", "scheduler", "steps", "cfg", "seed", "width", "height"} and isinstance(item, (str, int, float)):
                mapped = {
                    "ckpt_name": "checkpointName",
                    "sampler_name": "sampler",
                    "seed": "generationSeed",
                }.get(str(key), str(key))
                fields.setdefault(mapped, item)
            _walk_comfy(item, fields)
    elif isinstance(value, list):
        for item in value:
            _walk_comfy(item, fields)


def parse_comfy_metadata(chunks: Mapping[str, str]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in ("prompt", "workflow"):
        raw = chunks.get(key)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        _walk_comfy(parsed, fields)
    if "width" in fields and "height" in fields:
        fields["resolution"] = {
            "width": int(fields.pop("width")),
            "height": int(fields.pop("height")),
        }
    return fields


def recipe_generation_fields(recipe: Mapping[str, object]) -> dict[str, Any]:
    normalized = normalize_recipe(recipe)
    parameters = normalized["parameters"]
    prompts = normalized["prompts"]
    return {
        "positivePrompt": prompts["positiveEn"],
        "negativePrompt": prompts["negativeEn"],
        **{
            key: parameters[key]["value"]
            for key in (
                "sampler",
                "scheduler",
                "steps",
                "cfg",
                "generationSeed",
                "denoiseStrength",
                "resolution",
            )
            if key in parameters
        },
    }


def generation_field_diff(recipe: Mapping[str, object], actual: Mapping[str, Any]) -> list[dict[str, Any]]:
    expected = recipe_generation_fields(recipe)
    return [
        {
            "field": key,
            "expected": value,
            "actual": actual.get(key),
            "status": "missing" if key not in actual else "match" if actual[key] == value else "mismatch",
        }
        for key, value in expected.items()
    ]


def inspect_generation_png(image_bytes: bytes, recipe: Mapping[str, object]) -> dict[str, Any]:
    chunks = read_png_text_chunks(image_bytes)
    if "parameters" in chunks:
        source = "a1111"
        actual = parse_a1111_infotext(chunks["parameters"])
    elif "prompt" in chunks or "workflow" in chunks:
        source = "comfyui"
        actual = parse_comfy_metadata(chunks)
    else:
        source = "none"
        actual = {}
    return {
        "source": source,
        "actual": actual,
        "diff": generation_field_diff(recipe, actual),
        "manualDraft": recipe_generation_fields(recipe),
        "metadataAvailable": bool(actual),
    }


def format_a1111_infotext(recipe: Mapping[str, object]) -> str:
    fields = recipe_generation_fields(recipe)
    resolution = fields.get("resolution", {})
    parts = [
        f"Steps: {fields.get('steps', '')}",
        f"Sampler: {fields.get('sampler', '')}",
        f"Schedule type: {fields.get('scheduler', '')}",
        f"CFG scale: {fields.get('cfg', '')}",
        f"Seed: {fields.get('generationSeed', '')}",
    ]
    if isinstance(resolution, Mapping):
        parts.append(f"Size: {resolution.get('width', '')}x{resolution.get('height', '')}")
    if "denoiseStrength" in fields:
        parts.append(f"Denoising strength: {fields['denoiseStrength']}")
    return "\n".join(
        [fields["positivePrompt"], f"Negative prompt: {fields['negativePrompt']}", ", ".join(parts)]
    )


__all__ = [
    "GenerationMetadataError",
    "format_a1111_infotext",
    "inspect_generation_png",
    "parse_a1111_infotext",
    "read_png_text_chunks",
]
