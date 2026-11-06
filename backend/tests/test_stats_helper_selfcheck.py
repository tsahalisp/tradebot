"""Tests for tools/stats_helper_selfcheck.py. Synthetic data only (tmp_path for any files).

Benchmark/placebo: a SPY-like random walk compared with itself must show alpha ~ 0
and beta ~ 1; a constant-return series has closed-form answers. xfail(strict=True)
tests document REAL discrepancies in metrics.compute_metrics (code deliberately not fixed).
"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import stats_helper_selfcheck as sc  # noqa: E402


def by_check(rows, check_prefix, func=None):
    out = [r for r in rows if r[2].startswith(check_prefix) and (func is None or r[1] == func)]
    assert out, f"no row for {check_prefix}"
    return out


def good_adapter(fn, r, b, rf=None):
    r = np.asarray(r, float)
    return {"sharpe": sc.ref_sharpe(r), "max_drawdown": sc.ref_max_drawdown(r),
            "total_return": sc.ref_total_return(r), "alpha": sc.ref_alpha_vs_bench(r, b)}


def mk_module(tmp_path, monkeypatch, name, body):
    (tmp_path / f"{name}.py").write_text(body)
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(name, None)


# (1) placebo: SPY-like random walk vs itself
def test_placebo_alpha_zero_beta_one():
    spy = sc.synth(n=1000, mean=0.0004, sd=0.011, seed=7)
    alpha, beta = sc.ref_alpha_beta(spy, spy)
    assert abs(alpha) < 1e-12 and abs(beta - 1) < 1e-12
    assert sc.ref_alpha_vs_bench(spy, spy) == 0.0
    # the real function agrees: strategy == benchmark -> alpha_vs_hold 0
    got = sc._metrics_adapter(__import__("metrics").compute_metrics, spy, spy)
    assert got["alpha"] == 0.0


# (2) known constant-return series: exact values
def test_constant_return_exact():
    c, n = 0.001, 252
    r = np.full(n, c)
    assert sc.ref_total_return(r) == pytest.approx((1 + c) ** n - 1, rel=1e-12)
    assert sc.ref_annualised_return(r) == pytest.approx((1 + c) ** 252 - 1, rel=1e-12)
    assert sc.ref_max_drawdown(r) == 0.0
    assert np.isnan(sc.ref_sharpe(r))                 # zero variance -> undefined
    d = np.full(n, -0.01)
    assert sc.ref_max_drawdown(d) == pytest.approx(0.99 ** n - 1, rel=1e-12)
    got = sc._metrics_adapter(__import__("metrics").compute_metrics, r, np.zeros(n))
    assert got["total_return"] == pytest.approx((1 + c) ** n - 1, abs=6e-5)
    assert got["max_drawdown"] == 0.0


@pytest.mark.xfail(strict=True, reason="metrics.compute_metrics returns Sharpe 0.0 for zero-variance "
                   "returns (sd>0 guard) instead of NaN/undefined. Proposed fix: return None/NaN when sd==0.")
def test_constant_return_sharpe_undefined_not_zero():
    got = sc._metrics_adapter(__import__("metrics").compute_metrics, np.full(100, 0.001), np.zeros(100))
    assert not np.isfinite(got["sharpe"]) or got["sharpe"] is None


# real-code checks against reference
def test_real_function_drawdown_total_alpha_match():
    rows = sc.run_checks()
    for k in ("(a) max_drawdown", "(a) total_return", "(a) alpha"):
        assert by_check(rows, k)[0][3] == "PASS"
    assert by_check(rows, "(d)")[0][3] == "PASS"


@pytest.mark.xfail(strict=True, reason="metrics._PPY['1d']=365 and backtest.evaluate_pos hard-codes '1d' for "
                   "equity bars (252 trading days) -> Sharpe inflated by sqrt(365/252)=1.20x. Proposed fix: "
                   "use 252 for equities (granularity key per asset class) - needs Peter's approval.")
def test_real_annualisation_is_252():
    rows = sc.run_checks()
    assert by_check(rows, "(b) annualisation")[0][3] == "PASS"
    assert by_check(rows, "(a) sharpe")[0][3] == "PASS"


@pytest.mark.xfail(strict=True, reason="compute_metrics on an empty return series returns all-zero metrics "
                   "silently (Sharpe 0.0, no error). Proposed fix: raise ValueError / return None below a min "
                   "sample; likewise Sharpe=0.0 when returns contain NaN (sd NaN fails 'sd>0').")
def test_real_empty_input_not_silent():
    assert by_check(sc.run_checks(), "(c) empty")[0][3] == "PASS"


# (3) deliberately wrong annualisation flagged
def test_wrong_annualisation_flagged(tmp_path, monkeypatch):
    mk_module(tmp_path, monkeypatch, "fake_wrong_ann", "def f(r):\n    return r\n")

    def adapter(fn, r, b, rf=None):
        out = good_adapter(fn, r, b)
        r = np.asarray(r, float)
        if len(r) > 1 and r.std(ddof=1) > 0:
            out["sharpe"] = r.mean() / r.std(ddof=1) * np.sqrt(365)
        return out
    rows = sc.run_checks([sc.Target("fake_wrong_ann", "f", adapter)])
    st = by_check(rows, "(b) annualisation")[0]
    assert st[3] == "FAIL"
    assert by_check(rows, "(a) sharpe")[0][3] == "FAIL"
    # control: correct annualisation passes everything but is not flagged
    ok = sc.run_checks([sc.Target("fake_wrong_ann", "f", good_adapter)])
    assert by_check(ok, "(b)")[0][3] == "PASS" and by_check(ok, "(a) sharpe")[0][3] == "PASS"


def test_rf_double_applied_and_ignored_flagged(tmp_path, monkeypatch):
    mk_module(tmp_path, monkeypatch, "fake_rf", "def f(r):\n    return r\n")

    def mk(mult):
        def adapter(fn, r, b, rf=None):
            out = good_adapter(fn, r, b)
            if rf is not None:
                out["sharpe"] = sc.ref_sharpe(r, rf=rf * mult)
            return out
        return adapter
    assert by_check(sc.run_checks([sc.Target("fake_rf", "f", mk(1), rf_param=True)]), "(d)")[0][3] == "PASS"
    d2 = by_check(sc.run_checks([sc.Target("fake_rf", "f", mk(2), rf_param=True)]), "(d)")[0]
    assert d2[3] == "FAIL" and "double" in d2[4]
    d0 = by_check(sc.run_checks([sc.Target("fake_rf", "f", mk(0), rf_param=True)]), "(d)")[0]
    assert d0[3] == "FAIL" and "ignored" in d0[4]


# (4) NaN / empty input
def test_nan_and_empty_policy(tmp_path, monkeypatch):
    mk_module(tmp_path, monkeypatch, "fake_nan", "def f(r):\n    return r\ng = f\n")

    def silent(fn, r, b, rf=None):
        r = np.asarray(r, float)
        if len(r) == 0 or np.isnan(r).any():
            return {"sharpe": 0.0, "max_drawdown": 0.0, "total_return": 0.0, "alpha": 0.0}
        return good_adapter(fn, r, b)

    def loud(fn, r, b, rf=None):
        r = np.asarray(r, float)
        if len(r) == 0:
            raise ValueError("empty returns")
        out = good_adapter(fn, r, b)
        return out                      # NaN propagates -> non-finite -> loud enough
    rs = sc.run_checks([sc.Target("fake_nan", "f", silent)])
    assert by_check(rs, "(c) empty")[0][3] == "FAIL" and by_check(rs, "(c) NaN")[0][3] == "FAIL"
    rl = sc.run_checks([sc.Target("fake_nan", "f", loud)])
    assert by_check(rl, "(c) empty")[0][3] == "PASS" and by_check(rl, "(c) NaN")[0][3] == "PASS"
    # cross-module inconsistency is flagged
    rc = sc.run_checks([sc.Target("fake_nan", "f", silent), sc.Target("fake_nan", "g", loud)])
    assert any(r[0] == "(cross)" and r[3] == "FAIL" for r in rc)


# (5) short series: marked insufficient, function not computed
def test_short_series_insufficient_not_computed(tmp_path, monkeypatch):
    mk_module(tmp_path, monkeypatch, "fake_short", "def f(r):\n    return r\n")
    calls = []

    def adapter(fn, r, b, rf=None):
        calls.append(len(r))
        return good_adapter(fn, r, b)
    rows = sc.run_checks([sc.Target("fake_short", "f", adapter)])
    assert by_check(rows, "(e)")[0][3] == "INSUFFICIENT DATA"
    assert 10 not in calls                       # the 10-obs series was never computed


# (6) missing / erroring / unimportable target reported, not skipped
def test_missing_erroring_unimportable_reported(tmp_path, monkeypatch):
    mk_module(tmp_path, monkeypatch, "fake_err", "def boom(*a):\n    raise RuntimeError('x')\n")
    ts = [sc.Target("fake_err", "absent", good_adapter),
          sc.Target("fake_err", "boom", lambda fn, r, b, rf=None: fn(r)),
          sc.Target("no_such_module_xyz", "f", good_adapter)]
    rows = sc.run_checks(ts)
    assert len(rows) == 3
    assert all(r[3] == "FAIL" for r in rows)
    assert {r[0] for r in rows} == {"fake_err", "no_such_module_xyz"}
    assert "not found" in rows[0][4] and "raised" in rows[1][4] and "import failed" in rows[2][4]


# CLI: non-zero exit naming module/function/check; script runs from another cwd
def test_cli_exit_nonzero_names_failure(tmp_path):
    import subprocess
    p = subprocess.run([sys.executable, str(ROOT / "tools" / "stats_helper_selfcheck.py")],
                       cwd=tmp_path, capture_output=True, text=True)
    assert p.returncode == 1
    assert "FAIL: metrics.compute_metrics :: (b) annualisation" in p.stderr
    assert "INSUFFICIENT DATA" in p.stdout
