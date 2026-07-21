"""Role assignment — who writes, who critiques, and the invariants that must hold.

The one hard invariant: **a report is never critiqued, on any lens, by the model
that authored it.** Everything here is expressed over *resolved* identities
(provider/model), not aliases, so two aliases pointing at the same underlying model
can never masquerade as two independent reviewers (RA-017).
"""

from __future__ import annotations

from .config import Roster
from .schemas import CleanRecord, LensStatus
from .taxonomy import LENSES, Lens


class RosterExhausted(RuntimeError):
    """No eligible model remains for a required role — fatal, fails closed."""


def writer_pool(
    roster: Roster,
    identities: dict[str, str],
    last_author_identity: str | None,
) -> list[str]:
    """Every alias eligible to write the next draft, in roster order.

    Exposed alongside `next_writer` because a caller that wants to *fall back* after a
    dud model has to know how many distinct candidates exist — rotating past the end
    of the pool would re-ask the model that just failed."""
    candidates = [
        alias
        for alias in roster.writers
        if last_author_identity is None or identities[alias] != last_author_identity
    ]
    if not candidates:
        raise RosterExhausted(
            "writer pool contains no model other than the current author; "
            "add a second distinct writer"
        )
    return candidates


def next_writer(
    roster: Roster,
    identities: dict[str, str],
    last_author_identity: str | None,
    rotation: int,
) -> str:
    """Round-robin over the writer pool, never the model that authored the current
    draft. The next report is always improved by someone who did not write it."""
    candidates = writer_pool(roster, identities, last_author_identity)
    return candidates[rotation % len(candidates)]


def eligible_critics(
    roster: Roster,
    identities: dict[str, str],
    lens: Lens,
    author_identity: str,
) -> list[str]:
    """Aliases eligible to critique this lens for this author, deduplicated by
    resolved identity (the first alias for an identity wins)."""
    out: list[str] = []
    seen: set[str] = set()
    for alias in roster.critics_for(lens):
        ident = identities[alias]
        if ident == author_identity or ident in seen:
            continue
        seen.add(ident)
        out.append(alias)
    return out


def pick_critic(
    roster: Roster,
    identities: dict[str, str],
    lens: Lens,
    author_identity: str,
    used_identities: set[str],
) -> str:
    """Prefer a model that has not yet reviewed this lens on this artifact — that is
    what turns a weak clearance into a strong one. Falls back to the first eligible
    model when everyone has already reviewed (a re-critique after a lens failure)."""
    eligible = eligible_critics(roster, identities, lens, author_identity)
    if not eligible:
        raise RosterExhausted(f"lens '{lens.value}' has no eligible non-author critic")
    for alias in eligible:
        if identities[alias] not in used_identities:
            return alias
    return eligible[0]


def lens_statuses(
    roster: Roster,
    identities: dict[str, str],
    author_identity: str,
    artifact_hash: str,
    records: list[CleanRecord],
    used: dict[str, set[str]],
) -> list[LensStatus]:
    """Per-lens acceptance predicates for the CURRENT artifact hash only. Records for
    any other hash are stale by construction and never counted (RC-002)."""
    out: list[LensStatus] = []
    for lens in LENSES:
        eligible = eligible_critics(roster, identities, lens, author_identity)
        eligible_ids = {identities[a] for a in eligible}
        # Defence in depth: a record counts only if it attests THIS artifact, under
        # THIS author, by a model that is still an eligible non-author critic. Any
        # one of these failing means the record is evidence about something else.
        cleared = {
            r.critic_identity
            for r in records
            if r.artifact_hash == artifact_hash
            and r.lens is lens
            and r.artifact_author_identity == author_identity
            and r.critic_identity != author_identity
            and r.critic_identity in eligible_ids
        }
        used_ids = used.get(lens.value, set())
        out.append(
            LensStatus(
                lens=lens,
                cleared_count=len(cleared),
                eligible_count=len(eligible_ids),
                unused_eligible=len(eligible_ids - used_ids),
            )
        )
    return out


def assert_author_exclusion(critic_identity: str, author_identity: str, lens: Lens) -> None:
    """Belt-and-braces: this is asserted at the moment of the call, not just at
    selection, so no retry path can smuggle in a self-review."""
    if critic_identity == author_identity:
        raise RosterExhausted(
            f"invariant violated: lens '{lens.value}' critic is the author of the artifact"
        )
