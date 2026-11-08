"""Build and load a tensorized BC sequence cache from a frozen accepted collection."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import torch

from scv_star.data.bc_catalog import build_ability_catalog, catalog_digest, sha256_file
from scv_star.data.bc_collection import iter_collection_rows
from scv_star.data.bc_dataset import collate, iter_sequences
from scv_star.data.bc_features import (
    ENTITY_NUMERIC_INDEX,
    NUMERIC_FIELDS,
    SCALAR_INPUT_FIELDS,
)
from scv_star.data.bc_schema import UNIT_FIELDS

CACHE_SCHEMA = "tvt-bc-cache-v1"
ROW_SCHEMA = "tvt-bc-v2"
VALID_SPLITS = ("train", "validation", "test")
UNIT_TYPE_INDEX = UNIT_FIELDS.index("unit_type")
NUMERIC_INDEX = torch.tensor(ENTITY_NUMERIC_INDEX, dtype=torch.long)
ROOT = Path(__file__).resolve().parents[3]


def _sha256(path):
    return sha256_file(Path(path))


def _relative(path, start):
    try:
        return Path(os.path.relpath(path, start)).as_posix()
    except ValueError:  # Different drives on Windows.
        return Path(path).as_posix()


def _new_stats():
    return {
        "entity_count": 0,
        "entity_sum": torch.zeros(len(NUMERIC_FIELDS), dtype=torch.float64),
        "entity_sumsq": torch.zeros(len(NUMERIC_FIELDS), dtype=torch.float64),
        "scalar_count": 0,
        "scalar_sum": torch.zeros(len(SCALAR_INPUT_FIELDS), dtype=torch.float64),
        "scalar_sumsq": torch.zeros(len(SCALAR_INPUT_FIELDS), dtype=torch.float64),
    }


def _accumulate(stats, entities, entity_mask, stale, scalars):
    numeric = entities.index_select(-1, NUMERIC_INDEX)
    valid = entity_mask & ~stale
    values = numeric[valid].double()
    stats["entity_count"] += values.shape[0]
    stats["entity_sum"] += values.sum(0)
    stats["entity_sumsq"] += (values**2).sum(0)
    scalars = scalars.double()
    stats["scalar_count"] += scalars.shape[0]
    stats["scalar_sum"] += scalars.sum(0)
    stats["scalar_sumsq"] += (scalars**2).sum(0)


def _finalize_normalization(stats):
    if stats["entity_count"] == 0 or stats["scalar_count"] == 0:
        raise ValueError("Cannot build normalization statistics without training rows")

    def moments(total, sumsq, count):
        mean = total / count
        variance = torch.clamp(sumsq / count - mean * mean, min=0.0)
        return mean, torch.clamp(torch.sqrt(variance), min=1e-6)

    entity_mean, entity_std = moments(
        stats["entity_sum"], stats["entity_sumsq"], stats["entity_count"]
    )
    scalar_mean, scalar_std = moments(
        stats["scalar_sum"], stats["scalar_sumsq"], stats["scalar_count"]
    )
    return {
        "entity_fields": list(NUMERIC_FIELDS),
        "entity_mean": entity_mean.tolist(),
        "entity_std": entity_std.tolist(),
        "scalar_fields": list(SCALAR_INPUT_FIELDS),
        "scalar_mean": scalar_mean.tolist(),
        "scalar_std": scalar_std.tolist(),
        "train_entity_rows": stats["entity_count"],
        "train_scalar_rows": stats["scalar_count"],
    }


def _stale_tensor(rows, entity_count):
    stale = torch.zeros(len(rows), entity_count, dtype=torch.bool)
    for step, row in enumerate(rows):
        for index, entity in enumerate(row["observation"]["entities"]):
            if index >= entity_count:
                raise ValueError("Entity count changed inside a sequence")
            stale[step, index] = bool(entity["stale"])
    return stale


def _map_size(static_maps):
    if not static_maps or any(item is None for item in static_maps):
        raise ValueError("Every sequence row must carry a static map")
    sizes = {(int(item["map_size"]["x"]), int(item["map_size"]["y"])) for item in static_maps}
    if len(sizes) != 1:
        raise ValueError("A sequence must stay on one map")
    width, height = sizes.pop()
    if width <= 0 or height <= 0:
        raise ValueError("Static map size must be positive")
    return (width, height)


def _sequence_to_tensors(
    rows,
    catalog,
    *,
    collect,
    stats,
    unit_types,
    ability_kinds,
):
    ability_ids = [int(value) for value in catalog["ability_ids"]]
    vocabulary = {ability: index for index, ability in enumerate(ability_ids)}
    for row in rows:
        raw = int(row["action"]["ability_id"])
        if raw not in vocabulary:
            raise ValueError(
                f"Ability {raw} outside canonical catalog in replay {row['replay_sha256']}"
            )
    batch = collate(rows, ability_ids)
    entity_mask = batch["entity_mask"]
    entities = batch["entities"]
    stale = _stale_tensor(rows, entities.shape[1])
    map_size = _map_size(batch["static_maps"])
    for step, row in enumerate(rows):
        expected = vocabulary[int(row["action"]["ability_id"])]
        if int(batch["targets"]["ability"][step]) != expected:
            raise ValueError("Collated ability index disagrees with the canonical catalog")
        if collect:
            ability_kinds.setdefault(expected, set()).add(row["action"]["kind"])
    if collect:
        _accumulate(stats, entities, entity_mask, stale, batch["scalars"])
        types = entities[..., UNIT_TYPE_INDEX][entity_mask]
        unit_types.update(int(value) for value in types.tolist())
    first, last = rows[0], rows[-1]
    if first["replay_sha256"] != last["replay_sha256"] or first["player_id"] != last["player_id"]:
        raise ValueError("Sequence crossed a replay or player view")
    if any(row["split"] != first["split"] for row in rows):
        raise ValueError("Sequence crossed splits")
    key = (
        str(first["replay_sha256"]),
        int(first["player_id"]),
        int(first["source_action_index"]),
        int(last["source_action_index"]),
    )
    return {
        "key": key,
        "split": first["split"],
        "length": len(rows),
        "entities": entities.to(torch.float32).contiguous(),
        "entity_mask": entity_mask.contiguous(),
        "stale": stale.contiguous(),
        "scalars": batch["scalars"].to(torch.float32).contiguous(),
        "ability": batch["targets"]["ability"].contiguous(),
        "kind": batch["targets"]["kind"].contiguous(),
        "selected_indices": batch["targets"]["selected_indices"].contiguous(),
        "selected_mask": batch["targets"]["selected_mask"].contiguous(),
        "target_kind": batch["targets"]["target_kind"].contiguous(),
        "target_index": batch["targets"]["target_index"].contiguous(),
        "target_point": batch["targets"]["target_point"].to(torch.float32).contiguous(),
        "queue": batch["targets"]["queued"].contiguous(),
        "queue_mask": batch["targets"]["queue_mask"].contiguous(),
        "delay": batch["targets"]["delay"].to(torch.float32).contiguous(),
        "delay_mask": batch["targets"]["delay_mask"].contiguous(),
        "map_size": (int(map_size[0]), int(map_size[1])),
    }


def _write_shard(path, split, sequences):
    torch.save({"schema": CACHE_SCHEMA, "split": split, "sequences": sequences}, path)
    return {
        "path": path.name,
        "sha256": _sha256(path),
        "sequences": len(sequences),
        "rows": sum(sequence["length"] for sequence in sequences),
        "bytes": path.stat().st_size,
    }


def _cache_path(path):
    path = Path(path)
    if path.is_dir():
        path = path / "cache.json"
    return path.resolve()


def _load_reference(path, collection_sha, catalog_hash):
    path = _cache_path(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != CACHE_SCHEMA:
        raise ValueError("Reference cache has an unexpected schema")
    if document.get("collection_sha256") != collection_sha:
        raise ValueError("Reference cache was built from a different collection")
    if document.get("catalog_digest") != catalog_hash:
        raise ValueError("Reference cache uses a different canonical catalog")
    return path, document


def build_cache(
    collection,
    build,
    output,
    splits=("train", "validation"),
    sequence_length=32,
    shard_sequences=500,
    reference_cache=None,
):
    """Build an immutable tensor cache; only train rows contribute statistics."""
    collection = Path(collection).resolve()
    output = Path(output).absolute()
    if output.exists():
        raise ValueError(f"Cache output already exists: {output}")
    if sequence_length < 1 or shard_sequences < 1:
        raise ValueError("Sequence length and shard size must be positive")
    splits = tuple(dict.fromkeys(splits))
    if not splits or set(splits) - set(VALID_SPLITS):
        raise ValueError("Invalid cache split")
    started = time.monotonic()
    collection_sha = _sha256(collection)
    catalog = build_ability_catalog(collection, build)
    catalog_hash = catalog_digest(catalog)

    reference_path, reference = None, None
    if reference_cache is not None:
        if "train" in splits:
            raise ValueError("A reference cache cannot be combined with the train split")
        reference_path, reference = _load_reference(reference_cache, collection_sha, catalog_hash)
        if int(reference.get("build", -1)) != int(build):
            raise ValueError("Reference cache build mismatch")
        if int(reference.get("sequence_length", -1)) != int(sequence_length):
            raise ValueError("Reference cache sequence length mismatch")
    elif "train" not in splits:
        raise ValueError("Training statistics require the train split")

    output.mkdir(parents=True, exist_ok=False)
    stats = _new_stats()
    unit_types = set()
    ability_kinds = {}
    split_infos = {}
    total_rows = 0
    total_sequences = 0
    total_bytes = 0
    try:
        for split in splits:
            split_files = []
            pending = []
            shard_index = 0
            split_rows = 0
            split_sequences = 0
            collect = split == "train"
            for rows in iter_sequences(
                iter_collection_rows(collection, split, builds={build}), sequence_length
            ):
                sequence = _sequence_to_tensors(
                    rows,
                    catalog,
                    collect=collect,
                    stats=stats,
                    unit_types=unit_types,
                    ability_kinds=ability_kinds,
                )
                pending.append(sequence)
                split_rows += sequence["length"]
                split_sequences += 1
                if len(pending) >= shard_sequences:
                    path = output / split / f"shard-{shard_index:05d}.pt"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    info = _write_shard(path, split, pending)
                    info["path"] = path.relative_to(output).as_posix()
                    split_files.append(info)
                    total_bytes += info["bytes"]
                    pending = []
                    shard_index += 1
            if pending:
                path = output / split / f"shard-{shard_index:05d}.pt"
                path.parent.mkdir(parents=True, exist_ok=True)
                info = _write_shard(path, split, pending)
                info["path"] = path.relative_to(output).as_posix()
                split_files.append(info)
                total_bytes += info["bytes"]
            split_infos[split] = {
                "files": split_files,
                "sequences": split_sequences,
                "rows": split_rows,
            }
            total_rows += split_rows
            total_sequences += split_sequences

        if reference is not None:
            normalization = reference["normalization"]
            unit_type_vocab = list(reference["unit_type_vocab"])
            ability_kinds_list = list(reference["ability_kinds"])
        else:
            normalization = _finalize_normalization(stats)
            unit_type_vocab = sorted(int(value) for value in unit_types)
            ability_kinds_list = [
                sorted(ability_kinds.get(index, set()))
                for index in range(len(catalog["ability_ids"]))
            ]
        cache = {
            "schema": CACHE_SCHEMA,
            "row_schema": ROW_SCHEMA,
            "build": int(build),
            "collection": _relative(collection, ROOT),
            "collection_sha256": collection_sha,
            "catalog": catalog,
            "catalog_digest": catalog_hash,
            "sequence_length": int(sequence_length),
            "splits": split_infos,
            "rows": total_rows,
            "sequences": total_sequences,
            "normalization": normalization,
            "unit_type_vocab": unit_type_vocab,
            "ability_kinds": ability_kinds_list,
            "reference_cache_sha256": _sha256(reference_path) if reference_path else None,
            "build_seconds": time.monotonic() - started,
            "cache_bytes": total_bytes,
        }
        cache_path = output / "cache.json"
        cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        if _sha256(collection) != collection_sha:
            raise ValueError("Collection changed while building the cache")
        return cache, cache_path
    except Exception:
        raise


def load_cache(cache_path, splits=None):
    """Load cache shards after verifying every shard hash; tensors stay on CPU."""
    cache_path = _cache_path(cache_path)
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    if cache.get("schema") != CACHE_SCHEMA or cache.get("row_schema") != ROW_SCHEMA:
        raise ValueError("Unexpected cache schema")
    selected = tuple(cache["splits"]) if splits is None else tuple(splits)
    if set(selected) - set(cache["splits"]):
        raise ValueError("Requested split is not present in the cache")
    result = {}
    for split in selected:
        sequences = []
        for shard in cache["splits"][split]["files"]:
            path = (cache_path.parent / shard["path"]).resolve()
            if _sha256(path) != shard["sha256"]:
                raise ValueError(f"Cache shard hash mismatch: {shard['path']}")
            payload = torch.load(path, weights_only=True, map_location="cpu")
            if payload.get("schema") != CACHE_SCHEMA or payload.get("split") != split:
                raise ValueError(f"Cache shard metadata mismatch: {shard['path']}")
            sequences.extend(payload["sequences"])
        if len(sequences) != cache["splits"][split]["sequences"]:
            raise ValueError("Cache sequence count mismatch")
        result[split] = sequences
    return cache, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--build", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--splits",
        nargs="+",
        choices=VALID_SPLITS,
        default=("train", "validation"),
    )
    parser.add_argument("--sequence-length", type=int, default=32)
    parser.add_argument("--shard-sequences", type=int, default=500)
    parser.add_argument("--reference-cache", type=Path)
    args = parser.parse_args()
    cache, cache_path = build_cache(
        collection=args.collection,
        build=args.build,
        output=args.output,
        splits=args.splits,
        sequence_length=args.sequence_length,
        shard_sequences=args.shard_sequences,
        reference_cache=args.reference_cache,
    )
    print(
        json.dumps(
            {
                "cache": str(cache_path),
                "build": cache["build"],
                "splits": {
                    split: {
                        "sequences": info["sequences"],
                        "rows": info["rows"],
                        "files": len(info["files"]),
                    }
                    for split, info in cache["splits"].items()
                },
                "build_seconds": cache["build_seconds"],
                "cache_bytes": cache["cache_bytes"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
