"""Drive the Pokemon TCG Live macOS client.

The client is a Unity app: it renders its own UI and exposes no accessibility
tree, so there are no elements to query. Everything here works on pixels --
capture the window, OCR it with the macOS Vision framework to locate labels,
and synthesise clicks/keystrokes at the coordinates those labels imply.

Requires two TCC grants for the *terminal* running this code:
  - Screen & System Audio Recording  (window capture; without it capture
    returns None)
  - Accessibility                    (synthetic mouse/keyboard events)
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from enum import StrEnum

import Quartz
import Vision
from AppKit import NSPasteboard

APP_NAME = "Pokemon TCG Live"
UTF8 = "public.utf8-plain-text"


class MacAppError(RuntimeError):
    pass


@dataclass(frozen=True)
class Box:
    """A piece of recognised text, in global screen points (top-left origin)."""

    text: str
    x: float
    y: float
    w: float
    h: float
    confidence: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2, self.y + self.h / 2)


@dataclass(frozen=True)
class Window:
    number: int
    x: float
    y: float
    w: float
    h: float


def find_window(app_name: str = APP_NAME) -> Window:
    """Locate the app's largest on-screen window."""
    options = (
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    )
    listing = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []
    best: Window | None = None
    for entry in listing:
        if entry.get("kCGWindowOwnerName") != app_name:
            continue
        bounds = entry.get("kCGWindowBounds") or {}
        candidate = Window(
            number=int(entry.get("kCGWindowNumber", 0)),
            x=float(bounds.get("X", 0)),
            y=float(bounds.get("Y", 0)),
            w=float(bounds.get("Width", 0)),
            h=float(bounds.get("Height", 0)),
        )
        if candidate.w < 200 or candidate.h < 200:
            continue  # tooltips, shadows, helper windows
        if best is None or candidate.w * candidate.h > best.w * best.h:
            best = candidate
    if best is None:
        raise MacAppError(
            f"No on-screen window for {app_name!r}. Is the app running and un-minimised?"
        )
    return best


def activate(app_name: str = APP_NAME) -> None:
    """Bring the app to the front so it receives synthetic events."""
    from AppKit import NSWorkspace

    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.localizedName() == app_name:
            app.activateWithOptions_(1 << 1)  # NSApplicationActivateIgnoringOtherApps
            return
    raise MacAppError(f"{app_name!r} is not running")


def capture(window: Window):
    """Capture the window as a CGImage at nominal (point) resolution.

    Returns None when Screen Recording permission has not been granted -- the
    API fails silently rather than raising, which is the usual cause.
    """
    return Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        window.number,
        Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageNominalResolution,
    )


def ocr(image, window: Window, *, fast: bool = False) -> list[Box]:
    """Recognise every text run in the image, in global screen points."""
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(
        Vision.VNRequestTextRecognitionLevelFast
        if fast
        else Vision.VNRequestTextRecognitionLevelAccurate
    )
    request.setUsesLanguageCorrection_(False)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
    ok, err = handler.performRequests_error_([request], None)
    if not ok:
        raise MacAppError(f"Vision OCR failed: {err}")

    img_w = float(Quartz.CGImageGetWidth(image))
    img_h = float(Quartz.CGImageGetHeight(image))
    # The capture is the window, so image pixels map onto window points by this
    # ratio (2.0 on Retina if the nominal-resolution hint is ignored).
    scale_x = window.w / img_w if img_w else 1.0
    scale_y = window.h / img_h if img_h else 1.0

    boxes: list[Box] = []
    for obs in request.results() or []:
        candidates = obs.topCandidates_(1)
        if not candidates:
            continue
        best = candidates[0]
        bb = obs.boundingBox()  # normalised, origin BOTTOM-left
        px = bb.origin.x * img_w
        py = (1.0 - bb.origin.y - bb.size.height) * img_h  # flip to top-left
        boxes.append(
            Box(
                text=best.string(),
                x=window.x + px * scale_x,
                y=window.y + py * scale_y,
                w=bb.size.width * img_w * scale_x,
                h=bb.size.height * img_h * scale_y,
                confidence=float(best.confidence()),
            )
        )
    return boxes


def read_screen(*, fast: bool = False) -> tuple[Window, list[Box]]:
    window = find_window()
    image = capture(window)
    if image is None:
        raise MacAppError(
            "Window capture returned nothing. Grant your terminal "
            "'Screen & System Audio Recording' in System Settings > Privacy & "
            "Security, then restart the terminal."
        )
    return window, ocr(image, window, fast=fast)


def find_text(boxes: list[Box], needle: str) -> Box | None:
    """First box whose text contains `needle`, case-insensitively."""
    target = needle.casefold()
    for box in boxes:
        if target in box.text.casefold():
            return box
    return None


# --- input synthesis -------------------------------------------------------


def click(x: float, y: float, *, settle: float = 0.15) -> None:
    point = Quartz.CGPointMake(x, y)
    move = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, point, 0)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, move)
    time.sleep(0.05)
    for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        event = Quartz.CGEventCreateMouseEvent(None, kind, point, Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(0.03)
    time.sleep(settle)


def _key(keycode: int, *, flags: int = 0) -> None:
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
        if flags:
            Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(0.02)


KEY_V = 9


def set_clipboard(text: str) -> None:
    board = NSPasteboard.generalPasteboard()
    board.clearContents()
    if not board.setString_forType_(text, UTF8):
        raise MacAppError("could not write to the clipboard")


def clipboard_text() -> str:
    return NSPasteboard.generalPasteboard().stringForType_(UTF8) or ""


def paste_text(text: str) -> None:
    """Clipboard + Cmd-V. Far more reliable than per-character keycodes."""
    set_clipboard(text)
    time.sleep(0.08)
    _key(KEY_V, flags=Quartz.kCGEventFlagMaskCommand)


def permissions_report() -> dict[str, bool]:
    """Best-effort check of the two TCC grants this module needs."""
    trusted = bool(Quartz.CGPreflightScreenCaptureAccess())
    try:
        from ApplicationServices import AXIsProcessTrusted

        accessibility = bool(AXIsProcessTrusted())
    except Exception:
        accessibility = False
    return {"screen_recording": trusted, "accessibility": accessibility}


# --- redemption flow -------------------------------------------------------

# Hit targets as a fraction of the window, calibrated against the 2560x1440
# fullscreen client. Fractions (not pixels) so a differently sized window still
# lands, and `calibrate` refuses to run if the OCR anchors aren't where these
# imply they should be.
_FIELD_F = (0.3008, 0.7625)
_CLEAR_F = (0.3941, 0.7625)
_SUBMIT_F = (0.3004, 0.8500)
_COLLECT_PANEL_F = (0.8270, 0.9042)
_COLLECT_MODAL_F = (0.5000, 0.9465)
_DONE_F = (0.5008, 0.8917)
_SHOP_TAB_F = (0.1984, 0.0333)
_REDEEM_TAB_F = (0.2352, 0.0958)
_FIELD_TOP_F, _FIELD_BOT_F = 0.7430, 0.7780
_STATUS_TOP_F, _STATUS_BOT_F = 0.6910, 0.7290
# The status label sits under the code field on the left. The rewards panel
# occupies the same rows once it fills up, so bound the region in x too --
# otherwise pack SKUs leak into the status and it never reads as cleared.
_STATUS_RIGHT_F = 0.5500

STATUS_SCANNED = "SCANNED"
STATUS_ALREADY_REDEEMED = "ALREADY REDEEMED"
STATUS_NOT_VALID = "NOT VALID"
STATUS_IN_LIST = "ALREADY IN THE LIST"


class Outcome(StrEnum):
    SUCCESS = "success"
    ALREADY_REDEEMED = "already_redeemed"
    INVALID = "invalid"
    IN_LIST = "in_list"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class Layout:
    window: Window

    def _at(self, frac: tuple[float, float]) -> tuple[float, float]:
        return (self.window.x + self.window.w * frac[0], self.window.y + self.window.h * frac[1])

    @property
    def field(self) -> tuple[float, float]:
        return self._at(_FIELD_F)

    @property
    def clear(self) -> tuple[float, float]:
        return self._at(_CLEAR_F)

    @property
    def submit(self) -> tuple[float, float]:
        return self._at(_SUBMIT_F)

    @property
    def shop_tab(self) -> tuple[float, float]:
        return self._at(_SHOP_TAB_F)

    @property
    def redeem_tab(self) -> tuple[float, float]:
        return self._at(_REDEEM_TAB_F)

    @property
    def collect_panel(self) -> tuple[float, float]:
        return self._at(_COLLECT_PANEL_F)

    @property
    def collect_modal(self) -> tuple[float, float]:
        return self._at(_COLLECT_MODAL_F)

    @property
    def done(self) -> tuple[float, float]:
        return self._at(_DONE_F)

    def field_text(self, boxes: list[Box]) -> str:
        top = self.window.y + self.window.h * _FIELD_TOP_F
        bottom = self.window.y + self.window.h * _FIELD_BOT_F
        right = self.window.x + self.window.w * _STATUS_RIGHT_F
        return " ".join(
            b.text for b in boxes if top < b.y < bottom and b.x < right
        ).strip()

    def status_text(self, boxes: list[Box]) -> str:
        top = self.window.y + self.window.h * _STATUS_TOP_F
        bottom = self.window.y + self.window.h * _STATUS_BOT_F
        right = self.window.x + self.window.w * _STATUS_RIGHT_F
        return " ".join(
            b.text for b in boxes if top < b.y < bottom and b.x < right
        ).strip()


def calibrate() -> Layout:
    """Confirm the client is on the Redeem screen and the hit targets line up."""
    window, boxes = read_screen()
    layout = Layout(window)
    submit = find_text(boxes, "SUBMIT CODE")
    if submit is None:
        raise MacAppError(
            "Not on the Redeem screen (no 'SUBMIT CODE' button). "
            "Open Shop > Redeem in the client."
        )
    want_x, want_y = layout.submit
    got_x, got_y = submit.center
    if abs(got_x - want_x) > 60 or abs(got_y - want_y) > 40:
        raise MacAppError(
            f"Layout drift: 'SUBMIT CODE' at ({got_x:.0f},{got_y:.0f}) but expected "
            f"~({want_x:.0f},{want_y:.0f}). Recalibrate before running."
        )
    return layout


def _read_status(layout: Layout) -> str:
    window = layout.window
    image = capture(window)
    if image is None:
        raise MacAppError("window capture failed mid-run (screen recording revoked?)")
    return layout.status_text(ocr(image, window))


def classify_status(text: str) -> Outcome:
    upper = text.upper()
    if STATUS_ALREADY_REDEEMED in upper:
        return Outcome.ALREADY_REDEEMED
    if STATUS_IN_LIST in upper:
        return Outcome.IN_LIST
    if STATUS_NOT_VALID in upper:
        return Outcome.INVALID
    if STATUS_SCANNED in upper:
        return Outcome.SUCCESS
    return Outcome.INDETERMINATE




def frontmost_app() -> str:
    from AppKit import NSWorkspace

    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return app.localizedName() if app is not None else ""


def ensure_frontmost(app_name: str = APP_NAME, *, timeout: float = 20.0) -> None:
    """Guarantee the client owns the screen before we synthesise any click.

    Clicks are posted to global screen coordinates, so if another window is in
    front they land on *it*. Every code re-checks rather than trusting the
    activation we did at startup.
    """
    if frontmost_app() == app_name:
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        activate(app_name)
        time.sleep(0.8)
        if frontmost_app() == app_name:
            return
    raise MacAppError(
        f"{app_name!r} would not come to the front (currently {frontmost_app()!r}); "
        "refusing to click blindly"
    )


def ensure_redeem_screen(layout: Layout, *, timeout: float = 60.0) -> None:
    """Return the client to the Redeem screen, dismissing any reward modal.

    A modal left up would swallow the next code's clicks, so every submit
    starts by making sure we are actually looking at the redeem form.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        ensure_frontmost()
        window, boxes = read_screen()
        if (window.x, window.y, window.w, window.h) != (
            layout.window.x, layout.window.y, layout.window.w, layout.window.h
        ):
            # Every hit target is a global screen point derived at calibrate
            # time; if the window moved they now point at empty desktop.
            raise MacAppError("client window moved or resized -- recalibrate")
        words = {w for b in boxes for w in re.findall(r"[A-Z]+", b.text.upper())}
        texts = " | ".join(b.text.upper() for b in boxes)
        if "SUBMIT CODE" in texts:
            return
        if "DONE" in words:
            click(*layout.done)
        elif "PACKS LEFT" in texts or "COLLECT ALL" in texts:
            click(*layout.collect_modal)
        else:
            # Not a reward modal -- a stray click navigated the client away
            # (e.g. into the Learning Lab). Walk back via Shop > Redeem.
            click(*layout.shop_tab)
            time.sleep(1.2)
            click(*layout.redeem_tab)
        time.sleep(1.5)
    raise MacAppError("could not get back to the Redeem screen")


def submit_code(layout: Layout, code: str, *, timeout: float = 15.0) -> tuple[Outcome, str]:
    """Type one code and wait for the status line to report on it.

    Clearing the field blanks the status line, so a non-empty reading after
    submit is always this code's result -- never a stale one from the previous
    code, even when two codes in a row fail the same way.
    """
    # A stray human click can leave a stale message or a half-open panel behind.
    # Re-settle the screen and retry rather than ending the run over it.
    for attempt in range(4):
        ensure_redeem_screen(layout)
        click(*layout.clear)
        time.sleep(0.35 + 0.35 * attempt)
        if not _read_status(layout):
            break
    else:
        raise MacAppError("status line would not clear after 4 attempts")

    click(*layout.field)
    time.sleep(0.2)
    paste_text(code)
    time.sleep(0.45)

    # Read the field back. Anything else touching the pasteboard between the
    # write and Cmd-V would submit the wrong text, and an unrecognised code is
    # recorded as terminally invalid -- so confirm before committing to it.
    window = layout.window
    image = capture(window)
    if image is None:
        raise MacAppError("window capture failed before submit")
    typed = layout.field_text(ocr(image, window)).replace(" ", "").upper()
    if typed != code.upper():
        raise MacAppError(f"field shows {typed!r}, expected the code -- not submitting")

    click(*layout.submit)

    deadline = time.time() + timeout
    while time.time() < deadline:
        text = _read_status(layout)
        if text:
            return classify_status(text), text
        time.sleep(0.35)
    return Outcome.INDETERMINATE, "no status after submit"


def collect_all(layout: Layout, *, timeout: float = 45.0) -> str:
    """Drain the rewards list: panel button -> modal -> DONE, back to Redeem.

    Treated as the step that finalises redemption, so callers should only mark
    codes durably redeemed once this returns cleanly.
    """
    deadline = time.time() + timeout
    ensure_frontmost()
    click(*layout.collect_panel)
    time.sleep(1.5)

    stage = "panel"
    retries = 0
    while time.time() < deadline:
        window, boxes = read_screen()
        words = {w for b in boxes for w in re.findall(r"[A-Z]+", b.text.upper())}
        texts = " | ".join(b.text.upper() for b in boxes)
        if "DONE" in words:
            click(*layout.done)
            stage = "done"
            time.sleep(2.0)
            continue
        if "SUBMIT CODE" in texts:
            if stage != "panel":
                return f"collected (last stage: {stage})"
            # Back on Redeem without ever seeing a reward modal: the panel click
            # missed. Reporting success here would let the caller mark codes
            # redeemed that were never collected, so try again instead.
            if retries >= 2:
                raise MacAppError("collect_all: rewards panel never opened")
            retries += 1
            ensure_frontmost()
            click(*layout.collect_panel)
            time.sleep(1.5)
            continue
        if "PACKS LEFT" in texts or "COLLECT ALL" in texts:
            click(*layout.collect_modal)
            stage = "modal"
            time.sleep(2.0)
            continue
        time.sleep(1.0)
    raise MacAppError(f"collect_all timed out at stage {stage!r}")
