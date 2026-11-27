"""Failure-mode tests for the weekly paper-portfolio report (NAV vs SPY, drawdown, turnover).

There is NO dedicated weekly report generator in this repo. The closest real code is
autoportfolio.status() (NAV vs SPY, alpha, nav_history) and metrics.compute_metrics()
(max drawdown). Those are what is tested. There is no turnover code at all.
All data is synthetic (fixed seed, tmp_path sqlite); _price is faked, no network.
Tests marked xfail(strict=True) document REAL gaps; the code is deliberately not fixed
(see docs/weekly_report_gaps_2026-11-27.md). Benchmark: SPY-only placebo => zero alpha.
"""
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autoportfolio  # noqa: E402
import metrics  # noqa: E402

DAY = 86400
NOW = 1_800_000_000.0


def spy_path(n=40, seed=7):
    rng = np.random.default_rng(seed)
    return 400.0 * np.cumprod(1 + rng.normal(0.0004, 0.01, n))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(autoportfolio, "DB", str(tmp_path / "portfolio.db"))
    prices = {}
    monkeypatch.setattr(autoportfolio, "_price", lambda t: prices.get(t))
    monkeypatch.setattr(autoportfolio.time, "time", lambda: NOW)
    return prices


def seed_book(book="core", cash=autoportfolio.START_CASH, spy0=400.0, positions=(), hist=()):
    autoportfolio.ensure_books()
    c = autoportfolio._db()
    c.execute("UPDATE books SET cash=?, spy0=? WHERE name=?", (cash, spy0, book))
    for t, sh, cost in positions:
        c.execute("INSERT INTO positions VALUES(?,?,?,?)", (book, t, sh, cost))
    for ts, nav, spy in hist:
        c.execute("INSERT INTO nav_history(book,ts,nav,cash,invested,spy) VALUES(?,?,?,?,?,?)",
                  (book, ts, nav, 0.0, nav, spy))
    c.commit()
    c.close()


def hist_rows(navs, spys, end=NOW):
    n = len(navs)
    return [(end - (n - 1 - i) * DAY, float(navs[i]), None if spys[i] is None else float(spys[i]))
            for i in range(n)]


def loud(fn, *names):
    """Passes only if fn raises/exits non-zero with every name in the message."""
    with pytest.raises((Exception, SystemExit)) as ei:
        fn()
    msg = str(ei.value).lower()
    for nm in names:
        assert nm.lower() in msg, f"error does not name {nm!r}: {msg!r}"


# ---- (1) clean input vs independent numpy recomputation ---------------------------------

def test_clean_nav_vs_spy_matches_numpy(env):
    spy = spy_path()
    spy0, spy_now = spy[0], spy[-1]
    shares = 150.0
    cash = 40_000.0
    env.update({"AAA": 410.0, "SPY": float(spy_now)})
    seed_book(cash=cash, spy0=float(spy0), positions=[("AAA", shares, 400.0)])
    s = autoportfolio.status("core")
    nav = cash + shares * 410.0
    ret = (nav / autoportfolio.START_CASH - 1) * 100
    bench = (spy_now / spy0 - 1) * 100
    assert s["nav"] == pytest.approx(nav, abs=0.01)
    assert s["return_pct"] == pytest.approx(ret, abs=0.01)
    assert s["spy_return_pct"] == pytest.approx(bench, abs=0.01)
    assert s["alpha_pct"] == pytest.approx(ret - bench, abs=0.02)


def test_spy_only_placebo_has_zero_excess_return(env):
    spy = spy_path()
    spy0, spy_now = float(spy[0]), float(spy[-1])
    sh = autoportfolio.START_CASH / spy0
    env.update({"SPY": spy_now})
    seed_book(cash=0.0, spy0=spy0, positions=[("SPY", sh, spy0)])
    s = autoportfolio.status("core")
    assert s["alpha_pct"] == pytest.approx(0.0, abs=0.01)


def test_max_drawdown_matches_numpy():
    eq = (100_000 * np.cumprod(1 + np.random.default_rng(11).normal(0, 0.01, 60))).tolist()
    m = metrics.compute_metrics(eq, [0.0] * 59, [1] * 60, [], "1d", 0.0)
    a = np.array(eq)
    expect = (a / np.maximum.accumulate(a) - 1).min() * 100
    assert m["max_drawdown_pct"] == pytest.approx(expect, abs=0.01)


@pytest.mark.xfail(strict=True, reason="No turnover computation exists anywhere in the repo "
                   "(README claims metrics.py has turnover; it does not).")
def test_turnover_matches_numpy():
    assert hasattr(metrics, "turnover") or hasattr(autoportfolio, "turnover")


# ---- (2) missing / stale inputs must fail loudly ----------------------------------------

@pytest.mark.xfail(strict=True, reason="status() on a missing DB silently creates an empty DB "
                   "and returns {'error': ...} instead of raising.")
def test_missing_nav_file_fails_loudly(env, tmp_path):
    db = Path(autoportfolio.DB)
    assert not db.exists()
    loud(lambda: autoportfolio.status("core"), "core", "portfolio.db")


@pytest.mark.xfail(strict=True, reason="SPY price failure is swallowed (_price -> None); status() "
                   "returns spy_return_pct=None and alpha=None with no error.")
def test_missing_spy_fails_loudly(env):
    env["AAA"] = 410.0  # SPY absent
    seed_book(positions=[("AAA", 100.0, 400.0)])
    loud(lambda: autoportfolio.status("core"), "core", "spy")


@pytest.mark.xfail(strict=True, reason="status() never checks last nav_history date; a 30-day "
                   "stale series is reported as current.")
def test_stale_last_date_fails_loudly(env):
    spy = spy_path()
    env["SPY"] = float(spy[-1])
    seed_book(spy0=float(spy[0]), hist=hist_rows([1e5] * 25, spy[:25], end=NOW - 30 * DAY))
    loud(lambda: autoportfolio.status("core"), "core", "stale")


# ---- (3) NaN / <20 observations => 'insufficient data', not computed --------------------

def _flagged(m):
    return "insufficient data" in str(m).lower()


@pytest.mark.xfail(strict=True, reason="compute_metrics computes Sharpe/drawdown on any length "
                   "(here 10 points) with no insufficient-data flag.")
def test_fewer_than_20_obs_flagged_not_computed():
    eq = [100_000 * (1 + 0.001 * i) for i in range(10)]
    try:
        m = metrics.compute_metrics(eq, [0.001] * 9, [1] * 10, [], "1d", 0.0)
    except Exception as e:
        m = e
    assert _flagged(m)


@pytest.mark.xfail(strict=True, reason="compute_metrics propagates NaN into results with no "
                   "insufficient-data flag.")
def test_nan_values_flagged_not_computed():
    eq = (100_000 * np.cumprod(1 + np.random.default_rng(3).normal(0, 0.01, 40))).tolist()
    eq[20] = float("nan")
    rets = [0.0] * 39
    rets[19] = float("nan")
    try:
        m = metrics.compute_metrics(eq, rets, [1] * 40, [], "1d", 0.0)
    except Exception as e:
        m = e
    assert _flagged(m)


# ---- (4) date misalignment between portfolio and SPY is reported ------------------------

@pytest.mark.xfail(strict=True, reason="nav_history rows with missing SPY marks (portfolio dates "
                   "not matched by SPY dates) are passed through; nothing reports the misalignment.")
def test_date_misalignment_is_reported(env):
    spy = spy_path()
    env["SPY"] = float(spy[-1])
    spys = list(spy[:30])
    for i in range(10, 18):
        spys[i] = None  # SPY has no observation on those portfolio dates
    seed_book(spy0=float(spy[0]), hist=hist_rows([1e5] * 30, spys))
    s = autoportfolio.status("core")
    assert "misalign" in str(s).lower() or "missing spy" in str(s).lower()
