"""Read-only verification of previously recorded replay resource downloads."""

import argparse
import json
from pathlib import Path

from scv_star.envs.sc2_client import sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a new output path to preserve previous evidence")
    records = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    results = []
    for record in records:
        path = Path(record["path"])
        status = "missing"
        if path.is_file():
            status = "verified" if sha256(path) == record["sha256"] else "hash_mismatch"
        results.append({"path": str(path), "sha256": record["sha256"], "status": status})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    verified = sum(r["status"] == "verified" for r in results)
    print(json.dumps({"resources": len(results), "verified": verified}))
    if verified != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
