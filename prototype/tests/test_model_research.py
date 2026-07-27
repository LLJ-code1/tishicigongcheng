import dataclasses
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import model_research  # noqa: E402
import model_profiles  # noqa: E402


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

    def claim(
        self,
        claim_id,
        field_path,
        value,
        *,
        evidence_class="original_source",
        application_status="proposed",
    ):
        return model_research.EvidenceClaim(
            claim_id=claim_id,
            field_path=field_path,
            value=value,
            evidence_class=evidence_class,
            evidence_refs=(self.snapshot.snapshot_id,)
            if evidence_class != "ai_inference"
            else (),
            rationale="test evidence",
            verification_status=(
                "source_recorded"
                if evidence_class in {"original_source", "supplemental_source"}
                else "unverified"
            ),
            application_status=application_status,
        )

    def test_partial_fetch_builds_usable_pending_profile(self):
        failed = dataclasses.replace(
            self.snapshot,
            fetch_status="failed",
            error_code="timeout",
            extracted_text="",
            body_sha256="",
        )
        profile = model_research.build_pending_profile(
            self.source_url,
            [failed],
            [],
            manual_fields={"displayName": "My Model", "model.versionName": "v1"},
        )

        self.assertEqual(profile["validationStatus"], "pending_local_validation")
        self.assertEqual(profile["prompting"]["positivePrefix"], [])
        self.assertEqual(profile["parameters"]["defaults"], {})
        self.assertEqual(profile["metadata"]["research"]["researchStatus"], "pending_verification")
        self.assertIn("research_fetch_failed", profile["metadata"]["research"]["warnings"])
        self.assertEqual(model_profiles.profile_default_parameters(profile), {})

    def test_only_approved_claims_are_projected_and_every_claim_is_audited(self):
        claims = [
            self.claim("official", "parameters.defaults.steps", 28),
            self.claim(
                "supplemental",
                "parameters.defaults.cfg",
                5,
                evidence_class="supplemental_source",
            ),
            self.claim(
                "inference",
                "parameters.defaults.sampler",
                "Euler",
                evidence_class="ai_inference",
            ),
        ]
        empty = model_research.build_pending_profile(
            self.source_url, [self.snapshot], []
        )
        profile, audit = model_research.apply_claim_decisions(
            empty,
            claims,
            {"official": "approved", "supplemental": "rejected", "inference": "proposed"},
            {},
        )

        self.assertEqual(profile["parameters"]["defaults"], {"steps": 28})
        self.assertEqual(
            {item["evidenceClass"] for item in audit},
            {"original_source", "supplemental_source", "ai_inference"},
        )
        self.assertEqual(
            {item["claimId"]: item["applicationStatus"] for item in audit},
            {"official": "approved", "supplemental": "rejected", "inference": "proposed"},
        )

    def test_approved_supplemental_and_inference_fields_keep_their_evidence_labels(self):
        claims = [
            self.claim(
                "supplemental-limit",
                "metadata.limitations",
                ["Needs more testing"],
                evidence_class="supplemental_source",
            ),
            self.claim(
                "inferred-negative",
                "prompting.notRecommended",
                ["overlong prose"],
                evidence_class="ai_inference",
            ),
        ]
        empty = model_research.build_pending_profile(self.source_url, [self.snapshot], [])
        profile, audit = model_research.apply_claim_decisions(
            empty,
            claims,
            {"supplemental-limit": "approved", "inferred-negative": "approved"},
            {},
        )

        self.assertEqual(profile["metadata"]["limitations"], ["Needs more testing"])
        self.assertEqual(profile["prompting"]["notRecommended"], ["overlong prose"])
        self.assertEqual(
            {item["claimId"]: item["evidenceClass"] for item in audit},
            {
                "supplemental-limit": "supplemental_source",
                "inferred-negative": "ai_inference",
            },
        )

    def test_manual_fields_are_user_supplied_and_identity_is_stable(self):
        manual = {"displayName": "Manual Name", "model.versionId": 22}
        first = model_research.build_pending_profile(
            self.source_url, [self.snapshot], [], manual_fields=manual
        )
        second = model_research.build_pending_profile(
            self.source_url,
            [dataclasses.replace(self.snapshot, snapshot_id="snap-new")],
            [],
            manual_fields=manual,
        )

        self.assertEqual(first["profileId"], second["profileId"])
        audit = first["metadata"]["research"]["claimAudit"]
        self.assertTrue(audit)
        self.assertTrue(all(item["evidenceClass"] == "user_supplied" for item in audit))

    def test_candidate_dimensions_never_become_validated_and_no_local_evidence_means_pending(self):
        candidate = self.claim(
            "size",
            "resolutions.candidatePresets",
            [
                {
                    "id": "square",
                    "width": 1024,
                    "height": 1024,
                    "label": "Square",
                }
            ],
        )
        profile = model_research.build_pending_profile(
            self.source_url, [self.snapshot], [candidate]
        )
        projected, _ = model_research.apply_claim_decisions(
            profile, [candidate], {"size": "approved"}, {}
        )

        self.assertEqual(projected["resolutions"]["validatedPresets"], [])
        self.assertFalse(projected["resolutions"]["candidatePresets"][0]["autoRecommend"])
        projected["validationStatus"] = "locally_validated"
        with self.assertRaisesRegex(
            model_profiles.ModelProfileError, "local validation evidence"
        ):
            model_profiles.validate_researched_model_profile(projected)

    def test_reapplying_decisions_clears_rejected_claims_and_removed_manual_fields(self):
        steps = self.claim("steps", "parameters.defaults.steps", 28)
        initial = model_research.build_pending_profile(
            self.source_url,
            [self.snapshot],
            [],
            manual_fields={"displayName": "Temporary", "model.versionId": 22},
        )
        approved, _ = model_research.apply_claim_decisions(
            initial, [steps], {"steps": "approved"}, {}
        )
        self.assertEqual(approved["parameters"]["defaults"]["steps"], 28)

        revised, audit = model_research.apply_claim_decisions(
            approved, [steps], {"steps": "rejected"}, {}
        )

        self.assertNotIn("steps", revised["parameters"]["defaults"])
        self.assertEqual(revised["displayName"], "Pending model research")
        self.assertIsNone(revised["model"]["versionId"])
        self.assertTrue(revised["profileId"].endswith("pending-ceed14790ac1"))
        self.assertEqual(
            {item["claimId"]: item["applicationStatus"] for item in audit},
            {"steps": "rejected"},
        )

    def test_candidate_claim_rejects_missing_stable_id(self):
        claim = self.claim(
            "size-no-id",
            "resolutions.candidatePresets",
            [{"width": 1024, "height": 1024, "label": "Square"}],
        )
        profile = model_research.build_pending_profile(
            self.source_url, [self.snapshot], []
        )
        with self.assertRaisesRegex(model_research.ResearchError, "ID"):
            model_research.apply_claim_decisions(
                profile, [claim], {"size-no-id": "approved"}, {}
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

    def test_by_hash_adapter_keeps_air_as_snapshot_evidence_not_profile_identity(self):
        sha256 = "a" * 64
        source_url = model_research.civitai_hash_source_url(sha256.upper())
        adapter = model_research.default_adapter_registry().resolve(source_url)
        snapshot = model_research.SourceSnapshot(
            snapshot_id="snap-by-hash",
            source_class="original_source",
            requested_url=source_url,
            final_url=source_url,
            retrieved_at="2026-07-27T10:00:00Z",
            content_type="application/json",
            body_sha256="b" * 64,
            extracted_text=json.dumps(
                {
                    "id": 42,
                    "modelId": 9,
                    "name": "exact checkpoint",
                    "baseModel": "Illustrious",
                    "trainedWords": ["local trigger"],
                    "files": [{"hashes": {"SHA256": sha256}}],
                    "air": "external-air-identifier",
                }
            ),
            fetch_status="succeeded",
            error_code=None,
        )

        claims = adapter.parse(snapshot)

        self.assertEqual(
            model_research.civitai_hash_model_page(snapshot),
            "https://civitai.com/models/9?modelVersionId=42",
        )
        self.assertEqual(
            {claim.field_path for claim in claims},
            {"model.versionId", "model.versionName", "model.baseModel"},
        )
        self.assertNotIn("external-air-identifier", str([claim.value for claim in claims]))

    def test_by_hash_rejects_non_sha256_and_failed_source(self):
        with self.assertRaises(model_research.ResearchError):
            model_research.civitai_hash_source_url("not-a-hash")
        failed = dataclasses.replace(
            self.snapshot,
            requested_url=model_research.civitai_hash_source_url("c" * 64),
            final_url=model_research.civitai_hash_source_url("c" * 64),
            fetch_status="failed",
            extracted_text="",
            body_sha256="",
        )
        with self.assertRaises(model_research.ResearchError):
            model_research.civitai_hash_model_page(failed)

    def test_adapter_keeps_only_a_positive_model_version_pin_and_selects_it(self):
        pinned_url = self.source_url + "?modelVersionId=23"
        payload = json.loads(self.snapshot.extracted_text)
        payload["modelVersions"].append(
            {"id": 23, "name": "pinned", "baseModel": "NoobAI"}
        )
        snapshot = dataclasses.replace(
            self.snapshot,
            requested_url=pinned_url,
            final_url=pinned_url,
            extracted_text=json.dumps(payload),
        )
        adapter = model_research.default_adapter_registry().resolve(pinned_url)

        self.assertEqual(adapter.request_for(pinned_url).url, pinned_url)
        claims = {claim.field_path: claim.value for claim in adapter.parse(snapshot)}
        self.assertEqual(claims["model.versionId"], 23)
        self.assertEqual(claims["model.versionName"], "pinned")

    def test_adapter_rejects_unapproved_or_malformed_civitai_queries(self):
        registry = model_research.default_adapter_registry()
        for query in (
            "?modelVersionId=0",
            "?modelVersionId=not-a-number",
            "?modelVersionId=23&foo=bar",
            "?foo=bar",
            "?modelVersionId=23&modelVersionId=24",
        ):
            with self.subTest(query=query), self.assertRaisesRegex(
                model_research.ResearchError, "unsupported_source"
            ):
                registry.resolve(self.source_url + query)

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


class ModelResearchFetchPolicyTests(unittest.TestCase):
    def setUp(self):
        self.url = "https://civitai.com/models/1"
        self.registry = model_research.default_adapter_registry()
        self.adapter = self.registry.resolve(self.url)
        self.request = self.adapter.request_for(self.url)
        self.public_resolver = lambda host: ["93.184.216.34"]
        self.clock = lambda: "2026-07-26T10:00:00Z"

    def fetch(self, fetcher, *, resolver=None):
        return model_research.fetch_snapshot(
            self.request,
            adapter=self.adapter,
            resolver=resolver or self.public_resolver,
            fetcher=fetcher,
            clock=self.clock,
        )

    def test_rejects_private_dns_before_fetch(self):
        calls = []
        with self.assertRaisesRegex(model_research.ResearchError, "unsafe_address"):
            self.fetch(lambda request: calls.append(request), resolver=lambda host: ["127.0.0.1"])
        self.assertEqual(calls, [])

    def test_rejects_all_non_global_and_mixed_dns_answers(self):
        unsafe_sets = (
            ["::1"],
            ["fe80::1"],
            ["fc00::1"],
            ["10.0.0.1"],
            ["169.254.1.1"],
            ["224.0.0.1"],
            ["240.0.0.1"],
            ["93.184.216.34", "127.0.0.1"],
            [object()],
            [],
        )
        for answers in unsafe_sets:
            with self.subTest(answers=answers), self.assertRaises(model_research.ResearchError):
                model_research.validate_fetch_url(self.url, lambda host, value=answers: value)

    def test_rejects_credentials_fragment_ports_ip_literals_and_non_https(self):
        bad_urls = (
            "https://user@civitai.com/models/1",
            "https://civitai.com/models/1#private",
            "https://civitai.com:8443/models/1",
            "https://127.0.0.1/models/1",
            "https://[::1]/models/1",
            "http://civitai.com/models/1",
        )
        for url in bad_urls:
            with self.subTest(url=url), self.assertRaises(model_research.ResearchError):
                model_research.validate_fetch_url(url, self.public_resolver)

    def test_revalidates_redirect_adapter_and_dns_before_next_fetch(self):
        calls = []

        def fetcher(request):
            calls.append(request.url)
            return model_research.FetchResponse(
                request.url,
                302,
                {"Location": "https://civitai.com/models/2"},
                b"",
            )

        def resolver(host):
            return ["93.184.216.34"] if len(calls) == 0 else ["127.0.0.1"]

        with self.assertRaisesRegex(model_research.ResearchError, "unsafe_address"):
            self.fetch(fetcher, resolver=resolver)
        self.assertEqual(calls, [self.url])

    def test_resolves_exactly_once_before_each_fetch_hop(self):
        fetch_calls = []
        resolve_calls = []

        def fetcher(request):
            fetch_calls.append(request.url)
            if len(fetch_calls) == 1:
                return model_research.FetchResponse(
                    request.url,
                    302,
                    {"Location": "https://civitai.com/models/2"},
                    b"",
                )
            return model_research.FetchResponse(
                request.url,
                200,
                {"Content-Type": "text/html"},
                b"ok",
            )

        self.fetch(
            fetcher,
            resolver=lambda host: (
                resolve_calls.append(host) or ["93.184.216.34"]
            ),
        )
        self.assertEqual(resolve_calls, ["civitai.com", "civitai.com"])
        self.assertEqual(fetch_calls, [self.url, "https://civitai.com/models/2"])

    def test_rejects_redirect_to_unsupported_or_encoded_target(self):
        targets = (
            "https://localhost/private",
            "//example.com/private",
            "https:%2f%2fcivitai.com/models/2",
        )
        for target in targets:
            with self.subTest(target=target), self.assertRaises(model_research.ResearchError):
                self.fetch(
                    lambda request, value=target: model_research.FetchResponse(
                        request.url, 302, {"Location": value}, b""
                    )
                )

    def test_rejects_sixth_redirect(self):
        calls = []

        def fetcher(request):
            calls.append(request.url)
            number = len(calls) + 1
            return model_research.FetchResponse(
                request.url, 302, {"Location": f"https://civitai.com/models/{number}"}, b""
            )

        with self.assertRaisesRegex(model_research.ResearchError, "too_many_redirects"):
            self.fetch(fetcher)
        self.assertEqual(len(calls), 6)

    def test_rejects_oversize_body_decoded_text_bad_type_encoding_and_status(self):
        cases = (
            (
                "response_too_large",
                model_research.FetchResponse(
                    self.url,
                    200,
                    {"Content-Type": "text/html; charset=utf-8"},
                    b"x" * (2 * 1024 * 1024 + 1),
                ),
            ),
            (
                "response_too_large",
                model_research.FetchResponse(
                    self.url,
                    200,
                    {"Content-Type": "text/html; charset=utf-8"},
                    ("\u4e00" * (1024 * 1024)).encode("utf-8"),
                ),
            ),
            (
                "unsupported_content_type",
                model_research.FetchResponse(
                    self.url, 200, {"Content-Type": "image/png"}, b"png"
                ),
            ),
            (
                "unsupported_encoding",
                model_research.FetchResponse(
                    self.url,
                    200,
                    {"Content-Type": "text/html; charset=latin-1"},
                    b"text",
                ),
            ),
            (
                "invalid_utf8",
                model_research.FetchResponse(
                    self.url,
                    200,
                    {"Content-Type": "application/json"},
                    b"\xff",
                ),
            ),
            (
                "unexpected_status",
                model_research.FetchResponse(
                    self.url, 404, {"Content-Type": "text/html"}, b"missing"
                ),
            ),
        )
        for code, response in cases:
            with self.subTest(code=code), self.assertRaisesRegex(
                model_research.ResearchError, code
            ):
                self.fetch(lambda request, value=response: value)

    def test_successful_response_is_bounded_utf8_snapshot(self):
        body = '{"name":"Example"}'.encode()
        seen_addresses = []
        snapshot = self.fetch(
            lambda request: (
                seen_addresses.append(request.resolved_addresses)
                or model_research.FetchResponse(
                    request.url,
                    200,
                    {"content-type": "application/json; charset=UTF-8"},
                    body,
                )
            )
        )
        self.assertEqual(seen_addresses, [("93.184.216.34",)])
        self.assertEqual(snapshot.fetch_status, "succeeded")
        self.assertEqual(snapshot.requested_url, self.url)
        self.assertEqual(snapshot.final_url, self.url)
        self.assertEqual(snapshot.content_type, "application/json")
        self.assertEqual(snapshot.extracted_text, body.decode())
        self.assertEqual(snapshot.body_sha256, __import__("hashlib").sha256(body).hexdigest())

    def test_transport_failure_returns_stable_failed_snapshot(self):
        def broken_fetcher(request):
            raise TimeoutError("secret upstream details")

        snapshot = self.fetch(broken_fetcher)
        self.assertEqual(snapshot.fetch_status, "failed")
        self.assertEqual(snapshot.error_code, "fetch_failed")
        self.assertEqual(snapshot.extracted_text, "")
        self.assertEqual(snapshot.final_url, self.url)

    def test_research_source_uses_only_injected_fetcher(self):
        calls = []
        result = model_research.research_source(
            model_research.ResearchRequest(self.url),
            registry=self.registry,
            resolver=self.public_resolver,
            fetcher=lambda request: (
                calls.append(request.url)
                or model_research.FetchResponse(
                    request.url,
                    200,
                    {"Content-Type": "application/json"},
                    b'{"name":"Example"}',
                )
            ),
            clock=self.clock,
        )
        self.assertEqual(calls, [self.url])
        self.assertEqual(result.snapshot.fetch_status, "succeeded")
        self.assertEqual(result.claims[0].field_path, "displayName")

    def test_production_transport_is_single_hop_bounded_and_strips_secrets(self):
        class Response:
            status = 302

            def getheaders(self):
                return [("Location", "/models/2")]

            def read(self, amount):
                self.amount = amount
                return b""

        class Connection:
            def __init__(self, host, pinned_address, port, timeout):
                self.init = (host, pinned_address, port, timeout)
                self.response = Response()
                connections.append(self)

            def request(self, method, target, headers):
                self.sent = (method, target, headers)

            def getresponse(self):
                return self.response

            def close(self):
                self.closed = True

        connections = []
        response = model_research.production_fetcher(
            model_research.FetchRequest(
                self.url + "?version=2",
                {
                    "Accept": "text/html",
                    "authorization": "secret",
                    "Cookie": "secret",
                    "accept-encoding": "gzip",
                },
                ("93.184.216.34",),
            ),
            connection_factory=Connection,
        )
        connection = connections[0]
        self.assertEqual(
            connection.init, ("civitai.com", "93.184.216.34", 443, 15)
        )
        self.assertEqual(connection.sent[0:2], ("GET", "/models/1?version=2"))
        sent_headers = {key.casefold(): value for key, value in connection.sent[2].items()}
        self.assertNotIn("authorization", sent_headers)
        self.assertNotIn("cookie", sent_headers)
        self.assertEqual(sent_headers["accept-encoding"], "identity")
        self.assertEqual(connection.response.amount, 2 * 1024 * 1024 + 1)
        self.assertTrue(connection.closed)
        self.assertEqual(response.status, 302)

    def test_fetch_to_production_transport_pins_the_single_audited_dns_answer(self):
        resolve_calls = []
        connections = []

        class Response:
            status = 200

            def getheaders(self):
                return [("Content-Type", "text/html")]

            def read(self, amount):
                return b"ok"

        class Connection:
            def __init__(self, host, pinned_address, port, timeout):
                connections.append((host, pinned_address, port, timeout))

            def request(self, method, target, headers):
                pass

            def getresponse(self):
                return Response()

            def close(self):
                pass

        def resolver(host):
            resolve_calls.append(host)
            if len(resolve_calls) == 1:
                return ["93.184.216.34"]
            return ["127.0.0.1"]

        snapshot = model_research.fetch_snapshot(
            self.request,
            adapter=self.adapter,
            resolver=resolver,
            fetcher=lambda request: model_research.production_fetcher(
                request, connection_factory=Connection
            ),
            clock=self.clock,
        )

        self.assertEqual(snapshot.fetch_status, "succeeded")
        self.assertEqual(resolve_calls, ["civitai.com"])
        self.assertEqual(
            connections,
            [("civitai.com", "93.184.216.34", 443, 15)],
        )

    def test_pinned_connection_uses_numeric_tcp_peer_and_original_tls_sni(self):
        class RawSocket:
            def settimeout(self, timeout):
                self.timeout = timeout

            def connect(self, address):
                self.address = address

            def close(self):
                self.closed = True

        class Context:
            def wrap_socket(self, raw_socket, *, server_hostname):
                self.wrapped = (raw_socket, server_hostname)
                return "tls-socket"

        raw_socket = RawSocket()
        context = Context()
        connection = model_research._PinnedHTTPSConnection(
            "civitai.com", "93.184.216.34", 443, 15
        )
        connection._context = context

        with mock.patch.object(
            model_research.socket, "socket", return_value=raw_socket
        ) as socket_factory:
            connection.connect()

        socket_factory.assert_called_once_with(
            model_research.socket.AF_INET, model_research.socket.SOCK_STREAM
        )
        self.assertEqual(raw_socket.timeout, 15)
        self.assertEqual(raw_socket.address, ("93.184.216.34", 443))
        self.assertEqual(context.wrapped, (raw_socket, "civitai.com"))
        self.assertEqual(connection.sock, "tls-socket")


if __name__ == "__main__":
    unittest.main()
