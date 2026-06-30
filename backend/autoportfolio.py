"""
Autopilot sandbox — two paper portfolios driven by the Conviction Engine.
  - "core"     : ~40 liquid megacaps + whatever Congress recently bought
  - "congress" : ONLY tickers Congress recently bought (tests the thesis in isolation)
Equal-weight top-N by conviction, weekly rebalance + daily NAV mark vs SPY.
Honest: real prices, fees on every trade, no lookahead, long-only. PAPER ONLY —
this never places a real order; it moves virtual cash in a local SQLite book.
"""
import os, time, sqlite3, json
import datasource, conviction

DB = os.path.expanduser("~/.tradebot/portfolio.db")
START_CASH = 100000.0
N = 10                 # target positions
FEE_BPS = 0.0005       # 5 bps per trade = commission + slippage
MIN_CONV = 45.0        # exclude only genuine "Avoid" (<45); take the best N of the rest
REBAL_DAYS = 7         # weekly

MEGACAP_CORE = [
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","AVGO","ORCL","CRM","AMD","ADBE","CSCO",
    "TSLA","HD","MCD","NKE","SBUX","COST","WMT","PG","KO","PEP",
    "UNH","JNJ","LLY","ABBV","MRK","PFE",
    "JPM","BAC","V","MA","GS",
    "CAT","BA","XOM","CVX","GE","NFLX","DIS",
]


def _price(ticker):
    try:
        b = datasource.get_bars(ticker, "1d", 2)
        return float(b[-1]["c"]) if b else None
    except Exception:
        return None


_SCORE_CACHE = {}  # ticker -> (ts, conviction|None); shared across books within a tick


def score_one(t):
    import time
    e = _SCORE_CACHE.get(t)
    if e and (time.time() - e[0]) < 2 * 3600:
        return e[1]
    val = None
    for attempt in range(2):
        try:
            r = conviction.conviction_score(t)
            if not r.get("error") and r.get("conviction") is not None:
                val = r["conviction"]
                break
        except Exception:
            pass
        time.sleep(3)  # back off (yfinance throttles at volume)
    _SCORE_CACHE[t] = (time.time(), val)
    return val


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")      # concurrent reader (app) + writer (cron)
    c.execute("PRAGMA busy_timeout=30000")    # wait, don't error, on a brief lock
    c.execute("""CREATE TABLE IF NOT EXISTS books(
        name TEXT PRIMARY KEY, cash REAL, created REAL, universe TEXT,
        last_rebal REAL, spy0 REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS positions(
        book TEXT, ticker TEXT, shares REAL, avg_cost REAL, PRIMARY KEY(book,ticker))""")
    c.execute("""CREATE TABLE IF NOT EXISTS nav_history(
        book TEXT, ts REAL, nav REAL, cash REAL, invested REAL, spy REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS trades(
        id INTEGER PRIMARY KEY AUTOINCREMENT, book TEXT, ts REAL, ticker TEXT,
        side TEXT, shares REAL, price REAL, fee REAL, reason TEXT)""")
    return c


def ensure_books():
    c = _db()
    for name, uni in (("core", "megacap+congress"), ("congress", "congress-only")):
        if not c.execute("SELECT 1 FROM books WHERE name=?", (name,)).fetchone():
            c.execute("INSERT INTO books(name,cash,created,universe,last_rebal,spy0) VALUES(?,?,?,?,?,?)",
                      (name, START_CASH, time.time(), uni, None, None))
    c.commit()


def _congress_buys():
    tally = conviction.congress_tally()
    return [t for t, e in tally.items() if e.get("buys", 0) > e.get("sells", 0)]


def universe_for(book):
    cb = _congress_buys()
    if book == "congress":
        return sorted(set(cb))
    return sorted(set(MEGACAP_CORE) | set(cb))


def _positions(c, book):
    return {t: {"shares": s, "avg_cost": a}
            for t, s, a in c.execute("SELECT ticker,shares,avg_cost FROM positions WHERE book=? AND shares>0", (book,))}


def _nav(c, book, prices=None):
    row = c.execute("SELECT cash FROM books WHERE name=?", (book,)).fetchone()
    cash = row[0] if row else 0.0
    invested = 0.0
    for t, p in _positions(c, book).items():
        px = (prices or {}).get(t) or _price(t)
        if px:
            invested += p["shares"] * px
    return cash + invested, cash, invested


def mark(book):
    c = _db()
    nav, cash, invested = _nav(c, book)
    spy = _price("SPY")
    row = c.execute("SELECT spy0 FROM books WHERE name=?", (book,)).fetchone()
    if row and row[0] is None and spy:
        c.execute("UPDATE books SET spy0=? WHERE name=?", (spy, book))
    c.execute("INSERT INTO nav_history(book,ts,nav,cash,invested,spy) VALUES(?,?,?,?,?,?)",
              (book, time.time(), nav, cash, invested, spy))
    c.commit()
    return {"book": book, "nav": round(nav, 2), "cash": round(cash, 2), "invested": round(invested, 2)}


def rebalance(book):
    c = _db()
    import time
    uni = universe_for(book)
    scores = {}
    for t in uni:
        s = score_one(t)
        if s is not None and _price(t):
            scores[t] = s
        time.sleep(0.4)  # be polite to yfinance
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    targets = [t for t, s in ranked if s >= MIN_CONV][:N]

    nav, cash, _ = _nav(c, book)
    pos = _positions(c, book)
    slot = (nav * (1 - FEE_BPS * 1.2)) / N  # reserve a fee buffer so cash stays >= 0
    now = time.time()

    def trade(tk, side, shares, price, reason):
        nonlocal cash
        notional = shares * price
        fee = notional * FEE_BPS
        if side == "BUY":
            cash -= notional + fee
        else:
            cash += notional - fee
        c.execute("INSERT INTO trades(book,ts,ticker,side,shares,price,fee,reason) VALUES(?,?,?,?,?,?,?,?)",
                  (book, now, tk, side, round(shares, 4), round(price, 2), round(fee, 2), reason))

    # 1) sell anything not in targets
    for tk, p in list(pos.items()):
        if tk not in targets:
            px = _price(tk)
            if px and p["shares"] > 0:
                trade(tk, "SELL", p["shares"], px, "dropped from targets")
                c.execute("UPDATE positions SET shares=0 WHERE book=? AND ticker=?", (book, tk))
    # 2) move each target toward its slot
    for tk in targets:
        px = _price(tk)
        if not px:
            continue
        cur = pos.get(tk, {}).get("shares", 0.0)
        want = slot / px
        diff = want - cur
        if abs(diff * px) < 25:   # ignore dust moves
            continue
        if diff > 0:
            trade(tk, "BUY", diff, px, f"conviction {scores.get(tk):.0f}")
        else:
            trade(tk, "SELL", -diff, px, f"trim to slot (conv {scores.get(tk):.0f})")
        # upsert position to target shares at this price as avg cost
        c.execute("INSERT INTO positions(book,ticker,shares,avg_cost) VALUES(?,?,?,?) "
                  "ON CONFLICT(book,ticker) DO UPDATE SET shares=excluded.shares, avg_cost=excluded.avg_cost",
                  (book, tk, want, px))

    c.execute("UPDATE books SET cash=?, last_rebal=? WHERE name=?", (cash, now, book))
    c.commit()
    mark(book)
    return {"book": book, "rebalanced": True, "targets": targets,
            "scored": len(scores), "universe": len(uni)}


def tick():
    """Daily: mark every book; rebalance any book due (>=7d since last / never).
    Warms a shared score cache over the union of due books so we score each
    unique ticker once (congress is a subset of core) and stay under yfinance limits."""
    import time
    ensure_books()
    c = _db()
    names = [n for (n,) in c.execute("SELECT name FROM books").fetchall()]
    def _due(n):
        lr = c.execute("SELECT last_rebal FROM books WHERE name=?", (n,)).fetchone()[0]
        return lr is None or (time.time() - lr) >= REBAL_DAYS * 86400
    due = [n for n in names if _due(n)]
    if due:
        union = set()
        for n in due:
            union |= set(universe_for(n))
        for t in sorted(union):
            score_one(t)
            time.sleep(0.4)
    out = {"marked": [], "rebalanced": []}
    for n in names:
        if n in due:
            out["rebalanced"].append(rebalance(n))
        else:
            out["marked"].append(mark(n))
    return out


def status(book):
    c = _db()
    b = c.execute("SELECT cash,created,universe,last_rebal,spy0 FROM books WHERE name=?", (book,)).fetchone()
    if not b:
        return {"error": f"no book '{book}'"}
    cash, created, uni, last_rebal, spy0 = b
    holds = []
    invested = 0.0
    for t, p in _positions(c, book).items():
        px = _price(t) or 0
        val = p["shares"] * px
        invested += val
        holds.append({"ticker": t, "shares": round(p["shares"], 3), "price": round(px, 2),
                      "value": round(val, 2), "cost": round(p["avg_cost"], 2),
                      "unreal_pct": round((px / p["avg_cost"] - 1) * 100, 1) if p["avg_cost"] else 0})
    holds.sort(key=lambda h: h["value"], reverse=True)
    for h in holds:
        h["weight_pct"] = round(100 * h["value"] / (cash + invested), 1) if (cash + invested) else 0
    nav = cash + invested
    ret = (nav / START_CASH - 1) * 100
    spy_now = _price("SPY")
    bench = (spy_now / spy0 - 1) * 100 if (spy0 and spy_now) else None
    hist = [{"ts": ts, "nav": round(n, 2), "spy": s}
            for ts, n, s in c.execute("SELECT ts,nav,spy FROM nav_history WHERE book=? ORDER BY ts", (book,))]
    trades = [dict(zip(["ts", "ticker", "side", "shares", "price", "reason"], row))
              for row in c.execute("SELECT ts,ticker,side,shares,price,reason FROM trades WHERE book=? ORDER BY ts DESC LIMIT 25", (book,))]
    return {"book": book, "universe": uni, "created": created, "last_rebal": last_rebal,
            "nav": round(nav, 2), "cash": round(cash, 2), "invested": round(invested, 2),
            "return_pct": round(ret, 2), "spy_return_pct": (round(bench, 2) if bench is not None else None),
            "alpha_pct": (round(ret - bench, 2) if bench is not None else None),
            "positions": holds, "nav_history": hist, "trades": trades,
            "note": "Paper sandbox — virtual money, real prices, fees on every trade. Not advice."}


def summary():
    ensure_books()
    return {"books": [status("core"), status("congress")]}
