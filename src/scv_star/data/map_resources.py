"""Resolve replay map dependencies using only Blizzard's hash-addressed depot."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import mpyq
from s2protocol import versions


MAX_OBJECT_BYTES = 100_000_000
REQUEST_TIMEOUT_SECONDS = 45
REGIONS = ("eu", "us", "kr")
OFFICIAL_HOSTS = frozenset(f"{region}-s2-depot.classic.blizzard.com" for region in REGIONS)


class ResourceDownloadError(RuntimeError):
    """Every official region failed to return a valid referenced object."""

    def __init__(self, resource: str, attempts: list[dict]):
        self.attempts = attempts
        super().__init__(f"Cannot retrieve {resource}: {attempts}")


def _check_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in OFFICIAL_HOSTS
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("Resource URL must use an official HTTPS depot")


class _OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_response(url: str):
    _check_url(url)
    return build_opener(_OfficialRedirects()).open(Request(url), timeout=REQUEST_TIMEOUT_SECONDS)


def _decode_handle(handle: bytes) -> tuple[str, str]:
    if not isinstance(handle, bytes) or len(handle) != 40:
        raise ValueError("Replay resource handle must contain exactly 40 bytes")
    try:
        extension = handle[:4].decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("Invalid replay resource extension") from error
    if extension not in ("s2ma", "s2mv"):
        raise ValueError("Replay resource extension must be s2ma or s2mv")
    return handle[8:].hex(), extension


def _cached_record(path: Path, digest: str, extension: str) -> dict | None:
    try:
        with path.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            if size > MAX_OBJECT_BYTES:
                raise ValueError(f"Existing map resource exceeds size limit: {path}")
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
    except FileNotFoundError:
        return None
    if actual != digest:
        raise ValueError(f"Existing map resource has wrong SHA-256; not overwritten: {path}")
    return {
        "sha256": digest,
        "extension": extension,
        "bytes": size,
        "path": str(path),
        "cached": True,
    }


def _download_region(url: str, path: Path, digest: str, extension: str) -> dict:
    temporary = None
    try:
        with _open_response(url) as response:
            status = getattr(response, "status", None)
            if status != 200:
                raise ValueError(f"Unexpected resource HTTP status: {status}")
            _check_url(response.geturl())
            length_text = response.headers.get("Content-Length")
            length = None if length_text is None else int(length_text)
            if length is not None and (length < 0 or length > MAX_OBJECT_BYTES):
                raise ValueError("Resource Content-Length exceeds allowed range")
            path.parent.mkdir(parents=True, exist_ok=True)
            digest_state = hashlib.sha256()
            size = 0
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{digest}.", suffix=".part", dir=path.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                while chunk := response.read(min(1_048_576, MAX_OBJECT_BYTES - size + 1)):
                    size += len(chunk)
                    if size > MAX_OBJECT_BYTES:
                        raise ValueError("Resource body exceeds size limit")
                    stream.write(chunk)
                    digest_state.update(chunk)
                if length is not None and size != length:
                    raise ValueError("Resource body does not match Content-Length")
                if digest_state.hexdigest() != digest:
                    raise ValueError("Resource SHA-256 does not match replay reference")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Link a completed temporary inode, never an unfinished body. Unlike
                # replace(), this atomic publication cannot overwrite another writer.
                # Removing the temporary name immediately leaves one cache object.
                os.link(temporary, path)
            except FileExistsError:
                concurrent = _cached_record(path, digest, extension)
                if concurrent is None:
                    raise RuntimeError("Concurrent cache publication disappeared")
                return concurrent
            return {
                "sha256": digest,
                "extension": extension,
                "bytes": size,
                "path": str(path),
                "cached": False,
                "url": url,
            }
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def ensure_replay_resources(
    path: Path,
    build: int,
    cache_root: Path | None = None,
    protocol_build: int | None = None,
    download: bool = False,
) -> list[dict]:
    """Verify exact map/module references, optionally acquiring missing objects.

    ``protocol_build`` is an explicit override for static replay.details decoding
    only (for example CN metadata supported by protocol 97563). It does not assert
    action/event compatibility or change the engine build. Without it the exact
    ``build`` protocol is required; no nearest-version fallback is attempted.
    A corrupt existing cache entry raises and is never replaced.
    """
    if cache_root is None:
        program_data = os.environ.get("PROGRAMDATA")
        if not program_data:
            raise ValueError("Pass cache_root when PROGRAMDATA is unavailable")
        cache_root = Path(program_data) / "Blizzard Entertainment/Battle.net/Cache"
    cache_root = Path(cache_root)
    protocol = versions.build(build if protocol_build is None else protocol_build)
    archive = mpyq.MPQArchive(str(path))
    try:
        details = protocol.decode_replay_details(archive.read_file("replay.details"))
        handles = details.get("m_cacheHandles", [])
    finally:
        archive.file.close()
    if not handles:
        raise ValueError("Replay has no exact map resource references")
    # Validate every handle before creating directories or contacting a depot.
    resources = list(dict.fromkeys(_decode_handle(handle) for handle in handles))
    results = []
    for digest, extension in resources:
        destination = cache_root / digest[:2] / digest[2:4] / f"{digest}.{extension}"
        cached = _cached_record(destination, digest, extension)
        if cached is not None:
            results.append(cached)
            continue
        if not download:
            raise FileNotFoundError(f"Missing map resource: {destination}")
        attempts = []
        for region in REGIONS:
            url = f"https://{region}-s2-depot.classic.blizzard.com/{digest}.{extension}"
            try:
                result = _download_region(url, destination, digest, extension)
            except (OSError, URLError, ValueError) as error:
                # An independently published corrupt object must remain visible as
                # an explicit error, rather than becoming a retry/overwrite target.
                _cached_record(destination, digest, extension)
                attempts.append({"url": url, "error": f"{type(error).__name__}: {error}"})
            else:
                results.append(result)
                break
        else:
            raise ResourceDownloadError(f"{digest}.{extension}", attempts)
    return results
