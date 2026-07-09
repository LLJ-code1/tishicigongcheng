"""Standalone Florence image caption worker using the existing local model."""

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import torch
from transformers import (
    AutoModelForCausalLM,
    AutoProcessor,
    GenerationConfig,
    GenerationMixin,
)
from transformers.dynamic_module_utils import get_imports
from transformers.utils import logging as transformers_logging


def fixed_get_imports(filename: str | Path) -> list[str]:
    imports = get_imports(filename)
    if str(filename).endswith("modeling_florence2.py") and "flash_attn" in imports:
        imports.remove("flash_attn")
    return imports


def analyze(image_path: Path, model_path: Path) -> str:
    warnings.filterwarnings("ignore")
    transformers_logging.set_verbosity_error()
    with patch(
        "transformers.dynamic_module_utils.get_imports",
        fixed_get_imports,
    ):
        model = AutoModelForCausalLM.from_pretrained(
            model_path,
            dtype=torch.float32,
            trust_remote_code=True,
            attn_implementation="eager",
            local_files_only=True,
        ).to("cpu")
    if hasattr(model, "language_model") and not isinstance(
        model.language_model,
        GenerationMixin,
    ):
        model.language_model.__class__.__bases__ = (
            GenerationMixin,
        ) + model.language_model.__class__.__bases__
    if hasattr(model, "language_model") and (
        not hasattr(model.language_model, "generation_config")
        or model.language_model.generation_config is None
    ):
        model.language_model.generation_config = GenerationConfig.from_pretrained(
            model_path,
            local_files_only=True,
        )
    processor = AutoProcessor.from_pretrained(
        model_path,
        trust_remote_code=True,
        local_files_only=True,
    )
    image = Image.open(image_path).convert("RGB")
    prompt = "<GENERATE_TAGS>"
    inputs = processor(
        text=prompt,
        images=image,
        return_tensors="pt",
        do_rescale=False,
    )
    with torch.inference_mode():
        generated_ids = model.generate(
            input_ids=inputs["input_ids"],
            pixel_values=inputs["pixel_values"],
            max_new_tokens=384,
            do_sample=False,
            num_beams=1,
            use_cache=False,
        )
    generated_text = processor.batch_decode(
        generated_ids,
        skip_special_tokens=False,
    )[0]
    parsed = processor.post_process_generation(
        generated_text,
        task=prompt,
        image_size=image.size,
    )
    return str(parsed[prompt]).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    result = analyze(Path(args.image), Path(args.model))
    print(json.dumps({"result": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
