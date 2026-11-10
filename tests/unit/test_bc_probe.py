import unittest

import torch

from scv_star.data.bc_probe import _fit, aggregate_features


class ProbeTests(unittest.TestCase):
    def test_aggregate_features_respects_the_entity_mask(self):
        entities = torch.tensor(
            [
                [[1.0, 2.0], [3.0, 4.0], [100.0, 100.0]],
                [[5.0, 6.0], [0.0, 0.0], [0.0, 0.0]],
            ]
        )
        mask = torch.tensor([[True, True, False], [True, False, False]])
        scalars = torch.zeros(2, 3)
        features = aggregate_features(entities, mask, scalars)
        self.assertEqual(features.shape, (2, 3 + 2 + 2 + 1))
        self.assertAlmostEqual(float(features[0, 3]), 2.0)
        self.assertAlmostEqual(float(features[0, 4]), 3.0)
        self.assertAlmostEqual(float(features[0, 5]), 3.0)
        self.assertAlmostEqual(float(features[0, 6]), 4.0)
        self.assertAlmostEqual(float(features[0, 7]), 2.0)
        self.assertAlmostEqual(float(features[1, 3]), 5.0)
        self.assertAlmostEqual(float(features[1, 5]), 5.0)
        self.assertAlmostEqual(float(features[1, 7]), 1.0)

    def test_fit_separates_a_learnable_problem(self):
        torch.manual_seed(0)
        centres = torch.tensor([[5.0, 0.0], [0.0, 5.0], [-5.0, 0.0]])
        labels = torch.randint(0, 3, (300,))
        features = centres[labels] + torch.randn(300, 2)
        accuracy, loss = _fit(features, labels, 3, hidden=32, steps=300, learning_rate=0.05)
        self.assertGreater(accuracy, 0.97)
        self.assertLess(loss, 0.2)

        linear_accuracy, _ = _fit(features, labels, 3, steps=300, learning_rate=0.05)
        self.assertLess(linear_accuracy, accuracy)


if __name__ == "__main__":
    unittest.main()
