from pathlib import Path
import json
import tempfile
import unittest

from scv_star.data.bc_batch import (
    atomic_json,
    normalise_existing_screen,
    normalise_source,
    plan_candidates,
    validate_screen,
)


class BatchTests(unittest.TestCase):
    def test_source_label_preserves_all_restricted_source_terms(self):
        row = {
            "sha256": "a",
            "source_records": [
                {
                    "manifest": "data/manifests/spawningtool-kevin-2026-10-06/catalog.jsonl",
                    "record": {
                        "sha256": "a",
                        "source": "Spawning Tool",
                        "source_record": "spawningtool-kevin-2026-10-05.json",
                    },
                }
            ],
            "source_policies": [
                {
                    "source": "Spawning Tool (Kevin export)",
                    "record": "data/manifests/spawningtool-kevin-2026-10-05.json",
                    "redistribution_allowed": False,
                }
            ],
        }
        result = normalise_source(row)
        self.assertEqual(result["collection_source"], "SpawningTool_Kevin_export")
        self.assertEqual(result["source_policies"], row["source_policies"])
        self.assertEqual(result["source_records"], row["source_records"])
        self.assertNotIn("collection_source", row)

    def test_unknown_source_cannot_be_accepted_with_an_invented_label(self):
        with self.assertRaisesRegex(ValueError, "Unrecognised"):
            normalise_source(
                {
                    "sha256": "a",
                    "source_records": [{"record": {"sha256": "a"}}],
                    "source_policies": [{}],
                }
            )

    def row(self, digest, group):
        return {
            "sha256": digest,
            "init_data_sha256": group,
            "queue": "tvt_candidates",
            "game_loops": 5000,
            "races": ["Terr", "Terr"],
            "identity_mismatches": [],
        }

    def test_existing_fingerprint_keeps_split_and_rejected_files_are_skipped(self):
        old = {"files": [{"sha256": "old", "init_data_sha256": "same", "split": "test"}]}
        rows = [self.row("old", "same"), self.row("copy", "same"), self.row("bad", "other")]
        plan = plan_candidates(rows, [old], {"bad"}, 123)
        self.assertEqual([(r["sha256"], r["bc_split"]) for r in plan], [("copy", "test")])

    def test_plan_is_deterministic_and_binds_new_known_copies(self):
        rows = [self.row("a", "shared"), self.row("b", "shared")]
        first = plan_candidates(rows, [], set(), 123)
        self.assertEqual(first, plan_candidates(list(reversed(rows)), [], set(), 123))
        self.assertEqual(first[0]["bc_split"], first[1]["bc_split"])

    def test_unknown_outcome_and_short_games_do_not_enter_plan(self):
        unknown, short = self.row("a", "one"), self.row("b", "two")
        unknown["outcome_label_ready"] = False
        short["game_loops"] = 4480
        self.assertEqual(plan_candidates([unknown, short], [], set(), 123), [])

    def test_missing_api_evidence_cannot_confirm_cn_humans(self):
        row = self.row("a", "one")
        row.update(
            screening_status="eligible",
            outcome_label_ready=True,
            screening={"evidence": "missing.json"},
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                normalise_existing_screen(row, Path(directory))

    def test_atomic_json_preserves_unicode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            atomic_json(path, {"map": "地图"})
            atomic_json(path, {"map": "新版地图"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["map"], "新版地图")
            self.assertFalse(path.with_name("progress.json.next").exists())

    def test_api_unknown_results_are_rejected_explicitly(self):
        identity = {
            "game_version": "5.0.8.86383",
            "base_build": 86383,
            "data_build": 86383,
            "data_version": "v",
        }
        info = dict(
            identity,
            game_duration_loops=5000,
            map_name="Ladder",
            player_info=[
                {"player_info": {"player_id": i, "type": "Participant", "race_actual": "Terran"}}
                for i in (1, 2)
            ],
        )
        with self.assertRaisesRegex(ValueError, "Unknown outcome"):
            validate_screen(info, identity)


if __name__ == "__main__":
    unittest.main()
