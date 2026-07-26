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
        snapshot = self.fetch(
            lambda request: model_research.FetchResponse(
                request.url,
                200,
                {"content-type": "application/json; charset=UTF-8"},
                body,
            )
        )
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
            def __init__(self, host, port, timeout):
                self.init = (host, port, timeout)
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
            ),
            connection_factory=Connection,
        )
        connection = connections[0]
        self.assertEqual(connection.init, ("civitai.com", 443, 15))
        self.assertEqual(connection.sent[0:2], ("GET", "/models/1?version=2"))
        sent_headers = {key.casefold(): value for key, value in connection.sent[2].items()}
        self.assertNotIn("authorization", sent_headers)
        self.assertNotIn("cookie", sent_headers)
        self.assertEqual(sent_headers["accept-encoding"], "identity")
        self.assertEqual(connection.response.amount, 2 * 1024 * 1024 + 1)
        self.assertTrue(connection.closed)
        self.assertEqual(response.status, 302)


if __name__ == "__main__":
    unittest.main()
