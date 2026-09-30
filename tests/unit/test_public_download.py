"""Acquisition must reject path traversal, corrupt bytes and oversized responses."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from scv_star.data.public_download import destination, fetch, validate


class DownloadGuardsTests(unittest.TestCase):
    def test_complete_partial_is_verified_before_promotion_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.zip.part").write_bytes(b"abc")
            item = {
                "name": "a.zip",
                "url": "https://example.org/a",
                "size": 3,
                "md5": hashlib.md5(b"abc").hexdigest(),
            }
            with patch("urllib.request.urlopen") as request:
                result = fetch(item, root)
            request.assert_not_called()
            self.assertEqual(result["status"], "resumed_complete")
            self.assertEqual((root / "a.zip").read_bytes(), b"abc")

    def test_wrong_resume_offset_preserves_existing_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            part = root / "a.zip.part"
            part.write_bytes(b"ab")
            response = MagicMock()
            response.status = 206
            response.headers = {"Content-Range": "bytes 1-2/3", "Content-Length": "1"}
            response.__enter__.return_value = response
            with patch("urllib.request.urlopen", return_value=response):
                result = fetch({"name": "a.zip", "url": "https://example.org/a", "size": 3}, root)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(part.read_bytes(), b"ab")
            response.read.assert_not_called()

    def test_remote_paths_cannot_escape_download_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ["../bad.zip", "a/b.zip", r"a\b.zip", "C:bad.zip", "..", ""]:
                with self.assertRaises(ValueError):
                    destination(Path(tmp), name)

    def test_existing_corrupt_file_is_rejected_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            path.write_bytes(b"bad")
            item = {
                "name": path.name,
                "url": "https://example.org/a",
                "size": 3,
                "md5": hashlib.md5(b"yes").hexdigest(),
            }
            with patch("urllib.request.urlopen") as request:
                result = fetch(item, Path(tmp))
            self.assertEqual(result["status"], "failed")
            request.assert_not_called()
            self.assertEqual(path.read_bytes(), b"bad")

    def test_oversized_response_is_rejected_before_reading(self):
        with tempfile.TemporaryDirectory() as tmp:
            response = MagicMock()
            response.headers = {"Content-Length": "100"}
            response.status = 200
            response.__enter__.return_value = response
            with patch("urllib.request.urlopen", return_value=response):
                result = fetch(
                    {"name": "a.zip", "url": "https://example.org/a", "size": 10}, Path(tmp)
                )
            self.assertEqual(result["status"], "failed")
            response.read.assert_not_called()
            self.assertFalse((Path(tmp) / "a.zip").exists())

    def test_checksum_and_size_both_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a"
            path.write_bytes(b"abc")
            with self.assertRaisesRegex(ValueError, "size"):
                validate(path, {"size": 4, "md5": hashlib.md5(b"abc").hexdigest()})
