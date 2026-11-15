"""Tests for tools/multiple_testing_check.py. Synthetic data in tmp_path only, fixed seeds.
Placebo/benchmark: a SPY-like benchmark is regressed out; a sign-flip placebo must show ~0 edge on noise."""
import json
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import multiple_testing_check as mtc  # noqa: E402

N = 500


def bench(seed=1):
    return np.random.default_rng(seed).normal(0.0004, 0.01, N)


def write(tmp_path, strats, nv, b=None, name="in.json"):
    b = bench() if b is None else b
    d = {"benchmark": list(b), "strategies": {k: list(v) for k, v in strats.items()}}
    if nv is not None:
        d["n_variants_tried"] = nv
    p = tmp_path / name
    p.write_text(json.dumps(d))
    return str(p)


def noise(k=50):
    rng = np.random.default_rng(7)
    return {f"noise{i:02d}": rng.normal(0, 0.01, N) for i in range(k)}


def run(path, capsys, *extra):
    code = mtc.main(["--input", path, *extra])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_noise_best_is_raw_significant_but_flagged_and_placebo_null(tmp_path, capsys):
    s = noise()
    b = bench()
    rows, fails = mtc.analyze(b, s, 50)
    best = min(rows.values(), key=lambda r: r["p_raw"])
    assert best["p_raw"] < 0.05           # looks like an edge raw
    assert best["p_bonf"] >= 0.05 and best["p_bh"] >= 0.05
    assert best["flag"] == "RAW-ONLY"
    assert any(f[0] == best["name"] and f[1] == "raw_significant_not_after_adjustment" for f in fails)
    # placebo edge is ~0 relative to the strategies' own scale (1% daily vol => ~16% ann vol)
    assert all(abs(r["placebo_alpha_ann"]) < 0.02 for r in rows.values())
    code, out, err = run(write(tmp_path, s, 50), capsys)
    assert code == 1 and best["name"] in err and "raw_significant_not_after_adjustment" in err
    assert "p_bonf" in out and best["name"] in out


def test_real_alpha_survives_adjustment(tmp_path, capsys):
    s = noise(49)
    rng = np.random.default_rng(99)
    s["real_alpha"] = 0.4 * bench() + 0.002 + rng.normal(0, 0.01, N)  # ~50% annual alpha
    rows, fails = mtc.analyze(bench(), s, 50)
    r = rows["real_alpha"]
    assert r["p_bonf"] < 0.05 and r["p_bh"] < 0.05 and r["p_placebo"] < 0.05 and r["flag"] == ""
    assert not [f for f in fails if f[0] == "real_alpha"]


def test_placebo_is_reproducible():
    s = noise(3)
    a, _ = mtc.analyze(bench(), s, 3)
    b, _ = mtc.analyze(bench(), s, 3)
    assert [r["p_placebo"] for r in a.values()] == [r["p_placebo"] for r in b.values()]


@pytest.mark.parametrize("nv,check", [(None, "variants_missing"), (3, "variants_too_small")])
def test_variants_missing_or_too_small(tmp_path, capsys, nv, check):
    code, _, err = run(write(tmp_path, noise(5), nv), capsys)
    assert code == 1 and check in err


@pytest.mark.parametrize("bad", [[], [0.01] * 29 + [float("nan")], None])
def test_nan_inf_or_empty_returns(tmp_path, capsys, bad):
    s = noise(2)
    if bad is None:
        bad = list(np.random.default_rng(3).normal(0, 0.01, N))
        bad[5] = float("inf")
        p = write(tmp_path, {**s, "bad": bad}, 3)
        p_text = pathlib.Path(p).read_text().replace("Infinity", "1e999")  # keep json parseable
        pathlib.Path(p).write_text(p_text)
    elif bad == []:
        p = write(tmp_path, {**s, "bad": bad}, 3)
    else:
        p = write(tmp_path, {**s, "bad": bad + [0.0] * (N - 30)}, 3)
    code, out, err = run(p, capsys)
    assert code == 1 and "[bad] bad_returns" in err
    assert "bad" in out and "noise00" in out  # others still computed, bad one still listed


def test_nan_in_json_null(tmp_path, capsys):
    s = noise(1)
    v = list(s["noise00"])
    v[3] = None
    code, _, err = run(write(tmp_path, {"noise00": v}, 1), capsys)
    assert code == 1 and "[noise00] bad_returns" in err


def test_short_series_insufficient_not_computed(tmp_path, capsys):
    s = {"short": np.random.default_rng(4).normal(0, 0.01, 19)}
    rows, fails = mtc.analyze(bench()[:19], s, 1)
    assert rows["short"]["status"] == "insufficient data" and "p_raw" not in rows["short"]
    p = write(tmp_path, s, 1, b=bench()[:19])
    code, out, err = run(p, capsys)
    assert code == 1 and "[short] insufficient_data" in err and "INSUFFICIENT DATA" in out


def test_exactly_20_obs_is_computed():
    s = {"ok20": np.random.default_rng(4).normal(0, 0.01, 20)}
    rows, _ = mtc.analyze(bench()[:20], s, 1)
    assert "p_raw" in rows["ok20"]


def test_length_mismatch_flagged(tmp_path, capsys):
    code, _, err = run(write(tmp_path, {"m": np.zeros(40) + np.arange(40) * 1e-5}, 1), capsys)
    assert code == 1 and "[m] length_mismatch" in err


def test_missing_file(tmp_path, capsys):
    code, _, err = run(str(tmp_path / "nope.json"), capsys)
    assert code == 2 and "input_file" in err


@pytest.mark.parametrize("content", ["", "{not json", "[1,2,3]", '{"benchmark": [1], "strategies": {"a": 5}}',
                                     '{"benchmark": [1]}', '{"benchmark": [1], "strategies": {"a": ["x"]}}'])
def test_malformed_file(tmp_path, capsys, content):
    p = tmp_path / "bad.json"
    p.write_text(content)
    code, _, err = run(str(p), capsys)
    assert code == 2 and "input_file" in err


def test_csv_input_with_variants_flag(tmp_path, capsys):
    b, s = bench(), noise(2)
    lines = ["date,benchmark,noise00,noise01"]
    lines += [f"d{i},{b[i]},{s['noise00'][i]},{s['noise01'][i]}" for i in range(N)]
    p = tmp_path / "in.csv"
    p.write_text("\n".join(lines))
    code, out, _ = run(str(p), capsys, "--variants", "2")
    assert "noise01" in out and code in (0, 1)
    code, _, err = run(str(p), capsys)
    assert code == 1 and "variants_missing" in err


def test_csv_ragged_rows_malformed(tmp_path, capsys):
    p = tmp_path / "in.csv"
    p.write_text("benchmark,a\n0.1,0.2\n0.3\n")
    code, _, err = run(str(p), capsys, "--variants", "1")
    assert code == 2 and "input_file" in err
