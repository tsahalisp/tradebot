"""Read-only self-check of the repo's performance-metric functions.

Cross-checks each located metric function against an independent numpy-only
reference on fixed-seed synthetic daily returns (no network, no files, no DB).
Flags: (a) value mismatch, (b) annualisation factor != documented, or differing
across modules, (c) silent handling of NaN/empty input, (d) risk-free rate
ignored/double-applied vs the documented convention, (e) < MIN_OBS observations
(printed as INSUFFICIENT DATA, function not called). A target that fails to
import, is missing, or raises on normal data is a FAIL row - never skipped.

Documented convention (the harness's assumption; the repo itself documents none):
daily US-equity bars, 252 periods/year, Sharpe = mean(r - rf/252)/std(r, ddof=1)*sqrt(252),
rf = 0, alpha = simple total-return difference vs benchmark.

Usage: python tools/stats_helper_selfcheck.py     (exit 1 on any FAIL)
"""
import importlib
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ANN = 252                 # documented periods per year (daily equities)
RF = 0.0                  # documented annual risk-free rate
MIN_OBS = 20
ANN_TOL = 0.02            # relative tolerance on implied annualisation factor
SEED = 20261106

# Located in the repo (see report): the only metric implementation is
# metrics.compute_metrics. autoportfolio.status computes alpha inline (a plain
# return difference vs SPY) and is not callable as a function; there is no
# annualised-return, beta, stats-helper or weekly_report implementation.
INLINE_NOT_CHECKABLE = [
    ("autoportfolio", "status (inline alpha_pct = ret - SPY ret)", "needs DB + network pricing"),
]
ABSENT_CONCEPTS = ["annualised return", "regression alpha/beta vs SPY", "weekly_report metrics"]


# ---- independent references (numpy only) ----
def ref_total_return(r):
    return float(np.prod(1.0 + np.asarray(r, float)) - 1.0)


def ref_annualised_return(r, ann=ANN):
    r = np.asarray(r, float)
    return float(np.prod(1.0 + r) ** (ann / len(r)) - 1.0)


def ref_sharpe(r, ann=ANN, rf=RF):
    r = np.asarray(r, float)
    ex = r - rf / ann
    sd = ex.std(ddof=1)
    return float("nan") if sd <= 1e-12 * max(abs(ex.mean()), 1e-300) else float(ex.mean() / sd * math.sqrt(ann))


def ref_max_drawdown(r):
    eq = np.concatenate([[1.0], np.cumprod(1.0 + np.asarray(r, float))])
    return float(np.min(eq / np.maximum.accumulate(eq) - 1.0))


def ref_alpha_beta(r, b, ann=ANN):
    """OLS alpha (annualised, simple) and beta of r on b."""
    r, b = np.asarray(r, float), np.asarray(b, float)
    beta = float(np.cov(r, b, ddof=1)[0, 1] / b.var(ddof=1))
    return float((r.mean() - beta * b.mean()) * ann), beta


def ref_alpha_vs_bench(r, b):
    return ref_total_return(r) - ref_total_return(b)


# ---- targets ----
@dataclass
class Target:
    module: str
    function: str
    adapter: Callable            # (fn, returns, bench, rf) -> dict of fractions: sharpe,max_drawdown,total_return,alpha
    tol: Optional[dict] = None   # per-key abs tolerance (covers output rounding)
    rf_param: bool = False       # adapter forwards rf to the function


def _metrics_adapter(fn, r, b, rf=None):
    r = np.asarray(r, float)
    eq = [1.0] + list(np.cumprod(1.0 + r))
    bh = float(np.prod(1.0 + np.asarray(b, float)) - 1.0) if len(b) else 0.0
    m = fn(eq, list(r), [1] * len(r), [], "1d", bh)
    return {"sharpe": m["sharpe"], "max_drawdown": m["max_drawdown_pct"] / 100,
            "total_return": m["total_return_pct"] / 100, "alpha": m["alpha_vs_hold_pct"] / 100}


DEFAULT_TARGETS = [
    Target("metrics", "compute_metrics", _metrics_adapter,
           tol={"sharpe": 0.006, "max_drawdown": 6e-5, "total_return": 6e-5, "alpha": 1.1e-4}),
]


# ---- helpers ----
def synth(n=500, mean=0.0004, sd=0.01, seed=SEED):
    return np.random.default_rng(seed).normal(mean, sd, n)


def _resolve(t):
    try:
        mod = importlib.import_module(t.module)
    except Exception as e:  # noqa: BLE001 - any import failure must be reported
        return None, f"import failed: {type(e).__name__}: {e}"
    fn = getattr(mod, t.function, None)
    if not callable(fn):
        return None, f"function '{t.function}' not found in module"
    return fn, None


def _close(a, b, tol):
    return (a is not None and b is not None and math.isfinite(a) and math.isfinite(b)
            and abs(a - b) <= tol)


def _classify(t, fn, r, b):
    try:
        out = t.adapter(fn, r, b, None)
    except Exception as e:  # noqa: BLE001
        return "raises", f"{type(e).__name__}: {e}"
    vals = [v for v in out.values()]
    if any(v is None or not math.isfinite(v) for v in vals):
        return "nan", "non-finite metric returned"
    return "silent-finite", f"returned finite values {dict((k, round(v, 4)) for k, v in out.items())}"


def run_checks(targets=None):
    """Return list of (module, function, check, status, detail). Status: PASS/FAIL/INSUFFICIENT DATA/NOTE."""
    targets = DEFAULT_TARGETS if targets is None else targets
    rows = []
    r, b = synth(), synth(seed=SEED + 1)
    hi = synth(mean=0.002)                     # high daily Sharpe -> precise implied factor
    nan_r = r.copy(); nan_r[10] = np.nan
    implied, nan_cls = {}, {}

    for t in targets:
        def add(check, status, detail=""):
            rows.append((t.module, t.function, check, status, detail))
        fn, err = _resolve(t)
        if err:
            add("resolve", "FAIL", err)
            continue
        try:
            got = t.adapter(fn, r, b, None)
        except Exception as e:  # noqa: BLE001
            add("run", "FAIL", f"raised on valid data: {type(e).__name__}: {e}")
            continue
        ref = {"sharpe": ref_sharpe(r), "max_drawdown": ref_max_drawdown(r),
               "total_return": ref_total_return(r), "alpha": ref_alpha_vs_bench(r, b)}
        tol = t.tol or {}
        for k in ref:
            tk = tol.get(k, 1e-9)
            if k not in got:
                add(f"(a) {k}", "FAIL", "adapter returned no value")
            elif _close(got[k], ref[k], tk):
                add(f"(a) {k}", "PASS", f"{got[k]:.6g} vs ref {ref[k]:.6g}")
            else:
                add(f"(a) {k}", "FAIL", f"got {got[k]:.6g} vs ref {ref[k]:.6g} (tol {tk})")
        # (b) implied annualisation factor from Sharpe on a high-Sharpe series
        try:
            s = t.adapter(fn, hi, b, None)["sharpe"]
            unit = float(hi.mean() / hi.std(ddof=1))
            f = (s / unit) ** 2
            implied[(t.module, t.function)] = f
            ok = abs(f / ANN - 1) <= ANN_TOL
            add("(b) annualisation", "PASS" if ok else "FAIL",
                f"implied {f:.0f} periods/yr vs documented {ANN}")
        except Exception as e:  # noqa: BLE001
            add("(b) annualisation", "FAIL", f"could not infer: {type(e).__name__}: {e}")
        # (d) risk-free handling
        if t.rf_param:
            try:
                rf = 0.04
                s = t.adapter(fn, hi, b, rf)["sharpe"]
                once, twice, none = ref_sharpe(hi, rf=rf), ref_sharpe(hi, rf=2 * rf), ref_sharpe(hi, rf=0.0)
                tk = tol.get("sharpe", 1e-9)
                if _close(s, once, tk):
                    add("(d) risk-free", "PASS", "rf applied exactly once")
                elif _close(s, twice, tk):
                    add("(d) risk-free", "FAIL", "rf double-applied")
                elif _close(s, none, tk):
                    add("(d) risk-free", "FAIL", "rf parameter ignored")
                else:
                    add("(d) risk-free", "FAIL", f"sharpe {s:.4g} matches no rf convention (once {once:.4g})")
            except Exception as e:  # noqa: BLE001
                add("(d) risk-free", "FAIL", f"{type(e).__name__}: {e}")
        else:
            ok = RF == 0.0
            add("(d) risk-free", "PASS" if ok else "FAIL",
                "no rf input; consistent with documented rf=0" if ok
                else f"function ignores rf but documented rf={RF}")
        # (c) NaN / empty
        for label, rr in (("empty", np.array([])), ("NaN", nan_r)):
            cls, det = _classify(t, fn, rr, b if label == "NaN" else np.array([]))
            nan_cls.setdefault(label, {})[(t.module, t.function)] = cls
            add(f"(c) {label} input", "PASS" if cls in ("raises", "nan") else "FAIL",
                f"{cls}: {det}" if cls != "silent-finite" else f"silent: {det}")
        # (e) minimum observations - not computed
        add("(e) < %d obs" % MIN_OBS, "INSUFFICIENT DATA", "n=10 series not computed")

    # cross-module consistency (only meaningful with >=2 resolved targets)
    if len(implied) > 1 and max(implied.values()) / min(implied.values()) - 1 > ANN_TOL:
        rows.append(("(cross)", "*", "(b) annualisation consistency", "FAIL",
                     ", ".join(f"{m}.{f}={v:.0f}" for (m, f), v in implied.items())))
    for label, d in nan_cls.items():
        if len(set(d.values())) > 1:
            rows.append(("(cross)", "*", f"(c) {label} consistency", "FAIL",
                         ", ".join(f"{m}.{f}={v}" for (m, f), v in d.items())))
    return rows


def print_table(rows):
    w = [max(len(str(x[i])) for x in rows + [("module", "function", "check", "status", "detail")]) for i in range(4)]
    line = lambda x: "  ".join(str(x[i]).ljust(w[i]) for i in range(4)) + "  " + str(x[4])
    print(line(("module", "function", "check", "status", "detail")))
    for x in rows:
        print(line(x))
    for m, f, why in INLINE_NOT_CHECKABLE:
        print(f"NOTE: {m}.{f}: not checkable ({why})")
    print("NOTE: no implementation found for: " + "; ".join(ABSENT_CONCEPTS))


def main():
    rows = run_checks()
    print_table(rows)
    fails = [x for x in rows if x[3] == "FAIL"]
    for m, f, c, _, d in fails:
        print(f"FAIL: {m}.{f} :: {c} :: {d}", file=sys.stderr)
    print(f"\n{len(rows)} checks, {len(fails)} FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
