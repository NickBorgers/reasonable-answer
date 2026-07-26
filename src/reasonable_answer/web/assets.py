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
    URL the worker precaches — also changes the version. The names carry the base path, so
    a deployment under a prefix keys its cache distinctly from one at the root, which is
    correct: the two precache different URLs.
    """
    digest = hashlib.sha256()
    for name in sorted(bodies):
        digest.update(name.encode())
        digest.update(b"\x00")
        digest.update(bodies[name])
        digest.update(b"\x00")
    return digest.hexdigest()[:12]


#: Manifest members whose values are root-absolute URLs the browser resolves against the
#: origin. Under a base path they have to carry the prefix or the installed app's scope and
#: launch URL escape back to the root, past the Access policy scoped to the prefix. `id`,
#: `start_url` and `scope` are single paths; icon `src`es are rewritten separately.
_MANIFEST_PATH_KEYS = ("id", "start_url", "scope")


def _rewrite_manifest(body: bytes, base_path: str) -> bytes:
    """Prefix the path-valued members of the manifest with the base path.

    Returns the input untouched when there is no prefix (and on the two failure modes a
    swapped-in manifest can present — invalid JSON, or a non-object top level), so the
    root-origin deployment and a hand-broken manifest both degrade exactly as before.
    """
    if not base_path:
        return body
    try:
        doc = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body
    if not isinstance(doc, dict):
        return body
    for key in _MANIFEST_PATH_KEYS:
        value = doc.get(key)
        if isinstance(value, str) and value.startswith("/"):
            doc[key] = base_path + value
    for icon in doc.get("icons", []) or []:
        src = isinstance(icon, dict) and icon.get("src")
        if isinstance(src, str) and src.startswith("/"):
            icon["src"] = base_path + src
    return json.dumps(doc).encode()


def load(base_path: str = "") -> Assets:
    """Resolve everything served from `static/`, prefixing every browser-facing URL with
    `base_path` (`''` for a root-origin deployment, `'/app'` behind a stripping proxy).

    The route paths in `web/app.py` are unaffected — the proxy strips the prefix before the
    request arrives, so the app still serves at `/manifest.webmanifest`. It is the URLs the
    *manifest names* and the worker *precaches and fetches* that carry the prefix, because a
    browser resolves those against the origin, not the app's mount point.
    """
    icons = {}
    for name, media_type in ICON_TYPES.items():
        asset = _read(ICONS_DIR / name, media_type)
        if asset is not None:
            icons[name] = asset

    manifest = _read(STATIC_DIR / "manifest.webmanifest", _MANIFEST_TYPE)
    if manifest is not None:
        manifest = Asset(_rewrite_manifest(manifest.body, base_path), manifest.media_type)
    offline = _read(STATIC_DIR / "offline.html", _OFFLINE_TYPE)

    # The precache list, and therefore the only set of URLs the worker is able to store.
    # It holds no run URL, and there is no code path that adds one. Each key is a
    # browser-facing URL, so it carries the base path.
    offline_url = base_path + OFFLINE_PATH
    cached: dict[str, bytes] = {}
    if offline is not None:
        cached[offline_url] = offline.body
    if manifest is not None:
        cached[base_path + MANIFEST_PATH] = manifest.body
    for name, asset in icons.items():
        cached[base_path + ICONS_PREFIX + name] = asset.body

    version = cache_version(cached)
    precache = sorted(cached)

    worker = _read(STATIC_DIR / "sw.js", "text/javascript")
    source = worker.body.decode() if worker is not None else ""
    service_worker = (
        source.replace("__RA_CACHE_VERSION__", version)
        .replace("__RA_PRECACHE__", json.dumps(precache))
        .replace("__RA_OFFLINE__", offline_url)
    )

    return Assets(
        icons=icons,
        manifest=manifest,
        offline=offline,
        service_worker=service_worker,
        precache=precache,
        version=version,
    )
