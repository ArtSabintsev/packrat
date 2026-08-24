from pathlib import Path

import pytest

from packrat.codes import is_code, iter_codes, mask_code, normalize_code, pending_codes
from packrat.store import CsvStore


def test_accepts_modern_and_legacy_codes():
    assert is_code("ABCDEFGHIJKLM")
    assert is_code("276-BLW2-ZVD-ZD6")
    assert not is_code("Email 1 — Destined Rivals (100/400)")
    assert not is_code("")


def test_normalize_strips_whitespace():
    assert normalize_code(" abcdefghijklm ") == "ABCDEFGHIJKLM"


def test_mask_does_not_leak_code():
    assert mask_code("ABCDEFGHIJKLM") == "…JKLM"
    assert "ABCD" not in mask_code("ABCDEFGHIJKLM")


def test_skips_headers_blanks_and_redeemed(tmp_path: Path):
    path = tmp_path / "codes.csv"
    path.write_text(
        "Code,Set,Batch,Date,Redeemed,Status,Detail\n"
        "Email 1 — Set (2/2),Set,2/2,2026-01-01,FALSE,,\n"
        "TESTCODE00001,Set,2/2,2026-01-01,TRUE,success,\n"
        "TESTCODE00002,Set,2/2,2026-01-01,FALSE,rejected,already used\n"
        "TESTCODE00003,Set,2/2,2026-01-01,FALSE,,\n"
        ",,,,\n",
        encoding="utf-8",
    )
    store = CsvStore(path)
    codes = store.codes()
    assert [row.code for row in codes] == ["TESTCODE00001", "TESTCODE00002", "TESTCODE00003"]
    pending = store.pending()
    assert [row.code for row in pending] == ["TESTCODE00003"]


def test_mark_roundtrip(tmp_path: Path):
    path = tmp_path / "codes.csv"
    path.write_text(
        "Code,Set,Batch,Date,Redeemed\nTESTCODE00001,Set,1/1,2026-01-01,FALSE\n",
        encoding="utf-8",
    )
    store = CsvStore(path)
    store.mark("TESTCODE00001", redeemed=True, status="success", detail="redeemed")
    again = CsvStore(path)
    row = again.codes()[0]
    assert row.redeemed is True
    assert row.status == "success"


def test_mark_matches_despite_case_and_whitespace(tmp_path: Path):
    path = tmp_path / "codes.csv"
    path.write_text(
        "Code,Set,Batch,Date,Redeemed\ntest code00001,Set,1/1,2026-01-01,FALSE\n",
        encoding="utf-8",
    )
    store = CsvStore(path)
    store.mark("TESTCODE00001", redeemed=True, status="success")
    assert CsvStore(path).codes()[0].redeemed is True


def test_mark_updates_duplicate_rows(tmp_path: Path):
    path = tmp_path / "codes.csv"
    path.write_text(
        "Code,Set,Batch,Date,Redeemed\n"
        "TESTCODE00001,Set,1/1,2026-01-01,FALSE\n"
        "TESTCODE00001,Set,1/1,2026-01-01,FALSE\n",
        encoding="utf-8",
    )
    store = CsvStore(path)
    store.mark("TESTCODE00001", redeemed=False, status="rejected", detail="already used")
    assert CsvStore(path).pending() == []


def test_mark_unknown_code_raises_masked(tmp_path: Path):
    path = tmp_path / "codes.csv"
    path.write_text("Code,Redeemed\nTESTCODE00001,FALSE\n", encoding="utf-8")
    store = CsvStore(path)
    try:
        store.mark("OTHERCODE99999", redeemed=True, status="success")
    except KeyError as exc:
        assert "OTHERCODE" not in str(exc)
        assert "…9999" in str(exc)
    else:
        raise AssertionError("expected KeyError")


def test_failed_save_leaves_original_intact(tmp_path: Path, monkeypatch):
    path = tmp_path / "codes.csv"
    original = "Code,Set,Batch,Date,Redeemed\nTESTCODE00001,Set,1/1,2026-01-01,FALSE\n"
    path.write_text(original, encoding="utf-8")
    store = CsvStore(path)

    def boom(fd):
        raise OSError("disk full")

    monkeypatch.setattr("packrat.store.os.fsync", boom)
    try:
        store.mark("TESTCODE00001", redeemed=True, status="success")
    except OSError:
        pass
    assert path.read_text(encoding="utf-8") == original


def test_interim_statuses_stay_pending():
    rows = iter_codes(
        [
            {"Code": "TESTCODE00001", "Redeemed": "FALSE", "Status": "indeterminate"},
            {"Code": "TESTCODE00002", "Redeemed": "FALSE", "Status": "not_attempted"},
            {"Code": "TESTCODE00003", "Redeemed": "FALSE", "Status": "valid_not_redeemed"},
            {"Code": "TESTCODE00004", "Redeemed": "FALSE", "Status": "rejected"},
            {"Code": "TESTCODE00005", "Redeemed": "FALSE", "Status": "success"},
        ]
    )
    pending = pending_codes(rows)
    assert [row.code for row in pending] == [
        "TESTCODE00001",
        "TESTCODE00002",
        "TESTCODE00003",
    ]


def test_pending_set_filter():
    rows = iter_codes(
        [
            {
                "Code": "TESTCODE00001",
                "Set": "Destined Rivals",
                "Redeemed": "FALSE",
                "Status": "",
                "Detail": "",
            },
            {
                "Code": "TESTCODE00002",
                "Set": "Black Bolt",
                "Redeemed": "FALSE",
                "Status": "",
                "Detail": "",
            },
        ]
    )
    pending = pending_codes(rows, set_name="black bolt")
    assert [row.code for row in pending] == ["TESTCODE00002"]


class TestRedeemedRoundTrip:
    def test_redeemed_survives_a_csv_without_that_column(self, tmp_path):
        """A sheet export may omit Redeemed; marking must still stick.

        DictWriter is created with extrasaction="ignore", so if Redeemed is not
        in fieldnames the flag is silently dropped on save and the code reloads
        as pending -- resubmitted on every future run, forever.
        """
        csv_path = tmp_path / "codes.csv"
        csv_path.write_text("Code,Set\nTESTCODE00001,Example Set\n", encoding="utf-8")

        store = CsvStore(csv_path)
        store.mark("TESTCODE00001", redeemed=True, status="success", detail="redeemed")

        reloaded = CsvStore(csv_path)
        assert reloaded.codes()[0].redeemed is True
        assert reloaded.pending() == []


class TestSkipVocabulary:
    """The skip set must match the statuses the tool actually writes."""

    def _row(self, status: str, redeemed: str = "FALSE"):
        return {
            "Code": "TESTCODE00001", "Set": "S", "Batch": "", "Date": "",
            "Redeemed": redeemed, "Status": status, "Detail": "",
        }

    @pytest.mark.parametrize("status", ["success", "already_redeemed", "invalid"])
    def test_terminal_statuses_are_not_pending(self, status):
        # Terminal: redeeming again is impossible or pointless.
        assert pending_codes(iter_codes([self._row(status)])) == []

    @pytest.mark.parametrize("status", ["in_list", "indeterminate", ""])
    def test_unresolved_statuses_stay_pending(self, status):
        # Unresolved: the outcome is not yet known, so it must be retried.
        assert len(pending_codes(iter_codes([self._row(status)]))) == 1

    def test_legacy_rejected_is_still_skipped(self):
        # Written by an older version; existing sheets must not be re-run.
        assert pending_codes(iter_codes([self._row("rejected")])) == []
