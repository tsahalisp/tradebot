"""The user's OWN trading model, assembled piece-by-piece as they complete
lessons. Persisted to ~/.tradebot/model.json. Composes an ENTRY signal with an
optional 200-day TREND FILTER over a chosen UNIVERSE, then backtests/validates
the assembled model on real prices - so learning literally builds a strategy."""
import json
import pathlib

from datasource import get_bars, get_closes_many
from backtest import evaluate_pos
from strategies import REGISTRY, _sma

MODEL_PATH = pathlib.Path.home() / ".tradebot" / "model.json"
DEFAULT = {"horizon": "long-term", "entry": "buy_hold", "params": {}, "trend_filter": False,
           "universe": ["SPY"], "risk_pct": 2.0, "notes": [], "broker": "robinhood"}


def load():
    try:
        return {**DEFAULT, **json.loads(MODEL_PATH.read_text())}
    except Exception:
        return dict(DEFAULT)


def save(d):
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_text(json.dumps(d, indent=2))


def update(**patch):
    d = load()
    for k, v in patch.items():
        if k == "notes" and isinstance(v, str):
            notes = d.get("notes", [])
            if v not in notes:
                notes.append(v)
            d["notes"] = notes
        elif v is not None:
            d[k] = v
    save(d)
    return d


def reset():
    save(dict(DEFAULT))
    return load()


def _positions(closes, m):
    entry = m.get("entry", "buy_hold")
    if entry not in REGISTRY:
        entry = "buy_hold"
    pos = REGISTRY[entry](closes, **(m.get("params") or {}))
    if m.get("trend_filter"):
        s = _sma(closes, 200)
        pos = [p if (s[i] is not None and closes[i] > s[i]) else 0 for i, p in enumerate(pos)]
    return pos


def backtest():
    m = load(); out = []
    for t, closes in get_closes_many(m.get("universe", ["SPY"]), 400).items():
        if len(closes) < 60:
            continue
        mets, _ = evaluate_pos(closes, _positions(closes, m))
        out.append({"product": t, **mets})
    out.sort(key=lambda x: -x["alpha_vs_hold_pct"])
    avg_alpha = round(sum(x["alpha_vs_hold_pct"] for x in out) / len(out), 2) if out else 0.0
    return {"model": m, "results": out, "avg_alpha_vs_hold": avg_alpha}


def validate(split=0.7):
    m = load(); res = []
    for t, closes in get_closes_many(m.get("universe", ["SPY"]), 600).items():
        if len(closes) < 120:
            continue
        k = int(len(closes) * split)
        mi, _ = evaluate_pos(closes[:k], _positions(closes[:k], m))
        mo, _ = evaluate_pos(closes[k:], _positions(closes[k:], m))
        res.append({"product": t, "in_sample_alpha": mi["alpha_vs_hold_pct"],
                    "out_sample_alpha": mo["alpha_vs_hold_pct"]})
    if not res:
        return {"results": [], "verdict": "Not enough data to validate."}
    avg = round(sum(x["out_sample_alpha"] for x in res) / len(res), 2)
    if avg > 0.5:
        verdict = f"Your model HELD out-of-sample (avg OOS alpha +{avg}%). Promising - paper trade it next."
    elif avg > -0.5:
        verdict = f"Your model's edge vanished out-of-sample (avg OOS alpha {avg}%) - likely overfit or no real edge yet."
    else:
        verdict = f"Negative out-of-sample (avg {avg}%) - this model hurt on unseen data. Rethink the rules."
    return {"results": res, "avg_oos_alpha": avg, "verdict": verdict}


def apply_lesson(lesson_id):
    """Apply the model block a given lesson teaches (BUILDS map)."""
    import curriculum
    b = curriculum.BUILDS.get(lesson_id)
    return update(**b[0]) if b else load()


def signals():
    """The advisor view: for EACH stock in the universe, what is the model saying RIGHT NOW?"""
    m = load(); out = []
    for t, closes in get_closes_many(m.get("universe", ["SPY"]), 400).items():
        if len(closes) < 60:
            continue
        pos = _positions(closes, m)
        ref = closes[-21] if len(closes) > 21 else closes[0]
        out.append({"product": t, "stance": "LONG" if pos[-1] == 1 else "CASH",
                    "price": round(closes[-1], 2), "chg_1mo_pct": round((closes[-1] / ref - 1) * 100, 2)})
    out.sort(key=lambda x: (x["stance"] != "LONG", -x["chg_1mo_pct"]))
    longs = sum(1 for x in out if x["stance"] == "LONG")
    return {"count": len(out), "long": longs, "cash": len(out) - longs, "signals": out}


def add_ticker(t):
    d = load(); u = list(d.get("universe", []))
    if t not in u:
        u.append(t)
    return update(universe=u)


def remove_ticker(t):
    return update(universe=[x for x in load().get("universe", []) if x != t])


def graduate(notional=10000.0):
    """Start forward paper-trading the EXACT assembled model across its universe."""
    import paper
    m = load(); created = []
    for t in m.get("universe", ["SPY"]):
        created.append(paper.track(t, "model", notional))
    paper.step()
    return {"graduated": sum(1 for c in created if c.get("status") == "tracking"),
            "already": sum(1 for c in created if c.get("status") == "already tracking"),
            "universe": m.get("universe", [])}
