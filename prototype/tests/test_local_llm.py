import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_llm import (  # noqa: E402
    LocalLlmError,
    build_llama_command,
    runtime_paths,
    start_local_llm,
)


class LocalLlmRuntimeTests(unittest.TestCase):
    def test_builds_a_gpu_offloaded_llama_server_command(self):
        paths = runtime_paths(Path("H:/prompt-project"))
        command = build_llama_command(paths)

        self.assertEqual(command[0], str(paths["executable"]))
        self.assertIn("--model", command)
        self.assertIn("--alias", command)
        self.assertIn("Huihui-Qwen3-14B-abliterated-v2.Q4_K_M.gguf", command)
        self.assertIn("--flash-attn", command)
        self.assertIn("auto", command)
        self.assertIn("--jinja", command)
        self.assertIn("--chat-template-kwargs", command)
        self.assertIn('{"enable_thinking":false}', command)
        self.assertIn("--parallel", command)
        self.assertIn("1", command)
        self.assertIn("-ngl", command)
        self.assertIn("99", command)
        self.assertIn("8192", command)
        self.assertIn("--reasoning", command)
        self.assertIn("off", command)
        self.assertIn("--reasoning-budget", command)
        self.assertIn("0", command)

    def test_start_rejects_an_uninstalled_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("local_llm.probe_local_llm", return_value=False):
                with self.assertRaises(LocalLlmError) as context:
                    start_local_llm(Path(directory), ready_timeout=0)

        self.assertEqual(context.exception.code, "runtime_not_installed")


if __name__ == "__main__":
    unittest.main()
