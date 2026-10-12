import unittest
from s2clientprotocol import sc2api_pb2 as sc, raw_pb2 as raw
from scv_star.data.bc_schema import features, encode_action, decode_action, UNIT_FIELDS


class BCSchemaTests(unittest.TestCase):
    def observation(self):
        obs = sc.Observation(game_loop=10)
        obs.raw_data.units.add(tag=20, alliance=1, unit_type=45, display_type=1)
        obs.raw_data.units.add(tag=10, alliance=4, unit_type=48, display_type=2, health=99)
        return obs

    def test_point_target_and_queue_roundtrip(self):
        action = raw.ActionRaw()
        c = action.unit_command
        c.ability_id = 16
        c.unit_tags.append(20)
        c.queue_command = True
        c.target_world_space_pos.x = 11.25
        c.target_world_space_pos.y = 12.5
        obs = self.observation()
        encoded = encode_action(action, obs, {16})
        self.assertEqual(decode_action(encoded, features(obs)["entity_tags"]), action)

    def test_unit_target_roundtrip_and_stale_health_mask(self):
        action = raw.ActionRaw()
        action.unit_command.ability_id = 23
        action.unit_command.unit_tags.append(20)
        action.unit_command.target_unit_tag = 10
        obs = self.observation()
        f = features(obs)
        self.assertEqual(f["entities"][0]["values"][UNIT_FIELDS.index("health")], 0)
        self.assertEqual(decode_action(encode_action(action, obs, {23}), f["entity_tags"]), action)

    def test_autocast_roundtrip(self):
        action = raw.ActionRaw()
        action.toggle_autocast.ability_id = 386
        action.toggle_autocast.unit_tags.append(20)
        obs = self.observation()
        self.assertEqual(
            decode_action(encode_action(action, obs, {386}), features(obs)["entity_tags"]), action
        )

    def test_unknown_and_missing_selected_explicit(self):
        action = raw.ActionRaw()
        action.unit_command.ability_id = 16
        action.unit_command.unit_tags.append(123)
        with self.assertRaisesRegex(ValueError, "unknown_ability"):
            encode_action(action, self.observation(), set())
        with self.assertRaisesRegex(ValueError, "selected_tag_missing"):
            encode_action(action, self.observation(), {16})

    def test_enemy_orders_and_scores_not_policy_inputs(self):
        obs = self.observation()
        obs.raw_data.units[1].orders.add(ability_id=16)
        obs.score.score = 999
        self.assertFalse(features(obs)["entities"][0]["orders"])
        self.assertNotIn("score", features(obs))

    def test_same_loop_commands_not_deduplicated(self):
        action = raw.ActionRaw()
        action.unit_command.ability_id = 16
        action.unit_command.unit_tags.append(20)
        labels = [encode_action(a, self.observation(), {16}) for a in [action, action]]
        self.assertEqual(len(labels), 2)

    def test_radar_blips_have_positions_but_no_fabricated_unit_identity(self):
        obs = self.observation()
        for x in (4, 5):
            blip = obs.raw_data.units.add(alliance=4, display_type=3, is_blip=True)
            blip.pos.x = x
        data = features(obs)
        self.assertEqual(len(data["blips"]), 2)
        self.assertEqual(data["entity_tags"], [10, 20])
        self.assertNotIn("unit_type", data["blips"][0])
        self.assertNotIn("tag", data["blips"][0])


if __name__ == "__main__":
    unittest.main()
