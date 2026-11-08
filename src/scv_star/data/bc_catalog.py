"""Canonical per-build ability catalog shared by cache builds and checkpoints."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CATALOG_SCHEMA = "tvt-bc-catalog-v1"
COLLECTION_SCHEMA = "tvt-bc-collection-v1"
ROW_SCHEMA = "tvt-bc-v2"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def catalog_digest(catalog: dict) -> str:
    """Stable hash of the catalog document independent of key order."""
    encoded = json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)


def build_ability_catalog(collection_path, build):
    """Intersect accepted child catalogs for one build and verify observed labels.

    Child ``ability_ids`` are captured from per-replay game data, so they can differ
    by map/mod even when the build and DataVersion match. The intersection is the
    canonical vocabulary for that build; every observed action must be inside it.
    """
    collection_path = Path(collection_path).resolve()
    collection_bytes = collection_path.read_bytes()
    collection = json.loads(collection_bytes.decode("utf-8"))
    if collection.get("schema") != COLLECTION_SCHEMA or not collection.get("training_ready"):
        raise ValueError("Expected an accepted collection")
    if collection.get("row_schema") != ROW_SCHEMA:
        raise ValueError("Unexpected collection row schema")
    build = int(build)
    datasets = [entry for entry in collection["datasets"] if entry["engine"]["base_build"] == build]
    if not datasets:
        raise ValueError(f"No accepted datasets for build {build}")

    catalogs, observed = [], set()
    for entry in datasets:
        manifest = (collection_path.parent / entry["manifest"]).resolve()
        if sha256_file(manifest) != entry["manifest_sha256"]:
            raise ValueError(f"Child manifest hash mismatch: {manifest}")
        child = json.loads(manifest.read_text(encoding="utf-8"))
        if child.get("schema") != ROW_SCHEMA or not child.get("training_ready"):
            raise ValueError(f"Child dataset is not accepted: {manifest}")
        if child.get("engine") != entry["engine"] or child.get("engine_base_build") != build:
            raise ValueError(f"Child engine identity mismatch: {manifest}")
        ability_ids = [int(value) for value in child["ability_ids"]]
        if len(ability_ids) != len(set(ability_ids)):
            raise ValueError(f"Duplicate child ability IDs: {manifest}")
        catalogs.append(set(ability_ids))
        observed.update(int(value) for value in child.get("observed_ability_counts", {}))

    catalog = set.intersection(*catalogs)
    if not catalog:
        raise ValueError(f"Empty canonical catalog for build {build}")
    missing = sorted(observed - catalog)
    if missing:
        raise ValueError(
            f"Observed abilities outside canonical catalog for build {build}: {missing}"
        )
    return {
        "schema": CATALOG_SCHEMA,
        "build": build,
        "ability_ids": sorted(catalog),
        "observed_ability_ids": sorted(observed),
        "collection_sha256": sha256_bytes(collection_bytes),
        "datasets": len(datasets),
    }
