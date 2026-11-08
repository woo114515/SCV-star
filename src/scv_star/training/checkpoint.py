"""Atomic BC checkpoints with strict state comparison and schema binding."""

from __future__ import annotations

from pathlib import Path

import torch

CHECKPOINT_SCHEMA = "tvt-bc-checkpoint-v1"


def assert_state_equal(left, right, path="state"):
    """Compare all checkpoint fields, including optimizer counters and RNG tensors."""
    if type(left) is not type(right):
        raise AssertionError(f"{path}: types differ")
    if isinstance(left, torch.Tensor):
        if left.dtype != right.dtype or left.shape != right.shape or not torch.equal(left, right):
            raise AssertionError(f"{path}: tensors differ")
    elif isinstance(left, dict):
        if left.keys() != right.keys():
            raise AssertionError(f"{path}: keys differ")
        for key in left:
            assert_state_equal(left[key], right[key], f"{path}.{key}")
    elif isinstance(left, (list, tuple)):
        if len(left) != len(right):
            raise AssertionError(f"{path}: lengths differ")
        for index, (first, second) in enumerate(zip(left, right)):
            assert_state_equal(first, second, f"{path}[{index}]")
    elif left != right:
        raise AssertionError(f"{path}: values differ")


def save_checkpoint(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if state.get("schema") != CHECKPOINT_SCHEMA:
        raise ValueError("Unexpected checkpoint schema")
    temporary = path.with_name(path.name + ".next")
    torch.save(state, temporary)
    temporary.replace(path)
    return path


def load_checkpoint(path, expected_hashes=None):
    state = torch.load(Path(path), weights_only=True, map_location="cpu")
    if not isinstance(state, dict) or state.get("schema") != CHECKPOINT_SCHEMA:
        raise ValueError("Unexpected checkpoint schema")
    if expected_hashes:
        hashes = state.get("hashes", {})
        for name, value in expected_hashes.items():
            if hashes.get(name) != value:
                raise ValueError(f"Checkpoint {name} hash mismatch")
    return state
