"""Lossless stream formats supported by the BC dataset contract."""

import gzip
import lzma
from pathlib import Path


def open_shard(path, mode="rt"):
    path = Path(path)
    if path.suffix == ".gz":
        opener = gzip.open
    elif path.suffix == ".xz":
        opener = lzma.open
    else:
        raise ValueError(f"Unsupported shard compression: {path.suffix}")
    return opener(path, mode, **({"encoding": "utf-8"} if "t" in mode else {}))
