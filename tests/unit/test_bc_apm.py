import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scv_star.data.bc_apm import (
    APM_SCHEMA,
    POINTER_SCHEMA,
    build_apm_manifest,
    load_apm_labels,
    quality_label,
)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class ApmlFixture:
    def __init__(self, root):
        self.root = root
        self.replay = "a" * 64
        self.collection = write_json(
            root / "data/processed/m2-expand/collection.json",
            {
                "schema": "tvt-bc-collection-v1",
                "row_schema": "tvt-bc-v2",
                "training_ready": True,
                "player_views": 2,
                "rows": 2,
            },
        )
        child = write_json(
            root / "data/processed/m2-expand/child/manifest.json",
            {
                "schema": "tvt-bc-v2",
                "training_ready": True,
                "shards": [
                    {"replay_sha256": self.replay, "player_id": 1, "rows": 1},
                    {"replay_sha256": self.replay, "player_id": 2, "rows": 1},
                ],
            },
        )
        evidence = write_json(
            root / "artifacts/evidence/one.json",
            {
                "info": {
                    "player_info": [
                        {"player_info": {"player_id": 1}, "player_apm": 300},
                        {"player_info": {"player_id": 2}, "player_apm": 100},
                    ]
                }
            },
        )
        self.audit = write_json(
            root / "artifacts/audit.json",
            {
                "qualification_issues": 0,
                "files": [
                    {
                        "sha256": self.replay,
                        "identity": {"base_build": 86383, "data_version": "dv"},
                        "split": "train",
                        "manifest": "data/processed/m2-expand/child/manifest.json",
                        "api_evidence": "artifacts/evidence/one.json",
                        "api_evidence_sha256": digest(evidence),
                        "api_qualification_passed": True,
                    }
                ],
            },
        )
        self.child = child
        self.evidence = evidence

    def build(self, output="out/quality.json", pointer="out/pointer.json", **changes):
        payload = {
            "collection": self.collection,
            "audit": self.audit,
            "evidence_root": self.root,
            "output": self.root / output,
            "pointer": self.root / pointer if pointer else None,
            "minimum_files": 1,
        }
        payload.update(changes)
        return build_apm_manifest(**payload)


class ApmTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(quality_label(251), "high")
        self.assertEqual(quality_label(250), "medium")
        self.assertEqual(quality_label(150), "medium")
        self.assertEqual(quality_label(149), "low")
        for bad in (None, -1, True, "250"):
            with self.assertRaises(ValueError):
                quality_label(bad)

    def test_build_load_and_pointer(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ApmlFixture(Path(temporary))
            document, output = fixture.build()
            self.assertEqual(document["schema"], APM_SCHEMA)
            self.assertEqual(document["views"], 2)
            self.assertEqual(document["rows"], 2)
            self.assertEqual(document["summary"]["views_by_quality"], {"high": 1, "low": 1})
            self.assertEqual(document["summary"]["rows_by_quality"], {"high": 1, "low": 1})
            self.assertEqual(document["summary"]["views_by_build"], {"86383": 2})
            labels = [record["quality_label"] for record in document["records"]]
            self.assertEqual(labels, ["high", "low"])
            pointer = json.loads((fixture.root / "out/pointer.json").read_text())
            self.assertEqual(pointer["schema"], POINTER_SCHEMA)
            self.assertEqual(pointer["quality_manifest"], "quality.json")
            loaded, mapping, path = load_apm_labels(fixture.root / "out/pointer.json")
            self.assertEqual(loaded["schema"], APM_SCHEMA)
            self.assertEqual(mapping[(fixture.replay, 1)]["quality_label"], "high")
            self.assertEqual(mapping[(fixture.replay, 2)]["player_apm"], 100)
            self.assertEqual(path, output)
            direct, direct_mapping, _ = load_apm_labels(output)
            self.assertEqual(direct_mapping, mapping)

    def test_existing_output_and_pointer_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ApmlFixture(Path(temporary))
            (fixture.root / "out").mkdir()
            (fixture.root / "out/quality.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "already exists"):
                fixture.build()
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ApmlFixture(Path(temporary))
            fixture.build()
            with self.assertRaisesRegex(ValueError, "already exists"):
                fixture.build(pointer="out/new-pointer.json")

    def test_evidence_hash_missing_and_bad_apm_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ApmlFixture(Path(temporary))
            fixture.evidence.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                fixture.build()
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ApmlFixture(Path(temporary))
            (fixture.root / "artifacts/evidence/one.json").unlink()
            with self.assertRaises(FileNotFoundError):
                fixture.build()
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ApmlFixture(Path(temporary))
            write_json(
                fixture.evidence,
                {"info": {"player_info": [{"player_info": {"player_id": 1}}]}},
            )
            self._refresh_audit(fixture)
            with self.assertRaisesRegex(ValueError, "two players"):
                fixture.build()
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ApmlFixture(Path(temporary))
            write_json(
                fixture.evidence,
                {
                    "info": {
                        "player_info": [
                            {"player_info": {"player_id": 1}, "player_apm": -1},
                            {"player_info": {"player_id": 2}, "player_apm": 100},
                        ]
                    }
                },
            )
            self._refresh_audit(fixture)
            with self.assertRaises(ValueError):
                fixture.build()

    def _refresh_audit(self, fixture):
        audit = json.loads(fixture.audit.read_text())
        audit["files"][0]["api_evidence_sha256"] = digest(fixture.evidence)
        write_json(fixture.audit, audit)

    def test_collection_row_mismatch_and_audit_issues_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ApmlFixture(Path(temporary))
            collection = json.loads(fixture.collection.read_text())
            collection["rows"] = 3
            write_json(fixture.collection, collection)
            with self.assertRaisesRegex(ValueError, "mismatch"):
                fixture.build()
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ApmlFixture(Path(temporary))
            audit = json.loads(fixture.audit.read_text())
            audit["qualification_issues"] = 1
            write_json(fixture.audit, audit)
            with self.assertRaisesRegex(ValueError, "issues"):
                fixture.build()

    def test_pointer_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ApmlFixture(Path(temporary))
            fixture.build()
            pointer_path = fixture.root / "out/pointer.json"
            (fixture.root / "out/quality.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                load_apm_labels(pointer_path)


if __name__ == "__main__":
    unittest.main()
