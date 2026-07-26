import dataclasses
import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import model_research  # noqa: E402


class ModelResearchContractTests(unittest.TestCase):
    def setUp(self):
        self.source_url = "https://civitai.com/models/934764/miaomiao-harem"
        self.snapshot = model_research.SourceSnapshot(
            snapshot_id="snap-1",
            source_class="original_source",
            requested_url=self.source_url,
            final_url=self.source_url,
            retrieved_at="2026-07-26T10:00:00Z",
            content_type="application/json",
            body_sha256="a" * 64,
            extracted_text=json.dumps(
                {
                    "name": "Example",
                    "modelVersions": [
                        {
                            "id": 22,
                            "name": "v1",
                            "baseModel": "Illustrious",
                            "description": "Use Euler, 28 steps, CFG 5.",
                        }
                    ],
                }
            ),
            fetch_status="succeeded",
            error_code=None,
        )

    def test_contract_records_are_immutable(self):
        request = model_research.ResearchRequest(source_url=self.source_url)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            request.source_url = "https://civitai.com/models/1"

    def test_registry_accepts_only_registered_original_page(self):
        registry = model_research.default_adapter_registry()
        adapter = registry.resolve(self.source_url)
        self.assertEqual(adapter.adapter_id, "civitai-model-page-v1")

        rejected = (
            "https://example.com/models/1",
            "https://civitai.com/images/1",
            "https://civitai.com/models/0",
            "https://civitai.com/models/1/extra/path",
        )
        for url in rejected:
            with self.subTest(url=url), self.assertRaisesRegex(
                model_research.ResearchError, "unsupported_source"
            ):
                registry.resolve(url)

    def test_adapter_request_keeps_canonical_original_page_and_bounded_accept_header(self):
        adapter = model_research.default_adapter_registry().resolve(self.source_url)
        request = adapter.request_for(self.source_url)
        self.assertEqual(request.url, self.source_url)
        self.assertEqual(
            dict(request.headers),
            {"Accept": "application/json, text/html; q=0.9"},
        )

    def test_adapter_maps_allowlisted_author_fields_to_original_source_claims(self):
        claims = model_research.default_adapter_registry().resolve(
            self.snapshot.requested_url
        ).parse(self.snapshot)
        by_path = {claim.field_path: claim for claim in claims}

        self.assertEqual(by_path["displayName"].value, "Example")
        self.assertEqual(by_path["model.versionId"].value, 22)
        self.assertEqual(by_path["model.versionName"].value, "v1")
        self.assertEqual(by_path["model.baseModel"].value, "Illustrious")
        self.assertEqual(
            by_path["model.versionId"].evidence_class, "original_source"
        )
        self.assertEqual(
            by_path["model.versionId"].application_status, "proposed"
        )
        self.assertEqual(by_path["model.versionId"].evidence_refs, ("snap-1",))
        self.assertNotIn("parameters.defaults.steps", by_path)
        self.assertNotIn("parameters.defaults.cfg", by_path)

    def test_adapter_ignores_unknown_page_keys(self):
        payload = json.loads(self.snapshot.extracted_text)
        payload["secret"] = "must not become a claim"
        payload["modelVersions"][0]["downloadUrl"] = "https://example.invalid/file"
        snapshot = dataclasses.replace(
            self.snapshot, extracted_text=json.dumps(payload)
        )

        claims = model_research.default_adapter_registry().resolve(
            snapshot.requested_url
        ).parse(snapshot)

        self.assertTrue(claims)
        self.assertTrue(
            all(claim.field_path in model_research.ALLOWED_CLAIM_PATHS for claim in claims)
        )

    def test_adapter_never_upgrades_supplemental_snapshot_to_original_claims(self):
        snapshot = dataclasses.replace(
            self.snapshot,
            source_class="supplemental_source",
        )
        with self.assertRaisesRegex(model_research.ResearchError, "source_mismatch"):
            model_research.default_adapter_registry().resolve(
                snapshot.requested_url
            ).parse(snapshot)

    def test_adapter_rejects_snapshot_whose_redirect_final_url_is_unsupported(self):
        snapshot = dataclasses.replace(
            self.snapshot,
            final_url="https://example.com/models/934764/miaomiao-harem",
        )
        with self.assertRaisesRegex(model_research.ResearchError, "source_mismatch"):
            model_research.default_adapter_registry().resolve(
                snapshot.requested_url
            ).parse(snapshot)

    def test_claim_rejects_unbacked_official_label(self):
        with self.assertRaisesRegex(model_research.ResearchError, "missing_evidence"):
            model_research.normalize_claim(
                {
                    "claimId": "claim-1",
                    "fieldPath": "parameters.defaults.steps",
                    "value": 28,
                    "evidenceClass": "original_source",
                    "evidenceRefs": [],
                    "rationale": "page says so",
                    "verificationStatus": "source_recorded",
                    "applicationStatus": "proposed",
                }
            )

    def test_claim_rejects_unknown_field_and_lifecycle_values(self):
        base = {
            "claimId": "claim-1",
            "fieldPath": "parameters.defaults.steps",
            "value": 28,
            "evidenceClass": "original_source",
            "evidenceRefs": ["snap-1"],
            "rationale": "structured source field",
            "verificationStatus": "source_recorded",
            "applicationStatus": "proposed",
        }
        invalid_cases = (
            {**base, "fieldPath": "checkpoint.downloadUrl"},
            {**base, "evidenceClass": "official"},
            {**base, "applicationStatus": "active"},
        )
        for value in invalid_cases:
            with self.subTest(value=value), self.assertRaises(
                model_research.ResearchError
            ):
                model_research.normalize_claim(value)

    def test_direct_claim_construction_cannot_bypass_evidence_validation(self):
        with self.assertRaisesRegex(model_research.ResearchError, "missing_evidence"):
            model_research.EvidenceClaim(
                claim_id="claim-direct",
                field_path="parameters.defaults.steps",
                value=28,
                evidence_class="original_source",
                evidence_refs=(),
                rationale="structured source field",
                verification_status="source_recorded",
                application_status="proposed",
            )

    def test_claim_rejects_non_json_or_oversized_values(self):
        invalid_values = (
            object(),
            {"items": {1, 2}},
            float("nan"),
            "x" * (64 * 1024 + 1),
        )
        for claim_value in invalid_values:
            with self.subTest(claim_value=type(claim_value).__name__), self.assertRaisesRegex(
                model_research.ResearchError, "invalid_claim_value"
            ):
                model_research.EvidenceClaim(
                    claim_id="claim-direct",
                    field_path="parameters.defaults.steps",
                    value=claim_value,
                    evidence_class="ai_inference",
                    evidence_refs=(),
                    rationale="inferred pending review",
                    verification_status="unverified",
                    application_status="proposed",
                )

    def test_claim_to_dict_revalidates_even_a_low_level_bypassed_instance(self):
        claim = object.__new__(model_research.EvidenceClaim)
        values = {
            "claim_id": "claim-bypassed",
            "field_path": "checkpoint.downloadUrl",
            "value": "https://example.invalid/model",
            "evidence_class": "official",
            "evidence_refs": (),
            "rationale": "",
            "verification_status": "source_recorded",
            "application_status": "approved",
        }
        for field, value in values.items():
            object.__setattr__(claim, field, value)

        with self.assertRaises(model_research.ResearchError):
            model_research.claim_to_dict(claim)

    def test_claim_defensively_freezes_nested_value_without_aliasing_input(self):
        original = {"presets": [{"width": 1024, "tags": ["portrait"]}]}
        claim = model_research.EvidenceClaim(
            claim_id="claim-nested",
            field_path="resolutions.candidatePresets",
            value=original,
            evidence_class="original_source",
            evidence_refs=("snap-1",),
            rationale="structured source field",
            verification_status="source_recorded",
            application_status="proposed",
        )

        original["presets"][0]["width"] = 1
        original["presets"][0]["tags"].append("mutated")

        exported = model_research.claim_to_dict(claim)
        self.assertEqual(
            exported["value"],
            {"presets": [{"width": 1024, "tags": ["portrait"]}]},
        )
        with self.assertRaises(TypeError):
            claim.value["presets"] = ()
        with self.assertRaises(TypeError):
            claim.value["presets"][0]["width"] = 1

    def test_claim_export_returns_deep_copy_that_cannot_mutate_claim(self):
        claim = model_research.normalize_claim(
            {
                "claimId": "claim-export",
                "fieldPath": "metadata.strengths",
                "value": ["hands", {"lighting": ["soft"]}],
                "evidenceClass": "original_source",
                "evidenceRefs": ["snap-1"],
                "rationale": "structured source field",
                "verificationStatus": "source_recorded",
                "applicationStatus": "proposed",
            }
        )
        exported = model_research.claim_to_dict(claim)
        exported["value"][0] = "mutated"
        exported["value"][1]["lighting"].append("hard")

        second_export = model_research.claim_to_dict(claim)
        self.assertEqual(
            second_export["value"],
            ["hands", {"lighting": ["soft"]}],
        )
        self.assertEqual(
            model_research.normalize_claim(claim),
            claim,
        )


if __name__ == "__main__":
    unittest.main()
