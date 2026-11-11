import copy
import unittest

import torch

from scv_star.models.bc import (
    BCModelConfig,
    bc_loss,
    build_bc_model,
    model_config_matches,
)


def make_cache():
    return {
        "normalization": {
            "entity_mean": [0.0] * 19,
            "entity_std": [1.0] * 19,
            "scalar_mean": [0.0] * 9,
            "scalar_std": [1.0] * 9,
        },
        "unit_type_vocab": [18, 19],
        "catalog": {"ability_ids": [10, 20, 30]},
    }


def make_batch(batch=2, steps=4, entities=5):
    torch.manual_seed(7)
    values = torch.zeros(batch, steps, entities, 27)
    values[..., 0] = 18.0
    values[..., 1] = 1.0
    values[..., 2] = 1.0
    values[..., 3:6] = 10.0
    values[..., 7:9] = 100.0
    mask = torch.ones(batch, steps, entities, dtype=torch.bool)
    mask[0, 0, 4] = False
    stale = torch.zeros(batch, steps, entities, dtype=torch.bool)
    stale[0, 0, 3] = True
    scalars = torch.arange(batch * steps * 9, dtype=torch.float32).reshape(batch, steps, 9)
    raw_indices = torch.tensor(
        [
            [[0, 1, -1], [2, -1, -1], [0, 0, 1], [3, 2, 1]],
            [[0, 1, -1], [2, -1, -1], [0, 0, 1], [3, 2, 1]],
        ]
    )
    selected_mask = raw_indices >= 0
    indices = raw_indices.clamp(min=0)
    targets = {
        "ability": torch.randint(0, 3, (batch, steps)),
        "kind": torch.randint(0, 2, (batch, steps)),
        "selected_indices": indices,
        "selected_mask": selected_mask,
        "target_kind": torch.randint(0, 3, (batch, steps)),
        "target_index": torch.randint(0, 4, (batch, steps)),
        "target_point": torch.rand(batch, steps, 2) * 100,
        "queue": torch.randint(0, 2, (batch, steps)),
        "queue_mask": torch.rand(batch, steps) > 0.3,
        "delay": torch.randint(0, 20, (batch, steps)).float(),
        "delay_mask": torch.rand(batch, steps) > 0.3,
        "map_size": torch.full((batch, steps, 2), 128.0),
    }
    return {
        "entities": values,
        "entity_mask": mask,
        "stale": stale,
        "scalars": scalars,
        "targets": targets,
    }


class ModelTests(unittest.TestCase):
    def test_forward_loss_backward_and_inference_shapes(self):
        model = build_bc_model(make_cache(), {})
        batch = make_batch()
        outputs = model(batch)
        self.assertEqual(tuple(outputs["ability"].shape), (2, 4, 3))
        self.assertEqual(tuple(outputs["kind"].shape), (2, 4, 2))
        self.assertEqual(len(outputs["selection"]), 4)
        self.assertEqual(tuple(outputs["selection"][0].shape), (8, 6))
        self.assertEqual(tuple(outputs["target_kind"].shape), (8, 3))
        self.assertEqual(tuple(outputs["target_unit"].shape), (8, 5))
        self.assertEqual(tuple(outputs["target_point"].shape), (8, 2))
        self.assertEqual(tuple(outputs["queue"].shape), (8, 2))
        self.assertEqual(tuple(outputs["delay"].shape), (8,))
        losses = bc_loss(outputs, batch["targets"])
        self.assertTrue(torch.isfinite(losses["total"]))
        for name in ("ability", "kind", "selection", "target_kind", "target_unit"):
            self.assertTrue(torch.isfinite(losses[name]), name)
        self.assertEqual(losses["counts"]["ability"], 8)
        self.assertEqual(losses["counts"]["kind"], 8)
        self.assertEqual(losses["counts"]["target_kind"], 8)
        self.assertEqual(losses["counts"]["selection"], 26)
        losses["total"].backward()
        for parameter in model.parameters():
            if parameter.grad is not None:
                self.assertTrue(torch.isfinite(parameter.grad).all())

    def test_selection_loss_ignores_padded_entity_slots(self):
        # Two batches that differ only in how many masked entity slots they carry
        # must produce the same selection distribution: the pointer scores are
        # masked, so padding cannot dilute the softmax or the loss.
        torch.manual_seed(11)
        model = build_bc_model(make_cache(), {})
        model.eval()
        batch = make_batch()
        padded = copy.deepcopy(batch)
        extra = 3

        def with_extra_slots(values, dtype=None):
            # Entity axis is dim 2 for both the 27-field tensors and the boolean masks.
            pad_shape = values.shape[:2] + (extra,) + values.shape[3:]
            pad = torch.zeros(pad_shape, dtype=dtype or values.dtype)
            return torch.cat((values, pad), dim=2)

        for key in ("entities", "stale", "entity_mask"):
            padded[key] = with_extra_slots(padded[key])
        with torch.no_grad():
            base = model(batch)
            wider = model(padded)
            base_loss = bc_loss(base, batch["targets"])
            wider_loss = bc_loss(wider, padded["targets"])
        self.assertAlmostEqual(
            float(base_loss["selection"]), float(wider_loss["selection"]), places=6
        )
        real = batch["entity_mask"].shape[-1]
        for index, logits in enumerate(base["selection"]):
            # The stop logit sits after the entity columns, which is a different
            # index once the padded batch carries extra slots.
            torch.testing.assert_close(
                logits[:, :real], wider["selection"][index][:, :real], rtol=0, atol=1e-4
            )
            # The encoder may sum masked slots in a different order, so allow
            # float noise on individual logits; the loss check above is strict.
            torch.testing.assert_close(
                logits[:, real:], wider["selection"][index][:, -1:], rtol=0, atol=1e-4
            )

    def test_inference_returns_masked_actions(self):
        model = build_bc_model(make_cache(), {})
        model.eval()
        batch = make_batch()
        with torch.no_grad():
            actions = model.act(batch)
        self.assertEqual(tuple(actions["ability"].shape), (2, 4))
        self.assertEqual(tuple(actions["kind"].shape), (2, 4))
        self.assertEqual(tuple(actions["selected_indices"].shape), (2, 4, 128))
        self.assertEqual(tuple(actions["selected_mask"].shape), (2, 4, 128))
        self.assertTrue((actions["ability"] < 3).all())
        self.assertTrue((actions["kind"] < 2).all())
        self.assertTrue((actions["target_kind"] < 3).all())
        self.assertTrue((actions["target_unit"] < 5).all())
        self.assertTrue((actions["delay"] >= 0).all())

    def test_act_uses_the_wide_ability_head(self):
        model = build_bc_model(make_cache(), {"ability_head_hidden": 64})
        model.eval()
        with torch.no_grad():
            actions = model.act(make_batch())
        self.assertEqual(tuple(actions["ability"].shape), (2, 4))
        self.assertTrue((actions["ability"] < 3).all())
        self.assertEqual(tuple(actions["target_unit"].shape), (2, 4))
        self.assertTrue((actions["target_point"] >= 0).all())
        self.assertTrue((actions["target_point"] <= 1).all())

    def test_all_inactive_heads_contribute_zero(self):
        model = build_bc_model(make_cache(), {})
        batch = make_batch()
        batch["targets"]["selected_mask"] = torch.zeros_like(batch["targets"]["selected_mask"])
        batch["targets"]["selected_indices"] = torch.zeros_like(
            batch["targets"]["selected_indices"]
        )
        batch["targets"]["target_kind"] = torch.zeros_like(batch["targets"]["target_kind"])
        batch["targets"]["queue_mask"] = torch.zeros_like(batch["targets"]["queue_mask"])
        batch["targets"]["delay_mask"] = torch.zeros_like(batch["targets"]["delay_mask"])
        outputs = model(batch)
        losses = bc_loss(outputs, batch["targets"])
        self.assertEqual(losses["counts"]["target_unit"], 0)
        self.assertEqual(losses["counts"]["target_point"], 0)
        self.assertEqual(losses["counts"]["queue"], 0)
        self.assertEqual(losses["counts"]["delay"], 0)
        self.assertEqual(float(losses["target_unit"].detach()), 0.0)
        self.assertEqual(float(losses["target_point"].detach()), 0.0)
        self.assertEqual(float(losses["queue"].detach()), 0.0)
        self.assertEqual(float(losses["delay"].detach()), 0.0)
        self.assertEqual(losses["counts"]["selection"], 8)
        self.assertTrue(torch.isfinite(losses["total"]))

    def test_wide_ability_head_uses_encoder_skip(self):
        plain = build_bc_model(make_cache(), {})
        wide = build_bc_model(make_cache(), {"ability_head_hidden": 64})
        state = torch.zeros(2, 256)
        pooled = torch.zeros(2, 128)
        self.assertEqual(plain._ability_input(state, pooled).shape[-1], 256)
        self.assertEqual(wide._ability_input(state, pooled).shape[-1], 384)
        outputs = wide(make_batch())
        self.assertEqual(outputs["ability"].shape, (2, 4, 3))

    def test_old_checkpoint_config_still_matches(self):
        old = {
            "entity_width": 128,
            "entity_layers": 2,
            "entity_heads": 4,
            "entity_ff": 512,
            "dropout": 0.1,
            "scalar_width": 128,
            "lstm_hidden": 256,
            "lstm_layers": 2,
            "selection_hidden": 128,
            "max_selection_steps": 128,
        }
        self.assertTrue(model_config_matches(old, BCModelConfig(**old)))
        wide = BCModelConfig(**dict(old, ability_head_hidden=64))
        self.assertFalse(model_config_matches(old, wide))
        self.assertFalse(model_config_matches(None, BCModelConfig(**old)))
        self.assertFalse(model_config_matches(dict(old, bogus=1), BCModelConfig(**old)))

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            build_bc_model({**make_cache(), "catalog": {"ability_ids": []}}, {})
        bad = copy.deepcopy(make_cache())
        bad["normalization"]["entity_mean"] = [0.0]
        with self.assertRaises(ValueError):
            build_bc_model(bad, {})


if __name__ == "__main__":
    unittest.main()
