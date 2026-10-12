"""Schedule-assisted extraction: labels and observations always come from the same pass."""

import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import shutil
import time

from s2clientprotocol import sc2api_pb2 as sc

from scv_star.data.bc_schema import SCHEMA, features, encode_action, decode_action
from scv_star.data.replay_sources import inspect_path
from scv_star.envs.sc2_client import Client, message_dict, sha256, validate_version
from scv_star.runtime.replay_probe import human_tvt, replay_identity
from scv_star.runtime.alignment_probe import directory_bytes


def start(client, replay, player):
    client.call(
        "start_replay",
        sc.RequestStartReplay(
            replay_path=str(replay.resolve()),
            observed_player_id=player,
            options=sc.InterfaceOptions(
                raw=True,
                score=False,
                raw_affects_selection=False,
                show_cloaked=False,
                show_burrowed_shadows=False,
                show_placeholders=False,
            ),
            disable_fog=False,
            realtime=False,
            record_replay=False,
        ),
    )
    response = client.observe()
    obs = response.observation
    if obs.game_loop != 0 or obs.player_common.player_id != player:
        raise ValueError("Unexpected initial loop/player")
    if not obs.raw_data.map_state.visibility.data or not any(
        u.alliance == 1 for u in obs.raw_data.units
    ):
        raise ValueError("Missing legal observation")
    if any(u.alliance == 4 and u.display_type == 1 for u in obs.raw_data.units):
        raise ValueError("Unexpected enemy visibility at start")
    return response


def policy_actions(response):
    return [
        a
        for a in response.actions
        if a.HasField("action_raw")
        and (a.action_raw.HasField("unit_command") or a.action_raw.HasField("toggle_autocast"))
    ]


def extract(replay, output, engine_path, local_path, split, deadline_seconds=1800):
    root = Path(__file__).resolve().parents[3]
    output = output.resolve()
    if not output.is_relative_to(root / "data/processed") or output.exists():
        raise ValueError("Need a new output directory under data/processed")
    if directory_bytes(root) > 99_000_000_000 or shutil.disk_usage(root).free < 20_000_000_000:
        raise RuntimeError("Storage guard")
    engine = json.loads(engine_path.read_text(encoding="utf-8"))
    local = json.loads(local_path.read_text(encoding="utf-8"))
    validate_version(replay_identity(inspect_path(replay)), engine)
    digest = sha256(replay)
    output.mkdir(parents=True)
    started = time.monotonic()
    client = None
    report = {
        "schema": SCHEMA,
        "alignment_method": "same-pass-obs-and-label; first-pass-timestamps-only",
        "replay_sha256": digest,
        "split": split,
        "engine": engine,
        "views": [],
        "status": "running",
        "training_ready": False,
        "observation_offset": -2,
        "source_sha256": {
            str(p.relative_to(root)): sha256(p)
            for p in [
                Path(__file__),
                root / "src/scv_star/data/bc_schema.py",
                root / "src/scv_star/data/bc_extract.py",
                root / "src/scv_star/envs/sc2_client.py",
            ]
        },
    }

    def guard():
        if time.monotonic() - started > deadline_seconds:
            raise TimeoutError("Replay deadline")
        if directory_bytes(output) > 100_000_000:
            raise RuntimeError("Per-replay output limit")

    def save():
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    try:
        client = Client(
            Path(local["install_path"]),
            engine["base_build"],
            output / "client",
            engine,
            binary=Path(local["engine_binary"]) if local.get("engine_binary") else None,
        )
        with client:
            info = message_dict(
                client.call(
                    "replay_info",
                    sc.RequestReplayInfo(replay_path=str(replay.resolve()), download_data=False),
                )
            )
            validate_version(info, engine)
            if not human_tvt(info):
                raise ValueError("Not human TvT")
            results = {
                p["player_result"]["player_id"]: p["player_result"]["result"]
                for p in info["player_info"]
            }
            report["map_name"] = info["map_name"]
            report["duration_loops"] = info["game_duration_loops"]
            for player in (1, 2):
                response = start(client, replay, player)
                game_info = client.call("game_info", sc.RequestGameInfo())
                (output / "map-data.json").write_text(
                    json.dumps(message_dict(game_info.start_raw)), encoding="utf-8"
                )
                data = client.call(
                    "data", sc.RequestData(ability_id=True, unit_type_id=True, upgrade_id=True)
                )
                abilities = {a.ability_id for a in data.abilities}
                (output / "game-data.json").write_text(
                    json.dumps(message_dict(data)), encoding="utf-8"
                )
                first = []
                previous = -1
                steps = 0
                while True:
                    loop = response.observation.game_loop
                    if loop <= previous:
                        raise ValueError("Nonmonotonic scan")
                    previous = loop
                    first.extend(policy_actions(response))
                    if response.player_result:
                        break
                    client.call("step", sc.RequestStep(count=32))
                    response = client.observe()
                    steps += 1
                    if steps % 128 == 0:
                        guard()
                if {
                    p.player_id: sc.Result.Name(p.result) for p in response.player_result
                } != results:
                    raise ValueError("Scan result mismatch")
                if not first or any(not a.HasField("game_loop") for a in first):
                    raise ValueError("No timestamped commands")
                client.call("leave_game", sc.RequestLeaveGame())
                response = start(client, replay, player)
                frames = {0: response.observation}
                current = 0
                pending = None
                index = 0
                accepted = 0
                rejects = Counter()
                counts = Counter()
                kinds = Counter()
                second = []
                loops = Counter()
                last_action = -1
                shard = output / f"player-{player}.jsonl.gz"
                with (
                    gzip.open(shard, "wt", encoding="utf-8", compresslevel=3) as writer,
                    (output / f"player-{player}-quarantine.jsonl").open(
                        "w", encoding="utf-8"
                    ) as quarantine,
                ):

                    def consume(resp):
                        nonlocal pending, index, accepted, last_action
                        if resp.observation.player_common.player_id != player:
                            raise ValueError("Wrong player")
                        for action in resp.actions:
                            counts["all_api_actions"] += 1
                            if action.HasField("action_raw"):
                                counts["raw_" + str(action.action_raw.WhichOneof("action"))] += 1
                        counts["action_errors"] += len(resp.action_errors)
                        for action in policy_actions(resp):
                            if not action.HasField("game_loop") or action.game_loop < last_action:
                                raise ValueError("Invalid action chronology")
                            last_action = action.game_loop
                            second.append(action)
                            loops[action.game_loop] += 1
                            before = frames.get(max(0, action.game_loop - 2))
                            row = None
                            try:
                                if before is None or before.game_loop >= action.game_loop:
                                    raise ValueError("pre_observation_missing")
                                observation = features(before)
                                label = encode_action(action.action_raw, before, abilities)
                                if (
                                    decode_action(label, observation["entity_tags"])
                                    != action.action_raw
                                ):
                                    raise ValueError("roundtrip_mismatch")
                                row = {
                                    "schema": SCHEMA,
                                    "replay_sha256": digest,
                                    "player_id": player,
                                    "split": split,
                                    "engine_base_build": engine["base_build"],
                                    "data_version": engine["data_version"],
                                    "source_action_index": index,
                                    "action_loop": action.game_loop,
                                    "observation": observation,
                                    "action": label,
                                    "next_action_delay": None,
                                    "delay_mask": False,
                                }
                            except ValueError as error:
                                reason = str(error)
                                rejects[reason] += 1
                                quarantine.write(
                                    json.dumps(
                                        {
                                            "index": index,
                                            "loop": action.game_loop,
                                            "reason": reason,
                                            "raw": message_dict(action.action_raw),
                                        }
                                    )
                                    + "\n"
                                )
                                if reason == "duplicate_entity_tag" and rejects[reason] == 1:
                                    tag_counts = Counter(u.tag for u in before.raw_data.units)
                                    (output / f"player-{player}-duplicate-tags.json").write_text(
                                        json.dumps(
                                            [
                                                message_dict(u)
                                                for u in before.raw_data.units
                                                if tag_counts[u.tag] > 1
                                            ],
                                            indent=2,
                                        )
                                    )
                            if pending is not None:
                                if row is not None:
                                    pending["next_action_delay"] = (
                                        action.game_loop - pending["action_loop"]
                                    )
                                    pending["delay_mask"] = True
                                writer.write(json.dumps(pending, separators=(",", ":")) + "\n")
                            pending = row
                            if row is not None:
                                accepted += 1
                                kinds[label["kind"]] += 1
                            index += 1

                    consume(response)
                    schedule = sorted({max(0, a.game_loop - 2) for a in first})
                    for target in schedule:
                        if target <= current:
                            continue
                        client.call("step", sc.RequestStep(count=target - current))
                        response = client.observe()
                        current = response.observation.game_loop
                        if current != target and not response.player_result:
                            raise ValueError("Step/observation mismatch")
                        consume(response)
                        frames[current] = response.observation
                        if len(frames) > 64:
                            frames = {k: frames[k] for k in sorted(frames)[-64:]}
                        if response.player_result:
                            break
                        if index % 128 == 0:
                            writer.flush()
                            guard()
                    while not response.player_result:
                        client.call("step", sc.RequestStep(count=32))
                        response = client.observe()
                        consume(response)
                        guard()
                    if pending is not None:
                        writer.write(json.dumps(pending, separators=(",", ":")) + "\n")
                if {
                    p.player_id: sc.Result.Name(p.result) for p in response.player_result
                } != results:
                    raise ValueError("Extraction result mismatch")
                client.call("leave_game", sc.RequestLeaveGame())
                if accepted + sum(rejects.values()) != len(second):
                    raise ValueError("Accounting mismatch")
                view = {
                    "player_id": player,
                    "policy_actions": len(second),
                    "first_pass_policy_actions": len(first),
                    "accepted": accepted,
                    "coverage": accepted / len(second) if second else 0,
                    "quarantine": dict(rejects),
                    "api_action_types": dict(counts),
                    "kind_counts": dict(kinds),
                    "same_loop_groups": sum(n > 1 for n in loops.values()),
                    "same_pass_labels": True,
                    "action_stream_equal": first == second,
                    "accepted_roundtrip_verified": True,
                    "terminal_loop": response.observation.game_loop,
                    "recorded_tail_loops": info["game_duration_loops"]
                    - response.observation.game_loop,
                    "shard": shard.name,
                    "shard_sha256": sha256(shard),
                }
                report["views"].append(view)
                save()
                print(
                    json.dumps(
                        {
                            "replay": digest[:12],
                            "player": player,
                            "accepted": accepted,
                            "rejected": dict(rejects),
                        }
                    ),
                    flush=True,
                )
            report["status"] = "extracted"
            report["training_ready"] = all(
                v["coverage"] >= 0.98 and v["accepted"] >= 128 for v in report["views"]
            )
    except Exception as error:
        report.update(
            status="failed", error=f"{type(error).__name__}: {error}", training_ready=False
        )
    finally:
        report["original_unchanged"] = sha256(replay) == digest
        report["cleanup"] = client.cleanup if client else None
        report["seconds"] = time.monotonic() - started
        if not report["original_unchanged"]:
            report["training_ready"] = False
        save()
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ["replay", "output", "engine", "local"]:
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--split", choices=["train", "validation", "test"], required=True)
    a = p.parse_args()
    r = extract(a.replay, a.output, a.engine, a.local, a.split)
    if r["status"] != "extracted":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
