"""Versioned, lossless RAW action labels and conservative legal-observation features."""

import base64
from s2clientprotocol import raw_pb2 as raw

SCHEMA = "tvt-bc-v2"
UNIT_FIELDS = (
    "unit_type",
    "alliance",
    "display_type",
    "pos.x",
    "pos.y",
    "pos.z",
    "radius",
    "health",
    "health_max",
    "shield",
    "shield_max",
    "energy",
    "energy_max",
    "build_progress",
    "is_flying",
    "is_burrowed",
    "is_selected",
    "is_powered",
    "mineral_contents",
    "vespene_contents",
    "assigned_harvesters",
    "ideal_harvesters",
    "weapon_cooldown",
    "cargo_space_taken",
    "cargo_space_max",
    "facing",
    "is_blip",
)
SCALAR_FIELDS = (
    "minerals",
    "vespene",
    "food_cap",
    "food_used",
    "food_army",
    "food_workers",
    "idle_worker_count",
    "army_count",
)


def field(unit, name):
    value = unit
    for part in name.split("."):
        value = getattr(value, part)
    return float(value)


def image_layer(image):
    return {
        "width": image.size.x,
        "height": image.size.y,
        "bits": image.bits_per_pixel,
        "data": base64.b64encode(image.data).decode("ascii"),
    }


def addressable_units(obs):
    units = sorted((u for u in obs.raw_data.units if u.tag != 0), key=lambda u: u.tag)
    if len({u.tag for u in units}) != len(units):
        raise ValueError("duplicate_entity_tag")
    return units


def features(obs):
    """Only player API observation fields; no scores, results or future actions."""
    units = addressable_units(obs)
    tags = [u.tag for u in units]
    indices = {tag: index for index, tag in enumerate(tags)}
    if len(tags) != len(set(tags)):
        raise ValueError("duplicate_entity_tag")
    entities = []
    for unit in units:
        stale = unit.display_type != 1 or unit.is_blip
        row = []
        for name in UNIT_FIELDS:
            # Snapshots expose only static identity/location, never current combat state.
            allowed = name in (
                "unit_type",
                "alliance",
                "display_type",
                "pos.x",
                "pos.y",
                "pos.z",
                "is_blip",
            )
            value = field(unit, name) if not stale or allowed else 0.0
            if unit.is_blip and name == "unit_type":
                value = 0.0
            row.append(value)
        own = unit.alliance == 1
        entities.append(
            {
                "values": row,
                "stale": stale,
                "orders": [
                    {
                        "ability_id": o.ability_id,
                        "progress": o.progress,
                        "target_tag_index": indices.get(o.target_unit_tag, -1)
                        if o.HasField("target_unit_tag")
                        else -1,
                        "target_point": [o.target_world_space_pos.x, o.target_world_space_pos.y]
                        if o.HasField("target_world_space_pos")
                        else None,
                    }
                    for o in unit.orders
                ]
                if own
                else [],
                "addon_index": indices.get(unit.add_on_tag, -1) if own else -1,
                "buff_ids": list(unit.buff_ids) if not stale else [],
                "passengers": [
                    {"unit_type": p.unit_type, "health": p.health, "health_max": p.health_max}
                    for p in unit.passengers
                ]
                if own
                else [],
            }
        )
    return {
        "blips": [
            {
                "position": [u.pos.x, u.pos.y, u.pos.z],
                "alliance": u.alliance,
                "display_type": u.display_type,
            }
            for u in obs.raw_data.units
            if u.tag == 0
        ],
        "loop": obs.game_loop,
        "player_id": obs.player_common.player_id,
        "scalars": [getattr(obs.player_common, n) for n in SCALAR_FIELDS],
        "upgrades": list(obs.raw_data.player.upgrade_ids),
        "entity_tags": tags,
        "entities": entities,
        "visibility": image_layer(obs.raw_data.map_state.visibility),
        "creep": image_layer(obs.raw_data.map_state.creep),
    }


def encode_action(action_raw, obs, known_abilities):
    if action_raw.HasField("unit_command"):
        kind, command = "command", action_raw.unit_command
    elif action_raw.HasField("toggle_autocast"):
        kind, command = "autocast", action_raw.toggle_autocast
    else:
        raise ValueError("non_policy_raw_action")
    if command.ability_id not in known_abilities:
        raise ValueError("unknown_ability")
    units = addressable_units(obs)
    indices = {u.tag: i for i, u in enumerate(units)}
    if not command.unit_tags or any(t not in indices for t in command.unit_tags):
        raise ValueError("selected_tag_missing")
    if any(units[indices[t]].alliance != 1 for t in command.unit_tags):
        raise ValueError("selected_unit_not_own")
    result = {
        "kind": kind,
        "ability_id": command.ability_id,
        "selected_indices": [indices[t] for t in command.unit_tags],
        "queue": None,
        "target_kind": "none",
        "target_index": None,
        "target_point": None,
    }
    if kind == "command":
        result["queue"] = command.queue_command if command.HasField("queue_command") else None
        if command.HasField("target_unit_tag"):
            if command.target_unit_tag not in indices:
                raise ValueError("target_tag_missing")
            result.update(target_kind="unit", target_index=indices[command.target_unit_tag])
        elif command.HasField("target_world_space_pos"):
            result.update(
                target_kind="point",
                target_point=[command.target_world_space_pos.x, command.target_world_space_pos.y],
            )
    return result


def decode_action(label, tags):
    result = raw.ActionRaw()
    command = result.unit_command if label["kind"] == "command" else result.toggle_autocast
    command.ability_id = label["ability_id"]
    command.unit_tags.extend(tags[i] for i in label["selected_indices"])
    if label["kind"] == "command":
        if label["queue"] is not None:
            command.queue_command = label["queue"]
        if label["target_kind"] == "unit":
            command.target_unit_tag = tags[label["target_index"]]
        elif label["target_kind"] == "point":
            command.target_world_space_pos.x, command.target_world_space_pos.y = label[
                "target_point"
            ]
    return result
