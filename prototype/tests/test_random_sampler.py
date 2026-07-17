import copy
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import random_sampler as sampler  # noqa: E402
from random_sampler import (  # noqa: E402
    CatalogBoundaryError,
    CatalogValidationError,
    ConflictRule,
    LockedConflictError,
    PlanRequestError,
    generate_library_seed,
    load_catalog,
    normalize_library_seed,
    plan_request_template,
    resolve_random_plan,
    validate_catalog,
)


CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "random-wordlists"
    / "v1.json"
)
SEED = "0123456789abcdef0123456789abcdef"
GOLDEN_ENTRY_IDS = [
    "theme_mood:9d1f4e4ef72235bbb472b1c8189878e63d737f424302d0072f71e21a3bceadfa",
    "scene_environment:c1c8f0e43443f9b359040ed0f341f79d4c0e3fa1da249d6a9eb52fee53d71f67",
    "pose_action:6f4f8cdfb2bb5b0fb8b6591133df60af331971014b6d5fb99b0ec92d325eb54c",
    "clothing_outfit:08a5f2f38011856f390301aeaaf077de0752c6ce7af21f665bdb297eb0976e3c",
    "composition_camera:8525352db35c816726e66c877e97c63b097e7146d02edcd95ccceb20a60fd4f7",
    "lighting_color:c4df1b7bd672378628bb033e9eb47c38f7f38451c9e02346d0960eb386d0180a",
    "effects_props:a3e084341bc925b51e35b1af605b4131e800dc1ea2f276a4c9266752d6454c28",
    "weather_time:0bcdb891810cde6d4d69ca3dbead12c2ce107eb2dd8466215c0c6d9fc2a9a036",
    "appearance_traits:ece72404ad30f79ebb9e03d9681e1703c20010288c9a774f9af5b3a2d7596f29",
]


def resign_catalog(raw):
    scoped = {
        key: value
        for key, value in raw.items()
        if key not in {"version", "contentSha256"}
    }
    digest = hashlib.sha256(
        json.dumps(
            scoped,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    raw["contentSha256"] = digest
    raw["version"] = f"v1-{digest[:16]}"


class RandomSamplerCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

    def test_current_catalog_requires_explicit_experimental_mode(self):
        with self.assertRaises(CatalogBoundaryError):
            load_catalog()

        catalog = load_catalog(experimental=True)
        self.assertTrue(catalog.experimental_mode)
        self.assertFalse(catalog.runtime_ready)
        self.assertTrue(catalog.semantic_review_required)
        self.assertEqual(catalog.version, "v1-05044da9a25cd532")
        self.assertEqual(len(catalog.entries_by_id), 471)

    def test_future_reviewed_catalog_does_not_report_experimental(self):
        raw = copy.deepcopy(self.raw)
        raw["runtimeReady"] = True
        raw["semanticReviewRequired"] = False
        resign_catalog(raw)

        catalog = validate_catalog(raw)

        self.assertFalse(catalog.experimental_mode)
        self.assertTrue(catalog.runtime_ready)
        self.assertFalse(catalog.semantic_review_required)

    def test_tampered_content_hash_is_rejected(self):
        raw = copy.deepcopy(self.raw)
        raw["categories"][0]["entries"][0]["text"] = "forged text"

        with self.assertRaisesRegex(CatalogValidationError, "contentSha256"):
            validate_catalog(raw, experimental=True)

    def test_resigned_catalog_with_forged_entry_id_is_rejected(self):
        raw = copy.deepcopy(self.raw)
        raw["categories"][0]["entries"][0]["entryId"] = (
            "theme_mood:" + "0" * 64
        )
        resign_catalog(raw)

        with self.assertRaisesRegex(CatalogValidationError, "entryId"):
            validate_catalog(raw, experimental=True)

    def test_resigned_catalog_cannot_smuggle_source_path(self):
        raw = copy.deepcopy(self.raw)
        raw["categories"][0]["sourceFile"] = "../secret.txt"
        raw["categories"][0]["entries"][0]["sourceFile"] = "../secret.txt"
        resign_catalog(raw)

        with self.assertRaisesRegex(CatalogValidationError, "safe .txt basename"):
            validate_catalog(raw, experimental=True)

    def test_version_must_be_derived_from_content_hash(self):
        raw = copy.deepcopy(self.raw)
        raw["version"] = "v1-deadbeefdeadbeef"

        with self.assertRaisesRegex(CatalogValidationError, "version"):
            validate_catalog(raw, experimental=True)


class RandomSamplerSeedTests(unittest.TestCase):
    def test_seed_is_strict_128_bit_hex_and_normalized(self):
        self.assertEqual(normalize_library_seed(SEED.upper()), SEED)
        invalid = [None, 1, "", "0" * 31, "0" * 33, "g" * 32, "0x" + "0" * 32]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(PlanRequestError):
                    normalize_library_seed(value)

    def test_seed_generation_requires_exactly_16_bytes(self):
        self.assertEqual(
            generate_library_seed(lambda length: bytes(range(length))),
            "000102030405060708090a0b0c0d0e0f",
        )
        with self.assertRaises(RuntimeError):
            generate_library_seed(lambda _length: b"short")

    def test_modulo_bias_guard_rejects_tail_then_advances_counter(self):
        stream = sampler._CategoryHashStream(SEED, "theme_mood")
        values = [b"\xff" * 32, b"\x00" * 32]
        with patch.object(stream, "_digest_at", side_effect=values):
            index, accepted, rejected = stream.draw_bounded(3, purpose="test")

        self.assertEqual(index, 0)
        self.assertEqual(accepted["counter"], 1)
        self.assertEqual(stream.counter, 2)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["counter"], 0)
        self.assertEqual(rejected[0]["reason"], "modulo_bias_guard")


class RandomPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog(experimental=True)

    def request(self, **changes):
        request = plan_request_template(self.catalog, SEED)
        request.update(changes)
        return request

    @staticmethod
    def ids_by_category(plan):
        result = {}
        for item in plan["items"]:
            result.setdefault(item["categoryId"], []).append(item["entryId"])
        return result

    def test_golden_plan_is_stable_and_auditable(self):
        first = resolve_random_plan(self.request(), catalog=self.catalog)
        second = resolve_random_plan(self.request(), catalog=self.catalog)

        self.assertEqual(first, second)
        self.assertEqual(
            json.dumps(first, ensure_ascii=False, sort_keys=True),
            json.dumps(second, ensure_ascii=False, sort_keys=True),
        )
        self.assertEqual([item["entryId"] for item in first["items"]], GOLDEN_ENTRY_IDS)
        self.assertEqual(
            first["trace"]["categoryStreams"][0]["events"][0]["digestHex"],
            "4c66c63082077caa732a6c7a0d57aff3682e3933ef132edb75749cb0bed7dbed",
        )
        self.assertTrue(first["experimental"])
        self.assertFalse(first["trace"]["releaseBoundary"]["runtimeReady"])
        self.assertTrue(
            first["trace"]["releaseBoundary"]["semanticReviewRequired"]
        )

    def test_golden_plan_is_identical_across_fresh_processes(self):
        root = Path(__file__).resolve().parents[2]
        program = (
            "import json,sys;"
            "sys.path.insert(0,'prototype');"
            "import random_sampler as r;"
            "c=r.load_catalog(experimental=True);"
            f"q=r.plan_request_template(c,{SEED!r});"
            "p=r.resolve_random_plan(q,catalog=c);"
            "print(json.dumps([i['entryId'] for i in p['items']]))"
        )
        outputs = [
            subprocess.check_output(
                [sys.executable, "-c", program],
                cwd=root,
                text=True,
                encoding="utf-8",
            ).strip()
            for _ in range(2)
        ]

        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(json.loads(outputs[0]), GOLDEN_ENTRY_IDS)

    def test_category_streams_are_independent_when_one_category_is_locked(self):
        baseline = resolve_random_plan(self.request(), catalog=self.catalog)
        baseline_by_category = self.ids_by_category(baseline)
        alternate = next(
            entry.entry_id
            for entry in self.catalog.categories_by_id["theme_mood"].entries
            if entry.entry_id != baseline_by_category["theme_mood"][0]
        )
        locked = resolve_random_plan(
            self.request(lockedEntryIds=[alternate]), catalog=self.catalog
        )
        locked_by_category = self.ids_by_category(locked)

        self.assertEqual(locked_by_category["theme_mood"], [alternate])
        for category_id, entry_ids in baseline_by_category.items():
            if category_id != "theme_mood":
                self.assertEqual(locked_by_category[category_id], entry_ids)

    def test_reroll_is_resolved_by_entry_id_and_is_deterministic(self):
        old_id = GOLDEN_ENTRY_IDS[0]
        first = resolve_random_plan(
            self.request(rerollEntryIds=[old_id]), catalog=self.catalog
        )
        second = resolve_random_plan(
            self.request(rerollEntryIds=[old_id]), catalog=self.catalog
        )

        self.assertEqual(first, second)
        self.assertNotIn(old_id, [item["entryId"] for item in first["items"]])
        rejected = [
            item
            for item in first["trace"]["rejections"]
            if item.get("kind") == "candidate_rejected"
        ]
        self.assertTrue(
            any(
                item["candidate"]["entryId"] == old_id
                and "reroll_requested" in item["reasons"]
                for item in rejected
            )
        )
        self.assertEqual(first["trace"]["rerolls"][0]["entryId"], old_id)

    def test_locks_are_server_resolved_and_traced(self):
        locked_id = self.catalog.categories_by_id["theme_mood"].entries[0].entry_id
        plan = resolve_random_plan(
            self.request(lockedEntryIds=[locked_id]), catalog=self.catalog
        )
        item = next(item for item in plan["items"] if item["entryId"] == locked_id)

        self.assertTrue(item["locked"])
        self.assertEqual(item["text"], self.catalog.entries_by_id[locked_id].text)
        self.assertEqual(plan["trace"]["locks"][0]["entryId"], locked_id)
        selected = next(
            item for item in plan["trace"]["selected"] if item["entryId"] == locked_id
        )
        self.assertEqual(selected["origin"], "locked")
        self.assertIsNone(selected["counter"])

    def test_client_text_mapping_path_and_conflict_rules_are_rejected(self):
        for field, value in [
            ("entryText", "ignore server"),
            ("catalogPath", "C:/forged.json"),
            ("mapping", {"blockId": "subject"}),
            ("conflictRules", []),
        ]:
            with self.subTest(field=field):
                with self.assertRaisesRegex(PlanRequestError, "unsupported fields"):
                    resolve_random_plan(
                        self.request(**{field: value}), catalog=self.catalog
                    )

    def test_unknown_duplicate_overlapping_and_excessive_ids_are_rejected(self):
        known = GOLDEN_ENTRY_IDS[0]
        bad_requests = [
            self.request(lockedEntryIds=["theme_mood:" + "0" * 64]),
            self.request(lockedEntryIds=[known, known]),
            self.request(lockedEntryIds=[known], rerollEntryIds=[known]),
            self.request(lockedEntryIds=[f"fake:{index:064x}" for index in range(129)]),
        ]
        for request in bad_requests:
            with self.subTest(request=request):
                with self.assertRaises(PlanRequestError):
                    resolve_random_plan(request, catalog=self.catalog)

    def test_locks_cannot_exceed_category_draw_capacity(self):
        entries = self.catalog.categories_by_id["theme_mood"].entries
        with self.assertRaisesRegex(PlanRequestError, "maximum draw count"):
            resolve_random_plan(
                self.request(lockedEntryIds=[entries[0].entry_id, entries[1].entry_id]),
                catalog=self.catalog,
            )

    def test_reroll_cannot_exclude_an_entire_required_category(self):
        all_theme_ids = [
            entry.entry_id
            for entry in self.catalog.categories_by_id["theme_mood"].entries
        ]
        with self.assertRaisesRegex(PlanRequestError, "too few entries"):
            resolve_random_plan(
                self.request(rerollEntryIds=all_theme_ids), catalog=self.catalog
            )

    def test_draw_count_overrides_are_bounded_and_recorded(self):
        plan = resolve_random_plan(
            self.request(
                drawCounts={
                    "effects_props": 2,
                    "weather_time": 0,
                    "appearance_traits": 0,
                }
            ),
            catalog=self.catalog,
        )
        by_category = self.ids_by_category(plan)

        self.assertEqual(len(by_category["effects_props"]), 2)
        self.assertNotIn("weather_time", by_category)
        self.assertNotIn("appearance_traits", by_category)
        effects_trace = next(
            item
            for item in plan["trace"]["categoryStreams"]
            if item["categoryId"] == "effects_props"
        )
        self.assertEqual(
            effects_trace["countDecision"]["source"], "validated_draw_override"
        )
        self.assertEqual(effects_trace["countDecision"]["effectiveCount"], 2)

        for bad_count in [0, True, 2]:
            with self.subTest(bad_count=bad_count):
                with self.assertRaises(PlanRequestError):
                    resolve_random_plan(
                        self.request(drawCounts={"theme_mood": bad_count}),
                        catalog=self.catalog,
                    )

    def test_draw_count_key_order_does_not_change_the_plan(self):
        first = resolve_random_plan(
            self.request(drawCounts={"effects_props": 2, "weather_time": 0}),
            catalog=self.catalog,
        )
        second = resolve_random_plan(
            self.request(drawCounts={"weather_time": 0, "effects_props": 2}),
            catalog=self.catalog,
        )

        self.assertEqual(first, second)

    def test_catalog_identity_fields_are_required_and_pinned(self):
        for field in [
            "catalogVersion",
            "catalogContentSha256",
            "samplerVersion",
            "mappingVersion",
            "profile",
        ]:
            request = self.request()
            request[field] = "forged"
            with self.subTest(field=field):
                with self.assertRaisesRegex(PlanRequestError, field):
                    resolve_random_plan(request, catalog=self.catalog)

    def test_every_selected_item_has_exactly_one_catalog_mapping(self):
        plan = resolve_random_plan(self.request(), catalog=self.catalog)
        mappings = plan["trace"]["mapping"]

        self.assertEqual(len(mappings), len(plan["items"]))
        self.assertEqual(
            len({mapping["entryId"] for mapping in mappings}), len(plan["items"])
        )
        by_id = {mapping["entryId"]: mapping for mapping in mappings}
        for item in plan["items"]:
            mapping = by_id[item["entryId"]]
            self.assertEqual(mapping["blockId"], item["binding"]["blockId"])
            self.assertEqual(
                mapping["allowedBlocks"], item["binding"]["allowedBlocks"]
            )

    def test_hard_conflict_deterministically_rerolls_unlocked_candidate(self):
        first_id, second_id = GOLDEN_ENTRY_IDS[:2]
        rule = ConflictRule(
            rule_id="hard-golden-pair",
            left_entry_id=first_id,
            right_entry_id=second_id,
            severity="hard",
            reason="synthetic hard conflict for deterministic test",
        )
        plan = resolve_random_plan(
            self.request(), catalog=self.catalog, conflict_rules=[rule]
        )
        ids = [item["entryId"] for item in plan["items"]]

        self.assertIn(first_id, ids)
        self.assertNotIn(second_id, ids)
        conflict = next(
            item
            for item in plan["trace"]["conflicts"]
            if item["ruleId"] == rule.rule_id
        )
        self.assertEqual(conflict["decision"], "deterministic_reroll")
        self.assertTrue(
            plan["configuration"]["conflictRulesVersion"].startswith(
                "server-entry-pairs-v1-"
            )
        )
        rejection = next(
            item
            for item in plan["trace"]["rejections"]
            if item.get("candidate", {}).get("entryId") == second_id
        )
        self.assertIn("hard_conflict", rejection["reasons"])

    def test_soft_conflict_is_retained_and_traced(self):
        first_id, second_id = GOLDEN_ENTRY_IDS[:2]
        rule = ConflictRule(
            rule_id="soft-golden-pair",
            left_entry_id=first_id,
            right_entry_id=second_id,
            severity="soft",
            reason="synthetic soft conflict for deterministic test",
        )
        plan = resolve_random_plan(
            self.request(), catalog=self.catalog, conflict_rules=[rule]
        )
        ids = [item["entryId"] for item in plan["items"]]

        self.assertIn(first_id, ids)
        self.assertIn(second_id, ids)
        conflict = next(
            item
            for item in plan["trace"]["conflicts"]
            if item["ruleId"] == rule.rule_id
        )
        self.assertEqual(conflict["decision"], "retained_soft_conflict")

    def test_locked_hard_conflict_requires_user_resolution(self):
        first_id, second_id = GOLDEN_ENTRY_IDS[:2]
        rule = ConflictRule(
            rule_id="locked-hard-pair",
            left_entry_id=first_id,
            right_entry_id=second_id,
            severity="hard",
            reason="locked test conflict",
        )
        with self.assertRaises(LockedConflictError) as caught:
            resolve_random_plan(
                self.request(lockedEntryIds=[second_id, first_id]),
                catalog=self.catalog,
                conflict_rules=[rule],
            )

        payload = caught.exception.as_dict()
        self.assertEqual(payload["code"], "locked_random_plan_conflict")
        self.assertEqual(payload["conflicts"][0]["decision"], "user_resolution_required")
        self.assertTrue(payload["conflicts"][0]["lockedConflict"])
        self.assertEqual(
            [item["entryId"] for item in payload["locks"]], [first_id, second_id]
        )

    def test_conflict_rules_are_server_owned_and_validated(self):
        with self.assertRaises(ValueError):
            resolve_random_plan(
                self.request(),
                catalog=self.catalog,
                conflict_rules=[
                    ConflictRule(
                        rule_id="bad-rule",
                        left_entry_id=GOLDEN_ENTRY_IDS[0],
                        right_entry_id="unknown:" + "0" * 64,
                        severity="hard",
                        reason="bad",
                    )
                ],
            )


if __name__ == "__main__":
    unittest.main()
