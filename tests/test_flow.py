from ptcgl_redeem import flow
from ptcgl_redeem.classify import CodeStatus


class FakePage:
    def on(self, event, handler):
        pass


def _wire(monkeypatch, *, verify, commits: list[list[str]], clears: list[int]):
    monkeypatch.setattr(flow, "pace", lambda *a, **k: None)
    monkeypatch.setattr(flow, "clear_table", lambda page: clears.append(1))

    def fake_submit(page, capture, code):
        return verify(code)

    def fake_commit(page, capture, codes):
        commits.append(list(codes))
        return {code: (CodeStatus.SUCCESS, "redeemed") for code in codes}

    monkeypatch.setattr(flow, "submit_with_retry", fake_submit)
    monkeypatch.setattr(flow, "commit_redeem", fake_commit)


def test_all_valid_chunk_redeems(monkeypatch):
    commits, clears = [], []
    _wire(
        monkeypatch,
        verify=lambda code: (CodeStatus.VALID, "ok"),
        commits=commits,
        clears=clears,
    )
    codes = [f"TESTCODE{i:05d}" for i in range(12)]
    results = flow.redeem_codes(FakePage(), codes)
    assert commits == [codes[:10], codes[10:]]
    assert {status for _, status, _ in results} == {CodeStatus.SUCCESS}


def test_rejected_row_never_reaches_redeem_click(monkeypatch):
    commits, clears = [], []
    bad = "TESTCODE00003"
    _wire(
        monkeypatch,
        verify=lambda code: (CodeStatus.REJECTED, "already used")
        if code == bad
        else (CodeStatus.VALID, "ok"),
        commits=commits,
        clears=clears,
    )
    codes = [f"TESTCODE{i:05d}" for i in range(5)]
    results = flow.redeem_codes(FakePage(), codes)

    # The table was cleared and the valid codes re-verified before Redeem,
    # so no commit includes the rejected code and every valid code redeems.
    assert clears
    assert all(bad not in chunk for chunk in commits)
    outcomes = {code: status for code, status, _ in results}
    assert outcomes[bad] is CodeStatus.REJECTED
    assert all(outcomes[c] is CodeStatus.SUCCESS for c in codes if c != bad)


def test_dry_run_never_clicks_redeem(monkeypatch):
    commits, clears = [], []
    _wire(
        monkeypatch,
        verify=lambda code: (CodeStatus.VALID, "ok"),
        commits=commits,
        clears=clears,
    )
    codes = [f"TESTCODE{i:05d}" for i in range(3)]
    results = flow.redeem_codes(FakePage(), codes, dry_run=True)
    assert commits == []
    assert {status for _, status, _ in results} == {CodeStatus.VALID_NOT_REDEEMED}


def test_captcha_stops_batch_and_marks_rest_not_attempted(monkeypatch):
    commits, clears = [], []
    blocked = "TESTCODE00002"
    _wire(
        monkeypatch,
        verify=lambda code: (CodeStatus.CAPTCHA, "blocked")
        if code == blocked
        else (CodeStatus.VALID, "ok"),
        commits=commits,
        clears=clears,
    )
    codes = [f"TESTCODE{i:05d}" for i in range(5)]
    results = flow.redeem_codes(FakePage(), codes)
    outcomes = {code: status for code, status, _ in results}

    assert commits == []
    assert outcomes[blocked] is CodeStatus.CAPTCHA
    # Codes verified before the stop, and codes never reached, both stay
    # unredeemed so the next run retries them.
    for code in codes:
        if code != blocked:
            assert outcomes[code] is CodeStatus.NOT_ATTEMPTED
    assert set(outcomes) == set(codes)
