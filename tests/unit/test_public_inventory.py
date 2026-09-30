"""Keep version combinations separate and count duplicates without inflating data."""

import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from scv_star.data.public_inventory import identity, initialize, inspect, scan, summarize


class InventoryTests(unittest.TestCase):
    def test_appledouble_sidecar_is_not_counted_as_a_replay(self):
        row = inspect(bytes.fromhex("0005160700020000") + b"Mac OS X")
        with sqlite3.connect(":memory:") as db:
            initialize(db)
            db.execute("INSERT INTO replays VALUES (?,?)", (row["sha256"], json.dumps(row)))
            db.execute("INSERT INTO locations VALUES (?,?,?)", ("pack", "sidecar", row["sha256"]))
            result = summarize(db)
            self.assertEqual(result["counts"]["non_replay_files"], 1)
            self.assertEqual(result["counts"].get("valid_replay_files", 0), 0)
            self.assertEqual(result["byte_identical_duplicates"], 0)

    def test_header_metadata_conflict_is_reported(self):
        header = {
            "m_version": {
                "m_major": 5,
                "m_minor": 0,
                "m_revision": 16,
                "m_build": 97563,
                "m_baseBuild": 97563,
            },
            "m_dataBuildNum": 97563,
            "m_ngdpRootKey": {"m_data": b"\x01" * 16},
        }
        ident, conflicts = identity({"DataVersion": "02" * 16}, header)
        self.assertEqual(ident["base_build"], 97563)
        self.assertEqual(conflicts, ["DataVersion"])

    def test_same_build_with_different_data_versions_stays_separate(self):
        with sqlite3.connect(":memory:") as db:
            initialize(db)
            for key in ["A", "B"]:
                row = {
                    "identity": {
                        "base_build": 1,
                        "data_build": 1,
                        "data_version": key,
                        "game_version": "5.0.0.1",
                    },
                    "identity_mismatches": [],
                    "tvt_candidate": True,
                    "human_1v1": True,
                    "game_loops": 3000,
                }
                db.execute("INSERT INTO replays VALUES (?,?)", (key, json.dumps(row)))
                db.execute("INSERT INTO locations VALUES (?,?,?)", (key, "a", key))
            result = summarize(db)
            self.assertEqual(len(result["engine_groups"]), 2)
            self.assertEqual(result["counts"]["tvt_candidates"], 2)

    def test_corrupt_duplicate_replays_count_once_and_are_not_extracted(self):
        with tempfile.TemporaryDirectory() as tmp, sqlite3.connect(":memory:") as db:
            initialize(db)
            path = Path(tmp) / "pack.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("../escape.SC2Replay", b"not a replay")
                archive.writestr("copy.SC2Replay", b"not a replay")
            scan(db, path)
            scan(db, path)
            result = summarize(db)
            self.assertEqual(result["byte_identical_duplicates"], 1)
            self.assertEqual(result["counts"]["parse_errors"], 1)
            self.assertEqual(result["sources"], 1)
            self.assertEqual(list(Path(tmp).iterdir()), [path])
