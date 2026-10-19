import json
from pathlib import Path
import tempfile
import unittest

from scv_star.data.bc_dataset import STATIC_MAP_FIELDS
from scv_star.data.bc_storage import open_shard
from scv_star.envs.sc2_client import sha256
from scv_star.runtime.bc_reader_check import largest_observation


class LargestObservationTests(unittest.TestCase):
    def fixture(self, directory, digest="right"):
        root = Path(directory)
        shard = root / "rows.jsonl.xz"
        rows = [
            {
                "replay_sha256": key,
                "player_id": 1,
                "source_action_index": 3,
                "observation": {"entities": [1] * count},
            }
            for key, count in (("wrong", 2), (digest, 3))
        ]
        with open_shard(shard, "wt") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")
        map_path = root / "map.json"
        map_path.write_text(
            json.dumps(dict.fromkeys(STATIC_MAP_FIELDS, []) | {"start_locations": [1]}),
            encoding="utf-8",
        )
        return root / "manifest.json", {
            "path": shard.name,
            "sha256": sha256(shard),
            "static_map": map_path.name,
            "static_map_sha256": sha256(map_path),
            "replay_sha256": "right",
            "player_id": 1,
            "max_entities_source_index": 3,
            "max_entities": 3,
        }

    def test_same_action_index_in_another_game_is_not_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            row = largest_observation(*self.fixture(directory))
            self.assertEqual(row["replay_sha256"], "right")
            self.assertNotIn("start_locations", row["_static_map"])

    def test_missing_largest_observation_cannot_silently_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "missing"):
                largest_observation(*self.fixture(directory, digest="absent"))


if __name__ == "__main__":
    unittest.main()
