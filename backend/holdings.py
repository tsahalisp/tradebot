"""Peter's real holdings + the advisor signal per position. Persisted to
~/.tradebot/holdings.json. Each holding gets a HOLD / TRIM / ADD / EXIT call by
combining the model's stance (in-trend or not) with its weight vs an equal-weight
target. Decision-support only - it never places an order."""
import csv as _csv
import io
import json
import pathlib

from datasource import get_closes_many

HOLD_PATH = pathlib.Path.home() / ".tradebot" / "holdings.json"


def load():
    try:
        return json.loads(HOLD_PATH.read_text())
    except Exception:
        return []


def save(h):
    HOLD_PATH.parent.mkdir(parents=True, exist_ok=True)
    HOLD_PATH.write_text(json.dumps(h, indent=2))


def upsert(ticker, shares, cost_basis=0.0):
    h = load()
    for x in h:
        if x["ticker"] == ticker:
            x["shares"] = float(shares); x["cost_basis"] = float(cost_basis)
            save(h); return {"status": "updated", "ticker": ticker}
    h.append({"ticker": ticker, "shares": float(shares), "cost_basis": float(cost_basis)})
    save(h); return {"status": "added", "ticker": ticker}


def remove(ticker):
    save([x for x in load() if x["ticker"] != ticker])
    return {"status": "removed", "ticker": ticker}


def portfolio():
    import model
    m = model.load()
    h = load()
    if not h:
        return {"holdings": [], "total_value": 0, "total_cost": 0, "total_pnl": 0, "total_pnl_pct": 0, "count": 0}
    data = get_closes_many([x["ticker"] for x in h], 400)
    # values first (for weights)
    priced = []
    total = 0.0
    for x in h:
        closes = data.get(x["ticker"])
        price = closes[-1] if closes else None
        val = price * x["shares"] if price else 0.0
        total += val
        priced.append((x, closes, price, val))
    n = len(priced); target = 1.0 / n if n else 0
    out = []
    for x, closes, price, val in priced:
        weight = val / total if total else 0
        stance = "LONG"
        if closes and len(closes) >= 60:
            pos = model._positions(closes, m)
            stance = "LONG" if pos[-1] == 1 else "CASH"
        if stance == "CASH":
            action, why = "EXIT", "model is out of the trend on this name"
        elif weight > target * 1.5:
            action, why = "TRIM", "overweight vs your equal-weight target"
        elif weight < target * 0.6:
            action, why = "ADD", "underweight vs your equal-weight target"
        else:
            action, why = "HOLD", "in-trend and near target weight"
        pnl = (price / x["cost_basis"] - 1) * 100 if (x.get("cost_basis") and price) else None
        out.append({"ticker": x["ticker"], "shares": x["shares"], "cost_basis": x.get("cost_basis"),
                    "price": round(price, 2) if price else None, "value": round(val, 2),
                    "weight_pct": round(weight * 100, 1), "pnl_pct": round(pnl, 1) if pnl is not None else None,
                    "stance": stance, "action": action, "why": why})
    rank = {"EXIT": 0, "TRIM": 1, "ADD": 2, "HOLD": 3}
    out.sort(key=lambda r: (rank.get(r["action"], 4), -r["value"]))
    total_cost = sum((x.get("cost_basis") or 0) * x["shares"] for x in h)
    return {"holdings": out, "total_value": round(total, 2), "total_cost": round(total_cost, 2),
            "total_pnl": round(total - total_cost, 2) if total_cost else 0,
            "total_pnl_pct": round((total / total_cost - 1) * 100, 2) if total_cost else 0,
            "count": len(out)}


def rebalance():
    """Suggest trades to bring the portfolio to equal-weight among IN-TREND names,
    and fully exit the out-of-trend ones. Suggestions only - you place them."""
    p = portfolio()
    hs = p["holdings"]
    if not hs:
        return {"trades": [], "note": "No holdings yet."}
    total = p["total_value"]
    longs = [h for h in hs if h["stance"] == "LONG"]
    n = len(longs); target_w = 1.0 / n if n else 0
    trades = []
    for h in hs:
        cur = h["value"]; price = h.get("price")
        tv = total * target_w if h["stance"] == "LONG" else 0.0
        delta = tv - cur
        if price and abs(delta) > max(25.0, total * 0.02):     # ignore tiny drift
            trades.append({"ticker": h["ticker"], "action": "BUY" if delta > 0 else "SELL",
                           "shares": round(abs(delta) / price, 2), "dollars": round(abs(delta), 2),
                           "from_weight": h["weight_pct"], "to_weight": round((tv / total * 100) if total else 0, 1)})
    trades.sort(key=lambda x: -x["dollars"])
    return {"trades": trades, "target_each_pct": round(target_w * 100, 1), "total_value": total,
            "note": "Equal-weight the in-trend names, exit the rest. Suggestions only - you place the trades."}


def import_csv(text):
    """Import positions from a broker CSV export. Heuristic column detection — no
    credentials, no OAuth: you export the CSV, we read Symbol / Quantity / Cost."""
    rows = list(_csv.reader(io.StringIO(text or "")))
    if len(rows) < 2:
        return {"imported": 0, "error": "empty or header-only CSV"}
    header = [c.strip().lower() for c in rows[0]]
    def col(*names):
        for n in names:
            for i, hc in enumerate(header):
                if n in hc:
                    return i
        return None
    si = col("symbol", "ticker")
    qi = col("quantity", "shares", "qty")
    ci_per = col("average cost", "avg cost", "average price", "cost per share", "price paid", "avg price")
    ci_tot = col("cost basis", "total cost", "cost")
    if si is None or qi is None:
        return {"imported": 0, "error": "need a Symbol and a Quantity column"}
    hold = load(); imported = 0
    for r in rows[1:]:
        try:
            if si >= len(r) or qi >= len(r):
                continue
            sym = r[si].strip().upper()
            if not sym or not sym.replace(".", "").isalpha():
                continue
            qty = float(str(r[qi]).replace(",", "").replace("$", "").strip())
            cost = 0.0
            if ci_per is not None and ci_per < len(r) and str(r[ci_per]).strip():
                cost = float(str(r[ci_per]).replace(",", "").replace("$", "").strip())
            elif ci_tot is not None and ci_tot < len(r) and str(r[ci_tot]).strip() and qty:
                cost = float(str(r[ci_tot]).replace(",", "").replace("$", "").strip()) / qty
            found = False
            for x in hold:
                if x["ticker"] == sym:
                    x["shares"] = qty; x["cost_basis"] = cost; found = True
            if not found:
                hold.append({"ticker": sym, "shares": qty, "cost_basis": cost})
            imported += 1
        except Exception:
            continue
    save(hold)
    return {"imported": imported, "total_holdings": len(hold)}
