"""Web Push notification of a stopped run (D-stop-notification).

A run takes 10-25 minutes and the interface makes it easy to start several. Before this
module the only way to learn one had finished was to be looking at its page, where the SSE
stream (`app.stream`) reloads it. Close the tab -- or background the installed app on a
phone, where iOS suspends it -- and nothing ever said. This is the piece that says it.

It exists because D-installable-pwa shipped a service worker. Web Push has no other delivery mechanism:
the browser wakes the worker, and the worker shows the notification. On iOS it works only
for a web app added to the home screen, which is the posture D-installable-pwa already built for.

Three properties bound what is here.

1. **The subscription endpoint is a URL the browser chose and this server then POSTs to.**
   That is the same shape as the seed-URL fetch that `docs/ssrf-egress-isolation.md` exists
   for, and it is checked the same way: an allowlist of push-service hosts, matched on
   labelled suffixes rather than substrings. `validate_endpoint` runs at subscribe time
   *and* again before every send, so a subscription stored under a wider allowlist cannot
   be used after that allowlist narrows.

2. **State is two files, never a directory.** `Registry._run_dirs` skips anything without an
   `events.jsonl`, but `store.expired_runs` filters on `is_dir()` alone -- so a `push/`
   subdirectory under `runs_dir` would be swept as an expired run and deleted. Files are
   invisible to both by construction, which is a stronger guarantee than an exclusion rule
   a later refactor could drop. They live under `runs_dir` because that is the mounted
   volume; anywhere else and the VAPID key dies with the container.

3. **Nothing here may break a run.** Every send is best-effort: the worker calls this after
   a run is already finished and durable, and a push service being slow or gone must cost
   a log line, not the run's result. There is no retry queue -- a missed notification is
   recoverable by opening the app, and the alternative is durable state that can wedge.

Payload privacy: Web Push bodies are encrypted to the subscription's own key (RFC 8291,
aes128gcm), so Apple and Google relay ciphertext they cannot read. That is what makes it
acceptable to put a truncated question in the body, which is what tells two concurrent runs
apart on a lock screen.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..config import DEFAULT_PUSH_ENDPOINT_HOSTS

log = logging.getLogger(__name__)

#: Both live directly in `runs_dir`, as files rather than a directory -- see this module's
#: docstring for why that is a correctness property and not a style choice. Dot-prefixed so
#: they also fail `store.RUN_ID`, which requires an alphanumeric first character.
SUBSCRIPTIONS_FILE = ".push-subscriptions.json"
VAPID_FILE = ".vapid-private.pem"

#: A push service answering with either of these means the subscription is permanently
#: gone -- the browser was uninstalled, the user revoked permission, the endpoint expired.
#: Deliberately *not* imported from `fetch.NOT_FOUND_STATUSES`, which D-notfound-fabrication owns for deciding
#: whether a *citation* is fabricated: the two happen to be the same pair today, and a
#: change to what counts as a missing source must not silently change what counts as a dead
#: device.
GONE_STATUSES = frozenset({404, 410})

#: Longest question fragment placed in a notification body. Lock screens truncate somewhere
#: around here anyway, and the point is to tell two runs apart, not to re-read the question.
QUESTION_CHARS = 80


class PushUnavailable(RuntimeError):
    """`pywebpush` is not installed, so no notification can be sent.

    Raised at construction rather than at send time: a deployment that turned the feature
    on wants to hear about a missing dependency at boot, not silently at 2am when a run
    finishes. Mirrors how `ingest` reports a missing `pypdf` -- an actionable message, not
    an ImportError from the middle of a request.
    """


# --------------------------------------------------------------------- endpoints


def validate_endpoint(url: str, allowed_hosts: tuple[str, ...]) -> str:
    """Return `url` if it addresses a known push service, else raise `ValueError`.

    The caller hands us this string; the server then makes a request to it. Everything
    refused here is refused because of that: a non-HTTPS scheme (the payload is encrypted
    but the VAPID assertion is not), embedded credentials, an explicit port that would let
    a matching hostname reach an unexpected service.

    The host test is on *labels*, never substrings. `endswith("fcm.googleapis.com")` also
    accepts `evil-fcm.googleapis.com`, and `endswith(".fcm.googleapis.com")` accepts
    `fcm.googleapis.com.attacker.net` for a wildcard entry -- so an exact match and a
    dot-anchored suffix are the only two accepted shapes.
    """
    try:
        parts = urlsplit(url)
    except ValueError as exc:  # pragma: no cover - urlsplit is lenient
        raise ValueError("unparseable endpoint") from exc

    if parts.scheme != "https":
        raise ValueError("endpoint must be https")
    if parts.username or parts.password:
        raise ValueError("endpoint must not carry credentials")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("endpoint has an invalid port") from exc
    if port is not None:
        raise ValueError("endpoint must not specify a port")

    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("endpoint has no host")

    for allowed in allowed_hosts:
        if allowed.startswith("."):
            if host.endswith(allowed) and len(host) > len(allowed):
                return url
        elif host == allowed:
            return url
    raise ValueError(f"endpoint host is not a known push service: {host}")


# --------------------------------------------------------------------- VAPID


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def load_or_create_vapid(path: Path) -> tuple[Path, str]:
    """Return `(pem_path, application_server_key)`, generating the key on first call.

    Generated rather than configured on purpose. A VAPID keypair is self-issued -- there is
    no account anywhere to register it with -- so making the operator produce one by hand
    buys nothing and adds a setup step that can be got wrong. The cost is that the key is
    now state worth backing up: losing it invalidates every subscription on every device,
    and the failure is silent, because a push to a subscription minted under a different key
    is simply refused and there is no channel left to ask the device to re-subscribe.

    The private key is stored as a PEM file, not inside a JSON blob, because that is the
    form `pywebpush` consumes directly. The returned key is the uncompressed public point,
    base64url-encoded, which is what `pushManager.subscribe` wants for
    `applicationServerKey`.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    if path.exists():
        private = serialization.load_pem_private_key(path.read_bytes(), password=None)
    else:
        private = ec.generate_private_key(ec.SECP256R1())
        pem = private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pem)
        os.chmod(path, 0o600)
        log.warning(
            "generated a new VAPID key at %s — back this file up; losing it silently "
            "invalidates every existing push subscription",
            path,
        )

    point = private.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return path, _b64url(point)


# --------------------------------------------------------------------- storage


class PushStore:
    """Subscriptions, keyed by identity, in one JSON file under `runs_dir`.

    A single file rather than a file per identity: the whole set is read on every send and
    rewritten on every subscribe, both rare, and one `os.replace` is the entire durability
    story. The lock is process-local, which is the same single-instance assumption the run
    queue, the live-status map and both rate limiters already make.
    """

    def __init__(self, path: Path, max_per_identity: int = 10) -> None:
        self.path = Path(path)
        self._max = max(1, max_per_identity)
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- reading

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            # A corrupt file must not take the app down, and it holds nothing that cannot
            # be rebuilt by tapping the button again.
            log.warning("push subscription store at %s is unreadable; ignoring it", self.path)
            return {}
        if not isinstance(raw, dict):
            return {}
        return {k: v for k, v in raw.items() if isinstance(v, list)}

    def for_identity(self, identity: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._load().get(identity, ()))

    def endpoints(self) -> set[str]:
        with self._lock:
            return {
                sub["endpoint"]
                for subs in self._load().values()
                for sub in subs
                if isinstance(sub, dict) and sub.get("endpoint")
            }

    # ---------------------------------------------------------------- writing

    def _save(self, data: dict[str, list[dict[str, Any]]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def add(self, identity: str, endpoint: str, p256dh: str, auth: str, now: float) -> None:
        """Record a device against `identity`, replacing any prior claim on that endpoint.

        The endpoint is dropped from *every* identity first, not just this one. A browser
        profile signed in as one person and then another keeps the same push endpoint, and
        leaving the old row would send that person's questions to a device that is now
        someone else's session.
        """
        with self._lock:
            data = self._load()
            for who in list(data):
                data[who] = [s for s in data[who] if s.get("endpoint") != endpoint]
            subs = data.setdefault(identity, [])
            subs.append(
                {"endpoint": endpoint, "p256dh": p256dh, "auth": auth, "added_at": now}
            )
            # Oldest out first. A cap that dropped the *newest* would make the device you
            # just enabled the one that never gets a notification.
            data[identity] = subs[-self._max :]
            self._save({k: v for k, v in data.items() if v})

    def remove(self, identity: str, endpoint: str) -> bool:
        with self._lock:
            data = self._load()
            before = len(data.get(identity, ()))
            data[identity] = [s for s in data.get(identity, ()) if s.get("endpoint") != endpoint]
            changed = len(data[identity]) != before
            if changed:
                self._save({k: v for k, v in data.items() if v})
            return changed

    def prune(self, endpoint: str) -> None:
        """Forget a subscription the push service has told us is permanently gone."""
        with self._lock:
            data = self._load()
            touched = False
            for who in list(data):
                kept = [s for s in data[who] if s.get("endpoint") != endpoint]
                if len(kept) != len(data[who]):
                    data[who] = kept
                    touched = True
            if touched:
                self._save({k: v for k, v in data.items() if v})


# --------------------------------------------------------------------- sending


class Notifier:
    """Sends one notification per subscribed device when a run stops.

    Constructed only when the feature is enabled, so a deployment with `push.enabled:
    false` never imports `pywebpush` and never has a key on disk.
    """

    def __init__(
        self,
        *,
        store: PushStore,
        vapid_pem: Path,
        subject: str,
        public_base: str,
        endpoint_hosts: tuple[str, ...] = DEFAULT_PUSH_ENDPOINT_HOSTS,
        timeout_seconds: float = 5.0,
        sender: Any | None = None,
    ) -> None:
        if sender is None:
            try:
                from pywebpush import webpush
            except ImportError as exc:  # pragma: no cover - exercised by fake sender
                raise PushUnavailable(
                    "push.enabled is true but pywebpush is not installed; "
                    "install the `web` extra or set push.enabled: false"
                ) from exc
            sender = webpush
        self._send = sender
        self._store = store
        self._pem = Path(vapid_pem)
        self._subject = subject
        self._public_base = public_base
        self._hosts = tuple(endpoint_hosts)
        self._timeout = timeout_seconds

    # ------------------------------------------------------------------ body

    def _payload(self, run_id: str, question: str, status: str, has_report: bool) -> str:
        """The notification, as the service worker will receive it.

        Deep-links at the *public* base, not the gated one: run URLs are the shareable
        surface (D-id-as-credential) and the reader-facing base is what every other run link uses, so a
        notification that opened `/app/runs/<id>` would be the one link in the app that
        behaves differently. A finished run points at the report -- the thing that was
        waited for -- and one that stopped without shipping an answer points at the run
        page, which is where the trail and the resume button are.
        """
        trimmed = question.strip().replace("\n", " ")
        if len(trimmed) > QUESTION_CHARS:
            trimmed = trimmed[: QUESTION_CHARS - 1].rstrip() + "…"
        spoken = status.replace("_", " ")
        title = f"Report ready — {spoken}" if has_report else f"Run stopped — {spoken}"
        target = f"{self._public_base}/runs/{run_id}"
        if has_report:
            target += "/report"
        return json.dumps(
            {"title": title, "body": trimmed, "url": target, "tag": run_id}
        )

    # ------------------------------------------------------------------ send

    def notify(
        self, *, run_id: str, owner: str | None, question: str, status: str, has_report: bool
    ) -> int:
        """Push to every device `owner` has registered. Returns the number delivered.

        An owner-less run notifies nobody: there is no identity to attribute it to, and
        D-identity-header already settled that inventing one is how a stranger's run reaches someone
        else. Exceptions never escape -- see this module's docstring.
        """
        if not owner:
            return 0
        subs = self._store.for_identity(owner)
        if not subs:
            return 0

        data = self._payload(run_id, question, status, has_report)
        sent = 0
        for sub in subs:
            endpoint = sub.get("endpoint") or ""
            try:
                validate_endpoint(endpoint, self._hosts)
            except ValueError as exc:
                # Stored under a wider allowlist than the one now configured. Dropping it
                # rather than skipping it keeps the file from accumulating rows that can
                # never be used again.
                log.warning("dropping push subscription with unusable endpoint: %s", exc)
                self._store.prune(endpoint)
                continue
            if self._deliver(endpoint, sub, data):
                sent += 1
        return sent

    def _deliver(self, endpoint: str, sub: dict[str, Any], data: str) -> bool:
        try:
            self._send(
                subscription_info={
                    "endpoint": endpoint,
                    "keys": {"p256dh": sub.get("p256dh", ""), "auth": sub.get("auth", "")},
                },
                data=data,
                vapid_private_key=str(self._pem),
                vapid_claims={"sub": self._subject},
                timeout=self._timeout,
            )
            return True
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in GONE_STATUSES:
                log.info("push subscription is gone (%s); pruning it", status)
                self._store.prune(endpoint)
            else:
                # Best-effort by design. The run is already finished and durable.
                log.warning("push to %s failed: %s", _host_of(endpoint), exc)
            return False


def _host_of(endpoint: str) -> str:
    """Host only. The full endpoint is a bearer credential for that device's notifications
    and does not belong in a log line."""
    try:
        return urlsplit(endpoint).hostname or "?"
    except ValueError:
        return "?"
