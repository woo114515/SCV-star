"""Regression tests for version drift, map drift and transport failures."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from s2clientprotocol import sc2api_pb2 as sc

from scv_star.envs.sc2_client import Client, sha256, validate_version
from scv_star.runtime.m0_scenarios import options, validate_map
from scv_star.runtime.m0_multiplayer import build_join_requests, choose_worker


class VersionGuards(unittest.TestCase):
    def setUp(self):
        self.version = dict(
            game_version="5.0.test", base_build=123, data_build=124, data_version="A"
        )

    def test_every_identity_field_must_match(self):
        for field in self.version:
            with self.subTest(field=field):
                modified = dict(self.version, **{field: "different"})
                with self.assertRaisesRegex(ValueError, "Version mismatch"):
                    validate_version(self.version, modified)

    def test_missing_and_null_fields_do_not_fall_back(self):
        for field in self.version:
            for value in (None, ""):
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, "Missing pinned"):
                        validate_version(self.version, dict(self.version, **{field: value}))

    def test_exact_match(self):
        validate_version(self.version, self.version)


class MapGuards(unittest.TestCase):
    def test_changed_map_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.SC2Map"
            path.write_bytes(b"initial synthetic fixture")
            digest = sha256(path)
            self.assertEqual(validate_map(path, digest), path.resolve())
            path.write_bytes(b"changed synthetic fixture")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                validate_map(path, digest)

    def test_missing_map_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                validate_map(Path(directory) / "missing.SC2Map", "unused")

    def test_fog_sensitive_interface_options_disabled(self):
        interface = options()
        self.assertTrue(interface.raw)
        self.assertFalse(interface.show_cloaked)
        self.assertFalse(interface.show_burrowed_shadows)
        self.assertFalse(interface.show_placeholders)
        self.assertFalse(interface.HasField("feature_layer"))
        self.assertFalse(interface.score)


class TransportGuards(unittest.TestCase):
    def client_for(self, response):
        client = Client.__new__(Client)
        client.request_id = 0
        client.latencies = []
        client.ws = Mock()
        client.ws.recv.return_value = response.SerializeToString()
        return client

    def test_mismatched_response_id_rejected(self):
        client = self.client_for(sc.Response(id=99, ping=sc.ResponsePing()))
        with self.assertRaisesRegex(RuntimeError, "response id"):
            client.call("ping", sc.RequestPing())

    def test_top_level_protocol_error_rejected(self):
        client = self.client_for(sc.Response(id=1, error=["bad state"]))
        with self.assertRaisesRegex(RuntimeError, "bad state"):
            client.call("ping", sc.RequestPing())

    def test_missing_payload_rejected(self):
        client = self.client_for(sc.Response(id=1, status=sc.launched))
        with self.assertRaisesRegex(RuntimeError, "Missing ping"):
            client.call("ping", sc.RequestPing())

    def test_create_game_error_not_silently_accepted(self):
        client = self.client_for(sc.Response(id=1, create_game=sc.ResponseCreateGame(error=1)))
        with self.assertRaisesRegex(RuntimeError, "create_game"):
            client.call("create_game", sc.RequestCreateGame())


class HumanInterfaceGuards(unittest.TestCase):
    def test_destroyed_worker_is_replaced_only_by_owned_scv(self):
        observation = sc.ResponseObservation()
        units = observation.observation.raw_data.units
        units.add(tag=10, unit_type=45, alliance=4)
        units.add(tag=11, unit_type=48, alliance=1)
        units.add(tag=12, unit_type=45, alliance=1)
        self.assertEqual(choose_worker(observation, 9).tag, 12)
        self.assertEqual(choose_worker(observation, 12).tag, 12)

    def test_missing_worker_does_not_create_invalid_command_target(self):
        self.assertIsNone(choose_worker(sc.ResponseObservation(), 9))

    def test_native_human_host_does_not_inherit_actor_raw_options(self):
        human, actor = build_join_requests(
            [5100, 5101, 5102, 5103], native_human=True, human_side=0
        )
        self.assertEqual(human.player_name, "Human player")
        self.assertFalse(human.HasField("options"))
        self.assertTrue(actor.options.raw)
        self.assertFalse(actor.options.show_cloaked)
        self.assertEqual(human.server_ports, actor.server_ports)
        self.assertEqual(human.client_ports[0], actor.client_ports[0])

    def test_fog_oracle_uses_raw_for_both_players(self):
        requests = build_join_requests([5100, 5101, 5102, 5103])
        self.assertTrue(all(request.options.raw for request in requests))
        self.assertTrue(all(not request.options.show_cloaked for request in requests))


if __name__ == "__main__":
    unittest.main()
