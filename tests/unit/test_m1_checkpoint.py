"""Ensure the resume comparison cannot silently accept incomplete state."""

import importlib.util
import unittest

from scv_star.runtime.m1 import assert_state_equal


@unittest.skipUnless(importlib.util.find_spec("torch"), "M1 PyTorch dependency is not installed")
class CheckpointComparison(unittest.TestCase):
    def test_changed_optimizer_counter_is_rejected(self):
        import torch

        with self.assertRaisesRegex(AssertionError, "optimizer.step"):
            assert_state_equal(
                {"optimizer": {"step": torch.tensor(1)}},
                {"optimizer": {"step": torch.tensor(2)}},
            )

    def test_missing_rng_state_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, "keys differ"):
            assert_state_equal({"model": {}, "cuda_rng": []}, {"model": {}})

    def test_equal_values_with_changed_dtype_are_rejected(self):
        import torch

        with self.assertRaisesRegex(AssertionError, "tensors differ"):
            assert_state_equal(
                torch.ones(2, dtype=torch.float32), torch.ones(2, dtype=torch.float64)
            )


if __name__ == "__main__":
    unittest.main()
