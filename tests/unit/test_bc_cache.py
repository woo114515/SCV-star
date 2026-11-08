import gzip
import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from scv_star.data.bc_cache import CACHE_SCHEMA, build_cache, load_cache


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def make_row(
    replay,
    split,
    index,
    *,
    ability=10,
    kind="command",
    selected=(0,),
    target_kind="none",
    target_index=0,
    target_point=(0.0, 0.0),
    queue=None,
    delay=None,
    delay_mask=False,
    unit_type=18,
    stale=False,
):
    entities = []
    for slot, (entity_type, alliance) in enumerate(((unit_type, 1), (19, 4))):
        values = [0.0] * 27
        values[0] = float(entity_type)
        values[1] = float(alliance)
        values[2] = 1.0
        values[3] = 10.0 + slot
        values[4] = 20.0 + slot
        values[5] = 8.0
        values[6] = 0.5
        values[7] = 100.0
        values[8] = 100.0
        values[13] = 1.0
        values[16] = 1.0 if slot == 0 else 0.0
        values[25] = 1.0
        entities.append({"values": values, "stale": bool(stale and slot == 0)})
    action = {
        "ability_id": ability,
        "kind": kind,
        "queue": queue,
        "target_kind": target_kind,
        "target_index": target_index if target_kind == "unit" else None,
        "target_point": list(target_point) if target_kind == "point" else None,
        "selected_indices": list(selected),
    }
    return {
        "schema": "tvt-bc-v2",
        "replay_sha256": replay,
        "player_id": 1,
        "split": split,
        "engine_base_build": 86383,
        "data_version": "dv-86383",
        "source_action_index": index,
        "action_loop": index * 10 + 2,
        "observation": {
            "entities": entities,
            "scalars": [50, 0, 15, 12, 0, 12, 1, 0],
            "loop": index * 10,
        },
        "action": action,
        "next_action_delay": delay,
        "delay_mask": delay_mask,
    }


def default_train_rows():
    replay = "a" * 64
    return [
        make_row(
            replay,
            "train",
            0,
            ability=10,
            selected=[0],
            target_kind="unit",
            target_index=1,
            queue=True,
            delay=4,
            delay_mask=True,
        ),
        make_row(
            replay,
            "train",
            1,
            ability=20,
            kind="autocast",
            selected=[1],
            target_kind="none",
            queue=None,
            delay=2,
            delay_mask=True,
        ),
        make_row(
            replay,
            "train",
            2,
            ability=10,
            selected=[0, 1],
            target_kind="point",
            target_point=(64.5, 40.25),
            queue=False,
            delay=None,
            delay_mask=False,
        ),
        make_row(replay, "train", 4, ability=10, selected=[0], queue=True),
    ]


def default_validation_rows():
    replay = "b" * 64
    return [
        make_row(
            replay, "validation", 0, ability=10, selected=[0], queue=True, delay=3, delay_mask=True
        ),
        make_row(replay, "validation", 1, ability=10, selected=[0], queue=False),
    ]


def default_test_rows():
    replay = "c" * 64
    return [
        make_row(replay, "test", 0, ability=10, selected=[0], queue=True, delay=3, delay_mask=True),
        make_row(replay, "test", 1, ability=10, selected=[0], queue=False),
    ]


def ability_counts(rows):
    counts = Counter(row["action"]["ability_id"] for row in rows)
    return {str(ability): count for ability, count in sorted(counts.items())}


class CacheFixture:
    def __init__(self, root, *, train_rows=None, train_observed=None, map_size=(128, 128)):
        self.root = root
        self.collection = root / "data" / "collection.json"
        self.datasets = []
        self.children = {}
        train = default_train_rows() if train_rows is None else train_rows
        observed = ability_counts(train) if train_observed is None else train_observed
        self._add("train", "train", train, observed, map_size=map_size)
        validation = default_validation_rows()
        self._add("validation", "validation", validation, ability_counts(validation))
        test = default_test_rows()
        self._add("test", "test", test, ability_counts(test))

    def _add(self, name, split, rows, observed, *, map_size=(128, 128)):
        directory = self.root / "data" / name
        directory.mkdir(parents=True, exist_ok=True)
        map_path = write_json(
            directory / "map-data.json",
            {
                "map_size": {"x": map_size[0], "y": map_size[1]},
                "pathing_grid": {},
                "terrain_height": {},
                "placement_grid": {},
                "playable_area": {},
            },
        )
        shard_path = directory / "rows.jsonl.gz"
        with gzip.open(shard_path, "wt", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")
        engine = {"base_build": 86383, "data_version": "dv-86383"}
        shard = {
            "path": "rows.jsonl.gz",
            "sha256": digest(shard_path),
            "split": split,
            "replay_sha256": rows[0]["replay_sha256"],
            "player_id": 1,
            "static_map": "map-data.json",
            "static_map_sha256": digest(map_path),
        }
        child = {
            "schema": "tvt-bc-v2",
            "training_ready": True,
            "engine_base_build": 86383,
            "engine": engine,
            "files": [{"sha256": rows[0]["replay_sha256"], "split": split}],
            "shards": [shard],
            "ability_ids": [10, 20],
            "observed_ability_counts": observed,
        }
        child_path = write_json(directory / "manifest.json", child)
        self.children[name] = child_path
        self.datasets.append(
            {
                "manifest": f"{name}/manifest.json",
                "manifest_sha256": digest(child_path),
                "engine": engine,
                "engine_base_build": 86383,
            }
        )
        self.write_collection()

    def write_collection(self):
        write_json(
            self.collection,
            {
                "schema": "tvt-bc-collection-v1",
                "row_schema": "tvt-bc-v2",
                "training_ready": True,
                "datasets": self.datasets,
            },
        )


class CacheTests(unittest.TestCase):
    def test_build_and_load_train_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = CacheFixture(root)
            output = root / "cache"
            cache, cache_path = build_cache(fixture.collection, 86383, output)
            self.assertEqual(cache["schema"], CACHE_SCHEMA)
            self.assertEqual(cache_path, output / "cache.json")
            self.assertEqual(cache["collection_sha256"], digest(fixture.collection))
            self.assertEqual(cache["catalog"]["ability_ids"], [10, 20])
            self.assertEqual(cache["splits"]["train"]["sequences"], 2)
            self.assertEqual(cache["splits"]["train"]["rows"], 4)
            self.assertEqual(cache["splits"]["validation"]["sequences"], 1)
            self.assertEqual(cache["splits"]["validation"]["rows"], 2)
            self.assertEqual(cache["normalization"]["train_entity_rows"], 8)
            self.assertEqual(cache["normalization"]["train_scalar_rows"], 4)
            self.assertEqual(len(cache["normalization"]["entity_mean"]), 19)
            self.assertEqual(len(cache["normalization"]["scalar_mean"]), 9)
            self.assertEqual(cache["unit_type_vocab"], [18, 19])
            self.assertEqual(cache["ability_kinds"], [["command"], ["autocast"]])
            self.assertIsNone(cache["reference_cache_sha256"])
            self.assertGreater(cache["cache_bytes"], 0)
            loaded, sequences = load_cache(output)
            self.assertEqual(len(loaded["splits"]["train"]["files"]), 1)
            self.assertEqual(len(sequences["train"]), 2)
            first = sequences["train"][0]
            self.assertEqual(first["length"], 3)
            self.assertEqual(tuple(first["entities"].shape), (3, 2, 27))
            self.assertEqual(first["map_size"], (128, 128))
            self.assertEqual(first["ability"].tolist(), [0, 1, 0])
            self.assertEqual(first["kind"].tolist(), [0, 1, 0])
            self.assertEqual(first["selected_mask"].sum().item(), 4)
            self.assertEqual(first["queue_mask"].tolist(), [True, False, True])
            self.assertEqual(first["delay_mask"].tolist(), [True, True, False])
            self.assertEqual(first["key"][2:], (0, 2))
            self.assertEqual(sequences["train"][1]["key"][2:], (4, 4))
            self.assertEqual(sequences["validation"][0]["length"], 2)

    def test_test_cache_reuses_reference_statistics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = CacheFixture(root)
            reference, reference_path = build_cache(fixture.collection, 86383, root / "reference")
            test, test_path = build_cache(
                fixture.collection,
                86383,
                root / "test-cache",
                splits=("test",),
                reference_cache=root / "reference",
            )
            self.assertEqual(test["normalization"], reference["normalization"])
            self.assertEqual(test["unit_type_vocab"], reference["unit_type_vocab"])
            self.assertEqual(test["ability_kinds"], reference["ability_kinds"])
            self.assertEqual(test["reference_cache_sha256"], digest(reference_path))
            self.assertEqual(test["collection_sha256"], reference["collection_sha256"])
            self.assertEqual(test["catalog_digest"], reference["catalog_digest"])
            self.assertEqual(test["splits"]["test"]["sequences"], 1)
            self.assertEqual(test["splits"]["test"]["rows"], 2)
            _, sequences = load_cache(test_path)
            self.assertEqual(sequences["test"][0]["length"], 2)

    def test_output_must_be_new(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = CacheFixture(root)
            output = root / "cache"
            output.mkdir()
            with self.assertRaisesRegex(ValueError, "already exists"):
                build_cache(fixture.collection, 86383, output)

    def test_test_or_validation_without_train_requires_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = CacheFixture(root)
            with self.assertRaisesRegex(ValueError, "Training statistics require"):
                build_cache(fixture.collection, 86383, root / "test", splits=("test",))
            with self.assertRaisesRegex(ValueError, "Training statistics require"):
                build_cache(fixture.collection, 86383, root / "validation", splits=("validation",))

    def test_mixed_kind_ability_is_stored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = default_train_rows()
            rows[1] = make_row(
                rows[1]["replay_sha256"],
                "train",
                1,
                ability=10,
                kind="autocast",
                selected=[1],
                queue=None,
                delay=2,
                delay_mask=True,
            )
            fixture = CacheFixture(root, train_rows=rows)
            cache, _ = build_cache(fixture.collection, 86383, root / "cache")
            self.assertEqual(cache["ability_kinds"][0], ["autocast", "command"])
            _, sequences = load_cache(root / "cache")
            self.assertEqual(sequences["train"][0]["kind"].tolist(), [0, 1, 0])

    def test_ability_outside_catalog_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = default_train_rows()
            rows[0] = make_row(
                rows[0]["replay_sha256"],
                "train",
                0,
                ability=99,
                selected=[0],
                queue=True,
                delay=1,
                delay_mask=True,
            )
            fixture = CacheFixture(root, train_rows=rows, train_observed={"10": 3, "20": 1})
            with self.assertRaisesRegex(ValueError, "outside canonical catalog"):
                build_cache(fixture.collection, 86383, root / "cache")

    def test_zero_map_size_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = CacheFixture(root, map_size=(0, 0))
            with self.assertRaisesRegex(ValueError, "map size"):
                build_cache(fixture.collection, 86383, root / "cache")

    def test_shard_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = CacheFixture(root)
            build_cache(fixture.collection, 86383, root / "cache")
            shard = root / "cache" / "train" / "shard-00000.pt"
            shard.write_bytes(shard.read_bytes() + b"x")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                load_cache(root / "cache")

    def test_load_can_select_a_split(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = CacheFixture(root)
            build_cache(fixture.collection, 86383, root / "cache")
            _, sequences = load_cache(root / "cache", splits=("validation",))
            self.assertEqual(list(sequences), ["validation"])


if __name__ == "__main__":
    unittest.main()
