import gzip
import json
from pathlib import Path
import tempfile
import unittest

from scv_star.data.bc_repack import repack_report
from scv_star.data.bc_storage import open_shard
from scv_star.envs.sc2_client import sha256


class RepackTests(unittest.TestCase):
    def test_lossless_and_idempotent_before_freeze(self):
        with tempfile.TemporaryDirectory() as temp:
            dataset = Path(temp)
            replay = dataset / "replay"
            replay.mkdir()
            content = b'{"label":123,"observation":[0,1,2]}\n' * 1000
            shard = replay / "player-1.jsonl.gz"
            with gzip.open(shard, "wb", compresslevel=1) as writer:
                writer.write(content)
            report = replay / "report.json"
            report.write_text(
                json.dumps(
                    {
                        "status": "extracted",
                        "cleanup": {"exited": True},
                        "views": [{"shard": shard.name, "shard_sha256": sha256(shard)}],
                    }
                )
            )
            self.assertGreater(repack_report(report), 0)
            with gzip.open(shard, "rb") as reader:
                self.assertEqual(reader.read(), content)
            self.assertEqual(
                json.loads(report.read_text())["views"][0]["shard_sha256"], sha256(shard)
            )
            self.assertEqual(repack_report(report), 0)
            repack_report(report, codec="xz")
            updated = json.loads(report.read_text())["views"][0]
            with open_shard(replay / updated["shard"], "rb") as stream:
                self.assertEqual(stream.read(), content)
            self.assertFalse(shard.exists())
            self.assertEqual(sha256(replay / updated["shard"]), updated["shard_sha256"])
            self.assertEqual(repack_report(report, codec="xz"), 0)
            (dataset / "manifest.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "Frozen"):
                repack_report(report)


if __name__ == "__main__":
    unittest.main()
