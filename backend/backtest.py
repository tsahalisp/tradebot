"""Backtest a long/flat strategy over historical bars. evaluate() is the reusable
core (used by run_backtest and by the walk-forward validator). Per-side fee;
position chosen at bar i is realized into bar i+1 (no look-ahead)."""
from datasource import get_bars
from strategies import REGISTRY
from metrics import compute_metrics


def evaluate(closes, strategy="sma_cross", fee_bps=10.0, **params):
    """Run a named strategy -> (metrics, trades)."""
    if strategy not in REGISTRY:
        return None, []
    return evaluate_pos(closes, REGISTRY[strategy](closes, **params), fee_bps)


def evaluate_pos(closes, pos, fee_bps=10.0):
    """Core loop on a price series + a precomputed position list -> (metrics, trades)."""
    fee = float(fee_bps) / 10000.0
    equity, rets, trades = [1.0], [], []
    in_pos, entry_px, entry_i = 0, None, None
    for i in range(len(closes) - 1):
        r = pos[i] * (closes[i + 1] / closes[i] - 1.0)
        if i > 0 and pos[i] != pos[i - 1]:
            r -= fee
        rets.append(r); equity.append(equity[-1] * (1 + r))
        if in_pos == 0 and pos[i] == 1:
            in_pos, entry_px, entry_i = 1, closes[i], i
        elif in_pos == 1 and pos[i] == 0:
            trades.append({"entry_i": entry_i, "exit_i": i,
                           "pnl_pct": round((closes[i] / entry_px - 1 - 2 * fee) * 100, 2)})
            in_pos = 0
    if in_pos == 1:
        trades.append({"entry_i": entry_i, "exit_i": len(closes) - 1, "open": True,
                       "pnl_pct": round((closes[-1] / entry_px - 1 - 2 * fee) * 100, 2)})
    buy_hold = closes[-1] / closes[0] - 1 if len(closes) > 1 else 0.0
    return compute_metrics(equity, rets, pos, trades, "1d", buy_hold), trades


def run_backtest(product="SPY", granularity="1d", strategy="sma_cross", fee_bps=10.0, **params):
    if strategy not in REGISTRY:
        return {"error": f"unknown strategy '{strategy}'", "available": list(REGISTRY)}
    bars = get_bars(product, granularity, 400)
    closes = [b["c"] for b in bars]
    if len(closes) < 35:
        return {"error": "not enough bars"}
    m, trades = evaluate(closes, strategy, fee_bps, **params)
    return {"product": product, "granularity": granularity, "strategy": strategy, "params": params,
            "fee_bps": fee_bps, "bars": len(bars), "metrics": m, "recent_trades": trades[-8:]}
