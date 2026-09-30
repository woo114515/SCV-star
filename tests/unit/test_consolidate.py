"""Consolidation preserves uncertainty and never upgrades metadata to training data."""

import copy
import sqlite3
import unittest

from scv_star.data.consolidate import verification, work_queue
from scv_star.data.public_inventory import initialize, matchup, record


class ConsolidationTests(unittest.TestCase):
    def test_missing_race_information_is_unknown_not_other_matchups(self):
        for races in [[], [None, None], ["Terr", None]]:
            self.assertEqual(matchup({"races": races}), "unclassified_race")

    def test_multiple_players_are_not_a_two_player_matchup(self):
        self.assertEqual(matchup({"races": ["Terr"] * 4}), "nonstandard_player_count")

    def test_identity_conflict_tvt_goes_to_review_before_version_selection(self):
        row = {
            "races": ["Terr", "Terr"],
            "identity": {"base_build": 91115},
            "identity_mismatches": ["GameVersion"],
        }
        self.assertEqual(work_queue(row), "identity_review")
        row["identity_mismatches"] = []
        self.assertEqual(work_queue(row), "tvt_91115_paused")

    def test_one_completed_perspective_does_not_qualify_as_verified(self):
        report = {
            "status": "passed",
            "engine": {},
            "step": 112,
            "replays": [
                {
                    "sha256": "a",
                    "status": "replay_passed",
                    "perspectives": [{"player_id": 1, "status": "passed", "terminal": True}],
                }
            ],
        }
        self.assertEqual(verification(report), {})
        both = copy.deepcopy(report)
        both["replays"][0]["perspectives"].append(
            {"player_id": 2, "status": "passed", "terminal": True}
        )
        self.assertIn("a", verification(both))
        self.assertFalse(verification(both)["a"]["bc_alignment_verified"])

    def test_same_bytes_keep_all_source_locations(self):
        with sqlite3.connect(":memory:") as db:
            initialize(db)
            record(db, "first.zip", "a.SC2Replay", b"bad replay")
            record(db, "second.zip", "b.SC2Replay", b"bad replay")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM replays").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM locations").fetchone()[0], 2)
