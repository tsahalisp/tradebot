"""Edge metrics from an equity curve + round-trip trades. Honest by construction:
the equity curve already includes fees + 1-bar execution lag from the backtester."""
import math

_PPY = {"1m": 525600, "5m": 105120, "15m": 35040, "1h": 8760, "6h": 1460, "1d": 365}


def compute_metrics(equity, rets, pos, trades, granularity, buy_hold):
    ppy = _PPY.get(granularity, 365)
    total = equity[-1] / equity[0] - 1 if len(equity) > 1 else 0.0
    n = len(rets)
    if n > 1:
        mean = sum(rets) / n
        var = sum((r - mean) ** 2 for r in rets) / (n - 1)
        sd = math.sqrt(var)
        sharpe = (mean / sd * math.sqrt(ppy)) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    peak, mdd = equity[0], 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    wins = [t["pnl_pct"] for t in trades if t["pnl_pct"] > 0]
    losses = [t["pnl_pct"] for t in trades if t["pnl_pct"] <= 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0.0
    pf = (sum(wins) / abs(sum(losses))) if (losses and sum(losses) != 0) else None
    exposure = sum(1 for p in pos if p != 0) / len(pos) * 100 if pos else 0.0
    return {
        "total_return_pct": round(total * 100, 2),
        "buy_hold_return_pct": round(buy_hold * 100, 2),
        "alpha_vs_hold_pct": round((total - buy_hold) * 100, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown_pct": round(mdd * 100, 2),
        "num_trades": len(trades),
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(pf, 2) if pf else None,
        "exposure_pct": round(exposure, 1),
    }
