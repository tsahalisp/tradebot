"""Tests for tools/turnover_drawdown_check.py. All data is synthetic, written under tmp_path.

Benchmark/placebo: a SPY-only portfolio (nav == spy) must show SPY's drawdown and ~0 alpha.
"""
import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "backend"))
import turnover_drawdown_check as T  # noqa: E402

DAY = 86400
T0 = 1_700_006_400
N = 30


def spy_series():
    # rise to a peak, fall 20%, partial recovery: SPY max DD = -20%
    s = [100 + i for i in range(10)]            # peak 109
    s += [109 * (1 - 0.02 * k) for k in range(1, 11)]   # to 87.2 = -20%
    s += [87.2 + k for k in range(1, 11)]
    return s


def write(tmp, name, nav, spy, trades=(), report=None, report_extra=None):
    with (tmp / f"{name}_nav.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "nav", "spy"])
        for i, (n, s) in enumerate(zip(nav, spy)):
            w.writerow([T0 + i * DAY, n, s])
    with (tmp / f"{name}_trades.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "ticker", "side", "shares", "price", "fee"])
        for i, sh, px in trades:
            w.writerow([T0 + i * DAY, "AAA", "buy", sh, px, 0])
    return report


def write_report(tmp, entries):
    p = tmp / "report.json"
    p.write_text(json.dumps({"portfolios": entries}))
    return p


def good(tmp, name="core", trades=((0, 50, 100), (10, 20, 100))):
    spy = spy_series()
    nav = [10_000 * (1 + 0.3 * (v / 100 - 1)) for v in spy]  # damped SPY, ~10k NAV
    write(tmp, name, nav, spy, trades)
    mean_nav = sum(nav) / len(nav)
    return {"max_drawdown_pct": T.max_drawdown_pct(nav),
            "turnover_pct": sum(sh * px for _, sh, px in trades) / mean_nav * 100}


def checks(tmp, entries):
    rows, failures, errors = T.run(tmp, write_report(tmp, entries))
    return rows, failures, errors


def test_consistent_portfolio_passes_and_placebo_matches_spy(tmp_path, capsys):
    entry = good(tmp_path)
    spy = spy_series()
    write(tmp_path, "spyonly", spy, spy, trades=())
    rpt = write_report(tmp_path, {"core": entry,
                                  "spyonly": {"max_drawdown_pct": T.max_drawdown_pct(spy), "turnover_pct": 0.0}})
    assert T.main([str(tmp_path), str(rpt)]) == 0
    out = capsys.readouterr().out
    assert "core" in out and "spyonly" in out
    rows = {r["portfolio"]: r for r in T.run(tmp_path, rpt)[0]}
    assert rows["spyonly"]["dd"] == pytest.approx(rows["spyonly"]["spy_dd"])
    assert rows["spyonly"]["dd"] == pytest.approx(-20.0, abs=0.05)
    assert rows["spyonly"]["alpha"] == pytest.approx(0.0, abs=1e-9)
    assert rows["core"]["status"] == "ok"


def test_drawdown_mismatch(tmp_path, capsys):
    entry = good(tmp_path)
    entry["max_drawdown_pct"] += 3.0
    rpt = write_report(tmp_path, {"core": entry})
    assert T.main([str(tmp_path), str(rpt)]) == 1
    assert "FAIL core: drawdown_mismatch" in capsys.readouterr().err


def test_turnover_mismatch(tmp_path, capsys):
    entry = good(tmp_path)
    entry["turnover_pct"] *= 2
    rpt = write_report(tmp_path, {"core": entry})
    assert T.main([str(tmp_path), str(rpt)]) == 1
    err = capsys.readouterr().err
    assert "FAIL core: turnover_mismatch" in err and "drawdown_mismatch" not in err


@pytest.mark.parametrize("trades", [((3, 5000, 100),), ((3, -10, 100),)])
def test_implausible_turnover(tmp_path, capsys, trades):
    entry = good(tmp_path, trades=trades)  # report matches recompute, so only plausibility fails
    rpt = write_report(tmp_path, {"core": entry})
    assert T.main([str(tmp_path), str(rpt)]) == 1
    err = capsys.readouterr().err
    assert "FAIL core: turnover_implausible" in err and "mismatch" not in err


def test_base_date_inconsistent_shallower_than_spy(tmp_path, capsys):
    spy = spy_series()
    nav = [100 * (1 - 0.35 * max(0, min(1, (i - 9) / 10))) for i in range(N)]  # -35% trough
    write(tmp_path, "core", nav, spy)
    rpt = write_report(tmp_path, {"core": {"max_drawdown_pct": -5.0, "turnover_pct": 0.0}})
    assert T.main([str(tmp_path), str(rpt)]) == 1
    err = capsys.readouterr().err
    assert "core: base_date_inconsistent" in err and "core: drawdown_mismatch" in err


def test_short_series_is_insufficient_not_computed(tmp_path, capsys):
    spy = spy_series()[:19]
    write(tmp_path, "new", spy, spy)
    rpt = write_report(tmp_path, {"new": {"max_drawdown_pct": 0, "turnover_pct": 0}})
    assert T.main([str(tmp_path), str(rpt)]) == 1
    cap = capsys.readouterr()
    assert "insufficient data" in cap.out and "FAIL new: insufficient_data" in cap.err
    row = T.run(tmp_path, rpt)[0][0]
    assert row["dd"] is None and row["turn"] is None


@pytest.mark.parametrize("breakage", ["no_nav", "no_trades", "garbage_nav", "bad_column", "nan_price",
                                      "bad_ts", "bad_report", "no_report_entry", "orphan_file"])
def test_missing_or_malformed_never_silent(tmp_path, capsys, breakage):
    entry = good(tmp_path)
    entries = {"core": entry}
    rpt_text = None
    if breakage == "no_nav":
        (tmp_path / "core_nav.csv").unlink()
    elif breakage == "no_trades":
        (tmp_path / "core_trades.csv").unlink()
    elif breakage == "garbage_nav":
        (tmp_path / "core_nav.csv").write_text("ts,nav,spy\n1700006400,abc,100\n")
    elif breakage == "bad_column":
        (tmp_path / "core_nav.csv").write_text("when,value\n1,2\n")
    elif breakage == "nan_price":
        (tmp_path / "core_trades.csv").write_text("ts,ticker,side,shares,price,fee\n1700006400,A,buy,1,nan,0\n")
    elif breakage == "bad_ts":
        (tmp_path / "core_trades.csv").write_text("ts,ticker,side,shares,price,fee\nyesterday,A,buy,1,2,0\n")
    elif breakage == "bad_report":
        rpt_text = "{not json"
    elif breakage == "no_report_entry":
        entries = {}
    elif breakage == "orphan_file":
        write(tmp_path, "ghost", spy_series(), spy_series())
    rpt = write_report(tmp_path, entries)
    if rpt_text:
        rpt.write_text(rpt_text)
    assert T.main([str(tmp_path), str(rpt)]) == 1
    err = capsys.readouterr().err
    assert "bad_input" in err and "FAIL" in err


def test_missing_report_file_and_data_dir(tmp_path, capsys):
    assert T.main([str(tmp_path), str(tmp_path / "nope.json")]) == 1
    assert T.main([str(tmp_path / "nodir"), str(write_report(tmp_path, {"a": {}}))]) == 1


@pytest.mark.xfail(strict=True, reason=(
    "REAL GAP: the repo has no weekly report and no turnover calculation, although README says "
    "metrics.py covers 'turnover'. metrics.compute_metrics returns max_drawdown_pct but no turnover_pct, and "
    "NAV/trades live only in the autoportfolio sqlite DB (no CSV/JSON export). Proposed fix: add "
    "turnover_pct to compute_metrics and a read-only weekly_report exporting <book>_nav.csv, "
    "<book>_trades.csv and report.json in the format this tool expects."))
def test_real_metrics_report_turnover():
    import metrics
    eq = [100.0 + i for i in range(25)]
    m = metrics.compute_metrics(eq, [0.01] * 24, [1] * 25, [], "1d", 0.1)
    assert "max_drawdown_pct" in m and "turnover_pct" in m
