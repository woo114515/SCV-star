"""Two-client transport, fog tests and a human-operated realtime check."""

from __future__ import annotations

import json
import ctypes
import math
import socket
import time
from concurrent.futures import ThreadPoolExecutor

import psutil
from s2clientprotocol import common_pb2 as common, error_pb2 as errors
from s2clientprotocol import raw_pb2 as raw, sc2api_pb2 as sc

from scv_star.envs.sc2_client import Client, message_dict
from scv_star.runtime.m0_scenarios import command, create_game, options, own


def join_pair(clients, pool, native_human=False, human_side=1):
    sockets = [socket.socket() for _ in range(4)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        ports = [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()
    joins = build_join_requests(ports, native_human, human_side)
    futures = [pool.submit(c.call, "join_game", req) for c, req in zip(clients, joins)]
    return [message_dict(f.result()) for f in futures]


def build_join_requests(ports, native_human=False, human_side=1):
    joins = []
    for side in (0, 1):
        name = "Human player" if native_human and side == human_side else "SCV-star test controller"
        request = sc.RequestJoinGame(race=common.Terran, player_name=name)
        if not native_human or side != human_side:
            request.options.CopyFrom(options())
        request.server_ports.game_port, request.server_ports.base_port = ports[:2]
        request.client_ports.add(game_port=ports[2], base_port=ports[3])
        joins.append(request)
    return joins


def pair_observe(clients, pool):
    futures = [pool.submit(c.observe) for c in clients]
    return [f.result() for f in futures]


def timed_observe_pair(clients, pool):
    def observe(client):
        observation = client.observe()
        return observation, time.perf_counter()

    futures = [pool.submit(observe, c) for c in clients]
    results = [future.result() for future in futures]
    return [item[0] for item in results], [item[1] for item in results]


def pair_step(clients, pool, count=32):
    futures = [pool.submit(c.call, "step", sc.RequestStep(count=count)) for c in clients]
    for future in futures:
        future.result()
    return pair_observe(clients, pool)


def fog_scenario(clients, pool, report):
    observations = pair_observe(clients, pool)
    records = report.setdefault("checks", [])

    def check(name, passed, **evidence):
        records.append(dict(name=name, passed=bool(passed), **evidence))
        if not passed:
            raise AssertionError(name)

    def act(client, *args, **kwargs):
        response = command(client, *args, **kwargs)
        check(
            "fog_test_command_accepted",
            list(response.result) == [errors.Success],
            response=message_dict(response),
        )

    # Each direction is tested. Extra player observations belong ONLY to this
    # acceptance-test oracle, never to a policy or a training sample.
    for side in (0, 1):
        observer, opponent = clients[side], clients[1 - side]
        before, other = observations[side], observations[1 - side]
        base = own(before, 18)[0]
        other_base = own(other, 18)[0]
        scout = own(before, 45)[0]
        hidden_worker = own(other, 45)[-1]
        check(
            f"side_{side}_enemy_base_initially_not_visible",
            not any(
                u.tag == other_base.tag and u.display_type == raw.Visible
                for u in before.observation.raw_data.units
            ),
        )
        # Candidate start location is public map information, not oracle input.
        starts = observer.call("game_info", sc.RequestGameInfo()).start_raw.start_locations
        destination = max(starts, key=lambda p: math.hypot(p.x - base.pos.x, p.y - base.pos.y))
        act(observer, 3794, [scout.tag], point=(destination.x, destination.y))
        sighted = None
        for _ in range(256):
            observations = pair_step(clients, pool)
            sighted = next(
                (
                    u
                    for u in observations[side].observation.raw_data.units
                    if u.tag == other_base.tag and u.display_type == raw.Visible
                ),
                None,
            )
            if sighted:
                break
        check(
            f"side_{side}_enemy_base_visible_after_scout",
            sighted is not None,
            loop=observations[side].observation.game_loop,
        )
        act(observer, 3794, [scout.tag], point=(base.pos.x, base.pos.y))
        for _ in range(256):
            observations = pair_step(clients, pool)
            current = next((u for u in own(observations[side]) if u.tag == scout.tag), None)
            if current and math.hypot(current.pos.x - base.pos.x, current.pos.y - base.pos.y) < 8:
                break
        check(
            f"side_{side}_scout_returned_alive",
            current is not None
            and math.hypot(current.pos.x - base.pos.x, current.pos.y - base.pos.y) < 8,
        )
        unseen = next(
            (u for u in observations[side].observation.raw_data.units if u.tag == other_base.tag),
            None,
        )
        check(
            f"side_{side}_base_absent_or_snapshot_after_retreat",
            unseen is None or unseen.display_type == raw.Snapshot,
            display_type=raw.DisplayType.Name(unseen.display_type) if unseen else "absent",
        )
        truth_before = next(u for u in own(observations[1 - side]) if u.tag == hidden_worker.tag)
        act(opponent, 3794, [hidden_worker.tag], point=(other_base.pos.x + 5, other_base.pos.y + 5))
        for _ in range(12):
            observations = pair_step(clients, pool)
        truth_after = next(u for u in own(observations[1 - side]) if u.tag == hidden_worker.tag)
        check(
            f"side_{side}_hidden_worker_actually_moved",
            math.hypot(
                truth_after.pos.x - truth_before.pos.x, truth_after.pos.y - truth_before.pos.y
            )
            > 1,
        )
        observed_worker = next(
            (
                u
                for u in observations[side].observation.raw_data.units
                if u.tag == hidden_worker.tag
            ),
            None,
        )
        check(
            f"side_{side}_hidden_worker_live_state_not_exposed",
            observed_worker is None or observed_worker.display_type == raw.Snapshot,
            display_type=raw.DisplayType.Name(observed_worker.display_type)
            if observed_worker
            else "absent",
        )
    report["final_game_loop"] = observations[0].observation.game_loop
    report["test_oracle_isolated"] = True


def percentiles(values):
    if not values:
        return {}
    ordered = sorted(values)
    return {
        f"p{p}": ordered[min(len(ordered) - 1, math.ceil(len(ordered) * p / 100) - 1)]
        for p in (50, 95, 99)
    }


def choose_worker(observation, previous_tag):
    workers = own(observation, 45)
    return next((unit for unit in workers if unit.tag == previous_tag), next(iter(workers), None))


def realtime_scenario(clients, pool, report, output, seconds):
    observations = pair_observe(clients, pool)
    base = own(observations[0], 18)[0]
    worker = own(observations[0], 45)[0]
    report["human_initial"] = message_dict(observations[1].observation.player_common)
    # Only touch windows belonging to these two explicitly owned processes.
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def configure_window(hwnd, _):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if user32.IsWindowVisible(hwnd):
            if pid.value == clients[0].process.pid:
                user32.ShowWindow(hwnd, 0)
            elif pid.value == clients[1].process.pid:
                user32.SetWindowTextW(hwnd, "SCV-star - Human M0 acceptance")
                user32.ShowWindow(hwnd, 9)
                user32.SetForegroundWindow(hwnd)
        return True

    user32.EnumWindows(callback_type(configure_window), 0)
    (output / "ready.json").write_text(
        json.dumps(
            {
                "status": "ready",
                "duration_seconds": seconds,
                "human_pid": clients[1].process.pid,
                "human_player_id": observations[1].observation.player_common.player_id,
            }
        ),
        encoding="utf-8",
    )
    print("HUMAN_WINDOW_READY", flush=True)
    start = time.perf_counter()
    previous_loop = observations[0].observation.game_loop
    initial_loop = previous_loop
    last_advance = start
    deadline_misses = 0
    command_count = 0
    worker_replacements = 0
    command_cycles_without_worker = 0
    action_errors = []
    action_age_loops = []
    pending_source_loop = None
    decision_latencies = []
    observation_roundtrips = []
    human_action_count = 0
    ram_peak = 0
    samples = 0
    process_handles = [psutil.Process(c.process.pid) for c in clients]
    cpu_start = [sum(p.cpu_times()[:2]) for p in process_handles]
    next_command = start
    while time.perf_counter() - start < seconds:
        if (output / "stop-request").exists():
            raise RuntimeError("Diagnostic stopped by operator request")
        tick = time.perf_counter()
        observations, receive_times = timed_observe_pair(clients, pool)
        received = receive_times[0]
        observation_roundtrips.append(received - tick)
        current_loop = observations[0].observation.game_loop
        if current_loop > previous_loop:
            previous_loop, last_advance = current_loop, received
        if received - last_advance > 10:
            raise TimeoutError("Realtime game stopped advancing for ten seconds")
        if any(o.player_result for o in observations):
            report["early_game_result"] = [
                [message_dict(r) for r in o.player_result] for o in observations
            ]
            raise RuntimeError("Game ended before realtime duration was reached")
        for side, obs in enumerate(observations):
            action_errors.extend(dict(side=side, **message_dict(e)) for e in obs.action_errors)
        if pending_source_loop is not None:
            for action in observations[0].actions:
                if action.HasField("action_raw") and action.action_raw.HasField("unit_command"):
                    raw_command = action.action_raw.unit_command
                    if raw_command.ability_id in (16, 3794) and worker.tag in raw_command.unit_tags:
                        if action.HasField("game_loop"):
                            action_age_loops.append(action.game_loop - pending_source_loop)
                        pending_source_loop = None
                        break
        human_action_count += len(observations[1].actions)
        if received >= next_command:
            current_worker = choose_worker(observations[0], worker.tag)
            if current_worker is None:
                command_cycles_without_worker += 1
            else:
                worker_replacements += int(current_worker.tag != worker.tag)
                worker = current_worker
                offset = 6 if command_count % 2 else -6
                response = command(
                    clients[0], 3794, [worker.tag], point=(base.pos.x + offset, base.pos.y)
                )
                if list(response.result) != [errors.Success]:
                    raise RuntimeError(f"Realtime test move rejected: {message_dict(response)}")
                decision_latencies.append(time.perf_counter() - received)
                pending_source_loop = current_loop
                command_count += 1
            next_command = time.perf_counter() + 2
        elapsed = time.perf_counter() - tick
        deadline_misses += int(elapsed > 0.25)
        ram_peak = max(ram_peak, sum(p.memory_info().rss for p in process_handles))
        samples += 1
        if samples % 40 == 0:
            (output / "progress.json").write_text(
                json.dumps(
                    {
                        "elapsed_seconds": time.perf_counter() - start,
                        "game_loop": current_loop,
                        "human_actions_observed": human_action_count,
                        "commands": command_count,
                        "deadline_misses": deadline_misses,
                        "human_workers": observations[1].observation.player_common.food_workers,
                        "human_food_cap": observations[1].observation.player_common.food_cap,
                    }
                ),
                encoding="utf-8",
            )
        time.sleep(max(0, 0.25 - elapsed))
    report["realtime"] = dict(
        wall_seconds=time.perf_counter() - start,
        samples=samples,
        game_loops_advanced=previous_loop - initial_loop,
        commands=command_count,
        worker_replacements=worker_replacements,
        command_cycles_without_worker=command_cycles_without_worker,
        human_actions_observed=human_action_count,
        action_errors=action_errors,
        decision_to_action_ack_seconds=percentiles(decision_latencies),
        observation_roundtrip_seconds=percentiles(observation_roundtrips),
        observation_to_action_effect_loops=percentiles(action_age_loops),
        action_effect_samples=len(action_age_loops),
        timer="perf_counter",
        measurement_start="actor_observation_received",
        cycle_budget_seconds=0.25,
        deadline_misses=deadline_misses,
        deadline_miss_rate=deadline_misses / samples,
        two_clients_rss_peak_sampled_bytes=ram_peak,
        sc2_cpu_seconds=[
            sum(p.cpu_times()[:2]) - start_cpu for p, start_cpu in zip(process_handles, cpu_start)
        ],
        human_confirmation_required=True,
    )
    report["human_final"] = message_dict(observations[1].observation.player_common)


def multiplayer(host, args, expected, map_path, report):
    interactive = args.mode in ("human", "human-check")
    report["human_display_mode"] = args.human_display_mode
    report["diagnostic_only"] = args.mode == "human-check"
    guest = Client(
        args.install,
        args.build,
        args.output / "guest",
        expected=expected,
        visible=interactive,
        display_mode=args.human_display_mode,
    )
    try:
        with guest, ThreadPoolExecutor(max_workers=2) as pool:
            clients = [host, guest]
            hosting_client = guest if interactive else host
            joining_clients = [guest, host] if interactive else clients
            create_game(hosting_client, map_path, args.seed, realtime=interactive, two_players=True)
            report["joins"] = join_pair(
                joining_clients, pool, native_human=interactive, human_side=0
            )
            report["human_is_game_host"] = interactive
            report["human_native_interface"] = interactive
            report["guest_engine"] = guest.version
            if args.mode == "fog":
                fog_scenario(clients, pool, report)
            else:
                realtime_scenario(clients, pool, report, args.output, args.seconds)
            replay = host.call("save_replay", sc.RequestSaveReplay())
            (args.output / "acceptance.SC2Replay").write_bytes(replay.data)
            host.call("leave_game", sc.RequestLeaveGame())
            guest.call("leave_game", sc.RequestLeaveGame())
    finally:
        report["guest_cleanup"] = guest.cleanup
