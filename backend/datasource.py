"""Pluggable market-data adapters — all return oldest->newest [{t,o,h,l,c,v}].
- Equities: yfinance (free, no key) — the default, since this is a STOCK bot.
- Crypto:   Coinbase public candles (free, no key, 24/7).
Routing: symbols ending in -USD => crypto; everything else => equity ticker.
"""
import json
import urllib.request

_COINBASE = "https://api.exchange.coinbase.com"
GRAN = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}


def _coinbase(product, granularity, limit):
    g = GRAN.get(granularity, 86400)
    url = f"{_COINBASE}/products/{product}/candles?granularity={g}"
    req = urllib.request.Request(url, headers={"User-Agent": "tradebot/0.1"})
    raw = sorted(json.load(urllib.request.urlopen(req, timeout=15)), key=lambda r: r[0])
    return [{"t": int(r[0]), "o": float(r[3]), "h": float(r[2]),
             "l": float(r[1]), "c": float(r[4]), "v": float(r[5])} for r in raw][-limit:]


def _equity(symbol, limit):
    import yfinance as yf
    period = "5y" if limit > 500 else "2y"
    df = yf.download(symbol, period=period, interval="1d", progress=False, auto_adjust=True)
    if df is None or len(df) == 0:
        return []
    df = df.dropna()
    if getattr(df.columns, "nlevels", 1) > 1:          # flatten ('Close','SPY') -> 'Close'
        df.columns = df.columns.get_level_values(0)
    out = []
    for ix, r in df.iterrows():
        try:
            out.append({"t": str(ix.date()), "o": float(r["Open"]), "h": float(r["High"]),
                        "l": float(r["Low"]), "c": float(r["Close"]), "v": float(r["Volume"])})
        except Exception:
            continue
    return out[-limit:]


def get_bars(symbol="SPY", granularity="1d", limit=300):
    if symbol.upper().endswith("-USD"):
        return _coinbase(symbol, granularity, limit)
    return _equity(symbol, limit)


def get_closes_many(symbols, limit=400):
    """{symbol: [closes]} efficiently — equities batched in ONE yfinance call, crypto per-symbol."""
    out = {}
    equities = [x for x in symbols if not x.upper().endswith("-USD")]
    cryptos = [x for x in symbols if x.upper().endswith("-USD")]
    if equities:
        import yfinance as yf
        period = "3y" if limit > 500 else "2y"
        df = yf.download(equities, period=period, interval="1d", progress=False,
                         auto_adjust=True, group_by="ticker")
        for sym in equities:
            try:
                sub = df[sym] if getattr(df.columns, "nlevels", 1) > 1 else df
                closes = [float(x) for x in sub["Close"].dropna().tolist()][-limit:]
                if len(closes) >= 60:
                    out[sym] = closes
            except Exception:
                pass
    for sym in cryptos:
        try:
            out[sym] = [b["c"] for b in _coinbase(sym, "1d", limit)]
        except Exception:
            pass
    return out
