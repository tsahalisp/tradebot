"""
Conviction Engine — combine every research signal into one score, then prove it.
Equal-weight composite v1 (spine-agnostic): scorecard + DCF + earnings + news +
insider + a Congress smart-money overlay. Every pick is logged at entry and
settled later so ATTRIBUTION shows which signal actually predicted returns.
All inputs are public + lagged -> prior is "no edge until proven". Not advice.
"""
import os, json, time, sqlite3, statistics
import screener

DB = os.path.expanduser("~/.tradebot/conviction.db")
WEIGHTS = {"scorecard": 1, "dcf": 1, "earnings": 1, "news": 1, "insider": 1, "congress": 1}


# ---------- per-signal scorers (each -> 0..100, 50 = neutral) ----------
def _grade_num(g):
    g = (g or "").upper()
    if any(w in g for w in ("STRONG", "CHEAP", "UNDERVAL", "UPTREND", "FAST")):
        return 85
    if any(w in g for w in ("WEAK", "SLOW", "RICH", "EXPENSIVE", "OVERVAL", "DOWNTREND", "POOR")):
        return 25
    return 55


def _scorecard_score(sc):
    if not sc:
        return None, None
    vals = [_grade_num(sc[k].get("grade")) for k in ("growth", "value", "quality", "trend")
            if isinstance(sc.get(k), dict) and sc[k].get("grade")]
    if not vals:
        return None, None
    return sum(vals) / len(vals), {k: sc.get(k, {}).get("grade") for k in sc}


def _dcf_score(ti):
    fcf, sh, price = ti.get("fcf"), ti.get("shares"), ti.get("price")
    cash, debt = ti.get("cash") or 0, ti.get("debt") or 0
    g = (ti.get("suggested_growth_pct") or 10) / 100.0
    if not (fcf and sh and price) or fcf <= 0:
        return None, None
    g = max(min(g, 0.30), 0.0)
    disc, term, npv, f = 0.10, 0.025, 0.0, fcf
    for t in range(1, 6):
        f *= (1 + g)
        npv += f / ((1 + disc) ** t)
    npv += ((f * (1 + term)) / (disc - term)) / ((1 + disc) ** 5)
    fair = (npv + cash - debt) / sh
    up = (fair / price - 1) * 100
    return max(0, min(100, 50 + up)), {"fair_value": round(fair, 2), "upside_pct": round(up, 1)}


def _earn_score(e):
    tot = e.get("total") or 0
    if not tot:
        return None, None
    br = (e.get("beats") or 0) / tot
    surp = e.get("avg_surprise_pct") or 0
    s = 0.6 * (br * 100) + 0.4 * max(0, min(100, 50 + surp * 2))
    return max(0, min(100, s)), {"lean": e.get("lean"), "beats": e.get("beats"),
                                 "total": tot, "avg_surprise_pct": surp}


def _news_score(nw):
    o = (nw.get("overall") or "").upper()
    for k, v in {"BULLISH": 75, "NEUTRAL": 50, "BEARISH": 25}.items():
        if k in o:
            return v, {"overall": nw.get("overall")}
    return None, None


def _insider_score(ins):
    s = ins.get("summary") or {}
    b, se = s.get("buys", 0), s.get("sells", 0)
    if b + se == 0:
        return 50, {"buys": b, "sells": se}
    ratio = (b - se) / (b + se)
    return max(0, min(100, 50 + ratio * 40)), {"buys": b, "sells": se, "net_value": s.get("net_value")}


# ---------- Congress smart-money overlay (parse recent filings, tally by ticker) ----------
_CTALLY = {"data": None, "ts": 0.0}


def congress_tally(n=35):
    if _CTALLY["data"] is not None and (time.time() - _CTALLY["ts"]) < 6 * 3600:
        return _CTALLY["data"]
    tally = {}
    try:
        for f in screener.congress_trades(limit=n).get("filings", []):
            det = screener.parse_filing(f.get("doc_id"))
            for t in det.get("transactions", []):
                tk = (t.get("ticker") or "").upper()
                if not tk:
                    continue
                e = tally.setdefault(tk, {"buys": 0, "sells": 0, "members": set()})
                typ = t.get("type") or ""
                if "Purchase" in typ:
                    e["buys"] += 1
                elif "Sale" in typ:
                    e["sells"] += 1
                e["members"].add(f.get("name"))
    except Exception:
        pass
    for e in tally.values():
        e["members"] = sorted(x for x in e["members"] if x)
    _CTALLY["data"] = tally
    _CTALLY["ts"] = time.time()
    return tally


def _congress_score(ticker, tally):
    e = tally.get(ticker.upper())
    if not e:
        return 50, {"note": "no recent congressional activity"}
    net = e["buys"] - e["sells"]
    return max(0, min(100, 50 + net * 12)), {"buys": e["buys"], "sells": e["sells"],
                                             "members": e["members"][:5]}


# ---------- composite ----------
def conviction_score(ticker):
    ticker = ticker.upper()
    r = screener.research(ticker)
    if not isinstance(r, dict) or r.get("error"):
        return {"ticker": ticker, "error": (r.get("error") if isinstance(r, dict) else "research failed")}
    subs = {}
    subs["scorecard"] = _scorecard_score(r.get("scorecard"))
    try: subs["dcf"] = _dcf_score(screener.dcf_inputs(ticker))
    except Exception: subs["dcf"] = (None, None)
    try: subs["earnings"] = _earn_score(screener.earnings_lean(ticker))
    except Exception: subs["earnings"] = (None, None)
    try: subs["news"] = _news_score(screener.news(ticker))
    except Exception: subs["news"] = (None, None)
    try: subs["insider"] = _insider_score(screener.insider(ticker))
    except Exception: subs["insider"] = (None, None)
    try: subs["congress"] = _congress_score(ticker, congress_tally())
    except Exception: subs["congress"] = (None, None)
    used = {k: v for k, v in subs.items() if v[0] is not None}
    if not used:
        return {"ticker": ticker, "error": "no signals available"}
    wsum = sum(WEIGHTS[k] for k in used)
    comp = sum(WEIGHTS[k] * v[0] for k, v in used.items()) / wsum
    label = ("High conviction" if comp >= 70 else "Lean long" if comp >= 55
             else "Neutral" if comp >= 45 else "Avoid")
    return {
        "ticker": ticker, "price": r.get("price"), "name": r.get("name"),
        "conviction": round(comp, 1), "label": label,
        "signals_used": list(used.keys()),
        "breakdown": {k: {"score": (None if v[0] is None else round(v[0], 1)), "detail": v[1]}
                      for k, v in subs.items()},
        "disclaimer": "Equal-weight blend of public, lagged signals. Research tool, not advice. "
                      "Validate on paper before trusting — see /conviction/attribution.",
    }


# ---------- validation harness ----------
def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS picks(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, ticker TEXT, conviction REAL,
        price_entry REAL, breakdown TEXT, settled INTEGER DEFAULT 0,
        price_exit REAL, fwd_return_pct REAL, settled_ts REAL)""")
    return c


def log_pick(ticker):
    r = conviction_score(ticker)
    if r.get("error"):
        return r
    c = _db()
    cur = c.execute("INSERT INTO picks(ts,ticker,conviction,price_entry,breakdown) VALUES(?,?,?,?,?)",
                    (time.time(), r["ticker"], r["conviction"], r.get("price"), json.dumps(r["breakdown"])))
    c.commit()
    r["logged_id"] = cur.lastrowid
    return r


def ledger(limit=100):
    c = _db()
    cols = ["id", "ts", "ticker", "conviction", "price_entry", "settled", "price_exit", "fwd_return_pct"]
    rows = c.execute(f"SELECT {','.join(cols)} FROM picks ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    picks = [dict(zip(cols, row)) for row in rows]
    open_n = sum(1 for p in picks if not p["settled"])
    return {"count": len(picks), "open": open_n, "settled": len(picks) - open_n, "picks": picks}


def settle(min_days=30):
    c = _db()
    now = time.time()
    rows = c.execute("SELECT id,ticker,price_entry,ts FROM picks WHERE settled=0").fetchall()
    done = 0
    for pid, tk, pe, ts in rows:
        if now - ts < min_days * 86400 or not pe:
            continue
        r = screener.research(tk)
        px = r.get("price") if isinstance(r, dict) else None
        if not px:
            continue
        ret = (px / pe - 1) * 100
        c.execute("UPDATE picks SET settled=1,price_exit=?,fwd_return_pct=?,settled_ts=? WHERE id=?",
                  (px, ret, now, pid))
        done += 1
    c.commit()
    return {"settled_now": done, **ledger()}


def attribution():
    c = _db()
    rows = c.execute("SELECT conviction,fwd_return_pct,breakdown FROM picks WHERE settled=1").fetchall()
    n = len(rows)
    if n < 8:
        return {"settled": n, "ready": False,
                "note": f"Need ~8+ settled picks to attribute signal edge (have {n}). Log picks, let 30d pass, then settle."}
    hi = [r for cv, r, _ in rows if cv >= 55]
    lo = [r for cv, r, _ in rows if cv < 55]
    sig = {}
    for cv, ret, bd in rows:
        for k, v in json.loads(bd).items():
            sc = v.get("score")
            if sc is None:
                continue
            d = sig.setdefault(k, {"hi": [], "lo": []})
            (d["hi"] if sc >= 60 else d["lo"]).append(ret)
    edge = {k: round(statistics.mean(d["hi"]) - statistics.mean(d["lo"]), 2)
            for k, d in sig.items() if d["hi"] and d["lo"]}
    edge = dict(sorted(edge.items(), key=lambda x: x[1], reverse=True))
    return {"settled": n, "ready": True,
            "high_conv_avg_return_pct": round(statistics.mean(hi), 2) if hi else None,
            "low_conv_avg_return_pct": round(statistics.mean(lo), 2) if lo else None,
            "signal_edge_pct": edge,
            "note": "signal_edge_pct = avg forward return when a signal scored high (>=60) minus when it "
                    "scored low. The signal with the biggest positive edge is the one to overweight — "
                    "let this pick the strategy's spine, don't guess it."}
