"""Consolidate immutable collection snapshots and screening evidence; never move replays."""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from scv_star.envs.sc2_client import sha256


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def lines(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line]


def write(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2), encoding="utf-8")


def merge(rows: list[dict], screening: list[dict], samples: list[dict]) -> list[dict]:
    result = {row["sha256"]: deepcopy(row) for row in rows}
    if len(result) != len(rows):
        raise ValueError("Duplicate SHA entries; no automatic deduplication")
    seen = set()
    for record in screening:
        digest = record["sha256"]
        if digest not in result or digest in seen:
            raise ValueError("Unknown or duplicate screening record")
        seen.add(digest)
        row = result[digest]
        if record["identity"] != row["identity"] or record["local_replay"] != row["local_replay"]:
            raise ValueError("Screening identity/path differs from source snapshot")
        status = record["screening_status"]
        if status not in ("eligible", "excluded", "review"):
            raise ValueError("Unknown screening status")
        row["screening"] = deepcopy(record)
        row["outcome_label_ready"] = record.get("outcome_label_ready")
        row["map_name"] = record.get("map_name")
        row["collection_source"] = record.get("source")
        if status == "excluded":
            row["queue"] = "excluded"
            row["exclusion_reasons"] = record["reasons"]
        elif status == "review":
            row["queue"] = "screening_review"
        else:
            row["human_status"] = "confirmed"
    sample_seen = set()
    for sample in samples:
        digest = sample["sha256"]
        if digest not in seen or digest in sample_seen:
            raise ValueError("Sample lacks screening evidence or is duplicated")
        sample_seen.add(digest)
        result[digest]["sample_verification"] = deepcopy(sample)
    for row in result.values():
        # Existing known exclusions must not stay in the active queue.
        if row.get("human_status") == "excluded" and row["queue"] == "tvt_candidates":
            row["queue"] = "excluded"
            row["exclusion_reasons"] = ["historical_non_1v1_exclusion"]
        row["screening_status"] = row.get("screening", {}).get("screening_status", "not_screened")
        row["full_replay_status"] = row.get("sample_verification", {}).get("status", "not_sampled")
        row["training_ready"] = False
        row["bc_alignment_verified"] = False
    return sorted(result.values(), key=lambda row: row["sha256"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--screening", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    if output.parent != root / "data/manifests" or output.exists():
        raise ValueError("Output must be a new snapshot immediately under data/manifests")
    source = args.source.resolve()
    screen = args.screening.resolve()
    inputs = [
        source / "catalog.jsonl",
        *(screen / (s + ".jsonl") for s in ("eligible", "excluded", "review")),
        screen / "sample-verification.json",
    ]
    hashes = {str(p.relative_to(root)): sha256(p) for p in inputs}
    rows = merge(lines(inputs[0]), [r for p in inputs[1:4] for r in lines(p)], read(inputs[4]))
    for row in rows:
        path = (root / row["local_replay"]).resolve()
        if not path.is_relative_to(root / "data/raw/replays"):
            raise ValueError("Replay path escapes project raw replay directories")
        if sha256(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
            raise ValueError(f"Original file changed: {row['sha256']}")
    pointer_path = root / "data/manifests/current.json"
    old_pointer = read(pointer_path)
    output.mkdir()

    def jsonl(name, subset):
        (output / name).write_text(
            "".join(json.dumps(r, ensure_ascii=True) + "\n" for r in subset), encoding="utf-8"
        )

    jsonl("catalog.jsonl", rows)
    queues = Counter(r["queue"] for r in rows)
    for queue in queues:
        jsonl(queue + ".jsonl", [r for r in rows if r["queue"] == queue])
    jsonl(
        "screened_eligible.jsonl",
        [r for r in rows if r["screening_status"] == "eligible" and r["queue"] == "tvt_candidates"],
    )
    jsonl(
        "outcome_label_review.jsonl",
        [
            r
            for r in rows
            if r.get("outcome_label_ready") is False and r["queue"] == "tvt_candidates"
        ],
    )
    with sqlite3.connect(output / "catalog.sqlite") as db:
        db.execute("CREATE TABLE replays (sha TEXT PRIMARY KEY, info TEXT NOT NULL)")
        db.execute(
            "CREATE TABLE locations (source TEXT, member TEXT, sha TEXT, PRIMARY KEY(source,member))"
        )
        db.executemany(
            "INSERT INTO replays VALUES (?,?)", [(r["sha256"], json.dumps(r)) for r in rows]
        )
        db.executemany(
            "INSERT INTO locations VALUES (?,?,?)",
            [(r["local_replay"], "", r["sha256"]) for r in rows],
        )
        db.commit()
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
    summary = {
        "schema_version": 3,
        "snapshot": output.name,
        "unique_replay_files": len(rows),
        "queues": dict(queues),
        "screening": dict(Counter(r["screening_status"] for r in rows)),
        "sample_verification": dict(Counter(r["full_replay_status"] for r in rows)),
        "outcome_label_review": sum(
            r.get("outcome_label_ready") is False and r["queue"] == "tvt_candidates" for r in rows
        ),
        "training_ready_files": 0,
        "same_match_deduplication_performed": False,
        "raw_files_moved_or_deleted": 0,
        "raw_sha256_verified": len(rows),
    }
    write(output / "summary.json", summary)
    write(
        output / "provenance.json",
        {
            "input_sha256": hashes,
            "previous_pointer": old_pointer,
            "previous_snapshot": str(source.relative_to(root)),
            "overlay_incorporated": str(screen.relative_to(root)),
            "snapshot_tool_sha256": sha256(Path(__file__)),
        },
    )
    for name, digest in hashes.items():
        if sha256(root / name) != digest:
            raise ValueError("Source snapshot changed during consolidation")
    if args.activate:
        pointer = {
            "schema_version": 3,
            "snapshot": output.name,
            "summary_sha256": sha256(output / "summary.json"),
            "replay_directories": old_pointer["replay_directories"],
            "replay_path_field": "local_replay",
            "readable_replays": len(rows),
            "active_tvt_candidates": queues.get("tvt_candidates", 0),
            "paused_91115": queues.get("tvt_91115_paused", 0),
            "identity_review": queues.get("identity_review", 0),
            "excluded": queues.get("excluded", 0),
            "training_ready": 0,
            "screening_overlay_applied": True,
        }
        pointer["screened_eligible"] = sum(
            r["screening_status"] == "eligible" and r["queue"] == "tvt_candidates" for r in rows
        )
        temporary = pointer_path.with_suffix(".next.json")
        write(temporary, pointer)
        temporary.replace(pointer_path)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
