"""Decode-mode diagnostics for the behaviour-cloning selection head.

Reports what the action heads do when the prefix is given versus when the model
has to continue from its own choices, which the training metric cannot show.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from scv_star.data.bc_cache import load_cache
from scv_star.data.bc_features import ENTITY_CATEGORICAL_INDEX, TARGET_KINDS
from scv_star.models.bc import build_bc_model, model_config_matches
from scv_star.training.bc import _device, collate_sequences, load_config, set_determinism
from scv_star.training.checkpoint import load_checkpoint

ALLIANCE_INDEX = ENTITY_CATEGORICAL_INDEX[1]
SELF_ALLIANCE = 1
SIZE_BUCKETS = ((1, 1), (2, 4), (5, 9), (10, 10**6))


def _stop_after_first_stop(logits, stop_index):
    """Turn a per-step logit list into the set of picked units for each row."""
    rows = logits[0].shape[0]
    picked = [[] for _ in range(rows)]
    stopped = [False] * rows
    for step_logits in logits:
        choice = step_logits.argmax(-1)
        for row in range(rows):
            if stopped[row]:
                continue
            value = int(choice[row])
            if value == stop_index:
                stopped[row] = True
            else:
                picked[row].append(value)
    return picked


def _as_sets(selected_indices, selected_mask):
    picked = []
    for row_index in range(selected_indices.shape[0]):
        row = selected_indices[row_index]
        keep = selected_mask[row_index]
        picked.append([int(value) for value in row[keep].tolist()])
    return picked


def _set_scores(picked, target):
    tp = sum(len(set(p) & set(t)) for p, t in zip(picked, target))
    fp = sum(len(set(p) - set(t)) for p, t in zip(picked, target))
    fn = sum(len(set(t) - set(p)) for p, t in zip(picked, target))
    exact = sum(int(set(p) == set(t)) for p, t in zip(picked, target))
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "exact": exact / max(len(picked), 1),
        "mean_predicted": sum(len(p) for p in picked) / max(len(picked), 1),
        "mean_target": sum(len(t) for t in target) / max(len(target), 1),
    }


def _bucket_of(size):
    for low, high in SIZE_BUCKETS:
        if low <= size <= high:
            return (low, high)
    return SIZE_BUCKETS[-1]


def _recall_by_size(picked, target):
    totals = defaultdict(lambda: [0, 0])
    for p, t in zip(picked, target):
        key = _bucket_of(len(set(t)))
        totals[key][0] += len(set(p) & set(t))
        totals[key][1] += len(set(t))
    return {
        f"{low}-{'inf' if high > 10**5 else high}": round(tp / max(total, 1), 4)
        for (low, high), (tp, total) in sorted(totals.items())
    }


def _counts_by_size(picked, target):
    totals = Counter()
    for t in target:
        totals[_bucket_of(len(set(t)))] += 1
    return {
        f"{low}-{'inf' if high > 10**5 else high}": totals[(low, high)]
        for low, high in SIZE_BUCKETS
    }


def _expected_target_kind(rows):
    """Majority target kind for each ability, learned from the labels themselves."""
    counts = defaultdict(Counter)
    for ability, target_kind in rows:
        counts[int(ability)][int(target_kind)] += 1
    return {ability: counter.most_common(1)[0][0] for ability, counter in counts.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path("data/interim/bc-86383-v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", default="validation")
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--max-sequences", type=int)
    parser.add_argument("--device", default=None)
    parser.add_argument("--biases", nargs="+", type=float, default=[])
    args = parser.parse_args()
    biases = list(args.biases)

    output = Path(args.output).absolute()
    if output.exists():
        raise ValueError(f"Output already exists: {output}")
    config, _ = load_config(args.config)
    cache, data = load_cache(Path(args.cache).absolute())
    sequences = list(data[args.split])
    if args.max_sequences:
        sequences = sequences[: args.max_sequences]
    device = _device(args.device)
    set_determinism(config["seed"])
    model = build_bc_model(cache, config["model"]).to(device)
    state = load_checkpoint(args.checkpoint)
    if not model_config_matches(state.get("model_config"), model.config):
        raise ValueError("Checkpoint model config mismatch")
    model.load_state_dict(state["model"])
    model.eval()

    records = defaultdict(lambda: defaultdict(list))
    target_kind_pairs = []
    for start in range(0, len(sequences), args.batch_size):
        chunk = sequences[start : start + args.batch_size]
        batch = collate_sequences(chunk, device)
        targets = batch["targets"]
        step = targets["step_mask"].reshape(-1)
        entity_mask = batch["entity_mask"].reshape(-1, batch["entity_mask"].shape[-1])
        alliance = batch["entities"].reshape(-1, entity_mask.shape[-1], 27)[..., ALLIANCE_INDEX]
        allowed = entity_mask & (alliance.int() == SELF_ALLIANCE)
        labels = {
            "ability": targets["ability"].reshape(-1),
            "kind": targets["kind"].reshape(-1),
            "selected": _as_sets(
                targets["selected_indices"].reshape(-1, *targets["selected_indices"].shape[2:]),
                targets["selected_mask"].reshape(-1, *targets["selected_mask"].shape[2:]),
            ),
            "target_kind": targets["target_kind"].reshape(-1),
            "target_unit": targets["target_index"].reshape(-1),
        }
        target_kind_pairs.extend(
            zip(labels["ability"][step].tolist(), labels["target_kind"][step].tolist())
        )
        real = step.nonzero().flatten().tolist()
        window = batch["entity_mask"].shape[1]
        step_index = {row: row % window for row in real}

        outputs = model(batch)
        count = batch["entity_mask"].shape[-1]
        teacher_picked = _stop_after_first_stop(outputs["selection"], count)
        predicted = {
            "A": {
                "ability": outputs["ability"].reshape(-1, outputs["ability"].shape[-1]).argmax(-1),
                "kind": outputs["kind"].reshape(-1, outputs["kind"].shape[-1]).argmax(-1),
                "selected": teacher_picked,
                "target_kind": outputs["target_kind"].argmax(-1),
                "target_unit": outputs["target_unit"].argmax(-1),
            }
        }
        forced_ability = targets["ability"].reshape(-1)
        forced_kind = targets["kind"].reshape(-1)
        mode_calls = [
            ("B", {"ability": forced_ability, "kind": forced_kind}),
            ("C", {}),
            ("B_sample", {"ability": forced_ability, "kind": forced_kind, "greedy": False}),
            ("C_sample", {"greedy": False}),
            ("C_legal", {}),
            ("C_legal_stop", {}),
        ]
        for bias in biases:
            mode_calls.append(
                (
                    f"B_bias{bias:g}",
                    {"ability": forced_ability, "kind": forced_kind, "stop_logit_bias": bias},
                )
            )
            mode_calls.append((f"C_bias{bias:g}", {"stop_logit_bias": bias}))
        for mode, keyword in mode_calls:
            call = dict(greedy=True)
            call.update(keyword)
            if mode.startswith("C_legal"):
                call["allowed_entities"] = allowed
            if mode == "C_legal_stop":
                call["forbid_immediate_stop"] = True
            actions = model.decode(batch, **call)
            predicted[mode] = {
                "ability": actions["ability"].reshape(-1),
                "kind": actions["kind"].reshape(-1),
                "selected": _as_sets(
                    actions["selected_indices"].reshape(-1, actions["selected_indices"].shape[-1]),
                    actions["selected_mask"].reshape(-1, actions["selected_mask"].shape[-1]),
                ),
                "target_kind": actions["target_kind"].reshape(-1),
                "target_unit": actions["target_unit"].reshape(-1),
            }
        for mode, values in predicted.items():
            for row in real:
                records[mode]["ability"].append(
                    int(values["ability"][row] == labels["ability"][row])
                )
                records[mode]["kind"].append(int(values["kind"][row] == labels["kind"][row]))
                records[mode]["target_kind"].append(
                    int(values["target_kind"][row] == labels["target_kind"][row])
                )
                picked = values["selected"][row]
                target = labels["selected"][row]
                records[mode]["picked"].append(picked)
                records[mode]["pred_ability"].append(int(values["ability"][row]))
                records[mode]["pred_target_kind"].append(int(values["target_kind"][row]))
                records[mode]["target"].append(target)
                records[mode]["empty"].append(int(len(picked) == 0))
                non_self = sum(1 for unit in set(picked) if not bool(allowed[row, unit]))
                records[mode]["non_self"].append(non_self)
                records[mode]["selected_total"].append(len(set(picked)))
                if int(values["target_kind"][row]) == TARGET_KINDS.index("unit"):
                    unit = int(values["target_unit"][row])
                    records[mode]["unit_valid"].append(int(bool(entity_mask[row, unit])))
                full = (
                    int(values["ability"][row] == labels["ability"][row])
                    & int(values["kind"][row] == labels["kind"][row])
                    & int(set(picked) == set(target))
                    & int(values["target_kind"][row] == labels["target_kind"][row])
                )
                records[mode]["full"].append(full)
                records[mode]["step_bucket"].append(step_index[row] // 8)

    expected_kind = _expected_target_kind(target_kind_pairs)
    report = {"checkpoint_update": int(state["updates"]), "split": args.split, "modes": {}}
    for mode, values in records.items():
        picked, target = values["picked"], values["target"]
        scores = _set_scores(picked, target)
        entries = len(values["ability"])
        non_self = sum(values["non_self"])
        selected_total = sum(values["selected_total"])
        unit_valid = values["unit_valid"]
        report["modes"][mode] = {
            "rows": entries,
            **scores,
            "ability_top1": sum(values["ability"]) / max(entries, 1),
            "kind_top1": sum(values["kind"]) / max(entries, 1),
            "target_kind_top1": sum(values["target_kind"]) / max(entries, 1),
            "full_action_match": sum(values["full"]) / max(entries, 1),
            "empty_selection": sum(values["empty"]) / max(entries, 1),
            "non_self_selected": non_self / max(selected_total, 1),
            "unit_target_in_mask": sum(unit_valid) / max(len(unit_valid), 1),
            "recall_by_size": _recall_by_size(picked, target),
            "rows_by_size": _counts_by_size(picked, target),
        }
        consistent = 0
        for ability, target_kind in zip(values["pred_ability"], values["pred_target_kind"]):
            expected = expected_kind.get(int(ability))
            if expected is not None and int(target_kind) == int(expected):
                consistent += 1
        report["modes"][mode]["target_kind_consistency"] = consistent / max(entries, 1)
        buckets = defaultdict(lambda: [0, 0])
        for bucket, hit in zip(values["step_bucket"], values["full"]):
            buckets[bucket][0] += hit
            buckets[bucket][1] += 1
        report["modes"][mode]["full_action_match_by_step"] = {
            f"{bucket * 8}-{bucket * 8 + 7}": round(hit / max(total, 1), 4)
            for bucket, (hit, total) in sorted(buckets.items())
        }
        report["modes"][mode]["stop_size_histogram"] = {
            str(size): count for size, count in Counter(values["selected_total"]).most_common(6)
        }
    report["expected_target_kind"] = {str(k): int(v) for k, v in expected_kind.items()}

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    header = f"{'mode':13s} {'F1':>6} {'P':>6} {'R':>6} {'pred':>5} {'tgt':>5} {'empty':>6} {'nonself':>7} {'unittgt':>7} {'full':>6} {'top1':>6}"
    print(header)
    for mode, values in report["modes"].items():
        print(
            f"{mode:13s} {values['f1']:6.4f} {values['precision']:6.4f} {values['recall']:6.4f} "
            f"{values['mean_predicted']:5.2f} {values['mean_target']:5.2f} "
            f"{values['empty_selection']:6.3f} {values['non_self_selected']:7.4f} "
            f"{values['unit_target_in_mask']:7.4f} {values['full_action_match']:6.4f} {values['ability_top1']:6.4f}"
        )
    print("\nrecall by target size:")
    for mode, values in report["modes"].items():
        print(f"  {mode:13s} {values['recall_by_size']}")
    print(f"\nwrote {output}")
    return report


if __name__ == "__main__":
    main()
