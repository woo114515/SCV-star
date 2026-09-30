"""Bounded, resumable downloads from an explicit public-file manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

MAX_FILE_BYTES = 500_000_000
MAX_BATCH_BYTES = 10_000_000_000


def destination(root: Path, name: str) -> Path:
    """Do not let remote filenames escape the selected download directory."""
    if not name or Path(name).name != name or any(c in name for c in "/\\:"):
        raise ValueError("Unsafe download filename")
    target = (root / name).resolve()
    if target.parent != root.resolve():
        raise ValueError("Download path escapes root")
    return target


def hashes(path: Path) -> dict:
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with path.open("rb") as stream:
        while data := stream.read(1024 * 1024):
            sha.update(data)
            md5.update(data)
    return {"sha256": sha.hexdigest(), "md5": md5.hexdigest(), "bytes": path.stat().st_size}


def validate(path: Path, item: dict) -> dict:
    result = hashes(path)
    if result["bytes"] > min(item.get("max_bytes", MAX_FILE_BYTES), MAX_FILE_BYTES):
        raise ValueError("File exceeds download limit")
    if item.get("size") is not None and result["bytes"] != item["size"]:
        raise ValueError("Download size mismatch")
    if item.get("md5") and result["md5"] != item["md5"]:
        raise ValueError("Download checksum mismatch")
    return result


def fetch(item: dict, root: Path) -> dict:
    target = destination(root, item["name"])
    result = {"name": item["name"], "url": item["url"], "path": str(target)}
    try:
        if target.exists():
            return dict(result, **validate(target, item), status="verified_existing")
        part = target.with_name(target.name + ".part")
        limit = min(item.get("max_bytes", MAX_FILE_BYTES), MAX_FILE_BYTES)
        if limit <= 0 or (item.get("size") or 0) > limit:
            raise ValueError("File exceeds download limit")
        limit = item.get("size") or limit
        if part.exists() and item.get("size") == part.stat().st_size:
            checked = validate(part, item)
            part.replace(target)
            return dict(result, **checked, status="resumed_complete")
        for attempt in range(4):
            try:
                offset = part.stat().st_size if part.exists() else 0
                headers = {"User-Agent": "SCV-star/0.1 public replay research"}
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                request = urllib.request.Request(item["url"], headers=headers)
                with urllib.request.urlopen(request, timeout=45) as response:
                    append = offset > 0 and response.status == 206
                    if append and not response.headers.get("Content-Range", "").startswith(
                        f"bytes {offset}-"
                    ):
                        raise ValueError("Invalid resume Content-Range")
                    total = offset if append else 0
                    length = response.headers.get("Content-Length")
                    expected_total = int(length) + total if length is not None else None
                    if expected_total is not None and expected_total > limit:
                        raise ValueError("Response exceeds file limit")
                    if "text/html" in response.headers.get("Content-Type", ""):
                        raise ValueError("HTML response instead of downloadable file")
                    with part.open("ab" if append else "wb") as stream:
                        while data := response.read(1024 * 1024):
                            total += len(data)
                            if total > limit or shutil.disk_usage(root).free < 20_000_000_000:
                                raise ValueError("Storage limit reached")
                            stream.write(data)
                    if expected_total is not None and total != expected_total:
                        raise OSError("Incomplete response body; retain partial download")
                checked = validate(part, item)
                part.replace(target)
                return dict(result, **checked, status="downloaded")
            except ValueError:
                raise
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 ** (attempt + 1))
    except Exception as error:
        return dict(result, status="failed", error=f"{type(error).__name__}: {error}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=2)
    args = parser.parse_args()
    items = json.loads(args.manifest.read_text(encoding="utf-8"))
    if len({i["name"] for i in items}) != len(items):
        raise ValueError("Duplicate destination names")
    args.output.mkdir(parents=True, exist_ok=True)
    for item in items:
        destination(args.output, item["name"])
    budget = sum(
        i.get("size") or min(i.get("max_bytes", MAX_FILE_BYTES), MAX_FILE_BYTES) for i in items
    )
    project_bytes = sum(
        (Path(base) / name).stat().st_size
        for base, _, names in os.walk(Path.cwd())
        for name in names
    )
    if budget > MAX_BATCH_BYTES or project_bytes + budget > 80_000_000_000:
        raise ValueError("Batch exceeds conservative 80 GB project guard")
    if shutil.disk_usage(args.output).free < budget + 20_000_000_000:
        raise ValueError("Insufficient free disk space")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    print(
        json.dumps(
            {"files": len(items), "budget_bytes": budget, "project_bytes_before": project_bytes}
        ),
        flush=True,
    )
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch, item, args.output) for item in items]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            args.report.write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(json.dumps({"finished": len(results), "total": len(items), **result}), flush=True)
    if any(r["status"] == "failed" for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
