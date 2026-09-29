"""Deterministic test commands, isolated from any learning policy."""

from __future__ import annotations

import math
import hashlib
import time
from pathlib import Path

import psutil
from s2clientprotocol import common_pb2 as common, error_pb2 as errors
from s2clientprotocol import query_pb2 as query, raw_pb2 as raw, sc2api_pb2 as sc

from scv_star.envs.sc2_client import message_dict, sha256


def validate_map(path: Path, expected_hash: str) -> Path:
    path = path.resolve(strict=True)
    if not expected_hash or sha256(path) != expected_hash:
        raise ValueError("Map SHA-256 mismatch")
    return path


def options():
    return sc.InterfaceOptions(
        raw=True,
        score=False,
        show_cloaked=False,
        show_burrowed_shadows=False,
        show_placeholders=False,
        raw_affects_selection=False,
    )


def create_game(client, map_path: Path, seed: int, realtime=False, two_players=False):
    request = sc.RequestCreateGame(
        local_map=sc.LocalMap(map_path=str(map_path)),
        realtime=realtime,
        disable_fog=False,
        random_seed=seed,
    )
    request.player_setup.add(type=sc.Participant)
    if two_players:
        request.player_setup.add(type=sc.Participant)
    else:
        request.player_setup.add(
            type=sc.Computer, race=common.Terran, difficulty=sc.Medium, ai_build=sc.RandomBuild
        )
    client.call("create_game", request)


def own(observation, unit_type=None):
    return [
        u
        for u in observation.observation.raw_data.units
        if u.alliance == raw.Self and (unit_type is None or u.unit_type == unit_type)
    ]


def command(client, ability, tags, point=None, target_tag=None):
    payload = raw.ActionRawUnitCommand(ability_id=ability, unit_tags=tags, queue_command=False)
    if point is not None:
        payload.target_world_space_pos.CopyFrom(common.Point2D(x=point[0], y=point[1]))
    if target_tag is not None:
        payload.target_unit_tag = target_tag
    action = sc.Action(action_raw=raw.ActionRaw(unit_command=payload))
    return client.call("action", sc.RequestAction(actions=[action]))


class Checks:
    def __init__(self, client, report):
        self.client = client
        self.report = report
        self.report["checks"] = []
        self.report["action_errors"] = []
        self.report["action_responses"] = []
        self.process = psutil.Process(client.process.pid)
        self.cpu_start = sum(self.process.cpu_times()[:2])
        self.ram_peak = 0
        self.memory_available_min = psutil.virtual_memory().available
        self.start = time.monotonic()

    def check(self, name, condition, **evidence):
        self.report["checks"].append(dict(name=name, passed=bool(condition), **evidence))
        if not condition:
            raise AssertionError(f"Acceptance check failed: {name}")

    def observe(self):
        observation = self.client.observe()
        self.report["action_errors"].extend(message_dict(e) for e in observation.action_errors)
        self.ram_peak = max(self.ram_peak, self.process.memory_info().rss)
        self.memory_available_min = min(
            self.memory_available_min, psutil.virtual_memory().available
        )
        return observation

    def advance(self, loops=16):
        self.client.call("step", sc.RequestStep(count=loops))
        return self.observe()

    def wait_until(self, predicate, max_loops):
        observation = self.observe()
        first = observation.observation.game_loop
        while not predicate(observation):
            if observation.player_result or observation.observation.game_loop - first >= max_loops:
                raise TimeoutError("Expected game state did not occur within loop budget")
            observation = self.advance()
        return observation

    def act(self, ability, tags, **kwargs):
        response = command(self.client, ability, tags, **kwargs)
        self.report["action_responses"].append(dict(ability=ability, result=message_dict(response)))
        if list(response.result) != [errors.Success]:
            raise RuntimeError(f"Unexpected command rejection: {message_dict(response)}")

    def finish(self, last_loop):
        duration = time.monotonic() - self.start
        self.report["resources"] = dict(
            sc2_rss_peak_sampled_bytes=self.ram_peak,
            system_available_ram_min_sampled_bytes=self.memory_available_min,
            sc2_cpu_seconds=sum(self.process.cpu_times()[:2]) - self.cpu_start,
            scenario_wall_seconds=duration,
            last_game_loop=last_loop,
            game_loops_per_wall_second=last_loop / duration,
        )


def smoke(client, report):
    checks = Checks(client, report)
    observation = checks.observe()
    data = client.call("data", sc.RequestData(unit_type_id=True, ability_id=True))
    report["game_data_sha256"] = hashlib.sha256(data.SerializeToString()).hexdigest()
    units = {u.name: u for u in data.units}
    abilities = {a.ability_id: a for a in data.abilities}
    worker_type = units["SCV"].unit_id
    depot_type = units["SupplyDepot"].unit_id
    workers = own(observation, worker_type)
    base = own(observation, units["CommandCenter"].unit_id)[0]
    checks.check("initial_terran_workers", len(workers) > 0, count=len(workers))
    enemies = [u for u in observation.observation.raw_data.units if u.alliance == raw.Enemy]
    checks.check("initial_enemy_hidden", not enemies, count=len(enemies))
    # IDs are checked against this engine's live ability table before use.
    checks.check(
        "move_stop_harvest_identity",
        all(
            abilities[i].link_name == name
            for i, name in ((3794, "GeneralMove"), (3665, "Stop"), (295, "SCVHarvest"))
        ),
    )
    report["start_position"] = message_dict(base.pos)
    checks.act(3665, [w.tag for w in workers])
    observation = checks.advance(16)
    worker = next(w for w in own(observation, worker_type) if w.tag == workers[0].tag)
    before = (worker.pos.x, worker.pos.y)
    # Move toward the map centre, remaining near the starting base.
    info = client.call("game_info", sc.RequestGameInfo())
    direction = (
        info.start_raw.map_size.x / 2 - worker.pos.x,
        info.start_raw.map_size.y / 2 - worker.pos.y,
    )
    norm = math.hypot(*direction)
    target = (worker.pos.x + direction[0] / norm * 6, worker.pos.y + direction[1] / norm * 6)
    checks.act(3794, [worker.tag], point=target)
    observation = checks.wait_until(
        lambda o: any(
            w.tag == worker.tag and math.hypot(w.pos.x - before[0], w.pos.y - before[1]) > 2
            for w in own(o)
        ),
        256,
    )
    moved = next(w for w in own(observation) if w.tag == worker.tag)
    checks.check("move_changes_position", True, before=list(before), after=message_dict(moved.pos))
    minerals = [
        u
        for u in observation.observation.raw_data.units
        if u.alliance == raw.Neutral and u.display_type == raw.Visible and u.mineral_contents > 0
    ]
    mineral = min(minerals, key=lambda u: math.hypot(u.pos.x - base.pos.x, u.pos.y - base.pos.y))
    before_minerals = observation.observation.player_common.minerals
    checks.act(295, [worker.tag], target_tag=mineral.tag)
    observation = checks.wait_until(
        lambda o: o.observation.player_common.minerals > before_minerals, 1024
    )
    checks.check(
        "harvest_increases_minerals",
        True,
        before=before_minerals,
        after=observation.observation.player_common.minerals,
    )
    checks.act(295, [w.tag for w in workers], target_tag=mineral.tag)
    before_count = len(own(observation, worker_type))
    observation = checks.wait_until(
        lambda o: o.observation.player_common.minerals >= units["SCV"].mineral_cost, 1024
    )
    checks.act(units["SCV"].ability_id, [base.tag])
    observation = checks.wait_until(lambda o: len(own(o, worker_type)) > before_count, 2048)
    checks.check(
        "train_scv_completes", True, before=before_count, after=len(own(observation, worker_type))
    )
    observation = checks.wait_until(
        lambda o: o.observation.player_common.minerals >= units["SupplyDepot"].mineral_cost, 1024
    )
    worker = own(observation, worker_type)[0]
    candidates = [
        (math.floor(base.pos.x) + dx, math.floor(base.pos.y) + dy)
        for dx in range(-7, 8, 2)
        for dy in range(-7, 8, 2)
        if abs(dx) + abs(dy) >= 6
    ]
    request = query.RequestQuery(ignore_resource_requirements=False)
    for x, y in candidates:
        request.placements.add(
            ability_id=units["SupplyDepot"].ability_id,
            target_pos=common.Point2D(x=x, y=y),
            placing_unit_tag=worker.tag,
        )
    results = client.call("query", request).placements
    position = next((p for p, r in zip(candidates, results) if r.result == errors.Success), None)
    checks.check("legal_depot_position_found", position is not None, position=position)
    old_cap = observation.observation.player_common.food_cap
    checks.act(units["SupplyDepot"].ability_id, [worker.tag], point=position)
    observation = checks.wait_until(
        lambda o: any(u.build_progress == 1 for u in own(o, depot_type)), 2048
    )
    checks.check(
        "depot_completes_and_adds_supply",
        observation.observation.player_common.food_cap > old_cap,
        before_cap=old_cap,
        after_cap=observation.observation.player_common.food_cap,
    )
    checks.check(
        "valid_commands_have_no_async_errors",
        not report["action_errors"],
        errors=list(report["action_errors"]),
    )
    invalid = command(client, units["SCV"].ability_id, [0])
    after_invalid = checks.advance()
    rejected = list(invalid.result) != [errors.Success] or bool(after_invalid.action_errors)
    checks.check(
        "invalid_unit_command_is_reported",
        rejected,
        response=message_dict(invalid),
        async_errors=[message_dict(e) for e in after_invalid.action_errors],
    )
    report["end_reason"] = "intentional_test_surrender"
    checks.finish(after_invalid.observation.game_loop)
