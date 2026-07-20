"""Run configuration: the role-structured roster, budgets, and startup validation.

The roster is a **writer pool** plus **per-lens critic pools**, and an optional
**orchestrator** entry (D15/D16/D18). Critic-only specialists are allowed and are the
clean way to satisfy author-exclusion: a model that never writes can review every tick.
That is how the strongest model in the roster earns its keep — as a writer it would be
barred from reviewing its own drafts.

Startup validation is **fail closed** (RA-015): an empty writer pool, a lens with no
eligible non-author model, or a bad `min_ticks`/`hard_cap` pair aborts the run before
a single token is spent.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .taxonomy import LENSES, Lens

#: Leading alphabetic run of a model name — 'gemma-4-31b-it' and 'gemma4' both -> 'gemma'.
_FAMILY_STEM = re.compile(r"[a-z]+")

#: Shipped inside the wheel (see pyproject force-include), so an installed package
#: has a working default even with no source tree around it.
PACKAGED_CONFIG = Path(__file__).resolve().parent / "_default_roster.yaml"

#: Searched in order. The source-tree path keeps `uv run ra ...` working from a
#: checkout; `RA_CONFIG` and /etc/ra are how a container gets its roster mounted in.
CONFIG_SEARCH_PATH: tuple[Path, ...] = (
    Path("config/roster.yaml"),
    Path("/etc/ra/roster.yaml"),
    PACKAGED_CONFIG,
)


def default_config_path() -> Path:
    """First existing candidate, honouring $RA_CONFIG. Never a path that only
    resolves inside a source checkout — that broke every containerized run."""
    override = os.environ.get("RA_CONFIG")
    if override:
        return Path(override)
    for candidate in CONFIG_SEARCH_PATH:
        if candidate.exists():
            return candidate
    return PACKAGED_CONFIG


class ConfigError(RuntimeError):
    """Raised for any fail-closed startup violation."""


class Budgets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Bounds are part of failing closed: a zero concurrency crashes the executor, a
    # negative stagnation limit terminates the run on tick one, and a negative budget
    # silently reads as "exhausted" — all of which change the state machine without
    # anyone saying so.
    min_ticks: int = Field(default=2, ge=1, le=100)
    hard_cap: int = Field(default=8, ge=2, le=200)
    polish_cap: int = Field(default=1, ge=0, le=20)
    critique_attempts: int = Field(default=12, ge=0, le=100)
    confirmation_attempts: int = Field(default=6, ge=0, le=100)
    stagnation_limit: int = Field(default=3, ge=1, le=100)
    cycle_period: int = Field(default=4, ge=1, le=100)
    repair_retries: int = Field(default=1, ge=0, le=10)
    call_retries: int = Field(default=2, ge=0, le=10)
    timeout_seconds: float = Field(default=300.0, gt=0, le=7200)
    max_concurrency: int = Field(default=3, ge=1, le=16)

    @model_validator(mode="after")
    def _check(self) -> Budgets:
        # RI-001: guarantees no generating rule can fire at or beyond the cap.
        if not (0 < self.min_ticks < self.hard_cap):
            raise ConfigError(
                f"config invariant violated: 0 < min_ticks < hard_cap "
                f"(got min_ticks={self.min_ticks}, hard_cap={self.hard_cap})"
            )
        return self


class ProxyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = "https://llm.featherback-mermaid.ts.net/v1"
    api_key_env: str = "LITELLM_API_KEY"
    api_key_fallback: str = "fake-key"

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env) or self.api_key_fallback


class Roster(BaseModel):
    model_config = ConfigDict(extra="forbid")

    writers: list[str] = Field(min_length=1)
    critics: dict[str, list[str]]
    #: The blind orchestrator's model. Its whole job is bounded ints in, one boolean
    #: out (schemas.OrchestratorView), so it needs neither reach nor a writer's
    #: capability. Defaults to writers[0] only because that alias is guaranteed to
    #: exist and to have been probed.
    orchestrator: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Roster:
        missing = {lens.value for lens in LENSES} - set(self.critics)
        if missing:
            raise ConfigError(f"roster is missing critic pools for lenses: {sorted(missing)}")
        extra = set(self.critics) - {lens.value for lens in LENSES}
        if extra:
            raise ConfigError(f"roster declares unknown lenses: {sorted(extra)}")
        for lens, pool in self.critics.items():
            if not pool:
                raise ConfigError(f"critic pool for lens '{lens}' is empty")
            if len(set(pool)) != len(pool):
                raise ConfigError(f"critic pool for lens '{lens}' has duplicate aliases")
        if len(set(self.writers)) != len(self.writers):
            raise ConfigError("writer pool has duplicate aliases")
        return self

    def critics_for(self, lens: Lens) -> list[str]:
        return list(self.critics[lens.value])

    @property
    def orchestrator_alias(self) -> str:
        """The explicit entry if set, else writers[0]. Total: writers has min_length=1."""
        return self.orchestrator or self.writers[0]

    @property
    def all_aliases(self) -> list[str]:
        # The orchestrator belongs here even when it is just writers[0]: `all_aliases`
        # is what startup resolves identities for and probes for structured output
        # (graph.build_runtime). An alias missing from it would skip both — the
        # identity guard would silently degrade to accepting the bare alias, and a
        # structured-output failure would surface mid-run instead of at startup.
        pools = (a for pool in self.critics.values() for a in pool)
        seen: list[str] = []
        for alias in [*self.writers, *pools, self.orchestrator_alias]:
            if alias not in seen:
                seen.append(alias)
        return seen


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    roster: Roster
    budgets: Budgets = Field(default_factory=Budgets)
    runs_dir: Path = Path("runs")
    retention_days: int = 14
    max_report_chars: int = 60_000
    max_question_chars: int = 4_000
    #: anchor every critic quote to the paragraph it cites, closing the last
    #: free-text channel from critic to writer
    require_verbatim_spans: bool = True

    @classmethod
    def load(cls, path: str | Path | None = None) -> Config:
        p = Path(path) if path else default_config_path()
        if not p.exists():
            raise ConfigError(
                f"config file not found: {p}\n"
                f"searched: $RA_CONFIG, {', '.join(str(c) for c in CONFIG_SEARCH_PATH)}"
            )
        data = yaml.safe_load(p.read_text()) or {}
        return cls.model_validate(data)


def validate_roster_health(config: Config, identities: dict[str, str]) -> list[str]:
    """Fail-closed structural checks + soft warnings. Returns the warning list.

    `identities` maps alias -> resolved provider/model/version string (RA-017).
    Distinctness is enforced at the *resolved* level, not the alias level: two
    aliases pointing at the same underlying model do not count as two reviewers.
    """
    roster = config.roster
    if not roster.writers:
        raise ConfigError("fail closed: writer pool is empty")

    warnings: list[str] = []

    unresolved = [a for a in roster.all_aliases if a not in identities]
    if unresolved:
        raise ConfigError(f"fail closed: could not resolve identities for {unresolved}")

    # A lens must always have at least one model that can review *any* writer's
    # output; otherwise some tick would have zero eligible non-author critics.
    for lens in LENSES:
        pool_ids = {identities[a] for a in roster.critics_for(lens)}
        for writer in roster.writers:
            eligible = pool_ids - {identities[writer]}
            if not eligible:
                raise ConfigError(
                    f"fail closed: lens '{lens.value}' has no eligible non-author critic "
                    f"when '{writer}' is the author"
                )
            if len(eligible) < 2:
                warnings.append(
                    f"lens '{lens.value}' is roster_limited when '{writer}' authors "
                    f"(only {len(eligible)} eligible model) — acceptance will degrade to "
                    f"converged_unconfirmed"
                )
        if len(pool_ids) < len(roster.critics_for(lens)):
            warnings.append(
                f"lens '{lens.value}' has aliases resolving to the same underlying model; "
                f"they do not count as distinct reviewers"
            )
        families = {_family(identities[a]) for a in roster.critics_for(lens)}
        if len(families) < 2:
            warnings.append(
                f"lens '{lens.value}' critic pool shares one model family {sorted(families)} — "
                f"weak independence (correlated blind spots)"
            )
    return warnings


def _family(identity: str) -> str:
    """Coarse model-family key taken from the model *name*, ignoring the provider or
    serving-backend prefix: 'openrouter/google/gemma-4-31b-it' and
    'ollama_chat/gemma4:26b-a4b-it-q8_0' are both 'gemma'.

    Keying on the prefix instead would read two namespaces at once — the org for a
    three-segment identity, the backend for a two-segment one — and so call those two
    Gemma checkpoints different families.
    """
    stem = identity.split("/")[-1].split(":")[0].lower()
    match = _FAMILY_STEM.match(stem)
    return match.group(0) if match else stem
