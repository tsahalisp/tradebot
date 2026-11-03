"""Tests for tools/backtest_cost_sensitivity_check.py. Synthetic data in tmp_path, fixed seeds.

Benchmark/placebo: every strategy table carries the fixed-seed random placebo (same trade
timing), and test 3 asserts the placebo shows ~zero alpha at every cost level.
No real paper-portfolio return/turnover files exist in the repo (NAV lives in the DB), so
nothing here is run against live portfolios and nothing is xfailed.
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import backtest_cost_sensitivity_check as cs  # noqa: E402

N = 1000


def spy_series(n=N):
    return np.random.default_rng(1).normal(0.0004, 0.01, n)


def edge_strategy(edge, turnover_rate, n=N, seed=2):
    """Beta-0 strategy: constant daily edge + noise; turnover=rate on every day."""
    rng = np.random.default_rng(seed)
    r = edge + rng.normal(0, 0.003, n)
    return r, np.full(n, turnover_rate)


def strat(name, r, tn, spy=None, delayed=None):
    return cs._build(name, None if r is None else list(r), None if tn is None else list(tn),
                     list(spy_series() if spy is None else spy),
                     None if delayed is None else list(delayed))


def checks(res):
    return {c for c, _ in res["flags"]}


def test_low_turnover_real_edge_passes():
    r, tn = edge_strategy(0.001, 0.01)
    res = cs.analyse(strat("lowturn", r, tn))
    assert res["flags"] == []
    assert [row["bps"] for row in res["rows"]] == [0, 5, 10, 25]
    assert all(row["alpha"] > 0 for row in res["rows"])
    assert res["rows"][0]["ann"] > res["rows"][-1]["ann"]   # gross > net
    assert res["breakeven_bps"] > 25


def test_high_turnover_alpha_vanishes_at_10bps_flagged():
    r, tn = edge_strategy(0.0005, 1.0)           # ~12.6%/yr gross, 25%/yr at 10 bps
    res = cs.analyse(strat("churn", r, tn))
    assert "negative_alpha_at_10bps" in checks(res)
    assert res["rows"][0]["alpha"] > 0 > res["rows"][2]["alpha"]
    assert 0 < res["breakeven_bps"] < 10


def test_placebo_near_zero_net_alpha_at_every_cost():
    """Placebo has no edge: gross alpha ~0, and net alpha is gross minus the pure cost drag."""
    r, tn = edge_strategy(0.001, 0.05)
    res = cs.analyse(strat("lowturn", r, tn))
    pl = res["placebo"]
    assert abs(pl[0]["alpha"]) < 0.04, pl[0]
    for p in pl:
        drag = cs.DAYS * p["bps"] / 1e4 * 0.05
        assert p["alpha"] == pytest.approx(pl[0]["alpha"] - drag, abs=1e-9)
        assert abs(p["alpha"]) < 0.04 + drag
    # deterministic: fixed seed
    again = cs.analyse(strat("lowturn", r, tn))
    assert [p["alpha"] for p in again["placebo"]] == [p["alpha"] for p in pl]
    # the real edge beats the placebo at every cost level
    assert all(row["alpha"] > p["alpha"] for row, p in zip(res["rows"], pl))


@pytest.mark.parametrize("tn_kind", ["negative", "missing", "nan"])
def test_bad_turnover_flagged(tn_kind):
    r, tn = edge_strategy(0.001, 0.01)
    if tn_kind == "negative":
        tn[5] = -0.1
    elif tn_kind == "missing":
        tn = None
    else:
        tn[3] = float("nan")
    res = cs.analyse(strat("badturn", r, tn))
    assert "turnover" in checks(res)
    assert res["rows"] == []                    # not computed, but reported


def test_short_series_insufficient_data_not_computed():
    r, tn = edge_strategy(0.001, 0.01, n=19)
    res = cs.analyse(strat("short", r, tn, spy=spy_series(19)))
    assert checks(res) == {"insufficient_data"}
    assert res["rows"] == []
    ok = cs.analyse(strat("exact20", *edge_strategy(0.001, 0.01, n=20), spy=spy_series(20)))
    assert "insufficient_data" not in checks(ok)


def test_delay_sensitive_signal_flagged():
    r, tn = edge_strategy(0.001, 0.01)
    # delayed run keeps almost none of the edge
    delayed = r - 0.001 * 0.9
    res = cs.analyse(strat("fastsig", r, tn, delayed=delayed))
    assert "delay_sensitive" in checks(res)
    assert res["delay"]["loss"] > 0.5
    # a robust signal (delay keeps the edge) is not flagged
    ok = cs.analyse(strat("slowsig", r, tn, delayed=r * 0.95))
    assert "delay_sensitive" not in checks(ok)


def test_no_delayed_returns_is_visible_not_silent():
    r, tn = edge_strategy(0.001, 0.01)
    res = cs.analyse(strat("nodelay", r, tn))
    assert res["delay"] is None
    assert "n/a (no delayed_returns)" in cs.render([res])


def write_json(p, strategies, spy=None):
    p.write_text(json.dumps({"spy": list(spy_series() if spy is None else spy),
                             "strategies": strategies}))
    return p


def test_missing_file_fails_naming_check(tmp_path, capsys):
    rc = cs.main([str(tmp_path / "nope.json")])
    out = capsys.readouterr().out
    assert rc == 1 and "input_file" in out and "nope.json" in out


@pytest.mark.parametrize("body", ["{not json", "[]", '{"strategies": {}}', '{"strategies": {"a": 3}}'])
def test_malformed_json_fails(tmp_path, capsys, body):
    p = tmp_path / "bad.json"
    p.write_text(body)
    assert cs.main([str(p)]) == 1
    assert "input_file" in capsys.readouterr().out


def test_malformed_values_name_strategy(tmp_path, capsys):
    r, tn = edge_strategy(0.001, 0.01)
    p = write_json(tmp_path / "s.json", {
        "good": {"returns": list(r), "turnover": list(tn)},
        "garbage": {"returns": ["x", 1], "turnover": [0, 0]},
        "norets": {"turnover": list(tn)},
        "ragged": {"returns": list(r), "turnover": list(tn[:-1])},
    })
    rc = cs.main([str(p)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL garbage: malformed_returns" in out
    assert "FAIL norets: missing_returns" in out
    assert "FAIL ragged: length_mismatch" in out
    assert "FAIL good" not in out               # others still computed, none skipped


def test_csv_and_exit_code_zero_on_clean_input(tmp_path, capsys):
    r, tn = edge_strategy(0.001, 0.01)
    spy = spy_series()
    p = tmp_path / "lowturn.csv"
    p.write_text("returns,turnover,spy\n" + "\n".join(f"{a},{b},{c}" for a, b, c in zip(r, tn, spy)))
    assert cs.main([str(p)]) == 0
    assert "lowturn" in capsys.readouterr().out


def test_cli_subprocess_exit_nonzero_names_strategy_and_check(tmp_path):
    r, tn = edge_strategy(0.0005, 1.0)
    p = write_json(tmp_path / "s.json", {"churn": {"returns": list(r), "turnover": list(tn)}})
    cp = subprocess.run([sys.executable, str(ROOT / "tools" / "backtest_cost_sensitivity_check.py"), str(p)],
                        capture_output=True, text=True)
    assert cp.returncode == 1
    assert "FAIL churn: negative_alpha_at_10bps" in cp.stdout
