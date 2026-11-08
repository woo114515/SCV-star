import unittest

import torch

from scv_star.training.metrics import MetricsAccumulator


def make_outputs_targets():
    outputs = {
        "ability": torch.tensor([[[0.1, 2.0, 0.3]]]),
        "kind": torch.tensor([[[0.1, 2.0]]]),
        "selection": [
            torch.tensor([[2.0, 0.1, 0.2]]),
            torch.tensor([[0.1, 0.2, 2.0]]),
        ],
        "target_kind": torch.tensor([[0.1, 0.1, 2.0]]),
        "target_unit": torch.tensor([[0.1, 2.0]]),
        "target_point": torch.tensor([[0.5, 0.25]]),
        "queue": torch.tensor([[0.1, 2.0]]),
        "delay": torch.tensor([torch.log(torch.tensor(6.0))]),
    }
    targets = {
        "ability": torch.tensor([[1]]),
        "kind": torch.tensor([[1]]),
        "selected_indices": torch.tensor([[[0, 1]]]),
        "selected_mask": torch.tensor([[[True, True]]]),
        "target_kind": torch.tensor([[2]]),
        "target_index": torch.tensor([[1]]),
        "target_point": torch.tensor([[[64.0, 32.0]]]),
        "queue": torch.tensor([[1]]),
        "queue_mask": torch.tensor([[True]]),
        "delay": torch.tensor([[5.0]]),
        "delay_mask": torch.tensor([[True]]),
        "map_size": torch.tensor([[[128.0, 128.0]]]),
    }
    return outputs, targets


class MetricsTests(unittest.TestCase):
    def test_single_update_metrics(self):
        metrics = MetricsAccumulator()
        outputs, targets = make_outputs_targets()
        metrics.update(outputs, targets)
        result = metrics.compute()
        self.assertEqual(result["ability"]["count"], 3)
        self.assertEqual(result["ability"]["top1"], 1.0)
        self.assertEqual(result["ability"]["top5"], 1.0)
        self.assertEqual(result["ability"]["per_ability"]["1"]["target"], 1)
        self.assertEqual(result["ability"]["per_ability"]["1"]["correct"], 1)
        self.assertEqual(result["kind"]["accuracy"], 1.0)
        self.assertAlmostEqual(result["selection"]["precision"], 1.0)
        self.assertAlmostEqual(result["selection"]["recall"], 0.5)
        self.assertAlmostEqual(result["selection"]["f1"], 2 / 3)
        self.assertEqual(result["selection"]["exact_match"], 0.0)
        self.assertEqual(result["selection"]["mean_predicted"], 1.0)
        self.assertEqual(result["selection"]["mean_target"], 2.0)
        self.assertEqual(result["target_kind"]["accuracy"], 1.0)
        self.assertEqual(result["target_unit"]["accuracy"], 1.0)
        self.assertEqual(result["target_point"]["rows"], 0)
        self.assertEqual(result["queue"]["accuracy"], 1.0)
        self.assertEqual(result["delay"]["rows"], 1)
        self.assertAlmostEqual(result["delay"]["mae"], 0.0)
        self.assertAlmostEqual(result["delay"]["median"], 0.0)

    def test_counts_accumulate(self):
        metrics = MetricsAccumulator()
        outputs, targets = make_outputs_targets()
        metrics.update(outputs, targets)
        metrics.update(outputs, targets)
        result = metrics.compute()
        self.assertEqual(result["ability"]["rows"], 2)
        self.assertEqual(result["ability"]["per_ability"]["1"]["target"], 2)
        self.assertEqual(result["ability"]["per_ability"]["1"]["correct"], 2)
        self.assertEqual(result["selection"]["rows"], 2)
        self.assertAlmostEqual(result["selection"]["f1"], 2 / 3)
        self.assertEqual(result["delay"]["rows"], 2)

    def test_masks_remove_inactive_rows(self):
        metrics = MetricsAccumulator()
        outputs, targets = make_outputs_targets()
        targets["target_kind"] = torch.tensor([[0]])
        targets["queue_mask"] = torch.tensor([[False]])
        targets["delay_mask"] = torch.tensor([[False]])
        metrics.update(outputs, targets)
        result = metrics.compute()
        self.assertEqual(result["target_unit"]["rows"], 0)
        self.assertEqual(result["target_point"]["rows"], 0)
        self.assertEqual(result["queue"]["rows"], 0)
        self.assertEqual(result["delay"]["rows"], 0)
        self.assertEqual(result["queue"]["accuracy"], 0.0)
        self.assertEqual(result["delay"]["mae"], 0.0)


if __name__ == "__main__":
    unittest.main()
