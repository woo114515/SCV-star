"""Explicit discovery/pinned SC2 interface checks; never starts training."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from s2clientprotocol import sc2api_pb2 as sc, common_pb2 as common

from scv_star.envs.sc2_client import Client, message_dict, sha256
from scv_star.runtime.m0_scenarios import create_game, options, smoke, validate_map
from scv_star.runtime.m0_multiplayer import multiplayer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=["probe", "inspect", "smoke", "fog", "human", "human-check"]
    )
    parser.add_argument("--install", type=Path, required=True)
    parser.add_argument("--build", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--engine", type=Path)
    parser.add_argument("--map-name", default="Alcyone LE")
    parser.add_argument("--map", type=Path)
    parser.add_argument("--map-sha256")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--human-display-mode", type=int, choices=[0, 1, 2], default=0)
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[3]
    report = {
        "mode": args.mode,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "passed": False,
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("s2clientprotocol", "protobuf", "websocket-client", "psutil")
        },
    }
    start = time.monotonic()
    client = None
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        report["source_sha256"] = {
            str(p.relative_to(project_root)): sha256(p)
            for p in (project_root / "src").rglob("*.py")
        }
        report["dependency_lock_sha256"] = sha256(
            project_root / "requirements/m0-windows-py311.txt"
        )
        report["git_head"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project_root, text=True
        ).strip()
        report["git_dirty"] = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=project_root, text=True
            ).strip()
        )
        usage = sum(p.stat().st_size for p in project_root.rglob("*") if p.is_file())
        free = shutil.disk_usage(project_root).free
        report["storage_before"] = dict(
            project_file_bytes=usage,
            volume_free_bytes=free,
            external_game_cache_delta="not_measured",
        )
        if usage >= 80_000_000_000 or free < 20_000_000_000:
            raise RuntimeError(
                "Storage guard: project >=80 GB or volume free <20 GB; review before continuing"
            )
        expected = json.loads(args.engine.read_text(encoding="utf-8-sig")) if args.engine else None
        if args.mode != "probe" and not expected:
            raise ValueError("A pinned --engine manifest is required")
        if args.mode == "human" and args.seconds < 600:
            raise ValueError("Human acceptance requires at least 600 seconds")
        if args.seconds <= 0:
            raise ValueError("Duration must be positive")
        map_path = (
            validate_map(args.map, args.map_sha256)
            if args.mode in ("smoke", "fog", "human", "human-check")
            else None
        )
        client = Client(args.install, args.build, args.output / "client", expected=expected)
        with client:
            report["engine"] = dict(client.version, binary_sha256=client.binary_hash)
            if args.mode == "probe":
                report["maps"] = message_dict(
                    client.call("available_maps", sc.RequestAvailableMaps())
                )
            elif args.mode == "smoke":
                report["map"] = dict(path=str(map_path), sha256=args.map_sha256)
                report["seed"] = args.seed
                create_game(client, map_path, args.seed)
                report["join"] = message_dict(
                    client.call(
                        "join_game", sc.RequestJoinGame(race=common.Terran, options=options())
                    )
                )
                smoke(client, report)
                replay = client.call("save_replay", sc.RequestSaveReplay())
                (args.output / "acceptance.SC2Replay").write_bytes(replay.data)
                client.call("leave_game", sc.RequestLeaveGame())
            elif args.mode in ("fog", "human", "human-check"):
                report["map"] = dict(path=str(map_path), sha256=args.map_sha256)
                report["seed"] = args.seed
                multiplayer(client, args, expected, map_path, report)
            else:
                request = sc.RequestCreateGame(
                    battlenet_map_name=args.map_name,
                    realtime=False,
                    disable_fog=False,
                    random_seed=0,
                )
                request.player_setup.add(type=sc.Participant)
                request.player_setup.add(
                    type=sc.Computer,
                    race=common.Terran,
                    difficulty=sc.Medium,
                    ai_build=sc.RandomBuild,
                )
                client.call("create_game", request)
                report["join"] = message_dict(
                    client.call(
                        "join_game",
                        sc.RequestJoinGame(
                            race=common.Terran,
                            options=sc.InterfaceOptions(
                                raw=True,
                                score=False,
                                show_cloaked=False,
                                show_burrowed_shadows=False,
                                show_placeholders=False,
                            ),
                        ),
                    )
                )
                report["game_info"] = message_dict(client.call("game_info", sc.RequestGameInfo()))
                data = client.call("data", sc.RequestData(ability_id=True, unit_type_id=True))
                (args.output / "game-data.json").write_text(
                    json.dumps(message_dict(data), indent=2), encoding="utf-8"
                )
                report["initial_observation"] = message_dict(client.observe())
                client.call("step", sc.RequestStep(count=16))
                report["next_loop"] = client.observe().observation.game_loop
                client.call("leave_game", sc.RequestLeaveGame())
            report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
        report["traceback"] = traceback.format_exc()
    finally:
        report["wall_seconds"] = time.monotonic() - start
        if client:
            report["cleanup"] = client.cleanup
            report["request_latencies"] = client.latencies
        (args.output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("mode", "passed", "engine", "error", "cleanup", "wall_seconds")
                if key in report
            },
            indent=2,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
