"""Tests for the pure logic in the macOS client driver.

Everything here runs without the game, a screen grab, or OCR: the functions
under test take already-recognised text boxes, so the geometry and
classification rules can be exercised directly. The module imports pyobjc, so
the whole file is skipped off macOS.
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("Quartz", reason="macOS-only driver")
if sys.platform != "darwin":  # pragma: no cover
    pytest.skip("macOS-only driver", allow_module_level=True)

from packrat.macapp import Box, Layout, Outcome, Window, classify_status, find_text

# The client as it actually runs: fullscreen on a 2560x1440 display.
FULLSCREEN = Window(number=1, x=0.0, y=0.0, w=2560.0, h=1440.0)


def box(text: str, x: float, y: float) -> Box:
    return Box(text=text, x=x, y=y, w=100.0, h=30.0, confidence=1.0)


class TestClassifyStatus:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("SCANNED", Outcome.SUCCESS),
            ("YOU HAVE ALREADY REDEEMED THAT CODE.", Outcome.ALREADY_REDEEMED),
            ("THAT CODE IS NOT VALID.", Outcome.INVALID),
            ("ALREADY IN THE LIST", Outcome.IN_LIST),
            ("", Outcome.INDETERMINATE),
            ("SOMETHING NEW FROM AN APP UPDATE", Outcome.INDETERMINATE),
        ],
    )
    def test_known_statuses(self, text: str, expected: Outcome) -> None:
        assert classify_status(text) is expected

    def test_is_case_insensitive(self) -> None:
        assert classify_status("Scanned") is Outcome.SUCCESS

    def test_already_redeemed_wins_over_scanned(self) -> None:
        # Both words can co-occur once panel text bleeds in; owning the code is
        # the stronger claim and must not be misread as a fresh redemption.
        assert classify_status("SCANNED ... YOU HAVE ALREADY REDEEMED THAT CODE.") is (
            Outcome.ALREADY_REDEEMED
        )

    def test_invalid_wins_over_scanned(self) -> None:
        # Ordering must favour the outcome that does not claim a redemption.
        assert classify_status("SCANNED THAT CODE IS NOT VALID.") is Outcome.INVALID

    def test_in_list_wins_over_scanned(self) -> None:
        assert classify_status("ALREADY IN THE LIST SCANNED") is Outcome.IN_LIST

    def test_unknown_status_never_reports_success(self) -> None:
        # An unrecognised message must stop the run, never mark a code redeemed.
        assert classify_status("DAILY LIMIT REACHED") is Outcome.INDETERMINATE


class TestStatusRegion:
    def test_reads_the_status_label(self) -> None:
        layout = Layout(FULLSCREEN)
        assert layout.status_text([box("SCANNED", 688, 1019)]) == "SCANNED"

    def test_ignores_rewards_panel_at_the_same_height(self) -> None:
        # Regression: the status band once spanned the full window width, so
        # pack SKUs in the rewards panel leaked in ("SCANNED ENSV9BST") and the
        # cleared-field check could never pass.
        layout = Layout(FULLSCREEN)
        boxes = [box("SCANNED", 688, 1019), box("ENSV9BST", 1786, 1020)]
        assert layout.status_text(boxes) == "SCANNED"

    def test_ignores_text_outside_the_vertical_band(self) -> None:
        layout = Layout(FULLSCREEN)
        boxes = [box("SUBMIT CODE", 659, 1206), box("ENTER YOUR CODE", 525, 1083)]
        assert layout.status_text(boxes) == ""

    def test_empty_when_nothing_matches(self) -> None:
        assert Layout(FULLSCREEN).status_text([]) == ""


class TestFieldRegion:
    """Coordinates below are real observations from a live 2560x1440 client."""

    def test_reads_the_typed_code(self) -> None:
        # The code text was consistently recognised at y=1083 across a 4,000
        # code run, whether placeholder or a real code.
        assert Layout(FULLSCREEN).field_text([box("TESTCODE00042", 528, 1083)]) == (
            "TESTCODE00042"
        )

    def test_excludes_the_status_label_above_it(self) -> None:
        # Status sits at y=1020; reading it as field content would make every
        # paste look wrong and block all submissions.
        layout = Layout(FULLSCREEN)
        boxes = [box("THAT CODE IS NOT VALID.", 576, 1020), box("ABCDEFGHIJKLM", 528, 1083)]
        assert layout.field_text(boxes) == "ABCDEFGHIJKLM"

    def test_excludes_the_submit_button_below_it(self) -> None:
        assert Layout(FULLSCREEN).field_text([box("SUBMIT CODE", 659, 1206)]) == ""

    def test_excludes_the_rewards_panel(self) -> None:
        layout = Layout(FULLSCREEN)
        boxes = [box("ABCDEFGHIJKLM", 528, 1083), box("ENZSV10P5BST", 1786, 1085)]
        assert layout.field_text(boxes) == "ABCDEFGHIJKLM"


class TestLayout:
    def test_targets_land_inside_the_window(self) -> None:
        layout = Layout(FULLSCREEN)
        for name in ("field", "clear", "submit", "collect_panel", "collect_modal", "done"):
            x, y = getattr(layout, name)
            assert 0 < x < FULLSCREEN.w and 0 < y < FULLSCREEN.h, name

    def test_targets_follow_a_moved_window(self) -> None:
        moved = Window(number=1, x=2560.0, y=30.0, w=2560.0, h=1440.0)
        assert Layout(moved).submit == pytest.approx((2560 + 769, 30 + 1224), abs=1)

    def test_targets_scale_with_a_smaller_window(self) -> None:
        half = Window(number=1, x=0.0, y=0.0, w=1280.0, h=720.0)
        assert Layout(half).submit == pytest.approx((769 / 2, 1224 / 2), abs=1)

    def test_status_region_scales_too(self) -> None:
        half = Layout(Window(number=1, x=0.0, y=0.0, w=1280.0, h=720.0))
        # Same relative position as the full-size case, so it still reads.
        assert half.status_text([box("SCANNED", 344, 510)]) == "SCANNED"


class TestFindText:
    def test_matches_a_substring_case_insensitively(self) -> None:
        boxes = [box("BUNDLES", 67, 122), box("SUBMIT CODE", 659, 1206)]
        found = find_text(boxes, "submit code")
        assert found is not None and found.text == "SUBMIT CODE"

    def test_returns_none_when_absent(self) -> None:
        assert find_text([box("BUNDLES", 67, 122)], "SUBMIT CODE") is None

    def test_box_center(self) -> None:
        assert Box("X", 100.0, 200.0, 50.0, 20.0, 1.0).center == (125.0, 210.0)
