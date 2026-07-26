import concurrent.futures
import copy
import json
import tempfile
import unittest
from pathlib import Path

import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backup  # noqa: E402
import db  # noqa: E402
from ai_edit_engine import AIEditError, create_ai_edit_preview  # noqa: E402
from random_sampler import RandomSamplerError  # noqa: E402
from recipe import project_recipe_to_prompt_version, recipe_hash  # noqa: E402
from server import (  # noqa: E402
    enrich_workspace_commit_payload,
    process_edit_apply_request,
    process_edit_preview_request,
    process_edit_undo_request,
    process_random_plan_request,
    process_recipe_resolve_request,
)


SEED = "0123456789abcdef0123456789abcdef"
WORKBENCH_BLOCK_IDS = [
    "quality",
    "artist",
    "subject",
    "appearance",
    "outfit",
    "expression",
    "pose",
    "interaction",
    "scene",
    "composition",
    "lighting",
    "effects",
    "negative",
]


def confirmed_brief_session():
    return {
        "schemaVersion": 1,
        "revision": 4,
        "stage": "brief_confirmed",
        "inputs": {"text": "雨夜里撑红伞的女孩", "images": []},
        "directions": [
            {
                "id": "rainy-red",
                "label": "雨夜红伞",
                "summary": "霓虹雨夜中的红伞少女",
            }
        ],
        "selectedDirectionId": "rainy-red",
        "brief": {
            "status": "confirmed",
            "summary": "红伞少女在雨夜街头奔跑",
            "items": [
                {
                    "id": "subject",
                    "category": "subject",
                    "text": "红伞少女",
                    "source": {"type": "user", "refId": None},
                    "locked": True,
                }
            ],
            "aiAdditions": [],
            "openQuestions": [],
        },
        "selectedModelProfileId": None,
        "decomposition": None,
        "recipeStatus": "missing",
        "conflicts": [],
    }


class FullWorkflowIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "flow.db"
        db.init_db(self.database)

    def tearDown(self):
        self.tempdir.cleanup()

    @staticmethod
    def workbench_blocks():
        initial = {
            "subject": ("1girl", "少女"),
            "appearance": ("long black hair, blue eyes", "黑色长发，蓝色眼睛"),
            "outfit": ("white dress", "白色连衣裙"),
            "expression": ("calm expression", "平静表情"),
            "pose": ("standing", "站立"),
            "scene": ("night city", "夜晚城市"),
            "composition": ("eye-level medium shot", "平视中景"),
            "lighting": ("neon rim light", "霓虹轮廓光"),
            "negative": ("low quality", "低质量"),
        }
        return [
            {
                "id": block_id,
                "en": initial.get(block_id, ("", ""))[0],
                "zh": initial.get(block_id, ("", ""))[1],
                "weight": 100,
                "locked": False,
                "source": "flow-test",
            }
            for block_id in WORKBENCH_BLOCK_IDS
        ]

    def initial_recipe(self):
        plan = process_random_plan_request(
            {"librarySeed": SEED},
            experimental_enabled=True,
        )
        resolved = process_recipe_resolve_request(
            {
                "profileId": "anima-1.1-v1",
                "prompts": {
                    "positiveEn": "masterpiece, best quality, score_7, 1girl, night city",
                    "positiveZh": "杰作，最佳质量，少女，夜晚城市",
                    "negativeEn": "low quality",
                    "negativeZh": "低质量",
                },
                "blocks": self.workbench_blocks(),
                "randomPlan": plan,
                "parameterLayers": {
                    "task_preset": {"generationSeed": 42},
                    "manual_override": {"steps": 32, "cfg": 5.8},
                },
                "metadata": {"flow": "integration"},
            }
        )
        return resolved["recipe"], plan

    @staticmethod
    def version_payload(recipe, identifier, base_version, source, extra_metadata=None):
        payload = project_recipe_to_prompt_version(
            recipe,
            source=source,
            base_version=base_version,
            metadata=extra_metadata or {},
            block_projection="workbench-thirteen",
        )
        payload["id"] = identifier
        return payload

    def commit(self, operation_id, project, version, *, create):
        payload = enrich_workspace_commit_payload(
            {
                "operationId": operation_id,
                "createProject": create,
                "project": project,
                "version": version,
            }
        )
        return db.commit_workspace(
            payload,
            self.database,
            idempotency_key=operation_id,
        ), payload

    def test_creative_intake_survives_atomic_commit_and_project_reopen(self):
        session = confirmed_brief_session()
        result, _ = self.commit(
            "save-intake-1",
            {
                "id": "project-intake",
                "name": "雨夜红伞",
                "mode": "text",
                "status": "draft",
                "metadata": {
                    "workspaceBaseVersion": 0,
                    "creativeIntake": session,
                },
            },
            None,
            create=True,
        )

        reopened = db.get_project(result["project"]["id"], self.database)

        self.assertEqual(reopened["metadata"]["creativeIntake"], session)

    def test_creative_intake_commit_replay_is_idempotent_and_changed_session_conflicts(self):
        session = confirmed_brief_session()
        first, frozen_request = self.commit(
            "save-intake-idempotent",
            {
                "id": "project-intake-idempotent",
                "name": "雨夜红伞",
                "metadata": {"creativeIntake": session},
            },
            None,
            create=True,
        )

        replay = db.commit_workspace(
            frozen_request,
            self.database,
            idempotency_key="save-intake-idempotent",
        )
        changed_session = copy.deepcopy(frozen_request)
        changed_session["project"]["metadata"]["creativeIntake"]["brief"][
            "summary"
        ] = "改写后的简报"

        self.assertEqual(replay, first)
        with self.assertRaises(db.IdempotencyConflictError):
            db.commit_workspace(
                changed_session,
                self.database,
                idempotency_key="save-intake-idempotent",
            )

    def test_creative_intake_survives_logical_backup_restore(self):
        session = confirmed_brief_session()
        self.commit(
            "save-intake-backup",
            {
                "id": "project-intake-backup",
                "name": "雨夜红伞",
                "metadata": {"creativeIntake": session},
            },
            None,
            create=True,
        )

        archive = self.root / "intake-backup.zip"
        backup.export_database(archive, db_path=self.database)
        restored_database = self.root / "intake-restored.db"
        backup.restore_backup(archive, restored_database)
        reopened = db.get_project("project-intake-backup", restored_database)

        self.assertEqual(reopened["metadata"]["creativeIntake"], session)

    @staticmethod
    def ai_preview(payload):
        model_response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "changes": [
                                    {
                                        "blockId": "clothing",
                                        "en": "red dress",
                                        "zh": "红色连衣裙",
                                        "reason": "用户只要求替换服装。",
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }
        return process_edit_preview_request(
            payload,
            settings_payload={
                "localTextUrl": "http://127.0.0.1:8080/v1",
                "localTextModel": "fake-local-model",
            },
            engine=lambda instruction, recipe, base_hash, settings, provider, target_model: create_ai_edit_preview(
                instruction,
                recipe,
                base_hash,
                settings,
                provider,
                target_model,
                transport=lambda *_: model_response,
            ),
        )

    def test_seed_to_recipe_edit_undo_export_restore_and_reopen(self):
        recipe_v1, plan = self.initial_recipe()
        project = {
            "id": "project-full-flow",
            "name": "Full flow",
            "mode": "text",
            "status": "draft",
            "metadata": {"workspaceBaseVersion": 1},
        }
        version_v1 = self.version_payload(
            recipe_v1,
            "version-full-flow-v1",
            0,
            "wordlist-plan",
        )
        committed_v1, request_v1 = self.commit(
            "save-full-flow-v1", project, version_v1, create=True
        )

        # Simulate a lost HTTP response: retrying the exact frozen request must
        # return the same authoritative result without appending another V1.
        replay_v1 = db.commit_workspace(
            request_v1,
            self.database,
            idempotency_key="save-full-flow-v1",
        )
        self.assertEqual(replay_v1, committed_v1)
        self.assertEqual(committed_v1["version"]["version"], 1)
        self.assertEqual(
            committed_v1["version"]["metadata"]["recipe"]["randomPlan"],
            plan,
        )
        self.assertEqual(
            committed_v1["version"]["metadata"]["recipe"]["parameters"]["steps"]["source"],
            "manual_override",
        )

        preview = self.ai_preview(
            {
                "instruction": "只把服装换成红色连衣裙",
                "recipe": recipe_v1,
                "baseRecipeHash": recipe_hash(recipe_v1),
            }
        )
        self.assertTrue(preview["ready"])
        self.assertEqual(preview["affectedIds"], ["clothing"])
        applied = process_edit_apply_request(
            {
                "recipe": recipe_v1,
                "preview": preview,
                "parentVersion": 1,
                "newVersion": 2,
            }
        )
        version_v2 = self.version_payload(
            applied["recipe"],
            "version-full-flow-v2",
            1,
            "instruction-edit",
            {"change": applied["change"], "parentVersion": 1},
        )
        committed_v2, _ = self.commit(
            "save-full-flow-v2",
            {
                **project,
                "baseUpdatedAt": committed_v1["project"]["updatedAt"],
                "metadata": {"workspaceBaseVersion": 2},
            },
            version_v2,
            create=False,
        )
        self.assertEqual(committed_v2["version"]["version"], 2)
        edited_recipe = committed_v2["version"]["metadata"]["recipe"]
        clothing = next(item for item in edited_recipe["blocks"] if item["id"] == "clothing")
        self.assertEqual(clothing["en"], "red dress")
        self.assertEqual(len(edited_recipe["instructionHistory"]), 1)

        undone = process_edit_undo_request(
            {
                "currentRecipe": applied["recipe"],
                "parentRecipe": recipe_v1,
                "currentVersion": 2,
                "parentVersion": 1,
                "newVersion": 3,
            }
        )
        version_v3 = self.version_payload(
            undone["recipe"],
            "version-full-flow-v3",
            2,
            "undo",
            {"change": undone["change"], "parentVersion": 2},
        )
        committed_v3, _ = self.commit(
            "save-full-flow-v3",
            {
                **project,
                "baseUpdatedAt": committed_v2["project"]["updatedAt"],
                "metadata": {"workspaceBaseVersion": 3},
            },
            version_v3,
            create=False,
        )
        self.assertEqual(committed_v3["version"]["version"], 3)
        undo_recipe = committed_v3["version"]["metadata"]["recipe"]
        self.assertEqual(undo_recipe["blocks"], recipe_v1["blocks"])
        self.assertEqual(undo_recipe["parameters"], recipe_v1["parameters"])
        self.assertEqual(undo_recipe["instructionHistory"][-1]["type"], "undo")

        archive = self.root / "full-flow.zip"
        backup.export_database(archive, db_path=self.database)
        restored_database = self.root / "restored-flow.db"
        report = backup.restore_backup(archive, restored_database)
        reopened = db.get_project(project["id"], restored_database)

        self.assertEqual(report["counts"]["versions"], 3)
        self.assertEqual([item["version"] for item in reopened["versions"]], [1, 2, 3])
        self.assertEqual(
            reopened["versions"][2]["metadata"]["recipe"]["blocks"],
            recipe_v1["blocks"],
        )
        self.assertEqual(
            reopened["versions"][1]["metadata"]["change"]["instruction"],
            "只把服装换成红色连衣裙",
        )

    def test_tampered_preview_stale_hash_and_forged_plan_are_rejected(self):
        recipe, _ = self.initial_recipe()
        preview = self.ai_preview(
            {
                "instruction": "只把服装换成红色连衣裙",
                "recipe": recipe,
                "baseRecipeHash": recipe_hash(recipe),
            }
        )
        tampered = copy.deepcopy(preview)
        tampered["diffs"][0]["after"]["en"] = "forged payload"

        with self.assertRaises(AIEditError):
            process_edit_apply_request(
                {
                    "recipe": recipe,
                    "preview": tampered,
                    "parentVersion": 1,
                    "newVersion": 2,
                }
            )
        with self.assertRaises(AIEditError):
            self.ai_preview(
                {
                    "instruction": "把表情改成微笑",
                    "recipe": recipe,
                    "baseRecipeHash": "0" * 64,
                }
            )
        with self.assertRaises(RandomSamplerError):
            process_random_plan_request(
                {
                    "librarySeed": SEED,
                    "catalogContentSha256": "0" * 64,
                },
                experimental_enabled=True,
            )
        invalid_recipe = copy.deepcopy(recipe)
        invalid_recipe["blocks"][-1]["id"] = invalid_recipe["blocks"][0]["id"]
        with self.assertRaises(ValueError):
            enrich_workspace_commit_payload(
                {
                    "operationId": "invalid-recipe-save",
                    "createProject": True,
                    "project": {"id": "invalid-recipe-project"},
                    "version": {
                        "id": "invalid-recipe-version",
                        "baseVersion": 0,
                        "metadata": {"recipe": invalid_recipe},
                    },
                }
            )

    def test_concurrent_identical_workspace_replays_create_one_version(self):
        recipe, _ = self.initial_recipe()
        payload = enrich_workspace_commit_payload(
            {
                "operationId": "save-concurrent-flow",
                "createProject": True,
                "project": {
                    "id": "project-concurrent-flow",
                    "name": "Concurrent",
                },
                "version": self.version_payload(
                    recipe,
                    "version-concurrent-flow",
                    0,
                    "manual",
                ),
            }
        )

        def submit(_index):
            return db.commit_workspace(
                payload,
                self.database,
                idempotency_key="save-concurrent-flow",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(submit, range(8)))

        self.assertTrue(all(item == results[0] for item in results))
        reopened = db.get_project("project-concurrent-flow", self.database)
        self.assertEqual(len(reopened["versions"]), 1)
        with self.assertRaises(db.IdempotencyConflictError):
            db.commit_workspace(
                {
                    **payload,
                    "project": {**payload["project"], "name": "Forged replay"},
                },
                self.database,
                idempotency_key="save-concurrent-flow",
            )
        self.assertEqual(
            db.get_project("project-concurrent-flow", self.database)["name"],
            "Concurrent",
        )


if __name__ == "__main__":
    unittest.main()
