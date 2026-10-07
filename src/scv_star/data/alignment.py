"""Diagnostic pairing only: timestamps do not prove pre-execution engine semantics."""

from s2clientprotocol import sc2api_pb2 as sc


def pair_command(action, observation):
    """Reject future/same-loop observations and report unresolved command tags."""
    if not action.HasField("game_loop"):
        raise ValueError("Action has no execution timestamp")
    if observation.game_loop >= action.game_loop:
        raise ValueError("Observation must strictly precede action timestamp")
    if not action.HasField("action_raw") or not action.action_raw.HasField("unit_command"):
        raise ValueError("Expected a RAW unit command")
    command = action.action_raw.unit_command
    units = {u.tag: u for u in observation.raw_data.units}
    missing = [tag for tag in command.unit_tags if tag not in units]
    nonself = [tag for tag in command.unit_tags if tag in units and units[tag].alliance != 1]
    target = command.target_unit_tag if command.HasField("target_unit_tag") else None
    # This is only a tag consistency check, not a game-rule legality mask.
    return {
        "observation_loop": observation.game_loop,
        "action_loop": action.game_loop,
        "lag": action.game_loop - observation.game_loop,
        "missing_selected_tags": missing,
        "nonself_selected_tags": nonself,
        "target_tag_present": target in units if target is not None else None,
        "selected_tags_resolved": bool(command.unit_tags) and not missing and not nonself,
        "training_ready": False,
    }


def policy_observation(observation):
    """Explicit diagnostic whitelist; excludes action results, chat and final outcome."""
    result = sc.Observation(game_loop=observation.game_loop)
    result.player_common.CopyFrom(observation.player_common)
    result.raw_data.CopyFrom(observation.raw_data)
    return result
