import unittest
from scv_star.data.collection_archive import merge


class CollectionArchiveTests(unittest.TestCase):
    def row(self, digest="a"):
        return {
            "sha256": digest,
            "identity": {"base_build": 1},
            "local_replay": digest,
            "queue": "tvt_candidates",
            "source_policies": [{"redistribution_allowed": False}],
            "human_status": "unverified",
        }

    def test_historical_exclusion_leaves_active_queue_without_losing_provenance(self):
        row = self.row()
        row["human_status"] = "excluded"
        result = merge([row], [], [])[0]
        self.assertEqual(result["queue"], "excluded")
        self.assertEqual(result["source_policies"], row["source_policies"])
        self.assertEqual(row["queue"], "tvt_candidates")

    def test_light_screen_does_not_imply_full_replay_or_training_ready(self):
        row = self.row()
        screen = {**row, "screening_status": "eligible"}
        result = merge([row], [screen], [])[0]
        self.assertEqual(result["full_replay_status"], "not_sampled")
        self.assertFalse(result["training_ready"])

    def test_conflicting_overlay_or_orphan_sample_rejected(self):
        row = self.row()
        with self.assertRaises(ValueError):
            merge(
                [row], [{**row, "screening_status": "eligible", "identity": {"base_build": 2}}], []
            )
        with self.assertRaises(ValueError):
            merge([row], [], [{"sha256": "a", "status": "passed"}])


if __name__ == "__main__":
    unittest.main()
