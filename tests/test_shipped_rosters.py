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
    assert default_config.disputes.enabled is False
    # Auditioning itself needs no withholding — it only happens via `ra audition`. What
    # must stay off is the gate that reads its cache: `enforce` is warn-by-default (D20).
    assert default_config.audition.enforce is False


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
