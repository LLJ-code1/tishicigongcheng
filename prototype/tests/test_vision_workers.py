import json
import sys
import unittest
from pathlib import Path

import numpy
from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vision_worker_common import encode_worker_result  # noqa: E402
from vision_worker_joycaption import resolve_joycaption_model  # noqa: E402
from vision_worker_qwenvl import qwen_model_status  # noqa: E402
from vision_worker_wd14 import prepare_wd14_image  # noqa: E402


class VisionWorkerTests(unittest.TestCase):
    def test_worker_result_is_valid_json(self):
        payload = json.loads(encode_worker_result("wd14", "1girl, solo", 1.25))
        self.assertEqual(payload["analyzer"], "wd14")
        self.assertEqual(payload["result"], "1girl, solo")
        self.assertEqual(payload["elapsed"], 1.25)

    def test_wd14_preprocesses_to_bgr_square_float_batch(self):
        image = Image.new("RGB", (640, 320), (255, 0, 0))
        batch = prepare_wd14_image(image, 448)
        self.assertEqual(batch.shape, (1, 448, 448, 3))
        self.assertEqual(batch.dtype, numpy.float32)
        self.assertEqual(tuple(batch[0, 224, 224]), (0.0, 0.0, 255.0))

    def test_joycaption_prefers_existing_fp8_model(self):
        fp8 = Path("beta-FP8-Dynamic")
        alpha = Path("alpha-two")
        existing = {fp8, alpha}
        result = resolve_joycaption_model(
            [fp8, alpha],
            exists=lambda path: path in existing,
        )
        self.assertEqual(result, fp8)

    def test_qwen_status_requires_model_and_mmproj(self):
        status = qwen_model_status(
            Path("missing-model.gguf"),
            Path("missing-mmproj.gguf"),
            exists=lambda path: False,
        )
        self.assertFalse(status["installed"])
        self.assertEqual(len(status["missing"]), 2)


if __name__ == "__main__":
    unittest.main()
