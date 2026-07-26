# LoRA-lite Profiles Implementation Plan

**Goal:** Let one local user create, review, select, and persist simplified LoRA profiles without scanning, downloading, or modifying LoRA files.

**First-version boundary:** A profile stores only name, version, original HTTPS link, trigger words, suggested weight, user notes, compatible model-profile IDs, conflicting LoRA-profile IDs, and a short compatibility note. Full webpage research, local directory scanning, hashes, automatic combination recommendation, and complex compatibility inference are out of scope.

## Task 1: Strict profile domain and persistence

- Add `prototype/lora_profile_store.py` over the existing `resources` table with `type=lora_profile`; do not migrate SQLite.
- Normalize all fields, reject unknown keys, unsafe IDs, non-HTTPS links, duplicate trigger/model/conflict IDs, self-conflicts, and weights outside `0..2`.
- Add list/get/create/update/delete with `baseUpdatedAt` compare-and-swap on update/delete.
- Verify with temporary-database tests that writes never touch model/LoRA files.

## Task 2: HTTP API

- Add `GET/POST /api/lora-profiles` and `GET/PUT/DELETE /api/lora-profiles/<id>`.
- Return 404 for unknown IDs and 409 for stale update/delete.
- Keep bodies strict and bounded; never accept file paths, file bytes, API keys, inferred hashes, or research claims.

## Task 3: Workbench selection and Recipe

- Add frontend catalog state, selection state, strict render model, and reducer actions.
- A profile conflicting with the selected model or another selected LoRA cannot be enabled.
- Map selected profiles to existing Recipe v1 `loras`; use original-link source refs and preserve the exact user-confirmed weight and trigger words.
- Selection is explicit. Loading a catalog never enables a LoRA automatically.

## Task 4: Editor UI

- Add one compact workbench panel to list/select profiles and edit the first-version fields.
- Show compatibility/conflict reasons beside each candidate.
- Provide explicit create/update/delete controls and a visible notice that the app never scans or changes LoRA files.

## Task 5: Recovery, docs, and verification

- Prove selected LoRAs survive Recipe save/reopen and deleted profiles do not alter historical Recipes.
- Update architecture, data model, integration guide, roadmap, and operator recovery notes.
- Run all Python, Node, script, generated-content, whitespace, and browser smoke checks.

## Completion criteria

- Profile CRUD is strict, recoverable, and concurrency-safe.
- Conflicts fail closed and are visible.
- Confirmed selections enter Recipe v1 with trigger words, weight, compatibility status, and original-link provenance.
- No scan, download, move, delete, hash validation, live research, or image generation occurs.
