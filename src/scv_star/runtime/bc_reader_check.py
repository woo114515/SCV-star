"""Check version-specific BC readers on NVIDIA CUDA, without optimizer updates."""

import argparse
import itertools
import json
import math
from pathlib import Path

from scv_star.data.bc_collection import COLLECTION_SCHEMA, iter_collection_rows
from scv_star.data.bc_dataset import STATIC_MAP_FIELDS, collate, iter_rows
from scv_star.data.bc_storage import open_shard
from scv_star.envs.sc2_client import sha256


def largest_observation(child_path, shard):
    """Read the exact frozen shard; action indices repeat across games and views."""
    child_path = Path(child_path)
    shard_path = child_path.parent / shard["path"]
    map_path = child_path.parent / shard["static_map"]
    if sha256(shard_path) != shard["sha256"] or sha256(map_path) != shard["static_map_sha256"]:
        raise ValueError("Largest observation resource changed")
    static_map = json.loads(map_path.read_text(encoding="utf-8"))
    with open_shard(shard_path) as stream:
        for line in stream:
            row = json.loads(line)
            if (
                row["replay_sha256"] == shard["replay_sha256"]
                and row["player_id"] == shard["player_id"]
                and row["source_action_index"] == shard["max_entities_source_index"]
            ):
                if len(row["observation"]["entities"]) != shard["max_entities"]:
                    raise ValueError("Largest observation metadata mismatch")
                row["_static_map"] = {key: static_map[key] for key in STATIC_MAP_FIELDS}
                return row
    raise ValueError("Largest observation is missing from its frozen shard")


def check_collection(path, output):
    import torch
    import torch.nn.functional as functional

    path, output = Path(path).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new GPU check report")
    collection_hash = sha256(path)
    collection = json.loads(path.read_text(encoding="utf-8"))
    if collection.get("schema") != COLLECTION_SCHEMA or not collection.get("training_ready"):
        raise ValueError("Expected an accepted versioned collection")
    if not torch.cuda.is_available():
        raise RuntimeError("NVIDIA CUDA is unavailable")
    device = torch.cuda.get_device_name(0)
    if "NVIDIA" not in device or "5070" not in device:
        raise RuntimeError(f"Unexpected GPU: {device}")
    torch.manual_seed(20261019)
    torch.cuda.reset_peak_memory_stats()
    cases, seen, largest = [], set(), None
    for entry in collection["datasets"]:
        child_path = (path.parent / entry["manifest"]).resolve()
        if sha256(child_path) != entry["manifest_sha256"]:
            raise ValueError("Child manifest changed")
        child = json.loads(child_path.read_text(encoding="utf-8"))
        key = (child["engine_base_build"], child["engine"]["data_version"])
        if key not in seen:
            split = child["files"][0]["split"]
            rows = list(itertools.islice(iter_rows(child_path, split), 16))
            cases.append((f"build-{key[0]}-{split}", rows, child["ability_ids"]))
            seen.add(key)
        for shard in child["shards"]:
            if largest is None or shard["max_entities"] > largest[0]:
                largest = (shard["max_entities"], child_path, child, shard)
    for split in ("train", "validation", "test"):
        rows = list(itertools.islice(iter_collection_rows(path, split), 16))
        if not rows:
            raise ValueError("Missing required split")
        engine = (rows[0]["engine_base_build"], rows[0]["data_version"])
        if any((r["engine_base_build"], r["data_version"]) != engine for r in rows):
            # The smoke deliberately forms same-version batches.
            rows = [r for r in rows if (r["engine_base_build"], r["data_version"]) == engine]
        catalog = next(
            c["ability_ids"]
            for c in collection["ability_catalogs"]
            if (c["engine"]["base_build"], c["engine"]["data_version"]) == engine
        )
        cases.append(("split-" + split, rows, catalog))
    _, child_path, child, shard = largest
    row = largest_observation(child_path, shard)
    cases.append(("largest-observation", [row] * 16, child["ability_ids"]))
    results = []
    for name, rows, abilities in cases:
        batch = collate(rows, abilities)
        if not all(m is not None and "start_locations" not in m for m in batch["static_maps"]):
            raise ValueError("Static map filtering failed")
        targets = {key: value.cuda() for key, value in batch["targets"].items()}
        entities, mask = batch["entities"].cuda() / 1000, batch["entity_mask"].cuda()
        scalars = batch["scalars"].cuda() / 1000
        pooled = (entities * mask[..., None]).sum(1) / mask.sum(1, keepdim=True)
        inputs = torch.cat((pooled, scalars), dim=1)
        model = torch.nn.Sequential(
            torch.nn.Linear(inputs.shape[1], 64),
            torch.nn.ReLU(),
            torch.nn.Linear(64, len(abilities)),
        ).cuda()
        before = [p.detach().clone() for p in model.parameters()]
        loss = functional.cross_entropy(model(inputs), targets["ability"])
        loss.backward()
        if not math.isfinite(loss.item()) or not all(
            p.grad is not None and torch.isfinite(p.grad).all() for p in model.parameters()
        ):
            raise ValueError("Non-finite GPU loss or gradients")
        if not all(torch.equal(a, b) for a, b in zip(before, model.parameters())):
            raise ValueError("Unexpected parameter update")
        results.append(
            {
                "case": name,
                "rows": len(rows),
                "entities_shape": list(entities.shape),
                "loss": loss.item(),
            }
        )
    if sha256(path) != collection_hash:
        raise ValueError("Collection changed during GPU check")
    report = {
        "passed": True,
        "collection_sha256": collection_hash,
        "check_source_sha256": sha256(Path(__file__)),
        "gpu": device,
        "torch_version": torch.__version__,
        "cases": results,
        "versions_tested": len(seen),
        "peak_cuda_bytes": torch.cuda.max_memory_allocated(),
        "optimizer_steps": 0,
        "purpose": "reader/tensor/differentiability smoke; not policy training",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = check_collection(args.collection, args.output)
    print(
        json.dumps({k: report[k] for k in ("passed", "gpu", "versions_tested", "optimizer_steps")})
    )


if __name__ == "__main__":
    main()
