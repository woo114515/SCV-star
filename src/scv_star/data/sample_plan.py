"""Plan one random eligible replay per engine/map/source stratum; do not run SC2."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import random

from scv_star.data.collection_archive import lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Output already exists")
    groups = defaultdict(list)
    for row in lines(args.catalog):
        if row["queue"] == "tvt_candidates" and row["screening_status"] == "eligible":
            key = (row["identity"]["base_build"], row["map_name"], row["collection_source"])
            groups[key].append(row)
    rng = random.Random(args.seed)
    selected = []
    for key, rows in sorted(groups.items()):
        row = rng.choice(sorted(rows, key=lambda r: r["sha256"]))
        selected.append(
            {
                "stratum": key,
                "population": len(rows),
                "sha256": row["sha256"],
                "local_replay": row["local_replay"],
                "identity": row["identity"],
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {"seed": args.seed, "mode": "one_per_eligible_stratum", "tasks": selected}, indent=2
        ),
        encoding="utf-8",
    )
    print(json.dumps({"strata": len(groups), "selected": len(selected), "executed": 0}))


if __name__ == "__main__":
    main()
