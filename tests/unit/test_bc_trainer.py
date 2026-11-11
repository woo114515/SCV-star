import json
import tempfile
import torch
import unittest
from pathlib import Path

from scv_star.data.bc_cache import build_cache
from scv_star.models.bc import HEAD_NAMES, build_bc_model
from scv_star.training.bc import (
    evaluate_sequences,
    learning_rate_for,
    load_config,
    parse_lr_schedule,
    run_training,
)
from scv_star.training.checkpoint import assert_state_equal, load_checkpoint

try:
    from tests.unit.test_bc_cache import CacheFixture, digest, write_apm
    from tests.unit.test_bc_model import make_cache
except ImportError:  # unittest discover puts tests/unit on sys.path
    from test_bc_cache import CacheFixture, digest, write_apm
    from test_bc_model import make_cache


def make_config(fixture, cache_document, cache_dir, root):
    return {
        "schema_version": 1,
        "status": "planned",
        "kind": "behavior_cloning",
        "experiment_id": "bc-trainer-test",
        "build": 86383,
        "collection": str(fixture.collection),
        "collection_sha256": cache_document["collection_sha256"],
        "catalog_digest": cache_document["catalog_digest"],
        "cache": str(cache_dir),
        "cache_sha256": None,
        "seed": 3,
        "batch_size": 2,
        "sequence_length": 32,
        "training_budget_seconds": 60,
        "validation_interval": 1000,
        "checkpoint_interval_seconds": 1000,
        "learning_rate": 0.001,
        "weight_decay": 0.0,
        "grad_clip": 1.0,
        "precision": "fp32",
        "deterministic": True,
        "replays": [],
        "max_sequences": None,
        "model": {
            "entity_width": 32,
            "entity_layers": 1,
            "entity_heads": 2,
            "entity_ff": 64,
            "dropout": 0.0,
            "scalar_width": 32,
            "lstm_hidden": 64,
            "lstm_layers": 1,
            "selection_hidden": 32,
            "max_selection_steps": 16,
        },
        "head_weights": {
            "ability": 1.0,
            "kind": 0.5,
            "selection": 1.0,
            "target_kind": 0.25,
            "target_unit": 0.5,
            "target_point": 0.5,
            "queue": 0.25,
            "delay": 0.25,
        },
    }


def make_sequences(counts, length=4, entities=5):
    """Synthetic cache-style sequences whose selection lengths differ per row."""
    sequences = []
    for index, count in enumerate(counts):
        values = torch.zeros(length, entities, 27)
        values[..., 0] = 18.0
        values[..., 1] = 1.0
        values[..., 2] = 1.0
        values[..., 3:6] = 10.0
        values[..., 7:9] = 100.0
        selected = torch.zeros(length, count, dtype=torch.long)
        selected_mask = torch.zeros(length, count, dtype=torch.bool)
        for step in range(length):
            selected[step, :count] = torch.arange(count) % entities
            selected_mask[step, :] = True
        sequences.append(
            {
                "key": ("replay", 0, f"seq{index}"),
                "length": length,
                "entities": values,
                "entity_mask": torch.ones(length, entities, dtype=torch.bool),
                "stale": torch.zeros(length, entities, dtype=torch.bool),
                "scalars": torch.zeros(length, 9),
                "ability": torch.zeros(length, dtype=torch.long),
                "kind": torch.zeros(length, dtype=torch.long),
                "selected_indices": selected,
                "selected_mask": selected_mask,
                "target_kind": torch.ones(length, dtype=torch.long),
                "target_index": torch.zeros(length, dtype=torch.long),
                "target_point": torch.zeros(length, 2),
                "queue": torch.zeros(length, dtype=torch.long),
                "queue_mask": torch.zeros(length, dtype=torch.bool),
                "delay": torch.zeros(length),
                "delay_mask": torch.zeros(length, dtype=torch.bool),
                "map_size": (128, 128),
            }
        )
    return sequences


class TrainerTests(unittest.TestCase):
    def test_resume_matches_uninterrupted_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = CacheFixture(root)
            cache_dir = root / "cache"
            cache_document, _ = build_cache(fixture.collection, 86383, cache_dir)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(make_config(fixture, cache_document, cache_dir, root)),
                encoding="utf-8",
            )
            config, loaded_path = load_config(config_path)
            first = root / "run-a"
            report = run_training(config, loaded_path, first, max_updates=2, device="cpu")
            self.assertEqual(report["updates"], 2)
            self.assertTrue((first / "checkpoints" / "last.pt").is_file())
            self.assertTrue((first / "report.json").is_file())

            resumed = root / "run-b"
            run_training(config, loaded_path, resumed, max_updates=2, device="cpu")
            run_training(
                config,
                loaded_path,
                resumed,
                resume=resumed / "checkpoints" / "last.pt",
                max_updates=3,
                device="cpu",
            )
            uninterrupted = root / "run-c"
            run_training(config, loaded_path, uninterrupted, max_updates=3, device="cpu")
            left = load_checkpoint(resumed / "checkpoints" / "last.pt")
            right = load_checkpoint(uninterrupted / "checkpoints" / "last.pt")
            for key in ("model", "optimizer", "rng", "sampler", "updates", "best_loss"):
                assert_state_equal(left[key], right[key], key)

    def test_best_checkpoint_matches_reported_best(self):
        # The final validation runs after the last periodic update, so it can
        # improve on the periodic best. best.pt must be rewritten in that case
        # or the report and the checkpoint disagree about which weights are best.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = CacheFixture(root)
            cache_dir = root / "cache"
            cache_document, _ = build_cache(fixture.collection, 86383, cache_dir)
            config = make_config(fixture, cache_document, cache_dir, root)
            config["learning_rate"] = 0.001
            config["validation_interval"] = 1000
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            loaded, path = load_config(config_path)
            output = root / "run"
            run_training(loaded, path, output, max_updates=1, device="cpu")
            best_before = load_checkpoint(output / "checkpoints" / "best.pt")
            self.assertEqual(best_before["updates"], 1)
            report = run_training(
                loaded,
                path,
                output,
                resume=output / "checkpoints" / "last.pt",
                max_updates=41,
                device="cpu",
            )
            # The final validation at update 41 improves on the update-1 best,
            # so the reported best and best.pt must both point at update 41.
            self.assertEqual(report["best"]["update"], 41)
            best = load_checkpoint(output / "checkpoints" / "best.pt")
            self.assertEqual(best["updates"], report["best"]["update"])
            self.assertAlmostEqual(
                float(best["best_loss"]), float(report["best"]["loss"]), places=5
            )

    def test_apm_weighted_sampling_and_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = CacheFixture(root)
            apm = write_apm(
                root,
                fixture.collection,
                [
                    {
                        "replay_sha256": "a" * 64,
                        "base_build": 86383,
                        "player_id": 1,
                        "split": "train",
                        "quality_label": "high",
                        "player_apm": 300,
                        "rows": 4,
                    },
                    {
                        "replay_sha256": "b" * 64,
                        "base_build": 86383,
                        "player_id": 1,
                        "split": "validation",
                        "quality_label": "medium",
                        "player_apm": 200,
                        "rows": 2,
                    },
                ],
            )
            cache_dir = root / "cache"
            cache_document, _ = build_cache(fixture.collection, 86383, cache_dir, apm_manifest=apm)
            config = make_config(fixture, cache_document, cache_dir, root)
            config["apm_manifest"] = str(apm)
            config["apm_manifest_sha256"] = digest(apm)
            config["sampling_weights"] = {"high": 3.0, "medium": 2.0, "low": 1.0}
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            loaded, path = load_config(config_path)
            first = root / "run-a"
            report = run_training(loaded, path, first, max_updates=2, device="cpu")
            self.assertEqual(report["quality"]["sampled_by_quality"].get("high", 0), 4)
            self.assertIn("medium", report["final"]["by_quality"])
            resumed = root / "run-b"
            run_training(loaded, path, resumed, max_updates=2, device="cpu")
            run_training(
                loaded,
                path,
                resumed,
                resume=resumed / "checkpoints" / "last.pt",
                max_updates=3,
                device="cpu",
            )
            uninterrupted = root / "run-c"
            run_training(loaded, path, uninterrupted, max_updates=3, device="cpu")
            left = load_checkpoint(resumed / "checkpoints" / "last.pt")
            right = load_checkpoint(uninterrupted / "checkpoints" / "last.pt")
            for key in ("model", "optimizer", "rng", "sampler", "updates", "best_loss"):
                assert_state_equal(left[key], right[key], key)

    def test_init_from_warm_starts_weights_only(self):
        # A warm start copies weights but not optimizer/RNG/cursor state, so a
        # run started at zero updates must hold exactly the source weights.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = CacheFixture(root)
            cache_dir = root / "cache"
            cache_document, _ = build_cache(fixture.collection, 86383, cache_dir)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(make_config(fixture, cache_document, cache_dir, root)),
                encoding="utf-8",
            )
            config, loaded_path = load_config(config_path)
            source = root / "source"
            run_training(config, loaded_path, source, max_updates=2, device="cpu")
            source_checkpoint = source / "checkpoints" / "best.pt"
            warm = root / "warm"
            report = run_training(
                config,
                loaded_path,
                warm,
                init_from=source_checkpoint,
                max_updates=0,
                device="cpu",
            )
            self.assertEqual(report["updates"], 0)
            self.assertEqual(report["warm_start"]["source_updates"], 2)
            self.assertTrue((warm / "init.json").is_file())
            started = load_checkpoint(warm / "checkpoints" / "last.pt")
            origin = load_checkpoint(source_checkpoint)
            assert_state_equal(started["model"], origin["model"], "model")
            self.assertEqual(started["updates"], 0)

    def test_init_from_rejects_model_config_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = CacheFixture(root)
            cache_dir = root / "cache"
            cache_document, _ = build_cache(fixture.collection, 86383, cache_dir)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(make_config(fixture, cache_document, cache_dir, root)),
                encoding="utf-8",
            )
            config, loaded_path = load_config(config_path)
            source = root / "source"
            run_training(config, loaded_path, source, max_updates=1, device="cpu")
            wide = make_config(fixture, cache_document, cache_dir, root)
            wide["model"]["ability_head_hidden"] = 64
            wide_path = root / "wide.json"
            wide_path.write_text(json.dumps(wide), encoding="utf-8")
            wide_config, wide_loaded = load_config(wide_path)
            with self.assertRaisesRegex(ValueError, "model config mismatch"):
                run_training(
                    wide_config,
                    wide_loaded,
                    root / "warm",
                    init_from=source / "checkpoints" / "last.pt",
                    max_updates=0,
                    device="cpu",
                )

    def test_resume_and_init_from_are_exclusive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = CacheFixture(root)
            cache_dir = root / "cache"
            cache_document, _ = build_cache(fixture.collection, 86383, cache_dir)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(make_config(fixture, cache_document, cache_dir, root)),
                encoding="utf-8",
            )
            config, loaded_path = load_config(config_path)
            with self.assertRaisesRegex(ValueError, "mutually exclusive"):
                run_training(
                    config,
                    loaded_path,
                    root / "run",
                    resume=root / "last.pt",
                    init_from=root / "last.pt",
                    max_updates=0,
                    device="cpu",
                )

    def test_cosine_schedule_endpoints_and_warmup(self):
        schedule = parse_lr_schedule(
            {"kind": "cosine", "warmup_updates": 2, "total_updates": 10, "min_learning_rate": 0.0},
            1.0,
        )
        # Warmup ramps linearly over the first updates, then cosine takes over.
        self.assertAlmostEqual(learning_rate_for(schedule, 1.0, 0), 0.5)
        self.assertAlmostEqual(learning_rate_for(schedule, 1.0, 1), 1.0)
        self.assertAlmostEqual(learning_rate_for(schedule, 1.0, 2), 1.0)
        self.assertAlmostEqual(learning_rate_for(schedule, 1.0, 6), 0.5)
        self.assertAlmostEqual(learning_rate_for(schedule, 1.0, 10), 0.0)
        self.assertAlmostEqual(learning_rate_for(schedule, 1.0, 25), 0.0)
        self.assertAlmostEqual(learning_rate_for({"kind": "constant"}, 0.3, 7), 0.3)

    def test_invalid_lr_schedule_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "min_learning_rate"):
            parse_lr_schedule(
                {
                    "kind": "cosine",
                    "warmup_updates": 0,
                    "total_updates": 10,
                    "min_learning_rate": 2.0,
                },
                1.0,
            )
        with self.assertRaisesRegex(ValueError, "warmup_updates"):
            parse_lr_schedule({"kind": "cosine", "warmup_updates": 10, "total_updates": 10}, 1.0)
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            parse_lr_schedule({"kind": "step"}, 1.0)

    def test_lr_schedule_drives_the_optimizer_and_survives_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = CacheFixture(root)
            cache_dir = root / "cache"
            cache_document, _ = build_cache(fixture.collection, 86383, cache_dir)
            config = make_config(fixture, cache_document, cache_dir, root)
            config["learning_rate"] = 0.01
            config["lr_schedule"] = {
                "kind": "cosine",
                "warmup_updates": 1,
                "total_updates": 4,
                "min_learning_rate": 0.0,
            }
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            loaded, path = load_config(config_path)
            steps = 4
            first = root / "run-a"
            run_training(loaded, path, first, max_updates=steps, device="cpu")
            observed = [
                json.loads(line)["learning_rate"]
                for line in (first / "metrics.jsonl").read_text().splitlines()
            ]
            expected = [
                learning_rate_for(loaded["lr_schedule"], loaded["learning_rate"], index)
                for index in range(steps)
            ]
            for got, want in zip(observed, expected):
                self.assertAlmostEqual(got, want, places=8)
            self.assertAlmostEqual(observed[0], loaded["learning_rate"], places=8)
            self.assertLess(observed[-1], loaded["learning_rate"])
            resumed = root / "run-b"
            run_training(loaded, path, resumed, max_updates=2, device="cpu")
            run_training(
                loaded,
                path,
                resumed,
                resume=resumed / "checkpoints" / "last.pt",
                max_updates=steps + 1,
                device="cpu",
            )
            uninterrupted = root / "run-c"
            run_training(loaded, path, uninterrupted, max_updates=steps + 1, device="cpu")
            left = load_checkpoint(resumed / "checkpoints" / "last.pt")
            right = load_checkpoint(uninterrupted / "checkpoints" / "last.pt")
            # best_loss is excluded: the resumed run validates once before the
            # breakpoint, so its running best can legitimately be lower.
            for key in ("model", "optimizer", "rng", "sampler", "updates"):
                assert_state_equal(left[key], right[key], key)
            report = json.loads((resumed / "report.json").read_text())
            self.assertEqual(report["lr_schedule"]["kind"], "cosine")
            self.assertLess(report["final_learning_rate"], loaded["learning_rate"])

    def test_evaluation_loss_does_not_depend_on_batch_size(self):
        # Reported losses must pool by active count: averaging per-batch means
        # weights a batch by its size, and the selection head's active count per
        # batch depends on the largest selection it happens to contain. Grouping
        # the same sequences differently must therefore not move the numbers.
        model = build_bc_model(make_cache(), {})
        sequences = make_sequences([0, 1, 6, 2])
        weights = {name: 1.0 for name in HEAD_NAMES}
        two = evaluate_sequences(model, sequences, 2, "cpu", weights)
        three = evaluate_sequences(model, sequences, 3, "cpu", weights)
        for name in HEAD_NAMES:
            self.assertAlmostEqual(two["loss"][name], three["loss"][name], places=5, msg=name)
        self.assertAlmostEqual(two["loss"]["total"], three["loss"]["total"], places=5)

    def test_sampling_weights_require_apm_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = CacheFixture(root)
            cache_dir = root / "cache"
            cache_document, _ = build_cache(fixture.collection, 86383, cache_dir)
            config = make_config(fixture, cache_document, cache_dir, root)
            config["sampling_weights"] = {"high": 3.0}
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "require apm_manifest"):
                load_config(config_path)

    def test_test_split_is_not_a_training_split(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = CacheFixture(root)
            cache_dir = root / "cache"
            cache_document, _ = build_cache(fixture.collection, 86383, cache_dir)
            config = make_config(fixture, cache_document, cache_dir, root)
            config["max_sequences"] = 0
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            loaded, path = load_config(config_path)
            with self.assertRaisesRegex(ValueError, "No train sequences"):
                run_training(loaded, path, root / "run", max_updates=1, device="cpu")


if __name__ == "__main__":
    unittest.main()
