"""Walk-forward / out-of-sample validation. Optimize a strategy's params on the
in-sample window, then measure how those SAME params do on data they never saw.
The gap between in-sample and out-of-sample alpha is the overfitting penalty —
the single most important quant lesson (Module 7)."""
from datasource import get_bars
from backtest import evaluate

GRIDS = {
    "buy_hold": [{}],
    "sma_cross": [{"fast": f, "slow": s} for f in (5, 10, 20, 50) for s in (30, 50, 100, 200) if f < s],
    "zscore_meanrev": [{"n": n, "entry": e} for n in (10, 20, 30) for e in (-1.0, -1.5, -2.0)],
    "ts_momentum": [{"lookback": lb} for lb in (63, 126, 189, 252)],
    "donchian": [{"n": n, "exit_n": x} for n in (20, 40, 55) for x in (10, 20)],
    "sma200_filter": [{"n": n} for n in (100, 150, 200)],
}


def validate(product="SPY", strategy="sma_cross", split=0.7, fee_bps=10.0):
    bars = get_bars(product, "1d", 600)
    closes = [b["c"] for b in bars]
    if len(closes) < 120:
        return {"error": "not enough bars for a train/test split"}
    k = int(len(closes) * split)
    is_closes, oos_closes = closes[:k], closes[k:]
    grid = GRIDS.get(strategy, [{}])

    best_params, best_m = None, None
    for params in grid:
        m, _ = evaluate(is_closes, strategy, fee_bps, **params)
        if m is None:
            continue
        if best_m is None or m["alpha_vs_hold_pct"] > best_m["alpha_vs_hold_pct"]:
            best_params, best_m = params, m
    if best_m is None:
        return {"error": "no valid params"}
    oos_m, _ = evaluate(oos_closes, strategy, fee_bps, **best_params)
    is_a, oos_a = best_m["alpha_vs_hold_pct"], oos_m["alpha_vs_hold_pct"]
    gap = round(is_a - oos_a, 2)

    if oos_a > 0.5:
        verdict = (f"Edge HELD out-of-sample (OOS alpha +{oos_a}%). Promising — but confirm on more "
                   f"tickers and periods before trusting it.")
    elif oos_a > -0.5:
        verdict = (f"Edge VANISHED out-of-sample (OOS alpha {oos_a}% vs in-sample +{is_a}%). The in-sample "
                   f"result was largely curve-fit — the classic overfit trap.")
    else:
        verdict = (f"NEGATIVE out-of-sample (OOS alpha {oos_a}%). These tuned params actively hurt on unseen "
                   f"data. The backtest was a mirage.")
    return {
        "product": product, "strategy": strategy, "split": split,
        "best_params_in_sample": best_params,
        "in_sample": {"bars": len(is_closes), "alpha_vs_hold_pct": is_a,
                      "return_pct": best_m["total_return_pct"], "sharpe": best_m["sharpe"]},
        "out_of_sample": {"bars": len(oos_closes), "alpha_vs_hold_pct": oos_a,
                          "return_pct": oos_m["total_return_pct"], "sharpe": oos_m["sharpe"]},
        "overfit_gap_pct": gap,
        "verdict": verdict,
        "lesson": ("A strategy optimized on the past almost always looks great in-sample. Only out-of-sample "
                   "performance is evidence of a real edge — everything else is hindsight."),
    }
