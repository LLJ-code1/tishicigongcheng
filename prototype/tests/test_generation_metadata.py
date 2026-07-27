import io
import json
import sys
import unittest
from pathlib import Path

from PIL import Image, PngImagePlugin


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import model_profiles  # noqa: E402
import recipe  # noqa: E402
from generation_metadata import (  # noqa: E402
    format_a1111_infotext,
    inspect_generation_png,
)


def sample_recipe():
    profile = model_profiles.load_model_profile()
    parameters = recipe.resolve_parameters(
        model_default=model_profiles.profile_default_parameters(profile),
        task_preset={"resolution": {"width": 1024, "height": 1536}, "generationSeed": 42},
        required_keys=recipe.REQUIRED_PARAMETER_KEYS,
    )
    return recipe.build_recipe(
        model=model_profiles.model_reference(profile),
        prompts={"positiveEn": "1girl, blue dress", "positiveZh": "", "negativeEn": "bad hands", "negativeZh": ""},
        blocks=[],
        parameters=parameters,
    )


def png_bytes(metadata):
    image = Image.new("RGB", (2, 2), "white")
    info = PngImagePlugin.PngInfo()
    for key, value in metadata.items():
        info.add_text(key, value, zip=key in {"prompt", "workflow"})
    output = io.BytesIO()
    image.save(output, format="PNG", pnginfo=info)
    return output.getvalue()


class GenerationMetadataTests(unittest.TestCase):
    def test_a1111_export_round_trips_the_recipe_fields(self):
        value = sample_recipe()
        result = inspect_generation_png(
            png_bytes({"parameters": format_a1111_infotext(value)}), value
        )

        self.assertEqual(result["source"], "a1111")
        self.assertTrue(result["metadataAvailable"])
        self.assertTrue(all(item["status"] == "match" for item in result["diff"]))
        self.assertEqual(result["actual"]["resolution"], {"width": 1024, "height": 1536})

    def test_comfy_compressed_prompt_extracts_known_generation_fields(self):
        value = sample_recipe()
        prompt = {
            "1": {"inputs": {"ckpt_name": "anima.safetensors"}},
            "2": {"inputs": {"sampler_name": "Euler", "scheduler": "normal", "steps": 28, "cfg": 5.5, "seed": 42, "width": 1024, "height": 1536}},
        }
        result = inspect_generation_png(png_bytes({"prompt": json.dumps(prompt)}), value)

        self.assertEqual(result["source"], "comfyui")
        self.assertEqual(result["actual"]["checkpointName"], "anima.safetensors")
        self.assertEqual(result["actual"]["generationSeed"], 42)
        self.assertEqual(result["actual"]["resolution"], {"width": 1024, "height": 1536})

    def test_image_without_generation_metadata_returns_manual_fallback(self):
        value = sample_recipe()
        result = inspect_generation_png(png_bytes({"comment": "no generation data"}), value)

        self.assertEqual(result["source"], "none")
        self.assertFalse(result["metadataAvailable"])
        self.assertEqual(result["manualDraft"]["generationSeed"], 42)


if __name__ == "__main__":
    unittest.main()
