"""Bounded replay timing experiment, not a BC dataset exporter."""

import argparse
import base64
from collections import Counter
import gzip
import json
import os
from pathlib import Path
import shutil
import time

from s2clientprotocol import sc2api_pb2 as sc

from scv_star.data.alignment import pair_command, policy_observation
from scv_star.data.replay_sources import inspect_path
from scv_star.envs.sc2_client import Client, message_dict, sha256, validate_version
from scv_star.runtime.replay_probe import human_tvt, replay_identity


def directory_bytes(path):
    def unreadable(error):
        raise error

    total = 0
    for directory, _, files in os.walk(path, onerror=unreadable):
        for name in files:
            total += (Path(directory) / name).stat().st_size
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--local", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--loops", type=int, default=2240)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    if not 16 <= args.loops <= 4480:
        parser.error("loops must be 16..4480")
    output = args.output.resolve()
    if not output.is_relative_to(root / "artifacts/runs"):
        parser.error("output must be under artifacts/runs")
    if output.exists():
        parser.error("output must not exist")
    initial_bytes = directory_bytes(root)
    if (
        initial_bytes + 64_000_000 >= 100_000_000_000
        or shutil.disk_usage(root).free < 20_000_000_000
    ):
        raise RuntimeError("Insufficient space under 100 GB budget for bounded probe")
    expected = json.loads(args.engine.read_text(encoding="utf-8"))
    local = json.loads(args.local.read_text(encoding="utf-8"))
    validate_version(replay_identity(inspect_path(args.replay)), expected)
    digest = sha256(args.replay)
    output.mkdir(parents=True)
    started = time.monotonic()
    report = {
        "schema": "alignment-diagnostic-v1",
        "status": "running",
        "engine": expected,
        "replay_sha256": digest,
        "loop_limit": args.loops,
        "training_ready": False,
        "project_bytes_before": initial_bytes,
        "output_limit_bytes": 32_000_000,
        "passes": [],
        "source_sha256": {
            str(p.relative_to(root)): sha256(p)
            for p in (
                Path(__file__),
                root / "src/scv_star/data/alignment.py",
                root / "src/scv_star/envs/sc2_client.py",
            )
        },
    }
    client = None

    def guard():
        if time.monotonic() - started > 600:
            raise TimeoutError("Probe exceeded ten-minute total budget")
        if directory_bytes(output) > 32_000_000:
            raise RuntimeError("Probe output exceeded 32 MB; stopping")

    try:
        client = Client(
            Path(local["install_path"]),
            expected["base_build"],
            output / "client",
            expected,
            binary=Path(local["engine_binary"]) if local.get("engine_binary") else None,
        )
        with client:
            report["ping"] = client.version
            info = message_dict(
                client.call(
                    "replay_info",
                    sc.RequestReplayInfo(
                        replay_path=str(args.replay.resolve()), download_data=False
                    ),
                )
            )
            validate_version(info, expected)
            if not human_tvt(info) or info["game_duration_loops"] <= args.loops + 8:
                raise ValueError("Need a human TvT replay longer than diagnostic window")
            report["map_name"] = info["map_name"]
            signatures = {}
            for player in (1, 2):
                for step in (1, 8):
                    guard()
                    client.call(
                        "start_replay",
                        sc.RequestStartReplay(
                            replay_path=str(args.replay.resolve()),
                            observed_player_id=player,
                            options=sc.InterfaceOptions(
                                raw=True,
                                score=False,
                                show_cloaked=False,
                                show_burrowed_shadows=False,
                                show_placeholders=False,
                                raw_affects_selection=False,
                            ),
                            disable_fog=False,
                            realtime=False,
                            record_replay=False,
                        ),
                    )
                    frames, commands, counts, lags = {}, [], Counter(), Counter()
                    entry = {"player": player, "step": step, "status": "running"}
                    report["passes"].append(entry)
                    pass_start = time.monotonic()
                    previous = -1
                    observation_count = 0
                    with gzip.open(
                        output / f"player-{player}-step-{step}.jsonl.gz", "wt", encoding="utf-8"
                    ) as stream:
                        while True:
                            response = client.observe()
                            obs = response.observation
                            loop = obs.game_loop
                            if loop <= previous or obs.player_common.player_id != player:
                                raise ValueError("Non-increasing loop or wrong perspective")
                            if previous < 0:
                                if not obs.raw_data.map_state.visibility.data:
                                    raise ValueError("Missing visibility data")
                                if any(
                                    u.alliance == 4 and u.display_type == 1
                                    for u in obs.raw_data.units
                                ):
                                    raise ValueError("Visible enemy at standard map start")
                                entry["initial_loop"] = loop
                            frames[loop] = policy_observation(obs)
                            observation_count += 1
                            for action in response.actions:
                                counts["all_api_actions"] += 1
                                if not action.HasField(
                                    "action_raw"
                                ) or not action.action_raw.HasField("unit_command"):
                                    counts["non_unit_commands"] += 1
                                    continue
                                if not action.HasField("game_loop"):
                                    raise ValueError("Missing command timestamp")
                                # Ignore drain-window actions beyond the requested execution horizon.
                                if action.game_loop > args.loops:
                                    counts["after_horizon"] += 1
                                    continue
                                lags[loop - action.game_loop] += 1
                                command = {
                                    "loop": action.game_loop,
                                    "raw": message_dict(action.action_raw),
                                }
                                commands.append(command)
                                record = {
                                    "received_loop": loop,
                                    "action": command,
                                    "candidates": [],
                                }
                                if step == 1:
                                    for lag in (1, 2):
                                        before = frames.get(action.game_loop - lag)
                                        if before is None:
                                            counts[f"missing_observation_lag_{lag}"] += 1
                                            continue
                                        pair = pair_command(action, before)
                                        counts[f"paired_lag_{lag}"] += 1
                                        if not pair["selected_tags_resolved"]:
                                            counts[f"unresolved_selected_lag_{lag}"] += 1
                                        pair["observation_protobuf_base64"] = base64.b64encode(
                                            before.SerializeToString()
                                        ).decode()
                                        record["candidates"].append(pair)
                                stream.write(json.dumps(record) + "\n")
                            previous = loop
                            frames = {k: v for k, v in frames.items() if k >= loop - 32}
                            if response.player_result:
                                raise ValueError("Unexpected terminal result in diagnostic window")
                            if loop >= args.loops + 8:
                                break
                            if observation_count % 128 == 0:
                                stream.flush()
                                guard()
                            client.call(
                                "step", sc.RequestStep(count=min(step, args.loops + 8 - loop))
                            )
                    client.call("leave_game", sc.RequestLeaveGame())
                    signatures[player, step] = commands
                    entry.update(
                        status="completed",
                        counts=dict(counts),
                        command_count=len(commands),
                        receipt_lag_histogram=dict(lags),
                        observations=observation_count,
                        last_loop=loop,
                        seconds=time.monotonic() - pass_start,
                        same_loop_command_groups=sum(
                            n > 1 for n in Counter(c["loop"] for c in commands).values()
                        ),
                    )
                    print(json.dumps({k: v for k, v in entry.items() if k != "counts"}), flush=True)
            report["step_comparison"] = {
                str(p): signatures[p, 1] == signatures[p, 8] for p in (1, 2)
            }
            report["status"] = "diagnostic_completed"
    except Exception as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
    finally:
        report["original_unchanged"] = sha256(args.replay) == digest
        report["seconds"] = time.monotonic() - started
        report["cleanup"] = client.cleanup if client else None
        report["output_bytes_before_report"] = directory_bytes(output)
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if report["status"] != "diagnostic_completed" or not report["original_unchanged"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
