"""Forward paper-trading ledger: track (product, strategy) books and step them on
new daily bars, building a real equity-curve track record over time. End-of-day
marks; fills at the latest close when the signal flips. Idempotent per bar date."""
import json

from database import get_conn, init_db
from datasource import get_bars
from strategies import REGISTRY

FEE = 0.001   # 10 bps per side


def track(product, strategy="sma_cross", notional=10000.0, **params):
    init_db()
    if strategy != "model" and strategy not in REGISTRY:
        return {"error": f"unknown strategy '{strategy}'"}
    c = get_conn(); cur = c.cursor()
    cur.execute("SELECT book_id FROM paper_book WHERE product=? AND strategy=? AND active=1", (product, strategy))
    if cur.fetchone():
        c.close(); return {"status": "already tracking", "product": product, "strategy": strategy}
    cur.execute("INSERT INTO paper_book(product,strategy,params,cash,notional) VALUES(?,?,?,?,?)",
                (product, strategy, json.dumps(params), notional, notional))
    c.commit(); bid = cur.lastrowid; c.close()
    return {"status": "tracking", "book_id": bid, "product": product, "strategy": strategy, "notional": notional}


def step():
    init_db()
    c = get_conn(); cur = c.cursor()
    cur.execute("SELECT book_id,product,strategy,params,position,units,cash,last_date FROM paper_book WHERE active=1")
    books = cur.fetchall(); stepped = []
    for bid, product, strategy, params, position, units, cash, last_date in books:
        try:
            bars = get_bars(product, "1d", 260)
        except Exception as e:
            stepped.append({"book_id": bid, "error": str(e)}); continue
        if len(bars) < 5:
            continue
        date = str(bars[-1]["t"]); price = bars[-1]["c"]
        if last_date == date:
            stepped.append({"book_id": bid, "product": product, "skipped": "already up to date"}); continue
        closes = [b["c"] for b in bars]
        if strategy == "model":
            import model as _model
            pos = _model._positions(closes, _model.load())
        else:
            pos = REGISTRY[strategy](closes, **json.loads(params or "{}"))
        desired = pos[-1]
        if desired != position:
            if position == 0 and desired == 1:
                units = cash / price * (1 - FEE); cash = 0.0
            elif position == 1 and desired == 0:
                cash = units * price * (1 - FEE); units = 0.0
            position = desired
        equity = cash + units * price
        cur.execute("INSERT INTO paper_marks(book_id,date,price,position,equity) VALUES(?,?,?,?,?)",
                    (bid, date, price, position, equity))
        cur.execute("UPDATE paper_book SET position=?,units=?,cash=?,last_date=? WHERE book_id=?",
                    (position, units, cash, date, bid))
        stepped.append({"book_id": bid, "product": product, "strategy": strategy, "date": date,
                        "position": "long" if position else "cash", "equity": round(equity, 2)})
    c.commit(); c.close()
    return {"stepped": len(stepped), "books": stepped}


def portfolio():
    init_db()
    c = get_conn(); cur = c.cursor()
    cur.execute("SELECT book_id,product,strategy,position,notional FROM paper_book WHERE active=1")
    out, total_eq, total_notional = [], 0.0, 0.0
    for bid, product, strategy, position, notional in cur.fetchall():
        cur.execute("SELECT equity FROM paper_marks WHERE book_id=? ORDER BY mark_id DESC LIMIT 1", (bid,))
        row = cur.fetchone(); equity = row[0] if row else notional
        cur.execute("SELECT COUNT(*) FROM paper_marks WHERE book_id=?", (bid,))
        nmarks = cur.fetchone()[0]
        out.append({"book_id": bid, "product": product, "strategy": strategy,
                    "position": "long" if position else "cash", "equity": round(equity, 2),
                    "return_pct": round((equity / notional - 1) * 100, 2) if notional else 0.0,
                    "days_tracked": nmarks})
        total_eq += equity; total_notional += notional
    c.close()
    out.sort(key=lambda x: -x["return_pct"])
    return {"count": len(out), "books": out, "total_equity": round(total_eq, 2),
            "total_return_pct": round((total_eq / total_notional - 1) * 100, 2) if total_notional else 0.0}


def untrack(book_id):
    init_db()
    c = get_conn(); cur = c.cursor()
    cur.execute("UPDATE paper_book SET active=0 WHERE book_id=?", (book_id,))
    c.commit(); c.close()
    return {"status": "untracked", "book_id": book_id}
