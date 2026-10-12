"""Losslessly recompress completed shards before freezing a training manifest."""

import argparse
import gzip
import hashlib
import json
import lzma
import os
from pathlib import Path
import tempfile
import time

from scv_star.envs.sc2_client import sha256
from scv_star.data.bc_storage import open_shard


def replace_when_unlocked(source, target):
    for attempt in range(100):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.05)


def unpacked_hash(path):
    digest = hashlib.sha256()
    # Temporary files retain neither compression extension nor original name.
    with Path(path).open("rb") as raw_stream:
        magic = raw_stream.read(6)
    opener = lzma.open if magic == b"\xfd7zXZ\x00" else gzip.open
    with opener(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def repack_report(path, codec="gzip"):
    if codec not in ("gzip", "xz"):
        raise ValueError("Unsupported output compression")
    path = Path(path)
    if (path.parent.parent / "manifest.json").exists():
        raise ValueError("Frozen dataset must not be modified")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report["status"] != "extracted" or not report.get("cleanup", {}).get("exited"):
        return 0
    saved = 0
    for view in report["views"]:
        prior = view.get("lossless_repack")
        if prior and (codec == "gzip" or prior.get("codec", "gzip") == codec):
            continue
        shard = path.parent / view["shard"]
        if sha256(shard) != view["shard_sha256"]:
            raise ValueError("Original shard hash mismatch")
        before_bytes = shard.stat().st_size
        fd, temporary = tempfile.mkstemp(prefix=shard.name + ".repack-", dir=path.parent)
        os.close(fd)
        temporary = Path(temporary)
        try:
            digest = hashlib.sha256()
            with open_shard(shard, "rb") as reader, temporary.open("wb") as raw_writer:
                compressed_writer = (
                    lzma.LZMAFile(raw_writer, mode="wb", preset=3)
                    if codec == "xz"
                    else gzip.GzipFile(
                        filename="", mode="wb", fileobj=raw_writer, compresslevel=9, mtime=0
                    )
                )
                with compressed_writer as writer:
                    for block in iter(lambda: reader.read(1024 * 1024), b""):
                        digest.update(block)
                        writer.write(block)
            if unpacked_hash(temporary) != digest.hexdigest():
                raise ValueError("Recompression changed decompressed content")
            replacement_hash = sha256(temporary)
            after_bytes = temporary.stat().st_size
            record = {
                "original_sha256": view["shard_sha256"],
                "uncompressed_sha256": digest.hexdigest(),
                "before_bytes": before_bytes,
                "after_bytes": after_bytes,
                "codec": codec,
                "compression_level": 3 if codec == "xz" else 9,
                "replacement_sha256": replacement_hash,
            }
            if prior:
                record["previous_repack"] = prior
            # Keep recovery evidence before replacement; acceptance refuses any
            # interrupted hash mismatch instead of accepting a half-updated file.
            evidence = path.parent / (view["shard"] + ".repack.json")
            evidence.write_text(json.dumps(record, indent=2), encoding="utf-8")
            target = shard.with_suffix(".xz" if codec == "xz" else ".gz")
            if target != shard and target.exists():
                raise ValueError("Replacement shard already exists; inspect interrupted repack")
            temporary.replace(target)
            view["shard"] = target.name
            view["shard_sha256"] = replacement_hash
            view["lossless_repack"] = record
            next_report = path.with_suffix(".repack-next.json")
            next_report.write_text(json.dumps(report, indent=2), encoding="utf-8")
            replace_when_unlocked(next_report, path)
            if target != shard:
                shard.unlink()
            saved += before_bytes - after_bytes
        finally:
            temporary.unlink(missing_ok=True)
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--codec", choices=("gzip", "xz"), default="gzip")
    args = parser.parse_args()
    total = 0
    for report in sorted(args.root.glob("*/report.json")):
        saved = repack_report(report, args.codec)
        total += saved
        if saved:
            print(json.dumps({"replay": report.parent.name, "saved_bytes": saved}), flush=True)
    print(json.dumps({"total_saved_bytes": total}), flush=True)


if __name__ == "__main__":
    main()
