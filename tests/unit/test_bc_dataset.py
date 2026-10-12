import unittest
import gzip
import json
from pathlib import Path
import tempfile
from scv_star.data.bc_dataset import STATIC_MAP_FIELDS, collate, iter_rows, iter_sequences
from scv_star.data.bc_schema import SCHEMA, UNIT_FIELDS
from scv_star.envs.sc2_client import sha256


class SequenceTests(unittest.TestCase):
    def row(self, game, player, index):
        return {"replay_sha256": game, "player_id": player, "source_action_index": index}

    def test_never_crosses_game_player_or_quarantine_gap(self):
        rows = [
            self.row("a", 1, 0),
            self.row("a", 1, 1),
            self.row("a", 1, 3),
            self.row("a", 2, 0),
            self.row("b", 1, 0),
        ]
        self.assertEqual([len(s) for s in iter_sequences(rows)], [2, 1, 1, 1])

    def test_fixed_length_chunks_preserve_all_rows(self):
        rows = [self.row("a", 1, i) for i in range(5)]
        self.assertEqual([len(s) for s in iter_sequences(rows, 2)], [2, 2, 1])
        with self.assertRaises(ValueError):
            list(iter_sequences(rows, 0))


class ReaderTests(unittest.TestCase):
    def test_player_dependent_map_locations_do_not_enter_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            terrain = {key: {} for key in STATIC_MAP_FIELDS}
            (root / "map.json").write_text(json.dumps({**terrain, "start_locations": [123]}))
            with gzip.open(root / "rows.gz", "wt") as stream:
                stream.write(json.dumps({"schema": SCHEMA, "split": "train"}) + "\n")
            manifest = {
                "schema": SCHEMA,
                "training_ready": True,
                "shards": [
                    {
                        "split": "train",
                        "static_map": "map.json",
                        "path": "rows.gz",
                        "sha256": sha256(root / "rows.gz"),
                        "static_map_sha256": sha256(root / "map.json"),
                    }
                ],
            }
            (root / "manifest.json").write_text(json.dumps(manifest))
            rows = list(iter_rows(root / "manifest.json", "train"))
            self.assertEqual(rows[0]["_static_map"], terrain)
            self.assertEqual(list(iter_rows(root / "manifest.json", "test")), [])
            manifest["training_ready"] = False
            (root / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "acceptance"):
                list(iter_rows(root / "manifest.json", "train"))
            manifest["training_ready"] = True
            (root / "manifest.json").write_text(json.dumps(manifest))
            (root / "map.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "map hash"):
                list(iter_rows(root / "manifest.json", "train"))

    def test_collation_preserves_selection_order_and_conditional_masks(self):
        try:
            import torch
        except ImportError:
            self.skipTest("GPU/BC dependency group not installed")
        obs = {
            "entities": [{"values": [0.0] * len(UNIT_FIELDS)} for _ in range(3)],
            "scalars": [0] * 8,
            "loop": 10,
        }
        action = {
            "ability_id": 16,
            "kind": "command",
            "queue": True,
            "target_kind": "point",
            "target_point": [5, 6],
            "selected_indices": [2, 0],
        }
        first = {"observation": obs, "action": action, "next_action_delay": 0, "delay_mask": True}
        second = {
            **first,
            "action": {
                **action,
                "kind": "autocast",
                "queue": None,
                "target_kind": "none",
                "selected_indices": [1],
            },
            "next_action_delay": None,
            "delay_mask": False,
        }
        batch = collate([first, second], [16])
        self.assertEqual(batch["targets"]["selected_indices"].tolist(), [[2, 0], [1, -1]])
        self.assertEqual(batch["targets"]["selected_mask"].tolist(), [[True, True], [True, False]])
        self.assertEqual(batch["targets"]["queue_mask"].tolist(), [True, False])
        self.assertEqual(batch["targets"]["delay_mask"].tolist(), [True, False])
        self.assertEqual(batch["targets"]["delay"].tolist(), [0, 0])
        self.assertEqual(batch["entity_mask"].dtype, torch.bool)


if __name__ == "__main__":
    unittest.main()
