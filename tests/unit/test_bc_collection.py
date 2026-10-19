import gzip
import json
from pathlib import Path
import tempfile
import unittest

from scv_star.data.bc_collection import iter_collection_rows, publish_collection
from scv_star.data.bc_dataset import STATIC_MAP_FIELDS
from scv_star.data.bc_schema import SCHEMA
from scv_star.envs.sc2_client import sha256


class CollectionTests(unittest.TestCase):
    def child(self, root, name, split, build=86383, fingerprint=None, replay=None, row_build=None):
        directory = root / name
        view_root = directory / "replay"
        view_root.mkdir(parents=True)
        engine = {
            "game_version": f"5.0.8.{build}",
            "base_build": build,
            "data_build": build,
            "data_version": f"version-{build}",
        }
        replay = replay or sha256(self.write(directory / "raw-fixture", name.encode()))
        map_path = self.write(
            view_root / "map-data.json",
            json.dumps({key: {} for key in STATIC_MAP_FIELDS}).encode(),
        )
        game_data = self.write(view_root / "game-data.json", b'{"abilities":[]}')
        views, shards = [], []
        for player in (1, 2):
            path = view_root / f"player-{player}.jsonl.gz"
            row = {
                "schema": SCHEMA,
                "replay_sha256": replay,
                "player_id": player,
                "split": split,
                "engine_base_build": build if row_build is None else row_build,
                "data_version": engine["data_version"],
                "action": {"ability_id": build},
            }
            with gzip.open(path, "wt") as stream:
                stream.write((json.dumps(row) + "\n") * 128)
            views.append(
                {
                    "player_id": player,
                    "accepted": 128,
                    "coverage": 1,
                    "same_pass_labels": True,
                    "accepted_roundtrip_verified": True,
                    "shard": path.name,
                    "shard_sha256": sha256(path),
                }
            )
            shards.append(
                {
                    "path": path.relative_to(directory).as_posix(),
                    "sha256": sha256(path),
                    "split": split,
                    "replay_sha256": replay,
                    "player_id": player,
                    "rows": 128,
                    "static_map": map_path.relative_to(directory).as_posix(),
                    "static_map_sha256": sha256(map_path),
                }
            )
        report = self.write(
            view_root / "report.json",
            json.dumps(
                {
                    "replay_sha256": replay,
                    "split": split,
                    "engine": engine,
                    "status": "extracted",
                    "training_ready": True,
                    "original_unchanged": True,
                    "cleanup": {"exited": True},
                    "source_sha256": {"extractor": "historical-source"},
                    "views": views,
                }
            ).encode(),
        )
        manifest = {
            "schema": SCHEMA,
            "training_ready": True,
            "engine": engine,
            "engine_base_build": build,
            "extraction_source_sha256": {"extractor": "historical-source"},
            "files": [
                {
                    "sha256": replay,
                    "split": split,
                    "init_data_sha256": fingerprint or f"init-{name}",
                    "report": report.relative_to(directory).as_posix(),
                    "report_sha256": sha256(report),
                    "game_data_sha256": sha256(game_data),
                }
            ],
            "shards": shards,
            "file_splits": {split: 1},
            "rows": 256,
            "rows_by_split": {split: 256},
            "ability_ids": [build],
        }
        return self.write(directory / "manifest.json", json.dumps(manifest).encode())

    def write(self, path, content):
        path.write_bytes(content)
        return path

    def rewrite(self, path, transform):
        document = json.loads(path.read_text())
        transform(document)
        path.write_text(json.dumps(document))

    def test_collection_preserves_frozen_children_and_build_specific_action_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            children = [
                self.child(root, "first", "train"),
                self.child(root, "second", "validation", build=93333),
                self.child(root, "third", "test", build=93333),
            ]
            hashes = [sha256(child) for child in children]
            output = root / "collection.json"
            result = publish_collection(output, children, minimum_files=3)
            self.assertEqual(result["files"], 3)
            self.assertEqual(result["player_views"], 6)
            self.assertEqual(result["rows"], 768)
            self.assertEqual(result["file_splits"], {"train": 1, "validation": 1, "test": 1})
            self.assertFalse(result["action_ids_merged_across_versions"])
            self.assertEqual(result["ability_catalogs"][0]["ability_ids"], [86383])
            self.assertEqual(hashes, [sha256(child) for child in children])
            rows = list(iter_collection_rows(output, "train", builds=[86383]))
            self.assertEqual(len(rows), 256)
            self.assertEqual(rows[0]["action"]["ability_id"], 86383)
            self.assertEqual(list(iter_collection_rows(output, "train", builds=[93333])), [])
            with self.assertRaisesRegex(ValueError, "exists"):
                publish_collection(output, children, minimum_files=3)
            self.rewrite(children[0], lambda child: child.update(rows=0))
            with self.assertRaisesRegex(ValueError, "child manifest hash"):
                list(iter_collection_rows(output, "train"))

    def test_duplicate_sha_and_cross_split_initialization_groups_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self.child(root, "first", "train", fingerprint="same-init")
            second = self.child(root, "second", "test", fingerprint="same-init")
            with self.assertRaisesRegex(ValueError, "fingerprint crosses"):
                publish_collection(
                    root / "bad.json", [first, second], minimum_files=2, required_splits=("train",)
                )
            self.assertFalse((root / "bad.json").exists())
            replay = json.loads(first.read_text())["files"][0]["sha256"]
            duplicate = self.child(root, "duplicate", "train", replay=replay)
            with self.assertRaisesRegex(ValueError, "Duplicate replay SHA"):
                publish_collection(
                    root / "bad.json",
                    [first, duplicate],
                    minimum_files=2,
                    required_splits=("train",),
                )

    def test_missing_views_member_tampering_and_count_gates_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child = self.child(root, "first", "train")
            output = root / "bad.json"
            with self.assertRaisesRegex(ValueError, "file count or required splits"):
                publish_collection(output, [child], minimum_files=1)
            with self.assertRaisesRegex(ValueError, "file count or required splits"):
                publish_collection(output, [child], minimum_files=2, required_splits=("train",))
            original = child.read_bytes()
            self.rewrite(child, lambda data: data["shards"].pop())
            with self.assertRaisesRegex(ValueError, "Missing player view"):
                publish_collection(output, [child], minimum_files=1, required_splits=("train",))
            child.write_bytes(original)
            (child.parent / "replay/player-1.jsonl.gz").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                publish_collection(output, [child], minimum_files=1, required_splits=("train",))

    def test_reader_rejects_wrong_row_version_even_with_matching_compressed_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child = self.child(root, "first", "train", row_build=97563)
            output = root / "collection.json"
            publish_collection(output, [child], minimum_files=1, required_splits=("train",))
            with self.assertRaisesRegex(ValueError, "Row version"):
                list(iter_collection_rows(output, "train"))


if __name__ == "__main__":
    unittest.main()
