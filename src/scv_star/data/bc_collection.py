"""Reference immutable, version-specific BC datasets without merging action IDs."""

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path

from scv_star.data.bc_accept import VALID_SPLITS
from scv_star.data.bc_dataset import iter_rows
from scv_star.data.bc_schema import SCHEMA
from scv_star.envs.sc2_client import sha256, validate_version

COLLECTION_SCHEMA = "tvt-bc-collection-v1"


def _inside(root, name):
    path = (root / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError("Dataset member escapes its manifest directory")
    return path


def _checked_file(root, name, digest, memo):
    path = _inside(root, name)
    if path not in memo:
        memo[path] = sha256(path)
    if memo[path] != digest:
        raise ValueError(f"Frozen member hash mismatch: {name}")
    return path


def _inspect_dataset(path):
    """Recheck acceptance evidence and compressed bytes, without decoding rows again."""
    path = Path(path).resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA or not manifest.get("training_ready"):
        raise ValueError("Child dataset has not passed acceptance")
    engine = manifest["engine"]
    validate_version(engine, engine)
    if manifest["engine_base_build"] != engine["base_build"]:
        raise ValueError("Child manifest engine mismatch")
    files = manifest["files"]
    records = {record["sha256"]: record for record in files}
    if not files or len(records) != len(files):
        raise ValueError("Duplicate or empty child replay list")
    memo, reports, counts, row_counts = {}, {}, Counter(), Counter()
    for record in files:
        if record["split"] not in VALID_SPLITS or not record.get("init_data_sha256"):
            raise ValueError("Missing valid split or initialization fingerprint")
        report_path = _checked_file(path.parent, record["report"], record["report_sha256"], memo)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        validate_version(report["engine"], engine)
        if (
            report["engine"] != engine
            or report["replay_sha256"] != record["sha256"]
            or report["split"] != record["split"]
            or report["status"] != "extracted"
            or not report.get("training_ready")
            or not report.get("original_unchanged")
            or not report.get("cleanup", {}).get("exited")
            or report.get("source_sha256") != manifest["extraction_source_sha256"]
        ):
            raise ValueError("Child report does not match frozen acceptance evidence")
        views = report["views"]
        if len(views) != 2 or {view["player_id"] for view in views} != {1, 2}:
            raise ValueError("Each replay must contain both player views")
        _checked_file(
            path.parent,
            str(Path(record["report"]).parent / "game-data.json"),
            record["game_data_sha256"],
            memo,
        )
        reports[record["sha256"]] = (report_path, {v["player_id"]: v for v in views})
        counts[record["split"]] += 1
    seen = defaultdict(set)
    shard_paths = set()
    for shard in manifest["shards"]:
        record = records.get(shard["replay_sha256"])
        if record is None or shard["split"] != record["split"]:
            raise ValueError("Shard replay or split does not match file list")
        player = shard["player_id"]
        if player not in (1, 2) or player in seen[record["sha256"]]:
            raise ValueError("Duplicate or invalid player view")
        seen[record["sha256"]].add(player)
        report_path, views = reports[record["sha256"]]
        view = views[player]
        shard_path = _checked_file(path.parent, shard["path"], shard["sha256"], memo)
        if shard_path in shard_paths:
            raise ValueError("One shard cannot stand for multiple player views")
        shard_paths.add(shard_path)
        if (
            shard_path != (report_path.parent / view["shard"]).resolve()
            or shard["sha256"] != view["shard_sha256"]
            or shard["rows"] != view["accepted"]
            or shard["rows"] < 128
            or view["coverage"] < 0.98
            or not view["same_pass_labels"]
            or not view["accepted_roundtrip_verified"]
            or shard.get("engine_base_build", engine["base_build"]) != engine["base_build"]
            or shard.get("data_version", engine["data_version"]) != engine["data_version"]
        ):
            raise ValueError("Shard metadata disagrees with accepted view")
        static_map = _checked_file(
            path.parent, shard["static_map"], shard["static_map_sha256"], memo
        )
        if static_map != report_path.parent / "map-data.json":
            raise ValueError("Static map does not belong to the replay report")
        row_counts[shard["split"]] += shard["rows"]
    if set(seen) != set(records) or any(players != {1, 2} for players in seen.values()):
        raise ValueError("Missing player view in child manifest")
    if (
        dict(counts) != manifest["file_splits"]
        or sum(row_counts.values()) != manifest["rows"]
        or dict(row_counts) != manifest["rows_by_split"]
    ):
        raise ValueError("Child manifest counts disagree with its accepted views")
    if len(set(manifest["ability_ids"])) != len(manifest["ability_ids"]):
        raise ValueError("Duplicate child ability IDs")
    return manifest


def _reference(path, parent):
    try:
        return Path(os.path.relpath(path, parent)).as_posix()
    except ValueError:  # Different Windows volumes have no relative representation.
        return path.as_posix()


def publish_collection(output, manifests, minimum_files=1000, required_splits=VALID_SPLITS):
    """Publish a new index after checking hashes and global split consistency.

    Child acceptance already audited every row. This pass rechecks the frozen
    bytes and counts, and never rewrites, copies or recompresses child datasets.
    """
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Collection exists")
    required_splits = tuple(required_splits)
    if minimum_files < 1 or not required_splits or not set(required_splits) <= set(VALID_SPLITS):
        raise ValueError("Invalid minimum files or required splits")
    datasets, catalogs, replay_splits, init_splits = [], [], {}, {}
    file_counts, rows, versions = Counter(), Counter(), Counter()
    paths = [Path(path).resolve() for path in manifests]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate child manifest")
    for path in paths:
        digest = sha256(path)
        child = _inspect_dataset(path)
        # Reject a concurrently replaced manifest rather than pinning unchecked metadata.
        if sha256(path) != digest:
            raise ValueError("Child manifest changed during collection acceptance")
        for record in child["files"]:
            replay, split = record["sha256"], record["split"]
            if replay in replay_splits:
                raise ValueError("Duplicate replay SHA across child datasets")
            replay_splits[replay] = split
            fingerprint = record["init_data_sha256"]
            if init_splits.setdefault(fingerprint, split) != split:
                raise ValueError("Known initialization fingerprint crosses splits")
        entry = {
            "manifest": _reference(path, output.parent),
            "manifest_sha256": digest,
            "engine": child["engine"],
            "engine_base_build": child["engine_base_build"],
            "files": len(child["files"]),
            "rows": child["rows"],
        }
        datasets.append(entry)
        catalogs.append(
            {
                "manifest_sha256": digest,
                "engine": child["engine"],
                "ability_ids": child["ability_ids"],
            }
        )
        file_counts.update(child["file_splits"])
        rows.update(child["rows_by_split"])
        versions[str(child["engine_base_build"])] += len(child["files"])
    if len(replay_splits) < minimum_files or not set(required_splits) <= set(file_counts):
        raise ValueError("Collection does not meet file count or required splits")
    collection = {
        "schema": COLLECTION_SCHEMA,
        "row_schema": SCHEMA,
        "training_ready": True,
        "datasets": datasets,
        "files": len(replay_splits),
        "player_views": len(replay_splits) * 2,
        "rows": sum(rows.values()),
        "file_splits": dict(file_counts),
        "rows_by_split": dict(rows),
        "files_by_build": dict(versions),
        "ability_catalogs": catalogs,
        "action_ids_merged_across_versions": False,
        "minimum_files_gate": minimum_files,
        "required_splits": list(required_splits),
        "known_initialization_groups_crossing_splits": 0,
        "same_match_deduplication_performed": False,
        "cross_copy_leakage_not_ruled_out": True,
        "redistribution_allowed": False,
        "acceptance_source_sha256": sha256(Path(__file__)),
        "verification": "frozen child acceptance, manifest/report/map/game-data/shard hashes",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(collection, stream, indent=2)
    return collection


def iter_collection_rows(collection_path, split, builds=None):
    """Stream accepted rows, optionally restricting base builds; retain original IDs."""
    collection_path = Path(collection_path).resolve()
    collection = json.loads(collection_path.read_text(encoding="utf-8"))
    if (
        collection.get("schema") != COLLECTION_SCHEMA
        or collection.get("row_schema") != SCHEMA
        or not collection.get("training_ready")
        or split not in VALID_SPLITS
    ):
        raise ValueError("Invalid or unaccepted collection/split")
    selected = None if builds is None else {int(b) for b in builds}
    for dataset in collection["datasets"]:
        engine = dataset["engine"]
        if selected is not None and engine["base_build"] not in selected:
            continue
        path = (collection_path.parent / dataset["manifest"]).resolve()
        if sha256(path) != dataset["manifest_sha256"]:
            raise ValueError("Frozen child manifest hash mismatch")
        child = json.loads(path.read_text(encoding="utf-8"))
        if child["engine"] != engine or child["engine_base_build"] != engine["base_build"]:
            raise ValueError("Collection child engine mismatch")
        files = {record["sha256"]: record for record in child["files"]}
        for row in iter_rows(path, split):
            record = files.get(row["replay_sha256"])
            if (
                record is None
                or record["split"] != row["split"]
                or row["player_id"] not in (1, 2)
                or row["engine_base_build"] != engine["base_build"]
                or row["data_version"] != engine["data_version"]
            ):
                raise ValueError("Row version/player/split does not match child manifest")
            yield row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifests", nargs="+", type=Path, required=True)
    parser.add_argument("--minimum-files", type=int, default=1000)
    parser.add_argument("--required-splits", nargs="+", choices=VALID_SPLITS, default=VALID_SPLITS)
    args = parser.parse_args()
    collection = publish_collection(**vars(args))
    print(json.dumps({key: collection[key] for key in ("files", "player_views", "rows")}))


if __name__ == "__main__":
    main()
