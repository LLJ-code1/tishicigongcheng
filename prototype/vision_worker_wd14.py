"""Standalone WD14 ONNX image tagger."""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import numpy
from PIL import Image

from vision_worker_common import encode_worker_result, validate_image_path


def prepare_wd14_image(image: Image.Image, size: int) -> numpy.ndarray:
    image = image.convert("RGB")
    ratio = float(size) / max(image.size)
    resized = image.resize(
        tuple(max(1, int(value * ratio)) for value in image.size),
        Image.Resampling.LANCZOS,
    )
    square = Image.new("RGB", (size, size), (255, 255, 255))
    square.paste(
        resized,
        ((size - resized.width) // 2, (size - resized.height) // 2),
    )
    array = numpy.asarray(square, dtype=numpy.float32)
    return numpy.expand_dims(array[:, :, ::-1], axis=0)


def analyze(
    image_path: Path,
    model_path: Path,
    tags_path: Path,
    threshold: float = 0.35,
    character_threshold: float = 0.85,
) -> str:
    import onnxruntime

    session = onnxruntime.InferenceSession(
        str(model_path),
        providers=["CPUExecutionProvider"],
    )
    model_input = session.get_inputs()[0]
    size = int(model_input.shape[1])
    image = Image.open(image_path)
    batch = prepare_wd14_image(image, size)

    tags = []
    categories = []
    with tags_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            tags.append(row["name"].replace("_", " "))
            categories.append(int(row["category"]))

    output_name = session.get_outputs()[0].name
    probabilities = session.run(
        [output_name],
        {model_input.name: batch},
    )[0][0]
    selected = []
    for tag, category, probability in zip(tags, categories, probabilities):
        minimum = character_threshold if category == 4 else threshold
        if category in {0, 4} and float(probability) >= minimum:
            selected.append((tag, float(probability)))
    selected.sort(key=lambda item: item[1], reverse=True)
    return ", ".join(tag for tag, _ in selected)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--tags", required=True)
    args = parser.parse_args()
    started = time.perf_counter()
    result = analyze(
        validate_image_path(args.image),
        Path(args.model),
        Path(args.tags),
    )
    print(encode_worker_result("wd14", result, time.perf_counter() - started))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
