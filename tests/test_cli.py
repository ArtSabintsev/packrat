"""Tests for the redemption loop's write-back rules.

These monkeypatch the driver, so they run anywhere: no game, no screen capture,
no OCR. What they pin down is the part that decides whether an irreplaceable
code gets recorded as spent.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from packrat import cli
from packrat.store import CsvStore

macapp = pytest.importorskip("packrat.macapp", reason="macOS-only driver")
runner = CliRunner()

HEADER = "Code,Set,Batch,Date,Redeemed,Status,Detail\n"
CODES = ["TESTCODE00001", "TESTCODE00002", "TESTCODE00003"]


@pytest.fixture
def csv_path(tmp_path):
    p = tmp_path / "codes.csv"
    p.write_text(HEADER + "".join(f"{c},Set A,,,FALSE,,\n" for c in CODES), encoding="utf-8")
    return p


@pytest.fixture
def driver(monkeypatch, tmp_path):
    """Stub the whole macOS layer and record what the loop asks it to do."""
    calls = {"submitted": [], "collected": 0}

    monkeypatch.setattr(cli, "RESULTS_DIR", tmp_path / "results")
    (tmp_path / "results").mkdir()
    monkeypatch.setattr(macapp, "permissions_report",
                        lambda: {"screen_recording": True, "accessibility": True})
    monkeypatch.setattr(macapp, "activate", lambda *a, **k: None)
    monkeypatch.setattr(macapp, "calibrate", lambda: object())

    def collect(_layout):
        calls["collected"] += 1
        return "collected"

    monkeypatch.setattr(macapp, "collect_all", collect)

    def set_outcomes(outcomes):
        def submit(_layout, code, **_kw):
            calls["submitted"].append(code)
            return outcomes[code], str(outcomes[code])
        monkeypatch.setattr(macapp, "submit_code", submit)

    calls["set_outcomes"] = set_outcomes
    return calls


def run(csv_path, batch=10):
    return runner.invoke(cli.app, ["run", "--csv", str(csv_path), "--batch", str(batch)])


def test_success_is_recorded_after_collection(csv_path, driver):
    driver["set_outcomes"](dict.fromkeys(CODES, macapp.Outcome.SUCCESS))
    result = run(csv_path)
    assert result.exit_code == 0
    assert driver["collected"] == 1
    assert CsvStore(csv_path).pending() == []


def test_nothing_is_marked_when_collection_fails(csv_path, driver, monkeypatch):
    """Collection finalises a redemption, so a failure must record nothing.

    Marking codes redeemed here would claim rewards that were never collected.
    """
    driver["set_outcomes"](dict.fromkeys(CODES, macapp.Outcome.SUCCESS))
    before = csv_path.read_text()

    def boom(_layout):
        raise macapp.MacAppError("panel never opened")

    monkeypatch.setattr(macapp, "collect_all", boom)

    result = run(csv_path)
    assert result.exit_code == 1
    assert csv_path.read_text() == before
    assert len(CsvStore(csv_path).pending()) == 3


def test_already_redeemed_needs_no_collection(csv_path, driver):
    driver["set_outcomes"](dict.fromkeys(CODES, macapp.Outcome.ALREADY_REDEEMED))
    result = run(csv_path)
    assert result.exit_code == 0
    assert driver["collected"] == 0          # nothing was scanned, nothing to drain
    assert CsvStore(csv_path).pending() == []  # but the account owns them


def test_in_list_triggers_collection_and_resolves(csv_path, driver):
    """IN_LIST means rewards are already queued from an earlier crashed run.

    Without collecting, these codes stay pending and return IN_LIST forever.
    """
    driver["set_outcomes"](dict.fromkeys(CODES, macapp.Outcome.IN_LIST))
    result = run(csv_path)
    assert result.exit_code == 0
    assert driver["collected"] == 1
    assert CsvStore(csv_path).pending() == []


def test_invalid_is_terminal_and_not_owned(csv_path, driver):
    driver["set_outcomes"](dict.fromkeys(CODES, macapp.Outcome.INVALID))
    run(csv_path)
    store = CsvStore(csv_path)
    assert store.pending() == []                       # never retried
    assert all(not r.redeemed for r in store.codes())  # but not claimed either


def test_run_stops_on_an_unrecognised_status(csv_path, driver):
    outcomes = dict.fromkeys(CODES, macapp.Outcome.SUCCESS)
    outcomes[CODES[1]] = macapp.Outcome.INDETERMINATE
    driver["set_outcomes"](outcomes)

    result = run(csv_path)
    assert result.exit_code == 1
    assert driver["submitted"] == CODES[:2]   # third code never attempted
    assert CODES[2] in [r.code for r in CsvStore(csv_path).pending()]


def test_collects_once_per_batch(csv_path, driver):
    driver["set_outcomes"](dict.fromkeys(CODES, macapp.Outcome.SUCCESS))
    run(csv_path, batch=2)
    assert driver["collected"] == 2           # chunks of 2 and 1


def test_missing_permissions_abort_before_touching_the_client(csv_path, driver, monkeypatch):
    monkeypatch.setattr(macapp, "permissions_report",
                        lambda: {"screen_recording": False, "accessibility": True})
    result = run(csv_path)
    assert result.exit_code == 2
    assert driver["submitted"] == []
