import json
import tempfile
import unittest
from pathlib import Path

from scv_star.data.bc_cache import build_cache
from scv_star.training.bc import load_config, run_training
from scv_star.training.checkpoint import assert_state_equal, load_checkpoint

try:
    from tests.unit.test_bc_cache import CacheFixture, digest, write_apm
except ImportError:  # unittest discover puts tests/unit on sys.path
    from test_bc_cache import CacheFixture, digest, write_apm


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
