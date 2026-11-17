"""Tests for tools/paper_turnover_drawdown_check.py. Synthetic data in tmp_path, fixed seeds.
Benchmark/placebo: a SPY-only portfolio must show ~0 turnover and 0 excess drawdown vs SPY."""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import paper_turnover_drawdown_check as T  # noqa: E402

N = 60


def dates(n=N):
    d0 = date(2026, 1, 5)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def spy_nav(n=N, seed=1):
    r = np.random.default_rng(seed).normal(0.0004, 0.01, n)
    return 100 * np.cumprod(1 + r)


def rebalancing_weights(n=N, seed=2, k=4):
    """Weekly rebalance: a mild reshuffle every 7th day, held in between."""
    rng = np.random.default_rng(seed)
    w = np.empty((n, k))
    cur = np.full(k, 1.0 / k)
    for i in range(n):
        if i % 7 == 0:
            cur = 0.8 * cur + 0.2 * rng.dirichlet(np.ones(k))
        w[i] = cur
    return w


def write(root, name, nav=None, spy=None, w=None, cols=None, d=None, wd=None, reported="auto",
          report=None):
    p = root / name
    p.mkdir(parents=True, exist_ok=True)
    spy = spy_nav() if spy is None else spy
    nav = (spy * 1.001 ** np.arange(len(spy))) if nav is None else nav
    d = dates(len(nav)) if d is None else d
    (p / "nav.csv").write_text("date,nav,spy\n" + "\n".join(f"{a},{b},{c}" for a, b, c in zip(d, nav, spy)) + "\n")
    if w is not None:
        cols = cols or [f"T{i}" for i in range(w.shape[1])]
        wd = d if wd is None else wd
        (p / "weights.csv").write_text("date," + ",".join(cols) + "\n" +
                                       "\n".join(a + "," + ",".join(repr(float(x)) for x in row) for a, row in zip(wd, w)) + "\n")
    rep = report if report is not None else {}
    if reported == "auto" and w is not None:
        rep[name] = {"max_drawdown": T.max_drawdown(nav), "turnover": T.annualised_turnover(w)}
    elif isinstance(reported, dict):
        rep[name] = reported
    (root / "report.json").write_text(json.dumps({"portfolios": rep}))
    return rep


def flags_of(res, name="p"):
    return [f for r in res if r["name"] == name for f in r["flags"]]


def has(res, check, name="p"):
    return any(f.startswith(check) for f in flags_of(res, name))


def test_clean_portfolio_passes_and_cli_exit_zero(tmp_path, capsys):
    write(tmp_path, "p", w=rebalancing_weights())
    res = T.run(tmp_path, ceiling=100.0)
    assert flags_of(res) == []
    assert res[0]["mdd"] <= 0 and res[0]["turnover"] > 0
    assert T.main([str(tmp_path), "--turnover-ceiling", "100"]) == 0
    out = capsys.readouterr().out
    assert "portfolio" in out and "dd_vs_spy" in out and "OK" in out


def test_spy_only_placebo_zero_turnover_and_zero_excess_drawdown(tmp_path):
    spy = spy_nav()
    w = np.tile([1.0, 0.0], (N, 1))
    write(tmp_path, "p", nav=spy, spy=spy, w=w, cols=["SPY", "cash"])
    res = T.run(tmp_path, stall_days=None)
    assert flags_of(res) == []
    assert res[0]["turnover"] == pytest.approx(0, abs=1e-12)
    assert res[0]["vs_spy"] == pytest.approx(0, abs=1e-12)
    # the stall check cannot tell a passive placebo from a dead rebalancer: it fires by default
    assert has(T.run(tmp_path), "turnover-stalled")


def test_independent_formulas_known_values():
    assert T.max_drawdown([100, 120, 90, 110]) == pytest.approx(-0.25)
    w = np.array([[1, 0], [0, 1], [0, 1]], float)
    assert T.annualised_turnover(w) == pytest.approx(0.5 * 252)  # one-way 1.0 on 1 of 2 days


@pytest.mark.parametrize("bad", [{"max_drawdown": -0.01}, {"turnover": 0.0}])
def test_report_mismatch(tmp_path, bad):
    w = rebalancing_weights()
    nav = spy_nav(seed=5)
    good = {"max_drawdown": T.max_drawdown(nav), "turnover": T.annualised_turnover(w)}
    write(tmp_path, "p", nav=nav, w=w, reported={**good, **bad})
    assert has(T.run(tmp_path, ceiling=100.0), "report-mismatch")


def test_portfolio_missing_from_report_is_flagged(tmp_path):
    write(tmp_path, "p", w=rebalancing_weights())
    write(tmp_path, "q", w=rebalancing_weights(), report=json.loads((tmp_path / "report.json").read_text())["portfolios"],
          reported=None)
    res = T.run(tmp_path, ceiling=100.0)
    assert has(res, "report-mismatch", "q") and flags_of(res, "p") == []


@pytest.mark.parametrize("mut,label", [
    (lambda w: w * 1.2, "sum"),
    (lambda w: np.where(np.arange(w.size).reshape(w.shape) == 7, np.nan, w), "nan"),
    (lambda w: np.where(np.arange(w.size).reshape(w.shape) == 7, np.inf, w), "inf"),
    (lambda w: w - np.where(np.arange(w.size).reshape(w.shape) == 7, 0.3, 0) + np.where(np.arange(w.size).reshape(w.shape) == 8, 0.3, 0), "neg"),
])
def test_bad_weights(tmp_path, mut, label):
    w = mut(rebalancing_weights())
    write(tmp_path, "p", w=w, reported=None)
    res = T.run(tmp_path, ceiling=100.0)
    assert has(res, "weights"), flags_of(res)
    assert T.main([str(tmp_path), "--turnover-ceiling", "100"]) == 1


def test_weights_with_cash_column_sum_ok(tmp_path):
    w = rebalancing_weights()
    w = np.hstack([w * 0.9, np.full((N, 1), 0.1)])
    write(tmp_path, "p", w=w, cols=["A", "B", "C", "D", "cash"])
    assert flags_of(T.run(tmp_path, ceiling=100.0)) == []


def test_turnover_above_ceiling(tmp_path):
    w = np.tile(np.eye(2), (N // 2, 1))  # flips 100% every day -> one-way 1.0/day
    write(tmp_path, "p", w=w)
    res = T.run(tmp_path, ceiling=50.0)
    assert has(res, "turnover-ceiling")
    assert not has(T.run(tmp_path, ceiling=500.0), "turnover-ceiling")


def test_stalled_rebalancing_20_days(tmp_path):
    w = rebalancing_weights()
    w[20:45] = w[20]  # 24 zero-turnover diffs
    write(tmp_path, "p", w=w)
    assert has(T.run(tmp_path, ceiling=100.0), "turnover-stalled")
    w2 = rebalancing_weights()
    w2[20:30] = w2[20]
    write(tmp_path, "p", w=w2)
    assert not has(T.run(tmp_path, ceiling=100.0), "turnover-stalled")


def test_date_misalignment(tmp_path):
    d = dates()
    wd = d[:10] + d[11:] + ["2027-01-01"]
    write(tmp_path, "p", w=rebalancing_weights(), wd=wd)
    res = T.run(tmp_path, ceiling=100.0)
    assert has(res, "date-alignment")
    assert T.main([str(tmp_path), "--turnover-ceiling", "100"]) == 1


def test_short_series_is_insufficient_not_computed(tmp_path, capsys):
    write(tmp_path, "p", nav=spy_nav(19), spy=spy_nav(19), w=rebalancing_weights(19))
    res = T.run(tmp_path, ceiling=100.0)
    assert has(res, "insufficient data")
    assert res[0]["mdd"] is None and res[0]["turnover"] is None
    assert T.main([str(tmp_path)]) == 1
    cap = capsys.readouterr()
    assert "FAIL p: insufficient data" in cap.err and "n/a" in cap.out


def test_missing_positions_file(tmp_path):
    write(tmp_path, "p", w=rebalancing_weights())
    (tmp_path / "p" / "weights.csv").unlink()
    res = T.run(tmp_path, ceiling=100.0)
    assert has(res, "missing-file")


def test_missing_portfolio_dir_and_missing_report(tmp_path, capsys):
    write(tmp_path, "p", w=rebalancing_weights())
    res = T.run(tmp_path, portfolios=["p", "ghost"], ceiling=100.0)
    assert has(res, "missing-portfolio", "ghost") and flags_of(res, "p") == []
    (tmp_path / "report.json").unlink()
    assert any(r["name"] == "<report>" and r["flags"] for r in T.run(tmp_path))
    assert T.main([str(tmp_path / "nowhere")]) == 1


@pytest.mark.parametrize("fname,content", [
    ("weights.csv", "date,A,B\n2026-01-05,0.5,abc\n"),
    ("weights.csv", "date,A,B\n2026-01-05,0.5\n"),
    ("weights.csv", ""),
    ("nav.csv", "date,nav\n2026-01-05,100\n"),
    ("nav.csv", "date,nav,spy\n2026-01-05,-1,100\n"),
])
def test_malformed_files(tmp_path, fname, content):
    write(tmp_path, "p", w=rebalancing_weights())
    (tmp_path / "p" / fname).write_text(content)
    res = T.run(tmp_path, ceiling=100.0)
    assert flags_of(res), "malformed input must never pass silently"
    assert T.main([str(tmp_path), "--turnover-ceiling", "100"]) == 1


def test_malformed_report_json(tmp_path):
    write(tmp_path, "p", w=rebalancing_weights())
    (tmp_path / "report.json").write_text("{not json")
    assert any(r["name"] == "<report>" for r in T.run(tmp_path, portfolios=["p"]))
    assert T.main([str(tmp_path), "--portfolios", "p"]) == 1
