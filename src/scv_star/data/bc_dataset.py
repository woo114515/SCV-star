"""Streaming reader and padded CPU/GPU batches for the versioned BC manifest."""

import json
from pathlib import Path

from scv_star.data.bc_schema import SCHEMA, UNIT_FIELDS
from scv_star.envs.sc2_client import sha256
from scv_star.data.bc_storage import open_shard

STATIC_MAP_FIELDS = (
    "map_size",
    "pathing_grid",
    "terrain_height",
    "placement_grid",
    "playable_area",
)


def iter_rows(manifest_path, split):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["schema"] != SCHEMA or not manifest["training_ready"]:
        raise ValueError("Dataset has not passed acceptance")
    for shard in manifest["shards"]:
        if shard["split"] != split:
            continue
        if sha256(manifest_path.parent / shard["path"]) != shard["sha256"]:
            raise ValueError("Frozen shard hash mismatch")
        if sha256(manifest_path.parent / shard["static_map"]) != shard["static_map_sha256"]:
            raise ValueError("Frozen map hash mismatch")
        map_data = json.loads(
            (manifest_path.parent / shard["static_map"]).read_text(encoding="utf-8")
        )
        # GameInfo start_locations can depend on the observed player. Only static
        # terrain is shared between views; the policy gets its own start from units.
        static_map = {key: map_data[key] for key in STATIC_MAP_FIELDS}
        with open_shard(manifest_path.parent / shard["path"]) as stream:
            for line in stream:
                row = json.loads(line)
                if row["schema"] != SCHEMA or row["split"] != split:
                    raise ValueError("Shard schema/split mismatch")
                row["_static_map"] = static_map
                yield row


def iter_sequences(rows, length=64):
    """Never join games, player views or gaps caused by quarantined commands."""
    if length < 1:
        raise ValueError("Sequence length must be positive")
    sequence = []
    previous = None
    for row in rows:
        key = (row["replay_sha256"], row["player_id"], row["source_action_index"])
        if previous and (key[:2] != previous[:2] or key[2] != previous[2] + 1):
            if sequence:
                yield sequence
                sequence = []
        sequence.append(row)
        previous = key
        if len(sequence) == length:
            yield sequence
            sequence = []
    if sequence:
        yield sequence


def collate(rows, ability_ids):
    """Tensorize core policy features; no entity truncation or fitted statistics.

    Orders, buffs, cargo, upgrades and spatial layers remain in `structured` for
    the eventual architecture-specific encoders. They are not silently discarded.
    """
    import torch

    if not rows:
        raise ValueError("Empty batch")
    vocabulary = {int(ability): index for index, ability in enumerate(ability_ids)}
    if len(vocabulary) != len(ability_ids):
        raise ValueError("Duplicate abilities")
    batch, entities = len(rows), max(len(r["observation"]["entities"]) for r in rows)
    unit = torch.zeros(batch, entities, len(UNIT_FIELDS), dtype=torch.float32)
    unit_mask = torch.zeros(batch, entities, dtype=torch.bool)
    selected = torch.zeros(batch, entities, dtype=torch.bool)
    max_selected = max(len(r["action"]["selected_indices"]) for r in rows)
    selected_indices = torch.full((batch, max_selected), -1, dtype=torch.long)
    selected_mask = torch.zeros(batch, max_selected, dtype=torch.bool)
    target_index = torch.zeros(batch, dtype=torch.long)
    target_point = torch.zeros(batch, 2, dtype=torch.float32)
    scalar = []
    ability, kind, queue, queue_mask, target_kind, delay, delay_mask = [], [], [], [], [], [], []
    for i, row in enumerate(rows):
        obs, action = row["observation"], row["action"]
        count = len(obs["entities"])
        unit[i, :count] = torch.tensor([u["values"] for u in obs["entities"]])
        unit_mask[i, :count] = True
        for index in action["selected_indices"]:
            if not 0 <= index < count:
                raise ValueError("Selected index out of range")
            selected[i, index] = True
        selected_count = len(action["selected_indices"])
        selected_indices[i, :selected_count] = torch.tensor(action["selected_indices"])
        selected_mask[i, :selected_count] = True
        scalar.append(obs["scalars"] + [obs["loop"]])
        ability.append(vocabulary[action["ability_id"]])
        kind.append(int(action["kind"] == "autocast"))
        queue.append(int(bool(action["queue"])))
        queue_mask.append(action["kind"] == "command" and action["queue"] is not None)
        target_kind.append({"none": 0, "point": 1, "unit": 2}[action["target_kind"]])
        if action["target_kind"] == "unit":
            if not 0 <= action["target_index"] < count:
                raise ValueError("Target index out of range")
            target_index[i] = action["target_index"]
        elif action["target_kind"] == "point":
            target_point[i] = torch.tensor(action["target_point"])
        delay.append(row["next_action_delay"] or 0)
        delay_mask.append(row["delay_mask"])
    targets = {
        "ability": torch.tensor(ability),
        "kind": torch.tensor(kind),
        "queued": torch.tensor(queue),
        "queue_mask": torch.tensor(queue_mask, dtype=torch.bool),
        "target_kind": torch.tensor(target_kind),
        "target_index": target_index,
        "target_point": target_point,
        "selected": selected,
        "selected_indices": selected_indices,
        "selected_mask": selected_mask,
        "delay": torch.tensor(delay),
        "delay_mask": torch.tensor(delay_mask),
    }
    return {
        "entities": unit,
        "entity_mask": unit_mask,
        "scalars": torch.tensor(scalar, dtype=torch.float32),
        "targets": targets,
        "structured": [r["observation"] for r in rows],
        "static_maps": [r.get("_static_map") for r in rows],
    }
