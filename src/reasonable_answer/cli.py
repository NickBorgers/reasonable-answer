"""Command line entry point."""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import search
from .config import Config, ConfigError, validate_roster_health
from .graph import run as run_graph
from .llm import LLMClient
from .store import expired_runs
from .store import purge as purge_run

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

    try:
        final = run_graph(config, question=question, seed=seed_text, run_id=run_id)
    except ConfigError as exc:
        console.print(f"[red]fail closed:[/red] {exc}")
        raise typer.Exit(code=2) from exc

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
        row = [alias, identities[alias], ", ".join(roles_), mode]
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
    uvicorn.run(create_app(config, max_concurrent=concurrent), host=host, port=port)


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
