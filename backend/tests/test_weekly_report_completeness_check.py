"""Tests for tools/weekly_report_completeness_check.py. Synthetic reports in tmp_path only, fixed seed.

Benchmark/placebo: a SPY-only placebo portfolio must show zero excess return vs SPY.
"""
import datetime as dt
import json
import pathlib
import random
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import weekly_report_completeness_check as chk  # noqa: E402

TODAY = dt.date(2026, 11, 12)
EXPECT = "core,congress,spy_placebo"


def make_report(seed=7):
    rng = random.Random(seed)
    spy = round(rng.uniform(-3, 3), 4)

    def pf(name, **kw):
        d = {"name": name, "nav": round(100000 * (1 + rng.uniform(-.05, .05)), 2),
             "excess_return_pct": round(rng.uniform(-2, 2), 4), "drawdown_pct": round(-rng.uniform(0, 8), 4),
             "turnover_pct": round(rng.uniform(0, 40), 4), "n_obs": 60}
        d.update(kw)
        return d
    return {"as_of": "2026-11-09", "benchmark": {"name": "SPY", "return_pct": spy},
            "portfolios": [pf("core"), pf("congress"),
                           pf("spy_placebo", excess_return_pct=0.0, placebo=True)]}


def run(tmp_path, report, capsys, expect=EXPECT, raw=None):
    p = tmp_path / "report.json"
    p.write_text(raw if raw is not None else json.dumps(report))
    rc = chk.main([str(p), "--expect", expect, "--today", TODAY.isoformat()])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def test_complete_report_passes_and_placebo_zero_excess(tmp_path, capsys):
    rep = make_report()
    rc, out, err = run(tmp_path, rep, capsys)
    assert rc == 0, err
    assert "spy_placebo" in out and "4/4" in out
    placebo = [p for p in rep["portfolios"] if p["name"] == "spy_placebo"][0]
    assert placebo["excess_return_pct"] == 0.0


def test_placebo_with_nonzero_excess_fails(tmp_path, capsys):
    rep = make_report()
    rep["portfolios"][2]["excess_return_pct"] = 0.5
    rc, _, err = run(tmp_path, rep, capsys)
    assert rc == 1 and "[spy_placebo]" in err and "PLACEBO_NONZERO_EXCESS" in err


def test_missing_portfolio(tmp_path, capsys):
    rep = make_report()
    rep["portfolios"] = [p for p in rep["portfolios"] if p["name"] != "congress"]
    rc, _, err = run(tmp_path, rep, capsys)
    assert rc == 1 and "[congress] MISSING_PORTFOLIO" in err


@pytest.mark.parametrize("field", chk.REQUIRED)
@pytest.mark.parametrize("bad", [None, "", float("nan"), float("inf"), "abc"])
def test_missing_or_bad_field(tmp_path, capsys, field, bad):
    rep = make_report()
    rep["portfolios"][0][field] = bad
    rc, _, err = run(tmp_path, rep, capsys)
    assert rc == 1 and "[core] MISSING_FIELD: %s" % field in err


def test_absent_field_key(tmp_path, capsys):
    rep = make_report()
    del rep["portfolios"][1]["turnover_pct"]
    rc, _, err = run(tmp_path, rep, capsys)
    assert rc == 1 and "[congress] MISSING_FIELD: turnover_pct" in err


def test_nan_written_by_json_dumps_is_caught(tmp_path, capsys):
    rep = make_report()
    rep["portfolios"][0]["nav"] = float("nan")  # json.dumps emits bare NaN
    rc, _, err = run(tmp_path, rep, capsys)
    assert rc == 1 and "[core] MISSING_FIELD: nav" in err


def test_stale_report(tmp_path, capsys):
    rep = make_report()
    rep["as_of"] = (TODAY - dt.timedelta(days=8)).isoformat()
    rc, _, err = run(tmp_path, rep, capsys)
    assert rc == 1 and "[core] STALE" in err


def test_seven_days_old_is_ok(tmp_path, capsys):
    rep = make_report()
    rep["as_of"] = (TODAY - dt.timedelta(days=7)).isoformat()
    assert run(tmp_path, rep, capsys)[0] == 0


def test_bad_date(tmp_path, capsys):
    rep = make_report()
    rep["as_of"] = "not-a-date"
    rc, _, err = run(tmp_path, rep, capsys)
    assert rc == 1 and "STALE_OR_BAD_DATE" in err


@pytest.mark.parametrize("mutate", [
    lambda r: r.pop("benchmark"),
    lambda r: r["benchmark"].update(return_pct=float("nan")),
    lambda r: r["benchmark"].update(name="QQQ"),
])
def test_missing_spy(tmp_path, capsys, mutate):
    rep = make_report()
    mutate(rep)
    rc, _, err = run(tmp_path, rep, capsys)
    assert rc == 1 and "[SPY (benchmark)] SPY_MISSING" in err


def test_short_series_is_insufficient_and_not_computed(tmp_path, capsys):
    rep = make_report()
    rep["portfolios"][0].update(n_obs=19, excess_return_pct=None, drawdown_pct=None, turnover_pct=None)
    rc, out, err = run(tmp_path, rep, capsys)
    assert rc == 1 and "[core] INSUFFICIENT_DATA" in err
    assert "MISSING_FIELD" not in err  # nulls are correct for a short series
    assert "INSUFFICIENT_DATA" in out


def test_short_series_with_computed_figures_flagged(tmp_path, capsys):
    rep = make_report()
    rep["portfolios"][0]["n_obs"] = 5
    rc, _, err = run(tmp_path, rep, capsys)
    assert rc == 1 and "[core] COMPUTED_ON_SHORT_SERIES" in err


def test_exactly_20_obs_ok(tmp_path, capsys):
    rep = make_report()
    rep["portfolios"][0]["n_obs"] = 20
    assert run(tmp_path, rep, capsys)[0] == 0


def test_missing_n_obs(tmp_path, capsys):
    rep = make_report()
    del rep["portfolios"][0]["n_obs"]
    rc, _, err = run(tmp_path, rep, capsys)
    assert rc == 1 and "[core] MISSING_FIELD: n_obs" in err


def test_missing_file(tmp_path, capsys):
    rc = chk.main([str(tmp_path / "nope.json"), "--expect", EXPECT, "--today", "2026-11-12"])
    assert rc == 1 and "MISSING_OR_MALFORMED_FILE" in capsys.readouterr().err


@pytest.mark.parametrize("raw", ["", "{not json", "[]", '{"as_of": "2026-11-09"}', '{"portfolios": "x"}', "\xff"])
def test_malformed_file(tmp_path, capsys, raw):
    rc, _, err = run(tmp_path, None, capsys, raw=raw)
    assert rc == 1 and "FAIL [" in err


def test_no_expected_portfolios_is_an_error(tmp_path, capsys):
    rc, _, _ = run(tmp_path, make_report(), capsys, expect=" , ")
    assert rc == 2
