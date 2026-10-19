import os
import unittest

from scv_star.runtime.host_memory import windows_memory_info


class HostMemoryTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows native memory counters")
    def test_commit_counters_are_self_consistent_bytes(self):
        info = windows_memory_info()
        self.assertGreater(info["commit_limit_bytes"], 0)
        self.assertGreater(info["physical_available_bytes"], 0)
        self.assertEqual(
            info["commit_available_bytes"] + info["commit_used_bytes"],
            info["commit_limit_bytes"],
        )


if __name__ == "__main__":
    unittest.main()
