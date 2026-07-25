"""The static files: icons, the manifest, the offline page and the service worker.

Three things about how these are served are deliberate.

**No `StaticFiles` mount.** `web/app.py` records that no code path in this layer may build
a `Path` out of request data, and a mount would make that false — it resolves a request
string against a directory. Here the request string is only ever a *key* into the table
below, and every filesystem path is `STATIC_DIR` joined with a literal written in this
file. A request for something not in the table is a miss, so `..`, encoded separators and
absolute paths are all simply 404s rather than something to defend against. A mount also
cannot set `Service-Worker-Allowed`, and it takes its content types from `mimetypes`,
whose `.webmanifest` entry is not present on a bare `python:3.12-slim` — a manifest served
as `application/octet-stream` is silently ignored by the browser, which is the worst of
both worlds.

**Read once, at startup.** These files do not change while the process runs. Reading them
eagerly also means a file that has been deleted or replaced with something unreadable is
absent from the table and therefore a clean 404, rather than a 500 raised from inside a
request. Someone dropping in their own icons is the expected case, and half-doing it
should degrade, not crash.

**The cache version is a hash of the bytes, not a version number.** The package version
has never been bumped, so keying the service worker's cache on it would mean the cache is
never invalidated. Hashing the assets themselves means replacing an icon changes the
served `sw.js`, which is what makes the browser install a new worker and drop the old
cache — the swap-in-your-own-artwork path works with no manual step on any client.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: Built from this module's location, never from a request.
STATIC_DIR = Path(__file__).parent / "static"
ICONS_DIR = STATIC_DIR / "icons"

MANIFEST_PATH = "/manifest.webmanifest"
OFFLINE_PATH = "/offline.html"
SERVICE_WORKER_PATH = "/sw.js"
ICONS_PREFIX = "/static/icons/"

#: The complete set of files reachable under `/static/icons/`, with the type each is served
#: as. A name outside this mapping does not exist as far as the app is concerned.
ICON_TYPES = {
    "icon-192.png": "image/png",
    "icon-512.png": "image/png",
    "maskable-512.png": "image/png",
    "apple-touch-icon.png": "image/png",
    "favicon.svg": "image/svg+xml",
}

_MANIFEST_TYPE = "application/manifest+json"
_OFFLINE_TYPE = "text/html; charset=utf-8"


@dataclass(frozen=True)
class Asset:
    body: bytes
    media_type: str


@dataclass(frozen=True)
class Assets:
    """Everything served from `static/`, resolved once.

    `icons` is keyed by filename; anything that failed to load is missing from it, and
    `precache` lists only what did load — a service worker whose `cache.addAll` hits a 404
    fails to install at all, which would cost the whole feature over one absent file.
    """

    icons: dict[str, Asset]
    manifest: Asset | None
    offline: Asset | None
    service_worker: str
    precache: list[str]
    version: str


def _read(path: Path, media_type: str) -> Asset | None:
    try:
        return Asset(path.read_bytes(), media_type)
    except OSError as exc:
        log.warning("static asset %s is unreadable and will 404: %s", path, exc)
        return None


def cache_version(bodies: dict[str, bytes]) -> str:
    """A short digest over the precached bytes, stable across processes and machines.

    Names are folded in as well as contents so that renaming a file — which changes the
    URL the worker precaches — also changes the version.
    """
    digest = hashlib.sha256()
    for name in sorted(bodies):
        digest.update(name.encode())
        digest.update(b"\x00")
        digest.update(bodies[name])
        digest.update(b"\x00")
    return digest.hexdigest()[:12]


def load() -> Assets:
    icons = {}
    for name, media_type in ICON_TYPES.items():
        asset = _read(ICONS_DIR / name, media_type)
        if asset is not None:
            icons[name] = asset

    manifest = _read(STATIC_DIR / "manifest.webmanifest", _MANIFEST_TYPE)
    offline = _read(STATIC_DIR / "offline.html", _OFFLINE_TYPE)

    # The precache list, and therefore the only set of URLs the worker is able to store.
    # It holds no run URL, and there is no code path that adds one.
    cached: dict[str, bytes] = {}
    if offline is not None:
        cached[OFFLINE_PATH] = offline.body
    if manifest is not None:
        cached[MANIFEST_PATH] = manifest.body
    for name, asset in icons.items():
        cached[ICONS_PREFIX + name] = asset.body

    version = cache_version(cached)
    precache = sorted(cached)

    worker = _read(STATIC_DIR / "sw.js", "text/javascript")
    source = worker.body.decode() if worker is not None else ""
    service_worker = source.replace("__RA_CACHE_VERSION__", version).replace(
        "__RA_PRECACHE__", json.dumps(precache)
    )

    return Assets(
        icons=icons,
        manifest=manifest,
        offline=offline,
        service_worker=service_worker,
        precache=precache,
        version=version,
    )
