"""Replay feasibility probe with pinned engine identity and player fog of war.

Coarse stepping measures replay feasibility only; it does not export BC examples.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import shutil
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from s2clientprotocol import sc2api_pb2 as sc

from scv_star.data.replay_sources import inspect_path
from scv_star.envs.sc2_client import Client, message_dict, sha256, validate_version


def replay_identity(row: dict) -> dict:
    m = row["metadata"]
    identity = {
        "game_version": m["GameVersion"],
        "base_build": int(m["BaseBuild"].removeprefix("Base")),
        "data_build": int(m["DataBuild"]),
        "data_version": m["DataVersion"],
    }
    if (
        identity["base_build"] != row["header_version"]["m_baseBuild"]
        or identity["data_version"] != row["header_data_version"]
        or identity["data_build"] != row["header_data_build"]
    ):
        raise ValueError("Header/metadata identity mismatch")
    return identity


def human_tvt(info: dict) -> bool:
    players = [p["player_info"] for p in info.get("player_info", [])]
    return len(players) == 2 and all(
        p.get("type") == "Participant" and p.get("race_actual") == "Terran" for p in players
    )


def observe_replay(
    client: Client, path: Path, player_id: int, duration: int, step: int, expected_results: dict
) -> dict:
    started = time.monotonic()
    client.call(
        "start_replay",
        sc.RequestStartReplay(
            replay_path=str(path.resolve()),
            observed_player_id=player_id,
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
    count, actions, commands, previous = 0, 0, 0, -1
    action_loops, abilities = [], Counter()
    initial = None
    terminal = False
    for _ in range(duration // step + 20):
        if time.monotonic() - started > 600:
            raise TimeoutError("Replay perspective exceeded 10-minute wall-clock budget")
        response = client.observe()
        obs = response.observation
        if obs.player_common.player_id != player_id:
            raise ValueError("Wrong observed player")
        if obs.game_loop <= previous:
            raise ValueError("Replay game loop did not advance")
        previous = obs.game_loop
        units = obs.raw_data.units
        if initial is None:
            initial = {
                "game_loop": obs.game_loop,
                "own_units": sum(u.alliance == 1 for u in units),
                "enemy_visible": sum(u.alliance == 4 and u.display_type == 1 for u in units),
                "enemy_snapshots": sum(u.alliance == 4 and u.display_type == 2 for u in units),
                "visibility_bytes": len(obs.raw_data.map_state.visibility.data),
            }
            if not initial["own_units"] or not initial["visibility_bytes"]:
                raise ValueError("Missing own units or visibility layer")
            if initial["enemy_visible"]:
                raise ValueError("Enemy visible at standard ladder-map start; inspect fog settings")
        count += 1
        actions += len(response.actions)
        for action in response.actions:
            action_loops.append(action.game_loop)
            if action.HasField("action_raw") and action.action_raw.HasField("unit_command"):
                commands += 1
                abilities[action.action_raw.unit_command.ability_id] += 1
        if response.player_result:
            terminal = True
            break
        client.call("step", sc.RequestStep(count=step))
    if not terminal:
        raise ValueError("No terminal player results")
    results = {p.player_id: sc.Result.Name(p.result) for p in response.player_result}
    if results != expected_results:
        raise ValueError("Terminal winners differ from ReplayInfo")
    client.call("leave_game", sc.RequestLeaveGame())
    return {
        "player_id": player_id,
        "status": "passed",
        "observations": count,
        "last_loop": previous,
        "expected_loops": duration,
        "terminal": terminal,
        "player_results": results,
        "recorded_tail_loops": duration - previous,
        "terminal_loop_matches_header": duration == previous,
        "initial": initial,
        "actions_seen": actions,
        "raw_commands_seen": commands,
        "ability_counts": dict(abilities),
        "seconds": time.monotonic() - started,
        "action_loop_range": [min(action_loops), max(action_loops)] if action_loops else None,
        "step": step,
        "bc_alignment_verified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="JSON list of replay paths")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--engine", type=Path, default=Path("configs/engines/sc2-cn-97579.json"))
    parser.add_argument("--local", type=Path, default=Path("configs/local/m0.json"))
    parser.add_argument("--info-only", action="store_true")
    parser.add_argument(
        "--download-replay-data",
        action="store_true",
        help="Allow SC2 to fetch missing replay binary/data into the selected installation",
    )
    parser.add_argument("--step", type=int, default=112)
    parser.add_argument(
        "--project-limit-gb",
        type=int,
        default=80,
        help="Project storage guard in decimal GB; raise only within the authorized budget",
    )
    args = parser.parse_args()
    if not 1 <= args.project_limit_gb <= 200:
        parser.error("project-limit-gb must be between 1 and 200")
    if not 1 <= args.step <= 224:
        parser.error("step must be between 1 and 224")
    args.output.mkdir(parents=True, exist_ok=False)
    expected = json.loads(args.engine.read_text(encoding="utf-8"))
    local = json.loads(args.local.read_text(encoding="utf-8"))
    paths = [Path(p) for p in json.loads(args.input.read_text(encoding="utf-8"))]
    if not 1 <= len(paths) <= 20:
        parser.error("small-sample probe requires 1..20 files")
    root = Path(__file__).resolve().parents[3]
    started = time.monotonic()
    report = {
        "engine": expected,
        "step": args.step,
        "download_replay_data": args.download_replay_data,
        "project_limit_gb": args.project_limit_gb,
        "replays": [],
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": {
            str(p.relative_to(root)): sha256(p)
            for p in (
                Path(__file__),
                root / "src/scv_star/data/replay_sources.py",
                root / "src/scv_star/envs/sc2_client.py",
            )
        },
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in (
                "s2protocol",
                "mpyq",
                "s2clientprotocol",
                "protobuf",
                "websocket-client",
            )
        },
        "lock_sha256": {
            name: sha256(root / "requirements" / name)
            for name in (
                "m2-replays.txt",
                "m0-windows-py311.txt",
            )
        },
    }
    output = args.output / "report.json"

    def save():
        output.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")

    try:
        usage = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
        free = shutil.disk_usage(root).free
        report["storage_before"] = {"project_file_bytes": usage, "volume_free_bytes": free}
        if usage >= args.project_limit_gb * 1_000_000_000 or free < 20_000_000_000:
            raise RuntimeError(
                f"Storage guard: project >={args.project_limit_gb} GB or volume free <20 GB"
            )
        with Client(
            Path(local["install_path"]),
            expected["base_build"],
            args.output / "client",
            expected,
            binary=Path(local["engine_binary"]) if local.get("engine_binary") else None,
        ) as client:
            report["launch"] = {
                "binary": str(client.binary),
                "data_directory": str(client.install),
                "command": client.command,
                "ping": client.version,
            }
            for path in paths:
                row = {"path": str(path), "sha256": sha256(path), "perspectives": []}
                report["replays"].append(row)
                try:
                    meta = inspect_path(path)
                    validate_version(replay_identity(meta), expected)
                    info = message_dict(
                        client.call(
                            "replay_info",
                            sc.RequestReplayInfo(
                                replay_path=str(path.resolve()),
                                download_data=args.download_replay_data,
                            ),
                        )
                    )
                    row["info"] = info
                    validate_version(info, expected)
                    if not human_tvt(info):
                        raise ValueError("Not two human Terran participants")
                    if not args.info_only:
                        for p in info["player_info"]:
                            row["perspectives"].append(
                                observe_replay(
                                    client,
                                    path,
                                    p["player_info"]["player_id"],
                                    info["game_duration_loops"],
                                    args.step,
                                    {
                                        p["player_result"]["player_id"]: p["player_result"][
                                            "result"
                                        ]
                                        for p in info["player_info"]
                                    },
                                )
                            )
                    row["status"] = "info_passed" if args.info_only else "replay_passed"
                except Exception as error:
                    row.update(status="failed", error=f"{type(error).__name__}: {error}")
                    # Any failed replay start/step may leave the client in an uncertain state.
                    save()
                    break
                finally:
                    row["original_unchanged"] = sha256(path) == row["sha256"]
                    save()
                    print(
                        json.dumps(
                            {
                                "sha256": row["sha256"],
                                "status": row["status"],
                                "error": row.get("error"),
                            }
                        ),
                        flush=True,
                    )
        report["cleanup"] = client.cleanup
        report["status"] = (
            "passed"
            if len(report["replays"]) == len(paths)
            and all(r["status"] != "failed" and r["original_unchanged"] for r in report["replays"])
            else "failed"
        )
    except Exception as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
    finally:
        report["seconds"] = time.monotonic() - started
        save()
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
