"""The two rosters this repo ships, and the contract between them.

`config/roster.default.yaml` is baked into the image (Dockerfile) and into the wheel
(pyproject force-include). It is what a `docker run` with no mount, or a `pip install`
with no config, comes up on — so it must boot with no proxy, no credential, and no
egress. scripts/smoke-test-image.sh proves that end-to-end; these tests state the same
requirement where a reader will find it, and catch it before a 3-minute image build does.

`config/roster.yaml` is THIS deployment's config, mounted over the baked copy by
compose.yaml. It opts into things the default cannot have.

The two must keep naming the same models: an image whose default roster reviews with a
lineup the deployment abandoned is worse than an image with no default at all.

Fully offline — nothing here reaches the proxy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from reasonable_answer.config import Config
from reasonable_answer.taxonomy import Lens

DEFAULT_ROSTER = Path("config/roster.default.yaml")
DEPLOYMENT_ROSTER = Path("config/roster.yaml")


@pytest.fixture(scope="module")
def default_config() -> Config:
    return Config.load(DEFAULT_ROSTER)


@pytest.fixture(scope="module")
def deployment_config() -> Config:
    return Config.load(DEPLOYMENT_ROSTER)


@pytest.mark.parametrize("path", [DEFAULT_ROSTER, DEPLOYMENT_ROSTER])
def test_shipped_roster_parses(path: Path) -> None:
    # Both are loaded by something a user runs; neither gets to be syntactically stale.
    assert Config.load(path).roster.writers


def test_default_roster_boots_without_a_proxy_or_a_credential(default_config: Config) -> None:
    """Every opt-in below needs something the smoke test deliberately withholds.

    `refine` is the one that has actually broken this: enabling it puts the refine alias
    into startup identity resolution and structured-output probing (web/refine.py's
    `start`), so an unreachable proxy stops the web server from booting at all.
    """
    assert default_config.refine.enabled is False, "refine makes the proxy a boot dependency"
    assert default_config.search.enabled is False, "search needs a Brave credential"
    assert default_config.search.verify_sources is False, "fetching model-chosen URLs needs egress"
    # Two switches, both off. `sources.pdf` needs the optional `ingest` extra, which the
    # "clone and run the tests" path does not install — and the master switch has to
    # stay off independently so that enabling one tier never turns on another.
    assert default_config.sources.enabled is False
    assert default_config.sources.pdf.enabled is False
    # The registry tiers (D-existence-vs-body) reach hosts of their own, so they need egress the smoke
    # test withholds — and the identifier tier can raise a blocking `fabricated_citation`
    # via D-notfound-fabrication, which is not something an unattended default should be able to do.
    assert default_config.sources.identifiers.enabled is False
    assert default_config.sources.open_access.enabled is False
    # The paid tiers (D-paid-tier-page) additionally need a credential the smoke test withholds, and
    # refuse to start without one — so "off" here is what keeps `make test` bootable.
    assert default_config.sources.extraction.enabled is False
    assert default_config.sources.delivery.enabled is False
    assert default_config.disputes.enabled is False
    # Auditioning itself needs no withholding — it only happens via `ra audition`. What
    # must stay off is the gate that reads its cache: `enforce` is warn-by-default (D-critic-audition).
    assert default_config.audition.enforce is False


def test_the_deployment_roster_keeps_the_resolver_tiers_off(
    deployment_config: Config,
) -> None:
    """This deployment opts into things the default cannot have, but not into these.

    Both tiers stay off in the shipped file — the block is commented out entirely — so
    that the roster mounted over the baked copy cannot silently start spending registry
    calls, or start minting `fabricated_citation` from a registry's silence, on the
    strength of a config edit nobody reviewed.
    """
    assert deployment_config.sources.identifiers.enabled is False
    assert deployment_config.sources.open_access.enabled is False
    assert deployment_config.sources.extraction.enabled is False
    assert deployment_config.sources.delivery.enabled is False
    # `core` is keyed, so a default list containing it would turn "enable open access"
    # into "and also supply a CORE key or fail to boot".
    assert "core" not in deployment_config.sources.open_access.providers


@pytest.mark.parametrize("path", [DEFAULT_ROSTER, DEPLOYMENT_ROSTER])
def test_the_completeness_pool_excludes_the_unfit_critic(path: Path) -> None:
    """D-completeness-pool-noise. `mistral-large-3` graded `unfit` on completeness —
    2.61 material issues invented per sound control, spot-check confirmed — so it is not
    in that pool on either shipped roster.

    It is deliberately still a writer and a logic critic: the re-audition that call
    waited on (2026-08-07) graded it `fit` on logic — 0.94 sensitivity, 0.08 invented
    per control — and D-minimax-retirement moved it to position 1 of that pool.
    Asserting that here is what keeps this from being read as "drop the model", which
    is a different change.
    """
    roster = Config.load(path).roster
    completeness = roster.critics_for(Lens.COMPLETENESS)
    assert "mistral-large-3" not in completeness
    assert "mistral-large-3" in roster.critics_for(Lens.LOGIC)
    assert "mistral-large-3" in roster.writers


@pytest.mark.parametrize("path", [DEFAULT_ROSTER, DEPLOYMENT_ROSTER])
def test_the_completeness_pool_leads_with_the_audited_fit_critic(path: Path) -> None:
    """D-completeness-pool-noise, part 2: order is load-bearing, not cosmetic.

    Whichever slot a pass reaches first is the one whose *silence* the run acts on, so
    the measured-`fit` model (`gemma4`, 0.89 sensitivity / 0.17 invented per control)
    holds position 1 and the `marginal` one (`glm-5.2`, 0.72) sits behind it.

    Two families still staff the lens, which is what keeps a strong `accepted` reachable
    — but family independence is a property of *resolved* identities (RA-017), so the
    check that actually enforces it is `validate_roster_health` at startup, not this
    test. What is pinned here is the composition and the ordering the decision chose.
    """
    completeness = Config.load(path).roster.critics_for(Lens.COMPLETENESS)
    assert completeness == ["gemma4", "glm-5.2"]


@pytest.mark.parametrize("path", [DEFAULT_ROSTER, DEPLOYMENT_ROSTER])
def test_no_writer_sits_in_the_completeness_pool(path: Path) -> None:
    """Author exclusion never shrinks this lens below the two models above.

    The pool is at `review.depth`, with no spare, so a writer inside it would silently
    drop the lens to a single critic on every round that writer authored — which is what
    the dropped model did on R1 and every odd round.
    """
    roster = Config.load(path).roster
    assert not set(roster.critics_for(Lens.COMPLETENESS)) & set(roster.writers)


@pytest.mark.parametrize("path", [DEFAULT_ROSTER, DEPLOYMENT_ROSTER])
def test_minimax_m3_is_retired_from_every_critic_pool(path: Path) -> None:
    """D-minimax-retirement. Three audits (2026-08-02..07) agreed and worsened: on
    logic 1.12 invented material issues per sound control with obvious-tier
    sensitivity at 0.33, on evidence 1.52 invented with sensitivity 0.58, measured
    under the `json_schema` mode a run pins it to. A spot check attributed the noise
    to the model (it flags a clause while its own quoted span carries the adjacent
    qualifier that resolves the complaint), so a critic that cannot return an honest
    clean leaves both pools. It was critic-only, so it leaves the roster entirely.
    """
    roster = Config.load(path).roster
    for lens in Lens:
        assert "minimax-m3" not in roster.critics_for(lens)
    assert "minimax-m3" not in roster.writers


@pytest.mark.parametrize("path", [DEFAULT_ROSTER, DEPLOYMENT_ROSTER])
def test_the_logic_pool_leads_with_the_audited_fit_critic(path: Path) -> None:
    """D-minimax-retirement, ordering half — same rule as D-completeness-pool-noise:
    the pass acts on position 1's silence, so the lens's only measured `fit`
    (`mistral-large-3`, 0.94 sensitivity / 0.08 invented per control) leads and the
    `marginal` `glm-5.2` (1.00 sensitivity / 0.75 invented) backs it. On rounds
    mistral-large-3 authors, exclusion thins the pool to glm-5.2 alone — accepted as
    the cost of fit-first; the evidence ordering below makes the same trade the other
    way (no `fit` exists there, so the higher-sensitivity marginal leads).
    """
    roster = Config.load(path).roster
    assert roster.critics_for(Lens.LOGIC) == ["mistral-large-3", "glm-5.2"]
    assert roster.critics_for(Lens.EVIDENCE) == ["glm-5.2", "gemma4"]


def test_the_two_rosters_name_the_same_models(
    default_config: Config, deployment_config: Config
) -> None:
    """Drift guard. Changing the lineup in one file means changing it in both.

    Only the lineup is compared — the whole point of the split is that the *settings*
    differ.
    """
    default_roster, deployment_roster = default_config.roster, deployment_config.roster
    assert default_roster.writers == deployment_roster.writers
    assert default_roster.critics == deployment_roster.critics
    assert default_roster.orchestrator_alias == deployment_roster.orchestrator_alias
