import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
class Unicode15RangeGenerationTests(unittest.TestCase):
    def test_generated_unicode_tables_are_current(self):
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "generate_unicode15_ranges.py"), "--check"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
