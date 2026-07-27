import unittest
from pathlib import Path

import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiment_matrix import (  # noqa: E402
    ExperimentMatrixError,
    normalize_experiment_matrices_by_version,
    plan_experiment_matrix,
)


class ExperimentMatrixTests(unittest.TestCase):
    def payload(self, **changes):
        value = {
            "name": "CFG and sampler",
            "version": 3,
            "attributionSeed": 42,
            "design": "one-factor",
            "variables": [
                {"key": "cfgScale", "values": [5, 7, 9]},
                {"key": "sampler", "values": ["Euler", "DPM++ 2M"]},
            ],
        }
        value.update(changes)
        return value

    def test_one_factor_uses_same_attribution_seed_and_only_changes_one_variable(self):
        plan = plan_experiment_matrix(self.payload())
        self.assertEqual(len(plan["cells"]), 4)
        self.assertTrue(all(cell["generationSeed"] == 42 for cell in plan["cells"]))
        self.assertEqual(plan["cells"][0]["parameters"], {"cfgScale": 5, "sampler": "Euler"})

    def test_cartesian_is_bounded_and_persisted_cells_cannot_be_forged(self):
        plan = plan_experiment_matrix(self.payload(design="cartesian"))
        self.assertEqual(len(plan["cells"]), 6)
        persisted = normalize_experiment_matrices_by_version({"3": [plan]})
        self.assertEqual(persisted["3"][0]["id"], plan["id"])
        plan["cells"][0]["parameters"] = {"cfgScale": 999}
        with self.assertRaises(ExperimentMatrixError):
            normalize_experiment_matrices_by_version({"3": [plan]})

    def test_rejects_cartesian_matrix_over_cell_limit(self):
        with self.assertRaises(ExperimentMatrixError):
            plan_experiment_matrix(
                self.payload(
                    design="cartesian",
                    variables=[
                        {"key": f"v{index}", "values": list(range(8))}
                        for index in range(3)
                    ],
                )
            )
