"""Command line entry point."""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import audition as audition_mod
from . import ingest, search, shutdown
from .audition import Assignment as Assignment_t
from .build import build_identity
from .config import Config, ConfigError, validate_roster_health
from .export import export_html, export_markdown
from .graph import GracefulStop
from .graph import run as run_graph
from .llm import LLMClient
from .store import CorruptRun, UnsafeRunId, expired_runs, read_run
from .store import purge as purge_run
from .taxonomy import Lens

app = typer.Typer(add_completion=False, help="reasonable-answer — isolation-pipeline report refiner")
console = Console()

#: Top-level modules that only the `web` extra installs. An ImportError naming one of
#: these means "not installed"; anything else means the web layer itself is broken.
_WEB_EXTRA_MODULES = {"fastapi", "markdown_it", "uvicorn", "multipart", "starlette"}


#: Names a level for `ra` itself, for a deployment that cannot pass `--verbose` — the
#: container's CMD is fixed. Set because a night of aborted production runs left only
#: WARNING lines behind: no run starts, no controller decisions, no search results, so
#: the post-mortem was inference rather than reading (D-provider-retry).
LOG_LEVEL_ENV = "RA_LOG_LEVEL"


def _setup_logging(verbose: bool) -> None:
    level = logging.INFO if verbose else logging.WARNING
    # Env wins over the flag: `--verbose` can only ever raise verbosity, so honouring
    # an explicit RA_LOG_LEVEL is the only way a deployment can ask for less than INFO
    # once it has asked for more. An unparseable value keeps the flag's level rather
    # than failing a run over a logging preference.
    named = (os.environ.get(LOG_LEVEL_ENV) or "").strip().upper()
    if named:
        resolved = logging.getLevelNamesMapping().get(named)
        if resolved is None:
            print(f"warning: ignoring unknown ${LOG_LEVEL_ENV}={named!r}", file=sys.stderr)
        else:
            level = resolved
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


@app.command()
def run(
    question: str = typer.Option(..., "--question", "-q", help="The question to answer."),
    # Deliberately `str`, not `Path`: `Path("https://a/b")` normalises to 'https:/a/b'
    # and silently eats the slash, so a URL seed would be corrupted before it was read.
    seed: str | None = typer.Option(
        None,
        "--seed",
        "-s",
        help="Optional seed report: .md, .txt, .html, .pdf, .docx, or an http(s) URL.",
    ),
    config_path: Path | None = typer.Option(None, "--config", "-c", help="Roster config YAML."),
    run_id: str | None = typer.Option(None, "--run-id", help="Reuse a run id (resumes its dir)."),
    owner: str | None = typer.Option(
        None,
        "--owner",
        help="Identity this run belongs to; without it the run is CLI-only "
        "and the web interface will not serve it.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Refine a report until no eligible reviewer can find a material defect."""
    _setup_logging(verbose)
    config = Config.load(config_path)
    # Convert at the edge, so the graph only ever sees markdown and the text that is
    # hashed into the resume fingerprint is the text that gets stored and critiqued.
    ingested = None
    if seed:
        try:
            ingested = ingest.from_seed_arg(seed, config=config)
        except ingest.IngestError as exc:
            console.print(f"[red]cannot use that seed:[/red] {exc}")
            raise typer.Exit(code=2) from exc
        for warning in ingested.warnings:
            console.print(f"[yellow]warning:[/yellow] {warning}")

    # Nothing else owns signals in this command, and in a container `ra` is PID 1 —
    # which has no default SIGTERM disposition, so without this the signal is discarded
    # and docker waits out the entire grace period before killing us.
    shutdown.install_handlers()

    try:
        final = run_graph(
            config,
            question=question,
            seed=ingested.markdown if ingested else None,
            run_id=run_id,
            stop=shutdown.event(),
            seed_format=ingested.format if ingested else None,
            seed_source=ingested.source if ingested else None,
            seed_warnings=list(ingested.warnings) if ingested else None,
            owner=owner,
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
    modes: dict[str, str] = {}
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
        modes[alias] = mode
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
    warnings += _audition_warnings(config, identities, modes)
    for warning in warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")
    if not warnings:
        console.print(
            "[green]roster healthy: every lens has >=2 eligible non-author model families[/green]"
        )

    # Surfaced here because `unknown` is otherwise invisible: it costs nothing at the time
    # and only shows up much later, as a month of runs that cannot be attributed to a
    # commit (D-run-build-stamp).
    build = build_identity()
    if build.source == "unknown":
        console.print(
            "[yellow]warning:[/yellow] build unknown — runs will not record which commit "
            "produced them. Set RA_BUILD_SHA at image build time (docs/run-provenance.md)."
        )
    else:
        console.print(f"[dim]build: {build.describe()} (from {build.source})[/dim]")

    refine_line = _refine_doctor_line(config, client)
    if refine_line:
        console.print(refine_line)

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
            f"[green]web search: ready ("
            f"{'unbounded' if config.search.query_budget is None else config.search.query_budget}"
            f" queries/run)[/green]"
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

    Callers are identified by a header set by whatever fronts the app — Cloudflare
    Access or `tailscale serve` — and a request carrying none is refused. The header
    is trusted rather than verified, so anyone who can reach this port directly can
    claim to be any user: keep it bound to loopback or the tailnet and let the proxy
    be the only way in (docs/authentication.md).

    For local development, `$RA_DEV_IDENTITY` (or `auth.dev_identity`) supplies an
    identity when no header is present.
    """
    _setup_logging(verbose)
    import uvicorn

    from .web import create_app

    config = Config.load(config_path)
    if host not in ("127.0.0.1", "localhost", "::1"):
        console.print(
            f"[yellow]note:[/yellow] binding {host}:{port} — identity headers are trusted, "
            f"not verified, so make sure this interface is only reachable through the "
            f"proxy that sets them"
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
def export(
    run_id: str = typer.Argument(..., help="Run id to export."),
    fmt: str = typer.Option("md", "--format", "-f", help="md or html."),
    out: Path | None = typer.Option(None, "--out", "-o", help="Write here instead of stdout."),
    config_path: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Write a finished run as a shareable document: the report plus its review record.

    `runs/<id>/final.md` is the report on its own, which says nothing about whether it
    was accepted or shipped with blocking defects outstanding. This adds that.
    """
    if fmt not in ("md", "html"):
        console.print(f"[red]unknown format:[/red] {fmt} (expected md or html)")
        raise typer.Exit(code=2)

    config = Config.load(config_path)
    try:
        question, report, final = read_run(config.runs_dir, run_id)
    except UnsafeRunId:
        # A run id is a path component. Rejecting it is a usage error, not a lookup
        # that came back empty, so it does not share the "no such run" exit code.
        console.print(f"[red]invalid run id:[/red] {run_id}")
        raise typer.Exit(code=2) from None
    except FileNotFoundError:
        console.print(f"[red]no such run:[/red] {run_id}")
        raise typer.Exit(code=1) from None
    except CorruptRun as exc:
        # Refusing beats exporting: with an unreadable final.json the status is
        # unknown, and every export is required to state one.
        console.print(f"[red]cannot describe this run:[/red] {exc}")
        raise typer.Exit(code=1) from None

    if report is None:
        # `purge --content-only` removes final.md and keeps the decision record, so
        # this is a normal state for an old run, not a corrupt one.
        console.print(f"[red]no report stored for[/red] {run_id} (never finished, or purged)")
        raise typer.Exit(code=1)

    render = export_html if fmt == "html" else export_markdown
    try:
        document = render(question, report, final, run_id)
    except ImportError as exc:
        # Only a *missing optional dependency* is reported as one. An ImportError from
        # inside the web layer itself is a defect, and hiding it behind installation
        # advice would send someone to reinstall a package they already have.
        if exc.name not in _WEB_EXTRA_MODULES:
            raise
        console.print(f"[red]html export needs the web extra[/red] ({exc}): uv sync --extra web")
        raise typer.Exit(code=2) from None

    if out is None:
        # Not `console.print`: rich would interpret the report's own brackets as markup.
        typer.echo(document)
        return
    out.write_text(document)
    console.print(f"wrote {out}")


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

    # Pin every alias to the structured-output mode a run would pin it to, *before*
    # anything is measured or any cached verdict is read (D-audition-probe-parity).
    # Without this every audition call resolves `mode_for` to the default prompt mode,
    # so the harness certifies critics in an extraction regime production never runs
    # them in. Probing here rather than inside `run_assignment` is deliberate: the mode
    # is part of the cache identity below, so it has to be known before the freshness
    # check, and `probe_structured_output` memoises, making the harness's own calls free.
    # An alias that cannot be pinned at all fails closed exactly as `build_runtime`
    # would fail a run staffed by it — measuring it under a fallback mode would be the
    # same fidelity gap in a new place.
    try:
        modes = {slot.alias: client.probe_structured_output(slot.alias) for slot in slots}
    except ConfigError as exc:
        console.print(f"[red]fail closed:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    now = time.time()
    ph = audition_mod.prompt_hash()
    rh = audition_mod.rubric_hash()
    spans = config.require_verbatim_spans
    cache = {} if force else audition_mod.load_cache(cfg.cache_path)

    stale_or_missing = [
        s
        for s in slots
        if not _cache_usable(
            cache, s, fixtures.corpus_hash, ph, rh, spans, cfg, now, modes[s.alias]
        )
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
            require_verbatim_spans=spans,
            only=tuple(stale_or_missing),
        )
        for metrics in measured:
            cache[audition_mod.cache_key(metrics.identity, metrics.lens)] = (
                audition_mod.CacheEntry(
                    metrics=metrics,
                    corpus_hash=fixtures.corpus_hash,
                    prompt_hash=ph,
                    rubric_hash=rh,
                    require_verbatim_spans=spans,
                    structured_output_mode=modes[metrics.alias],
                    repetitions=cfg.repetitions,
                    recorded_at=now,
                )
            )
        audition_mod.save_cache(cfg.cache_path, cache)

    judgements: dict[tuple[str, Lens], audition_mod.Judgement] = {}
    rows: list[tuple[Assignment_t, audition_mod.Metrics | None, audition_mod.Judgement | None]] = []
    for slot in slots:
        entry = cache.get(audition_mod.cache_key(slot.identity, slot.lens))
        if entry is None or not entry.matches(
            fixtures.corpus_hash, ph, cfg.repetitions,
            rubric_hash=rh, require_verbatim_spans=spans,
            structured_output_mode=modes[slot.alias],
        ):
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
                "rubric_hash": rh,
                "require_verbatim_spans": spans,
                "slots": [
                    {
                        "alias": s.alias,
                        "identity": s.identity,
                        "lens": s.lens.value,
                        "position": s.position,
                        # The regime the numbers to the right were taken in. A report
                        # that does not say which extraction path it measured cannot be
                        # compared with one taken under another (D-audition-probe-parity).
                        "structured_output_mode": modes[s.alias],
                        "metrics": m.model_dump(mode="json") if m else None,
                        "verdict": j.verdict.value if j else audition_mod.Status.NOT_AUDITED.value,
                        "reasons": list(j.reasons) if j else [],
                    }
                    for s, m, j in rows
                ],
            }
        )
    else:
        _render_audition(rows, modes)

    for warning in audition_mod.roster_warnings(
        config.roster, identities, judgements, config.review
    ):
        console.print(f"[yellow]warning:[/yellow] {warning}")

    unfit = [s.alias for s, _, j in rows if j and j.verdict is audition_mod.Verdict.UNFIT]
    if unfit:
        console.print(f"[red]unfit critics assigned to lens pools: {sorted(set(unfit))}[/red]")
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


@app.command(name="audition-refine")
def audition_refine(
    config_path: Path | None = typer.Option(None, "--config", "-c", help="Roster config YAML."),
    fixtures_dir: Path | None = typer.Option(None, "--fixtures", help="Fixture corpus dir."),
    transforms: str | None = typer.Option(
        None,
        "--transforms",
        help="Comma-separated transform set to audition (default: the configured "
        "enabled set). Lets an operator measure a candidate set — e.g. with "
        "question_behind_the_question — without editing config.",
    ),
    force: bool = typer.Option(False, "--force", help="Ignore cached results and re-run."),
    as_json: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Measure whether the refine model respects the D-question-refinement guardrails (D-refine-audition).

    Graded mechanically against the fixture corpus: scope narrowing, disallowed
    transforms, dropped subjects, and chips manufactured for well-posed questions.
    Exits non-zero on `unfit` — but note that even then serving only warns:
    refinement degrades to silence by design, so fitness never gates runs.
    """
    _setup_logging(verbose)
    config = Config.load(config_path)
    try:
        from . import refine_audition as refine_mod
    except ImportError as exc:
        if exc.name not in _WEB_EXTRA_MODULES:
            raise
        console.print(f"[red]the refine audition needs the web extra[/red] ({exc}): uv sync --extra web")
        raise typer.Exit(code=2) from None

    cfg = config.audition.refine
    if transforms is not None:
        enabled = frozenset(t.strip() for t in transforms.split(",") if t.strip())
        unknown = enabled - set(refine_mod.REFINE_TRANSFORMS)
        if unknown or not enabled:
            console.print(f"[red]unknown transforms:[/red] {sorted(unknown) or '(none given)'}")
            raise typer.Exit(code=2)
    else:
        enabled = frozenset(config.refine.enabled_transforms)

    try:
        fixtures = refine_mod.load_refine_fixtures(fixtures_dir)
    except refine_mod.FixtureError as exc:
        console.print(f"[red]fixtures:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    skipped = [f.id for f in fixtures.fixtures if not f.runnable(enabled)]
    if skipped:
        # No silent caps: a skipped fixture must never read as a passed one.
        console.print(
            f"[dim]skipping {sorted(skipped)} — their transform is outside the "
            f"audited set (pass --transforms to include it)[/dim]"
        )

    client = LLMClient(config)
    alias = config.refine.effective_alias(config.roster)
    identity = client.resolve_identities([alias])[alias]
    # Same parity gap the critic audition had: unprobed, every call here would resolve
    # to the default prompt mode while `RefinementService.preflight` pins the strongest
    # supported one before serving (D-audition-probe-parity).
    try:
        mode = client.probe_structured_output(alias)
    except ConfigError as exc:
        console.print(f"[red]fail closed:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    now = time.time()
    ph = refine_mod.refine_prompt_hash(enabled)
    cache = {} if force else refine_mod.load_refine_cache(cfg.cache_path)
    key = refine_mod.refine_cache_key(identity, enabled)
    entry = cache.get(key)
    if (
        entry is None
        or not entry.matches(
            fixtures.corpus_hash, ph, cfg.repetitions, structured_output_mode=mode
        )
        or entry.is_stale(now, cfg.max_age_days)
    ):
        runnable = fixtures.runnable(enabled)
        console.print(
            f"auditioning '{alias}' ({identity}) against {len(runnable)} fixtures "
            f"x{cfg.repetitions} — up to {len(runnable) * cfg.repetitions} calls"
        )
        metrics = refine_mod.run_refine_audition(client, alias, identity, enabled, fixtures, cfg)
        entry = refine_mod.RefineCacheEntry(
            metrics=metrics,
            corpus_hash=fixtures.corpus_hash,
            prompt_hash=ph,
            structured_output_mode=mode,
            repetitions=cfg.repetitions,
            recorded_at=now,
        )
        cache[key] = entry
        refine_mod.save_refine_cache(cfg.cache_path, cache)

    metrics = entry.metrics
    judgement = refine_mod.judge_refine(metrics, cfg.thresholds)
    asymmetries = refine_mod.pair_asymmetries(fixtures, metrics)

    if as_json:
        console.print_json(
            data={
                "corpus_hash": fixtures.corpus_hash,
                "prompt_hash": ph,
                "alias": alias,
                "identity": identity,
                "structured_output_mode": mode,
                "transforms": sorted(enabled),
                "skipped_fixtures": sorted(skipped),
                "metrics": metrics.model_dump(mode="json"),
                "pair_asymmetries": asymmetries,
                "verdict": judgement.verdict.value,
                "reasons": list(judgement.reasons),
            }
        )
    else:
        _render_refine_audition(alias, metrics, judgement, asymmetries)

    raise typer.Exit(code=1 if judgement.verdict is refine_mod.Verdict.UNFIT else 0)


def _render_refine_audition(alias, metrics, judgement, asymmetries) -> None:
    table = Table(title="refine audition")
    for column in ("alias", "fire", "violations", "obvious", "ctrl/run", "schema", "verdict"):
        table.add_column(column)
    colour = {
        audition_mod.Verdict.FIT: "green",
        audition_mod.Verdict.MARGINAL: "yellow",
        audition_mod.Verdict.UNFIT: "red",
        audition_mod.Verdict.INSUFFICIENT: "dim",
    }
    style = colour[judgement.verdict]
    table.add_row(
        alias,
        f"{metrics.fire_rate:.2f}",
        f"{metrics.violation_rate:.2f}",
        f"{metrics.obvious_violation_rate:.2f}",
        f"{metrics.control_suggestion_rate:.2f}",
        f"{metrics.schema_failure_rate:.2f}",
        f"[{style}]{judgement.verdict.value}[/{style}]",
    )
    console.print(table)
    for reason in judgement.reasons:
        console.print(f"[yellow]{alias}:[/yellow] {reason}")
    for pair, spread in sorted(asymmetries.items()):
        # Diagnostic only, never a gate — the number the question_behind_the_question
        # enablement decision (D-question-refinement) was waiting on.
        console.print(f"[dim]mirror pair '{pair}': fire-rate spread {spread:.2f}[/dim]")


def _refine_doctor_line(config: Config, client: LLMClient) -> str | None:
    """One line for `ra doctor` when refinement is enabled: the cached refine
    audition verdict, never a fresh measurement. None when refinement is off."""
    if not config.refine.enabled:
        return None
    try:
        from . import refine_audition as refine_mod
    except ImportError:
        return "refine: [dim]web extra not installed[/dim]"
    alias = config.refine.effective_alias(config.roster)
    try:
        identity = client.resolve_identities([alias])[alias]
    except ConfigError as exc:
        return f"refine ('{alias}'): [red]{exc}[/red]"
    judgement = refine_mod.refine_cached_judgement(
        config.audition.refine, identity, frozenset(config.refine.enabled_transforms)
    )
    if judgement is None:
        return (
            f"refine ('{alias}'): [dim]{audition_mod.Status.NOT_AUDITED.value}[/dim] — "
            f"run `ra audition-refine` to measure it"
        )
    style = {
        audition_mod.Verdict.FIT: "green",
        audition_mod.Verdict.MARGINAL: "yellow",
        audition_mod.Verdict.UNFIT: "red",
        audition_mod.Verdict.INSUFFICIENT: "dim",
    }[judgement.verdict]
    line = f"refine ('{alias}'): [{style}]{judgement.verdict.value}[/{style}]"
    if judgement.verdict is audition_mod.Verdict.UNFIT:
        line += (
            " — suggestions may steer; refinement stays enabled (warn-only, D-refine-audition): "
            "re-roster refine.alias or re-measure with `ra audition-refine --force`"
        )
    return line


def _cache_usable(cache, slot, corpus_hash, ph, rh, spans, cfg, now, mode) -> bool:
    entry = cache.get(audition_mod.cache_key(slot.identity, slot.lens))
    if entry is None or not entry.matches(
        corpus_hash, ph, cfg.repetitions, rubric_hash=rh, require_verbatim_spans=spans,
        # This path has probed and is already spending, so it is the one place a
        # mode mismatch can be answered by re-measuring rather than reported
        # (D-audition-probe-parity).
        structured_output_mode=mode,
    ):
        return False
    return not entry.is_stale(now, cfg.max_age_days)


def _render_audition(rows, modes) -> None:
    table = Table(title="critic audition")
    # `cover` sits beside the rates because it says what they were measured over: a
    # fixture no call ever graded is absent from every denominator to its right
    # (D-audition-failure-coverage).
    columns = ("alias", "lens", "pos", "cover", "strict", "lens sens", "obvious", "ctrl/run",
               "verdict")
    for column in columns:
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
                slot.alias, slot.lens.value, str(slot.position + 1), "-", "-", "-", "-", "-",
                f"[dim]{audition_mod.Status.NOT_AUDITED.value}[/dim]",
            )
            continue
        style = colour[judgement.verdict]
        cover = f"{metrics.fixtures_covered}/{metrics.fixtures_owed}"
        table.add_row(
            slot.alias,
            slot.lens.value,
            str(slot.position + 1),
            cover if metrics.fixtures_covered == metrics.fixtures_owed else f"[red]{cover}[/red]",
            f"{metrics.strict_sensitivity:.2f}",
            f"{metrics.lens_sensitivity:.2f}",
            f"{metrics.obvious_sensitivity:.2f}",
            f"{metrics.control_material_rate:.2f}",
            f"[{style}]{judgement.verdict.value}[/{style}]",
        )
    console.print(table)
    # Said out loud rather than added as a tenth column: the mode is per alias, not per
    # slot, and a reader who does not know which extraction path produced a
    # `schema` count cannot interpret it (D-audition-probe-parity).
    if modes:
        pinned = ", ".join(f"{alias}={mode}" for alias, mode in sorted(modes.items()))
        console.print(f"[dim]measured under structured-output mode: {pinned}[/dim]")
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
    rh = audition_mod.rubric_hash()
    now = time.time()

    per_alias: dict[str, list[str]] = {}
    for slot in slots:
        entry = cache.get(audition_mod.cache_key(slot.identity, slot.lens))
        if entry is None or corpus_hash is None or not entry.matches(
            corpus_hash,
            ph,
            config.audition.repetitions,
            rubric_hash=rh,
            require_verbatim_spans=config.require_verbatim_spans,
            # Mode-agnostic, like the gate: doctor's own probe results are reported as
            # drift by `_audition_warnings`, never used to hide a cached verdict
            # (D-audition-probe-parity).
            structured_output_mode=None,
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


def _audition_warnings(
    config: Config, identities: dict[str, str], modes: dict[str, str]
) -> list[str]:
    """Roster-level audition warnings, from cached verdicts only.

    `ra doctor` must not spend an audition's worth of calls, so anything unmeasured is
    simply absent here and shows as `not audited` in the table.

    `modes` is what doctor's own structured-output probe already found — passed in as
    data, so this stays a cache read. A verdict measured under a different mode than the
    alias now pins to is still read (D-audition-probe-parity), so the divergence has to
    be reported or it is invisible.
    """
    judgements = audition_mod.cached_judgements(
        config.audition, config.roster, identities, config.require_verbatim_spans
    )
    warnings = audition_mod.roster_warnings(
        config.roster, identities, judgements, config.review
    )
    warnings += audition_mod.mode_drift(config.audition, config.roster, identities, modes)
    # Enforcement with nothing measured is the failure mode that got `audition.enabled`
    # deleted: a setting that reads as a safety control while gating nothing. It cannot
    # be an error (a fresh checkout has no cache and must still run) so it is said out
    # loud in the one place an operator goes to ask whether the roster is sound.
    if config.audition.enforce and not judgements:
        warnings.insert(
            0,
            "audition.enforce is on but no assigned critic has a usable verdict — the "
            "gate cannot block anything. Run `ra audition` to give it something to read",
        )
    return warnings
