"""Strategy interface: f(closes, **params) -> position series (0=flat, 1=long).
Both below are PLACEHOLDERS to exercise the harness — replace with your real edge.
No look-ahead: position[i] uses only data through bar i; the backtester realizes it
into bar i+1's return.
"""
import statistics


def _sma(values, n):
    out, s = [None] * len(values), 0.0
    for i, v in enumerate(values):
        s += v
        if i >= n:
            s -= values[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def sma_cross(closes, fast=10, slow=30):
    f, s = _sma(closes, int(fast)), _sma(closes, int(slow))
    pos = [0] * len(closes)
    for i in range(len(closes)):
        if f[i] is not None and s[i] is not None:
            pos[i] = 1 if f[i] > s[i] else 0
    return pos


def zscore_meanrev(closes, n=20, entry=-1.0, exit=0.0):
    n = int(n); pos = [0] * len(closes)
    for i in range(len(closes)):
        if i >= n:
            w = closes[i - n + 1:i + 1]
            m, sd = statistics.mean(w), statistics.pstdev(w)
            z = (closes[i] - m) / sd if sd > 0 else 0.0
            prev = pos[i - 1]
            if prev == 0 and z <= entry:
                pos[i] = 1
            elif prev == 1 and z >= exit:
                pos[i] = 0
            else:
                pos[i] = prev
    return pos


REGISTRY = {"sma_cross": sma_cross, "zscore_meanrev": zscore_meanrev}


def buy_hold(closes, **kw):
    """Own it and wait. The benchmark every active strategy must beat."""
    return [1] * len(closes)


REGISTRY["buy_hold"] = buy_hold

# What each strategy TEACHES — used by the professor layer to turn a backtest into a lesson.
META = {
    "buy_hold": {
        "school": "Passive indexing / compounding",
        "champions": "Jack Bogle (index funds), Warren Buffett (\"buy wonderful companies, hold\")",
        "idea": "Capture the market's long-run upward drift at near-zero cost; let compounding work.",
        "works_when": "Over long horizons in growing markets; it is the hardest baseline to beat after fees.",
        "fails_when": "Deep, long drawdowns test your stomach — you eat the full fall.",
        "diy": "Buy a low-cost broad index (e.g., an S&P 500 fund), add regularly, hold for years, ignore noise.",
        "horizon": "Long term",
    },
    "sma_cross": {
        "school": "Trend following (technical)",
        "champions": "Jesse Livermore, Richard Donchian, the Turtles (managed-futures trend)",
        "idea": "Ride sustained moves: go long when a fast average crosses above a slow average, step aside when it crosses below.",
        "works_when": "Strong, persistent trends — it cuts losers and lets winners run.",
        "fails_when": "Choppy, sideways markets — it whipsaws, paying fees on false signals.",
        "diy": "Plot a 50-day vs 200-day moving average (the \"golden/death cross\"); hold while fast>slow, reduce when fast<slow.",
        "horizon": "Medium term (swing/position)",
    },
    "zscore_meanrev": {
        "school": "Mean reversion (statistical)",
        "champions": "Ed Thorp, statistical-arbitrage desks",
        "idea": "Prices overshoot then snap back: buy when price is unusually far below its recent average (low z-score), exit as it normalizes.",
        "works_when": "Range-bound, liquid markets where extremes revert.",
        "fails_when": "Strong trends or regime breaks — 'cheap' keeps getting cheaper.",
        "diy": "Measure how many standard deviations price sits from its 20-day average; nibble on big negative deviations, trim back near the mean. Always cap risk.",
        "horizon": "Short term",
    },
}


def ts_momentum(closes, lookback=126):
    """Time-series momentum: long while the trailing-`lookback` return is positive."""
    lookback = int(lookback); pos = [0] * len(closes)
    for i in range(len(closes)):
        if i >= lookback:
            pos[i] = 1 if closes[i] > closes[i - lookback] else 0
    return pos


def donchian(closes, n=20, exit_n=10):
    """Turtle breakout: buy an n-day high, exit on an exit_n-day low."""
    n, exit_n = int(n), int(exit_n); pos = [0] * len(closes)
    for i in range(len(closes)):
        if i < n:
            continue
        prior_high = max(closes[i - n:i]); prior_low = min(closes[i - exit_n:i])
        prev = pos[i - 1]
        if prev == 0 and closes[i] > prior_high:
            pos[i] = 1
        elif prev == 1 and closes[i] < prior_low:
            pos[i] = 0
        else:
            pos[i] = prev
    return pos


def sma200_filter(closes, n=200):
    """Faber trend filter: hold while price is above its long-term average, else cash."""
    s = _sma(closes, int(n)); pos = [0] * len(closes)
    for i in range(len(closes)):
        if s[i] is not None:
            pos[i] = 1 if closes[i] > s[i] else 0
    return pos


REGISTRY.update({"ts_momentum": ts_momentum, "donchian": donchian, "sma200_filter": sma200_filter})

META.update({
    "ts_momentum": {
        "school": "Time-series momentum (the momentum factor)",
        "champions": "Jegadeesh & Titman (academic), Cliff Asness / AQR (practitioner)",
        "idea": "Buy what has been rising over the past 6-12 months; on average, momentum persists.",
        "works_when": "Trending markets, and broadly across many assets and eras.",
        "fails_when": "Sharp V-reversals and crashes ('momentum crashes').",
        "diy": "Stay long while the 6-month return is positive; step aside when it turns negative.",
        "horizon": "Medium/long term",
    },
    "donchian": {
        "school": "Breakout trend-following (the Turtles)",
        "champions": "Richard Donchian; Richard Dennis & the Turtle Traders",
        "idea": "Buy a fresh N-day high (a breakout), exit on an M-day low; ride the move that follows.",
        "works_when": "Clean breakouts that keep running.",
        "fails_when": "Choppy ranges that fake breakouts then reverse (whipsaw).",
        "diy": "Buy when price closes above its 20-day high; sell when it closes below its 10-day low.",
        "horizon": "Medium term",
    },
    "sma200_filter": {
        "school": "Trend-filtered holding / tactical risk management",
        "champions": "Meb Faber ('A Quantitative Approach to Tactical Asset Allocation')",
        "idea": "Hold while price is above its 200-day average; move to cash when below - similar upside, far smaller drawdowns.",
        "works_when": "Avoiding the worst of bear markets (cuts drawdown).",
        "fails_when": "Whipsaws near the average; lags sharp V-recoveries.",
        "diy": "Stay invested while price > 200-day MA; sell to cash when it breaks below; buy back when it reclaims.",
        "horizon": "Long term (risk-managed)",
    },
})
