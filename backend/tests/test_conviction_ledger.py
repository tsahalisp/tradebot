"""Tests for the conviction engine's scorers and its pick ledger (conviction.py).

conviction.py logs picks to a SQLite ledger, settles them after N days and attributes
forward returns to the signals that scored high. That attribution is how the project
decides which signal to trust, so it must be correct and must not invent an edge.

All data is synthetic. HOME and conviction.DB are redirected into tmp_path and every
screener.* call is replaced by a fake, so there is no network and no write outside tmp_path.
Tests marked xfail(strict=True) document REAL bugs; the code is deliberately not fixed.
Placebo / benchmark: attribution on shuffled or random-score data must show ~zero edge,
a planted signal must beat the placebo signal, and settled returns are compared with a
buy-and-hold SPY pick over the same window.
"""
import json
import random
import sqlite3
import statistics
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import conviction  # noqa: E402
import screener  # noqa: E402

DAY = 86400


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(conviction, "DB", str(tmp_path / ".tradebot" / "conviction.db"))
    monkeypatch.setitem(conviction._CTALLY, "data", {})      # no congress fetch
    monkeypatch.setitem(conviction._CTALLY, "ts", time.time())
    return tmp_path


def fake_screener(monkeypatch, prices, research_exc=()):
    """prices: dict ticker -> current price. Fundamental feeds return neutral data."""
    def research(tk):
        if tk in research_exc:
            raise RuntimeError("boom")
        if tk not in prices:
            return {"error": "unknown ticker"}
        return {"price": prices[tk], "name": tk, "scorecard": {
            "growth": {"grade": "STRONG"}, "value": {"grade": "CHEAP"},
            "quality": {"grade": "STRONG"}, "trend": {"grade": "UPTREND"}}}
    monkeypatch.setattr(screener, "research", research)
    monkeypatch.setattr(screener, "dcf_inputs", lambda t: {})
    monkeypatch.setattr(screener, "earnings_lean", lambda t: {})
    monkeypatch.setattr(screener, "news", lambda t: {})
    monkeypatch.setattr(screener, "insider", lambda t: {})


def insert_pick(ticker, conviction_score, price_entry, age_days, breakdown=None, settled=0,
                fwd=None):
    c = conviction._db()
    c.execute("INSERT INTO picks(ts,ticker,conviction,price_entry,breakdown,settled,fwd_return_pct)"
              " VALUES(?,?,?,?,?,?,?)",
              (time.time() - age_days * DAY, ticker, conviction_score, price_entry,
               json.dumps(breakdown or {}), settled, fwd))
    c.commit()
    c.close()


# ---------------- scorers ----------------
def test_grade_num_ordering():
    assert conviction._grade_num("STRONG") > conviction._grade_num("average") > conviction._grade_num("WEAK")
    assert conviction._grade_num(None) == conviction._grade_num("")


def test_scorecard_score_handles_empty_and_bad_input():
    assert conviction._scorecard_score(None) == (None, None)
    assert conviction._scorecard_score({}) == (None, None)
    assert conviction._scorecard_score({"growth": "STRONG"}) == (None, None)  # not a dict
    s, _ = conviction._scorecard_score({"growth": {"grade": "STRONG"}, "value": {"grade": "WEAK"}})
    assert s == (85 + 25) / 2


def test_all_scores_bounded_0_100_and_neutral_when_no_info():
    for surp in (-1e6, 0, 1e6):
        s, _ = conviction._earn_score({"total": 4, "beats": 4, "avg_surprise_pct": surp})
        assert 0 <= s <= 100
    for b, se in ((0, 0), (50, 0), (0, 50), (3, 3)):
        s, _ = conviction._insider_score({"summary": {"buys": b, "sells": se}})
        assert 0 <= s <= 100
    assert conviction._insider_score({"summary": {"buys": 0, "sells": 0}})[0] == 50   # neutral
    assert conviction._congress_score("zzz", {})[0] == 50
    assert conviction._news_score({"overall": "mixed"}) == (None, None)
    assert conviction._earn_score({}) == (None, None)


def test_signal_direction_monotone():
    assert conviction._insider_score({"summary": {"buys": 5, "sells": 0}})[0] > \
        conviction._insider_score({"summary": {"buys": 0, "sells": 5}})[0]
    bull, bear = conviction._news_score({"overall": "BULLISH"})[0], conviction._news_score({"overall": "BEARISH"})[0]
    assert bull > 50 > bear
    tally = {"AAA": {"buys": 3, "sells": 0, "members": ["x"]}, "BBB": {"buys": 0, "sells": 3, "members": ["y"]}}
    assert conviction._congress_score("aaa", tally)[0] > conviction._congress_score("bbb", tally)[0]


def test_dcf_score_cheap_vs_expensive_and_benchmark_neutral():
    base = {"fcf": 1000.0, "shares": 100.0, "cash": 0, "debt": 0, "suggested_growth_pct": 10}
    cheap = conviction._dcf_score({**base, "price": 20.0})[0]
    rich = conviction._dcf_score({**base, "price": 400.0})[0]
    assert cheap > rich
    assert conviction._dcf_score({**base, "price": 20.0, "fcf": -5})[0] is None
    assert conviction._dcf_score({**base, "price": None})[0] is None
    # benchmark: price == model fair value => neutral 50
    fair = conviction._dcf_score({**base, "price": 1.0})[1]["fair_value"]
    assert conviction._dcf_score({**base, "price": fair})[0] == pytest.approx(50, abs=0.2)


@pytest.mark.xfail(strict=True, reason="BUG: `ti.get('suggested_growth_pct') or 10` turns a legitimate "
                   "0% growth input into 10%, overstating fair value for no-growth companies.")
def test_dcf_zero_growth_is_respected():
    base = {"fcf": 1000.0, "shares": 100.0, "price": 50.0, "cash": 0, "debt": 0}
    zero = conviction._dcf_score({**base, "suggested_growth_pct": 0})[1]["fair_value"]
    ten = conviction._dcf_score({**base, "suggested_growth_pct": 10})[1]["fair_value"]
    assert zero < ten


# ---------------- composite ----------------
def test_conviction_score_error_paths(monkeypatch):
    fake_screener(monkeypatch, {})
    assert "error" in conviction.conviction_score("nope")
    monkeypatch.setattr(screener, "research", lambda t: None)
    assert conviction.conviction_score("x")["error"] == "research failed"


def test_conviction_score_survives_failing_feeds_and_uses_only_available(monkeypatch):
    fake_screener(monkeypatch, {"AAA": 10.0})
    def bad(t): raise RuntimeError("feed down")
    monkeypatch.setattr(screener, "news", bad)
    monkeypatch.setattr(screener, "insider", bad)
    r = conviction.conviction_score("aaa")
    assert r["ticker"] == "AAA" and "news" not in r["signals_used"]
    assert r["breakdown"]["news"]["score"] is None
    assert 0 <= r["conviction"] <= 100 and r["label"] in ("High conviction", "Lean long", "Neutral", "Avoid")


def test_label_thresholds(monkeypatch):
    fake_screener(monkeypatch, {"AAA": 10.0})
    r = conviction.conviction_score("AAA")       # all-STRONG scorecard -> 85 plus neutral 50s
    assert r["conviction"] >= 55 and r["label"] in ("Lean long", "High conviction")
    monkeypatch.setattr(screener, "research", lambda t: {"price": 1.0, "scorecard": {
        "growth": {"grade": "WEAK"}, "value": {"grade": "EXPENSIVE"}}})
    assert conviction.conviction_score("AAA")["conviction"] < 55


# ---------------- ledger IO ----------------
def test_log_pick_roundtrip_and_isolation(monkeypatch, sandbox):
    fake_screener(monkeypatch, {"AAA": 10.0, "BBB": 20.0})
    a = conviction.log_pick("aaa")
    conviction.log_pick("bbb")
    assert a["logged_id"] == 1
    led = conviction.ledger()
    assert led["count"] == 2 and led["open"] == 2 and led["settled"] == 0
    assert {p["ticker"] for p in led["picks"]} == {"AAA", "BBB"}
    assert Path(conviction.DB).is_relative_to(sandbox)          # hermetic
    stored = sqlite3.connect(conviction.DB).execute("SELECT breakdown FROM picks WHERE id=1").fetchone()[0]
    assert "scorecard" in json.loads(stored)


def test_log_pick_does_not_write_failed_research(monkeypatch):
    fake_screener(monkeypatch, {})
    assert "error" in conviction.log_pick("nope")
    assert conviction.ledger()["count"] == 0


def test_ledger_limit_and_newest_first():
    for i in range(5):
        insert_pick(f"T{i}", 60, 10.0, age_days=10 - i)
    led = conviction.ledger(limit=3)
    assert led["count"] == 3
    assert [p["ticker"] for p in led["picks"]] == ["T4", "T3", "T2"]


# ---------------- settle (with SPY benchmark) ----------------
def test_settle_respects_min_days_and_computes_return_vs_spy_benchmark(monkeypatch):
    insert_pick("AAA", 80, 100.0, age_days=40)     # +20%
    insert_pick("SPY", 50, 400.0, age_days=40)     # benchmark: +5%
    insert_pick("NEW", 90, 10.0, age_days=5)       # too young, must stay open
    fake_screener(monkeypatch, {"AAA": 120.0, "SPY": 420.0, "NEW": 99.0})
    out = conviction.settle(min_days=30)
    assert out["settled_now"] == 2 and out["open"] == 1
    by = {p["ticker"]: p for p in out["picks"]}
    assert by["AAA"]["fwd_return_pct"] == pytest.approx(20.0)
    assert by["SPY"]["fwd_return_pct"] == pytest.approx(5.0)
    assert by["AAA"]["fwd_return_pct"] - by["SPY"]["fwd_return_pct"] == pytest.approx(15.0)
    assert not by["NEW"]["settled"]
    assert conviction.settle(min_days=30)["settled_now"] == 0   # idempotent


def test_settle_skips_missing_price_rather_than_settling_at_zero(monkeypatch):
    insert_pick("AAA", 80, 100.0, age_days=40)
    fake_screener(monkeypatch, {})                  # research -> error dict, no price
    out = conviction.settle()
    assert out["settled_now"] == 0 and out["open"] == 1


@pytest.mark.xfail(strict=True, reason="BUG: settle() only commits after the loop, so one screener.research "
                   "exception (network error) aborts it and rolls back every pick already settled in the run.")
def test_settle_keeps_progress_when_one_ticker_raises(monkeypatch):
    insert_pick("AAA", 80, 100.0, age_days=40)
    insert_pick("BBB", 80, 100.0, age_days=40)
    fake_screener(monkeypatch, {"AAA": 110.0, "BBB": 110.0}, research_exc=("BBB",))
    try:
        conviction.settle()
    except RuntimeError:
        pass
    assert conviction.ledger()["settled"] >= 1


@pytest.mark.xfail(strict=True, reason="BUG (silent failure): log_pick stores a pick with price_entry=None when "
                   "research returns no price; settle() skips it forever, so it sits 'open' with no warning.")
def test_log_pick_without_price_is_rejected_or_flagged(monkeypatch):
    fake_screener(monkeypatch, {"AAA": None})
    r = conviction.log_pick("AAA")
    assert r.get("error") or conviction.ledger()["count"] == 0


# ---------------- attribution with placebo ----------------
def seed_settled(n, rng, signal_drives_return):
    """n settled picks. 'good' signal score drives the return when planted; 'placebo' never does."""
    for _ in range(n):
        good, placebo = rng.uniform(0, 100), rng.uniform(0, 100)
        noise = rng.gauss(0, 3)
        ret = (0.2 * (good - 50) + noise) if signal_drives_return else noise
        bd = {"good": {"score": good}, "placebo": {"score": placebo}}
        insert_pick("X", (good + placebo) / 2, 100.0, 40, bd, settled=1, fwd=ret)


def test_attribution_not_ready_below_threshold():
    for _ in range(7):
        insert_pick("X", 60, 100.0, 40, {"a": {"score": 70}}, settled=1, fwd=1.0)
    r = conviction.attribution()
    assert r["ready"] is False and r["settled"] == 7
    insert_pick("X", 60, 100.0, 40, {"a": {"score": 70}}, settled=1, fwd=1.0)
    assert conviction.attribution()["ready"] is True


def test_attribution_ignores_unsettled_picks():
    for _ in range(10):
        insert_pick("X", 90, 100.0, 40, {"a": {"score": 90}}, settled=0)
    assert conviction.attribution() == {**conviction.attribution(), "settled": 0, "ready": False}


def test_attribution_placebo_returns_show_no_edge():
    rng = random.Random(7)
    seed_settled(600, rng, signal_drives_return=False)
    r = conviction.attribution()
    # returns are pure noise (sd 3): every signal's edge must be within sampling error
    for k, e in r["signal_edge_pct"].items():
        assert abs(e) < 1.0, (k, e)
    assert abs(r["high_conv_avg_return_pct"] - r["low_conv_avg_return_pct"]) < 1.0


def test_attribution_finds_planted_signal_and_ranks_above_placebo():
    rng = random.Random(7)
    seed_settled(600, rng, signal_drives_return=True)
    r = conviction.attribution()
    edge = r["signal_edge_pct"]
    assert list(edge)[0] == "good"                 # sorted best first
    assert edge["good"] > 5 > abs(edge["placebo"])
    assert r["high_conv_avg_return_pct"] > r["low_conv_avg_return_pct"]


def test_attribution_shuffled_returns_destroy_planted_edge():
    """Placebo: take the planted dataset and shuffle returns across picks."""
    rng = random.Random(11)
    seed_settled(600, rng, signal_drives_return=True)
    real = conviction.attribution()["signal_edge_pct"]["good"]
    c = conviction._db()
    rows = c.execute("SELECT id,fwd_return_pct FROM picks").fetchall()
    rets = [r for _, r in rows]
    rng.shuffle(rets)
    for (pid, _), r in zip(rows, rets):
        c.execute("UPDATE picks SET fwd_return_pct=? WHERE id=?", (r, pid))
    c.commit()
    shuffled = conviction.attribution()["signal_edge_pct"]["good"]
    assert real > 5 and abs(shuffled) < real / 4


def test_attribution_edge_is_hi_minus_lo_mean_and_skips_none_scores():
    for s, ret in ((80, 10.0), (90, 6.0), (20, 1.0), (30, 3.0)) * 2:
        insert_pick("X", 60, 100.0, 40, {"a": {"score": s}, "b": {"score": None}}, settled=1, fwd=ret)
    r = conviction.attribution()
    assert r["signal_edge_pct"] == {"a": round(statistics.mean([10, 6]) - statistics.mean([1, 3]), 2)}
    assert "b" not in r["signal_edge_pct"]
    assert r["high_conv_avg_return_pct"] == pytest.approx(5.0)   # all convictions 60 => all 'hi'
    assert r["low_conv_avg_return_pct"] is None


def test_attribution_constant_market_return_gives_zero_edge():
    """Benchmark: if every pick just earned the market (+8%), no signal can have edge."""
    rng = random.Random(3)
    for _ in range(40):
        insert_pick("X", rng.uniform(0, 100), 100.0, 40,
                    {"a": {"score": rng.uniform(0, 100)}}, settled=1, fwd=8.0)
    r = conviction.attribution()
    assert r["signal_edge_pct"]["a"] == 0.0
    assert r["high_conv_avg_return_pct"] == r["low_conv_avg_return_pct"] == 8.0
