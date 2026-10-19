"""Resource acquisition is hash-pinned, bounded and safe under concurrent writers."""

import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request

from scv_star.data import map_resources as resources


def handle(data=b"map bytes", extension="s2ma"):
    return extension.encode("ascii") + b"\x00\x00EU" + hashlib.sha256(data).digest()


class Response(io.BytesIO):
    status = 200

    def __init__(self, data, length=None, url=None):
        super().__init__(data)
        self.headers = {} if length is None else {"Content-Length": str(length)}
        self.url = url or "https://eu-s2-depot.classic.blizzard.com/map.s2ma"
        self.read_calls = 0

    def read(self, size=-1):
        self.read_calls += 1
        return super().read(size)

    def geturl(self):
        return self.url


class MapResourceTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.cache = Path(self.directory) / "cache"
        self.protocol_build = self.enterContext(patch.object(resources.versions, "build"))
        self.archive = self.enterContext(patch.object(resources.mpyq, "MPQArchive"))
        self.open_response = resources._open_response
        self.network = self.enterContext(
            patch.object(resources, "_open_response", side_effect=AssertionError("No network"))
        )
        self.details = self.protocol_build.return_value.decode_replay_details
        self.details.return_value = {"m_cacheHandles": [handle()]}

    def ensure(self, **kwargs):
        return resources.ensure_replay_resources("test.SC2Replay", 86383, self.cache, **kwargs)

    def destination(self, data=b"map bytes", extension="s2ma"):
        digest = hashlib.sha256(data).hexdigest()
        return self.cache / digest[:2] / digest[2:4] / f"{digest}.{extension}"

    def write_cached(self, data=b"map bytes"):
        path = self.destination()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def assert_no_payloads(self):
        self.assertFalse(any(p.is_file() for p in Path(self.directory).rglob("*")))

    def test_verified_cache_deduplicates_handles_and_uses_exact_protocol(self):
        path = self.write_cached()
        self.details.return_value = {"m_cacheHandles": [handle(), handle()]}
        result = self.ensure()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["path"], str(path))
        self.assertEqual(result[0]["bytes"], 9)
        self.assertTrue(result[0]["cached"])
        self.protocol_build.assert_called_once_with(86383)
        self.archive.return_value.file.close.assert_called_once()
        self.network.assert_not_called()

    def test_protocol_override_is_explicit_and_missing_read_only_does_not_write(self):
        with self.assertRaises(FileNotFoundError):
            self.ensure(protocol_build=97563)
        self.protocol_build.assert_called_once_with(97563)
        self.assertFalse(self.cache.exists())
        self.network.assert_not_called()

    def test_existing_corrupt_cache_is_reported_and_never_overwritten(self):
        path = self.write_cached(b"corrupt")
        with self.assertRaisesRegex(ValueError, "wrong SHA-256"):
            self.ensure(download=True)
        self.assertEqual(path.read_bytes(), b"corrupt")
        self.network.assert_not_called()

    def test_valid_download_streams_and_publishes_s2mv(self):
        data = b"module bytes"
        self.details.return_value = {"m_cacheHandles": [handle(data, "s2mv")]}
        self.network.side_effect = None
        self.network.return_value = Response(data, len(data))
        result = self.ensure(download=True)[0]
        self.assertFalse(result["cached"])
        self.assertEqual(result["extension"], "s2mv")
        self.assertEqual(self.destination(data, "s2mv").read_bytes(), data)
        self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
        self.assertTrue(result["url"].endswith(result["sha256"] + ".s2mv"))
        self.assertFalse(list(self.cache.rglob("*.part")))

    def test_wrong_sha_rejects_all_regions_without_publishing(self):
        self.network.side_effect = lambda url: Response(b"bad map")
        with self.assertRaises(resources.ResourceDownloadError) as error:
            self.ensure(download=True)
        self.assertEqual(len(error.exception.attempts), 3)
        self.assertIn("SHA-256", str(error.exception))
        self.assert_no_payloads()

    def test_oversized_content_length_is_rejected_before_read(self):
        responses = [Response(b"", resources.MAX_OBJECT_BYTES + 1) for _ in range(3)]
        self.network.side_effect = responses
        with self.assertRaises(resources.ResourceDownloadError):
            self.ensure(download=True)
        self.assertTrue(all(response.read_calls == 0 for response in responses))
        self.assertFalse(self.cache.exists())

    def test_oversized_body_without_content_length_is_bounded(self):
        self.network.side_effect = lambda url: Response(b"four")
        with patch.object(resources, "MAX_OBJECT_BYTES", 3):
            with self.assertRaises(resources.ResourceDownloadError) as error:
                self.ensure(download=True)
        self.assertIn("body exceeds size limit", str(error.exception))
        self.assert_no_payloads()

    def test_content_length_must_equal_body_length(self):
        self.network.side_effect = lambda url: Response(b"map bytes", 8)
        with self.assertRaises(resources.ResourceDownloadError) as error:
            self.ensure(download=True)
        self.assertIn("does not match Content-Length", str(error.exception))
        self.assert_no_payloads()

    def test_region_fallback_reaches_kr_after_eu_and_us_404(self):
        self.network.side_effect = [
            HTTPError("eu", 404, "not found", {}, None),
            HTTPError("us", 404, "not found", {}, None),
            Response(b"map bytes"),
        ]
        result = self.ensure(download=True)[0]
        self.assertIn("kr-s2-depot.classic.blizzard.com", result["url"])
        self.assertEqual(
            [call.args[0].split("/")[2].split("-")[0] for call in self.network.call_args_list],
            ["eu", "us", "kr"],
        )

    def test_invalid_handle_prevents_any_download_or_cache_write(self):
        for invalid in [b"short", b"evil" + b"\x00" * 36, "not bytes", b"\xff" * 40]:
            with self.subTest(invalid=repr(invalid)):
                self.details.return_value = {"m_cacheHandles": [handle(), invalid]}
                with self.assertRaises(ValueError):
                    self.ensure(download=True)
        self.network.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_empty_handles_rejected(self):
        self.details.return_value = {"m_cacheHandles": []}
        with self.assertRaisesRegex(ValueError, "no exact"):
            self.ensure(download=True)
        self.network.assert_not_called()

    def test_simultaneous_valid_publisher_is_verified_without_overwrite(self):
        self.network.side_effect = None
        self.network.return_value = Response(b"map bytes")

        def concurrent_publish(temporary, destination):
            Path(destination).write_bytes(b"map bytes")
            raise FileExistsError(destination)

        with patch.object(resources.os, "link", side_effect=concurrent_publish):
            result = self.ensure(download=True)[0]
        self.assertTrue(result["cached"])
        self.assertEqual(self.destination().read_bytes(), b"map bytes")
        self.assertFalse(list(self.cache.rglob("*.part")))

    def test_simultaneous_corrupt_publisher_raises_without_overwrite(self):
        self.network.side_effect = None
        self.network.return_value = Response(b"map bytes")

        def concurrent_publish(temporary, destination):
            Path(destination).write_bytes(b"corrupt")
            raise FileExistsError(destination)

        with patch.object(resources.os, "link", side_effect=concurrent_publish):
            with self.assertRaisesRegex(ValueError, "wrong SHA-256"):
                self.ensure(download=True)
        self.assertEqual(self.destination().read_bytes(), b"corrupt")
        self.assertEqual(self.network.call_count, 1)
        self.assertFalse(list(self.cache.rglob("*.part")))

    def test_redirect_to_unofficial_host_is_rejected_before_following(self):
        request = Request("https://eu-s2-depot.classic.blizzard.com/a.s2ma")
        with self.assertRaisesRegex(ValueError, "official HTTPS"):
            resources._OfficialRedirects().redirect_request(
                request, None, 302, "moved", {}, "https://example.org/a.s2ma"
            )

    def test_network_timeout_is_fixed(self):
        opener = MagicMock()
        with patch.object(resources, "build_opener", return_value=opener):
            self.open_response("https://eu-s2-depot.classic.blizzard.com/a.s2ma")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 45)


if __name__ == "__main__":
    unittest.main()
