"""Tests for tools/cost_slippage_sensitivity_check.py. Synthetic data, fixed seeds, tmp_path only.
Benchmark/placebo: a SPY-only placebo (zero turnover, returns == benchmark) must show zero excess
at every cost level; the real-alpha strategy must beat it."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "backend"))
import cost_slippage_sensitivity_check as cs  # noqa: E402

N = 500


def spy(seed=1):
    return np.random.default_rng(seed).normal(0.0004, 0.01, N)


def write_json(path, **cols):
    path.write_text(json.dumps({k: list(map(float, v)) for k, v in cols.items()}))
    return path


def low_turn_alpha():
    b = spy()
    return b + 0.0005 + np.random.default_rng(2).normal(0, 0.002, N), np.full(N, 0.002), b


def high_turn_small_edge():
    b = spy()
    return b + 0.0002 + np.random.default_rng(3).normal(0, 0.002, N), np.full(N, 0.5), b


def test_alpha_survives_and_placebo_is_zero():
    g, t, b = low_turn_alpha()
    res = cs.analyze("alpha", g, t, b)
    assert res["flags"] == []
    assert all(r["excess"] > 0 for r in res["rows"])
    plc = cs.analyze("placebo", b, np.zeros(N), b)
    assert all(abs(r["excess"]) < 1e-12 for r in plc["rows"])
    assert len(plc["rows"]) == len(cs.DEFAULT_GRID)
    # alpha beats the placebo at every cost level
    assert all(a["excess"] > p["excess"] for a, p in zip(res["rows"], plc["rows"]))


def test_high_turnover_flagged_with_breakeven():
    g, t, b = high_turn_small_edge()
    res = cs.analyze("churn", g, t, b)
    exp = (g - b).mean() / t.mean() * 1e4
    assert res["breakeven_bps"] == pytest.approx(exp)
    assert 0 < exp < 10
    assert any(f.startswith("EDGE_VANISHES") for f in res["flags"])
    assert res["rows"][-1]["excess"] <= 0 and "VANISHED" in res["rows"][-1]["flags"]


def test_no_cost_modeled():
    g, t, b = high_turn_small_edge()
    res = cs.analyze("nocost", g, t, b, net=g)
    assert any(f.startswith("NO_COST_MODELED") for f in res["flags"])
    res = cs.analyze("zerocost", g, t, b, cost=np.zeros(N))
    assert any(f.startswith("NO_COST_MODELED") for f in res["flags"])
    ok = cs.analyze("costed", g, t, b, net=g - 0.001 * t, cost=0.001 * t)
    assert not any(f.startswith("NO_COST_MODELED") for f in ok["flags"])


@pytest.mark.parametrize("case", ["nan", "inf", "neg", "mismatch", "empty"])
def test_invalid_series(case):
    g, t, b = low_turn_alpha()
    if case == "nan":
        g = g.copy(); g[5] = np.nan
    elif case == "inf":
        b = b.copy(); b[5] = np.inf
    elif case == "neg":
        t = t.copy(); t[3] = -0.1
    elif case == "mismatch":
        t = t[:-1]
    else:
        g, t, b = g[:0], t[:0], b[:0]
    res = cs.analyze("bad", g, t, b)
    assert res["rows"] == []
    assert any(f.startswith("INVALID_INPUT") for f in res["flags"])


def test_short_series_insufficient_data(tmp_path, capsys):
    p = write_json(tmp_path / "short.json", gross=np.zeros(19), turnover=np.zeros(19), benchmark=np.zeros(19))
    assert cs.main([str(p)]) == 1
    cap = capsys.readouterr()
    assert "INSUFFICIENT_DATA" in cap.out and "short: INSUFFICIENT_DATA" in cap.err
    assert cs.analyze("short", np.zeros(19), np.zeros(19), np.zeros(19))["rows"] == []


def test_missing_and_malformed_files(tmp_path, capsys):
    bad_json = tmp_path / "bad.json"; bad_json.write_text("{not json")
    bad_csv = tmp_path / "badcsv.csv"; bad_csv.write_text("gross,turnover,benchmark\n0.1,abc,0.2\n")
    no_col = write_json(tmp_path / "nocol.json", gross=np.zeros(30), turnover=np.zeros(30))
    rc = cs.main([str(tmp_path / "missing.json"), str(bad_json), str(bad_csv), str(no_col)])
    err = capsys.readouterr().err
    assert rc == 1
    for name, code in [("missing", "MISSING_FILE"), ("bad", "MALFORMED_FILE"),
                       ("badcsv", "MALFORMED_FILE"), ("nocol", "MALFORMED_FILE")]:
        assert f"{name}: {code}" in err


def test_cli_end_to_end_table_and_exit_codes(tmp_path, capsys):
    g, t, b = low_turn_alpha()
    good = write_json(tmp_path / "alpha.json", gross=g, turnover=t, benchmark=b)
    assert cs.main([str(good), "--grid", "0,5,10"]) == 0
    out = capsys.readouterr().out
    assert "net_sharpe" in out and "excess_ann" in out
    g2, t2, b2 = high_turn_small_edge()
    churn = tmp_path / "churn.csv"
    churn.write_text("gross,turnover,benchmark\n" + "\n".join(f"{x},{y},{z}" for x, y, z in zip(g2, t2, b2)))
    assert cs.main([str(good), str(churn)]) == 1
    assert "churn: EDGE_VANISHES" in capsys.readouterr().err


def test_separate_benchmark_file(tmp_path):
    g, t, b = low_turn_alpha()
    s = write_json(tmp_path / "s.json", gross=g, turnover=t)
    bm = write_json(tmp_path / "spy.json", benchmark=b)
    assert cs.analyze_file(s, bm)["flags"] == []


def test_matches_repo_backtester_fee_model():
    """Consistency with backend/backtest.evaluate_pos: fee per position change == one-way turnover.
    (The repo has no separate slippage model; autoportfolio folds it into FEE_BPS, paper.py FEE.)"""
    bt = pytest.importorskip("backtest")
    rng = np.random.default_rng(7)
    closes = list(100 * np.cumprod(1 + rng.normal(0.0004, 0.01, 300)))
    pos = [int(x) for x in (rng.random(300) > 0.7)]
    rets = {}
    for fee in (0.0, 10.0):
        eq = bt.evaluate_pos(closes, pos, fee)[0]
        rets[fee] = eq
    px = np.asarray(closes)
    gross = np.array(pos[:-1]) * (px[1:] / px[:-1] - 1)
    turn = np.array([0.0] + [abs(pos[i] - pos[i - 1]) for i in range(1, len(pos) - 1)])
    net = gross - 10 / 1e4 * turn
    key = "total_return_pct"
    assert rets[10.0][key] < rets[0.0][key]
    assert (np.prod(1 + net) - 1) * 100 == pytest.approx(rets[10.0][key], abs=0.05)
