from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import browser as browser_mod
from .classify import STOPPING, CodeStatus
from .codes import mask_code
from .config import DEFAULT_CDP_PORT, DEFAULT_CSV, RESULTS_DIR, SHARE_DIR, ensure_dirs
from .store import CsvStore

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


def _store(csv_path: Path) -> CsvStore:
    if not csv_path.exists():
        raise typer.BadParameter(
            f"no codes file at {csv_path}. Export the Google Sheet as CSV and run: ptcgl-redeem import FILE"
        )
    return CsvStore(csv_path)


def _close_cdp(pw_browser) -> None:
    try:
        pw_browser.new_browser_cdp_session().send("Browser.close")
    except Exception:
        pass


@app.command("import")
def import_csv(
    source: Path = typer.Argument(..., exists=True, readable=True, help="CSV exported from the TCG Codes sheet"),
    dest: Path = typer.Option(DEFAULT_CSV, "--dest", help="Local working copy"),
) -> None:
    """Copy a Sheet export into the local working CSV (never committed)."""
    ensure_dirs()
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    store = CsvStore(dest)
    codes = store.codes()
    pending = store.pending()
    console.print(
        f"Imported {len(codes)} codes ({len(pending)} pending) → {dest}"
    )


@app.command()
def status(
    csv_path: Path = typer.Option(DEFAULT_CSV, "--csv"),
    set_name: str | None = typer.Option(None, "--set"),
) -> None:
    """Show pending vs redeemed counts without opening a browser."""
    store = _store(csv_path)
    codes = store.codes()
    pending = store.pending(set_name=set_name)
    redeemed = [row for row in codes if row.redeemed or row.status in ("redeemed", "success")]
    rejected = [row for row in codes if row.status == "rejected"]

    table = Table(title="TCG Live codes")
    table.add_column("Set")
    table.add_column("Total", justify="right")
    table.add_column("Redeemed", justify="right")
    table.add_column("Rejected", justify="right")
    table.add_column("Pending", justify="right")

    sets: dict[str, Counter] = {}
    for row in codes:
        if set_name and row.set_name.casefold() != set_name.casefold():
            continue
        bucket = sets.setdefault(row.set_name or "(none)", Counter())
        bucket["total"] += 1
        if row.redeemed or row.status in ("redeemed", "success"):
            bucket["redeemed"] += 1
        elif row.status == "rejected":
            bucket["rejected"] += 1
        else:
            bucket["pending"] += 1

    for name, counts in sets.items():
        table.add_row(
            name,
            str(counts["total"]),
            str(counts["redeemed"]),
            str(counts["rejected"]),
            str(counts["pending"]),
        )
    console.print(table)
    console.print(
        f"{len(codes)} codes  |  {len(redeemed)} redeemed  |  {len(rejected)} rejected  |  {len(pending)} pending"
    )


@app.command()
def login(
    cdp_port: int = typer.Option(DEFAULT_CDP_PORT, "--cdp-port"),
) -> None:
    """Open the redeem site in a persistent Brave/Chrome profile and wait until you sign in."""
    from playwright.sync_api import sync_playwright

    from .flow import FlowError, wait_until_logged_in

    ensure_dirs()
    cdp_url, proc = browser_mod.launch_or_attach(port=cdp_port)
    try:
        with sync_playwright() as playwright:
            pw_browser = playwright.chromium.connect_over_cdp(cdp_url)
            try:
                context = pw_browser.contexts[0] if pw_browser.contexts else pw_browser.new_context()
                page = context.new_page()
                console.print(
                    "Complete Pokémon Trainer Club login in the browser (2FA is fine). Waiting…"
                )
                try:
                    wait_until_logged_in(page)
                except FlowError as exc:
                    console.print(f"[red]{exc}[/red]")
                    raise typer.Exit(1) from exc
                console.print("[green]Logged in. Session is saved to the local browser profile.[/green]")
            finally:
                _close_cdp(pw_browser)
    finally:
        browser_mod.shutdown(proc)


@app.command("run")
def run_redeem(
    csv_path: Path = typer.Option(DEFAULT_CSV, "--csv"),
    set_name: str | None = typer.Option(None, "--set", help="Only this set, e.g. 'Destined Rivals'"),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Stop after N pending codes"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Verify only; never click Redeem"),
    cdp_port: int = typer.Option(DEFAULT_CDP_PORT, "--cdp-port"),
) -> None:
    """Redeem pending codes. Writes status back to the CSV after each code."""
    from playwright.sync_api import sync_playwright

    from .flow import FlowError, redeem_codes, wait_until_logged_in

    ensure_dirs()
    store = _store(csv_path)
    queue = store.pending(set_name=set_name, limit=limit)
    if not queue:
        console.print("Nothing pending.")
        raise typer.Exit(0)

    console.print(
        f"{'Dry-run verifying' if dry_run else 'Redeeming'} {len(queue)} codes from {csv_path}"
    )

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    result_path = RESULTS_DIR / f"{run_id}.jsonl"

    def persist(code: str, status: CodeStatus, detail: str) -> None:
        redeemed = status == CodeStatus.SUCCESS
        store.mark(code, redeemed=redeemed, status=status.value, detail=detail)
        with result_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "code": mask_code(code),
                        "status": status.value,
                        "detail": detail,
                        "ts": datetime.now(UTC).isoformat(),
                    }
                )
                + "\n"
            )
        color = {
            CodeStatus.SUCCESS: "green",
            CodeStatus.VALID_NOT_REDEEMED: "cyan",
            CodeStatus.REJECTED: "yellow",
            CodeStatus.CAPTCHA: "red",
            CodeStatus.INDETERMINATE: "red",
            CodeStatus.FATAL: "red",
        }.get(status, "white")
        console.print(f"[{color}]{mask_code(code):>8}  {status.value:<20} {detail}[/{color}]")

    cdp_url, proc = browser_mod.launch_or_attach(port=cdp_port)
    exit_code = 0
    try:
        with sync_playwright() as playwright:
            pw_browser = playwright.chromium.connect_over_cdp(cdp_url)
            try:
                context = pw_browser.contexts[0] if pw_browser.contexts else pw_browser.new_context()
                page = context.new_page()
                try:
                    wait_until_logged_in(page)
                except FlowError as exc:
                    console.print(f"[red]login failed: {exc}[/red]")
                    raise typer.Exit(1) from exc

                results = redeem_codes(
                    page,
                    [row.code for row in queue],
                    dry_run=dry_run,
                    on_result=persist,
                )
                counts = Counter(status.value for _, status, _ in results)
                console.print(dict(counts))
                console.print(f"results: {result_path}")
                if any(status in STOPPING for _, status, _ in results):
                    exit_code = 1
            finally:
                _close_cdp(pw_browser)
    finally:
        browser_mod.shutdown(proc)

    raise typer.Exit(exit_code)


@app.command()
def where() -> None:
    """Print local data paths."""
    console.print(f"codes   {DEFAULT_CSV}")
    console.print(f"results {RESULTS_DIR}")
    console.print(f"share   {SHARE_DIR}")


if __name__ == "__main__":
    app()
