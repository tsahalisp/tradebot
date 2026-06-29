"""TradeBot research API (:8001). Backtest harness over crypto bars; pluggable
strategies + data sources. Same skeleton as EdgeBot, separate concern."""
from fastapi import FastAPI, Body

from datasource import get_bars
from strategies import REGISTRY
from backtest import run_backtest
from config import config

app = FastAPI(title="TradeBot API", version="0.1.0")


@app.get("/")
def root():
    return {"status": "TradeBot API", "version": "0.1.0"}


@app.get("/holdings")
def get_holdings():
    import holdings
    return holdings.portfolio()


@app.post("/holdings")
def post_holding(ticker: str, shares: float, cost_basis: float = 0.0):
    import holdings
    return holdings.upsert(ticker.upper(), shares, cost_basis)


@app.post("/holdings/import")
def holdings_import(payload: dict = Body(...)):
    import holdings
    return holdings.import_csv(payload.get("csv", ""))


@app.get("/holdings/rebalance")
def holdings_rebalance():
    import holdings
    return holdings.rebalance()


@app.get("/research")
def get_research(ticker: str):
    import screener
    return screener.research(ticker.upper())


@app.get("/fundamentals")
def get_fundamentals(ticker: str):
    import screener
    return screener.fundamentals(ticker.upper())


@app.get("/research/thesis")
def get_thesis(ticker: str):
    import screener
    return screener.thesis(ticker.upper())


@app.get("/research/news")
def get_news(ticker: str):
    import screener
    return screener.news(ticker.upper())


@app.get("/research/insider")
def get_insider(ticker: str):
    import screener
    return screener.insider(ticker.upper())


@app.get("/research/dcf")
def get_dcf(ticker: str):
    import screener
    return screener.dcf_inputs(ticker.upper())


@app.get("/research/earnings")
def get_earnings(ticker: str):
    import screener
    return screener.earnings_lean(ticker.upper())


@app.get("/portfolio/risk")
def get_portfolio_risk():
    import screener
    return screener.portfolio_risk()


@app.get("/paper/review")
def get_paper_review():
    import screener
    return screener.journal_review()


@app.get("/research/sec")
def get_sec(ticker: str):
    import screener
    return screener.sec_analyze(ticker.upper())


@app.get("/research/options")
def get_options(ticker: str, outlook: str = "bullish", risk: str = "moderate"):
    import screener
    return screener.options_strategy(ticker.upper(), outlook, risk)


@app.get("/congress")
def get_congress(limit: int = 25, member: str = ""):
    import screener
    return screener.congress_trades(limit, member)


@app.post("/holdings/remove")
def remove_holding(ticker: str):
    import holdings
    return holdings.remove(ticker.upper())


@app.get("/discover")
def get_discover(screen: str = "growth_technology_stocks", limit: int = 12, min_rev_growth: float = None, max_pe: float = None):
    import screener
    return screener.discover(screen, limit, min_rev_growth, max_pe)


@app.post("/model/add_ticker")
def model_add_ticker(ticker: str):
    import model
    return model.add_ticker(ticker.upper())


@app.post("/model/remove_ticker")
def model_remove_ticker(ticker: str):
    import model
    return model.remove_ticker(ticker.upper())


@app.get("/model")
def get_model():
    import model
    return model.load()


@app.post("/model")
def post_model(patch: dict = Body(...)):
    import model
    return model.update(**patch)


@app.post("/model/reset")
def reset_model():
    import model
    return model.reset()


@app.post("/model/apply_lesson")
def model_apply_lesson(lesson_id: str):
    import model
    return model.apply_lesson(lesson_id)


@app.get("/model/signals")
def model_signals():
    import model
    return model.signals()


@app.post("/model/graduate")
def model_graduate():
    import model
    return model.graduate()


@app.get("/model/backtest")
def model_backtest():
    import model
    return model.backtest()


@app.get("/model/validate")
def model_validate():
    import model
    return model.validate()


@app.get("/health")
def health():
    return {"status": "healthy", "strategies": list(REGISTRY), "products": config.products}


@app.get("/strategies")
def strategies():
    from strategies import META
    return {"strategies": [{"name": n, **META.get(n, {})} for n in REGISTRY]}


@app.get("/data/bars")
def data_bars(product: str = "BTC-USD", granularity: str = "1d", limit: int = 120):
    try:
        return {"product": product, "granularity": granularity, "bars": get_bars(product, granularity, limit)}
    except Exception as e:
        return {"error": str(e)}


@app.get("/backtest")
def backtest(product: str = "BTC-USD", granularity: str = "1d",
             strategy: str = "sma_cross", fee_bps: float = None):
    return run_backtest(product, granularity, strategy,
                        fee_bps if fee_bps is not None else config.fee_bps)


@app.get("/scan")
def scan(granularity: str = "1d", strategy: str = "sma_cross"):
    """Run one strategy across all configured products, ranked by alpha vs buy&hold."""
    out = []
    for p in config.products:
        r = run_backtest(p, granularity, strategy, config.fee_bps)
        if "metrics" in r:
            out.append({"product": p, **r["metrics"]})
    out.sort(key=lambda x: -x["alpha_vs_hold_pct"])
    return {"strategy": strategy, "granularity": granularity, "count": len(out), "results": out}


@app.on_event("startup")
async def _startup():
    import asyncio
    from database import init_db
    init_db()
    asyncio.create_task(_auto_step())


async def _auto_step():
    import asyncio
    from paper import step
    await asyncio.sleep(10)
    while True:
        try:
            await asyncio.to_thread(step)
        except Exception as e:
            print("auto-step error:", e)
        await asyncio.sleep(6 * 3600)


@app.post("/paper/track")
def paper_track(product: str, strategy: str = "sma_cross", notional: float = 10000.0):
    from paper import track
    return track(product, strategy, notional)


@app.post("/paper/step")
def paper_step():
    from paper import step
    return step()


@app.get("/paper/portfolio")
def paper_portfolio():
    from paper import portfolio
    return portfolio()


@app.post("/paper/untrack")
def paper_untrack(book_id: int):
    from paper import untrack
    return untrack(book_id)


@app.get("/validate")
def validate_endpoint(product: str = "SPY", strategy: str = "sma_cross", split: float = 0.7):
    """Out-of-sample / walk-forward validation: optimize in-sample, test on unseen data."""
    from validate import validate
    return validate(product, strategy, split)


@app.get("/learn/curriculum")
def learn_curriculum():
    import curriculum
    return curriculum.overview()


@app.get("/learn/lesson/{lesson_id}")
def learn_lesson(lesson_id: str):
    import curriculum
    return curriculum.lesson(lesson_id)


@app.get("/learn/next")
def learn_next():
    import curriculum
    return curriculum.next_lesson()


@app.post("/learn/complete")
def learn_complete(lesson_id: str):
    import curriculum
    return curriculum.complete(lesson_id)


@app.get("/learn/explain")
def learn_explain(strategy: str = "sma_cross", product: str = "SPY"):
    """The professor: explain what a strategy DID on real data, why, who pioneered it,
    how to do it yourself, and the honest caveats. Grounded in an actual backtest."""
    from strategies import META
    r = run_backtest(product, "1d", strategy, config.fee_bps)
    if "metrics" not in r:
        return r
    m = r["metrics"]; meta = META.get(strategy, {})
    a = m["alpha_vs_hold_pct"]; bh = m["buy_hold_return_pct"]
    verdict = "beat buy & hold" if a > 0.5 else ("lagged buy & hold" if a < -0.5 else "roughly matched buy & hold")
    if a < -0.5 and bh > 0:
        takeaway = ("In a rising market this strategy underperformed simply holding - timing carries an "
                    "opportunity cost: stepping aside on pullbacks means missing the rebounds. Beating buy & hold is genuinely hard.")
    elif a > 0.5 and bh < 0:
        takeaway = "In a falling market it lost less than holding - its value showed up as downside protection, not raw gains."
    elif a > 0.5:
        takeaway = "It added value over buy & hold this window - but prove it out-of-sample before trusting it."
    else:
        takeaway = "It roughly matched buy & hold - little real edge here once you count the effort and fees."
    return {
        "headline": f"{strategy} on {product}: {verdict} by {abs(a):.1f}% over the test window.",
        "school": meta.get("school"), "the_idea": meta.get("idea"), "champions": meta.get("champions"),
        "what_happened": (f"Returned {m['total_return_pct']}% vs {bh}% from just holding {product} "
                          f"(alpha {a:+.1f}%), Sharpe {m['sharpe']}, max drawdown {m['max_drawdown_pct']}%, "
                          f"{m['num_trades']} trades, win rate {m['win_rate_pct']}%."),
        "works_when": meta.get("works_when"), "fails_when": meta.get("fails_when"),
        "lesson_from_this_run": takeaway, "how_to_do_it_yourself": meta.get("diy"), "horizon": meta.get("horizon"),
        "honest_caveats": (f"In-sample over one ~{r['bars']}-bar window; fees modeled at {r['fee_bps']} bps; "
                           "no look-ahead, but NOT yet validated out-of-sample. Past performance is not future results."),
        "metrics": m,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
