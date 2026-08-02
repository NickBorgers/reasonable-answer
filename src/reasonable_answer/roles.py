"""Role assignment — who writes, who critiques, and the invariants that must hold.

The one hard invariant: **a report is never critiqued, on any lens, by the model
that authored it.** Everything here is expressed over *resolved* identities
(provider/model), not aliases, so two aliases pointing at the same underlying model
can never masquerade as two independent reviewers (RA-017).
"""

from __future__ import annotations

from .config import Roster, model_family
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


def critic_slate(
    roster: Roster,
    identities: dict[str, str],
    lens: Lens,
    author_identity: str,
    used_identities: set[str],
    depth: int = 1,
    rotation: int = 0,
) -> list[str]:
    """Up to `depth` distinct eligible non-author critics to read this lens *this pass*
    (D-front-loaded-depth).

    Prefer models that have not yet reviewed this lens on this artifact — that is what
    turns a weak clearance into a strong one, and running two of them at once is what
    stops the second reviewer's findings arriving five rounds late.

    `depth` is a ceiling, never a quota. The slate is drawn only from *fresh* eligible
    models, so a lens the roster can staff only once returns one alias and the run
    reaches `converged_unconfirmed` through rule 10 instead of asking one model to
    double-review its way to a strong acceptance. `eligible_critics` has already
    dropped the author and deduplicated by resolved identity, so no slate can contain
    the author or the same model twice however large `depth` is.

    Once everyone has reviewed (a re-critique after a lens failure) it falls back to
    rotating a single model through the pool by `rotation` rather than always returning
    the first eligible one. That fallback used to be `eligible[0]` unconditionally,
    which meant a lens that kept failing asked the *same* model every remaining
    attempt: one production run spent 11 of its 12 `critique_attempts` on a single
    critic and aborted.
    """
    eligible = eligible_critics(roster, identities, lens, author_identity)
    if not eligible:
        raise RosterExhausted(f"lens '{lens.value}' has no eligible non-author critic")
    fresh = [alias for alias in eligible if identities[alias] not in used_identities]
    used_families = {model_family(identity) for identity in used_identities}
    family_fresh: list[str] = []
    selected_families = set(used_families)
    for alias in fresh:
        family = model_family(identities[alias])
        if family in selected_families:
            continue
        selected_families.add(family)
        family_fresh.append(alias)
    if family_fresh:
        return family_fresh[: max(1, depth)]
    if fresh:
        return [fresh[rotation % len(fresh)]]
    return [eligible[rotation % len(eligible)]]


def pick_critic(
    roster: Roster,
    identities: dict[str, str],
    lens: Lens,
    author_identity: str,
    used_identities: set[str],
    rotation: int = 0,
) -> str:
    """The single-critic case of `critic_slate` — one model for one lens, one pass."""
    return critic_slate(
        roster, identities, lens, author_identity, used_identities, 1, rotation
    )[0]


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
        eligible_families = {model_family(identity) for identity in eligible_ids}
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
        cleared_families = {model_family(identity) for identity in cleared}
        used_ids = used.get(lens.value, set())
        unused_families = {
            model_family(identities[alias])
            for alias in eligible
            if identities[alias] not in used_ids
            and model_family(identities[alias]) not in cleared_families
        }
        out.append(
            LensStatus(
                lens=lens,
                cleared_count=len(cleared_families),
                eligible_count=len(eligible_families),
                unused_eligible=len(unused_families),
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
