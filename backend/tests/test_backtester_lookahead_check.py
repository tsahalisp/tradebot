"""Tests for tools/backtester_lookahead_check.py (synthetic data, fixed seeds, tmp_path only).
Benchmark/placebo: noise-signal Sharpe through the real backtester must be ~0.
xfail(strict=True) marks real backtester findings; the backtester is deliberately not fixed."""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import backtester_lookahead_check as chk  # noqa: E402
from strategies import REGISTRY  # noqa: E402

CLOSES = chk.synthetic_closes(500, seed=7)


def leaky(closes, **kw):
    """Deliberately leaky: goes long iff TOMORROW's close is higher."""
    return [1 if i + 1 < len(closes) and closes[i + 1] > closes[i] else 0
            for i in range(len(closes))]


def names(flags):
    return {c for c, _ in flags}


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_clean_strategies_pass_every_check(name):
    status, flags, _ = chk.run_checks(CLOSES, REGISTRY[name])
    assert status == "ok" and flags == []


def test_leaky_strategy_flagged():
    status, flags, _ = chk.run_checks(CLOSES, leaky)
    assert status == "flagged"
    assert {"lookahead_truncation", "lookahead_shift"} <= names(flags)


def test_noise_placebo_near_zero_edge():
    flags, mean = chk.check_placebo(CLOSES)
    assert flags == [] and abs(mean) < 0.5


def test_placebo_flags_biased_fixture():
    # strong drift + random 50% exposure => clearly nonzero Sharpe => must be flagged
    biased = chk.synthetic_closes(500, seed=7, drift=0.01)
    flags, _ = chk.check_placebo(biased)
    assert names(flags) == {"placebo"}


@pytest.mark.parametrize("bad,frag", [
    ([], "empty"),
    ([100.0] * 30 + [float("nan")], "non-finite"),
    ([100.0] * 30 + [float("inf")], "non-finite"),
    ([100.0] * 29 + [-1.0], "non-positive"),
])
def test_bad_input_flagged(bad, frag):
    status, flags, _ = chk.run_checks(bad, REGISTRY["sma_cross"])
    assert status == "flagged" and frag in flags[0][1]


def test_misaligned_dates_flagged():
    d = [f"2024-01-{i + 1:02d}" for i in range(25)]
    assert "misaligned" in chk.check_input([100.0] * 30, d)[0][1]
    assert "increasing" in chk.check_input([100.0] * 3, ["a", "c", "b"])[0][1]


def test_short_series_is_insufficient_not_computed():
    status, flags, info = chk.run_checks(CLOSES[:19], REGISTRY["sma_cross"])
    assert status == "insufficient data" and info == {}
    assert "19 observations" in flags[0][1]


def test_cli_exit_codes_and_names_file_and_check(tmp_path, capsys):
    assert chk.main(["--strategy", "buy_hold"]) == 0
    p = tmp_path / "bad.csv"
    p.write_text("date,close\n" + "\n".join(f"d{i:03d},{100 + i}" for i in range(30))
                 + "\nd999,nan\n")
    assert chk.main(["--csv", str(p)]) == 1
    out = capsys.readouterr().out
    assert f"file={p}" in out and "check=input" in out
    s = tmp_path / "short.csv"
    s.write_text("date,close\n" + "\n".join(f"d{i:03d},{100 + i}" for i in range(5)) + "\n")
    assert chk.main(["--csv", str(s)]) == 3
    assert "insufficient data" in capsys.readouterr().out.lower()


def test_backtester_realizes_position_into_next_bar():
    """Real backtester: position at bar i earns bar i+1's return (no same-bar fill)."""
    closes = [100.0, 110.0, 110.0] + [110.0] * 20
    m, _ = chk.evaluate_pos(closes, [1, 0] + [0] * 21, fee_bps=0)
    assert m["total_return_pct"] == 10.0
    m2, _ = chk.evaluate_pos(closes, [0, 1] + [0] * 21, fee_bps=0)
    assert m2["total_return_pct"] == 0.0
