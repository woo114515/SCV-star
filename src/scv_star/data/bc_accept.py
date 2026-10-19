"""Validate every BC shard and publish a separate, immutable training manifest."""

import argparse
import base64
from collections import Counter
import json
import math
from pathlib import Path
from importlib.metadata import version
import platform
import os

import mpyq
from s2protocol import versions

from scv_star.data.bc_schema import SCHEMA, UNIT_FIELDS, SCALAR_FIELDS, decode_action
from scv_star.envs.sc2_client import sha256, validate_version
from scv_star.data.replay_sources import inspect_path
from scv_star.runtime.replay_probe import replay_identity
from scv_star.data.bc_storage import open_shard


def verify_resource(handle, cache_root, memo):
    if len(handle) != 40:
        raise ValueError("Invalid map cache handle length")
    extension = handle[:4].decode("ascii").strip("\x00 ")
    if extension not in ("s2ma", "s2mv"):
        raise ValueError("Invalid map cache resource type")
    digest = handle[8:].hex()
    path = Path(cache_root) / digest[:2] / digest[2:4] / f"{digest}.{extension}"
    if str(path) not in memo:
        if not path.is_file() or sha256(path) != digest:
            raise ValueError(f"Missing or corrupt map resource: {digest}.{extension}")
        memo[str(path)] = {"sha256": digest, "extension": extension, "bytes": path.stat().st_size}
    return memo[str(path)]


def replay_resources(path, build, cache_root, memo):
    archive = mpyq.MPQArchive(str(path))
    try:
        details = versions.build(build).decode_replay_details(archive.read_file("replay.details"))
        handles = details.get("m_cacheHandles", [])
        if not handles:
            raise ValueError("Replay has no exact resource handles")
        return [verify_resource(handle, cache_root, memo) for handle in handles]
    finally:
        archive.file.close()


def inspect_shard(path, report, view):
    if sha256(path) != view["shard_sha256"]:
        raise ValueError("Shard hash mismatch")
    count = 0
    previous_index = -1
    previous_loop = -1
    abilities = Counter()
    kinds = Counter()
    groups = Counter()
    max_entities = 0
    max_entities_source_index = None
    previous_row = None
    blip_rows = 0
    queued_actions = 0
    addon_rows = 0
    cargo_rows = 0
    max_order_queue = 0
    with open_shard(path) as stream:
        for line in stream:
            row = json.loads(line)
            obs = row["observation"]
            action = row["action"]
            tags = obs["entity_tags"]
            assert len(obs["scalars"]) == len(SCALAR_FIELDS)
            assert all(math.isfinite(value) for value in obs["scalars"])
            assert row["schema"] == SCHEMA and row["replay_sha256"] == report["replay_sha256"]
            assert (
                row["player_id"] == view["player_id"] == obs["player_id"]
                and row["split"] == report["split"]
            )
            assert row["engine_base_build"] == report["engine"]["base_build"]
            assert row["data_version"] == report["engine"]["data_version"]
            assert (
                row["source_action_index"] > previous_index and row["action_loop"] >= previous_loop
            )
            assert (
                obs["loop"] == max(0, row["action_loop"] - 2) and obs["loop"] < row["action_loop"]
            )
            assert len(tags) == len(set(tags)) == len(obs["entities"]) and tags == sorted(tags)
            assert set(obs) == {
                "loop",
                "player_id",
                "scalars",
                "upgrades",
                "entity_tags",
                "entities",
                "visibility",
                "creep",
                "blips",
            }
            for unit in obs["entities"]:
                assert len(unit["values"]) == len(UNIT_FIELDS) and all(
                    math.isfinite(v) for v in unit["values"]
                )
                if unit["values"][UNIT_FIELDS.index("alliance")] != 1:
                    assert (
                        not unit["orders"] and not unit["passengers"] and unit["addon_index"] == -1
                    )
                if unit["stale"]:
                    for field in [
                        "health",
                        "shield",
                        "energy",
                        "weapon_cooldown",
                        "assigned_harvesters",
                    ]:
                        assert unit["values"][UNIT_FIELDS.index(field)] == 0
                assert -1 <= unit["addon_index"] < len(tags)
                max_order_queue = max(max_order_queue, len(unit["orders"]))
                for order in unit["orders"]:
                    assert -1 <= order["target_tag_index"] < len(tags)
            addon_rows += any(u["addon_index"] >= 0 for u in obs["entities"])
            cargo_rows += any(u["passengers"] for u in obs["entities"])
            for layer in ("visibility", "creep"):
                image = obs[layer]
                data = base64.b64decode(image["data"], validate=True)
                assert len(data) == (image["width"] * image["height"] * image["bits"] + 7) // 8
            for index in action["selected_indices"]:
                assert 0 <= index < len(tags)
                assert obs["entities"][index]["values"][UNIT_FIELDS.index("alliance")] == 1
            if action["target_kind"] == "unit":
                assert 0 <= action["target_index"] < len(tags)
            elif action["target_kind"] == "point":
                assert len(action["target_point"]) == 2
                assert all(math.isfinite(value) for value in action["target_point"])
            else:
                assert action["target_kind"] == "none"
            assert action["kind"] in ("command", "autocast")
            decode_action(action, tags)
            assert row["next_action_delay"] is None or row["next_action_delay"] >= 0
            assert row["delay_mask"] == (row["next_action_delay"] is not None)
            if previous_row is not None:
                adjacent = row["source_action_index"] == previous_index + 1
                assert previous_row["delay_mask"] == adjacent
                if adjacent:
                    assert previous_row["next_action_delay"] == row["action_loop"] - previous_loop
            for blip in obs["blips"]:
                assert set(blip) == {"position", "alliance", "display_type"}
                assert all(math.isfinite(v) for v in blip["position"])
            blip_rows += bool(obs["blips"])
            queued_actions += action["queue"] is True
            abilities[action["ability_id"]] += 1
            kinds[action["kind"]] += 1
            groups[row["action_loop"]] += 1
            count += 1
            previous_index = row["source_action_index"]
            previous_loop = row["action_loop"]
            previous_row = row
            if len(tags) > max_entities:
                max_entities = len(tags)
                max_entities_source_index = row["source_action_index"]
    assert count == view["accepted"] and count >= 128
    assert previous_row is not None and not previous_row["delay_mask"]
    return {
        "rows": count,
        "ability_counts": dict(abilities),
        "kind_counts": dict(kinds),
        "same_loop_groups": sum(n > 1 for n in groups.values()),
        "max_entities": max_entities,
        "max_entities_source_index": max_entities_source_index,
        "blip_rows": blip_rows,
        "queued_actions": queued_actions,
        "addon_rows": addon_rows,
        "cargo_rows": cargo_rows,
        "max_order_queue": max_order_queue,
    }


VALID_SPLITS = ("train", "validation", "test")


def resource_protocol_identity(build, resource_protocol_build=None):
    """Resolve a declared details-only parser; never guess a compatible version."""
    selected = build if resource_protocol_build is None else resource_protocol_build
    protocol = versions.build(selected)
    return {
        "replay_base_build": build,
        "protocol_build": selected,
        "exact_build": selected == build,
        "scope": "replay.details static map cache handles only",
        "module_sha256": sha256(Path(protocol.__file__)),
    }


def accept_dataset(
    root,
    selection,
    boundary,
    minimum_files=100,
    cache_root=None,
    resource_protocol_build=None,
    required_splits=VALID_SPLITS,
):
    """Audit one pinned engine bucket and publish a new immutable manifest.

    An explicit alternate protocol only decodes map dependency handles. The
    replay header and API-reported engine identity must still match exactly.
    """
    if not __debug__:
        raise RuntimeError("Acceptance requires Python assertions; do not use -O")
    root, selection_path, boundary_path = map(Path, (root, selection, boundary))
    root = root.resolve()
    required_splits = tuple(required_splits)
    if minimum_files < 1 or not required_splits or not set(required_splits) <= set(VALID_SPLITS):
        raise ValueError("Invalid minimum files or required splits")
    cache_root = (
        Path(cache_root)
        if cache_root is not None
        else Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        / "Blizzard Entertainment/Battle.net/Cache"
    )
    if (root / "manifest.json").exists():
        raise ValueError("Manifest exists")
    boundary = json.loads(boundary_path.read_text(encoding="utf-8"))
    assert boundary["passed"], "Boundary evidence did not pass"
    expected = boundary["engine"]
    validate_version(expected, expected)
    build = expected["base_build"]
    resource_protocol = resource_protocol_identity(build, resource_protocol_build)
    selection = {r["sha256"]: r for r in json.loads(selection_path.read_text(encoding="utf-8"))}
    shards = []
    files = []
    excluded = []
    summaries = []
    vocabulary = set()
    observed = Counter()
    file_splits = Counter()
    init_splits = {}
    engine = None
    extraction_sources = None
    ability_names = {}
    coverages = []
    quarantine = Counter()
    action_errors = 0
    abilities_by_split = {split: Counter() for split in ("train", "validation", "test")}
    resource_memo = {}
    project_root = Path(__file__).resolve().parents[3]
    for path in sorted(root.glob("*/report.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        if report["status"] != "extracted" or not report["training_ready"]:
            excluded.append(
                {
                    "sha256": report["replay_sha256"],
                    "status": report["status"],
                    "reason": report.get("error", "coverage_or_minimum_samples_gate"),
                    "minimum_view_coverage": min(
                        (v["coverage"] for v in report["views"]), default=None
                    ),
                }
            )
            continue
        source = selection[report["replay_sha256"]]
        assert (
            report["split"] == source["bc_split"]
            and report["original_unchanged"]
            and report["cleanup"]["exited"]
        )
        assert len(report["views"]) == 2 and {v["player_id"] for v in report["views"]} == {1, 2}
        validate_version(report["engine"], expected)
        if expected.get("binary_sha256"):
            assert report["engine"]["binary_sha256"] == expected["binary_sha256"]
        if engine is None:
            engine = report["engine"]
            extraction_sources = report["source_sha256"]
        assert report["engine"] == engine and report["source_sha256"] == extraction_sources
        assert report["split"] in VALID_SPLITS
        validate_version(source["identity"], expected)
        assert source["screening_status"] == "eligible" and source["human_1v1"]
        assert source["races"] == ["Terr", "Terr"] and source["outcome_label_ready"]
        init_hash = source["init_data_sha256"]
        assert init_hash, "Missing initialization fingerprint"
        assert init_splits.setdefault(init_hash, report["split"]) == report["split"], (
            "Known initialization fingerprint crosses splits"
        )
        gamedata = json.loads((path.parent / "game-data.json").read_text())
        static_map = path.parent / "map-data.json"
        map_data = json.loads(static_map.read_text())
        assert map_data["map_size"]["x"] > 0 and map_data["map_size"]["y"] > 0
        for layer in ("pathing_grid", "placement_grid", "terrain_height"):
            image = map_data[layer]
            assert image["bits_per_pixel"] in (1, 8)
            assert image["size"] == map_data["map_size"]
            assert (
                len(base64.b64decode(image["data"], validate=True))
                == (image["size"]["x"] * image["size"]["y"] * image["bits_per_pixel"] + 7) // 8
            )
        static_map_hash = sha256(static_map)
        raw_replay = project_root / source["local_replay"]
        assert sha256(raw_replay) == report["replay_sha256"]
        validate_version(replay_identity(inspect_path(raw_replay)), expected)
        resources = replay_resources(
            raw_replay, resource_protocol["protocol_build"], cache_root, resource_memo
        )
        vocabulary.update(a["ability_id"] for a in gamedata["abilities"])
        ability_names.update(
            {a["ability_id"]: a.get("friendly_name", "") for a in gamedata["abilities"]}
        )
        for view in report["views"]:
            assert (
                view["coverage"] >= 0.98
                and view["same_pass_labels"]
                and view["accepted_roundtrip_verified"]
            )
            shard = path.parent / view["shard"]
            summary = inspect_shard(shard, report, view)
            assert set(summary["ability_counts"]) <= vocabulary
            coverages.append(view["coverage"])
            quarantine.update(view["quarantine"])
            action_errors += view["api_action_types"].get("action_errors", 0)
            summaries.append(summary)
            observed.update(summary["ability_counts"])
            abilities_by_split[report["split"]].update(summary["ability_counts"])
            shards.append(
                {
                    "path": shard.relative_to(root).as_posix(),
                    "sha256": view["shard_sha256"],
                    "split": report["split"],
                    "replay_sha256": report["replay_sha256"],
                    "player_id": view["player_id"],
                    "rows": summary["rows"],
                    "static_map": static_map.relative_to(root).as_posix(),
                    "static_map_sha256": static_map_hash,
                    "engine_base_build": build,
                    "data_version": engine["data_version"],
                    "max_entities": summary["max_entities"],
                    "max_entities_source_index": summary["max_entities_source_index"],
                }
            )
        files.append(
            {
                "sha256": report["replay_sha256"],
                "split": report["split"],
                "init_data_sha256": init_hash,
                "collection_source": source["collection_source"],
                "map_name": report["map_name"],
                "game_data_sha256": sha256(path.parent / "game-data.json"),
                "replay_resources": resources,
                "source_records": source["source_records"],
                "source_policies": source["source_policies"],
                "report": path.relative_to(root).as_posix(),
                "report_sha256": sha256(path),
            }
        )
        file_splits[report["split"]] += 1
        print(
            json.dumps({"validated_files": len(files), "rows": sum(s["rows"] for s in shards)}),
            flush=True,
        )
    assert len(files) >= minimum_files, "Insufficient accepted files"
    assert set(required_splits) <= set(file_splits), "Missing required split"
    assert set(file_splits) <= set(VALID_SPLITS)
    assert len({f["sha256"] for f in files}) == len(files)
    manifest = {
        "schema": SCHEMA,
        "training_ready": True,
        "engine_base_build": build,
        "resource_protocol": resource_protocol,
        "required_splits": list(required_splits),
        "engine": engine,
        "extraction_source_sha256": extraction_sources,
        "acceptance_source_sha256": sha256(Path(__file__)),
        "reader_source_sha256": {
            name: sha256(project_root / "src/scv_star/data" / name)
            for name in ("bc_dataset.py", "bc_storage.py", "bc_repack.py")
        },
        "dependency_lock_sha256": {
            str(path.relative_to(project_root)): sha256(path)
            for path in sorted((project_root / "requirements").glob("*.txt"))
        },
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "dependency_versions": {
            name: version(name)
            for name in (
                "s2clientprotocol",
                "s2protocol",
                "mpyq",
                "protobuf",
                "websocket-client",
                "psutil",
                "torch",
            )
        },
        "unit_fields": list(UNIT_FIELDS),
        "scalar_fields": list(SCALAR_FIELDS),
        "policy_action_types": ["unit_command", "toggle_autocast"],
        "minimum_files_gate": minimum_files,
        "files": files,
        "shards": shards,
        "ability_ids": sorted(vocabulary),
        "file_splits": dict(file_splits),
        "rows": sum(s["rows"] for s in shards),
        "observed_ability_counts": dict(observed),
        "observed_ability_names": {a: ability_names[a] for a in sorted(observed)},
        "ability_counts_by_split": {
            split: dict(counts) for split, counts in abilities_by_split.items()
        },
        "rows_by_split": {
            s: sum(r["rows"] for r in shards if r["split"] == s) for s in file_splits
        },
        "minimum_view_coverage": min(coverages),
        "quarantine_reasons": dict(quarantine),
        "api_action_errors": action_errors,
        "excluded": excluded,
        "max_entities": max(s["max_entities"] for s in summaries),
        "same_loop_groups": sum(s["same_loop_groups"] for s in summaries),
        "autocast_rows": sum(s["kind_counts"].get("autocast", 0) for s in summaries),
        "blip_rows": sum(s["blip_rows"] for s in summaries),
        "queued_actions": sum(s["queued_actions"] for s in summaries),
        "addon_rows": sum(s["addon_rows"] for s in summaries),
        "cargo_rows": sum(s["cargo_rows"] for s in summaries),
        "max_order_queue": max(s["max_order_queue"] for s in summaries),
        "known_initialization_groups_crossing_splits": 0,
        "verified_map_resources": list(resource_memo.values()),
        "selection_sha256": sha256(selection_path),
        "boundary_sha256": sha256(boundary_path),
        "same_match_deduplication_performed": False,
        "cross_copy_leakage_not_ruled_out": True,
        "policy_input_excludes_outcomes": True,
        "spawningtool_redistribution_allowed": False,
    }
    with (root / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="One dataset build directory")
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--boundary", type=Path, required=True)
    parser.add_argument("--minimum-files", type=int, default=100)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--resource-protocol-build", type=int)
    parser.add_argument("--required-splits", nargs="+", choices=VALID_SPLITS, default=VALID_SPLITS)
    args = parser.parse_args()
    manifest = accept_dataset(**vars(args))
    print(
        json.dumps(
            {
                k: manifest[k]
                for k in [
                    "training_ready",
                    "file_splits",
                    "rows",
                    "max_entities",
                    "same_loop_groups",
                    "autocast_rows",
                ]
            }
        )
    )


if __name__ == "__main__":
    main()
