"""Immutable lifecycle repository for researched model profiles."""

from __future__ import annotations

import copy
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Mapping

import db
from model_research import (
    EvidenceClaim,
    SourceSnapshot,
    claim_to_dict,
    normalize_claim,
    snapshot_to_dict,
)


class ProfileStoreError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@contextmanager
def _write_transaction(db_path: Path | str | None):
    db.init_db(db_path)
    connection = db.connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _required_text(value: Any, code: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ProfileStoreError(code, "required text is invalid")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ProfileStoreError(code, "text must be valid UTF-8") from error
    return value.strip()


def _normalize_profile(profile: Any) -> tuple[str, dict]:
    if not isinstance(profile, Mapping):
        raise ProfileStoreError("invalid_profile", "profile must be an object")
    normalized = copy.deepcopy(dict(profile))
    profile_id = normalized.get("id", normalized.get("profileId"))
    profile_id = _required_text(profile_id, "invalid_profile", 128)
    normalized["id"] = profile_id
    normalized.pop("profileId", None)
    try:
        db.json_dumps(normalized)
    except ValueError as error:
        raise ProfileStoreError("invalid_profile", str(error)) from error
    return profile_id, normalized


def _snapshot_from(value: Any) -> SourceSnapshot:
    if isinstance(value, SourceSnapshot):
        return value
    if not isinstance(value, Mapping):
        raise ProfileStoreError("invalid_snapshot", "snapshot is invalid")
    try:
        return SourceSnapshot(
            snapshot_id=value["snapshotId"],
            source_class=value["sourceClass"],
            requested_url=value["requestedUrl"],
            final_url=value["finalUrl"],
            retrieved_at=value["retrievedAt"],
            content_type=value.get("contentType"),
            body_sha256=value.get("bodySha256", ""),
            extracted_text=value.get("extractedText", ""),
            fetch_status=value["fetchStatus"],
            error_code=value.get("errorCode"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProfileStoreError("invalid_snapshot", "snapshot is invalid") from error


def _claim_from(value: Any) -> EvidenceClaim:
    try:
        return normalize_claim(value)
    except (TypeError, ValueError) as error:
        raise ProfileStoreError("invalid_claim", str(error)) from error


def _snapshot_row(row) -> dict:
    return {
        "snapshotId": row["id"],
        "sourceClass": row["source_class"],
        "requestedUrl": row["requested_url"],
        "finalUrl": row["final_url"],
        "retrievedAt": row["retrieved_at"],
        "contentType": row["content_type"],
        "bodySha256": row["body_sha256"] or "",
        "extractedText": row["extracted_text"],
        "fetchStatus": row["fetch_status"],
        "errorCode": row["error_code"],
    }


def _claim_row(row) -> dict:
    return {
        "claimId": row["id"],
        "fieldPath": row["field_path"],
        "value": db.json_loads(row["value_json"], None),
        "evidenceClass": row["evidence_class"],
        "evidenceRefs": db.json_loads_typed(
            row["evidence_refs_json"], list, []
        ),
        "rationale": row["rationale"],
        "verificationStatus": row["verification_status"],
        "applicationStatus": row["application_status"],
    }


def _run_result(connection: sqlite3.Connection, row) -> dict:
    return {
        "runId": row["id"],
        "sourceUrl": row["source_url"],
        "status": row["status"],
        "errorCode": row["error_code"],
        "createdAt": row["created_at"],
        "completedAt": row["completed_at"],
        "snapshots": [
            _snapshot_row(item)
            for item in db.evidence_snapshot_rows(connection, row["id"])
        ],
        "claims": [
            _claim_row(item)
            for item in db.evidence_claim_rows(connection, row["id"])
        ],
    }


def _version_result(row) -> dict:
    return {
        "versionId": row["id"],
        "profileId": row["profile_id"],
        "revision": row["revision"],
        "parentVersionId": row["parent_version_id"],
        "researchRunId": row["research_run_id"],
        "lifecycleStatus": row["lifecycle_status"],
        "profile": db.json_loads_typed(row["profile_json"], dict, {}),
        "claimDecisions": db.json_loads_typed(
            row["claim_decisions_json"], dict, {}
        ),
        "contentSha256": row["content_sha256"],
        "reviewNote": row["review_note"],
        "createdAt": row["created_at"],
        "reviewedAt": row["reviewed_at"],
        "activatedAt": row["activated_at"],
    }


def create_research_run(
    source_url: str, *, db_path: Path | str | None = None
) -> dict:
    source_url = _required_text(source_url, "invalid_source_url", 2048)
    run_id = db.new_id("research")
    created_at = db.now_iso()
    with _write_transaction(db_path) as connection:
        db.insert_research_run_row(
            connection,
            (run_id, source_url, "pending", None, created_at, None),
        )
        return _run_result(connection, db.research_run_row(connection, run_id))


def complete_research_run(
    run_id: str,
    snapshots: Iterable[Any],
    claims: Iterable[Any],
    *,
    db_path: Path | str | None = None,
) -> dict:
    run_id = _required_text(run_id, "unknown_run", 128)
    normalized_snapshots = [_snapshot_from(item) for item in snapshots]
    normalized_claims = [_claim_from(item) for item in claims]
    snapshot_ids = [item.snapshot_id for item in normalized_snapshots]
    claim_ids = [item.claim_id for item in normalized_claims]
    if len(snapshot_ids) != len(set(snapshot_ids)) or len(claim_ids) != len(
        set(claim_ids)
    ):
        raise ProfileStoreError("duplicate_evidence", "evidence IDs must be unique")
    for claim in normalized_claims:
        if any(reference not in snapshot_ids for reference in claim.evidence_refs):
            raise ProfileStoreError(
                "unknown_evidence", "claim references an unknown snapshot"
            )

    with _write_transaction(db_path) as connection:
        row = db.research_run_row(connection, run_id)
        if row is None:
            raise ProfileStoreError("unknown_run", "research run does not exist")
        if row["status"] == "completed":
            result = _run_result(connection, row)
            wanted_snapshots = sorted(
                (snapshot_to_dict(item) for item in normalized_snapshots),
                key=lambda item: item["snapshotId"],
            )
            wanted_claims = sorted(
                (claim_to_dict(item) for item in normalized_claims),
                key=lambda item: (item["fieldPath"], item["claimId"]),
            )
            if (
                result["snapshots"] == wanted_snapshots
                and result["claims"] == wanted_claims
            ):
                return result
            raise ProfileStoreError(
                "run_already_completed", "completed evidence cannot be changed"
            )
        for snapshot in normalized_snapshots:
            item = snapshot_to_dict(snapshot)
            db.insert_evidence_snapshot_row(
                connection,
                (
                    item["snapshotId"], run_id, item["sourceClass"],
                    item["requestedUrl"], item["finalUrl"], item["retrievedAt"],
                    item["contentType"], item["bodySha256"],
                    item["extractedText"], item["fetchStatus"], item["errorCode"],
                ),
            )
        for claim in normalized_claims:
            item = claim_to_dict(claim)
            db.insert_evidence_claim_row(
                connection,
                (
                    item["claimId"], run_id, item["fieldPath"],
                    db.json_dumps(item["value"]), item["evidenceClass"],
                    db.json_dumps(item["evidenceRefs"]), item["rationale"],
                    item["verificationStatus"], item["applicationStatus"],
                ),
            )
        db.complete_research_run_row(connection, run_id, db.now_iso())
        return _run_result(connection, db.research_run_row(connection, run_id))


def _claims_by_id(connection: sqlite3.Connection, run_id: str) -> dict[str, dict]:
    return {
        row["id"]: _claim_row(row)
        for row in db.evidence_claim_rows(connection, run_id)
    }


def _remove_path(profile: dict, path: str) -> None:
    pieces = path.split(".")
    current: Any = profile
    for piece in pieces[:-1]:
        if not isinstance(current, dict) or piece not in current:
            return
        current = current[piece]
    if isinstance(current, dict):
        current.pop(pieces[-1], None)


def _set_path(profile: dict, path: str, value: Any) -> None:
    pieces = path.split(".")
    current = profile
    for piece in pieces[:-1]:
        child = current.get(piece)
        if not isinstance(child, dict):
            child = {}
            current[piece] = child
        current = child
    current[pieces[-1]] = copy.deepcopy(value)


def _project_claims(
    profile: dict, claims: Mapping[str, dict], decisions: Mapping[str, str]
) -> dict:
    projected = copy.deepcopy(profile)
    for claim_id, claim in claims.items():
        _remove_path(projected, claim["fieldPath"])
        if decisions.get(claim_id) == "approved":
            _set_path(projected, claim["fieldPath"], claim["value"])
    return projected


def _insert_profile_version(
    connection: sqlite3.Connection,
    *,
    profile_id: str,
    parent_version_id: str | None,
    research_run_id: str | None,
    lifecycle_status: str,
    profile: dict,
    claim_decisions: dict,
    review_note: str,
    reviewed_at: str | None = None,
) -> dict:
    revision = db.next_profile_revision(connection, profile_id)
    version_id = db.new_id("profile-version")
    created_at = db.now_iso()
    content_sha256 = db.canonical_json_hash(profile)
    db.insert_profile_version_row(
        connection,
        (
            version_id, profile_id, revision, parent_version_id,
            research_run_id, lifecycle_status, db.json_dumps(profile),
            db.json_dumps(claim_decisions), content_sha256, review_note,
            created_at, reviewed_at, None,
        ),
    )
    return _version_result(db.profile_version_row(connection, version_id))


def create_profile_draft(
    run_id: str,
    profile: Mapping[str, Any],
    claims: Iterable[Any],
    *,
    db_path: Path | str | None = None,
) -> dict:
    profile_id, normalized_profile = _normalize_profile(profile)
    supplied_claims = {_claim_from(item).claim_id for item in claims}
    with _write_transaction(db_path) as connection:
        run = db.research_run_row(connection, run_id)
        if run is None or run["status"] != "completed":
            raise ProfileStoreError("run_not_completed", "research run is not complete")
        stored_claims = _claims_by_id(connection, run_id)
        if supplied_claims != set(stored_claims):
            raise ProfileStoreError("claim_mismatch", "claims do not match the run")
        decisions = {claim_id: "proposed" for claim_id in sorted(stored_claims)}
        projected = _project_claims(normalized_profile, stored_claims, decisions)
        return _insert_profile_version(
            connection, profile_id=profile_id, parent_version_id=None,
            research_run_id=run_id, lifecycle_status="draft",
            profile=projected, claim_decisions=decisions, review_note="",
        )


def revise_profile_draft(
    version_id: str,
    profile: Mapping[str, Any],
    claim_decisions: Mapping[str, str],
    note: str,
    *,
    db_path: Path | str | None = None,
) -> dict:
    profile_id, normalized_profile = _normalize_profile(profile)
    note = _required_text(note, "invalid_note")
    if not isinstance(claim_decisions, Mapping):
        raise ProfileStoreError("invalid_decisions", "decisions must be an object")
    with _write_transaction(db_path) as connection:
        parent = db.profile_version_row(connection, version_id)
        if parent is None:
            raise ProfileStoreError("unknown_version", "profile version does not exist")
        if parent["lifecycle_status"] != "draft":
            raise ProfileStoreError("not_draft", "only a draft may be revised")
        if parent["profile_id"] != profile_id:
            raise ProfileStoreError("profile_id_changed", "profile ID is immutable")
        claims = _claims_by_id(connection, parent["research_run_id"])
        previous = db.json_loads_typed(parent["claim_decisions_json"], dict, {})
        decisions = dict(previous)
        for claim_id, decision in claim_decisions.items():
            if claim_id not in claims or decision not in {"proposed", "approved", "rejected"}:
                raise ProfileStoreError("invalid_decisions", "claim decision is invalid")
            decisions[claim_id] = decision
        projected = _project_claims(normalized_profile, claims, decisions)
        return _insert_profile_version(
            connection, profile_id=profile_id, parent_version_id=version_id,
            research_run_id=parent["research_run_id"], lifecycle_status="draft",
            profile=projected, claim_decisions=decisions, review_note=note,
        )


def review_profile_version(
    version_id: str,
    reviewer_note: str,
    *,
    db_path: Path | str | None = None,
) -> dict:
    reviewer_note = _required_text(reviewer_note, "invalid_note")
    with _write_transaction(db_path) as connection:
        parent = db.profile_version_row(connection, version_id)
        if parent is None:
            raise ProfileStoreError("unknown_version", "profile version does not exist")
        if parent["lifecycle_status"] != "draft":
            raise ProfileStoreError("not_draft", "only a draft may be reviewed")
        return _insert_profile_version(
            connection, profile_id=parent["profile_id"],
            parent_version_id=version_id,
            research_run_id=parent["research_run_id"],
            lifecycle_status="reviewed",
            profile=db.json_loads_typed(parent["profile_json"], dict, {}),
            claim_decisions=db.json_loads_typed(
                parent["claim_decisions_json"], dict, {}
            ),
            review_note=reviewer_note, reviewed_at=db.now_iso(),
        )


def activate_profile_version(
    version_id: str,
    expected_active_version_id: str | None,
    *,
    db_path: Path | str | None = None,
) -> dict:
    if expected_active_version_id is not None:
        expected_active_version_id = _required_text(
            expected_active_version_id, "invalid_version", 128
        )
    with _write_transaction(db_path) as connection:
        target = db.profile_version_row(connection, version_id)
        if target is None:
            raise ProfileStoreError("unknown_version", "profile version does not exist")
        if target["lifecycle_status"] != "reviewed":
            raise ProfileStoreError("not_reviewed", "only reviewed versions activate")
        current = db.active_profile_version_row(connection, target["profile_id"])
        current_id = current["id"] if current is not None else None
        if current_id != expected_active_version_id:
            raise ProfileStoreError(
                "active_version_changed", "active profile version changed"
            )
        if current is not None:
            db.supersede_profile_version_row(connection, current_id)
        db.activate_profile_version_row(connection, version_id, db.now_iso())
        return _version_result(db.profile_version_row(connection, version_id))


def get_profile_version(
    version_id: str, *, db_path: Path | str | None = None
) -> dict | None:
    db.init_db(db_path)
    with db.database(db_path) as connection:
        row = db.profile_version_row(connection, version_id)
        return _version_result(row) if row is not None else None


def list_active_profile_versions(
    *, db_path: Path | str | None = None
) -> list[dict]:
    db.init_db(db_path)
    with db.database(db_path) as connection:
        return [
            _version_result(row)
            for row in db.active_profile_version_rows(connection)
        ]


__all__ = [
    "ProfileStoreError",
    "activate_profile_version",
    "complete_research_run",
    "create_profile_draft",
    "create_research_run",
    "get_profile_version",
    "list_active_profile_versions",
    "review_profile_version",
    "revise_profile_draft",
]
