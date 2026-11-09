"""Independent per-view APM quality labels from the frozen qualification evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

APM_SCHEMA = "tvt-bc-apm-v1"
POINTER_SCHEMA = "tvt-bc-apm-pointer-v1"
COLLECTION_SCHEMA = "tvt-bc-collection-v1"
ROW_SCHEMA = "tvt-bc-v2"
HIGH_MIN_EXCLUSIVE = 250
MEDIUM_MIN_INCLUSIVE = 150
QUALITY_LABELS = ("high", "medium", "low")
DEFAULT_SAMPLING_WEIGHTS = {"high": 3.0, "medium": 2.0, "low": 1.0}


def sha256_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def quality_label(apm):
    """map >250 high, 150..250 inclusive medium, <150 low; reject absent/invalid."""
    if not isinstance(apm, int) or isinstance(apm, bool) or apm < 0:
        raise ValueError(f"Invalid player APM: {apm!r}")
    if apm > HIGH_MIN_EXCLUSIVE:
        return "high"
    if apm >= MEDIUM_MIN_INCLUSIVE:
        return "medium"
    return "low"


def _inside(root, name):
    name = str(name).replace("\\", "/")
    path = (root / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes its root: {name}")
    return path


def _child_rows(manifest_root, audit_doc):
    rows = {}
    children = {}
    for entry in audit_doc["files"]:
        path = _inside(manifest_root, entry["manifest"])
        if path not in children:
            children[path] = json.loads(path.read_text(encoding="utf-8"))
        child = children[path]
        if child.get("schema") != ROW_SCHEMA or not child.get("training_ready"):
            raise ValueError(f"Child manifest is not accepted: {path}")
        for shard in child["shards"]:
            key = (shard["replay_sha256"], int(shard["player_id"]))
            if key in rows and rows[key] != int(shard["rows"]):
                raise ValueError(f"Conflicting shard row counts for {key}")
            rows[key] = int(shard["rows"])
    return rows


def build_apm_manifest(
    collection,
    audit,
    evidence_root,
    output,
    *,
    pointer=None,
    minimum_files=1000,
):
    """Build a per-view APM/quality manifest; evidence is read-only."""
    collection = Path(collection).resolve()
    audit = Path(audit).resolve()
    evidence_root = Path(evidence_root).resolve()
    output = Path(output).absolute()
    if output.exists():
        raise ValueError(f"APM output already exists: {output}")
    if pointer is not None:
        pointer = Path(pointer).absolute()
        if pointer.exists():
            raise ValueError(f"APM pointer already exists: {pointer}")
    manifest_root = collection.parents[3]
    collection_doc = json.loads(collection.read_text(encoding="utf-8"))
    if collection_doc.get("schema") != COLLECTION_SCHEMA or not collection_doc.get(
        "training_ready"
    ):
        raise ValueError("Expected an accepted collection")
    if collection_doc.get("row_schema") != ROW_SCHEMA:
        raise ValueError("Unexpected collection row schema")
    audit_doc = json.loads(audit.read_text(encoding="utf-8"))
    files = audit_doc.get("files", [])
    if len(files) < int(minimum_files):
        raise ValueError(f"Qualification audit has only {len(files)} files")
    if audit_doc.get("qualification_issues") not in (0, [], None):
        raise ValueError("Qualification audit reports issues")
    rows = _child_rows(manifest_root, audit_doc)
    records = []
    counts = Counter()
    row_counts = Counter()
    split_counts = Counter()
    split_rows = Counter()
    build_counts = Counter()
    split_views = Counter()
    split_row_totals = Counter()
    for entry in files:
        if not entry.get("api_qualification_passed"):
            raise ValueError(f"File did not pass API qualification: {entry['sha256']}")
        evidence_path = _inside(evidence_root, entry["api_evidence"])
        if sha256_file(evidence_path) != entry["api_evidence_sha256"]:
            raise ValueError(f"API evidence hash mismatch: {evidence_path}")
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        players = evidence.get("info", {}).get("player_info", [])
        player_ids = {int(item["player_info"]["player_id"]) for item in players}
        if player_ids != {1, 2}:
            raise ValueError(f"Evidence does not contain two players: {evidence_path}")
        identity = entry["identity"]
        split = entry["split"]
        for item in players:
            player_id = int(item["player_info"]["player_id"])
            apm = item.get("player_apm")
            label = quality_label(apm)
            rows_for_view = rows.get((entry["sha256"], player_id))
            if rows_for_view is None:
                raise ValueError(f"Missing shard rows for {entry['sha256']} player {player_id}")
            records.append(
                {
                    "replay_sha256": entry["sha256"],
                    "player_id": player_id,
                    "split": split,
                    "base_build": int(identity["base_build"]),
                    "data_version": identity["data_version"],
                    "player_apm": apm,
                    "quality_label": label,
                    "rows": rows_for_view,
                }
            )
            counts[label] += 1
            row_counts[label] += rows_for_view
            split_counts[(split, label)] += 1
            split_rows[(split, label)] += rows_for_view
            split_views[split] += 1
            split_row_totals[split] += rows_for_view
            build_counts[int(identity["base_build"])] += 1
    views = len(records)
    total_rows = sum(record["rows"] for record in records)
    expected_views = int(collection_doc.get("player_views", -1))
    expected_rows = int(collection_doc.get("rows", -1))
    if views != expected_views or total_rows != expected_rows:
        raise ValueError(
            f"Label/collection mismatch: {views}/{total_rows} vs {expected_views}/{expected_rows}"
        )
    document = {
        "schema": APM_SCHEMA,
        "collection_sha256": sha256_file(collection),
        "qualification_audit_sha256": sha256_file(audit),
        "evidence_root": evidence_root.as_posix(),
        "thresholds": {
            "high_min_exclusive": HIGH_MIN_EXCLUSIVE,
            "medium_min_inclusive": MEDIUM_MIN_INCLUSIVE,
            "high": ">250",
            "medium": "150..250",
            "low": "<150",
        },
        "views": views,
        "rows": total_rows,
        "records": records,
        "summary": {
            "views_by_quality": dict(counts),
            "rows_by_quality": dict(row_counts),
            "views_by_split": dict(split_views),
            "rows_by_split": dict(split_row_totals),
            "views_by_split_quality": {
                split: {
                    label: split_counts[(split, label)]
                    for label in QUALITY_LABELS
                    if split_counts[(split, label)]
                }
                for split in sorted(split_views)
            },
            "rows_by_split_quality": {
                split: {
                    label: split_rows[(split, label)]
                    for label in QUALITY_LABELS
                    if split_rows[(split, label)]
                }
                for split in sorted(split_views)
            },
            "views_by_build": {str(key): value for key, value in sorted(build_counts.items())},
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2), encoding="utf-8")
    if pointer is not None:
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer_document = {
            "schema": POINTER_SCHEMA,
            "quality_manifest": Path(os.path.relpath(output, pointer.parent)).as_posix(),
            "quality_manifest_sha256": sha256_file(output),
            "collection_sha256": document["collection_sha256"],
            "views": views,
            "rows": total_rows,
        }
        pointer.write_text(json.dumps(pointer_document, indent=2), encoding="utf-8")
    return document, output


def load_apm_labels(path):
    """Load labels from a quality manifest or its pointer; returns doc, labels, path."""
    path = Path(path).resolve()
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") == POINTER_SCHEMA:
        manifest_path = (path.parent / document["quality_manifest"]).resolve()
        if not manifest_path.is_relative_to(path.parent):
            raise ValueError("APM pointer escapes its directory")
        if sha256_file(manifest_path) != document["quality_manifest_sha256"]:
            raise ValueError("APM quality manifest hash mismatch")
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        path = manifest_path
    if document.get("schema") != APM_SCHEMA:
        raise ValueError("Unexpected APM manifest schema")
    labels = {}
    for record in document["records"]:
        key = (record["replay_sha256"], int(record["player_id"]))
        if key in labels:
            raise ValueError(f"Duplicate APM label for {key}")
        labels[key] = record
    return document, labels, path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pointer", type=Path)
    parser.add_argument("--minimum-files", type=int, default=1000)
    args = parser.parse_args()
    document, output = build_apm_manifest(
        args.collection,
        args.audit,
        args.evidence_root,
        args.output,
        pointer=args.pointer,
        minimum_files=args.minimum_files,
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "views": document["views"],
                "rows": document["rows"],
                "views_by_quality": document["summary"]["views_by_quality"],
                "rows_by_quality": document["summary"]["rows_by_quality"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
