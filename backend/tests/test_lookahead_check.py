"""Tests for tools/lookahead_check.py (point-in-time leakage guard).
Synthetic prices only, fixed seeds, files only under tmp_path. No strategy code changed.
Placebo/benchmark: clean trailing-mean signal = 0 flags; random signal = ~0 edge."""
import importlib
import random
import statistics
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "backend"))
import lookahead_check as lc  # noqa: E402

CLOSES = lc.synthetic_closes(seed=11, n=400)


def trailing_mean(closes, n=20):
    return [1 if i >= n and closes[i] > statistics.mean(closes[i - n:i]) else 0
            for i in range(len(closes))]


def leaky_shift(closes):  # pos[t] = tomorrow's direction
    return [1 if i + 1 < len(closes) and closes[i + 1] > closes[i] else 0
            for i in range(len(closes))]


def leaky_fullsample(closes):  # normalises by full-sample mean/std
    m, sd = statistics.mean(closes), statistics.pstdev(closes)
    return [1 if sd > 0 and (c - m) / sd > 0 else 0 for c in closes]


def test_placebo_trailing_mean_clean():
    assert lc.find_leaks(trailing_mean, CLOSES, step=1) == []
    assert lc.check_target("m", "trailing_mean", trailing_mean, CLOSES, 1)[0] == "OK"


@pytest.mark.parametrize("fn", [leaky_shift, leaky_fullsample])
def test_leaky_signals_flagged(fn):
    status, detail = lc.check_target("m", fn.__name__, fn, CLOSES, 1)
    assert status == "LEAK" and "t=" in detail


def test_random_placebo_vs_leaky_edge():
    rng = random.Random(3)
    rand_pos = [rng.randint(0, 1) for _ in CLOSES]
    rand_fn = lambda c: rand_pos[:len(c)]  # fixed random draws, prefix-stable => leak-free
    assert lc.find_leaks(rand_fn, CLOSES, 1) == []
    placebo = lc.edge(CLOSES, rand_pos)
    leaky = lc.edge(CLOSES, leaky_shift(CLOSES))
    pit = lc.edge(CLOSES, lc.pit_positions(leaky_shift, CLOSES))  # leak removed
    assert abs(placebo) < 0.002
    assert abs(pit) < 0.002
    assert leaky > 0.005 and leaky > 5 * abs(placebo)


def test_same_bar_detection():
    assert lc.uses_same_bar_close(trailing_mean, CLOSES, 1)       # reads close[t]
    assert not lc.uses_same_bar_close(lambda c: [1] * len(c), CLOSES, 1)
    # real executor trades next bar; a same-bar executor is detected by the probe
    import backtest
    assert lc.executor_realizes_same_bar(backtest.evaluate_pos) is False

    def same_bar_exec(closes, pos, fee):
        r = [pos[i] * (closes[i] / closes[i - 1] - 1) if i else 0 for i in range(len(closes))]
        eq = 1.0
        for x in r:
            eq *= 1 + x
        return {"total_return_pct": (eq - 1) * 100}, []
    assert lc.executor_realizes_same_bar(same_bar_exec) is True
    rows = lc.run([("m", "tm", lambda: trailing_mean)], CLOSES, 1, executor_same_bar=True)
    assert [r[2] for r in rows] == ["SAME_BAR", "SAME_BAR"]


def test_missing_and_erroring_reported(tmp_path, monkeypatch, capsys):
    (tmp_path / "lk_mod.py").write_text(
        "def boom(c):\n    raise RuntimeError('bad data')\n"
        "def short(c):\n    return [0]\n"
        "def leaky(c):\n    return [1 if i + 1 < len(c) and c[i+1] > c[i] else 0 for i in range(len(c))]\n"
        "def fine(c):\n    return [0] * len(c)\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    specs = ["lk_mod:boom", "lk_mod:short", "lk_mod:missing", "no_such_mod:f", "lk_mod:leaky", "lk_mod:fine"]
    args = [a for s in specs for a in ("--target", s)]
    rc = lc.main(args + ["--step", "1"])
    out = capsys.readouterr()
    status = {line.split()[1]: line.split()[2] for line in out.out.splitlines()[1:8] if "lk_mod" in line or "no_such" in line}
    assert rc == 1
    assert status == {"boom": "ERROR", "short": "ERROR", "missing": "ERROR",
                      "f": "ERROR", "leaky": "LEAK", "fine": "OK"}
    for name in ("lk_mod.boom", "lk_mod.missing", "no_such_mod.f", "lk_mod.leaky"):
        assert name in out.err
    assert "lk_mod.fine" not in out.err


def test_real_strategies_clean_and_none_skipped(capsys):
    from strategies import REGISTRY
    rc = lc.main(["--step", "3"])
    out = capsys.readouterr().out
    assert rc == 0
    for k in REGISTRY:  # every registered strategy has a row
        assert f"strategies  {k}" in out
