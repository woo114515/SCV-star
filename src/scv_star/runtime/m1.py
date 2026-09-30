"""Bounded GPU compatibility checks on synthetic data; no SC2 or policy training."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_state_equal(left, right, path="state"):
    """Compare all checkpoint fields, including optimizer counters and RNG tensors."""
    import torch

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
        for index, (a, b) in enumerate(zip(left, right)):
            assert_state_equal(a, b, f"{path}[{index}]")
    elif left != right:
        raise AssertionError(f"{path}: values differ")


def make_model():
    import torch
    from torch import nn

    class ProbeNetwork(nn.Module):
        def __init__(self):
            super().__init__()
            self.spatial = nn.Sequential(
                nn.Conv2d(4, 16, 3, padding=1), nn.ReLU(), nn.AdaptiveAvgPool2d(1)
            )
            self.entity = nn.Linear(32, 64)
            self.attention = nn.MultiheadAttention(64, 4, dropout=0.1, batch_first=True)
            self.memory = nn.LSTM(80, 64, batch_first=True)
            self.dropout = nn.Dropout(0.1)
            self.head = nn.Linear(64, 32)

        def forward(self, spatial, entities):
            batch, steps = spatial.shape[:2]
            spatial = self.spatial(spatial.flatten(0, 1)).flatten(1)
            encoded = self.entity(entities.flatten(0, 1))
            encoded, _ = self.attention(encoded, encoded, encoded, need_weights=False)
            combined = torch.cat((spatial, encoded.mean(1)), -1).reshape(batch, steps, 80)
            memory, _ = self.memory(combined)
            return self.head(self.dropout(memory))

    return ProbeNetwork().cuda()


def check_precision(name, dtype, output, steps):
    import torch
    from torch.nn import functional as F

    torch.manual_seed(20260930)
    torch.cuda.manual_seed_all(20260930)
    torch.cuda.reset_peak_memory_stats()
    model = make_model().train()
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, foreach=False)
    scaler = torch.amp.GradScaler("cuda", enabled=name == "fp16", init_scale=128)
    spatial = torch.randn(4, 4, 4, 16, 16, device="cuda")
    entities = torch.randn(4, 4, 16, 32, device="cuda")
    labels = torch.ones(4, 4, dtype=torch.long, device="cuda")
    legal = torch.arange(32, device="cuda") % 3 != 0
    initial = model.head.weight.detach().clone()

    def step():
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast("cuda", dtype=dtype, enabled=name != "fp32"):
            logits = model(spatial, entities)
            if not torch.isfinite(logits).all():
                raise AssertionError("Nonfinite logits")
            masked = logits.float().masked_fill(~legal, float("-inf"))
            loss = F.cross_entropy(masked.flatten(0, 1), labels.flatten())
        if not torch.isfinite(loss):
            raise AssertionError("Nonfinite loss")
        scaler.scale(loss).backward()
        scaler.unscale_(optimizer)
        if not all(p.grad is not None and torch.isfinite(p.grad).all() for p in model.parameters()):
            raise AssertionError("Missing or nonfinite gradients")
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
        scaler.step(optimizer)
        scaler.update()
        if not all(torch.isfinite(p).all() for p in model.parameters()):
            raise AssertionError("Nonfinite updated weights")
        sampled = torch.distributions.Categorical(logits=masked.detach()).sample()
        if not legal[sampled].all():
            raise AssertionError("Masked action sampled")
        torch.cuda.synchronize()
        return float(loss.detach()), str(logits.dtype)

    def snapshot():
        return {
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "scaler": scaler.state_dict(),
            "cpu_rng": torch.get_rng_state(),
            "cuda_rng": torch.cuda.get_rng_state_all(),
        }

    losses, latencies = [], []
    actual_dtype = None
    for _ in range(steps):
        started = time.perf_counter()
        loss, actual_dtype = step()
        latencies.append(time.perf_counter() - started)
        losses.append(loss)
    if torch.equal(initial, model.head.weight):
        raise AssertionError("Optimizer did not change weights")
    checkpoint = output / f"{name}-checkpoint.pt"
    torch.save(snapshot(), checkpoint)
    uninterrupted_loss, _ = step()
    # Serialize before mutating the model: state_dict tensors share model storage.
    expected_path = output / f"{name}-uninterrupted.pt"
    torch.save(snapshot(), expected_path)
    model = make_model().train()
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, foreach=False)
    scaler = torch.amp.GradScaler("cuda", enabled=name == "fp16", init_scale=128)
    saved = torch.load(checkpoint, weights_only=True)
    model.load_state_dict(saved["model"])
    optimizer.load_state_dict(saved["optimizer"])
    scaler.load_state_dict(saved["scaler"])
    torch.set_rng_state(saved["cpu_rng"])
    torch.cuda.set_rng_state_all(saved["cuda_rng"])
    assert_state_equal(snapshot(), saved)
    resumed_loss, _ = step()
    assert_state_equal(snapshot(), torch.load(expected_path, weights_only=True))
    if resumed_loss != uninterrupted_loss:
        raise AssertionError("Resumed loss differs")
    return {
        "passed": True,
        "precision": name,
        "logits_dtype": actual_dtype,
        "synthetic_updates": steps + 2,
        "losses": losses,
        "uninterrupted_loss": uninterrupted_loss,
        "resumed_loss": resumed_loss,
        "checkpoint_and_next_update_bitwise_equal": True,
        "checkpoint_sha256": digest(checkpoint),
        "parameter_count": sum(p.numel() for p in model.parameters()),
        "first_update_seconds": latencies[0],
        "remaining_updates_mean_seconds": sum(latencies[1:]) / len(latencies[1:]),
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--steps",
        type=int,
        default=20,
        choices=range(2, 101),
        metavar="N",
        help="Updates per precision before resume check (2-100; default 20)",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[3]
    report = {"passed": False, "started_at": datetime.now(timezone.utc).isoformat(), "checks": []}
    started = time.perf_counter()
    report["configuration"] = {
        "steps_per_precision": args.steps,
        "network_seed": 20260930,
        "reference_seed": 7,
        "spatial_shape": [4, 4, 4, 16, 16],
        "entity_shape": [4, 4, 16, 32],
        "deterministic_algorithms": True,
        "cublas_workspace_config": ":4096:8",
        "tf32": False,
    }
    try:
        project_bytes = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
        free = shutil.disk_usage(root).free
        report["storage_before"] = {
            "project_logical_bytes": project_bytes,
            "volume_free_bytes": free,
        }
        if project_bytes >= 80_000_000_000 or free < 20_000_000_000:
            raise RuntimeError("Storage guard: project >=80 GB or volume free <20 GB")
        os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable; CPU fallback is not accepted")
        if torch.__version__ != "2.13.0+cu130" or torch.version.cuda != "13.0":
            raise RuntimeError("PyTorch/CUDA differs from pinned M1 environment")
        torch.set_num_threads(4)
        torch.use_deterministic_algorithms(True)
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        properties = torch.cuda.get_device_properties(0)
        report["environment"] = {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "torch": torch.__version__,
            "cuda_runtime": torch.version.cuda,
            "cudnn": torch.backends.cudnn.version(),
            "gpu": properties.name,
            "compute_capability": list(torch.cuda.get_device_capability()),
            "compiled_architectures": torch.cuda.get_arch_list(),
            "vram_bytes": properties.total_memory,
            "bf16_supported": torch.cuda.is_bf16_supported(),
            "nvidia_smi": subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=name,driver_version,memory.total",
                    "--format=csv,noheader",
                ],
                text=True,
            ).strip(),
            "dependencies": {
                name: importlib.metadata.version(name)
                for name in (
                    "torch",
                    "setuptools",
                    "sympy",
                    "networkx",
                    "filelock",
                    "fsspec",
                    "jinja2",
                    "markupsafe",
                    "mpmath",
                    "typing-extensions",
                )
            },
        }
        report["source_sha256"] = digest(Path(__file__))
        report["dependency_lock_sha256"] = digest(root / "requirements/m1-windows-py311-cu130.txt")
        report["git_head"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        report["git_dirty"] = bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()
        )
        torch.manual_seed(7)
        a, b = torch.randn(128, 128), torch.randn(128, 128)
        reference = a.double() @ b.double()
        actual = (a.cuda() @ b.cuda()).cpu().double()
        torch.testing.assert_close(actual, reference, rtol=1e-4, atol=1e-4)
        report["checks"].append(
            {
                "name": "gpu_matmul_cpu_reference",
                "passed": True,
                "max_abs_error": float((actual - reference).abs().max()),
                "rtol": 1e-4,
                "atol": 1e-4,
            }
        )
        for name, dtype in (
            ("fp32", torch.float32),
            ("fp16", torch.float16),
            ("bf16", torch.bfloat16),
        ):
            if name == "bf16" and not torch.cuda.is_bf16_supported():
                raise RuntimeError("BF16 unsupported on this M1 target")
            torch.cuda.empty_cache()
            result = check_precision(name, dtype, args.output, args.steps)
            report["checks"].append(result)
            print(json.dumps(result), flush=True)
        report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
        report["traceback"] = traceback.format_exc()
    finally:
        report["wall_seconds"] = time.perf_counter() - started
        (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {key: report[key] for key in ("passed", "wall_seconds", "error") if key in report}
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
