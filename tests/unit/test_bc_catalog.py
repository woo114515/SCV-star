import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scv_star.data.bc_catalog import (
    CATALOG_SCHEMA,
    build_ability_catalog,
    catalog_digest,
    sha256_file,
)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class CatalogFixture:
    def __init__(self, root):
        self.root = root
        self.collection = root / "data" / "collection.json"
        self.children = {}
        datasets = []
        for build, name, abilities, observed in (
            (86383, "a", [1, 2, 3, 4], {"1": 10, "2": 5}),
            (86383, "b", [1, 2, 3], {"1": 7, "3": 2}),
            (84643, "c", [1, 2, 9], {"1": 3}),
        ):
            engine = {"base_build": build, "data_version": f"dv-{build}"}
            child = write_json(
                root / "data" / name / "manifest.json",
                {
                    "schema": "tvt-bc-v2",
                    "training_ready": True,
                    "engine_base_build": build,
                    "engine": engine,
                    "ability_ids": abilities,
                    "observed_ability_counts": observed,
                },
            )
            self.children[name] = child
            datasets.append(
                {
                    "manifest": child.relative_to(root / "data").as_posix(),
                    "manifest_sha256": digest(child),
                    "engine": engine,
                }
            )
        self.datasets = datasets
        write_json(
            self.collection,
            {
                "schema": "tvt-bc-collection-v1",
                "row_schema": "tvt-bc-v2",
                "training_ready": True,
                "datasets": datasets,
            },
        )

    def refresh(self, name):
        child = self.children[name]
        for entry in self.datasets:
            if Path(entry["manifest"]).parent.name == name:
                entry["manifest_sha256"] = digest(child)
        write_json(
            self.collection,
            {
                "schema": "tvt-bc-collection-v1",
                "row_schema": "tvt-bc-v2",
                "training_ready": True,
                "datasets": self.datasets,
            },
        )

    def child(self, name):
        return json.loads(self.children[name].read_text(encoding="utf-8"))

    def set_child(self, name, **changes):
        child = self.child(name)
        child.update(changes)
        write_json(self.children[name], child)
        self.refresh(name)


class CatalogTests(unittest.TestCase):
    def test_intersection_and_observed_subset(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = CatalogFixture(Path(temporary))
            catalog = build_ability_catalog(fixture.collection, 86383)
            self.assertEqual(catalog["schema"], CATALOG_SCHEMA)
            self.assertEqual(catalog["build"], 86383)
            self.assertEqual(catalog["datasets"], 2)
            self.assertEqual(catalog["ability_ids"], [1, 2, 3])
            self.assertEqual(catalog["observed_ability_ids"], [1, 2, 3])
            self.assertEqual(catalog["collection_sha256"], digest(fixture.collection))
            self.assertEqual(sha256_file(fixture.children["a"]), digest(fixture.children["a"]))
            reordered = dict(reversed(list(catalog.items())))
            self.assertEqual(catalog_digest(catalog), catalog_digest(reordered))

    def test_observed_ability_outside_intersection_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = CatalogFixture(Path(temporary))
            fixture.set_child("b", observed_ability_counts={"1": 7, "99": 1})
            with self.assertRaisesRegex(ValueError, "outside canonical catalog"):
                build_ability_catalog(fixture.collection, 86383)

    def test_unknown_build_and_unaccepted_collection_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = CatalogFixture(Path(temporary))
            with self.assertRaisesRegex(ValueError, "No accepted datasets"):
                build_ability_catalog(fixture.collection, 999)
            collection = json.loads(fixture.collection.read_text(encoding="utf-8"))
            collection["training_ready"] = False
            write_json(fixture.collection, collection)
            with self.assertRaisesRegex(ValueError, "accepted collection"):
                build_ability_catalog(fixture.collection, 86383)

    def test_child_hash_schema_and_identity_are_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = CatalogFixture(Path(temporary))
            fixture.children["a"].write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                build_ability_catalog(fixture.collection, 86383)
        with tempfile.TemporaryDirectory() as temporary:
            fixture = CatalogFixture(Path(temporary))
            fixture.set_child("a", schema="other")
            with self.assertRaisesRegex(ValueError, "not accepted"):
                build_ability_catalog(fixture.collection, 86383)
        with tempfile.TemporaryDirectory() as temporary:
            fixture = CatalogFixture(Path(temporary))
            fixture.set_child("a", engine={"base_build": 1, "data_version": "other"})
            with self.assertRaisesRegex(ValueError, "engine identity mismatch"):
                build_ability_catalog(fixture.collection, 86383)
        with tempfile.TemporaryDirectory() as temporary:
            fixture = CatalogFixture(Path(temporary))
            fixture.set_child("a", ability_ids=[1, 1, 2])
            with self.assertRaisesRegex(ValueError, "Duplicate child ability"):
                build_ability_catalog(fixture.collection, 86383)


if __name__ == "__main__":
    unittest.main()
