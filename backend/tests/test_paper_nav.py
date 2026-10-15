"""Tests for the live paper-portfolio accounting in autoportfolio.py (NAV, positions,
SPY comparison). Synthetic prices only; the DB is redirected to a tmp file and
_price is patched, so no network and no real book is touched. Tests that expose
real accounting bugs are xfail(strict=True) with the bug described."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import autoportfolio as ap  # noqa: E402

# Hand-made multi-day closes (day 0..3)
PX = {
    "AAA": [10.0, 11.0, 12.0, 9.0],
    "BBB": [50.0, 49.0, 51.0, 55.0],
    "SPY": [400.0, 404.0, 396.0, 408.0],
}
CASH0 = 1000.0
SHARES = {"AAA": 100.0, "BBB": 20.0}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "DB", str(tmp_path / "p.db"))
    state = {"day": 0, "missing": set()}

    def fake_price(t):
        if t in state["missing"] or t not in PX:
            return None
        return PX[t][state["day"]]

    monkeypatch.setattr(ap, "_price", fake_price)
    return state


def seed(book="core", cash=CASH0, shares=None, spy0=None):
    c = ap._db()
    c.execute("INSERT INTO books(name,cash,created,universe,last_rebal,spy0) VALUES(?,?,?,?,?,?)",
              (book, cash, 0.0, "t", None, spy0))
    for t, s in (shares if shares is not None else SHARES).items():
        c.execute("INSERT INTO positions(book,ticker,shares,avg_cost) VALUES(?,?,?,?)", (book, t, s, PX[t][0]))
    c.commit()
    return c


def hand_nav(day, cash=CASH0, shares=SHARES):
    return cash + sum(q * PX[t][day] for t, q in shares.items())


def test_nav_equals_cash_plus_qty_times_price_each_day(env):
    c = seed()
    for day in range(4):
        env["day"] = day
        nav, cash, invested = ap._nav(c, "core")
        assert cash == CASH0
        assert invested == pytest.approx(sum(q * PX[t][day] for t, q in SHARES.items()))
        assert nav == pytest.approx(hand_nav(day))
    # explicit hand numbers for day 3: 1000 + 100*9 + 20*55 = 3000
    assert nav == pytest.approx(3000.0)


def test_mark_records_nav_cash_invested_and_spy(env):
    seed()
    for day in range(4):
        env["day"] = day
        r = ap.mark("core")
        assert r["nav"] == pytest.approx(hand_nav(day))
        assert r["nav"] == pytest.approx(r["cash"] + r["invested"])
    rows = ap._db().execute("SELECT nav,cash,invested,spy FROM nav_history ORDER BY rowid").fetchall()
    assert [r[3] for r in rows] == PX["SPY"]
    assert [round(r[0], 6) for r in rows] == [round(hand_nav(d), 6) for d in range(4)]


def test_no_trade_day_positions_unchanged_nav_moves_only_with_prices(env):
    seed()
    env["day"] = 0
    ap.mark("core")
    before = ap._positions(ap._db(), "core")
    for day in (1, 2, 3):
        prev_nav = ap._nav(ap._db(), "core")[0]
        env["day"] = day
        ap.mark("core")
        assert ap._positions(ap._db(), "core") == before
        nav = ap._nav(ap._db(), "core")[0]
        dprice = sum(q * (PX[t][day] - PX[t][day - 1]) for t, q in SHARES.items())
        assert nav - prev_nav == pytest.approx(dprice)
    assert ap._db().execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    assert ap._db().execute("SELECT cash FROM books WHERE name='core'").fetchone()[0] == CASH0

    # flat prices -> NAV exactly unchanged
    flat = ap._nav(ap._db(), "core")[0]
    ap.mark("core")
    assert ap._nav(ap._db(), "core")[0] == flat


def test_cash_only_book_nav_never_moves(env):
    seed(shares={})
    for day in range(4):
        env["day"] = day
        assert ap.mark("core")["nav"] == CASH0


def test_spy_only_portfolio_matches_spy_exactly(env):
    # Placebo: 100% SPY bought at the base-date price with no fees.
    seed(cash=0.0, shares={"SPY": ap.START_CASH / PX["SPY"][0]}, spy0=PX["SPY"][0])
    for day in range(4):
        env["day"] = day
        ap.mark("core")
        s = ap.status("core")
        assert s["return_pct"] == pytest.approx(s["spy_return_pct"], abs=0.01)
        assert s["alpha_pct"] == pytest.approx(0.0, abs=0.01)
    # Also on the stored nav_history: nav/nav0 == spy/spy0 on every date
    hist = ap.status("core")["nav_history"]
    base_nav, base_spy = hist[0]["nav"], hist[0]["spy"]
    for h in hist:
        assert h["nav"] / base_nav == pytest.approx(h["spy"] / base_spy, rel=1e-6)


def test_spy_base_is_set_once_on_first_mark_and_not_moved(env):
    seed(spy0=None)
    env["day"] = 1
    ap.mark("core")
    env["day"] = 3
    ap.mark("core")
    spy0 = ap._db().execute("SELECT spy0 FROM books WHERE name='core'").fetchone()[0]
    assert spy0 == PX["SPY"][1]
    hist = ap.status("core")["nav_history"]
    assert [h["spy"] for h in hist] == [PX["SPY"][1], PX["SPY"][3]]


def test_status_spy_return_is_none_not_zero_when_spy_missing(env):
    seed(spy0=PX["SPY"][0])
    env["missing"].add("SPY")
    s = ap.status("core")
    assert s["spy_return_pct"] is None and s["alpha_pct"] is None


@pytest.mark.xfail(strict=True, reason=(
    "BUG: autoportfolio._nav() skips any held ticker whose price is None/0 (`if px:`), "
    "so a missing price silently values the position at $0 and NAV drops with no error "
    "or warning; mark() then writes that understated NAV into nav_history."))
def test_missing_price_for_held_ticker_is_not_silently_valued_at_zero(env):
    c = seed()
    env["day"] = 2
    env["missing"].add("AAA")
    try:
        nav, _, invested = ap._nav(c, "core")
    except Exception:
        return  # raising is acceptable
    # otherwise it must at least not report a NAV that silently excludes AAA
    assert nav == pytest.approx(hand_nav(2)) or nav is None


@pytest.mark.xfail(strict=True, reason=(
    "BUG: mark() persists a NAV row computed with a held ticker valued at $0 when its "
    "price is unavailable; there is no flag/raise, so the NAV-vs-SPY series gets a false drawdown."))
def test_mark_does_not_persist_nav_with_unpriced_position(env):
    seed()
    env["day"] = 2
    env["missing"].add("BBB")
    try:
        r = ap.mark("core")
    except Exception:
        return
    assert r["nav"] == pytest.approx(hand_nav(2)) or r.get("warning") or r.get("missing")


@pytest.mark.xfail(strict=True, reason=(
    "BUG: status() uses `_price(t) or 0` for holdings, so an unpriced position shows "
    "price 0 / value 0 / unreal_pct -100 and NAV is understated, with no warning field."))
def test_status_flags_unpriced_holding(env):
    seed()
    env["day"] = 2
    env["missing"].add("AAA")
    s = ap.status("core")
    aaa = next(p for p in s["positions"] if p["ticker"] == "AAA")
    assert aaa["price"] is None or s.get("warning") or s.get("missing_prices")


def test_nav_uses_explicit_prices_override(env):
    c = seed()
    nav, _, _ = ap._nav(c, "core", prices={"AAA": 1.0, "BBB": 2.0})
    assert nav == pytest.approx(CASH0 + 100 * 1.0 + 20 * 2.0)
