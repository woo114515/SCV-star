"""Controlled SCV command timing check, never part of the human BC dataset."""

import argparse
import json
from pathlib import Path

from s2clientprotocol import common_pb2 as common, raw_pb2 as raw, sc2api_pb2 as sc

from scv_star.envs.sc2_client import Client, message_dict, sha256
from scv_star.runtime.m0_scenarios import create_game, options, validate_map


def verify_boundary(engine_path, local_path, output):
    root = Path(__file__).resolve().parents[3]
    output = Path(output).resolve()
    if not output.is_relative_to(root / "artifacts/runs") or output.exists():
        raise ValueError("Use a new boundary output under artifacts/runs")
    output.mkdir(parents=True)
    engine = json.loads(Path(engine_path).read_text())
    local = json.loads(Path(local_path).read_text())
    map_config = json.loads((root / "configs/local/m0.json").read_text())
    map_path = validate_map(root / map_config["map_path"], map_config["map_sha256"])
    report = {
        "passed": False,
        "purpose": "controlled timing; excluded from human dataset",
        "engine": engine,
    }
    client = None
    try:
        client = Client(
            Path(local["install_path"]),
            engine["base_build"],
            output / "client",
            engine,
            binary=Path(local["engine_binary"]) if local.get("engine_binary") else None,
        )
        with client:
            create_game(client, map_path, 20261019)
            client.call("join_game", sc.RequestJoinGame(race=common.Terran, options=options()))
            client.call("step", sc.RequestStep(count=64))
            observation = client.observe().observation
            cc = next(
                u for u in observation.raw_data.units if u.alliance == 1 and u.unit_type == 18
            )
            command = raw.ActionRawUnitCommand(
                ability_id=524, unit_tags=[cc.tag], queue_command=False
            )
            result = client.call(
                "action",
                sc.RequestAction(
                    actions=[sc.Action(action_raw=raw.ActionRaw(unit_command=command))]
                ),
            )
            if list(result.result) != [1]:
                raise ValueError(message_dict(result))
            client.call("step", sc.RequestStep(count=64))
            report["save_observation"] = message_dict(client.observe().observation.player_common)
            replay = output / "controlled.SC2Replay"
            replay.write_bytes(client.call("save_replay", sc.RequestSaveReplay()).data)
            client.call("leave_game", sc.RequestLeaveGame())
            client.call(
                "start_replay",
                sc.RequestStartReplay(
                    replay_path=str(replay),
                    observed_player_id=1,
                    options=options(),
                    disable_fog=False,
                    realtime=False,
                ),
            )
            frames, commands = {}, []
            report["observed_commands"] = []
            while True:
                response = client.observe()
                obs = response.observation
                center = next(
                    u for u in obs.raw_data.units if u.alliance == 1 and u.unit_type == 18
                )
                frames[obs.game_loop] = {
                    "loop": obs.game_loop,
                    "minerals": obs.player_common.minerals,
                    "orders": [o.ability_id for o in center.orders],
                }
                for action in response.actions:
                    if action.HasField("action_raw"):
                        report["observed_commands"].append(message_dict(action))
                    if (
                        action.HasField("action_raw")
                        and action.action_raw.HasField("unit_command")
                        and action.action_raw.unit_command.ability_id == 524
                    ):
                        commands.append(action.game_loop)
                if obs.game_loop >= 100 or response.player_result:
                    break
                client.call("step", sc.RequestStep(count=1))
            if len(commands) != 1:
                raise ValueError("Expected exactly one controlled SCV command")
            loop = commands[0]
            if 524 in frames[loop - 2]["orders"] or 524 not in frames[loop + 1]["orders"]:
                raise ValueError("Command timing contract failed")
            report.update(
                passed=True,
                action_loop=loop,
                frames=[frames[i] for i in range(loop - 3, loop + 4)],
                replay_sha256=sha256(replay),
                map_sha256=sha256(map_path),
            )
            client.call("leave_game", sc.RequestLeaveGame())
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        report["cleanup"] = client.cleanup if client else None
        (output / "report.json").write_text(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("engine", "local", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    report = verify_boundary(args.engine, args.local, args.output)
    print(json.dumps(report))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
