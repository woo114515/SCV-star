"""Diagnostic probes for the frozen BC cache.

Two questions this answers without training the full policy:

* Is a label learnable from the cached rows at all?  A small classifier fitted on
  aggregate features gives the in-sample ceiling for "data / features are enough".
* Which stage loses the information?  Representations are exported from a trained
  checkpoint (encoder pooled, recurrent state) and probed with a linear and a
  two-layer classifier.

Everything reported is **in-sample** accuracy on a handful of replays.  It is a
diagnostic for fitting capacity, never a generalisation claim.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from torch import nn

from scv_star.data.bc_cache import _cache_path
from scv_star.models.bc import build_bc_model, model_config_matches
from scv_star.training.bc import load_config
from scv_star.training.checkpoint import load_checkpoint

DEFAULT_REPLAYS = (
    "06b3b0e4e33cd3f93cccdf2e18f60ee2f85612b410790552a40506d65c94e9c2",
    "0a167aa509500edfba4f6dfefb9c9ad821feb88b6941e53be5474377a4eeb221",
)


def aggregate_features(entities, entity_mask, scalars):
    """Mask-weighted mean and max over entities, plus the entity count."""
    mask = entity_mask.unsqueeze(-1).to(torch.float32)
    count = mask.sum(1).clamp(min=1)
    mean = (entities * mask).sum(1) / count
    maximum = (entities * mask + (mask - 1) * 1e4).max(1).values
    return torch.cat((scalars, mean, maximum, count), dim=-1)


def subset_sequences(cache_path, replays):
    """Return the cached train sequences belonging to the given replays."""
    cache = _cache_path(cache_path)
    document = json.loads(cache.read_text(encoding="utf-8"))
    wanted = set(replays)
    sequences = []
    for shard in document["splits"]["train"]["files"]:
        payload = torch.load(cache.parent / shard["path"], weights_only=True, map_location="cpu")
        sequences.extend(seq for seq in payload["sequences"] if seq["key"][0] in wanted)
    if not sequences:
        raise ValueError("No sequences for the requested replays")
    return document, sequences


def _fit(x, y, classes, *, hidden=None, steps=1200, learning_rate=0.02, seed=0):
    """Fit a linear or one-hidden-layer classifier and return its train accuracy."""
    x = (x - x.mean(0)) / x.std(0).clamp(min=1e-6)
    torch.manual_seed(seed)
    if hidden:
        model = nn.Sequential(nn.Linear(x.shape[1], hidden), nn.ReLU(), nn.Linear(hidden, classes))
    else:
        model = nn.Linear(x.shape[1], classes)
    model = model.to(x.device)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    for _ in range(steps + 1):
        optimizer.zero_grad()
        logits = model(x)
        loss = nn.functional.cross_entropy(logits, y)
        loss.backward()
        optimizer.step()
    return float((logits.argmax(1) == y).float().mean()), float(loss)


def probe(checkpoint, config_path, cache_path, replays, *, device=None, steps=1200):
    """Report in-sample accuracy of probes on raw features and on model representations."""
    device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
    document, sequences = subset_sequences(cache_path, replays)
    config, _ = load_config(config_path)
    model = build_bc_model(document, config["model"]).to(device)
    state = load_checkpoint(checkpoint)
    if not model_config_matches(state.get("model_config"), model.config):
        raise ValueError("Checkpoint model config does not match the config file")
    model.load_state_dict(state["model"])
    model.eval()

    pooled_rows, state_rows, raw_rows, labels = [], [], [], []
    with torch.no_grad():
        for sequence in sequences:
            entities = sequence["entities"].unsqueeze(0).to(device)
            mask = sequence["entity_mask"].unsqueeze(0).to(device)
            stale = sequence["stale"].unsqueeze(0).to(device)
            scalars = sequence["scalars"].unsqueeze(0).to(device)
            encoded, pooled, scalar_input = model.encode(entities, mask, stale, scalars)
            hidden = model.lstm(torch.cat((pooled, model.scalar_encoder(scalar_input)), dim=-1))[0]
            pooled_rows.append(pooled[0])
            state_rows.append(hidden[0])
            raw_rows.append(
                aggregate_features(
                    sequence["entities"].to(device),
                    sequence["entity_mask"].to(device),
                    sequence["scalars"].to(device),
                )
            )
            labels.append(sequence["ability"].to(device))

    pooled = torch.cat(pooled_rows)
    hidden = torch.cat(state_rows)
    raw = torch.cat(raw_rows)
    target = torch.cat(labels)
    classes = model.ability_count
    majority = float(torch.bincount(target).max()) / len(target)
    report = {
        "rows": int(len(target)),
        "classes": classes,
        "observed_classes": int(target.unique().numel()),
        "majority_baseline": majority,
        "checkpoint_update": int(state["updates"]),
        "probes": {},
    }
    for name, features in (
        ("raw_aggregate", raw),
        ("encoder_pooled", pooled),
        ("recurrent_state", hidden),
    ):
        linear, linear_loss = _fit(features, target, classes, steps=steps, learning_rate=0.05)
        mlp, mlp_loss = _fit(
            features, target, classes, hidden=512, steps=steps, learning_rate=0.003
        )
        report["probes"][name] = {
            "dimensions": int(features.shape[1]),
            "linear_accuracy": linear,
            "linear_loss": linear_loss,
            "mlp_accuracy": mlp,
            "mlp_loss": mlp_loss,
        }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path("data/interim/bc-86383-v1"))
    parser.add_argument("--replays", nargs="+", default=list(DEFAULT_REPLAYS))
    parser.add_argument("--device", default=None)
    parser.add_argument("--steps", type=int, default=1200)
    args = parser.parse_args()
    report = probe(
        args.checkpoint,
        args.config,
        args.cache,
        args.replays,
        device=args.device,
        steps=args.steps,
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
