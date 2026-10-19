import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scv_star.data.bc_accept import accept_dataset, resource_protocol_identity, verify_resource
from scv_star.envs.sc2_client import sha256


class ResourcePinTests(unittest.TestCase):
    def test_exact_resource_hash_is_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            content = b"Synthetic map fixture, not a game resource"
            digest = hashlib.sha256(content).hexdigest()
            handle = b"s2maUS  " + bytes.fromhex(digest)
            path = root / digest[:2] / digest[2:4] / (digest + ".s2ma")
            path.parent.mkdir(parents=True)
            path.write_bytes(content)
            self.assertEqual(verify_resource(handle, root, {})["sha256"], digest)
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "corrupt"):
                verify_resource(handle, root, {})

    def test_invalid_handle_cannot_be_treated_as_a_valid_map(self):
        with self.assertRaisesRegex(ValueError, "length"):
            verify_resource(b"bad", Path("."), {})
        with self.assertRaisesRegex(ValueError, "type"):
            verify_resource(b"xxxxUS  " + bytes(32), Path("."), {})


class AcceptanceVersionTests(unittest.TestCase):
    def fixture(self, root, build=93333):
        engine = {
            "game_version": f"5.0.14.{build}",
            "base_build": build,
            "data_build": build,
            "data_version": "fixture-data-version",
            "binary_sha256": "fixture-binary-hash",
        }
        replay = root / "original.SC2Replay"
        replay.write_bytes(b"Synthetic replay; the parser is mocked, no game is used")
        digest = sha256(replay)
        directory = root / "bucket" / digest
        directory.mkdir(parents=True)
        report = {
            "engine": engine,
            "replay_sha256": digest,
            "split": "train",
            "status": "extracted",
            "training_ready": True,
            "original_unchanged": True,
            "cleanup": {"exited": True},
            "source_sha256": {"extractor": "historical-extractor"},
            "map_name": "Synthetic",
            "views": [
                {
                    "player_id": player,
                    "accepted": 128,
                    "coverage": 1,
                    "same_pass_labels": True,
                    "accepted_roundtrip_verified": True,
                    "shard": f"player-{player}.jsonl.gz",
                    "shard_sha256": "mocked-shard-hash",
                    "quarantine": {},
                    "api_action_types": {},
                }
                for player in (1, 2)
            ],
        }
        (directory / "report.json").write_text(json.dumps(report))
        grid = {"bits_per_pixel": 8, "size": {"x": 1, "y": 1}, "data": "AA=="}
        (directory / "map-data.json").write_text(
            json.dumps(
                {
                    "map_size": {"x": 1, "y": 1},
                    "pathing_grid": grid,
                    "placement_grid": grid,
                    "terrain_height": grid,
                }
            )
        )
        (directory / "game-data.json").write_text(
            json.dumps({"abilities": [{"ability_id": 16, "friendly_name": "Move"}]})
        )
        selection = root / "selection.json"
        selection.write_text(
            json.dumps(
                [
                    {
                        "sha256": digest,
                        "identity": engine,
                        "bc_split": "train",
                        "screening_status": "eligible",
                        "human_1v1": True,
                        "races": ["Terr", "Terr"],
                        "outcome_label_ready": True,
                        "init_data_sha256": "fixture-init",
                        "local_replay": str(replay),
                        "collection_source": "Synthetic",
                        "source_records": [],
                        "source_policies": [],
                    }
                ]
            )
        )
        boundary = root / "boundary.json"
        boundary.write_text(json.dumps({"passed": True, "engine": engine}))
        summary = {
            "rows": 128,
            "ability_counts": {16: 128},
            "kind_counts": {"command": 128},
            "same_loop_groups": 0,
            "max_entities": 1,
            "max_entities_source_index": 0,
            "blip_rows": 0,
            "queued_actions": 0,
            "addon_rows": 0,
            "cargo_rows": 0,
            "max_order_queue": 0,
        }
        return engine, directory, selection, boundary, summary

    def test_non_86383_bucket_and_optional_single_split_are_supported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine, directory, selection, boundary, summary = self.fixture(root)
            with (
                patch("scv_star.data.bc_accept.inspect_shard", return_value=summary),
                patch("scv_star.data.bc_accept.replay_resources", return_value=[]) as resources,
                patch("scv_star.data.bc_accept.inspect_path", return_value={}),
                patch("scv_star.data.bc_accept.replay_identity", return_value=engine),
            ):
                manifest = accept_dataset(
                    directory.parent,
                    selection,
                    boundary,
                    minimum_files=1,
                    required_splits=("train",),
                )
            self.assertEqual(manifest["engine_base_build"], 93333)
            self.assertEqual(manifest["file_splits"], {"train": 1})
            self.assertEqual(manifest["rows"], 256)
            self.assertTrue(manifest["resource_protocol"]["exact_build"])
            self.assertEqual(resources.call_args.args[1], 93333)
            frozen = sha256(directory.parent / "manifest.json")
            with self.assertRaisesRegex(ValueError, "exists"):
                accept_dataset(directory.parent, selection, boundary)
            self.assertEqual(frozen, sha256(directory.parent / "manifest.json"))

    def test_boundary_identity_and_default_three_splits_are_enforced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine, directory, selection, boundary, summary = self.fixture(root)
            with (
                patch("scv_star.data.bc_accept.inspect_shard", return_value=summary),
                patch("scv_star.data.bc_accept.replay_resources", return_value=[]),
                patch("scv_star.data.bc_accept.inspect_path", return_value={}),
                patch("scv_star.data.bc_accept.replay_identity", return_value=engine),
            ):
                with self.assertRaisesRegex(AssertionError, "required split"):
                    accept_dataset(directory.parent, selection, boundary, minimum_files=1)
            self.assertFalse((directory.parent / "manifest.json").exists())
            changed = {**engine, "data_version": "wrong-data-version"}
            boundary.write_text(json.dumps({"passed": True, "engine": changed}))
            with self.assertRaisesRegex(ValueError, "data_version"):
                accept_dataset(directory.parent, selection, boundary, minimum_files=1)

    def test_missing_protocol_never_silently_uses_latest(self):
        with self.assertRaises(ImportError):
            resource_protocol_identity(96516)
        declared = resource_protocol_identity(96516, 97563)
        self.assertFalse(declared["exact_build"])
        self.assertEqual(declared["protocol_build"], 97563)
        self.assertEqual(declared["replay_base_build"], 96516)
        self.assertEqual(len(declared["module_sha256"]), 64)
        self.assertIn("static map", declared["scope"])

    def test_declared_resource_parser_keeps_the_exact_replay_engine_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            engine, directory, selection, boundary, summary = self.fixture(Path(temp), build=96516)
            with (
                patch("scv_star.data.bc_accept.inspect_shard", return_value=summary),
                patch("scv_star.data.bc_accept.replay_resources", return_value=[]) as resources,
                patch("scv_star.data.bc_accept.inspect_path", return_value={}),
                patch("scv_star.data.bc_accept.replay_identity", return_value=engine),
            ):
                manifest = accept_dataset(
                    directory.parent,
                    selection,
                    boundary,
                    minimum_files=1,
                    resource_protocol_build=97563,
                    required_splits=("train",),
                )
            self.assertEqual(manifest["engine_base_build"], 96516)
            self.assertEqual(manifest["engine"], engine)
            self.assertEqual(manifest["resource_protocol"]["protocol_build"], 97563)
            self.assertFalse(manifest["resource_protocol"]["exact_build"])
            self.assertEqual(resources.call_args.args[1], 97563)


if __name__ == "__main__":
    unittest.main()
