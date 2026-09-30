"""Merge public and local replay indexes, retaining originals and provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from scv_star.data.public_inventory import matchup, scan, summarize


def work_queue(row: dict) -> str:
    if row.get("non_replay"):
        return "non_replay_metadata"
    if "error" in row:
        return "parse_error"
    if row.get("identity_mismatches"):
        return "identity_review"
    kind = matchup(row)
    if kind in ["unclassified_race", "nonstandard_player_count"]:
        return kind
    if kind != "TvT":
        return "other_matchups"
    if row["identity"]["base_build"] == 91115:
        return "tvt_91115_paused"
    return "tvt_candidates"


def engine_key(ident: dict) -> tuple:
    return tuple(ident.get(k) for k in ["base_build", "data_build", "data_version"])


def verification(report: dict) -> dict[str, dict]:
    """Only matching file hashes with two completed perspectives carry evidence."""
    found = {}
    if report.get("status") != "passed":
        return found
    for row in report.get("replays", []):
        views = row.get("perspectives", [])
        if (
            row.get("status") == "replay_passed"
            and {v.get("player_id") for v in views} == {1, 2}
            and all(v.get("status") == "passed" and v.get("terminal") for v in views)
        ):
            found[row["sha256"]] = {
                "engine": report["engine"],
                "perspectives": len(views),
                "step": report["step"],
                "bc_alignment_verified": False,
            }
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-database", type=Path, required=True)
    parser.add_argument("--local-inventory", type=Path, required=True)
    parser.add_argument("--extracted", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    original_digest = hashlib.sha256(args.public_database.read_bytes()).hexdigest()
    prior_local = json.loads(args.local_inventory.read_text(encoding="utf-8"))["replays"]
    local_root = Path(os.path.commonpath([r["path"] for r in prior_local]))
    prior_hashes = {str(Path(r["path"]).resolve()): r["sha256"] for r in prior_local}
    evidence = {}
    evidence_origins = []
    for path in args.evidence:
        evidence.update(verification(json.loads(path.read_text(encoding="utf-8"))))
        evidence_origins.append(
            {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        )
    changed_local = []
    with sqlite3.connect(args.output / "catalog.sqlite") as db:
        with sqlite3.connect(args.public_database.resolve().as_uri() + "?mode=ro", uri=True) as src:
            src.backup(db)
        db.execute("CREATE TABLE origins (source TEXT PRIMARY KEY, category TEXT NOT NULL)")
        db.executemany(
            "INSERT INTO origins VALUES (?,?)",
            [(r[0], "public") for r in db.execute("SELECT path FROM sources")],
        )
        for path in sorted(local_root.rglob("*")):
            if not path.is_file() or path.suffix.lower() != ".sc2replay":
                continue
            scan(db, path)
            source = str(path.resolve())
            db.execute("INSERT INTO origins VALUES (?,?)", (source, "local_account"))
            sha = db.execute("SELECT sha FROM locations WHERE source=?", (source,)).fetchone()[0]
            if source in prior_hashes and prior_hashes[source] != sha:
                changed_local.append(source)
        for path in sorted(args.extracted.rglob("*.SC2Replay")):
            scan(db, path)
            db.execute(
                "INSERT INTO origins VALUES (?,?)", (str(path.resolve()), "public_extracted_copy")
            )
        db.commit()
        locations = defaultdict(list)
        for source, member, sha, category in db.execute(
            "SELECT l.source,l.member,l.sha,o.category FROM locations l JOIN origins o ON l.source=o.source"
        ):
            locations[sha].append({"source": source, "member": member, "category": category})
        report = summarize(db)
        queue_counts, verified_counts = Counter(), Counter()
        source_counts = defaultdict(Counter)
        source_hashes = defaultdict(set)
        init_groups = defaultdict(list)
        queues = defaultdict(list)
        public_matchups, local_matchups = Counter(), Counter()
        with (args.output / "catalog.jsonl").open("w", encoding="utf-8") as stream:
            for sha, encoded in db.execute("SELECT sha,info FROM replays ORDER BY sha"):
                row = json.loads(encoded)
                row["locations"] = locations[sha]
                row["matchup"] = matchup(row) if "identity" in row else "not_a_readable_replay"
                row["queue"] = work_queue(row)
                row["training_ready"] = False
                prior = evidence.get(sha)
                if prior and engine_key(prior["engine"]) != engine_key(row.get("identity", {})):
                    raise ValueError("Historical replay evidence has a different engine identity")
                row["replay_verification"] = prior
                if prior:
                    verified_counts["files"] += 1
                    verified_counts["perspectives"] += prior["perspectives"]
                queue_counts[row["queue"]] += 1
                categories = {x["category"] for x in locations[sha]}
                for category in categories:
                    source_hashes[category].add(sha)
                    source_counts[category]["unique_files"] += 1
                    source_counts[category]["readable_replays"] += "identity" in row
                    source_counts[category]["tvt_candidates"] += row["matchup"] == "TvT"
                if "identity" in row:
                    if "public" in categories:
                        public_matchups[row["matchup"]] += 1
                    if "local_account" in categories:
                        local_matchups[row["matchup"]] += 1
                ref = {
                    "sha256": sha,
                    "identity": row.get("identity"),
                    "matchup": row["matchup"],
                    "replay_verified": bool(prior),
                    "short_under_2min_faster": row.get("game_loops", 0) <= 2688,
                }
                queues[row["queue"]].append(ref)
                if row["matchup"] == "TvT" and row.get("init_data_sha256"):
                    init_groups[row["init_data_sha256"]].append(sha)
                stream.write(json.dumps(row, ensure_ascii=True) + "\n")
        for name, rows in queues.items():
            (args.output / (name + ".jsonl")).write_text(
                "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
            )
        report.update(
            schema_version=1,
            queues=dict(queue_counts),
            by_origin={k: dict(v) for k, v in source_counts.items()},
            public_matchups=dict(sorted(public_matchups.items())),
            local_matchups=dict(sorted(local_matchups.items())),
            public_local_byte_overlap=len(source_hashes["public"] & source_hashes["local_account"]),
            copied_samples_new_unique=len(
                source_hashes["public_extracted_copy"] - source_hashes["public"]
            ),
            prior_replay_verification=dict(verified_counts),
            training_ready_files=0,
            tvt_init_groups_for_review=sum(len(v) > 1 for v in init_groups.values()),
            tvt_excluding_91115=sum(
                g["counts"]["tvt_candidates"]
                for g in report["engine_groups"]
                if g["identity"]["base_build"] != 91115
            ),
            local_changed_since_previous_scan=len(changed_local),
            excluded_scope="Generic replay directory and generated runtime smoke/human tests are not human training sources",
        )
        (args.output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (args.output / "same_match_review.json").write_text(
            json.dumps({k: v for k, v in init_groups.items() if len(v) > 1}, indent=2),
            encoding="utf-8",
        )
        (args.output / "provenance.json").write_text(
            json.dumps(
                {
                    "public_database": str(args.public_database.resolve()),
                    "public_sha256": original_digest,
                    "local_root": str(local_root),
                    "changed_local_paths": changed_local,
                    "evidence_reports": evidence_origins,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    assert original_digest == hashlib.sha256(args.public_database.read_bytes()).hexdigest()
    print(json.dumps({k: v for k, v in report.items() if k != "engine_groups"}), flush=True)


if __name__ == "__main__":
    main()
