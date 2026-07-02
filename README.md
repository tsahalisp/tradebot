# TradeBot

A multi-asset systematic trading research harness: event-driven backtester, walk-forward
validator, point-in-time universe tools, and always-on paper-trading portfolios. Built to
answer one question honestly: **which edges survive when you stop letting the backtest lie?**

Python, FastAPI (`:8001`), SQLite, free data sources (yfinance, Coinbase public API).
Runs unattended as a macOS `launchd` service with weekly rebalances and NAV tracked vs SPY.

## Findings (the part that matters)

**Volatility targeting is the one robust edge I found.** Scaling exposure by
`target_vol / realized_vol` raised Sharpe and roughly halved max drawdown on SPY/QQQ,
and the result held in both halves of the data and across parameter choices — a plateau,
not a spike. Conclusion: with free daily data, risk management beats return-hunting.

**12-1 momentum was survivorship bias wearing a suit.** Ranking *today's* S&P members by
past returns produced t = 2.08. Retesting survivorship-free (sector ETFs) gave t = −0.37.
So I built `momentum_pit.py`: a point-in-time test that ranks only stocks that were index
members *on each historical date* (via `universe.members_asof`). The lesson is baked into
the codebase: universe construction can fabricate alpha.

**A seasonal strategy I designed got benched by its own harness.** `seasonal_lowvol`
(turn-of-year + low-vol + volume tilt) showed a faint edge that could not be distinguished
from a placebo family of random calendar rules. It stays in paper until it clears that bar.

**Diversified trend-following is a crisis hedge, not a return machine.** Cross-asset trend
cut SPY drawdowns and nudged blend Sharpe from 0.76 to 0.79 — real, but it does not beat
a plain 60/40 on return. Documented as such.

## Design principles

- **No look-ahead, structurally.** The position chosen at bar *i* is realized into bar
  *i+1*'s return, enforced in `backtest.evaluate()` — not by author vigilance.
- **Honest costs.** Per-side fees on every trade; every strategy benchmarked against
  buy-and-hold on the same bars.
- **Walk-forward by default.** `validate.py` optimizes parameters in-sample, then measures
  those same parameters on data they never saw. The in-sample/out-of-sample gap is
  reported as the overfitting penalty.
- **Placebo benchmarks.** A strategy must beat a family of random look-alikes, not zero.

## Architecture

| Module | Role |
|---|---|
| `backtest.py` | Reusable evaluation core (fees, no-look-ahead realization) |
| `strategies.py` | Strategy interface: `f(closes, **params) -> position series` |
| `validate.py` | Walk-forward / out-of-sample validation with parameter grids |
| `universe.py`, `momentum_pit.py` | Point-in-time index membership + survivorship-reduced tests |
| `datasource.py` | Bars from yfinance / Coinbase public (cached to parquet) |
| `paper.py`, `autoportfolio.py` | Live paper books: weekly rebalance, NAV vs SPY, fee buffers |
| `conviction.py`, `screener.py` | Cross-signal 0-100 scoring + log-settle-attribution harness |
| `metrics.py` | Sharpe, drawdown, turnover, trade stats |
| `main.py` | FastAPI service layer |

## Honest limitations

Daily bars and free data — no intraday microstructure, and data quality is what free
sources allow. The vol-target overlay is validated but not live: the position engine
needs fractional sizing first. Paper results are small-sample; Sharpe error bars
(~1/sqrt(years)) are respected in every conclusion above. No claim here is an
invitation to trade real money.

## Running

```bash
cd backend && python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python main.py        # serves on :8001
python validate.py    # walk-forward a strategy
python momentum_pit.py  # the point-in-time momentum study
```
