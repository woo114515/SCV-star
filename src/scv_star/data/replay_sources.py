"""Small-sample replay inspection. Metadata success is not an engine replay pass."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from collections import Counter
from pathlib import Path

import mpyq
from s2protocol import versions


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_tvt(metadata: dict) -> bool:
    """Use assigned races, including Random players who actually became Terran."""
    return [p.get("AssignedRace") for p in metadata.get("Players", [])] == ["Terr", "Terr"]


def inspect_replay(raw: bytes, *, events: bool = False) -> dict:
    result = {"sha256": digest(raw), "bytes": len(raw)}
    archive = mpyq.MPQArchive(io.BytesIO(raw))
    header = versions.latest().decode_replay_header(archive.header["user_data_header"]["content"])
    result.update(
        header_version=header["m_version"],
        game_loops=header["m_elapsedGameLoops"],
        header_data_version=header["m_ngdpRootKey"]["m_data"].hex().upper(),
        header_data_build=header["m_dataBuildNum"],
    )
    try:
        metadata = json.loads(archive.read_file("replay.gamemetadata.json"))
        result.update(metadata=metadata, tvt_candidate=is_tvt(metadata))
    except (ValueError, TypeError, UnicodeError) as error:
        result.update(metadata_error=str(error), tvt_candidate=False)
    # Init data are a useful conservative grouping hint, not a proven universal match ID.
    init_data = archive.read_file("replay.initData")
    result["init_data_sha256"] = digest(init_data)
    result["grouping_status"] = "provisional; cross-perspective deduplication not certified"
    try:
        protocol = versions.build(header["m_version"]["m_baseBuild"])
    except ImportError as error:
        result.update(protocol_status="missing_exact_build", protocol_error=str(error))
        return result
    details = protocol.decode_replay_details(archive.read_file("replay.details"))
    result["protocol_status"] = "exact_build_details_decoded"
    result["played_filetime"] = details.get("m_timeUTC")
    result["players"] = [
        {"control": p.get("m_control"), "team_id": p.get("m_teamId")}
        for p in details.get("m_playerList", [])
    ]
    if events:
        counts = {}
        for stream, decoder in (
            ("game", protocol.decode_replay_game_events),
            ("tracker", protocol.decode_replay_tracker_events),
        ):
            data = archive.read_file(f"replay.{stream}.events")
            count, last_loop = 0, -1
            for event in decoder(data):
                if event["_gameloop"] < last_loop:
                    raise ValueError(f"Non-monotonic {stream} event stream")
                last_loop = event["_gameloop"]
                count += 1
            counts[stream] = {"count": count, "last_loop": last_loop}
        result["events"] = counts
    return result


def inspect_path(path: Path, *, events: bool = False) -> dict:
    result = {"path": str(path.resolve()), "mtime_ns": path.stat().st_mtime_ns}
    try:
        result.update(inspect_replay(path.read_bytes(), events=events))
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
    return result


def summarize(rows: list[dict]) -> dict:
    return {
        "files": len(rows),
        "bytes": sum(r.get("bytes", 0) for r in rows),
        "errors": sum("error" in r for r in rows),
        "metadata_errors": sum("metadata_error" in r for r in rows),
        "tvt_candidates": sum(r.get("tvt_candidate", False) for r in rows),
        "exact_duplicate_files": sum("sha256" in r for r in rows)
        - len({r["sha256"] for r in rows if "sha256" in r}),
        "versions": dict(
            Counter(r.get("metadata", {}).get("GameVersion", "unknown") for r in rows)
        ),
        "protocol_status": dict(Counter(r.get("protocol_status", "failed") for r in rows)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--events", action="store_true")
    args = parser.parse_args()
    rows = [
        inspect_path(p, events=args.events) for p in sorted(args.directory.rglob("*.SC2Replay"))
    ]
    report = {"summary": summarize(rows), "replays": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=True, indent=2)
    print(json.dumps(report["summary"]))


if __name__ == "__main__":
    main()
