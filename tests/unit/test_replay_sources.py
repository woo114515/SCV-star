"""Guard against wrong race selection, version substitution and duplicate inflation."""

import copy
import unittest
from pathlib import Path
from unittest.mock import Mock

from s2clientprotocol import sc2api_pb2 as sc

from scv_star.data.replay_sources import is_tvt, summarize
from scv_star.runtime.replay_probe import human_tvt, observe_replay, replay_identity


class ReplaySelectionTests(unittest.TestCase):
    def test_random_assigned_terran_is_tvt(self):
        self.assertTrue(
            is_tvt(
                {
                    "Players": [
                        {"SelectedRace": "Rand", "AssignedRace": "Terr"},
                        {"AssignedRace": "Terr"},
                    ]
                }
            )
        )

    def test_other_race_and_team_game_are_not_tvt(self):
        for races in ([], ["Terr"], ["Terr", "Zerg"], ["Terr"] * 4):
            self.assertFalse(is_tvt({"Players": [{"AssignedRace": r} for r in races]}))

    def test_computer_participant_is_not_a_human_example(self):
        info = {
            "player_info": [
                {"player_info": {"type": "Participant", "race_actual": "Terran"}},
                {"player_info": {"type": "Computer", "race_actual": "Terran"}},
            ]
        }
        self.assertFalse(human_tvt(info))

    def test_header_data_version_disagreement_is_rejected(self):
        row = {
            "metadata": {
                "GameVersion": "5.0.15.97579",
                "BaseBuild": "Base97579",
                "DataBuild": "97579",
                "DataVersion": "A",
            },
            "header_version": {"m_baseBuild": 97579},
            "header_data_version": "A",
            "header_data_build": 97579,
        }
        self.assertEqual(replay_identity(row)["base_build"], 97579)
        for field in ("header_data_version", "header_data_build", "header_version"):
            modified = copy.deepcopy(row)
            modified[field] = {"m_baseBuild": 97563} if field == "header_version" else "B"
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                replay_identity(modified)

    def test_failed_parse_does_not_inflate_exact_duplicate_count(self):
        summary = summarize([{"sha256": "a"}, {"sha256": "a"}, {"error": "bad"}])
        self.assertEqual(summary["exact_duplicate_files"], 1)
        self.assertEqual(summary["errors"], 1)


class ReplayTerminalTests(unittest.TestCase):
    def client_at_terminal(self):
        response = sc.ResponseObservation()
        response.observation.player_common.player_id = 1
        response.observation.game_loop = 112
        response.observation.raw_data.units.add(alliance=1)
        response.observation.raw_data.map_state.visibility.data = b"\xff"
        response.player_result.add(player_id=1, result=sc.Victory)
        response.player_result.add(player_id=2, result=sc.Defeat)
        client = Mock()
        client.observe.return_value = response
        return client

    def test_matching_result_preserves_unobserved_tail_instead_of_claiming_alignment(self):
        result = observe_replay(
            self.client_at_terminal(),
            Path("synthetic.SC2Replay"),
            1,
            130,
            112,
            {1: "Victory", 2: "Defeat"},
        )
        self.assertEqual(result["recorded_tail_loops"], 18)
        self.assertFalse(result["terminal_loop_matches_header"])
        self.assertFalse(result["bc_alignment_verified"])

    def test_wrong_winner_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "winners differ"):
            observe_replay(
                self.client_at_terminal(),
                Path("synthetic.SC2Replay"),
                1,
                130,
                112,
                {1: "Defeat", 2: "Victory"},
            )


if __name__ == "__main__":
    unittest.main()
