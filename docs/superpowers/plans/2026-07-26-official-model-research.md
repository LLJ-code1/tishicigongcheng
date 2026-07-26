# Official Model Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user paste a model’s original official page, safely research it through approved source adapters, review evidence-backed claims, and activate an immutable model-profile version without blocking the creative flow when research is incomplete.

**Architecture:** Add a standalone research boundary beside the existing read-only bundled-profile loader. A strict adapter registry turns supported official URLs into bounded fetch requests; a network guard validates the initial URL, DNS results, and every redirect before an injected fetcher runs. Immutable source snapshots and claims are stored separately from immutable profile versions; draft review and activation create new records rather than editing history, while the existing `GET /api/model-profiles` projects only active versions plus bundled fallback profiles.

**Tech Stack:** Python 3 standard library, SQLite, existing `http.server` API, vanilla JavaScript/CSS, Python `unittest`, Node `node:test`.

## Global Constraints

- The main product is `prototype/`; `AnimaDex/` remains read-only.
- This is an independent Phase A. It must not implement thirteen-block decomposition, LoRA research, model execution, or local checkpoint validation.
- The only accepted input is an `https://` original model-page URL handled by a registered source adapter. Do not provide an unrestricted URL fetch endpoint.
- Reject URL credentials, fragments, non-HTTPS schemes, non-default ports, localhost names, IP-literal hosts, unsupported hosts, DNS answers in non-global ranges, and redirects that fail the same checks.
- Fetch at most 5 redirects, 2 MiB compressed response bytes, 2 MiB decoded text, and 15 seconds per request. Accept only UTF-8 `text/html` or `application/json`.
- Automated tests must inject a fake resolver, clock, and fetcher. They must never access the real network or depend on a live website.
- Every research conclusion is a claim with one evidence class: `original_source`, `supplemental_source`, `ai_inference`, or `local_validation`. Supplemental or inferred claims must never be labeled official.
- Only claims with `applicationStatus="approved"` may affect a profile. Research-created claims start `proposed`; claims with missing evidence stay visibly unverified.
- Evidence snapshots and activated profile versions are immutable. Review creates a reviewed version; activation changes only lifecycle pointers/status, never profile JSON or claim JSON.
- Failed or partial research produces a recoverable `pending_verification` draft and does not block the user from selecting an already active model or continuing with generic rules.
- Continue using `POST /api/workspace/commit` for workspace persistence. Research/profile lifecycle APIs may write their own isolated tables because they are library records, not a workspace revision.
- Do not commit API keys, `.env`, SQLite files, logs, PID files, virtual environments, downloaded pages, images, or model files.
- Update Python and Node tests for every behavior change; no automated test may require a real external API.

---

## File Structure

- `prototype/model_research.py` — strict request/response contracts, adapter registry, URL/DNS/redirect policy, bounded fetch orchestration, source parsing, claim normalization, and draft-profile projection.
- `prototype/model_profile_store.py` — SQLite repository for immutable research runs, snapshots, claims, profile versions, review records, and active-version lookup.
- `prototype/model_profiles.py` — retain bundled profile validation; add validation for research metadata and loading of an explicitly supplied active-profile snapshot.
- `prototype/db.py` — add research/profile-version tables and schema-integrity declarations only; do not mix research writes into project commits.
- `prototype/server.py` — dependency-injected research service, lifecycle APIs, errors, and merged active/bundled profile catalog.
- `prototype/app.js` — research drawer state/reducer/actions, API orchestration, review/activation, and model-list refresh.
- `prototype/index.html` — original-link form, evidence/claim review surface, manual supplement fields, and non-blocking status UI.
- `prototype/styles.css` — research drawer, evidence badges, claim controls, error/recovery, and narrow-screen layout.
- `prototype/tests/test_model_research.py` — adapters, SSRF guard, redirects, limits, parsing, evidence classes, and partial failure.
- `prototype/tests/test_model_profile_store.py` — immutable persistence, review/activation, concurrency, and active lookup.
- `prototype/tests/test_model_profiles.py` — research-profile validation and bundled/active compatibility.
- `prototype/tests/test_server.py` — HTTP contracts and fake-fetch integration.
- `prototype/tests/app.test.js` — reducer/render/request/retry/activate behavior.
- `docs/architecture.md`, `docs/data-model.md`, `docs/integration-guide.md`, `docs/operator-runbook.md`, `docs/roadmap.md` — current architecture, schema, API, failure recovery, and completion status.

---

### Task 1: Define the research contracts and adapter-only source parsing

**Files:**
- Create: `prototype/model_research.py`
- Create: `prototype/tests/test_model_research.py`

**Interfaces:**
- Produces: `ResearchError(code: str, message: str)`.
- Produces: `ResearchRequest(source_url: str)`.
- Produces: `FetchRequest(url: str, headers: Mapping[str, str])`.
- Produces: `FetchResponse(url: str, status: int, headers: Mapping[str, str], body: bytes)`.
- Produces: `SourceSnapshot(snapshot_id, source_class, requested_url, final_url, retrieved_at, content_type, body_sha256, extracted_text, fetch_status, error_code)`.
- Produces: `EvidenceClaim(claim_id, field_path, value, evidence_class, evidence_refs, rationale, verification_status, application_status)`.
- Produces: `SourceAdapter.supports(url) -> bool`, `request_for(url) -> FetchRequest`, and `parse(snapshot) -> list[EvidenceClaim]`.
- Produces: `AdapterRegistry.resolve(url: str) -> SourceAdapter`.
- Consumes later: Task 3 calls `research_source(request, registry, fetcher, resolver, clock)`.

- [ ] **Step 1: Write failing contract and adapter tests**

```python
class ModelResearchContractTests(unittest.TestCase):
    def test_registry_accepts_only_registered_original_page(self):
        registry = model_research.default_adapter_registry()
        adapter = registry.resolve(
            "https://civitai.com/models/934764/miaomiao-harem"
        )
        self.assertEqual(adapter.adapter_id, "civitai-model-page-v1")
        with self.assertRaisesRegex(
            model_research.ResearchError, "unsupported_source"
        ):
            registry.resolve("https://example.com/models/1")

    def test_adapter_maps_author_fields_to_original_source_claims(self):
        snapshot = model_research.SourceSnapshot(
            snapshot_id="snap-1",
            source_class="original_source",
            requested_url="https://civitai.com/models/934764/example",
            final_url="https://civitai.com/models/934764/example",
            retrieved_at="2026-07-26T10:00:00Z",
            content_type="application/json",
            body_sha256="a" * 64,
            extracted_text=json.dumps({
                "name": "Example",
                "modelVersions": [{
                    "id": 22,
                    "name": "v1",
                    "baseModel": "Illustrious",
                    "description": "Use Euler, 28 steps, CFG 5."
                }]
            }),
            fetch_status="succeeded",
            error_code=None,
        )
        claims = model_research.default_adapter_registry().resolve(
            snapshot.requested_url
        ).parse(snapshot)
        by_path = {claim.field_path: claim for claim in claims}
        self.assertEqual(by_path["model.versionId"].value, 22)
        self.assertEqual(
            by_path["model.versionId"].evidence_class, "original_source"
        )
        self.assertEqual(
            by_path["model.versionId"].application_status, "proposed"
        )
        self.assertEqual(by_path["model.versionId"].evidence_refs, ("snap-1",))

    def test_claim_rejects_unbacked_official_label(self):
        with self.assertRaisesRegex(
            model_research.ResearchError, "missing_evidence"
        ):
            model_research.normalize_claim({
                "claimId": "claim-1",
                "fieldPath": "parameters.defaults.steps",
                "value": 28,
                "evidenceClass": "original_source",
                "evidenceRefs": [],
                "rationale": "page says so",
                "verificationStatus": "source_recorded",
                "applicationStatus": "proposed",
            })
```

- [ ] **Step 2: Run the focused test to verify RED**

Run:

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_model_research -v
```

Expected: FAIL because `model_research` and its contracts do not exist.

- [ ] **Step 3: Implement strict dataclasses, normalizers, and the first adapter**

Implement frozen dataclasses and JSON conversion helpers. The first registry entry is `civitai-model-page-v1`, matching only:

```python
re.fullmatch(r"/models/[1-9][0-9]*(?:/[A-Za-z0-9._~-]+)?/?", parsed.path)
```

Its request must target the same canonical page URL; parsing may read embedded JSON or JSON returned by a fake fetcher, but must emit only these allowlisted field paths:

```python
ALLOWED_CLAIM_PATHS = {
    "displayName",
    "model.family",
    "model.versionName",
    "model.versionId",
    "model.baseModel",
    "prompting.positivePrefix",
    "prompting.positiveSuffix",
    "prompting.negativeDefault",
    "prompting.notRecommended",
    "parameters.defaults.sampler",
    "parameters.defaults.scheduler",
    "parameters.defaults.steps",
    "parameters.defaults.cfg",
    "parameters.recommendedRanges.cfg",
    "resolutions.candidatePresets",
    "metadata.strengths",
    "metadata.weaknesses",
    "metadata.limitations",
}
```

Unknown page keys are ignored. Page prose may be preserved as snapshot text, but must not be promoted to structured fields unless the adapter has an explicit deterministic extractor. All claims begin as `application_status="proposed"`.

- [ ] **Step 4: Run the focused test to verify GREEN**

Run the Task 1 command. Expected: all tests PASS.

- [ ] **Step 5: Commit the contract boundary**

```powershell
git add -- prototype/model_research.py prototype/tests/test_model_research.py
git commit -m "feat: define model research evidence contracts"
```

---

### Task 2: Enforce SSRF-safe bounded fetching with an injected transport

**Files:**
- Modify: `prototype/model_research.py`
- Modify: `prototype/tests/test_model_research.py`

**Interfaces:**
- Consumes: Task 1 `FetchRequest`, `FetchResponse`, `AdapterRegistry`.
- Produces: `validate_fetch_url(url: str, resolver: Resolver) -> str`.
- Produces: `fetch_snapshot(request, *, adapter, fetcher, resolver, clock) -> SourceSnapshot`.
- `Resolver(host: str) -> Sequence[str]` and `Fetcher(request: FetchRequest) -> FetchResponse` are injected callables.
- `research_source(...)` must call only the injected `fetcher`; no module-level network request is allowed.

- [ ] **Step 1: Write failing SSRF, redirect, and limit tests**

```python
def test_rejects_private_dns_before_fetch(self):
    calls = []
    with self.assertRaisesRegex(model_research.ResearchError, "unsafe_address"):
        model_research.fetch_snapshot(
            model_research.FetchRequest(
                "https://civitai.com/models/1", {"Accept": "text/html"}
            ),
            adapter=self.registry.resolve("https://civitai.com/models/1"),
            resolver=lambda host: ["127.0.0.1"],
            fetcher=lambda request: calls.append(request),
            clock=lambda: "2026-07-26T10:00:00Z",
        )
    self.assertEqual(calls, [])

def test_revalidates_redirect_target_and_dns(self):
    responses = iter([
        model_research.FetchResponse(
            "https://civitai.com/models/1", 302,
            {"Location": "https://localhost/private"}, b""
        )
    ])
    with self.assertRaisesRegex(model_research.ResearchError, "unsupported_source"):
        model_research.fetch_snapshot(
            self.request,
            adapter=self.registry.resolve(self.request.url),
            resolver=lambda host: ["93.184.216.34"],
            fetcher=lambda request: next(responses),
            clock=lambda: "2026-07-26T10:00:00Z",
        )

def test_rejects_credentials_ports_ip_literals_and_oversize_body(self):
    bad = (
        "https://user@civitai.com/models/1",
        "https://civitai.com:8443/models/1",
        "https://127.0.0.1/models/1",
    )
    for url in bad:
        with self.subTest(url=url), self.assertRaises(model_research.ResearchError):
            model_research.validate_fetch_url(url, lambda host: ["93.184.216.34"])
    with self.assertRaisesRegex(model_research.ResearchError, "response_too_large"):
        model_research.fetch_snapshot(
            self.request,
            adapter=self.registry.resolve(self.request.url),
            resolver=lambda host: ["93.184.216.34"],
            fetcher=lambda request: model_research.FetchResponse(
                request.url, 200, {"Content-Type": "text/html; charset=utf-8"},
                b"x" * (2 * 1024 * 1024 + 1),
            ),
            clock=lambda: "2026-07-26T10:00:00Z",
        )
```

Also cover IPv6 loopback/link-local/ULA, IPv4 private/link-local/multicast/reserved, mixed public/private DNS answers, encoded or scheme-relative redirect targets, six redirects, non-UTF-8, unsupported content type, HTTP status outside 200–299, and a successful bounded response.

- [ ] **Step 2: Run the focused tests to verify RED**

Run the Task 1 command. Expected: FAIL at the missing fetch policy.

- [ ] **Step 3: Implement the guard and transport loop**

Use `urllib.parse.urlsplit`, `ipaddress.ip_address`, and `socket.getaddrinfo` only behind the injected default resolver. Require `address.is_global` for every answer. Resolve and validate before each fetch. Resolve redirect URLs with `urljoin`, then require the same adapter to support the target before following it.

The production fetcher may use `http.client.HTTPSConnection` with a 15-second timeout, `Accept-Encoding: identity`, and manual redirect handling. It must read at most `MAX_RESPONSE_BYTES + 1`; do not use `urllib.request.urlopen`, because its automatic redirect behavior would bypass per-hop validation.

On fetch failure, return a snapshot with `fetch_status="failed"`, empty `extracted_text`, a stable `error_code`, and the canonical requested URL. Policy violations raise `ResearchError` and create no network call or snapshot.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run the Task 1 command. Expected: all tests PASS and fake fetcher call counts match exactly.

- [ ] **Step 5: Commit safe fetching**

```powershell
git add -- prototype/model_research.py prototype/tests/test_model_research.py
git commit -m "feat: add ssrf-safe model source fetching"
```

---

### Task 3: Persist immutable snapshots, claims, and profile versions

**Files:**
- Create: `prototype/model_profile_store.py`
- Create: `prototype/tests/test_model_profile_store.py`
- Modify: `prototype/db.py`
- Modify: `prototype/tests/test_db.py`

**Interfaces:**
- Consumes: Task 1 normalized snapshots and claims.
- Produces: `create_research_run(source_url, *, db_path) -> dict`.
- Produces: `complete_research_run(run_id, snapshots, claims, *, db_path) -> dict`.
- Produces: `create_profile_draft(run_id, profile, claims, *, db_path) -> dict`.
- Produces: `revise_profile_draft(version_id, profile, claim_decisions, note, *, db_path) -> dict`.
- Produces: `review_profile_version(version_id, reviewer_note, *, db_path) -> dict`.
- Produces: `activate_profile_version(version_id, expected_active_version_id, *, db_path) -> dict`.
- Produces: `get_profile_version(version_id, *, db_path) -> dict | None`.
- Produces: `list_active_profile_versions(*, db_path) -> list[dict]`.

- [ ] **Step 1: Write failing schema and repository tests**

Create tests proving:

```python
def test_research_evidence_and_profile_versions_are_immutable(self):
    run = store.create_research_run(self.url, db_path=self.db)
    completed = store.complete_research_run(
        run["runId"], [self.snapshot], [self.claim], db_path=self.db
    )
    draft = store.create_profile_draft(
        completed["runId"], self.profile, completed["claims"], db_path=self.db
    )
    revised = store.revise_profile_draft(
        draft["versionId"], self.revised_profile,
        {self.claim.claim_id: "approved"}, "确认官网版本号", db_path=self.db
    )
    self.assertNotEqual(revised["versionId"], draft["versionId"])
    self.assertEqual(
        store.get_profile_version(draft["versionId"], db_path=self.db)["profile"],
        self.profile,
    )

def test_only_reviewed_version_can_activate_with_compare_and_swap(self):
    with self.assertRaisesRegex(store.ProfileStoreError, "not_reviewed"):
        store.activate_profile_version(
            self.draft["versionId"], None, db_path=self.db
        )
    reviewed = store.review_profile_version(
        self.draft["versionId"], "证据已核对", db_path=self.db
    )
    active = store.activate_profile_version(
        reviewed["versionId"], None, db_path=self.db
    )
    self.assertEqual(active["lifecycleStatus"], "active")
    with self.assertRaisesRegex(store.ProfileStoreError, "active_version_changed"):
        store.activate_profile_version(
            self.other_reviewed["versionId"], None, db_path=self.db
        )
```

Also test duplicate completion idempotency, an unknown evidence reference, rejected claims excluded from projected profile rules, one active version per `profile_id`, old active version becoming `superseded`, rollback on partial writes, and schema mismatch detection.

- [ ] **Step 2: Run repository tests to verify RED**

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_model_profile_store prototype.tests.test_db -v
```

Expected: FAIL because tables and repository are absent.

- [ ] **Step 3: Add exact SQLite records and repository transactions**

Add these tables to `SCHEMA` and `EXPECTED_SCHEMA_COLUMNS`:

```sql
CREATE TABLE IF NOT EXISTS model_research_runs (
    id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    status TEXT NOT NULL,
    error_code TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS model_evidence_snapshots (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES model_research_runs(id),
    source_class TEXT NOT NULL,
    requested_url TEXT NOT NULL,
    final_url TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    content_type TEXT,
    body_sha256 TEXT,
    extracted_text TEXT NOT NULL,
    fetch_status TEXT NOT NULL,
    error_code TEXT
);
CREATE TABLE IF NOT EXISTS model_evidence_claims (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES model_research_runs(id),
    field_path TEXT NOT NULL,
    value_json TEXT NOT NULL,
    evidence_class TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    rationale TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    application_status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_profile_versions (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    parent_version_id TEXT REFERENCES model_profile_versions(id),
    research_run_id TEXT REFERENCES model_research_runs(id),
    lifecycle_status TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    claim_decisions_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    review_note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    activated_at TEXT,
    UNIQUE(profile_id, revision)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_model_profile_one_active
ON model_profile_versions(profile_id)
WHERE lifecycle_status = 'active';
```

Use `BEGIN IMMEDIATE` for revision allocation and activation. `revise_profile_draft` and `review_profile_version` insert a new row with `parent_version_id`; they never update profile JSON. Activation may update lifecycle columns of the reviewed row and prior active row inside one transaction, but must first compare `expected_active_version_id` to the current active ID.

- [ ] **Step 4: Run repository tests to verify GREEN**

Run the Task 3 command. Expected: all tests PASS.

- [ ] **Step 5: Commit immutable storage**

```powershell
git add -- prototype/db.py prototype/model_profile_store.py prototype/tests/test_db.py prototype/tests/test_model_profile_store.py
git commit -m "feat: persist versioned researched model profiles"
```

---

### Task 4: Build pending-verification drafts and gate applicable rules

**Files:**
- Modify: `prototype/model_research.py`
- Modify: `prototype/model_profiles.py`
- Modify: `prototype/tests/test_model_research.py`
- Modify: `prototype/tests/test_model_profiles.py`

**Interfaces:**
- Consumes: Tasks 1–3 claims, snapshots, and immutable version repository.
- Produces: `build_pending_profile(source_url, snapshots, claims, manual_fields=None) -> dict`.
- Produces: `apply_claim_decisions(profile, claims, decisions, manual_fields) -> tuple[dict, list[dict]]`.
- Produces: `validate_researched_model_profile(profile: object) -> dict`.
- The returned model profile stays compatible with `model_profile_api_item`, `model_reference`, `profile_default_parameters`, and candidate/validated resolution helpers.

- [ ] **Step 1: Write failing projection and safety tests**

```python
def test_partial_fetch_builds_usable_pending_profile(self):
    profile = model_research.build_pending_profile(
        self.url,
        [dataclasses.replace(
            self.snapshot, fetch_status="failed", error_code="timeout",
            extracted_text="", body_sha256=""
        )],
        [],
        manual_fields={"displayName": "My Model", "model.versionName": "v1"},
    )
    self.assertEqual(profile["validationStatus"], "pending_local_validation")
    self.assertEqual(profile["prompting"]["positivePrefix"], [])
    self.assertEqual(profile["parameters"]["defaults"], {})
    self.assertEqual(profile["metadata"]["researchStatus"], "pending_verification")
    self.assertIn("research_fetch_failed", profile["metadata"]["warnings"])

def test_only_approved_claims_are_projected(self):
    profile, audit = model_research.apply_claim_decisions(
        self.empty_profile,
        [self.official_claim, self.supplemental_claim, self.inference_claim],
        {
            self.official_claim.claim_id: "approved",
            self.supplemental_claim.claim_id: "rejected",
            self.inference_claim.claim_id: "proposed",
        },
        {},
    )
    self.assertEqual(profile["parameters"]["defaults"]["steps"], 28)
    self.assertNotIn("cfg", profile["parameters"]["defaults"])
    self.assertEqual(
        {item["evidenceClass"] for item in audit},
        {"original_source", "supplemental_source", "ai_inference"},
    )
```

Also test manual values are labeled `user_supplied`, supplemental claims remain visibly supplemental, inferred claims never enter defaults without approval, candidate dimensions never enter `validatedPresets`, and no researched profile can claim `locally_validated` without existing checkpoint/parameter evidence.

- [ ] **Step 2: Run focused projection tests to verify RED**

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_model_research prototype.tests.test_model_profiles -v
```

Expected: FAIL at missing projection/validation behavior.

- [ ] **Step 3: Implement conservative profile projection**

Generate a safe `profileId` from adapter ID, source model ID, and exact version ID. If an exact version is unavailable, use a stable `pending-<first 12 chars of sha256(canonical URL)>` suffix and require review before activation.

Store in `metadata.research`:

```json
{
  "sourceUrl": "https://…",
  "researchRunId": "research-123",
  "researchStatus": "pending_verification",
  "warnings": ["missing_exact_version"],
  "claimAudit": [
    {
      "claimId": "claim-123",
      "fieldPath": "parameters.defaults.steps",
      "evidenceClass": "original_source",
      "evidenceRefs": ["snapshot-…"],
      "applicationStatus": "approved"
    }
  ]
}
```

Do not invent positive prefixes, negatives, sampling parameters, dimensions, strengths, weaknesses, or limitations. Missing fields remain empty and generic downstream rules remain responsible for fallback behavior.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run the Task 4 command. Expected: all tests PASS.

- [ ] **Step 5: Commit conservative draft projection**

```powershell
git add -- prototype/model_research.py prototype/model_profiles.py prototype/tests/test_model_research.py prototype/tests/test_model_profiles.py
git commit -m "feat: project evidence-gated model profile drafts"
```

---

### Task 5: Expose research, review, activation, and active catalog APIs

**Files:**
- Modify: `prototype/server.py`
- Modify: `prototype/tests/test_server.py`

**Interfaces:**
- Consumes: Tasks 1–4 research service and store.
- Produces: `POST /api/model-research`.
- Produces: `GET /api/model-research/{runId}`.
- Produces: `PUT /api/model-profile-versions/{versionId}`.
- Produces: `POST /api/model-profile-versions/{versionId}/review`.
- Produces: `POST /api/model-profile-versions/{versionId}/activate`.
- Modifies: `GET /api/model-profiles` and `GET /api/model-profiles/{profileId}` to merge active researched versions with bundled profiles.

- [ ] **Step 1: Write failing HTTP contract tests with a fake fetcher**

Start the test server with injected:

```python
fake_fetcher = FakeFetcher({
    "https://civitai.com/models/934764/example":
        FetchResponse(
            "https://civitai.com/models/934764/example",
            200,
            {"Content-Type": "application/json"},
            json.dumps(self.source_payload).encode("utf-8"),
        )
})
fake_resolver = lambda host: ["93.184.216.34"]
```

Assert:

- `POST /api/model-research` with `{"sourceUrl": ...}` returns `201`, `run`, immutable `snapshots`, proposed `claims`, and a `draftVersion`.
- unsupported host/private resolution returns `400` with stable code and the fake fetcher is not called;
- a timeout returns `201` with `researchStatus="pending_verification"` and a retryable warning;
- update rejects unknown claim IDs, invalid decisions, changed snapshot text, and modified activated versions;
- review rejects unresolved exact model identity;
- activation requires `expectedActiveVersionId` (explicit `null` for first activation);
- stale compare-and-swap returns `409`;
- `GET /api/model-profiles` exposes the active immutable `profileVersionId`, `profileContentSha256`, evidence summary, warnings, and `generationReady`;
- bundled `anima-1.1-v1` remains available when no researched version exists.

- [ ] **Step 2: Run server tests to verify RED**

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_server -v
```

Expected: FAIL with missing routes.

- [ ] **Step 3: Implement dependency-injected service and exact routes**

Request schemas:

```json
POST /api/model-research
{"sourceUrl":"https://civitai.com/models/934764/example"}

PUT /api/model-profile-versions/{versionId}
{
  "claimDecisions":{"claim-1":"approved","claim-2":"rejected"},
  "manualFields":{"displayName":"…","model.versionName":"…"},
  "reviewNote":"人工补充名称，参数保持未验证"
}

POST /api/model-profile-versions/{versionId}/review
{"reviewerNote":"已核对原始页面与版本号"}

POST /api/model-profile-versions/{versionId}/activate
{"expectedActiveVersionId":null}
```

Use one JSON body read per request, strict unknown-key rejection, maximum URL length 2,048, maximum note length 4,096, and existing `RequestValidationError` handling. Do not accept supplemental URLs, arbitrary snapshot bodies, or evidence classes from clients in this phase.

The production server constructs the default resolver/fetcher. Tests replace them through server factory/class attributes before startup; the fetch layer itself remains unaware of HTTP handlers.

- [ ] **Step 4: Run server and profile suites to verify GREEN**

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest prototype.tests.test_server prototype.tests.test_model_research prototype.tests.test_model_profile_store prototype.tests.test_model_profiles -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit lifecycle APIs**

```powershell
git add -- prototype/server.py prototype/tests/test_server.py
git commit -m "feat: expose model research review lifecycle"
```

---

### Task 6: Add the original-link research and evidence review UI

**Files:**
- Modify: `prototype/index.html`
- Modify: `prototype/styles.css`
- Modify: `prototype/app.js`
- Modify: `prototype/tests/app.test.js`

**Interfaces:**
- Consumes: Task 5 API shapes.
- Produces state: `modelResearch = {status, sourceUrl, run, draftVersion, claimDecisions, manualFields, error}`.
- Produces actions: `MODEL_RESEARCH_STARTED`, `MODEL_RESEARCH_SUCCEEDED`, `MODEL_RESEARCH_FAILED`, `MODEL_CLAIM_DECISION_CHANGED`, `MODEL_MANUAL_FIELD_CHANGED`, `MODEL_DRAFT_SAVED`, `MODEL_VERSION_REVIEWED`, `MODEL_VERSION_ACTIVATED`.
- Produces: `researchModelSource`, `saveModelResearchDraft`, `reviewModelProfileVersion`, `activateModelProfileVersion`.

- [ ] **Step 1: Write failing reducer, rendering, and API orchestration tests**

Add Node tests proving:

- the model gate offers “粘贴官网原始链接研究” without removing existing profile choices;
- a valid submission sends only `sourceUrl`;
- loading disables duplicate submission but leaves model selection usable;
- successful research renders snapshot URL/time/hash/status and every claim’s field, value, evidence class, evidence refs, verification, and proposed/approved/rejected control;
- badges distinguish “原始页面”“补充资料”“AI 推断”“本地实测”;
- failed/partial research shows “待验证” and retry/manual supplement controls without blocking “使用现有模型继续”;
- save creates a new displayed version ID and preserves the prior immutable version in state history;
- review is disabled while exact model name/version is missing or proposed claims remain undecided;
- activation asks for confirmation, sends `expectedActiveVersionId`, refreshes `/api/model-profiles`, selects the activated profile, and leaves the confirmed creative brief unchanged;
- refresh/reopen retains only server-persisted run/version IDs, never cached page bodies.

- [ ] **Step 2: Run Node tests to verify RED**

```powershell
& 'C:\Users\13688\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test prototype/tests/app.test.js
```

Expected: FAIL at missing state/actions/rendering.

- [ ] **Step 3: Implement the research drawer and lifecycle controls**

Add one collapsible panel at the confirmed-brief model gate:

```html
<form id="modelResearchForm">
  <label for="modelSourceUrl">模型官网原始链接</label>
  <input id="modelSourceUrl" type="url" maxlength="2048"
         placeholder="https://civitai.com/models/…" required>
  <button id="modelResearchSubmit" type="submit">研究这个模型</button>
</form>
<section id="modelResearchResult" aria-live="polite"></section>
```

Render claims in stable `fieldPath, claimId` order. Default all decisions to `proposed`; never pre-check “采用”. Manual fields are limited to display name, family, exact version name/ID, base model, and notes. Snapshot extracted text is collapsed and escaped; no `innerHTML` receives source text.

Before review, show the count of approved/rejected/unresolved claims and all warnings. Before activation, show the exact immutable version ID/hash and the active version it will replace. If activation returns `409`, reload the current active profile and require the user to confirm again.

- [ ] **Step 4: Run Node tests to verify GREEN**

Run the Task 6 command. Expected: all tests PASS.

- [ ] **Step 5: Commit the review UI**

```powershell
git add -- prototype/index.html prototype/styles.css prototype/app.js prototype/tests/app.test.js
git commit -m "feat: add official model research review ui"
```

---

### Task 7: Document operations, recovery, and complete regression verification

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/data-model.md`
- Modify: `docs/integration-guide.md`
- Modify: `docs/operator-runbook.md`
- Modify: `docs/roadmap.md`

**Interfaces:**
- Consumes: final Tasks 1–6 behavior.
- Produces: exact API examples, schema lifecycle, SSRF boundary, backup/recovery notes, and completion status.

- [ ] **Step 1: Update architecture and data-model facts**

Document:

- adapter registry → URL/DNS/redirect guard → bounded fetcher → immutable snapshot/claims → draft/review/activation;
- table columns, foreign keys, one-active-version invariant, content hash, and append-only version lineage;
- active researched profiles are projected through the same model-profile validation/read API as bundled profiles;
- historical recipes retain their exact `profileVersionId` and snapshot fields; later activation never rewrites them.

- [ ] **Step 2: Add API and operator examples**

Add the four Task 5 JSON examples and response status/error table. Document these recovery cases:

- unsupported URL: use a supported original page or keep an existing model;
- DNS/policy rejection: no request was sent;
- timeout/parse failure: retain pending-verification draft, retry explicitly, or enter manual identity fields;
- stale activation: reload active version and reconfirm;
- incomplete evidence: generic rules continue and unverified optimizations are labeled;
- backups include the new tables; staged restore remains isolated and `activated=false`.

- [ ] **Step 3: Mark only this phase complete**

In `docs/roadmap.md`, mark original-link model research, evidence grading, immutable version review, and activation complete. Keep model-adapted thirteen-block decomposition, LoRA-lite profiles, and real Anima acceptance pending.

- [ ] **Step 4: Run complete automated verification**

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s prototype\tests -p "test_*.py"
& 'C:\Users\13688\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test prototype/tests/app.test.js
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s scripts\tests -p "test_*.py"
```

Expected: all suites PASS with no real network access.

- [ ] **Step 5: Run repository safety checks**

```powershell
git status --short
git diff --check
git diff --name-only --cached
```

Expected:

- no local database, page snapshot, API key, `.env`, log, PID, model, or unrelated user file is staged;
- no whitespace errors;
- only source, tests, and the listed current-fact documentation changed.

- [ ] **Step 6: Manually exercise the fake-source acceptance flow**

Use the focused HTTP integration test’s injected fake resolver/fetcher while exercising the browser against its test server, not a live website:

1. Confirm a creative brief and open the model gate.
2. Paste the fixture’s supported original link.
3. Verify evidence badges and snapshot metadata.
4. Approve one official claim, reject one claim, add an exact version manually, and save.
5. Review and activate the new immutable version.
6. Confirm it appears in the model selector and generic fallback warnings remain visible.
7. Reopen the project and confirm the brief and selected `profileVersionId` survive.
8. Attempt a private/localhost link and confirm the fixture fetcher records zero calls.

- [ ] **Step 7: Commit documentation**

```powershell
git add -- docs/architecture.md docs/data-model.md docs/integration-guide.md docs/operator-runbook.md docs/roadmap.md
git commit -m "docs: complete official model research phase"
```

---

## Acceptance Scenarios

1. Supported original link: safe fetch → immutable snapshot → proposed claims → reviewed immutable profile version → explicit activation → model selector refresh.
2. Unsupported or SSRF-like link: rejected before transport; no snapshot and no outbound request.
3. Redirect: every hop is adapter-, URL-, and DNS-validated; a private or unsupported target is rejected.
4. Partial website failure: pending-verification profile remains editable; existing model selection and generic creative flow continue.
5. Evidence honesty: original, supplemental, AI inference, and local validation are visually and structurally distinct; none is silently relabeled.
6. Claim gate: only explicitly approved claims enter the profile; candidate dimensions never become locally validated automatically.
7. Version history: editing/reviewing creates child versions, activation uses compare-and-swap, and old recipes keep their historical profile reference.
8. No live-test dependency: all automated fetching uses injected fake resolver/fetcher/clock and passes offline.

## Out of Scope / Handoff

- The next independent plan consumes only activated immutable profile versions to create semantic/model-layer thirteen-block previews.
- The later LoRA-lite plan owns LoRA link, version, trigger, weight, notes, and simple compatibility/conflict records.
- Local checkpoint/hash and output-quality testing may add `local_validation` claims in a later acceptance phase; this plan does not run models.
