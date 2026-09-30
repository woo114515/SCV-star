"""Inventory exact replay identities inside ZIPs without extracting game data."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sqlite3
import zipfile
from collections import Counter
from pathlib import Path

import mpyq
from s2protocol import versions

MAX_REPLAY_BYTES = 10_000_000


def matchup(row: dict) -> str:
    """Missing race information is unknown, not evidence of a non-TvT game."""
    races = row.get("races") or []
    if not races:
        return "unclassified_race"
    if len(races) != 2:
        return "nonstandard_player_count"
    aliases = {"Terr": "T", "Terran": "T", "Prot": "P", "Protoss": "P", "Zerg": "Z"}
    if any(race not in aliases for race in races):
        return "unclassified_race"
    return "v".join(sorted(aliases[race] for race in races))


def identity(metadata: dict, header: dict) -> tuple[dict, list[str]]:
    version = header["m_version"]
    root = header.get("m_ngdpRootKey", {}).get("m_data", b"").hex().upper()
    result = {
        "base_build": version["m_baseBuild"],
        "data_build": header.get("m_dataBuildNum"),
        "data_version": root or None,
        "game_version": ".".join(
            str(version.get(k, 0)) for k in ["m_major", "m_minor", "m_revision", "m_build"]
        ),
    }
    mismatches = []
    for key, expected in [
        ("BaseBuild", f"Base{result['base_build']}"),
        ("DataBuild", str(result["data_build"])),
        ("DataVersion", root),
        ("GameVersion", result["game_version"]),
    ]:
        if metadata.get(key) is not None and str(metadata[key]).upper() != expected.upper():
            mismatches.append(key)
    return result, mismatches


def inspect(raw: bytes) -> dict:
    result = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    # RFC 1740 AppleDouble metadata may be misnamed .SC2Replay in source packs.
    if raw[:4] == bytes.fromhex("00051607"):
        return dict(result, non_replay="appledouble_metadata")
    try:
        archive = mpyq.MPQArchive(io.BytesIO(raw))
        header = versions.latest().decode_replay_header(
            archive.header["user_data_header"]["content"]
        )
        metadata_raw = archive.read_file("replay.gamemetadata.json")
        metadata = json.loads(metadata_raw) if metadata_raw else {}
        result["identity"], result["identity_mismatches"] = identity(metadata, header)
        result["metadata_identity"] = {
            key: metadata[key]
            for key in ["GameVersion", "BaseBuild", "DataBuild", "DataVersion"]
            if key in metadata
        }
        result["game_loops"] = header["m_elapsedGameLoops"]
        result["races"] = [p.get("AssignedRace") for p in metadata.get("Players", [])]
        result["metadata_present"] = bool(metadata)
        init = archive.read_file("replay.initData")
        result["init_data_sha256"] = hashlib.sha256(init).hexdigest() if init else None
        try:
            protocol = versions.build(result["identity"]["base_build"])
            details = protocol.decode_replay_details(archive.read_file("replay.details"))
            players = details.get("m_playerList", [])
            result["players"] = [
                {"control": p.get("m_control"), "team": p.get("m_teamId")} for p in players
            ]
            result["human_1v1"] = (
                len(players) == 2
                and all(p.get("m_control") == 2 for p in players)
                and len({p.get("m_teamId") for p in players}) == 2
            )
            result["played_filetime"] = details.get("m_timeUTC")
            result["protocol_status"] = "exact_details"
            if not metadata:
                result["races"] = [p.get("m_race", b"").decode("utf-8", "replace") for p in players]
        except ImportError:
            result["protocol_status"] = "missing_exact_protocol"
        except Exception as error:
            result["protocol_status"] = "details_error"
            result["details_error"] = f"{type(error).__name__}: {error}"
        result["tvt_candidate"] = len(result["races"]) == 2 and all(
            race in ["Terr", "Terran"] for race in result["races"]
        )
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
    return result


def initialize(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS replays (sha TEXT PRIMARY KEY, info TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS locations (
            source TEXT, member TEXT, sha TEXT, PRIMARY KEY(source,member));
        CREATE TABLE IF NOT EXISTS sources (
            path TEXT PRIMARY KEY, bytes INTEGER, mtime_ns INTEGER, errors TEXT);
    """)


def scan(db: sqlite3.Connection, path: Path) -> None:
    source = str(path.resolve())
    stat = path.stat()
    previous = db.execute("SELECT bytes,mtime_ns FROM sources WHERE path=?", (source,)).fetchone()
    if previous == (stat.st_size, stat.st_mtime_ns):
        return
    if previous:
        raise ValueError(f"Previously inventoried source changed: {path}")
    errors = []
    count = 0
    with db:
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    if not member.filename.lower().endswith(".sc2replay"):
                        continue
                    if member.file_size > MAX_REPLAY_BYTES:
                        errors.append({"member": member.filename, "error": "Replay size limit"})
                        continue
                    try:
                        raw = archive.read(member)
                        record(db, source, member.filename, raw)
                        count += 1
                    except Exception as error:
                        errors.append({"member": member.filename, "error": str(error)})
        elif stat.st_size <= MAX_REPLAY_BYTES:
            record(db, source, "", path.read_bytes())
            count = 1
        else:
            errors.append({"error": "Replay size limit"})
        db.execute(
            "INSERT INTO sources VALUES (?,?,?,?)",
            (source, stat.st_size, stat.st_mtime_ns, json.dumps(errors)),
        )
    print(
        json.dumps({"source": path.name, "replays": count, "member_errors": len(errors)}),
        flush=True,
    )


def record(db: sqlite3.Connection, source: str, member: str, raw: bytes) -> None:
    sha = hashlib.sha256(raw).hexdigest()
    if not db.execute("SELECT 1 FROM replays WHERE sha=?", (sha,)).fetchone():
        db.execute("INSERT INTO replays VALUES (?,?)", (sha, json.dumps(inspect(raw))))
    db.execute("INSERT OR IGNORE INTO locations VALUES (?,?,?)", (source, member, sha))


def summarize(db: sqlite3.Connection) -> dict:
    counts = Counter()
    matchups = Counter()
    groups = {}
    for (encoded,) in db.execute("SELECT info FROM replays"):
        row = json.loads(encoded)
        counts["unique_files"] += 1
        if row.get("non_replay"):
            counts["non_replay_files"] += 1
            continue
        counts["unique_replay_files"] += 1
        if "error" in row:
            counts["parse_errors"] += 1
            continue
        counts["valid_replay_files"] += 1
        kind = matchup(row)
        matchups[kind] += 1
        counts["unclassified_race"] += kind == "unclassified_race"
        counts["nonstandard_player_count"] += kind == "nonstandard_player_count"
        counts["tvt_candidates"] += bool(row.get("tvt_candidate"))
        counts["identity_conflicts"] += bool(row["identity_mismatches"])
        ident = row["identity"]
        key = (ident["base_build"], ident["data_build"], ident["data_version"])
        group = groups.setdefault(key, {"identity": ident, "counts": Counter(), "versions": set()})
        group["versions"].add(ident["game_version"])
        group["counts"]["all_races"] += 1
        group["counts"]["unclassified_race"] += kind == "unclassified_race"
        group["counts"]["tvt_candidates"] += bool(row.get("tvt_candidate"))
        group["counts"]["human_tvt"] += bool(row.get("tvt_candidate") and row.get("human_1v1"))
        group["counts"]["tvt_over_2min"] += bool(
            row.get("tvt_candidate") and row["game_loops"] > 2688
        )
        group["counts"]["identity_conflicts"] += bool(row["identity_mismatches"])
        group["counts"]["exact_details"] += row.get("protocol_status") == "exact_details"
    occurrences = db.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
    return {
        "counts": dict(counts),
        "matchups": dict(sorted(matchups.items())),
        "source_occurrences": occurrences,
        "byte_identical_duplicates": occurrences - counts["unique_files"],
        "sources": db.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
        "engine_groups": [
            dict(
                identity=g["identity"],
                counts=dict(g["counts"]),
                game_versions=sorted(g["versions"]),
            )
            for _, g in sorted(groups.items(), key=lambda kv: str(kv[0]))
        ],
        "deduplication": "SHA-256 only; same-match variants not certified",
        "engine_replay_verified": False,
        "training_alignment_verified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    args.database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(args.database) as db:
        initialize(db)
        for source in args.paths:
            paths = sorted(source.rglob("*")) if source.is_dir() else [source]
            for path in paths:
                if path.is_file() and path.suffix.lower() in [".zip", ".sc2replay"]:
                    scan(db, path)
        result = summarize(db)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "engine_groups"}), flush=True)


if __name__ == "__main__":
    main()
