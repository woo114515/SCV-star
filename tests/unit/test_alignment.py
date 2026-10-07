import unittest
from unittest.mock import patch

from s2clientprotocol import sc2api_pb2 as sc

from scv_star.data.alignment import pair_command, policy_observation
from scv_star.runtime.alignment_probe import directory_bytes


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.action = sc.Action(game_loop=10)
        self.action.action_raw.unit_command.unit_tags.append(7)
        self.obs = sc.Observation(game_loop=9)
        self.obs.raw_data.units.add(tag=7, alliance=1)

    def test_rejects_current_and_future_observations(self):
        for loop in (10, 11):
            self.obs.game_loop = loop
            with self.assertRaises(ValueError):
                pair_command(self.action, self.obs)

    def test_missing_and_nonself_tags_are_explicit(self):
        self.action.action_raw.unit_command.unit_tags.append(8)
        self.obs.raw_data.units[0].alliance = 4
        result = pair_command(self.action, self.obs)
        self.assertEqual(result["missing_selected_tags"], [8])
        self.assertEqual(result["nonself_selected_tags"], [7])
        self.assertFalse(result["selected_tags_resolved"])

    def test_missing_timestamp_rejected(self):
        self.action.ClearField("game_loop")
        with self.assertRaises(ValueError):
            pair_command(self.action, self.obs)

    def test_diagnostic_input_excludes_score_and_ui(self):
        self.obs.score.score = 123
        clean = policy_observation(self.obs)
        self.assertFalse(clean.HasField("score"))
        self.assertEqual(clean.game_loop, 9)
        self.assertTrue(pair_command(self.action, clean)["selected_tags_resolved"])
        self.assertFalse(pair_command(self.action, clean)["training_ready"])

    def test_storage_measurement_rejects_unreadable_directories(self):
        def inaccessible(path, onerror):
            onerror(PermissionError("denied folder"))
            return iter(())

        with patch("scv_star.runtime.alignment_probe.os.walk", side_effect=inaccessible):
            with self.assertRaises(PermissionError):
                directory_bytes("unused")


if __name__ == "__main__":
    unittest.main()
