"""Command line entry point."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import audition as audition_mod
from . import search, shutdown
from .audition import Assignment as Assignment_t
from .config import Config, ConfigError, validate_roster_health
from .graph import GracefulStop
from .graph import run as run_graph
from .llm import LLMClient
from .store import expired_runs
from .store import purge as purge_run
from .taxonomy import Lens

app = typer.Typer(add_completion=False, help="reasonable-answer — isolation-pipeline report refiner")
console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


@app.command()
def run(
    question: str = typer.Option(..., "--question", "-q", help="The question to answer."),
    seed: Path | None = typer.Option(None, "--seed", "-s", help="Optional seed report (markdown)."),
    config_path: Path | None = typer.Option(None, "--config", "-c", help="Roster config YAML."),
    run_id: str | None = typer.Option(None, "--run-id", help="Reuse a run id (resumes its dir)."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Refine a report until no eligible reviewer can find a material defect."""
    _setup_logging(verbose)
    config = Config.load(config_path)
    seed_text = seed.read_text() if seed else None
    # Nothing else owns signals in this command, and in a container `ra` is PID 1 —
    # which has no default SIGTERM disposition, so without this the signal is discarded
    # and docker waits out the entire grace period before killing us.
    shutdown.install_handlers()

    try:
        final = run_graph(
            config, question=question, seed=seed_text, run_id=run_id, stop=shutdown.event()
        )
    except ConfigError as exc:
        console.print(f"[red]fail closed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    except GracefulStop as exc:
        console.print(f"\n[yellow]paused:[/yellow] {exc}")
        console.print(f"resume it with: [bold]ra run --run-id {exc.run_id} -q '{question}'[/bold]")
        raise typer.Exit(code=130) from exc

    status = final.get("terminal_status", "aborted")
    colour = {
        "accepted": "green",
        "converged_unconfirmed": "yellow",
        "exhausted_unresolved": "yellow",
        "needs_human_review": "red",
        "aborted": "red",
    }.get(status, "white")
    console.print(f"\n[{colour}]terminal status: {status}[/{colour}]")
    console.print(f"rounds: {final.get('round')}   run dir: {final.get('run_dir')}")
    raise typer.Exit(code=0 if status in ("accepted", "converged_unconfirmed") else 1)


@app.command()
def doctor(
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Check the proxy, resolve every alias, and report roster health — no run."""
    _setup_logging(verbose)
    config = Config.load(config_path)
    client = LLMClient(config)
    identities = client.resolve_identities(config.roster.all_aliases)

    table = Table(title="roster")
    table.add_column("alias")
    table.add_column("resolved identity")
    table.add_column("roles")
    table.add_column("structured output")
    table.add_column("audition")
    if config.search.enabled:
        table.add_column("tool calls")
    for alias in config.roster.all_aliases:
        roles_ = []
        if alias in config.roster.writers:
            roles_.append("writer")
        for lens, pool in config.roster.critics.items():
            if alias in pool:
                roles_.append(lens)
        if alias == config.roster.orchestrator_alias:
            roles_.append("orchestrator")
        mode = client.probe_structured_output(alias)
        row = [alias, identities[alias], ", ".join(roles_), mode, _audition_cell(config, identities, alias)]
        if config.search.enabled:
            # Only writers hold the tool today, so a critic's inability to call one
            # is information, not a problem.
            if alias not in config.roster.writers:
                row.append("[dim]n/a[/dim]")
            elif client.probe_tool_calling(alias):
                row.append("[green]yes[/green]")
            else:
                row.append("[red]NO[/red]")
        table.add_row(*row)
    console.print(table)

    warnings = validate_roster_health(config, identities)
    warnings += _audition_warnings(config, identities)
    for warning in warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")
    if not warnings:
        console.print("[green]roster healthy: every lens has >=2 eligible non-author models[/green]")

    if not config.search.enabled:
        console.print("[dim]web search: disabled (writers cite from model memory)[/dim]")
    else:
        try:
            search.resolve_token(config.search.api_key_env, config.search.token_file)
        except search.SearchConfigError as exc:
            console.print(f"[red]web search: {exc}[/red]")
            raise typer.Exit(code=1) from exc
        blind = [a for a in config.roster.writers if not client.probe_tool_calling(a)]
        if blind:
            console.print(
                f"[red]web search: writers cannot emit tool calls: {blind} — a run "
                f"would fail closed at startup[/red]"
            )
            raise typer.Exit(code=1)
        console.print(
            f"[green]web search: ready ({config.search.query_budget} queries/run)[/green]"
        )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address. Use 0.0.0.0 in a container."),
    port: int = typer.Option(8080, "--port"),
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    concurrent: int = typer.Option(1, "--concurrent", help="Runs executed at once."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Serve the web interface.

    There is no authentication: the intended posture is tailnet-only, with Tailscale
    ACLs as the access control. Anyone who can reach this can spend tokens and read
    every stored run, so do not bind it to a public interface.
    """
    _setup_logging(verbose)
    import uvicorn

    from .web import create_app

    config = Config.load(config_path)
    if host not in ("127.0.0.1", "localhost", "::1"):
        console.print(
            f"[yellow]note:[/yellow] binding {host}:{port} with no authentication — "
            f"make sure this interface is not publicly reachable"
        )
    console.print(f"serving on http://{host}:{port}  (runs dir: {config.runs_dir})")
    # Deadlines nest: the platform's SIGTERM-to-SIGKILL budget contains uvicorn's
    # connection drain, which contains the worker's wait for a node boundary. Deriving
    # all three from one number keeps them in that order when the platform is retuned;
    # three independent constants would eventually invert without anyone noticing.
    uvicorn.run(
        create_app(config, max_concurrent=concurrent),
        host=host,
        port=port,
        timeout_graceful_shutdown=int(shutdown.grace_seconds() * 0.8),
    )


@app.command()
def purge(
    run_id: str = typer.Argument(..., help="Run id to purge."),
    content_only: bool = typer.Option(
        False, "--content-only", help="Drop reports/critiques, keep the decision record."
    ),
    config_path: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Delete a run's stored material."""
    config = Config.load(config_path)
    removed = purge_run(config.runs_dir, run_id, content_only=content_only)
    for path in removed:
        console.print(f"removed {path}")


@app.command()
def expired(config_path: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """List runs past the retention window."""
    config = Config.load(config_path)
    names = expired_runs(config.runs_dir, config.retention_days)
    if not names:
        console.print("no runs past retention")
    for name in names:
        console.print(name)


if __name__ == "__main__":  # pragma: no cover
    app()


@app.command()
def audition(
    config_path: Path | None = typer.Option(None, "--config", "-c", help="Roster config YAML."),
    fixtures_dir: Path | None = typer.Option(None, "--fixtures", help="Fixture corpus dir."),
    lens_filter: str | None = typer.Option(None, "--lens", help="Audition one lens only."),
    alias_filter: str | None = typer.Option(None, "--alias", help="Audition one alias only."),
    force: bool = typer.Option(False, "--force", help="Ignore cached results and re-run."),
    as_json: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Measure whether each rostered critic can actually perform its lens.

    Exits non-zero if any model assigned to a critic pool is `unfit` — a lens staffed
    by such a model is not being reviewed, whatever the run's counters say.
    """
    _setup_logging(verbose)
    config = Config.load(config_path)
    cfg = config.audition
    client = LLMClient(config)
    identities = client.resolve_identities(config.roster.all_aliases)

    try:
        fixtures = audition_mod.load_fixtures(fixtures_dir)
    except audition_mod.FixtureError as exc:
        console.print(f"[red]fixtures:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    slots = audition_mod.assignments(config.roster, identities)
    if lens_filter:
        slots = tuple(s for s in slots if s.lens.value == lens_filter)
    if alias_filter:
        slots = tuple(s for s in slots if s.alias == alias_filter)
    if not slots:
        console.print("[yellow]no critic slots match those filters[/yellow]")
        raise typer.Exit(code=0)

    now = time.time()
    ph = audition_mod.prompt_hash()
    cache = {} if force else audition_mod.load_cache(cfg.cache_path)

    stale_or_missing = [
        s
        for s in slots
        if not _cache_usable(cache, s, fixtures.corpus_hash, ph, cfg, now)
    ]
    if stale_or_missing:
        calls = len(stale_or_missing) * len(fixtures.fixtures) * cfg.repetitions
        console.print(
            f"auditioning {len(stale_or_missing)} slot(s) against "
            f"{len(fixtures.fixtures)} fixtures x{cfg.repetitions} — up to {calls} calls"
        )
        measured = audition_mod.run_audition(
            client,
            config.roster,
            identities,
            fixtures,
            cfg,
            require_verbatim_spans=config.require_verbatim_spans,
            only=tuple(stale_or_missing),
        )
        for metrics in measured:
            cache[audition_mod.cache_key(metrics.identity, metrics.lens)] = (
                audition_mod.CacheEntry(
                    metrics=metrics,
                    corpus_hash=fixtures.corpus_hash,
                    prompt_hash=ph,
                    repetitions=cfg.repetitions,
                    recorded_at=now,
                )
            )
        audition_mod.save_cache(cfg.cache_path, cache)

    judgements: dict[tuple[str, Lens], audition_mod.Judgement] = {}
    rows: list[tuple[Assignment_t, audition_mod.Metrics | None, audition_mod.Judgement | None]] = []
    for slot in slots:
        entry = cache.get(audition_mod.cache_key(slot.identity, slot.lens))
        if entry is None or not entry.matches(fixtures.corpus_hash, ph, cfg.repetitions):
            rows.append((slot, None, None))
            continue
        judgement = audition_mod.judge(entry.metrics, cfg.thresholds)
        judgements[(slot.identity, slot.lens)] = judgement
        rows.append((slot, entry.metrics, judgement))

    if as_json:
        console.print_json(
            data={
                "corpus_hash": fixtures.corpus_hash,
                "prompt_hash": ph,
                "slots": [
                    {
                        "alias": s.alias,
                        "identity": s.identity,
                        "lens": s.lens.value,
                        "position": s.position,
                        "metrics": m.model_dump(mode="json") if m else None,
                        "verdict": j.verdict.value if j else audition_mod.Status.NOT_AUDITED.value,
                        "reasons": list(j.reasons) if j else [],
                    }
                    for s, m, j in rows
                ],
            }
        )
    else:
        _render_audition(rows)

    for warning in audition_mod.roster_warnings(config.roster, identities, judgements):
        console.print(f"[yellow]warning:[/yellow] {warning}")

    unfit = [s.alias for s, _, j in rows if j and j.verdict is audition_mod.Verdict.UNFIT]
    if unfit:
        console.print(f"[red]unfit critics assigned to lens pools: {sorted(set(unfit))}[/red]")
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


def _cache_usable(cache, slot, corpus_hash, ph, cfg, now) -> bool:
    entry = cache.get(audition_mod.cache_key(slot.identity, slot.lens))
    if entry is None or not entry.matches(corpus_hash, ph, cfg.repetitions):
        return False
    return not entry.is_stale(now, cfg.max_age_days)


def _render_audition(rows) -> None:
    table = Table(title="critic audition")
    for column in ("alias", "lens", "pos", "strict", "lens sens", "obvious", "ctrl/run", "verdict"):
        table.add_column(column)
    colour = {
        audition_mod.Verdict.FIT: "green",
        audition_mod.Verdict.MARGINAL: "yellow",
        audition_mod.Verdict.UNFIT: "red",
        audition_mod.Verdict.INSUFFICIENT: "dim",
    }
    for slot, metrics, judgement in rows:
        if metrics is None or judgement is None:
            # Never blank: a blank cell reads as a pass.
            table.add_row(
                slot.alias, slot.lens.value, str(slot.position + 1), "-", "-", "-", "-",
                f"[dim]{audition_mod.Status.NOT_AUDITED.value}[/dim]",
            )
            continue
        style = colour[judgement.verdict]
        table.add_row(
            slot.alias,
            slot.lens.value,
            str(slot.position + 1),
            f"{metrics.strict_sensitivity:.2f}",
            f"{metrics.lens_sensitivity:.2f}",
            f"{metrics.obvious_sensitivity:.2f}",
            f"{metrics.control_material_rate:.2f}",
            f"[{style}]{judgement.verdict.value}[/{style}]",
        )
    console.print(table)
    for slot, _, judgement in rows:
        for reason in judgement.reasons if judgement else ():
            console.print(f"[yellow]{slot.alias} / {slot.lens.value}:[/yellow] {reason}")


def _audition_cells(config: Config, identities: dict[str, str]) -> dict[str, str]:
    """Per-alias audition summary for `ra doctor`, read from the cache.

    Never returns a blank for a critic: a blank cell reads as a pass, and the whole
    point of the harness is that an unmeasured critic is *visibly* unmeasured.
    """
    slots = audition_mod.assignments(config.roster, identities)
    try:
        corpus_hash = audition_mod.load_fixtures().corpus_hash
    except audition_mod.FixtureError:
        corpus_hash = None
    cache = audition_mod.load_cache(config.audition.cache_path)
    ph = audition_mod.prompt_hash()
    now = time.time()

    per_alias: dict[str, list[str]] = {}
    for slot in slots:
        entry = cache.get(audition_mod.cache_key(slot.identity, slot.lens))
        if entry is None or corpus_hash is None or not entry.matches(
            corpus_hash, ph, config.audition.repetitions
        ):
            cell = f"[dim]{audition_mod.Status.NOT_AUDITED.value}[/dim]"
        elif entry.is_stale(now, config.audition.max_age_days):
            cell = f"[yellow]{audition_mod.Status.STALE.value}[/yellow]"
        else:
            verdict = audition_mod.judge(entry.metrics, config.audition.thresholds).verdict
            style = {
                audition_mod.Verdict.FIT: "green",
                audition_mod.Verdict.MARGINAL: "yellow",
                audition_mod.Verdict.UNFIT: "red",
                audition_mod.Verdict.INSUFFICIENT: "dim",
            }[verdict]
            cell = f"[{style}]{slot.lens.value[:4]}:{verdict.value}[/{style}]"
        per_alias.setdefault(slot.alias, []).append(cell)
    return {alias: " ".join(cells) for alias, cells in per_alias.items()}


def _audition_cell(config: Config, identities: dict[str, str], alias: str) -> str:
    # Writers and the orchestrator hold no lens, so "not audited" would be misleading
    # rather than informative — they are not critics and nothing measures them here.
    return _audition_cells(config, identities).get(alias, "[dim]n/a[/dim]")


def _audition_warnings(config: Config, identities: dict[str, str]) -> list[str]:
    """Roster-level audition warnings, from cached verdicts only.

    `ra doctor` must not spend an audition's worth of calls, so anything unmeasured is
    simply absent here and shows as `not audited` in the table.
    """
    try:
        corpus_hash = audition_mod.load_fixtures().corpus_hash
    except audition_mod.FixtureError:
        return []
    cache = audition_mod.load_cache(config.audition.cache_path)
    ph = audition_mod.prompt_hash()
    now = time.time()

    judgements: dict[tuple[str, Lens], audition_mod.Judgement] = {}
    for slot in audition_mod.assignments(config.roster, identities):
        entry = cache.get(audition_mod.cache_key(slot.identity, slot.lens))
        if entry is None or not entry.matches(corpus_hash, ph, config.audition.repetitions):
            continue
        if entry.is_stale(now, config.audition.max_age_days):
            continue
        judgements[(slot.identity, slot.lens)] = audition_mod.judge(
            entry.metrics, config.audition.thresholds
        )
    return audition_mod.roster_warnings(config.roster, identities, judgements)
