"""Tests for tools/report_completeness_check.py. Synthetic data in tmp_path only.
Benchmark/placebo: a SPY-only portfolio must show ~zero alpha vs SPY.
"""
import csv
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import report_completeness_check as rc  # noqa: E402

TODAY = dt.date(2026, 10, 30)      # Friday
AS_OF = dt.date(2026, 10, 29)
START = dt.date(2026, 10, 22)


def spy_series():
    d, px, out = dt.date(2026, 10, 1), 500.0, {}
    while d <= TODAY:
        if d.weekday() < 5:
            out[d] = round(px, 4)
            px *= 1.004
        d += dt.timedelta(days=1)
    return out


SPY = spy_series()
SPY_RET = (SPY[AS_OF] / SPY[START] - 1) * 100


def good(**kw):
    r = {"nav": 101234.5, "spy_return_pct": SPY_RET, "drawdown_pct": -1.2, "turnover_pct": 8.0, "return_pct": SPY_RET + 0.5}
    r.update(kw)
    return r


def setup(tmp_path, portfolios=None, as_of=AS_OF, names=("core", "congress", "spy_only")):
    portfolios = portfolios if portfolios is not None else {
        "core": good(), "congress": good(return_pct=SPY_RET - 1),
        "spy_only": good(return_pct=SPY_RET)}
    rep = tmp_path / "report.json"
    rep.write_text(json.dumps({"as_of": str(as_of), "period_start": str(START), "portfolios": portfolios}))
    snap = tmp_path / "params_snapshot.json"
    snap.write_text(json.dumps({"portfolios": {n: {} for n in names}}))
    px = tmp_path / "spy.csv"
    with open(px, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["date", "close"])
        for d, c in SPY.items():
            w.writerow([d.isoformat(), c])
    return rep, snap, px


def run(tmp_path, rep, snap, px, capsys, extra=()):
    code = rc.main([str(rep), "--snapshot", str(snap), "--spy-prices", str(px), "--today", str(TODAY), *extra])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_complete_report_passes_and_placebo_alpha_zero(tmp_path, capsys):
    code, out, _ = run(tmp_path, *setup(tmp_path), capsys)
    assert code == 0 and "RESULT: PASS" in out
    line = next(l for l in out.splitlines() if l.startswith("spy_only"))
    assert abs(float(line.split()[5])) < 0.01   # placebo: SPY-only has ~0 alpha
    core = next(l for l in out.splitlines() if l.startswith("core"))
    assert float(core.split()[5]) == pytest.approx(0.5, abs=0.01)


def test_missing_portfolio_named(tmp_path, capsys):
    ps = {"core": good(), "spy_only": good()}
    code, out, _ = run(tmp_path, *setup(tmp_path, ps), capsys)
    assert code == 1 and "portfolio=congress" in out and "missing from report" in out


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "", "  ", "abc"])
def test_bad_field_values(tmp_path, capsys, bad):
    ps = {"core": good(drawdown_pct=bad), "congress": good(), "spy_only": good()}
    code, out, _ = run(tmp_path, *setup(tmp_path, ps), capsys)
    assert code == 1 and "portfolio=core field=drawdown_pct" in out


def test_missing_field_named(tmp_path, capsys):
    c = good(); del c["turnover_pct"]
    code, out, _ = run(tmp_path, *setup(tmp_path, {"core": c, "congress": good(), "spy_only": good()}), capsys)
    assert code == 1 and "portfolio=core field=turnover_pct: field missing" in out


def test_stale_as_of(tmp_path, capsys):
    stale = dt.date(2026, 10, 23)   # 5 business days before TODAY
    code, out, _ = run(tmp_path, *setup(tmp_path, as_of=stale), capsys)
    assert code == 1 and "field=as_of" in out


def test_as_of_boundary_three_bdays_ok(tmp_path, capsys):
    code, out, _ = run(tmp_path, *setup(tmp_path, as_of=dt.date(2026, 10, 27)), capsys)
    assert "field=as_of" not in out   # exactly 3 bdays; other fails (spy mismatch) don't matter


def test_spy_return_mismatch(tmp_path, capsys):
    ps = {"core": good(spy_return_pct=SPY_RET + 2), "congress": good(), "spy_only": good()}
    code, out, _ = run(tmp_path, *setup(tmp_path, ps), capsys)
    assert code == 1 and "portfolio=core field=spy_return_pct" in out
    assert "portfolio=congress" not in out


def test_spy_series_not_covering_period_fails(tmp_path, capsys):
    rep, snap, px = setup(tmp_path)
    px.write_text("date,close\n2026-10-01,500\n2026-10-02,501\n")
    code, out, _ = run(tmp_path, rep, snap, px, capsys)
    assert code == 1 and "spy_series" in out


def test_missing_report_file(tmp_path, capsys):
    _, snap, px = setup(tmp_path)
    code, _, err = run(tmp_path, tmp_path / "nope.json", snap, px, capsys)
    assert code == 2 and "ERROR" in err


@pytest.mark.parametrize("content", ["", "{not json", "[1,2]", '{"as_of":"2026-10-29"}',
                                     '{"as_of":"garbage","period_start":"2026-10-22","portfolios":{}}',
                                     '{"as_of":"2026-10-29","period_start":"2026-10-22","portfolios":{"core":5}}'])
def test_malformed_report_file(tmp_path, capsys, content):
    rep, snap, px = setup(tmp_path)
    rep.write_text(content)
    code, _, err = run(tmp_path, rep, snap, px, capsys)
    assert code == 2 and "ERROR" in err


def test_malformed_entry_not_skipped_in_list_form(tmp_path, capsys):
    rep, snap, px = setup(tmp_path)
    rep.write_text(json.dumps({"as_of": str(AS_OF), "period_start": str(START),
                               "portfolios": [{"portfolio": "core", **good()}, {"nav": 1}]}))
    code, out, _ = run(tmp_path, rep, snap, px, capsys)
    assert code == 1 and "unnamed" in out and "portfolio=congress" in out


def test_csv_report(tmp_path, capsys):
    rep, snap, px = setup(tmp_path)
    rep = tmp_path / "report.csv"
    with open(rep, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["portfolio", "as_of", "period_start", "nav", "spy_return_pct", "drawdown_pct", "turnover_pct"])
        for n in ("core", "congress", "spy_only"):
            w.writerow([n, AS_OF, START, 100000, SPY_RET, -1, 5])
        w.writerow(["extra", AS_OF, START, "", SPY_RET, -1, 5])
    code, out, _ = run(tmp_path, rep, snap, px, capsys)
    assert code == 1 and "portfolio=extra" in out and "not in params snapshot" in out
    assert "portfolio=core" not in out


def test_bdays_between():
    assert rc.bdays_between(dt.date(2026, 10, 30), dt.date(2026, 11, 2)) == 1   # Fri -> Mon
    assert rc.bdays_between(dt.date(2026, 10, 30), dt.date(2026, 10, 30)) == 0
