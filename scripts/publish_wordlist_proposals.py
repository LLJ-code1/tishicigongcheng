"""Local reviewer command for publishing explicitly approved wordlist proposals.

This command is intentionally the only path that writes a proposal into the
checked-in wordlist sources.  The browser stores proposals as project metadata
through workspace/commit; it never invokes this module.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
sys.path.insert(0, str(ROOT / "scripts"))

import db as prompt_db  # noqa: E402
from build_random_wordlists import (  # noqa: E402
    CATEGORY_SPECS,
    DEFAULT_OUTPUT,
    DEFAULT_SOURCE_DIR,
    generate_catalog,
)


class ProposalPublishError(ValueError):
    pass


def pending_proposals(db_path: Path) -> list[dict]:
    """Return submitted proposals with their immutable project provenance."""

    result: list[dict] = []
    with prompt_db.database(db_path) as connection:
        rows = connection.execute("SELECT id, metadata_json FROM projects").fetchall()
    for row in rows:
        metadata = prompt_db.load_stored_metadata(row["metadata_json"])
        for proposal in metadata.get("wordlistProposals", []):
            if isinstance(proposal, dict) and proposal.get("status") == "submitted":
                result.append({**proposal, "projectId": row["id"]})
    return sorted(result, key=lambda item: (item.get("submittedAt", ""), item["id"]))


def _source_files_by_category() -> dict[str, Path]:
    return {spec["id"]: Path(spec["sourceFile"]) for spec in CATEGORY_SPECS}


def stage_publication(
    proposals: list[dict],
    *,
    source_dir: Path,
    output: Path,
) -> dict:
    """Validate all additions in an isolated source copy before any write."""

    category_files = _source_files_by_category()
    with tempfile.TemporaryDirectory(prefix="prompt-studio-wordlist-review-") as folder:
        staged_source = Path(folder) / "source"
        shutil.copytree(source_dir, staged_source)
        for proposal in proposals:
            category_id = proposal.get("categoryId")
            filename = category_files.get(category_id)
            if filename is None:
                raise ProposalPublishError(f"unknown proposal category: {category_id}")
            destination = staged_source / filename
            existing = destination.read_text(encoding="utf-8").splitlines()
            if proposal["text"] in existing:
                raise ProposalPublishError(f"proposal already exists: {proposal['id']}")
            destination.write_text(
                "\n".join([*existing, proposal["text"]]) + "\n",
                encoding="utf-8",
            )
        staged_output = Path(folder) / "v1.json"
        generate_catalog(staged_source, staged_output)
        import json

        return json.loads(staged_output.read_text(encoding="utf-8"))


def publish_approved_proposals(
    db_path: Path,
    proposal_ids: list[str],
    *,
    source_dir: Path = DEFAULT_SOURCE_DIR,
    output: Path = DEFAULT_OUTPUT,
) -> dict:
    if not proposal_ids or len(proposal_ids) != len(set(proposal_ids)):
        raise ProposalPublishError("provide one or more unique --proposal-id values")
    available = {item["id"]: item for item in pending_proposals(db_path)}
    missing = [proposal_id for proposal_id in proposal_ids if proposal_id not in available]
    if missing:
        raise ProposalPublishError(f"submitted proposal not found: {missing[0]}")
    proposals = [available[proposal_id] for proposal_id in proposal_ids]
    staged_catalog = stage_publication(proposals, source_dir=source_dir, output=output)
    category_files = _source_files_by_category()
    for proposal in proposals:
        destination = source_dir / category_files[proposal["categoryId"]]
        existing = destination.read_text(encoding="utf-8").splitlines()
        destination.write_text(
            "\n".join([*existing, proposal["text"]]) + "\n",
            encoding="utf-8",
        )
    generate_catalog(source_dir, output)

    published_at = prompt_db.now_iso()
    with prompt_db.database(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        project_rows = {
            row["id"]: row
            for row in connection.execute(
                "SELECT id, metadata_json FROM projects WHERE id IN ({})".format(
                    ",".join("?" for _ in {item["projectId"] for item in proposals})
                ),
                tuple({item["projectId"] for item in proposals}),
            ).fetchall()
        }
        approved_ids = set(proposal_ids)
        for project_id, row in project_rows.items():
            metadata = prompt_db.load_stored_metadata(row["metadata_json"])
            changed = False
            for item in metadata.get("wordlistProposals", []):
                if item.get("id") in approved_ids:
                    item["status"] = "published"
                    item["publishedCatalogVersion"] = staged_catalog["version"]
                    item["publishedAt"] = published_at
                    changed = True
            if changed:
                connection.execute(
                    "UPDATE projects SET metadata_json = ?, updated_at = ? WHERE id = ?",
                    (prompt_db.json_dumps(metadata), published_at, project_id),
                )
    return {"catalogVersion": staged_catalog["version"], "proposalIds": proposal_ids}


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish reviewed Prompt Studio wordlist proposals.")
    parser.add_argument("--db", type=Path, default=prompt_db.DEFAULT_DB_PATH)
    parser.add_argument("--proposal-id", action="append", dest="proposal_ids", default=[])
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--apply", action="store_true", help="required before source files are modified")
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("refusing to write: rerun with --apply and explicit --proposal-id")
    try:
        result = publish_approved_proposals(
            args.db,
            args.proposal_ids,
            source_dir=args.source_dir,
            output=args.output,
        )
    except (ProposalPublishError, OSError, ValueError) as error:
        raise SystemExit(f"wordlist proposal publish failed: {error}") from error
    print(result["catalogVersion"])


if __name__ == "__main__":
    main()
