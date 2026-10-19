"""Tests for the daily-runner / config glue: config.Config, paper.step (daily bar stepper)
and autoportfolio.tick/mark (daily NAV mark + weekly rebalance, hit via POST /portfolio/tick).

Findings from reading the code: there is NO CLI entrypoint and NO portfolio config with
weights / portfolio names; config.Config only holds bankroll, fee_bps, products. So the
"invalid field" tests target those. Everything is synthetic: DB paths are redirected to
tmp_path, data fetches/scoring/sleeps are mocked, no network or broker is touched.
Tests marked xfail(strict=True) document REAL bugs; the code is deliberately not fixed.
Benchmark/placebo: a SPY-only buy_hold book run through paper.step must match SPY returns.
"""
import json
import pathlib
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import autoportfolio  # noqa: E402
import config as config_mod  # noqa: E402
import database  # noqa: E402
import paper  # noqa: E402


# ---------- fixtures ----------
def make_bars(closes, start=20260101):
    return [{"t": str(start + i), "c": float(c)} for i, c in enumerate(closes)]


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", tmp_path / "tradebot.db")
    return tmp_path


def marks(book_id=1):
    c = database.get_conn()
    rows = c.execute("SELECT date,price,equity FROM paper_marks WHERE book_id=? ORDER BY mark_id",
                     (book_id,)).fetchall()
    c.close()
    return rows


@pytest.fixture
def auto(tmp_path, monkeypatch):
    monkeypatch.setattr(autoportfolio, "DB", str(tmp_path / "portfolio.db"))
    monkeypatch.setattr(autoportfolio.time, "sleep", lambda *_: None)
    monkeypatch.setattr(autoportfolio.conviction, "congress_tally", lambda: {})
    monkeypatch.setattr(autoportfolio, "MEGACAP_CORE", ["AAA", "BBB", "SPY"])
    monkeypatch.setattr(autoportfolio, "score_one", lambda t: 70.0)
    monkeypatch.setattr(autoportfolio, "_price", lambda t: 100.0)
    return tmp_path


def nav_rows(path, book="core"):
    c = sqlite3.connect(path / "portfolio.db")
    r = c.execute("SELECT ts,nav,spy FROM nav_history WHERE book=?", (book,)).fetchall()
    c.close()
    return r


def trade_rows(path):
    c = sqlite3.connect(path / "portfolio.db")
    r = c.execute("SELECT book,ticker,side,shares FROM trades").fetchall()
    c.close()
    return r


# ---------- (1) config validation ----------
def load_config(tmp_path, monkeypatch, settings):
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".tradebot").mkdir(exist_ok=True)
    (tmp_path / ".tradebot" / "settings.json").write_text(json.dumps(settings))
    return config_mod.Config()


def test_config_defaults_when_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    c = config_mod.Config()
    assert c.bankroll == 10000 and c.fee_bps == 10 and "SPY" in c.products


def test_config_non_numeric_field_raises(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        load_config(tmp_path, monkeypatch, {"bankroll": "lots"})


@pytest.mark.xfail(strict=True, reason="BUG: Config does no validation; negative bankroll is accepted silently "
                   "instead of raising an error naming 'bankroll'.")
def test_config_negative_bankroll_rejected(tmp_path, monkeypatch):
    with pytest.raises(Exception, match="bankroll"):
        load_config(tmp_path, monkeypatch, {"bankroll": -5})


@pytest.mark.xfail(strict=True, reason="BUG: Config accepts negative fee_bps (would pay the trader on every trade) "
                   "with no error naming 'fee_bps'.")
def test_config_negative_fee_rejected(tmp_path, monkeypatch):
    with pytest.raises(Exception, match="fee_bps"):
        load_config(tmp_path, monkeypatch, {"fee_bps": -10})


@pytest.mark.xfail(strict=True, reason="BUG: Config accepts duplicate tickers in 'products' with no error naming 'products'.")
def test_config_duplicate_products_rejected(tmp_path, monkeypatch):
    with pytest.raises(Exception, match="products"):
        load_config(tmp_path, monkeypatch, {"products": ["SPY", "SPY"]})


@pytest.mark.xfail(strict=True, reason="BUG: a corrupt settings.json is swallowed (_load catches everything) and "
                   "defaults are used silently; no error is raised or logged.")
def test_config_corrupt_file_is_loud(tmp_path, monkeypatch):
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".tradebot").mkdir()
    (tmp_path / ".tradebot" / "settings.json").write_text("{not json")
    with pytest.raises(Exception, match="settings"):
        config_mod.Config()


def test_track_unknown_strategy_names_it(ledger):
    r = paper.track("SPY", "no_such_strategy")
    assert "no_such_strategy" in r["error"]


def test_track_duplicate_book_not_duplicated(ledger):
    paper.track("SPY", "buy_hold")
    assert paper.track("SPY", "buy_hold")["status"] == "already tracking"
    assert paper.portfolio()["count"] == 1


# ---------- (2) idempotency ----------
def test_paper_step_twice_same_date_no_duplicate_marks(ledger, monkeypatch):
    monkeypatch.setattr(paper, "get_bars", lambda *a, **k: make_bars([100 + i for i in range(10)]))
    paper.track("SPY", "buy_hold")
    paper.step()
    second = paper.step()
    assert len(marks()) == 1
    assert second["books"][0]["skipped"] == "already up to date"


def test_autoportfolio_tick_twice_no_duplicate_trades(auto):
    autoportfolio.tick()
    n = len(trade_rows(auto))
    assert n > 0
    autoportfolio.tick()
    assert len(trade_rows(auto)) == n


@pytest.mark.xfail(strict=True, reason="BUG: autoportfolio.mark()/tick() are not idempotent per date: every call "
                   "appends a nav_history row keyed on time.time(); a second tick the same day (cron retry, manual "
                   "POST /portfolio/tick) gives duplicate NAV rows for the same date.")
def test_autoportfolio_tick_twice_same_day_one_nav_row(auto):
    autoportfolio.tick()
    autoportfolio.tick()
    assert len(nav_rows(auto, "core")) == 1


# ---------- (3) failed / partial fetch ----------
def test_paper_step_fetch_exception_appends_no_mark(ledger, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(paper, "get_bars", boom)
    paper.track("SPY", "buy_hold")
    out = paper.step()
    assert marks() == []
    assert "network down" in out["books"][0]["error"]


@pytest.mark.xfail(strict=True, reason="BUG: paper.step swallows a failed fetch into a per-book {'error':..} entry and "
                   "returns normally; the top-level result (and POST /portfolio/* HTTP status) gives no failure "
                   "signal, so a cron caller cannot exit non-zero.")
def test_paper_step_fetch_failure_signals_failure(ledger, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(paper, "get_bars", boom)
    paper.track("SPY", "buy_hold")
    try:
        out = paper.step()
    except Exception as e:
        assert "network down" in str(e)
        return
    assert out.get("ok") is False or out.get("errors")


def test_paper_step_partial_fetch_appends_no_mark(ledger, monkeypatch):
    monkeypatch.setattr(paper, "get_bars", lambda *a, **k: make_bars([100, 101]))  # truncated
    paper.track("SPY", "buy_hold")
    paper.step()
    assert marks() == []


@pytest.mark.xfail(strict=True, reason="BUG: a truncated/partial fetch (<5 bars) makes paper.step `continue` with no "
                   "entry in the result at all: no message, no error, 'stepped' undercounts silently.")
def test_paper_step_partial_fetch_is_reported(ledger, monkeypatch):
    monkeypatch.setattr(paper, "get_bars", lambda *a, **k: make_bars([100, 101]))
    paper.track("SPY", "buy_hold")
    out = paper.step()
    assert out["books"] and ("error" in out["books"][0] or "skipped" in out["books"][0])


@pytest.mark.xfail(strict=True, reason="BUG: autoportfolio.mark() appends a NAV row even when pricing fails: "
                   "_price() returns None on any exception, so positions are valued at 0 (NAV collapses to cash) and "
                   "spy=NULL, corrupting the NAV record.")
def test_autoportfolio_mark_with_failed_pricing_appends_no_nav(auto, monkeypatch):
    autoportfolio.tick()                      # establish positions at price 100
    before = len(nav_rows(auto))
    monkeypatch.setattr(autoportfolio, "_price", lambda t: None)   # fetch now fails
    try:
        autoportfolio.mark("core")
    except Exception:
        pass
    assert len(nav_rows(auto)) == before


# ---------- (4) SPY-only placebo/benchmark through the same path ----------
def test_spy_only_buy_hold_matches_spy_returns(ledger, monkeypatch):
    closes = [400, 404, 399, 410, 415, 412, 420, 430, 425, 440]
    state = {"n": 5}
    monkeypatch.setattr(paper, "get_bars", lambda *a, **k: make_bars(closes[:state["n"]]))
    paper.track("SPY", "buy_hold", notional=10000.0)
    for n in range(5, len(closes) + 1):       # one "day" per step, new bar each time
        state["n"] = n
        paper.step()
    m = marks()
    assert len(m) == len(closes) - 4
    eq0, p0 = m[0][2], m[0][1]
    for _, price, equity in m:                # path-for-path identical to SPY (fee is a constant scale)
        assert equity / eq0 == pytest.approx(price / p0, rel=1e-9)
    spy_ret = closes[-1] / closes[4] - 1
    book_ret = m[-1][2] / m[0][2] - 1
    assert book_ret == pytest.approx(spy_ret, rel=1e-9)
    # vs buying at the first mark with only the one-side entry fee
    assert m[0][2] == pytest.approx(10000.0 * (1 - paper.FEE))
