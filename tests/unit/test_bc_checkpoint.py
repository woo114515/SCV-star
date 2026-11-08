import copy
import tempfile
import unittest
from pathlib import Path

import torch

from scv_star.training.checkpoint import (
    CHECKPOINT_SCHEMA,
    assert_state_equal,
    load_checkpoint,
    save_checkpoint,
)


def make_state():
    return {
        "schema": CHECKPOINT_SCHEMA,
        "step": 7,
        "model": {"weight": torch.arange(6, dtype=torch.float32).reshape(2, 3)},
        "optimizer": {"step": 7, "exp_avg": torch.ones(2, 3)},
        "rng": {"cpu": torch.arange(4, dtype=torch.uint8), "cuda": []},
        "sampler": {"epoch": 1, "index": 4},
        "hashes": {"collection": "a" * 64, "cache": "b" * 64},
    }


class CheckpointTests(unittest.TestCase):
    def test_roundtrip_and_state_equality(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "checkpoints" / "last.pt"
            state = make_state()
            save_checkpoint(path, state)
            loaded = load_checkpoint(path, {"collection": "a" * 64})
            assert_state_equal(loaded, state)
            loaded["model"]["weight"].add_(1)
            with self.assertRaises(AssertionError):
                assert_state_equal(loaded, state)

    def test_schema_and_expected_hash_are_enforced(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "last.pt"
            state = make_state()
            save_checkpoint(path, state)
            with self.assertRaisesRegex(ValueError, "cache hash"):
                load_checkpoint(path, {"cache": "c" * 64})
            broken = copy.deepcopy(state)
            broken["schema"] = "other"
            with self.assertRaises(ValueError):
                save_checkpoint(Path(temporary) / "broken.pt", broken)
            torch.save(broken, Path(temporary) / "raw.pt")
            with self.assertRaisesRegex(ValueError, "schema"):
                load_checkpoint(Path(temporary) / "raw.pt")

    def test_dtype_and_type_changes_are_rejected(self):
        left = make_state()
        right = copy.deepcopy(left)
        right["model"]["weight"] = right["model"]["weight"].double()
        with self.assertRaises(AssertionError):
            assert_state_equal(left, right)
        right = copy.deepcopy(left)
        right["sampler"]["index"] = True
        with self.assertRaises(AssertionError):
            assert_state_equal(left, right)


if __name__ == "__main__":
    unittest.main()
