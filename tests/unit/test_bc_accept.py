import hashlib
from pathlib import Path
import tempfile
import unittest

from scv_star.data.bc_accept import verify_resource


class ResourcePinTests(unittest.TestCase):
    def test_exact_resource_hash_is_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            content = b"Synthetic map fixture, not a game resource"
            digest = hashlib.sha256(content).hexdigest()
            handle = b"s2maUS  " + bytes.fromhex(digest)
            path = root / digest[:2] / digest[2:4] / (digest + ".s2ma")
            path.parent.mkdir(parents=True)
            path.write_bytes(content)
            self.assertEqual(verify_resource(handle, root, {})["sha256"], digest)
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "corrupt"):
                verify_resource(handle, root, {})

    def test_invalid_handle_cannot_be_treated_as_a_valid_map(self):
        with self.assertRaisesRegex(ValueError, "length"):
            verify_resource(b"bad", Path("."), {})
        with self.assertRaisesRegex(ValueError, "type"):
            verify_resource(b"xxxxUS  " + bytes(32), Path("."), {})


if __name__ == "__main__":
    unittest.main()
