"""Resumable behaviour-cloning trainer for the frozen 86383 tensor cache."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import time
from collections import Counter
from pathlib import Path

import torch
from torch import nn

from scv_star.data.bc_apm import QUALITY_LABELS, load_apm_labels
from scv_star.data.bc_cache import load_cache
from scv_star.data.bc_catalog import sha256_file
from scv_star.models.bc import (
    HEAD_NAMES,
    bc_loss,
    build_bc_model,
    model_config_dict,
)
from scv_star.training.checkpoint import CHECKPOINT_SCHEMA, load_checkpoint, save_checkpoint
from scv_star.training.metrics import MetricsAccumulator

ROOT = Path(__file__).resolve().parents[3]
RUN_SCHEMA = "tvt-bc-run-v1"
REQUIRED_KEYS = (
    "build",
    "collection",
    "collection_sha256",
    "catalog_digest",
    "cache",
    "seed",
    "batch_size",
    "sequence_length",
    "training_budget_seconds",
    "validation_interval",
    "checkpoint_interval_seconds",
    "learning_rate",
    "weight_decay",
    "grad_clip",
    "precision",
    "deterministic",
    "model",
    "head_weights",
)
SOURCE_FILES = (
    "src/scv_star/data/bc_apm.py",
    "src/scv_star/data/bc_catalog.py",
    "src/scv_star/data/bc_cache.py",
    "src/scv_star/data/bc_features.py",
    "src/scv_star/models/bc.py",
    "src/scv_star/training/bc.py",
    "src/scv_star/training/checkpoint.py",
    "src/scv_star/training/metrics.py",
)


def _resolve(path):
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def _digest(path):
    return sha256_file(Path(path))


def load_config(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema_version") != 1 or config.get("kind") != "behavior_cloning":
        raise ValueError("Expected a behavior_cloning config with schema_version 1")
    missing = [key for key in REQUIRED_KEYS if key not in config]
    if missing:
        raise ValueError(f"Missing config fields: {missing}")
    if config["precision"] != "fp32":
        raise ValueError("Only fp32 training is supported in M4 v1")
    if not config["deterministic"]:
        raise ValueError("M4 v1 requires deterministic training")
    if int(config["batch_size"]) < 1 or int(config["sequence_length"]) < 1:
        raise ValueError("Invalid batch or sequence size")
    if int(config["training_budget_seconds"]) < 1:
        raise ValueError("Training budget must be positive")
    if int(config["validation_interval"]) < 1 or int(config["checkpoint_interval_seconds"]) < 1:
        raise ValueError("Validation and checkpoint intervals must be positive")
    weights = config["head_weights"]
    if set(weights) != set(HEAD_NAMES) or any(float(value) < 0 for value in weights.values()):
        raise ValueError("head_weights must cover every head with nonnegative values")
    config["replays"] = [str(value) for value in config.get("replays", [])]
    config["max_sequences"] = config.get("max_sequences")
    config["cache_sha256"] = config.get("cache_sha256")
    config["apm_manifest"] = config.get("apm_manifest")
    config["apm_manifest_sha256"] = config.get("apm_manifest_sha256")
    config["sampling_weights"] = config.get("sampling_weights")
    if config["sampling_weights"] is not None:
        sampling_weights = config["sampling_weights"]
        if set(sampling_weights) - set(QUALITY_LABELS):
            raise ValueError("Unknown quality label in sampling_weights")
        if any(float(value) <= 0 for value in sampling_weights.values()):
            raise ValueError("sampling_weights must be positive")
        if config["apm_manifest"] is None:
            raise ValueError("sampling_weights require apm_manifest")
    return config, path


def verify_cache(config, cache, cache_path):
    if int(cache.get("build", -1)) != int(config["build"]):
        raise ValueError("Cache build mismatch")
    if cache.get("collection_sha256") != config["collection_sha256"]:
        raise ValueError("Cache collection hash mismatch")
    if cache.get("catalog_digest") != config["catalog_digest"]:
        raise ValueError("Cache catalog hash mismatch")
    if int(cache.get("sequence_length", -1)) != int(config["sequence_length"]):
        raise ValueError("Cache sequence length mismatch")
    if _digest(_resolve(config["collection"])) != config["collection_sha256"]:
        raise ValueError("Frozen collection changed")
    if "train" not in cache["splits"]:
        raise ValueError("Cache has no train split")
    cache_sha = _digest(Path(cache_path) / "cache.json")
    if config["cache_sha256"] and config["cache_sha256"] != cache_sha:
        raise ValueError("Configured cache hash mismatch")
    if config.get("apm_manifest"):
        apm_doc, _, apm_path = load_apm_labels(_resolve(config["apm_manifest"]))
        if apm_doc.get("collection_sha256") != config["collection_sha256"]:
            raise ValueError("APM manifest collection mismatch")
        apm_sha = _digest(apm_path)
        if cache.get("apm_manifest_sha256") != apm_sha:
            raise ValueError("Cache APM manifest hash mismatch")
        if config.get("apm_manifest_sha256") and config["apm_manifest_sha256"] != apm_sha:
            raise ValueError("Configured APM manifest hash mismatch")
    elif config.get("sampling_weights"):
        raise ValueError("sampling_weights require apm_manifest")
    return cache_sha


def set_determinism(seed):
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    torch.manual_seed(int(seed))
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(int(seed))
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False


def _device(name):
    if name:
        return torch.device(name)
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def collate_sequences(sequences, device):
    if not sequences:
        raise ValueError("Empty batch")
    batch = len(sequences)
    steps = max(int(sequence["length"]) for sequence in sequences)
    entities = max(int(sequence["entities"].shape[1]) for sequence in sequences)
    selected = max(int(sequence["selected_indices"].shape[1]) for sequence in sequences)
    fields = {
        "entities": torch.zeros(batch, steps, entities, 27),
        "entity_mask": torch.zeros(batch, steps, entities, dtype=torch.bool),
        "stale": torch.zeros(batch, steps, entities, dtype=torch.bool),
        "scalars": torch.zeros(batch, steps, 9),
        "ability": torch.zeros(batch, steps, dtype=torch.long),
        "kind": torch.zeros(batch, steps, dtype=torch.long),
        "selected_indices": torch.zeros(batch, steps, selected, dtype=torch.long),
        "selected_mask": torch.zeros(batch, steps, selected, dtype=torch.bool),
        "target_kind": torch.zeros(batch, steps, dtype=torch.long),
        "target_index": torch.zeros(batch, steps, dtype=torch.long),
        "target_point": torch.zeros(batch, steps, 2),
        "queue": torch.zeros(batch, steps, dtype=torch.long),
        "queue_mask": torch.zeros(batch, steps, dtype=torch.bool),
        "delay": torch.zeros(batch, steps),
        "delay_mask": torch.zeros(batch, steps, dtype=torch.bool),
        "map_size": torch.zeros(batch, steps, 2),
    }
    for index, sequence in enumerate(sequences):
        length = int(sequence["length"])
        count = int(sequence["entities"].shape[1])
        width = int(sequence["selected_indices"].shape[1])
        fields["entities"][index, :length, :count] = sequence["entities"]
        fields["entity_mask"][index, :length, :count] = sequence["entity_mask"]
        fields["stale"][index, :length, :count] = sequence["stale"]
        fields["scalars"][index, :length] = sequence["scalars"]
        fields["ability"][index, :length] = sequence["ability"]
        fields["kind"][index, :length] = sequence["kind"]
        fields["selected_indices"][index, :length, :width] = sequence["selected_indices"]
        fields["selected_mask"][index, :length, :width] = sequence["selected_mask"]
        fields["target_kind"][index, :length] = sequence["target_kind"]
        fields["target_index"][index, :length] = sequence["target_index"]
        fields["target_point"][index, :length] = sequence["target_point"]
        fields["queue"][index, :length] = sequence["queue"]
        fields["queue_mask"][index, :length] = sequence["queue_mask"]
        fields["delay"][index, :length] = sequence["delay"]
        fields["delay_mask"][index, :length] = sequence["delay_mask"]
        fields["map_size"][index, :length] = torch.tensor(sequence["map_size"])
    targets = {
        key: fields.pop(key)
        for key in (
            "ability",
            "kind",
            "selected_indices",
            "selected_mask",
            "target_kind",
            "target_index",
            "target_point",
            "queue",
            "queue_mask",
            "delay",
            "delay_mask",
            "map_size",
        )
    }
    for key in fields:
        fields[key] = fields[key].to(device)
    for key in targets:
        targets[key] = targets[key].to(device)
    fields["targets"] = targets
    return fields


class SequenceSampler:
    """Seeded epoch permutation with an exact epoch/index data cursor."""

    def __init__(self, sequences, seed, batch_size, weights=None):
        self.sequences = list(sequences)
        self.seed = int(seed)
        self.batch_size = int(batch_size)
        if weights is not None:
            if len(weights) != len(self.sequences):
                raise ValueError("Sampler weights length mismatch")
            weights = [float(weight) for weight in weights]
            if any(weight <= 0 for weight in weights):
                raise ValueError("Sampler weights must be positive")
        self.weights = weights
        self.epoch = 0
        self.index = 0
        self._permutation = None

    def _permute(self):
        generator = torch.Generator().manual_seed(self.seed + self.epoch)
        if self.weights is None:
            return torch.randperm(len(self.sequences), generator=generator).tolist()
        return torch.multinomial(
            torch.tensor(self.weights, dtype=torch.float64),
            num_samples=len(self.sequences),
            replacement=True,
            generator=generator,
        ).tolist()

    def next_batch(self):
        if self._permutation is None:
            self._permutation = self._permute()
        indices = self._permutation[self.index : self.index + self.batch_size]
        if not indices:
            self.epoch += 1
            self._permutation = self._permute()
            self.index = 0
            indices = self._permutation[: self.batch_size]
        self.index += len(indices)
        return [self.sequences[index] for index in indices]

    def state(self):
        return {"epoch": self.epoch, "index": self.index, "seed": self.seed}

    def load_state(self, state):
        if int(state["seed"]) != self.seed:
            raise ValueError("Sampler seed mismatch")
        self.epoch = int(state["epoch"])
        self.index = int(state["index"])
        self._permutation = None


def _most_frequent_ability(sequences, ability_count):
    counts = torch.zeros(int(ability_count), dtype=torch.long)
    for sequence in sequences:
        counts += torch.bincount(sequence["ability"], minlength=int(ability_count))
    return int(counts.argmax())


def evaluate_sequences(model, sequences, batch_size, device, weights, train_ability=None):
    model.eval()
    metrics = MetricsAccumulator()
    losses = {name: 0.0 for name in (*HEAD_NAMES, "total")}
    batches = 0
    with torch.no_grad():
        for start in range(0, len(sequences), batch_size):
            batch = collate_sequences(sequences[start : start + batch_size], device)
            outputs = model(batch)
            result = bc_loss(outputs, batch["targets"], weights)
            if not torch.isfinite(result["total"]):
                raise ValueError("Non-finite evaluation loss")
            metrics.update(outputs, batch["targets"])
            for name in (*HEAD_NAMES, "total"):
                losses[name] += float(result[name].detach())
            batches += 1
    result = metrics.compute()
    result["loss"] = {name: value / max(batches, 1) for name, value in losses.items()}
    if train_ability is not None:
        entry = result["ability"]["per_ability"].get(str(train_ability), {"target": 0})
        result["ability"]["most_frequent_baseline"] = entry["target"] / max(
            result["ability"]["rows"], 1
        )
    return result


def _quality_breakdown(model, sequences, batch_size, device, weights, train_ability):
    breakdown = {}
    for label in QUALITY_LABELS:
        subset = [sequence for sequence in sequences if sequence.get("quality_label") == label]
        if subset:
            breakdown[label] = evaluate_sequences(
                model, subset, batch_size, device, weights, train_ability
            )
    return breakdown


def _state_hashes(config, config_path, cache_path):
    return {
        "collection": config["collection_sha256"],
        "catalog": config["catalog_digest"],
        "cache": _digest(Path(cache_path) / "cache.json"),
        "config": _digest(config_path),
    }


def _environment(config, config_path, cache_path):
    device = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "torch": str(torch.__version__),
        "cuda_runtime": str(torch.version.cuda) if torch.version.cuda else None,
        "cudnn": torch.backends.cudnn.version(),
        "cuda_available": torch.cuda.is_available(),
        "gpu": device,
        "compute_capability": list(torch.cuda.get_device_capability(0))
        if torch.cuda.is_available()
        else None,
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "git_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
        ),
        "dependency_lock_sha256": _digest(ROOT / "requirements/bc-linux-py311-cu130.txt"),
        "config_sha256": _digest(config_path),
        "cache_sha256": _digest(Path(cache_path) / "cache.json"),
        "collection_sha256": config["collection_sha256"],
        "catalog_digest": config["catalog_digest"],
        "source_sha256": {relative: _digest(ROOT / relative) for relative in SOURCE_FILES},
    }


def _checkpoint_state(
    model,
    optimizer,
    sampler,
    updates,
    training_seconds,
    best_loss,
    best_update,
    hashes,
    environment,
):
    return {
        "schema": CHECKPOINT_SCHEMA,
        "model_config": model_config_dict(model.config),
        "model": model.state_dict(),
        "optimizer": optimizer.state_dict(),
        "scaler": None,
        "rng": {
            "cpu": torch.get_rng_state(),
            "cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
        },
        "sampler": sampler.state(),
        "updates": int(updates),
        "training_seconds": float(training_seconds),
        "best_loss": float(best_loss),
        "best_update": int(best_update),
        "hashes": hashes,
        "environment": environment,
    }


def run_training(
    config,
    config_path,
    output,
    *,
    resume=None,
    max_updates=None,
    device=None,
    replays=None,
    max_sequences=None,
    train_metrics=False,
):
    output = Path(output).absolute()
    if not resume and output.exists():
        raise ValueError(f"Output already exists: {output}")
    cache_path = _resolve(config["cache"])
    cache, data = load_cache(cache_path)
    cache_sha = verify_cache(config, cache, cache_path)
    train_sequences = list(data["train"])
    validation_sequences = list(data.get("validation", []))
    replays = config.get("replays", []) if replays is None else replays
    max_sequences = config.get("max_sequences") if max_sequences is None else max_sequences
    if replays:
        allowed = set(replays)
        train_sequences = [
            sequence for sequence in train_sequences if sequence["key"][0] in allowed
        ]
        if not train_sequences:
            raise ValueError("No train sequences for the requested replays")
    if max_sequences is not None:
        train_sequences = train_sequences[: int(max_sequences)]
    if not train_sequences:
        raise ValueError("No train sequences selected")
    chosen_device = _device(device)
    set_determinism(config["seed"])
    model = build_bc_model(cache, config["model"]).to(chosen_device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(config["learning_rate"]),
        weight_decay=float(config["weight_decay"]),
        foreach=False,
    )
    sampling_weights = config.get("sampling_weights")
    sequence_weights = None
    if sampling_weights:
        if any(sequence.get("quality_label") is None for sequence in train_sequences):
            raise ValueError("sampling_weights require APM labels on every train sequence")
        sequence_weights = [
            float(sampling_weights.get(sequence["quality_label"], 1.0))
            for sequence in train_sequences
        ]
    sampler = SequenceSampler(
        train_sequences, config["seed"], config["batch_size"], weights=sequence_weights
    )
    weights = {name: float(config["head_weights"][name]) for name in HEAD_NAMES}
    hashes = _state_hashes(config, config_path, cache_path)
    environment = _environment(config, config_path, cache_path)
    updates = 0
    training_seconds = 0.0
    best_loss = float("inf")
    best_update = 0
    best_metrics = None
    if resume:
        state = load_checkpoint(resume, expected_hashes=hashes)
        if state.get("model_config") != model_config_dict(model.config):
            raise ValueError("Checkpoint model config mismatch")
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        torch.set_rng_state(state["rng"]["cpu"])
        if torch.cuda.is_available() and state["rng"]["cuda"]:
            torch.cuda.set_rng_state_all(state["rng"]["cuda"])
        sampler.load_state(state["sampler"])
        updates = int(state["updates"])
        training_seconds = float(state["training_seconds"])
        best_loss = float(state["best_loss"])
        best_update = int(state["best_update"])
        best_metrics = state.get("best_metrics")
    output.mkdir(parents=True, exist_ok=True)
    (output / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    (output / "environment.json").write_text(json.dumps(environment, indent=2), encoding="utf-8")
    metrics_stream = (output / "metrics.jsonl").open("a", encoding="utf-8")
    validation_stream = (output / "validation.jsonl").open("a", encoding="utf-8")
    checkpoints = output / "checkpoints"
    train_ability = _most_frequent_ability(train_sequences, len(cache["catalog"]["ability_ids"]))
    started = time.monotonic()
    last_checkpoint = time.monotonic()
    peak_cuda = 0
    sampled_quality = Counter()
    try:
        model.train()
        while training_seconds < float(config["training_budget_seconds"]):
            if max_updates is not None and updates >= int(max_updates):
                break
            update_started = time.monotonic()
            sequences = sampler.next_batch()
            sampled_quality.update(sequence.get("quality_label") for sequence in sequences)
            batch = collate_sequences(sequences, chosen_device)
            outputs = model(batch)
            losses = bc_loss(outputs, batch["targets"], weights)
            if not torch.isfinite(losses["total"]):
                raise ValueError(f"Non-finite training loss at update {updates + 1}")
            optimizer.zero_grad(set_to_none=True)
            losses["total"].backward()
            grad_norm = nn.utils.clip_grad_norm_(model.parameters(), float(config["grad_clip"]))
            optimizer.step()
            if chosen_device.type == "cuda":
                torch.cuda.synchronize()
            elapsed = time.monotonic() - update_started
            training_seconds += elapsed
            updates += 1
            samples = sum(int(sequence["length"]) for sequence in sequences)
            if chosen_device.type == "cuda":
                peak_cuda = max(peak_cuda, torch.cuda.max_memory_allocated())
            record = {
                "update": updates,
                "training_seconds": training_seconds,
                "batch_seconds": elapsed,
                "samples": samples,
                "samples_per_second": samples / max(elapsed, 1e-9),
                "learning_rate": float(optimizer.param_groups[0]["lr"]),
                "grad_norm": float(grad_norm),
                "loss": {name: float(losses[name].detach()) for name in (*HEAD_NAMES, "total")},
                "counts": losses["counts"],
                "peak_cuda_bytes": peak_cuda,
            }
            metrics_stream.write(json.dumps(record) + "\n")
            metrics_stream.flush()
            if updates % int(config["validation_interval"]) == 0 and validation_sequences:
                validation = evaluate_sequences(
                    model,
                    validation_sequences,
                    int(config["batch_size"]),
                    chosen_device,
                    weights,
                    train_ability,
                )
                validation["by_quality"] = _quality_breakdown(
                    model,
                    validation_sequences,
                    int(config["batch_size"]),
                    chosen_device,
                    weights,
                    train_ability,
                )
                validation["update"] = updates
                validation_stream.write(json.dumps(validation) + "\n")
                validation_stream.flush()
                if validation["loss"]["total"] < best_loss:
                    best_loss = float(validation["loss"]["total"])
                    best_update = updates
                    best_metrics = validation
                    state = _checkpoint_state(
                        model,
                        optimizer,
                        sampler,
                        updates,
                        training_seconds,
                        best_loss,
                        best_update,
                        hashes,
                        environment,
                    )
                    state["best_metrics"] = best_metrics
                    save_checkpoint(checkpoints / "best.pt", state)
                model.train()
            if time.monotonic() - last_checkpoint >= float(config["checkpoint_interval_seconds"]):
                state = _checkpoint_state(
                    model,
                    optimizer,
                    sampler,
                    updates,
                    training_seconds,
                    best_loss,
                    best_update,
                    hashes,
                    environment,
                )
                state["best_metrics"] = best_metrics
                save_checkpoint(checkpoints / "last.pt", state)
                last_checkpoint = time.monotonic()
        final_metrics = (
            evaluate_sequences(
                model,
                validation_sequences,
                int(config["batch_size"]),
                chosen_device,
                weights,
                train_ability,
            )
            if validation_sequences
            else None
        )
        if final_metrics is not None:
            final_metrics["by_quality"] = _quality_breakdown(
                model,
                validation_sequences,
                int(config["batch_size"]),
                chosen_device,
                weights,
                train_ability,
            )
        if final_metrics is not None and final_metrics["loss"]["total"] < best_loss:
            best_loss = float(final_metrics["loss"]["total"])
            best_update = updates
            best_metrics = final_metrics
        train_subset_metrics = (
            evaluate_sequences(
                model,
                train_sequences,
                int(config["batch_size"]),
                chosen_device,
                weights,
                train_ability,
            )
            if train_metrics
            else None
        )
        if train_subset_metrics is not None:
            train_subset_metrics["by_quality"] = _quality_breakdown(
                model,
                train_sequences,
                int(config["batch_size"]),
                chosen_device,
                weights,
                train_ability,
            )
        state = _checkpoint_state(
            model,
            optimizer,
            sampler,
            updates,
            training_seconds,
            best_loss,
            best_update,
            hashes,
            environment,
        )
        state["best_metrics"] = best_metrics
        save_checkpoint(checkpoints / "last.pt", state)
        if best_metrics is not None:
            if not (checkpoints / "best.pt").exists():
                save_checkpoint(checkpoints / "best.pt", state)
        report = {
            "schema": RUN_SCHEMA,
            "kind": "behavior_cloning",
            "experiment_id": config.get("experiment_id"),
            "status": "completed",
            "config": config,
            "config_sha256": hashes["config"],
            "hashes": hashes,
            "cache_sha256": cache_sha,
            "environment": environment,
            "updates": updates,
            "training_seconds": training_seconds,
            "wall_seconds": time.monotonic() - started,
            "train_sequences": len(train_sequences),
            "validation_sequences": len(validation_sequences),
            "train_most_frequent_ability": train_ability,
            "best": {
                "update": best_update,
                "loss": best_loss,
                "metrics": best_metrics,
                "checkpoint": "checkpoints/best.pt" if (checkpoints / "best.pt").exists() else None,
            },
            "final": final_metrics,
            "train_metrics": train_subset_metrics,
            "quality": {
                "cache": cache.get("quality_summary"),
                "sampling_weights": sampling_weights,
                "sampled_by_quality": {
                    label: count for label, count in sampled_quality.items() if label is not None
                },
            },
            "peak_cuda_bytes": peak_cuda,
            "limitations": [
                "single build 86383",
                "entity + scalar inputs only; orders/addons/buffs/upgrades/spatial layers deferred",
                "LSTM state resets every 32-step sequence chunk",
                "no M3 online legality or full-game execution",
                "APM sampling weights use per-view APM as a user-selected quality proxy",
            ],
        }
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    finally:
        metrics_stream.close()
        validation_stream.close()


def run_evaluation(config, config_path, output, checkpoint, test_cache, *, device=None):
    output = Path(output).absolute()
    if output.exists():
        raise ValueError(f"Output already exists: {output}")
    cache_path = _resolve(config["cache"])
    cache, data = load_cache(cache_path)
    verify_cache(config, cache, cache_path)
    test_path = Path(test_cache).absolute()
    test_document, test_data = load_cache(test_path)
    if "test" not in test_data:
        raise ValueError("Test cache has no test split")
    if test_document.get("collection_sha256") != config["collection_sha256"]:
        raise ValueError("Test cache collection mismatch")
    if test_document.get("catalog_digest") != config["catalog_digest"]:
        raise ValueError("Test cache catalog mismatch")
    if test_document.get("normalization") != cache["normalization"]:
        raise ValueError("Test cache normalization differs from the training cache")
    if config.get("apm_manifest") and test_document.get("apm_manifest_sha256") != cache.get(
        "apm_manifest_sha256"
    ):
        raise ValueError("Test cache APM labels differ from the training cache")
    chosen_device = _device(device)
    set_determinism(config["seed"])
    model = build_bc_model(cache, config["model"]).to(chosen_device)
    hashes = _state_hashes(config, config_path, cache_path)
    state = load_checkpoint(checkpoint, expected_hashes=hashes)
    if state.get("model_config") != model_config_dict(model.config):
        raise ValueError("Checkpoint model config mismatch")
    model.load_state_dict(state["model"])
    weights = {name: float(config["head_weights"][name]) for name in HEAD_NAMES}
    train_ability = _most_frequent_ability(data["train"], len(cache["catalog"]["ability_ids"]))
    metrics = evaluate_sequences(
        model,
        test_data["test"],
        int(config["batch_size"]),
        chosen_device,
        weights,
        train_ability,
    )
    metrics["by_quality"] = _quality_breakdown(
        model,
        test_data["test"],
        int(config["batch_size"]),
        chosen_device,
        weights,
        train_ability,
    )
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema": RUN_SCHEMA,
        "kind": "behavior_cloning_evaluation",
        "experiment_id": config.get("experiment_id"),
        "status": "completed",
        "config": config,
        "config_sha256": hashes["config"],
        "hashes": hashes,
        "environment": _environment(config, config_path, cache_path),
        "checkpoint": str(checkpoint),
        "checkpoint_update": int(state["updates"]),
        "test_cache": str(test_path),
        "test_sequences": len(test_data["test"]),
        "train_most_frequent_ability": train_ability,
        "metrics": metrics,
        "quality": {
            "test": test_document.get("quality_summary"),
            "sampling_weights": config.get("sampling_weights"),
        },
        "limitations": [
            "single final test evaluation; no test-set tuning",
            "APM test breakdown uses the cached APM labels",
            "single build 86383; deferred features as in training",
        ],
    }
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--max-updates", type=int)
    parser.add_argument("--device", default=None)
    parser.add_argument("--replays", nargs="+")
    parser.add_argument("--max-sequences", type=int)
    parser.add_argument("--train-metrics", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--test-cache", type=Path)
    args = parser.parse_args()
    config, config_path = load_config(args.config)
    if args.evaluate:
        if args.checkpoint is None or args.test_cache is None:
            raise SystemExit("--evaluate requires --checkpoint and --test-cache")
        report = run_evaluation(
            config,
            config_path,
            args.output,
            args.checkpoint,
            args.test_cache,
            device=args.device,
        )
    else:
        report = run_training(
            config,
            config_path,
            args.output,
            resume=args.resume,
            max_updates=args.max_updates,
            device=args.device,
            replays=args.replays,
            max_sequences=args.max_sequences,
            train_metrics=args.train_metrics,
        )
    print(
        json.dumps(
            {
                key: report.get(key)
                for key in ("status", "updates", "training_seconds", "best", "metrics")
                if key in report
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
