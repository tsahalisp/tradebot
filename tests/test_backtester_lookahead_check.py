"""Tests for tools/backtester_lookahead_check.py (shift / truncation / placebo probes).

Toy backtesters stand in for good and bad engines; the REAL backend/backtest.py is also
probed. All data is synthetic and lives under tmp_path. Real-backtester findings are
xfail(strict=True) and the backtester is deliberately not fixed.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import backtester_lookahead_check as chk  # noqa: E402


def _trades(pos):
    out, entry = [], None
    for i, p in enumerate(pos):
        if p == 1 and entry is None:
            entry = i
        elif p == 0 and entry is not None:
            out.append({"entry_i": entry, "exit_i": i, "pnl_pct": 0.0}); entry = None
    return out


def _run(closes, pos):
    eq = 1.0
    for i in range(len(closes) - 1):
        eq *= 1 + pos[i] * (closes[i + 1] / closes[i] - 1)
    tr = _trades(pos[:len(closes) - 1])
    # pnl_pct carries real round-trip return so differing prices show up in comparisons
    for t in tr:
        t["pnl_pct"] = round((closes[t["exit_i"]] / closes[t["entry_i"]] - 1) * 100, 6)
    return {"total_return_pct": (eq - 1) * 100}, tr


def clean_bt(closes, signal):
    """Enforces the 1-day lag: signal[i] only earns bar i+1's return (i.e. acts from i+1)."""
    return _run(closes, [0] + list(signal[:-1]))


def leaky_bt(closes, signal):
    """No lag: signal[i] earns the return it was computed from."""
    return _run(closes, list(signal))


def peek_bt(closes, signal):
    """Lagged, but overrides the signal with a 5-bar-ahead look at the price."""
    pos = [1 if i + 5 < len(closes) and closes[i + 5] > closes[i] else signal[i]
           for i in range(len(closes))]
    return _run(closes, [0] + pos[:-1])


def broken_bt(closes, signal):
    raise RuntimeError("boom")


def by_probe(rows):
    return {p: (res, flags) for p, res, flags in rows}


def test_clean_toy_passes_all_probes(tmp_path):
    rows = chk.run_probes("clean", clean_bt, tmp_path)
    got = by_probe(rows)
    assert set(got) == set(chk.PROBES)
    assert all(not flags for _, flags in got.values()), got
    # placebo: near-zero edge vs buy&hold
    edge = float(got["placebo"][0].split("=")[1].split("%")[0])
    assert abs(edge) < chk.PLACEBO_TOL_PCT


def test_leaky_toy_flagged_by_shift(tmp_path):
    got = by_probe(chk.run_probes("leaky", leaky_bt, tmp_path))
    assert any("LOOK-AHEAD" in f for f in got["shift"][1])
    assert not got["truncation"][1]


def test_future_peeking_toy_flagged_by_truncation(tmp_path):
    got = by_probe(chk.run_probes("peek", peek_bt, tmp_path))
    assert any("FUTURE DATA" in f for f in got["truncation"][1])


def test_short_series_is_insufficient_not_computed(tmp_path):
    called = []
    def spy(closes, signal):
        called.append(1); return clean_bt(closes, signal)
    rows = chk.run_probes("spy", spy, tmp_path, n_obs=chk.MIN_OBS - 1)
    assert not called
    assert [p for p, _, _ in rows] == list(chk.PROBES)
    assert all(r == "not computed" and "insufficient data" in f[0] for _, r, f in rows)


def test_erroring_entry_point_is_reported(tmp_path):
    got = by_probe(chk.run_probes("broken", broken_bt, tmp_path))
    assert set(got) == set(chk.PROBES)
    assert all(res == "ERROR" and "boom" in flags[0] for res, flags in got.values())


def test_missing_entry_point_is_reported(tmp_path, capsys):
    rc = chk.main(["--target", "no_such_module:f", "--target", "backtest:nope"], workdir=tmp_path)
    cap = capsys.readouterr()
    assert rc == 1
    assert "no_such_module:f" in cap.err and "backtest:nope" in cap.err
    assert cap.err.count("probe=") == 2 * len(chk.PROBES)


def test_cli_exit_nonzero_names_function_and_probe(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(chk, "load_target", lambda spec: leaky_bt)
    assert chk.main(["--target", "toy:leaky_bt"], workdir=tmp_path) == 1
    assert "FAIL toy:leaky_bt probe=shift" in capsys.readouterr().err


def test_only_writes_inside_workdir(tmp_path):
    chk.run_probes("clean", clean_bt, tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["synthetic_prices.csv"]


# ---- the REAL backend/backtest.py:evaluate_pos ------------------------------------
# Alignment: pos[i] (known at close i) earns the return from bar i to bar i+1
# (closes[i+1]/closes[i]). Strategies are trusted to be causal; evaluate_pos adds NO extra lag.

def test_real_backtester_truncation_and_placebo_clean(tmp_path):
    got = by_probe(chk.check_target("backtest:evaluate_pos", tmp_path))
    assert not got["truncation"][1], got["truncation"]
    assert not got["placebo"][1], got["placebo"]


@pytest.mark.xfail(strict=True, reason=(
    "evaluate_pos realizes pos[i] into bar i+1 with no lag enforcement of its own: a "
    "signal built from the next return earns a huge edge. Safe only if every strategy is "
    "causal (documented contract). Proposed fix (not applied): optional "
    "`enforce_lag=True` in evaluate_pos that shifts pos by one bar, or a causality "
    "assertion in evaluate() that re-runs the strategy on truncated closes."))
def test_real_backtester_shift_probe(tmp_path):
    got = by_probe(chk.check_target("backtest:evaluate_pos", tmp_path))
    assert not got["shift"][1], got["shift"]
