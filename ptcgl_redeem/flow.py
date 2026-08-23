from __future__ import annotations

import random
import time
from collections.abc import Callable

from .classify import STOPPING, CodeStatus, classify_redeem, classify_verify
from .config import CHUNK_SIZE, LOGIN_URL, PACE_MAX, PACE_MIN

VERIFY_PATH = "/commerce/v1/external/webccr/verify"
REDEEM_PATH = "/commerce/v1/external/webccr/redeem"


class FlowError(RuntimeError):
    pass


def pace(lo: float = PACE_MIN, hi: float = PACE_MAX) -> None:
    time.sleep(random.uniform(lo, hi))


def _settle(page, timeout: int = 20000) -> None:
    for state in ("domcontentloaded", "networkidle"):
        try:
            page.wait_for_load_state(state, timeout=timeout)
        except Exception:
            pass
    page.wait_for_timeout(800)


def dismiss_cookies(page) -> None:
    for selector in ("#onetrust-reject-all-handler", ".onetrust-close-btn-handler"):
        try:
            loc = page.locator(selector).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=4000)
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


def redemption_ready(page) -> bool:
    for selector in ("input#code", "[data-testid='code-redemption-view']"):
        try:
            if page.locator(selector).first.count():
                return True
        except Exception:
            continue
    return False


def wait_until_logged_in(page, timeout_s: int = 300) -> None:
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    _settle(page)
    dismiss_cookies(page)
    if redemption_ready(page):
        return

    deadline = time.time() + timeout_s
    off_site_since: float | None = None
    while time.time() < deadline:
        if redemption_ready(page):
            dismiss_cookies(page)
            return
        page.wait_for_timeout(1000)
        try:
            url = page.url
            if "redeem.tcg.pokemon.com" in url or "access.pokemon.com" in url:
                # On the redeem site or mid-OAuth — never navigate away, or we
                # destroy the login form the user is typing into.
                off_site_since = None
            elif off_site_since is None:
                off_site_since = time.time()
            elif time.time() - off_site_since > 15:
                # Stranded off both domains (hung redirect) — nudge back.
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
                _settle(page)
                dismiss_cookies(page)
                off_site_since = None
        except Exception:
            pass
    raise FlowError("timed out waiting for login — complete sign-in in the browser window")


class ResponseCapture:
    def __init__(self, page) -> None:
        self.last_verify: dict | None = None
        self.last_redeem: dict | None = None
        page.on("response", self._on_response)

    def _on_response(self, response) -> None:
        url = response.url
        try:
            if VERIFY_PATH in url:
                self.last_verify = response.json()
            elif REDEEM_PATH in url:
                self.last_redeem = response.json()
        except Exception:
            pass


def _wait_json(getter: Callable[[], dict | None], page, timeout_s: float = 20) -> dict | None:
    deadline = time.time() + timeout_s
    while getter() is None and time.time() < deadline:
        page.wait_for_timeout(200)
    return getter()


def submit_one(page, capture: ResponseCapture, code: str) -> tuple[CodeStatus, str]:
    capture.last_verify = None
    try:
        box = page.locator("input#code").first
        if not box.count():
            return CodeStatus.FATAL, "code input not found"
        box.fill("")
        box.fill(code)
        button = page.locator("[data-testid='verify-code-button']").first
        if button.count():
            button.click()
        else:
            box.press("Enter")
    except Exception as exc:
        return CodeStatus.TRANSIENT, f"submit failed: {type(exc).__name__}"

    payload = _wait_json(lambda: capture.last_verify, page)
    if payload is None:
        return CodeStatus.TRANSIENT, "no verify response"
    return classify_verify(payload, code)


def submit_with_retry(page, capture: ResponseCapture, code: str) -> tuple[CodeStatus, str]:
    status, detail = submit_one(page, capture, code)
    if status == CodeStatus.TRANSIENT:
        pace()
        status, detail = submit_one(page, capture, code)
        if status == CodeStatus.TRANSIENT:
            return CodeStatus.FATAL, f"transient error recurred: {detail}"
    return status, detail


def commit_redeem(
    page, capture: ResponseCapture, codes: list[str]
) -> dict[str, tuple[CodeStatus, str]]:
    capture.last_redeem = None
    button = page.locator("[data-testid='button-redeem']").first
    try:
        button.wait_for(state="visible", timeout=10000)
        deadline = time.time() + 10
        while not button.is_enabled() and time.time() < deadline:
            page.wait_for_timeout(200)
        if not button.is_enabled():
            return {code: (CodeStatus.INDETERMINATE, "Redeem never enabled") for code in codes}
        button.click()
    except Exception as exc:
        failed = f"redeem click failed: {type(exc).__name__}"
        return {code: (CodeStatus.INDETERMINATE, failed) for code in codes}

    payload = _wait_json(lambda: capture.last_redeem, page)
    if payload is None:
        return {code: (CodeStatus.INDETERMINATE, "no redeem response") for code in codes}
    return {code: classify_redeem(payload, code) for code in codes}


def clear_table(page) -> None:
    try:
        button = page.locator("[data-testid='button-clear-table']").first
        if button.count() and button.is_visible():
            button.click(timeout=4000)
            page.wait_for_timeout(800)
    except Exception:
        pass


def redeem_codes(
    page,
    codes: list[str],
    *,
    dry_run: bool = False,
    on_result: Callable[[str, CodeStatus, str], None] | None = None,
    pace_min: float = PACE_MIN,
    pace_max: float = PACE_MAX,
) -> list[tuple[str, CodeStatus, str]]:
    capture = ResponseCapture(page)
    resolved: list[tuple[str, CodeStatus, str]] = []
    remaining = list(codes)
    stop = False

    def record(code: str, status: CodeStatus, detail: str) -> None:
        resolved.append((code, status, detail))
        if on_result is not None:
            on_result(code, status, detail)

    while remaining and not stop:
        chunk, remaining = remaining[:CHUNK_SIZE], remaining[CHUNK_SIZE:]
        pending: list[str] = []
        rejected_in_chunk = False

        for code in chunk:
            status, detail = submit_with_retry(page, capture, code)
            if status == CodeStatus.VALID:
                if dry_run:
                    record(code, CodeStatus.VALID_NOT_REDEEMED, "valid (dry-run, not redeemed)")
                else:
                    pending.append(code)
            else:
                record(code, status, detail)
                rejected_in_chunk = rejected_in_chunk or status == CodeStatus.REJECTED
                if status in STOPPING:
                    stop = True
                    break
            pace(pace_min, pace_max)

        if dry_run:
            if remaining and not stop:
                clear_table(page)
                pace(pace_min, pace_max)
            continue

        if not stop and pending and rejected_in_chunk:
            # Rejected rows occupy table slots and can block the Redeem button.
            # Clear the table and requeue the verified codes so the next chunk
            # rebuilds it clean; verify does not consume a code.
            clear_table(page)
            remaining = pending + remaining
            pace(pace_min, pace_max)
            continue

        if not stop and pending:
            outcomes = commit_redeem(page, capture, pending)
            for code in pending:
                record(code, *outcomes[code])
            if any(status in STOPPING for status, _ in outcomes.values()):
                stop = True
        elif stop and pending:
            for code in pending:
                record(
                    code,
                    CodeStatus.NOT_ATTEMPTED,
                    "verified but batch stopped before Redeem",
                )

        if not stop and remaining:
            clear_table(page)
            pace(pace_min, pace_max)

    attempted = {code for code, _, _ in resolved}
    for code in codes:
        if code not in attempted:
            record(code, CodeStatus.NOT_ATTEMPTED, "batch stopped before this code")
    return resolved
