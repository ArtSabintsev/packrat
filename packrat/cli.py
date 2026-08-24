from __future__ import annotations

import json
import shutil
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .codes import hash_code, is_code, is_hashed, mask_code
from .config import DEFAULT_CSV, RESULTS_DIR, SHARE_DIR, ensure_dirs
from .store import CsvStore

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


def _store(csv_path: Path) -> CsvStore:
    if not csv_path.exists():
        raise typer.BadParameter(
            f"no codes file at {csv_path}. Export the Google Sheet as CSV and run: packrat import FILE"
        )
    return CsvStore(csv_path)


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



@app.command("run")
def redeem(
    csv_path: Path = typer.Option(DEFAULT_CSV, "--csv", help="Working CSV"),
    set_name: str | None = typer.Option(None, "--set", help="Only this set"),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Stop after N codes"),
    batch: int = typer.Option(10, "--batch", min=1, help="Collect rewards every N codes"),
    codes_log: Path | None = typer.Option(None, "--log", help="Append each result here"),
    print_codes: bool = typer.Option(
        False, "--print-codes", help="Show full codes instead of masking them"
    ),
) -> None:
    """Redeem pending codes through the Pokemon TCG Live macOS client.

    The client has no reCAPTCHA, so this is not rate-limited the way the web
    redeemer is. Codes are only written back as redeemed once the rewards list
    has been collected, since collection is what finalises them.
    """
    from . import macapp

    ensure_dirs()
    store = _store(csv_path)
    queue = store.pending(set_name=set_name, limit=limit)
    if not queue:
        console.print("Nothing pending.")
        return

    perms = macapp.permissions_report()
    missing = [k for k, v in perms.items() if not v]
    if missing:
        console.print(f"[red]Missing permissions: {', '.join(missing)}[/red]")
        raise typer.Exit(2)

    macapp.activate()
    layout = macapp.calibrate()
    console.print(f"[cyan]{len(queue)} pending; batch={batch}[/cyan]")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    result_path = RESULTS_DIR / f"macapp-{run_id}.jsonl"
    if codes_log is not None:
        codes_log.parent.mkdir(parents=True, exist_ok=True)

    counts: Counter[str] = Counter()
    # Outcomes that mean the account owns the code -- both stop it being retried.
    OWNED = {macapp.Outcome.SUCCESS, macapp.Outcome.ALREADY_REDEEMED,
             macapp.Outcome.IN_LIST}

    def persist(code: str, outcome: macapp.Outcome, detail: str) -> None:
        store.mark(code, redeemed=outcome in OWNED, status=outcome.value, detail=detail)
        counts[outcome.value] += 1
        ts = datetime.now(UTC).isoformat()
        # Codes are bearer tokens, so the console and the run journal mask them
        # by default. --log is opt-in and writes the real thing.
        shown = code if print_codes else mask_code(code)
        with result_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"code": shown, "status": outcome.value, "detail": detail, "ts": ts}) + "\n"
            )
        if codes_log is not None:
            with codes_log.open("a", encoding="utf-8") as handle:
                handle.write(f"{ts}\t{outcome.value}\t{code}\t{detail}\n")
                handle.flush()
        color = {
            macapp.Outcome.SUCCESS: "green",
            macapp.Outcome.ALREADY_REDEEMED: "yellow",
            macapp.Outcome.INVALID: "yellow",
        }.get(outcome, "red")
        console.print(f"[{color}]{shown}  {outcome.value:<18} {detail}[/{color}]")
        sys.stdout.flush()

    stop = False
    while queue and not stop:
        chunk, queue = queue[:batch], queue[batch:]
        staged: list[tuple[str, macapp.Outcome, str]] = []
        for row in chunk:
            try:
                outcome, detail = macapp.submit_code(layout, row.code)
            except macapp.MacAppError as exc:
                outcome, detail = macapp.Outcome.INDETERMINATE, str(exc)
            staged.append((row.code, outcome, detail))
            if outcome is macapp.Outcome.INDETERMINATE:
                stop = True
                break

        # Collection is what finalises a redemption, so it must succeed before
        # anything in this chunk is written back as redeemed.
        # IN_LIST means the code is already sitting in an uncollected rewards
        # list, so it needs draining too -- otherwise a chunk of only IN_LIST
        # codes never collects and they stay pending on every future run.
        needs_collect = {macapp.Outcome.SUCCESS, macapp.Outcome.IN_LIST}
        if any(o in needs_collect for _, o, _ in staged):
            try:
                note = macapp.collect_all(layout)
                console.print(f"[blue]{note}[/blue]")
            except macapp.MacAppError as exc:
                console.print(f"[red]collect failed: {exc} -- {len(staged)} codes left unmarked[/red]")
                raise typer.Exit(1) from exc

        for code, outcome, detail in staged:
            persist(code, outcome, detail)

    console.print(dict(counts))
    console.print(f"results: {result_path}")
    console.print(f"remaining pending: {len(store.pending())}")
    if stop:
        console.print("[red]Stopped on an unrecognised status. Check the client.[/red]")
        raise typer.Exit(1)

@app.command()
def status(
    csv_path: Path = typer.Option(DEFAULT_CSV, "--csv"),
    set_name: str | None = typer.Option(None, "--set"),
) -> None:
    """Show pending vs redeemed counts."""
    store = _store(csv_path)
    codes = store.codes()
    pending = store.pending(set_name=set_name)
    redeemed = [row for row in codes if row.redeemed or row.status in ("redeemed", "success")]
    TERMINAL_BAD = {"rejected", "invalid"}
    unusable = [row for row in codes if row.status in TERMINAL_BAD]

    table = Table(title="TCG Live codes")
    table.add_column("Set")
    table.add_column("Total", justify="right")
    table.add_column("Redeemed", justify="right")
    table.add_column("Unusable", justify="right")
    table.add_column("Pending", justify="right")

    sets: dict[str, Counter] = {}
    for row in codes:
        if set_name and row.set_name.casefold() != set_name.casefold():
            continue
        bucket = sets.setdefault(row.set_name or "(none)", Counter())
        bucket["total"] += 1
        if row.redeemed or row.status in ("redeemed", "success"):
            bucket["redeemed"] += 1
        elif row.status in TERMINAL_BAD:
            bucket["unusable"] += 1
        else:
            bucket["pending"] += 1

    for name, counts in sets.items():
        table.add_row(
            name,
            str(counts["total"]),
            str(counts["redeemed"]),
            str(counts["unusable"]),
            str(counts["pending"]),
        )
    console.print(table)
    console.print(
        f"{len(codes)} codes  |  {len(redeemed)} redeemed  |  "
        f"{len(unusable)} unusable  |  {len(pending)} pending"
    )



@app.command()
def doctor() -> None:
    """Check that macOS permissions and the game client are ready."""
    from . import macapp

    ok = True

    def line(label: str, good: bool, detail: str) -> None:
        nonlocal ok
        ok = ok and good
        mark = "[green]GRANTED[/green]" if good else "[red]MISSING[/red]"
        console.print(f"  {label:<18} {mark}  {detail}")

    console.print("[bold]permissions[/bold]")
    perms = macapp.permissions_report()
    line(
        "screen_recording",
        perms["screen_recording"],
        "" if perms["screen_recording"] else "System Settings > Privacy & Security > Screen "
        "& System Audio Recording, then RESTART this terminal",
    )
    line(
        "accessibility",
        perms["accessibility"],
        "" if perms["accessibility"] else "System Settings > Privacy & Security > Accessibility",
    )

    console.print("[bold]client[/bold]")
    try:
        window = macapp.find_window()
    except macapp.MacAppError as exc:
        line("window", False, str(exc))
        window = None
    else:
        line("window", True, f"{window.w:.0f}x{window.h:.0f} at ({window.x:.0f},{window.y:.0f})")

    if window is not None and perms["screen_recording"]:
        try:
            macapp.calibrate()
        except macapp.MacAppError as exc:
            line("redeem screen", False, str(exc))
        else:
            line("redeem screen", True, "layout matches")

    if not ok:
        console.print("\n[red]Not ready.[/red] Fix the items above, then run doctor again.")
        raise typer.Exit(1)
    console.print("\n[green]Ready.[/green] Run: packrat run --limit 10")


@app.command()
def scrub(
    csv_path: Path = typer.Option(DEFAULT_CSV, "--csv"),
    yes: bool = typer.Option(False, "--yes", help="Do not ask for confirmation"),
) -> None:
    """Replace stored codes with one-way hashes. Irreversible.

    Redemption codes are bearer tokens. Once they are spent the plaintext has no
    use, but it is still worth stealing, so this rewrites the working CSV and the
    run journals in place, leaving only SHA-256 fingerprints. Set, status and
    counts are preserved, and a code can still be recognised later by hashing it
    again -- it simply cannot be read back out.
    """
    store = _store(csv_path)
    pending = store.pending()
    if pending:
        # The CSV is the work queue; hashing it mid-run would destroy it.
        console.print(f"[red]{len(pending)} codes still pending. Finish the run first.[/red]")
        raise typer.Exit(1)

    already = sum(1 for row in store.rows if is_hashed(row.get("Code", "")))
    targets = [row for row in store.rows if is_code(row.get("Code", ""))]
    journals = sorted(RESULTS_DIR.glob("*.jsonl"))

    if not targets and already:
        console.print(f"Already scrubbed ({already} hashed rows).")
        return

    console.print(f"Will hash [bold]{len(targets)}[/bold] codes in {csv_path}")
    console.print(f"and rewrite [bold]{len(journals)}[/bold] run journals.")
    console.print("[yellow]This cannot be undone.[/yellow]")
    if not yes and not typer.confirm("Continue?"):
        raise typer.Abort()

    for row in targets:
        row["Code"] = hash_code(row["Code"])
    store.save()

    rewritten = 0
    for path in journals:
        lines = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            code = entry.get("code", "")
            if is_code(code):
                entry["code"] = hash_code(code)
                rewritten += 1
            lines.append(json.dumps(entry))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    console.print(f"[green]Scrubbed[/green] {len(targets)} codes and {rewritten} journal entries.")
    console.print("Check a code later with: shasum -a 256 <<< 'CODE'")

@app.command()
def where() -> None:
    """Print local data paths."""
    console.print(f"codes   {DEFAULT_CSV}")
    console.print(f"results {RESULTS_DIR}")
    console.print(f"share   {SHARE_DIR}")


if __name__ == "__main__":
    app()
