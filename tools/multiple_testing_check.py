#!/usr/bin/env python3
"""Read-only multiple-testing check for candidate strategy daily-return series.

Input (--input FILE, plus --variants N for CSV):
  JSON: {"n_variants_tried": N, "benchmark": [...], "strategies": {"name": [...], ...}}
  CSV:  header row; a "benchmark" column plus one column per strategy (optional "date" column).
Per strategy: raw Sharpe, alpha t-stat vs benchmark, Bonferroni and BH adjusted p-values using the
TOTAL number of variants tried, and a fixed-seed sign-flip (shuffled-signal) placebo p-value.
Exit 0 = all clean, 1 = a check failed (strategy + check named), 2 = input file missing/malformed.
Never writes anything; no network; numpy only (math.erfc for the normal tail).
"""
import argparse
import csv
import json
import math
import sys

import numpy as np

MIN_OBS = 20
ALPHA = 0.05
N_PLACEBO = 2000
SEED = 12345
ANN = 252


class InputError(Exception):
    pass


def _num_array(values):
    try:
        return np.array([np.nan if v is None or v == "" else float(v) for v in values], dtype=float)
    except (TypeError, ValueError) as e:
        raise InputError(f"non-numeric value in series: {e}")


def load_input(path, variants=None):
    """Return (benchmark, {name: array}, n_variants or None). Raises InputError."""
    try:
        with open(path, newline="") as f:
            text = f.read()
    except OSError as e:
        raise InputError(f"cannot read input file {path}: {e}")
    if not text.strip():
        raise InputError(f"input file {path} is empty")
    if str(path).lower().endswith(".json"):
        try:
            d = json.loads(text)
        except ValueError as e:
            raise InputError(f"malformed JSON in {path}: {e}")
        if (not isinstance(d, dict) or not isinstance(d.get("strategies"), dict)
                or not isinstance(d.get("benchmark"), list)):
            raise InputError("JSON must be an object with 'benchmark' (list) and 'strategies' (object of lists)")
        if not all(isinstance(v, list) for v in d["strategies"].values()):
            raise InputError("every entry of 'strategies' must be a list")
        bench = _num_array(d["benchmark"])
        strats = {k: _num_array(v) for k, v in d["strategies"].items()}
        nv = d.get("n_variants_tried")
    else:
        rows = list(csv.reader(text.splitlines()))
        head = [h.strip() for h in rows[0]]
        if "benchmark" not in head:
            raise InputError("CSV needs a 'benchmark' column")
        if any(len(r) != len(head) for r in rows[1:]):
            raise InputError("CSV has rows whose column count differs from the header")
        cols = {h: [r[i] for r in rows[1:]] for i, h in enumerate(head)}
        bench = _num_array(cols.pop("benchmark"))
        cols.pop("date", None)
        strats = {k: _num_array(v) for k, v in cols.items()}
        nv = None
    if not strats:
        raise InputError("no strategy series found in input")
    if variants is not None:
        nv = variants
    return bench, strats, nv


def _p_two_sided(t):
    return math.erfc(abs(t) / math.sqrt(2.0))


def _alpha_t(r, b):
    """OLS r = a + beta*b + e. Return (alpha, t_alpha, beta)."""
    n = len(r)
    X = np.column_stack([np.ones(n), b])
    coef, *_ = np.linalg.lstsq(X, r, rcond=None)
    resid = r - X @ coef
    s2 = resid @ resid / (n - 2)
    try:
        cov = s2 * np.linalg.inv(X.T @ X)
        se = math.sqrt(cov[0, 0])
    except np.linalg.LinAlgError:
        se = 0.0
    t = coef[0] / se if se > 0 else 0.0
    return float(coef[0]), float(t), float(coef[1])


def _sharpe(r):
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(ANN)) if sd > 0 else 0.0


def placebo(r, b, seed, n_boot=N_PLACEBO):
    """Shuffled-signal placebo: random +/-1 signal applied to the benchmark-neutral return stream.
    Returns (p_value, mean placebo alpha annualised). Fixed seed -> reproducible."""
    alpha, _, beta = _alpha_t(r, b)
    active = r - beta * b  # strategy return with benchmark exposure removed
    rng = np.random.default_rng(seed)
    signs = rng.choice([-1.0, 1.0], size=(n_boot, len(r)))
    fake = signs * active  # random-direction version of the same trades
    fake_alpha = fake.mean(axis=1)
    p = (1 + np.sum(np.abs(fake_alpha) >= abs(active.mean()))) / (n_boot + 1)
    return float(p), float(fake_alpha.mean() * ANN)


def analyze(bench, strats, n_variants, seed=SEED):
    """Return (rows, failures). failures = list of (strategy, check, detail)."""
    fails = []
    rows = {}
    n_series = len(strats)
    if n_variants is None:
        fails.append(("<all>", "variants_missing", "number of variants tried not supplied"))
    elif n_variants < n_series:
        fails.append(("<all>", "variants_too_small",
                      f"variants tried={n_variants} < series supplied={n_series}"))
    m = max(n_series, n_variants or 0)

    bench_ok = len(bench) > 0 and bool(np.all(np.isfinite(bench)))
    if not bench_ok:
        fails.append(("<benchmark>", "bad_returns", "benchmark empty or contains NaN/inf"))

    for i, (name, r) in enumerate(strats.items()):
        row = {"name": name, "n": len(r), "status": "ok"}
        rows[name] = row
        if len(r) == 0:
            row["status"] = "empty returns"
            fails.append((name, "bad_returns", "empty returns"))
            continue
        if not np.all(np.isfinite(r)):
            row["status"] = "NaN/inf returns"
            fails.append((name, "bad_returns", "contains NaN or inf"))
            continue
        if len(r) < MIN_OBS:
            row["status"] = "insufficient data"
            fails.append((name, "insufficient_data", f"{len(r)} observations < {MIN_OBS}; not computed"))
            continue
        if not bench_ok:
            row["status"] = "no valid benchmark"
            continue
        if len(r) != len(bench):
            row["status"] = "length mismatch"
            fails.append((name, "length_mismatch", f"{len(r)} obs vs benchmark {len(bench)}"))
            continue
        alpha, t, beta = _alpha_t(r, bench)
        row.update(sharpe=_sharpe(r), alpha_ann=alpha * ANN, beta=beta, t=t, p_raw=_p_two_sided(t))
        row["p_bonf"] = min(1.0, row["p_raw"] * m)
        row["p_placebo"], row["placebo_alpha_ann"] = placebo(r, bench, seed + i)

    # Benjamini-Hochberg over the m variants; untested/uncomputed variants count as p=1.
    comp = sorted((r for r in rows.values() if "p_raw" in r), key=lambda r: r["p_raw"])
    running = 1.0
    for rank in range(len(comp), 0, -1):
        running = min(running, comp[rank - 1]["p_raw"] * m / rank)
        comp[rank - 1]["p_bh"] = running
    for row in comp:
        sig_raw = row["p_raw"] < ALPHA
        row["flag"] = ""
        if sig_raw and min(row["p_bonf"], row["p_bh"]) >= ALPHA:
            row["flag"] = "RAW-ONLY"
            fails.append((row["name"], "raw_significant_not_after_adjustment",
                          f"p_raw={row['p_raw']:.4f} but p_bonf={row['p_bonf']:.4f}, p_bh={row['p_bh']:.4f} (m={m})"))
        elif sig_raw and row["p_placebo"] >= ALPHA:
            row["flag"] = "PLACEBO-FAIL"
            fails.append((row["name"], "placebo_not_beaten", f"p_placebo={row['p_placebo']:.4f}"))
    return rows, fails


def format_table(rows):
    hdr = f"{'strategy':<20}{'n':>6}{'sharpe':>8}{'t_alpha':>9}{'p_raw':>9}{'p_bonf':>9}{'p_bh':>9}{'p_plc':>9}  flag/status"
    out = [hdr, "-" * len(hdr)]
    for r in rows.values():
        if "p_raw" not in r:
            out.append(f"{r['name']:<20}{r['n']:>6}{'-':>8}{'-':>9}{'-':>9}{'-':>9}{'-':>9}{'-':>9}  {r['status'].upper()}")
        else:
            out.append(f"{r['name']:<20}{r['n']:>6}{r['sharpe']:>8.2f}{r['t']:>9.2f}{r['p_raw']:>9.4f}"
                       f"{r['p_bonf']:>9.4f}{r['p_bh']:>9.4f}{r['p_placebo']:>9.4f}  {r['flag']}")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", required=True)
    ap.add_argument("--variants", type=int, default=None, help="total variants tried (overrides JSON field)")
    a = ap.parse_args(argv)
    try:
        bench, strats, nv = load_input(a.input, a.variants)
    except InputError as e:
        print(f"FAIL [input_file] {e}", file=sys.stderr)
        return 2
    rows, fails = analyze(bench, strats, nv)
    print(f"variants tried: {nv if nv is not None else 'MISSING'}   series supplied: {len(strats)}")
    print(format_table(rows))
    for name, check, detail in fails:
        print(f"FAIL [{name}] {check}: {detail}", file=sys.stderr)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
