"""Discovery: surface up-and-coming / high-growth candidates from Yahoo's predefined
screens, enriched with fundamentals (P/E, revenue & earnings growth, margins) and
ranked by 6-month momentum. Filterable by min revenue growth / max P/E. Ideas to
research with your model + lessons - NOT recommendations.

NOTE: this is a CURRENT-snapshot screen for SELECTION. True historical fundamental
backtesting needs point-in-time data (a paid feed) - we don't fake that here."""
from datasource import get_closes_many

SCREENS = ["growth_technology_stocks", "aggressive_small_caps", "small_cap_gainers",
           "undervalued_growth_stocks", "day_gainers", "most_actives"]


def _num(v):
    return v if isinstance(v, (int, float)) else None


def fundamentals(ticker):
    import yfinance as yf
    try:
        i = yf.Ticker(ticker).info
    except Exception as e:
        return {"ticker": ticker, "error": f"{type(e).__name__}: {e}"}
    def pct(k):
        v = i.get(k)
        return round(v * 100, 1) if isinstance(v, (int, float)) else None
    return {"ticker": ticker, "name": i.get("shortName") or i.get("longName"), "sector": i.get("sector"),
            "market_cap": i.get("marketCap"), "pe": _num(i.get("trailingPE")), "fwd_pe": _num(i.get("forwardPE")),
            "price_to_book": _num(i.get("priceToBook")), "rev_growth_pct": pct("revenueGrowth"),
            "earnings_growth_pct": pct("earningsGrowth"), "profit_margin_pct": pct("profitMargins"),
            "summary": (i.get("longBusinessSummary") or "")[:280]}


def discover(screen="growth_technology_stocks", limit=12, min_rev_growth=None, max_pe=None):
    import yfinance as yf
    if screen not in SCREENS:
        screen = "growth_technology_stocks"
    try:
        r = yf.screen(screen, count=min(25, limit + 8))
        quotes = [q for q in (r.get("quotes", []) if isinstance(r, dict) else []) if q.get("symbol")]
    except Exception as e:
        return {"screen": screen, "screens": SCREENS, "error": f"{type(e).__name__}: {e}", "candidates": []}
    data = get_closes_many([q["symbol"] for q in quotes], 200)
    cands = []
    for q in quotes:
        s = q["symbol"]; closes = data.get(s)
        mom = round((closes[-1] / closes[-126] - 1) * 100, 1) if (closes and len(closes) >= 126) else None
        pe = _num(q.get("trailingPE")); epsT = _num(q.get("epsTrailingTwelveMonths")); epsC = _num(q.get("epsCurrentYear"))
        eps_g = round((epsC / epsT - 1) * 100, 1) if (epsT and epsC and epsT > 0) else None
        rev_g, margin, sector = None, None, None
        try:
            info = yf.Ticker(s).info
            rev_g = round(info.get("revenueGrowth") * 100, 1) if isinstance(info.get("revenueGrowth"), (int, float)) else None
            margin = round(info.get("profitMargins") * 100, 1) if isinstance(info.get("profitMargins"), (int, float)) else None
            sector = info.get("sector")
        except Exception:
            pass
        if min_rev_growth is not None and (rev_g is None or rev_g < min_rev_growth):
            continue
        if max_pe is not None and (pe is None or pe > max_pe):
            continue
        cands.append({"symbol": s, "name": (q.get("shortName") or q.get("longName") or "")[:30],
                      "market_cap": q.get("marketCap"), "change_pct": round(q.get("regularMarketChangePercent") or 0, 2),
                      "momentum_6mo_pct": mom, "pe": round(pe, 1) if pe else None, "eps_growth_pct": eps_g,
                      "rev_growth_pct": rev_g, "profit_margin_pct": margin, "sector": sector})
        if len(cands) >= limit:
            break
    cands.sort(key=lambda x: (x["momentum_6mo_pct"] is None, -(x["momentum_6mo_pct"] or -999)))
    return {"screen": screen, "screens": SCREENS, "count": len(cands), "candidates": cands}


def _grade_growth(g):
    if g is None: return {"grade": "N/A", "note": "No revenue-growth data."}
    if g >= 15: return {"grade": "STRONG", "note": f"Revenue growing {g}% - genuinely fast."}
    if g >= 0: return {"grade": "OK", "note": f"Revenue growing {g}% - modest."}
    return {"grade": "WEAK", "note": f"Revenue shrinking ({g}%) - a red flag."}


def _grade_value(pe):
    if pe is None: return {"grade": "N/A", "note": "No P/E - may be unprofitable or estimate missing."}
    if pe < 20: return {"grade": "CHEAP-ISH", "note": f"P/E {round(pe, 1)} - not demanding; little priced in."}
    if pe <= 40: return {"grade": "MODERATE", "note": f"P/E {round(pe, 1)} - fair-to-rich; needs growth to justify."}
    return {"grade": "PRICEY", "note": f"P/E {round(pe, 1)} - priced for big growth; little margin of safety."}


def _grade_quality(m):
    if m is None: return {"grade": "N/A", "note": "No margin data."}
    if m >= 20: return {"grade": "STRONG", "note": f"Profit margin {m}% - highly profitable, a sign of a moat."}
    if m >= 5: return {"grade": "OK", "note": f"Profit margin {m}% - decently profitable."}
    return {"grade": "THIN", "note": f"Profit margin {m}% - low or negative profitability."}


def research(ticker):
    """A learnable research scorecard for ANY stock: fundamentals + trend + a
    plain-English read on growth, value, quality, and trend. Research aid, not advice."""
    f = fundamentals(ticker)
    if "error" in f:
        return f
    closes = get_closes_many([ticker], 300).get(ticker, [])
    price = round(closes[-1], 2) if closes else None
    above = closes[-1] > (sum(closes[-200:]) / 200) if len(closes) >= 200 else None
    mom = round((closes[-1] / closes[-126] - 1) * 100, 1) if len(closes) >= 126 else None
    if above is True:
        trend = {"grade": "UPTREND", "note": "Above its 200-day average - long-term trend is up."}
    elif above is False:
        trend = {"grade": "DOWNTREND", "note": "Below its 200-day average - caution; don't catch a falling knife."}
    else:
        trend = {"grade": "N/A", "note": "Not enough price history."}
    f.update({"price": price, "momentum_6mo_pct": mom, "above_200d": above,
              "scorecard": {"growth": _grade_growth(f.get("rev_growth_pct")),
                            "value": _grade_value(f.get("pe")),
                            "quality": _grade_quality(f.get("profit_margin_pct")),
                            "trend": trend}})
    return f


def thesis(ticker):
    """Grounded AI bull/bear case for a ticker — Haiku reasons over the REAL
    research numbers only (no invented facts/news/prices). Research aid, not advice."""
    import json, re
    import llm
    f = research(ticker)
    if "error" in f:
        return f
    sc = f.get("scorecard", {})
    facts = (
        f"Ticker: {f['ticker']} ({f.get('name')}), sector {f.get('sector')}. "
        f"Price ${f.get('price')}. P/E {f.get('pe')}, forward P/E {f.get('fwd_pe')}, "
        f"price/book {f.get('price_to_book')}. Revenue growth {f.get('rev_growth_pct')}%, "
        f"earnings growth {f.get('earnings_growth_pct')}%, profit margin {f.get('profit_margin_pct')}%. "
        f"6-month momentum {f.get('momentum_6mo_pct')}%, "
        f"{'above' if f.get('above_200d') else 'below'} its 200-day average. "
        f"Scorecard grades — growth {sc.get('growth',{}).get('grade')}, "
        f"value {sc.get('value',{}).get('grade')}, quality {sc.get('quality',{}).get('grade')}, "
        f"trend {sc.get('trend',{}).get('grade')}."
    )
    system = (
        "You are a sober equity-research teacher for a student investor. Using ONLY the "
        "numbers you are given, write a BULL case and a BEAR case. 2-3 short bullets each, "
        "and every bullet must cite a specific number from the data. Invent nothing — no "
        "news, events, products, guidance, or prices that aren't in the data. Give no "
        "buy/sell recommendation. Output STRICT JSON only: "
        '{"bull":["..."],"bear":["..."]}'
    )
    try:
        txt = llm.haiku(system, facts, 500)
        m = re.search(r"\{.*\}", txt, re.DOTALL)
        data = json.loads(m.group(0)) if m else {"bull": [], "bear": []}
    except Exception as e:
        return {"ticker": ticker, "error": f"thesis failed: {type(e).__name__}: {e}"}
    return {"ticker": f["ticker"], "name": f.get("name"),
            "bull": [str(x) for x in (data.get("bull") or [])][:4],
            "bear": [str(x) for x in (data.get("bear") or [])][:4]}


def news(ticker):
    """Recent headlines + a grounded AI sentiment read (yfinance .news, free).
    Sentiment is judged ONLY from the headlines shown — invents nothing."""
    import json, re
    import yfinance as yf
    import llm
    try:
        raw = yf.Ticker(ticker).news or []
    except Exception as e:
        return {"ticker": ticker, "error": f"news fetch failed: {type(e).__name__}"}
    items = []
    for h in raw[:6]:
        c = h.get("content", h) if isinstance(h, dict) else {}
        title = c.get("title")
        if not title:
            continue
        prov = c.get("provider")
        prov = prov.get("displayName") if isinstance(prov, dict) else prov
        items.append({"title": str(title)[:160], "provider": prov,
                      "summary": str(c.get("summary") or c.get("description") or "")[:300]})
    if not items:
        return {"ticker": ticker.upper(), "items": [], "overall": None, "takeaway": "No recent headlines found."}
    facts = "\n".join(f"- {it['title']}" + (f" — {it['summary'][:140]}" if it["summary"] else "") for it in items)
    system = (
        "You read recent stock headlines for a student investor. Based ONLY on these headlines, "
        "give the overall near-term sentiment (exactly one of BULLISH, NEUTRAL, BEARISH) and one "
        "plain-English sentence on what they collectively suggest. Invent nothing beyond the "
        'headlines. Output STRICT JSON only: {"overall":"BULLISH|NEUTRAL|BEARISH","takeaway":"..."}'
    )
    overall, takeaway = None, None
    try:
        txt = llm.haiku(system, facts, 200)
        m = re.search(r"\{.*\}", txt, re.DOTALL)
        d = json.loads(m.group(0)) if m else {}
        overall = d.get("overall")
        takeaway = d.get("takeaway")
    except Exception:
        pass
    return {"ticker": ticker.upper(), "items": items, "overall": overall, "takeaway": takeaway}


def insider(ticker, limit=12):
    """Recent insider transactions (yfinance, free). Classifies BUY/SELL (gifts,
    grants and awards are neutral) and gives a net-dollar summary. Not advice."""
    import yfinance as yf
    try:
        df = yf.Ticker(ticker).insider_transactions
    except Exception as e:
        return {"ticker": ticker.upper(), "error": f"insider fetch failed: {type(e).__name__}"}
    if df is None or len(df) == 0:
        return {"ticker": ticker.upper(), "transactions": [], "summary": {"buys": 0, "sells": 0, "net_value": 0}}

    def direction(t):
        t = (t or "").lower()
        if "sale" in t or "sell" in t:
            return "SELL"
        if "purchase" in t or "buy" in t or "acquisition" in t:
            return "BUY"
        return "—"

    txns, buys, sells, buy_val, sell_val = [], 0, 0, 0.0, 0.0
    for _, r in df.head(40).iterrows():
        d = direction(str(r.get("Text") or r.get("Transaction") or ""))
        try:
            val = float(r.get("Value") or 0)
        except Exception:
            val = 0.0
        try:
            sh = int(r.get("Shares") or 0)
        except Exception:
            sh = 0
        if d == "BUY":
            buys += 1; buy_val += val
        elif d == "SELL":
            sells += 1; sell_val += val
        if len(txns) < limit:
            txns.append({"insider": str(r.get("Insider") or "")[:28],
                         "position": str(r.get("Position") or "")[:24],
                         "dir": d, "shares": sh, "value": round(val),
                         "date": str(r.get("Start Date"))[:10]})
    return {"ticker": ticker.upper(),
            "summary": {"buys": buys, "sells": sells, "net_value": round(buy_val - sell_val)},
            "transactions": txns}


def dcf_inputs(ticker):
    """Raw inputs for a discounted-cash-flow model (yfinance, free). The app does
    the projection live so assumptions are editable. Educational, not advice."""
    import yfinance as yf
    try:
        i = yf.Ticker(ticker).info
    except Exception as e:
        return {"ticker": ticker.upper(), "error": f"fetch failed: {type(e).__name__}"}
    fcf = i.get("freeCashflow")
    shares = i.get("sharesOutstanding")
    price = i.get("currentPrice") or i.get("regularMarketPrice")
    if not fcf or not shares:
        return {"ticker": ticker.upper(), "error": "Free cash flow or share count unavailable — can't model a DCF."}
    g = i.get("earningsGrowth") or i.get("revenueGrowth") or 0.08
    try:
        g = max(0.0, min(0.30, float(g)))
    except Exception:
        g = 0.08
    return {"ticker": ticker.upper(), "name": i.get("shortName") or i.get("longName"),
            "fcf": float(fcf), "shares": float(shares),
            "cash": float(i.get("totalCash") or 0), "debt": float(i.get("totalDebt") or 0),
            "price": float(price) if price else None, "suggested_growth_pct": round(g * 100, 1)}


def earnings_lean(ticker):
    """Historical earnings beat/miss pattern (yfinance, free). A LEAN from the
    record — explicitly NOT a prediction. Educational, not advice."""
    import math
    import yfinance as yf
    try:
        df = yf.Ticker(ticker).earnings_history
    except Exception as e:
        return {"ticker": ticker.upper(), "error": f"earnings fetch failed: {type(e).__name__}"}
    if df is None or len(df) == 0:
        return {"ticker": ticker.upper(), "quarters": [], "lean": None, "beats": 0, "total": 0}
    rows, beats, total, surprises = [], 0, 0, []
    for idx, r in df.tail(8).iterrows():
        act, est, sp = r.get("epsActual"), r.get("epsEstimate"), r.get("surprisePercent")
        if act is None or est is None or (isinstance(act, float) and math.isnan(act)):
            continue
        total += 1
        beat = float(act) >= float(est)
        if beat:
            beats += 1
        spv = float(sp) * 100 if (sp is not None and not (isinstance(sp, float) and math.isnan(sp))) else None
        if spv is not None:
            surprises.append(spv)
        rows.append({"period": str(idx)[:10], "actual": round(float(act), 2),
                     "estimate": round(float(est), 2),
                     "surprise_pct": round(spv, 1) if spv is not None else None, "beat": beat})
    avg = round(sum(surprises) / len(surprises), 1) if surprises else None
    lean = None
    if total > 0:
        rate = beats / total
        lean = "LEANS BEAT" if rate >= 0.75 else ("LEANS MISS" if rate <= 0.25 else "MIXED")
    return {"ticker": ticker.upper(), "beats": beats, "total": total,
            "avg_surprise_pct": avg, "lean": lean, "quarters": list(reversed(rows))}


def portfolio_risk():
    """Risk lens over your holdings: weighted beta, sector exposure, concentration."""
    import yfinance as yf
    import holdings
    p = holdings.portfolio()
    hs = p.get("holdings", []) if isinstance(p, dict) else []
    if not hs:
        return {"present": False, "count": 0}
    wbeta, sectors = 0.0, {}
    for h in hs:
        t = h.get("ticker"); w = (h.get("weight_pct") or 0) / 100.0
        b, sec = None, "Unknown"
        try:
            info = yf.Ticker(t).info
            b = info.get("beta"); sec = info.get("sector") or "Unknown"
        except Exception:
            pass
        if isinstance(b, (int, float)):
            wbeta += b * w
        sectors[sec] = sectors.get(sec, 0) + (h.get("weight_pct") or 0)
    top_h = max(hs, key=lambda x: x.get("weight_pct") or 0)
    sec_sorted = sorted(sectors.items(), key=lambda x: -x[1])
    return {"present": True, "count": len(hs), "weighted_beta": round(wbeta, 2),
            "top_holding": top_h.get("ticker"), "top_holding_pct": round(top_h.get("weight_pct") or 0, 1),
            "top_sector": sec_sorted[0][0] if sec_sorted else "—",
            "top_sector_pct": round(sec_sorted[0][1], 1) if sec_sorted else 0,
            "sectors": [{"sector": k, "weight_pct": round(v, 1)} for k, v in sec_sorted]}


def journal_review():
    """AI coach over the paper-trade track record (grounded in real book results)."""
    import json, re
    import paper, llm
    p = paper.portfolio()
    books = p.get("books", []) if isinstance(p, dict) else []
    if not books:
        return {"present": False, "notes": []}
    facts = (f"Paper track record: {len(books)} books, total return {p.get('total_return_pct')}%. "
             + "; ".join(f"{b.get('product')}/{b.get('strategy')} {b.get('return_pct')}% over "
                         f"{b.get('days_tracked')}d (position {b.get('position')})" for b in books[:14]))
    system = ("You are a trading-journal coach for a student investor. Using ONLY these paper-trading "
              "results, give 2-3 short, specific observations (what is working, what is lagging, any "
              "pattern) and one concrete next step. If the books are brand-new with no real track record "
              "yet, say that plainly rather than inventing insight. Invent nothing. "
              'Output STRICT JSON only: {"notes":["..."]}')
    try:
        txt = llm.haiku(system, facts, 350)
        m = re.search(r"\{.*\}", txt, re.DOTALL)
        notes = [str(x) for x in ((json.loads(m.group(0)) if m else {}).get("notes") or [])][:4]
    except Exception as e:
        return {"present": True, "error": f"review failed: {type(e).__name__}", "notes": []}
    return {"present": True, "total_return_pct": p.get("total_return_pct"), "books": len(books), "notes": notes}


_SEC = {"map": None}
_SEC_UA = {"User-Agent": "EdgeDesk research (peter tsahalisp@gmail.com)"}


def _sec_cik(ticker):
    import requests
    if _SEC["map"] is None:
        r = requests.get("https://www.sec.gov/files/company_tickers.json", timeout=20, headers=_SEC_UA)
        r.raise_for_status()
        _SEC["map"] = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in r.json().values()}
    return _SEC["map"].get(ticker.upper())


def sec_analyze(ticker):
    """Pull the latest 10-K/10-Q from SEC EDGAR (free) and have Haiku extract key
    risks + growth signals from the risk-factors section. Grounded in the filing."""
    import requests, re, json
    import llm
    try:
        cik = _sec_cik(ticker)
    except Exception as e:
        return {"ticker": ticker.upper(), "error": f"SEC map failed: {type(e).__name__}"}
    if not cik:
        return {"ticker": ticker.upper(), "error": "No SEC CIK for that ticker."}
    try:
        sub = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json", timeout=20, headers=_SEC_UA).json()
        rec = sub.get("filings", {}).get("recent", {})
        forms, accs, docs, dates = rec.get("form", []), rec.get("accessionNumber", []), rec.get("primaryDocument", []), rec.get("filingDate", [])
        idx = next((i for i, fm in enumerate(forms) if fm in ("10-K", "10-Q")), None)
        if idx is None:
            return {"ticker": ticker.upper(), "error": "No recent 10-K/10-Q found."}
        form, acc, doc, date = forms[idx], accs[idx].replace("-", ""), docs[idx], dates[idx]
        html = requests.get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{doc}", timeout=30, headers=_SEC_UA).text
    except Exception as e:
        return {"ticker": ticker.upper(), "error": f"SEC fetch failed: {type(e).__name__}"}
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    m = re.search(r"item\s*1a.{0,80}?risk factors(.{500,16000}?)item\s*1b", text, re.IGNORECASE | re.DOTALL)
    excerpt = (m.group(1) if m else text[:14000])[:14000]
    system = ("You analyze an excerpt of a company's SEC filing for a student investor. From ONLY this excerpt, "
              "extract the most important KEY RISKS (3-4) and any GROWTH/OPPORTUNITY signals (1-3), each as a short "
              "plain-English bullet. Invent nothing not in the excerpt. "
              'JSON only: {"risks":["..."],"opportunities":["..."],"summary":"one line"}')
    try:
        txt = llm.haiku(system, excerpt, 600)
        mm = re.search(r"\{.*\}", txt, re.DOTALL)
        d = json.loads(mm.group(0)) if mm else {}
    except Exception as e:
        return {"ticker": ticker.upper(), "error": f"analyze failed: {type(e).__name__}"}
    return {"ticker": ticker.upper(), "form": form, "filed": date,
            "risks": [str(x) for x in (d.get("risks") or [])][:5],
            "opportunities": [str(x) for x in (d.get("opportunities") or [])][:4],
            "summary": d.get("summary")}


def options_strategy(ticker, outlook="bullish", risk="moderate"):
    """Suggest an options structure for an outlook + risk tolerance, with a plain
    explanation. Rules-based pick + Haiku teach. Educational, not a recommendation."""
    import llm
    f = research(ticker)
    price = f.get("price") if isinstance(f, dict) else None
    outlook = (outlook or "bullish").lower()
    rk = (risk or "moderate").lower()
    rk = "conservative" if ("low" in rk or "conserv" in rk) else ("aggressive" if ("high" in rk or "aggr" in rk) else "moderate")
    table = {
        ("bullish", "conservative"): "Bull call spread (debit) — defined risk, cheaper than a long call, capped upside.",
        ("bullish", "moderate"): "Long call or bull call spread — directional upside with a defined cost.",
        ("bullish", "aggressive"): "Long call — maximum leverage to an up move, with time-decay risk.",
        ("bearish", "conservative"): "Bear put spread (debit) — defined-risk downside bet, cheaper than a long put.",
        ("bearish", "moderate"): "Long put or bear put spread — directional downside with a defined cost.",
        ("bearish", "aggressive"): "Long put — maximum leverage to a down move.",
        ("neutral", "conservative"): "Iron condor — collect premium in a range with defined risk on both sides.",
        ("neutral", "moderate"): "Iron condor / short strangle via spreads — premium if it stays range-bound.",
        ("neutral", "aggressive"): "Short straddle — most premium if it pins, but undefined risk (advanced).",
    }
    pick = table.get((outlook, rk), table[("bullish", "moderate")])
    facts = f"Stock {ticker.upper()} near ${price}. Outlook: {outlook}. Risk: {rk}. Suggested structure: {pick}"
    system = ("You are an options teacher for a student. In 2-3 short sentences explain how this structure works, "
              "its main risk, and when it pays off. Plain English, no specific strikes/prices. Not a recommendation.")
    try:
        explain = llm.haiku(system, facts, 250)
        if explain:
            import re as _re
            explain = _re.sub(r"[#*`]", "", explain).strip()
    except Exception:
        explain = None
    return {"ticker": ticker.upper(), "outlook": outlook, "risk": rk, "price": price, "strategy": pick, "explanation": explain}


# ── Congress Radar: U.S. House stock-trade disclosures (STOCK Act PTRs) ──────────
# Free + no key: the House Clerk publishes a yearly ZIP whose XML indexes every
# filing. FilingType "P" = Periodic Transaction Report = a member disclosing a
# securities trade. Filing-level (member + date + link to the actual PDF); the
# ticker/amount detail lives inside each PDF. Disclosed, lagged, informational.
_CONGRESS = {"data": None, "ts": 0.0}


def _house_ptr_filings():
    import io, time, zipfile
    import xml.etree.ElementTree as ET
    from datetime import datetime
    import requests
    if _CONGRESS["data"] is not None and (time.time() - _CONGRESS["ts"]) < 6 * 3600:
        return _CONGRESS["data"]
    year = datetime.now().year
    url = f"https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
    r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0 Chrome/124"})
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    xmlname = next(n for n in z.namelist() if n.endswith(".xml"))
    root = ET.fromstring(z.read(xmlname))
    rows = []
    today = datetime.now()
    for m in root.findall(".//Member"):
        if (m.findtext("FilingType") or "") != "P":
            continue
        first = (m.findtext("First") or "").strip()
        last = (m.findtext("Last") or "").strip()
        doc = (m.findtext("DocID") or "").strip()
        fd = (m.findtext("FilingDate") or "").strip()
        try:
            dt = datetime.strptime(fd, "%m/%d/%Y")
            days_ago = (today - dt).days
        except Exception:
            dt, days_ago = datetime.min, None
        rows.append({
            "name": f"{first} {last}".strip(),
            "state": m.findtext("StateDst") or "",
            "filed": fd,
            "filed_days_ago": days_ago,
            "doc_id": doc,
            "pdf_url": f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc}.pdf",
            "_sort": dt,
        })
    rows.sort(key=lambda x: x["_sort"], reverse=True)
    for x in rows:
        x.pop("_sort", None)
    _CONGRESS["data"] = rows
    _CONGRESS["ts"] = time.time()
    return rows


def congress_trades(limit=25, member=""):
    """Recent U.S. House securities-trade disclosures (free, no key). member filters by name."""
    try:
        rows = _house_ptr_filings()
    except Exception as e:
        return {"error": f"House disclosure fetch failed: {type(e).__name__}", "filings": []}
    if member:
        ml = member.lower()
        rows = [r for r in rows if ml in r["name"].lower()]
    return {"count": len(rows), "filings": rows[:limit],
            "source": "U.S. House Clerk — Periodic Transaction Reports (STOCK Act)"}
