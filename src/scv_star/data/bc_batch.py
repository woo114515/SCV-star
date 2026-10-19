"""Resume version-pinned replay extraction; count only fully accepted datasets."""

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import random
import shutil
import time

import psutil
from s2clientprotocol import sc2api_pb2 as sc

from scv_star.data.bc_accept import accept_dataset
from scv_star.data.bc_collection import publish_collection
from scv_star.data.bc_extract import extract
from scv_star.data.map_resources import ensure_replay_resources
from scv_star.envs.sc2_client import Client, message_dict, sha256, validate_version
from scv_star.runtime.alignment_probe import directory_bytes
from scv_star.runtime.replay_probe import human_tvt


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".next")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(100):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.05)


def validate_screen(info, identity):
    validate_version(info, identity)
    if not human_tvt(info):
        raise ValueError("Not two human Terran participants")
    ids = [p["player_info"]["player_id"] for p in info["player_info"]]
    results = [p.get("player_result", {}).get("result") for p in info["player_info"]]
    if set(ids) != {1, 2} or set(results) != {"Defeat", "Victory"}:
        raise ValueError("Unknown outcome or invalid participant identities")
    if info.get("game_duration_loops", 0) <= 4480 or not info.get("map_name"):
        raise ValueError("Insufficient duration or missing map")
    if "单一兵种" in info["map_name"]:
        raise ValueError("Previously identified nonstandard custom map")


def normalise_existing_screen(row, root):
    row = deepcopy(row)
    if row.get("screening_status") != "eligible" or not row.get("outcome_label_ready"):
        return row
    if row.get("human_1v1"):
        return row
    evidence_path = Path(root) / row["screening"]["evidence"]
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if (
        evidence["sha256"] != row["sha256"]
        or evidence["status"] != "eligible"
        or not evidence["original_copy_unchanged"]
    ):
        raise ValueError("Existing API screening evidence mismatch")
    validate_screen(evidence["info"], row["identity"])
    row["human_1v1"] = True
    row["human_confirmation"] = {
        "kind": "pinned_api_replay_info",
        "evidence": str(evidence_path.relative_to(root)),
        "sha256": sha256(evidence_path),
    }
    return row


@lru_cache(maxsize=4)
def public_source_index(root):
    index = {}
    for name, label in (
        ("zenodo-downloads.json", "SC2ReSet"),
        ("extra-packs-downloads.json", "SpawningTool_public"),
        ("recent-packs-downloads.json", "SpawningTool_public"),
        ("drive-downloads.json", "SpawningTool_public"),
    ):
        evidence = Path(root) / "artifacts/runs/m2-public-001" / name
        if not evidence.is_file():
            continue
        for record in json.loads(evidence.read_text(encoding="utf-8")):
            if record.get("status") in ("downloaded", "verified_existing"):
                key = str(record["path"]).replace("\\", "/").lower()
                index[key] = (label, str(evidence), sha256(evidence))
    return index


def normalise_source(row, root=None):
    """Recover display labels from recorded provenance without weakening any terms."""
    row = deepcopy(row)
    if row.get("collection_source"):
        return row
    if not row.get("source_records") or not row.get("source_policies"):
        raise ValueError("Missing recorded source provenance or usage policies")
    root = Path(root) if root is not None else Path(__file__).resolve().parents[3]
    locations = list(row.get("locations", []))
    records = [row]
    for source in row["source_records"]:
        record = source.get("record", {})
        if isinstance(record, dict):
            if record.get("sha256") != row["sha256"]:
                raise ValueError("Source record SHA does not match selected replay")
            records.append(record)
            locations.extend(record.get("locations", []))
    index = public_source_index(root)
    matches = [
        index[key]
        for location in locations
        if location.get("category") == "public"
        and (key := str(location.get("source", "")).replace("\\", "/").lower()) in index
    ]
    kevin_record = any(
        r.get("source") == "Spawning Tool"
        and r.get("source_record") == "spawningtool-kevin-2026-10-05.json"
        for r in records
    )
    kevin_policy = any(
        p.get("source") == "Spawning Tool (Kevin export)"
        and p.get("record") == "data/manifests/spawningtool-kevin-2026-10-05.json"
        for p in row["source_policies"]
    )
    if matches:
        label, evidence, digest = min(matches, key=lambda item: item[0] != "SC2ReSet")
        row["collection_source_evidence"] = {
            "record": str(Path(evidence).relative_to(root)),
            "sha256": digest,
        }
    elif kevin_record and kevin_policy:
        label = "SpawningTool_Kevin_export"
    else:
        raise ValueError("Unrecognised source provenance; manual classification required")
    row["collection_source"] = label
    row["collection_source_basis"] = "recorded_source_records_and_locations"
    return row


def plan_candidates(rows, existing, rejected, seed):
    known_sha, known_groups = {}, {}
    for manifest in existing:
        for record in manifest["files"]:
            known_sha[record["sha256"]] = record["split"]
            group = record["init_data_sha256"]
            if known_groups.setdefault(group, record["split"]) != record["split"]:
                raise ValueError("Existing initialization fingerprint crosses splits")
    result, seen = [], set()
    for original in sorted(rows, key=lambda r: r["sha256"]):
        digest = original["sha256"]
        if digest in known_sha or digest in rejected or digest in seen:
            continue
        if (
            original.get("queue") != "tvt_candidates"
            or original.get("game_loops", 0) <= 4480
            or original.get("identity_mismatches")
            or original.get("races") != ["Terr", "Terr"]
            or original.get("outcome_label_ready") is False
        ):
            continue
        group = original.get("init_data_sha256")
        if not group:
            continue
        row = deepcopy(original)
        score = int(hashlib.sha256(f"{seed}:{group}".encode()).hexdigest()[:16], 16) % 100
        split = "train" if score < 80 else "validation" if score < 90 else "test"
        row["bc_split"] = known_groups.setdefault(group, split)
        result.append(row)
        seen.add(digest)
    random.Random(seed).shuffle(result)
    return result


def pilot_rows(candidates, build, attempts=3):
    """Predetermine representative-duration pilots before looking at their labels."""
    return sorted(
        (r for r in candidates if r["identity"]["base_build"] == build),
        key=lambda r: (abs(r["game_loops"] - 12000), r["sha256"]),
    )[:attempts]


def completed_unit(unit):
    path = Path(unit) / "manifest.json"
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not manifest.get("training_ready") or len(manifest["files"]) != 1:
        raise ValueError("Invalid completed unit manifest")
    for shard in manifest["shards"]:
        if sha256(path.parent / shard["path"]) != shard["sha256"]:
            raise ValueError("Completed unit shard changed")
    return {
        "status": "accepted",
        "sha256": manifest["files"][0]["sha256"],
        "manifest": str(path),
        "manifest_sha256": sha256(path),
        "rows": manifest["rows"],
        "build": manifest["engine_base_build"],
        "split": manifest["files"][0]["split"],
    }


def run_candidate(task):
    root = Path(task["root"])
    unit = Path(task["unit"])
    row = normalise_source(normalise_existing_screen(task["row"], root), root)
    config = task["engine"]
    if ready := completed_unit(unit):
        return ready
    if unit.exists() and not (unit / "replay").exists() and not (unit / "selection.json").exists():
        archive = unit.parent / ".interrupted" / (unit.name + "-" + str(time.time_ns()))
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.resolve().is_relative_to(root / "data/processed"):
            raise ValueError("Unsafe interrupted screening archive")
        unit.rename(archive)
    if (unit / "replay").exists():
        report_path = unit / "replay/report.json"
        saved = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
        if saved.get("status") != "extracted" or not saved.get("cleanup", {}).get("exited"):
            archive = unit.parent / ".interrupted" / (unit.name + "-" + str(time.time_ns()))
            archive.parent.mkdir(parents=True, exist_ok=True)
            if not archive.resolve().is_relative_to(root / "data/processed"):
                raise ValueError("Unsafe interrupted output archive")
            unit.rename(archive)
    if (unit / "selection.json").is_file():
        saved_row = json.loads((unit / "selection.json").read_text(encoding="utf-8"))[0]
        if any(saved_row[key] != row[key] for key in ("sha256", "identity", "bc_split")):
            raise ValueError("Interrupted selection does not match frozen plan")
        row = normalise_source(saved_row, root)
    if psutil.virtual_memory().available < 4_000_000_000:
        raise RuntimeError("Less than 4 GB available RAM")
    unit.mkdir(parents=True, exist_ok=True)
    resources = ensure_replay_resources(
        root / row["local_replay"],
        row["identity"]["base_build"],
        protocol_build=config.get("resource_protocol_build"),
        download=True,
    )
    atomic_json(unit / "resources.json", resources)
    if (
        row.get("screening_status") != "eligible"
        or not row.get("human_1v1")
        or not row.get("outcome_label_ready")
    ):
        local = json.loads((root / config["local"]).read_text(encoding="utf-8"))
        engine = json.loads((root / config["engine"]).read_text(encoding="utf-8"))
        with Client(
            Path(local["install_path"]),
            engine["base_build"],
            unit / "screen-client",
            engine,
            binary=Path(local["engine_binary"]) if local.get("engine_binary") else None,
        ) as client:
            info = message_dict(
                client.call(
                    "replay_info",
                    sc.RequestReplayInfo(
                        replay_path=str((root / row["local_replay"]).resolve()), download_data=False
                    ),
                )
            )
            validate_screen(info, row["identity"])
            atomic_json(
                unit / "screening.json",
                {
                    "sha256": row["sha256"],
                    "identity": row["identity"],
                    "info": info,
                    "engine": client.version,
                    "status": "eligible",
                },
            )
        row.update(
            human_1v1=True,
            human_status="confirmed",
            outcome_label_ready=True,
            screening_status="eligible",
            map_name=info["map_name"],
        )
        row["human_confirmation"] = {
            "kind": "pinned_api_replay_info",
            "evidence": str(unit / "screening.json"),
            "sha256": sha256(unit / "screening.json"),
        }
    atomic_json(unit / "selection.json", [row])
    report_path = unit / "replay/report.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("status") != "extracted" or not report.get("cleanup", {}).get("exited"):
            raise RuntimeError("Interrupted unit must be archived before retry")
    else:
        report = extract(
            root / row["local_replay"],
            unit / "replay",
            root / config["engine"],
            root / config["local"],
            row["bc_split"],
            project_limit_bytes=task["limit_bytes"],
            compression="xz",
        )
    if not report.get("training_ready"):
        return {
            "status": "quality_excluded" if report["status"] == "extracted" else "failed",
            "sha256": row["sha256"],
            "build": row["identity"]["base_build"],
            "error": report.get("error", "coverage_or_minimum_samples_gate"),
        }
    accept_dataset(
        unit,
        unit / "selection.json",
        root / config["boundary"],
        minimum_files=1,
        resource_protocol_build=config.get("resource_protocol_build"),
        required_splits=(row["bc_split"],),
    )
    return completed_unit(unit)


def run_batch(plan_path, registry_path, destination, progress_path, target=1000, workers=6):
    root = Path(__file__).resolve().parents[3]
    destination = Path(destination).resolve()
    if not destination.is_relative_to(root / "data/processed"):
        raise ValueError("Destination must be under data/processed")
    plan_path, progress_path = Path(plan_path), Path(progress_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    existing_paths = [root / p for p in plan["existing_manifests"]]
    existing_count = sum(
        len(json.loads(p.read_text(encoding="utf-8"))["files"]) for p in existing_paths
    )
    plan_hash = sha256(plan_path)
    results = {}
    if progress_path.exists():
        prior = json.loads(progress_path.read_text(encoding="utf-8"))
        if prior["plan_sha256"] != plan_hash:
            raise ValueError("Cannot resume a different selection plan")
        results = {r["sha256"]: r for r in prior["results"]}
    planned = {r["sha256"] for r in plan["candidates"]}
    builds = {r["identity"]["base_build"] for r in plan["candidates"]}
    pilot_reserved = {
        r["sha256"] for build in builds for r in pilot_rows(plan["candidates"], build)
    }
    for path in destination.glob("*/*/manifest.json"):
        if path.parent.name in planned and path.parent.name not in results:
            result = completed_unit(path.parent)
            results[result["sha256"]] = result
    started = time.monotonic()
    attempted = {r["sha256"] for r in results.values()}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        jobs = {}
        while True:
            accepted = [r for r in results.values() if r["status"] == "accepted"]
            if existing_count + len(accepted) >= target and not jobs:
                break
            registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
            limit_bytes = int(registry["project_limit_bytes"])
            if (
                directory_bytes(root) > limit_bytes - 1_000_000_000
                or shutil.disk_usage(root).free < 20_000_000_000
            ):
                raise RuntimeError("Storage guard; checkpoint retained")
            enabled = {}
            for build, config in registry["engines"].items():
                boundary = root / config["boundary"]
                if boundary.is_file() and json.loads(boundary.read_text(encoding="utf-8")).get(
                    "passed"
                ):
                    pilot = root / config.get("pilot_manifest", "__not_verified__")
                    if pilot.is_file() and sha256(pilot) == config.get("pilot_manifest_sha256"):
                        enabled[int(build)] = config
                        pilot_result = completed_unit(pilot.parent)
                        if pilot_result["sha256"] in planned:
                            results[pilot_result["sha256"]] = pilot_result
                            attempted.add(pilot_result["sha256"])
            accepted = [r for r in results.values() if r["status"] == "accepted"]
            if existing_count + len(accepted) >= target and not jobs:
                break
            pending = [
                r
                for r in plan["candidates"]
                if r["sha256"] not in attempted
                and r["identity"]["base_build"] in enabled
                and (registry.get("pilots_finished", False) or r["sha256"] not in pilot_reserved)
            ]
            if not pending and not jobs:
                raise RuntimeError(
                    "No remaining candidates with verified engine/boundary; checkpoint retained"
                )
            batch = pending[
                : max(
                    0,
                    min(
                        min(workers, registry.get("workers", workers)) - len(jobs),
                        target - existing_count - len(accepted) - len(jobs),
                    ),
                )
            ]
            for row in batch:
                digest = row["sha256"]
                attempted.add(digest)
                build = row["identity"]["base_build"]
                task = {
                    "root": str(root),
                    "unit": str(destination / str(build) / digest),
                    "row": row,
                    "engine": enabled[build],
                    "limit_bytes": limit_bytes,
                }
                jobs[pool.submit(run_candidate, task)] = row
            done, _ = wait(jobs, timeout=30, return_when=FIRST_COMPLETED)
            for job in done:
                row = jobs.pop(job)
                try:
                    result = job.result()
                except Exception as error:
                    result = {
                        "status": "failed",
                        "sha256": row["sha256"],
                        "build": row["identity"]["base_build"],
                        "error": f"{type(error).__name__}: {error}",
                    }
                results[row["sha256"]] = result
                accepted = [r for r in results.values() if r["status"] == "accepted"]
                summary = {
                    "plan_sha256": plan_hash,
                    "qualified_total": existing_count + len(accepted),
                    "target": target,
                    "statuses": dict(Counter(r["status"] for r in results.values())),
                    "rows_added": sum(r["rows"] for r in accepted),
                    "seconds_this_run": time.monotonic() - started,
                    "results": list(results.values()),
                }
                atomic_json(progress_path, summary)
                print(json.dumps({k: v for k, v in summary.items() if k != "results"}), flush=True)
    manifests = existing_paths + [
        Path(r["manifest"]) for r in results.values() if r["status"] == "accepted"
    ]
    return publish_collection(destination / "collection.json", manifests, minimum_files=target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "registry", "destination", "progress"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--target", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    result = run_batch(
        args.plan, args.registry, args.destination, args.progress, args.target, args.workers
    )
    print(
        json.dumps(
            {"collection_published": True, "files": result.get("files", result.get("file_count"))}
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
