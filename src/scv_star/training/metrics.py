"""Masked per-head metrics for BC validation and evaluation."""

from __future__ import annotations

import torch

from scv_star.data.bc_features import TARGET_KINDS


class MetricsAccumulator:
    """Accumulate counts and errors without retaining full logits or labels."""

    def __init__(self):
        self.ability_count = None
        self.ability_target = None
        self.ability_correct = None
        self.ability_rows = 0
        self.ability_top1 = 0
        self.ability_top5 = 0
        self.kind_rows = 0
        self.kind_correct = 0
        self.selection_rows = 0
        self.selection_tp = 0
        self.selection_fp = 0
        self.selection_fn = 0
        self.selection_exact = 0
        self.selection_predicted = 0
        self.selection_target = 0
        self.target_kind_rows = 0
        self.target_kind_correct = 0
        self.target_unit_rows = 0
        self.target_unit_correct = 0
        self.point_rows = 0
        self.point_abs = 0.0
        self.queue_rows = 0
        self.queue_correct = 0
        self.delay_rows = 0
        self.delay_abs = 0.0
        self.delay_errors = []

    @torch.no_grad()
    def update(self, outputs, targets):
        ability_logits = outputs["ability"]
        rows, count = ability_logits.shape[0] * ability_logits.shape[1], ability_logits.shape[-1]
        step_active = targets["step_mask"].reshape(-1)
        # Padded steps carry a zero label and a zero prediction, so counting them
        # would inflate accuracy by roughly the padding share (1.5 points on the
        # test split). Every head below is restricted to real steps.
        ability_logits = ability_logits.reshape(rows, count)[step_active]
        ability_target = targets["ability"].reshape(-1)[step_active]
        if self.ability_target is None:
            self.ability_target = torch.zeros(count, dtype=torch.long, device=ability_target.device)
            self.ability_correct = torch.zeros(
                count, dtype=torch.long, device=ability_target.device
            )
            self.ability_count = count
        predicted = ability_logits.argmax(-1)
        correct = predicted == ability_target
        self.ability_rows += ability_target.numel()
        self.ability_top1 += int(correct.sum())
        top = min(5, count)
        self.ability_top5 += int(
            (ability_logits.topk(top, dim=-1).indices == ability_target[:, None]).any(-1).sum()
        )
        self.ability_target += torch.bincount(ability_target, minlength=count)
        self.ability_correct += torch.bincount(ability_target[correct], minlength=count)

        kind_target = targets["kind"].reshape(-1)[step_active]
        kind_predicted = outputs["kind"].reshape(rows, -1).argmax(-1)[step_active]
        self.kind_rows += kind_target.numel()
        self.kind_correct += int((kind_predicted == kind_target).sum())

        entity_count = outputs["target_unit"].shape[-1]
        selection_logits = outputs["selection"]
        indices = targets["selected_indices"].reshape(rows, -1)
        selection_mask = targets["selected_mask"].reshape(rows, -1)
        predicted_selection = [[] for _ in range(rows)]
        stopped = [False] * rows
        for logits in selection_logits:
            choices = logits.argmax(-1)
            for row in range(rows):
                if stopped[row] or not bool(step_active[row]):
                    continue
                choice = int(choices[row])
                if choice == entity_count:
                    stopped[row] = True
                else:
                    predicted_selection[row].append(choice)
        for row in range(rows):
            if not bool(step_active[row]):
                continue
            target = [int(value) for value in indices[row][selection_mask[row]].tolist()]
            actual = predicted_selection[row]
            actual_set, target_set = set(actual), set(target)
            self.selection_tp += len(actual_set & target_set)
            self.selection_fp += len(actual_set - target_set)
            self.selection_fn += len(target_set - actual_set)
            self.selection_exact += int(actual == target)
            self.selection_predicted += len(actual)
            self.selection_target += len(target)
        self.selection_rows += int(step_active.sum())

        target_kind = targets["target_kind"].reshape(-1)[step_active]
        target_kind_predicted = outputs["target_kind"].argmax(-1)[step_active]
        self.target_kind_rows += target_kind.numel()
        self.target_kind_correct += int((target_kind_predicted == target_kind).sum())

        unit_active = target_kind == TARGET_KINDS.index("unit")
        unit_predicted = outputs["target_unit"].argmax(-1)[step_active]
        unit_target = targets["target_index"].reshape(-1)[step_active]
        self.target_unit_rows += int(unit_active.sum())
        self.target_unit_correct += int(((unit_predicted == unit_target) & unit_active).sum())

        point_active = target_kind == TARGET_KINDS.index("point")
        point_target = targets["target_point"].reshape(-1, 2)[step_active]
        map_size = targets["map_size"].reshape(-1, 2).clamp(min=1.0)[step_active]
        point_predicted = outputs["target_point"].reshape(-1, 2)[step_active] * map_size
        point_error = (point_predicted - point_target).abs().mean(-1)
        self.point_rows += int(point_active.sum())
        self.point_abs += float((point_error * point_active).sum())

        queue_active = targets["queue_mask"].reshape(-1) & step_active
        queue_predicted = outputs["queue"].argmax(-1)
        queue_target = targets["queue"].reshape(-1)
        self.queue_rows += int(queue_active.sum())
        self.queue_correct += int(((queue_predicted == queue_target) & queue_active).sum())

        delay_active = targets["delay_mask"].reshape(-1) & step_active
        delay_actual = targets["delay"].reshape(-1)
        delay_predicted = torch.expm1(outputs["delay"]).clamp(min=0)
        delay_error = (delay_predicted - delay_actual).abs()
        errors = delay_error[delay_active]
        self.delay_rows += int(errors.numel())
        self.delay_abs += float(errors.sum())
        if errors.numel():
            self.delay_errors.append(errors.detach().cpu())

    def compute(self):
        ability = {
            "count": self.ability_count,
            "top1": self.ability_top1 / max(self.ability_rows, 1),
            "top5": self.ability_top5 / max(self.ability_rows, 1),
            "rows": self.ability_rows,
        }
        per_ability = {}
        if self.ability_target is not None:
            for index in torch.nonzero(self.ability_target > 0).flatten().tolist():
                per_ability[str(index)] = {
                    "target": int(self.ability_target[index]),
                    "correct": int(self.ability_correct[index]),
                    "accuracy": float(self.ability_correct[index] / self.ability_target[index]),
                }
        precision = self.selection_tp / max(self.selection_tp + self.selection_fp, 1)
        recall = self.selection_tp / max(self.selection_tp + self.selection_fn, 1)
        f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
        delay_median = 0.0
        if self.delay_errors:
            delay_median = float(torch.cat(self.delay_errors).median())
        return {
            "ability": {**ability, "per_ability": per_ability},
            "kind": {
                "accuracy": self.kind_correct / max(self.kind_rows, 1),
                "rows": self.kind_rows,
            },
            "selection": {
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "exact_match": self.selection_exact / max(self.selection_rows, 1),
                "mean_predicted": self.selection_predicted / max(self.selection_rows, 1),
                "mean_target": self.selection_target / max(self.selection_rows, 1),
                "rows": self.selection_rows,
            },
            "target_kind": {
                "accuracy": self.target_kind_correct / max(self.target_kind_rows, 1),
                "rows": self.target_kind_rows,
            },
            "target_unit": {
                "accuracy": self.target_unit_correct / max(self.target_unit_rows, 1),
                "rows": self.target_unit_rows,
            },
            "target_point": {
                "mae": self.point_abs / max(self.point_rows, 1),
                "rows": self.point_rows,
            },
            "queue": {
                "accuracy": self.queue_correct / max(self.queue_rows, 1),
                "rows": self.queue_rows,
            },
            "delay": {
                "mae": self.delay_abs / max(self.delay_rows, 1),
                "median": delay_median,
                "rows": self.delay_rows,
            },
        }
