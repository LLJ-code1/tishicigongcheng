import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import model_profiles  # noqa: E402


class BundledAnimaProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = model_profiles.load_model_profile()

    def test_exact_anima_version_and_unverified_checkpoint_are_explicit(self):
        self.assertEqual(self.profile["profileId"], "anima-1.1-v1")
        self.assertEqual(self.profile["displayName"], "MiaoMiao Harem Anima_1.1")
        self.assertEqual(self.profile["model"]["versionId"], 3004063)
        self.assertEqual(self.profile["model"]["versionName"], "Anima_1.1")
        self.assertEqual(self.profile["model"]["baseModel"], "Anima")
        self.assertIsNone(self.profile["model"]["checkpoint"]["filename"])
        self.assertIsNone(self.profile["model"]["checkpoint"]["sha256"])
        self.assertEqual(
            self.profile["model"]["checkpoint"]["verificationStatus"],
            "unverified",
        )
        self.assertEqual(self.profile["validationStatus"], "pending_local_validation")

    def test_prompt_baseline_has_no_implicit_safe_token(self):
        self.assertEqual(
            self.profile["prompting"]["positivePrefix"],
            ["masterpiece", "best quality", "score_7"],
        )
        self.assertNotIn(
            "safe", [item.casefold() for item in self.profile["prompting"]["positivePrefix"]]
        )
        self.assertEqual(
            model_profiles.compile_positive_prefix(self.profile),
            "masterpiece, best quality, score_7",
        )
        self.assertEqual(
            model_profiles.compile_positive_prefix(
                self.profile, optional_enhancements=["very aesthetic"]
            ),
            "masterpiece, best quality, score_7, very aesthetic",
        )

    def test_parameter_baseline_matches_product_requirements(self):
        parameters = self.profile["parameters"]
        self.assertEqual(
            parameters["defaults"],
            {"sampler": "Euler", "scheduler": "Normal", "steps": 30, "cfg": 5.5},
        )
        self.assertEqual(parameters["recommendedRanges"]["cfg"], {"min": 4.0, "max": 6.0})
        self.assertEqual(
            parameters["samplerSchedulerPairs"],
            [
                {"sampler": "Euler", "scheduler": "Normal"},
                {"sampler": "Euler a", "scheduler": "Normal"},
            ],
        )

    def test_unverified_sizes_are_candidates_not_automatic_recommendations(self):
        self.assertEqual(model_profiles.validated_resolution_presets(self.profile), [])
        candidates = model_profiles.candidate_resolution_presets(self.profile)
        self.assertEqual(len(candidates), 5)
        self.assertTrue(all(not item["autoRecommend"] for item in candidates))
        self.assertTrue(
            all(
                item["verificationStatus"]
                == "author_reported_pending_local_validation"
                for item in candidates
            )
        )

    def test_recipe_helpers_never_invent_size_seed_filename_or_hash(self):
        reference = model_profiles.model_reference(self.profile)
        defaults = model_profiles.profile_default_parameters(self.profile)
        self.assertEqual(reference["versionId"], 3004063)
        self.assertIsNone(reference["checkpoint"]["filename"])
        self.assertIsNone(reference["checkpoint"]["sha256"])
        self.assertEqual(set(defaults), {"sampler", "scheduler", "steps", "cfg"})
        self.assertNotIn("resolution", defaults)
        self.assertNotIn("generationSeed", defaults)

    def test_profile_hash_is_stable_for_object_key_order(self):
        reordered = {key: self.profile[key] for key in reversed(self.profile)}
        self.assertEqual(
            model_profiles.model_profile_hash(self.profile),
            model_profiles.model_profile_hash(reordered),
        )
        self.assertEqual(len(model_profiles.model_profile_hash(self.profile)), 64)

    def test_listing_returns_the_bundled_profile(self):
        profiles = model_profiles.list_model_profiles()
        self.assertIn("anima-1.1-v1", [item["profileId"] for item in profiles])


class ModelProfileAdversarialTests(unittest.TestCase):
    def test_researched_profile_validator_rejects_unknown_fields(self):
        import model_research

        profile = model_research.build_pending_profile(
            "https://civitai.com/models/1",
            [],
            [],
            manual_fields={"displayName": "Pending"},
        )
        profile["parameters"]["invented"] = 7
        with self.assertRaisesRegex(model_profiles.ModelProfileError, "unsupported"):
            model_profiles.validate_researched_model_profile(profile)

    def test_researched_profile_helpers_preserve_sparse_unverified_values(self):
        import model_research

        profile = model_research.build_pending_profile(
            "https://civitai.com/models/1",
            [],
            [],
            manual_fields={"displayName": "Pending"},
        )
        self.assertEqual(model_profiles.profile_default_parameters(profile), {})
        self.assertEqual(model_profiles.validated_resolution_presets(profile), [])
        self.assertEqual(model_profiles.candidate_resolution_presets(profile), [])
        self.assertEqual(model_profiles.compile_positive_prefix(profile), "")

    def test_researched_profile_revalidates_claim_audit_evidence_labels(self):
        import model_research

        profile = model_research.build_pending_profile(
            "https://civitai.com/models/1",
            [],
            [],
            manual_fields={"displayName": "Pending"},
        )
        profile["metadata"]["research"]["claimAudit"][0]["evidenceClass"] = "official"
        with self.assertRaisesRegex(model_profiles.ModelProfileError, "evidenceClass"):
            model_profiles.validate_researched_model_profile(profile)

    def test_researched_profile_audit_must_bind_to_matching_snapshot_and_allowlisted_field(self):
        import model_research

        snapshot = model_research.SourceSnapshot(
            snapshot_id="snapshot-1",
            source_class="original_source",
            requested_url="https://civitai.com/models/1",
            final_url="https://civitai.com/models/1",
            retrieved_at="2026-07-26T10:00:00Z",
            content_type="application/json",
            body_sha256="a" * 64,
            extracted_text="{}",
            fetch_status="succeeded",
            error_code=None,
        )
        claim = model_research.EvidenceClaim(
            claim_id="claim-1",
            field_path="parameters.defaults.steps",
            value=28,
            evidence_class="original_source",
            evidence_refs=("snapshot-1",),
            rationale="source",
            verification_status="source_recorded",
            application_status="approved",
        )
        profile = model_research.build_pending_profile(
            snapshot.requested_url, [snapshot], [claim]
        )
        invalid_profiles = []
        missing = copy.deepcopy(profile)
        missing["metadata"]["research"]["claimAudit"][0]["evidenceRefs"] = ["missing"]
        invalid_profiles.append(missing)
        mismatch = copy.deepcopy(profile)
        mismatch["evidence"][0]["sourceClass"] = "supplemental_source"
        invalid_profiles.append(mismatch)
        bad_path = copy.deepcopy(profile)
        bad_path["metadata"]["research"]["claimAudit"][0]["fieldPath"] = "checkpoint.downloadUrl"
        invalid_profiles.append(bad_path)
        for value in invalid_profiles:
            with self.subTest(value=value), self.assertRaises(model_profiles.ModelProfileError):
                model_profiles.validate_researched_model_profile(value)

    def test_researched_profile_cannot_self_declare_local_validation(self):
        import model_research

        profile = model_research.build_pending_profile(
            "https://civitai.com/models/1", [], [], manual_fields={"displayName": "Pending"}
        )
        profile["validationStatus"] = "locally_validated"
        profile["model"]["checkpoint"] = {
            "filename": "model.safetensors",
            "sha256": "a" * 64,
            "verificationStatus": "locally_validated",
        }
        profile["parameters"]["verificationStatus"] = "locally_validated"
        with self.assertRaisesRegex(model_profiles.ModelProfileError, "local_validation evidence"):
            model_profiles.validate_researched_model_profile(profile)

    def test_researched_candidate_presets_use_strict_resolution_validation(self):
        import model_research

        profile = model_research.build_pending_profile(
            "https://civitai.com/models/1", [], [], manual_fields={"displayName": "Pending"}
        )
        profile["resolutions"]["candidatePresets"] = [{
            "id": "square",
            "width": 1024,
            "height": 1024,
            "label": "Square",
            "verificationStatus": "project_candidate_pending_local_validation",
            "autoRecommend": False,
        }]
        with self.assertRaisesRegex(model_profiles.ModelProfileError, "evidenceRef"):
            model_profiles.validate_researched_model_profile(profile)

    def test_validator_rejects_safe_in_the_fixed_prefix(self):
        profile = model_profiles.load_model_profile()
        profile["prompting"]["positivePrefix"].append("safe")
        with self.assertRaisesRegex(model_profiles.ModelProfileError, "must not add safe"):
            model_profiles.validate_model_profile(profile)

    def test_unverified_resolution_cannot_be_marked_auto_recommended(self):
        profile = model_profiles.load_model_profile()
        profile["resolutions"]["candidatePresets"][0]["autoRecommend"] = True
        with self.assertRaisesRegex(model_profiles.ModelProfileError, "cannot be auto"):
            model_profiles.validate_model_profile(profile)

        profile = model_profiles.load_model_profile()
        profile["resolutions"]["candidatePresets"][0][
            "verificationStatus"
        ] = "locally_validated"
        profile["resolutions"]["candidatePresets"][0]["autoRecommend"] = True
        with self.assertRaisesRegex(model_profiles.ModelProfileError, "candidate collection"):
            model_profiles.validate_model_profile(profile)

    def test_verified_checkpoint_cannot_omit_hash(self):
        profile = model_profiles.load_model_profile()
        profile["model"]["checkpoint"]["verificationStatus"] = "hash_verified"
        with self.assertRaisesRegex(model_profiles.ModelProfileError, "must include"):
            model_profiles.validate_model_profile(profile)

    def test_unknown_optional_enhancement_is_rejected(self):
        with self.assertRaisesRegex(model_profiles.ModelProfileError, "unknown optional"):
            model_profiles.compile_positive_prefix(
                model_profiles.load_model_profile(),
                optional_enhancements=["not in the profile"],
            )

    def test_temp_directory_loader_rejects_traversal_mismatch_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            with self.assertRaisesRegex(model_profiles.ModelProfileError, "invalid"):
                model_profiles.load_model_profile("../outside", directory=root)

            profile = model_profiles.load_model_profile()
            (root / "wrong-name.json").write_text(
                json.dumps(profile, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(model_profiles.ModelProfileError, "does not match"):
                model_profiles.load_model_profile("wrong-name", directory=root)

            (root / "duplicate.json").write_text(
                '{"kind":"anima_model_profile","kind":"duplicate"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(model_profiles.ModelProfileError, "duplicate JSON key"):
                model_profiles.load_model_profile("duplicate", directory=root)

    def test_loader_returns_independent_values(self):
        first = model_profiles.load_model_profile()
        second = model_profiles.load_model_profile()
        first["prompting"]["positivePrefix"][0] = "mutated"
        self.assertEqual(second["prompting"]["positivePrefix"][0], "masterpiece")


if __name__ == "__main__":
    unittest.main()
