"""The TEACHER layer: a deep, certification-style finance syllabus + progress
tracking. Each lesson is a full classroom unit: the key idea, an in-depth
explanation, a worked numeric example, what to look for, when to act, common
pitfalls, what the masters say, and your move. Content is established investing
principle (not real-time facts); examples use illustrative numbers."""
import json
import pathlib

_PROGRESS = pathlib.Path.home() / ".tradebot" / "progress.json"

CURRICULUM = [
    {"id": "m1", "title": "Foundations & Mindset", "lessons": [
        {"id": "m1l1", "title": "What the market is, and why most underperform", "strategy": "buy_hold",
         "principle": "A stock is fractional ownership of a real business. Price follows crowd emotion in the short run but the value of the underlying business in the long run.",
         "deep": "When you buy a share you own a slice of a company's future profits and cash flow. Short term, the price is just whatever the most emotional buyer and seller agree on today - it swings on fear, greed, and headlines. Long term, price is pulled toward the cash the business actually produces. That gap between price and value is where both danger and opportunity live.\n\nThe uncomfortable truth: most active investors LOSE to a plain index fund. Studies (SPIVA) show roughly 80-90% of professional active funds trail their benchmark over 10-15 years. The drags are fees, taxes from frequent trading, the bid-ask spread, and - biggest of all - behavior: the average investor buys after big run-ups and sells after crashes, earning less than the very funds they hold. This is the 'behavior gap.' Your first job is not a hot tip - it is to stop being your own worst enemy.",
         "example": "Put $10,000 in a low-cost S&P 500 index fund (0.03% fee) vs a typical active fund (1.0% fee). At 8%/yr for 30 years, the index grows to about $99k; the 1% fee drags the active fund to about $76k - roughly $23k lost to fees alone, before the active manager even underperforms.",
         "look_for": ["Expense ratio under ~0.10% for a core index fund", "Your own behavior triggers - the urge to buy hot or sell scared", "Whether you actually have an edge, or are just paying to feel active", "Turnover/taxes eating returns in a taxable account"],
         "signals": ["BUY: a broad, low-cost index fund - regularly, on a schedule, regardless of headlines", "HOLD: through scary headlines; that is when behavior gaps are created", "AVOID: high-fee funds, frequent trading, and acting on tips from strangers"],
         "pitfalls": ["Confusing activity with progress - more trades usually means lower returns", "Chasing last year's top performer (it rarely repeats)", "Believing you will be the exception without a real, definable edge"],
         "masters": "Jack Bogle proved costs compound against you and built the first index fund. Warren Buffett has instructed that most people - even his own estate - should simply buy a low-cost S&P 500 fund.",
         "apply": "Know the baseline you must beat: a low-cost S&P 500 fund, bought regularly and held. Everything else in this course has to beat THAT after fees and taxes, or it is not worth doing."},

        {"id": "m1l2", "title": "Risk, return & the magic of compounding", "strategy": "buy_hold", "widget": "compounding",
         "principle": "Returns reward risk, but compounding rewards TIME. Small edges compounded for decades dwarf big one-off wins - and avoiding large losses matters more than chasing large gains.",
         "deep": "Compounding means earning returns on your past returns, so growth accelerates. The Rule of 72 gives you the speed: divide 72 by your annual return to get the years to double. At 10%/yr money doubles about every 7 years; at 7%/yr, about every 10.\n\nThe hidden enemy is asymmetry of loss. A -50% drop needs a +100% gain just to get back to even - not +50%. A -20% needs +25%. This is why protecting against big drawdowns matters more than squeezing out the last bit of upside. Volatility itself is a drag: a portfolio that returns +50% then -50% is down 25%, not flat. Steady compounding beats a roller coaster with the same average.",
         "example": "$10,000 at 10%/yr: about $17,500 in 6 years, about $67,000 in 20 years, about $452,000 in 40 years - the last decade adds more than the first three combined. Now suffer one -50% year: you drop to $5,000 and need a full +100% just to recover, erasing roughly 7 years of progress.",
         "look_for": ["Your time horizon - the longer it is, the more compounding does the heavy lifting", "Anything that interrupts compounding: panic-selling, fees, taxes", "Position sizes large enough to cause a -50% account-level hit"],
         "signals": ["DO: start as early as possible and add on a fixed schedule", "DO: prioritize NOT losing big over hitting home runs", "AVOID: concentrated bets that can halve your whole account"],
         "pitfalls": ["Interrupting compounding by selling in a panic", "Underestimating how fees silently compound against you", "Chasing 'double my money' bets that carry blow-up risk"],
         "masters": "Buffett's two rules: Rule 1, never lose money; Rule 2, never forget Rule 1. Einstein (apocryphally) called compounding the eighth wonder of the world.",
         "apply": "Use the compounding widget below: try $50/week added for 30 years at 8% and watch the curve bend upward. Optimize your life for surviving and staying invested, not for being right once."},

        {"id": "m1l3", "title": "Your edge & time horizon",
         "principle": "You only make money beyond the index when you know something the price does not already reflect - an edge. Define yours, and match your time horizon to it.",
         "deep": "Markets are mostly efficient: today's price already bakes in public news and the consensus view. To beat the index you need a genuine edge - one of four kinds. Informational: you know something legally before others (rare for retail). Analytical: you interpret public facts better. Behavioral: you stay patient and rational when others panic - the one edge a disciplined individual can reliably hold. Structural: rules or size let you do things others cannot.\n\nLong-term investing and short-term trading are different games with different edges. Buffett's edge is patience plus business judgment over years. Jim Simons' edge is fleeting statistical patterns over minutes. Trying to play both at once usually means playing neither well. Pick the game your temperament and time can actually win.",
         "example": "Two people buy the same stock. One has a thesis - 'this business will earn 50% more in 5 years and the market underrates that' - and holds 5 years. The other has no edge and sells the first time it drops 8%. Same stock, opposite outcomes; the difference was an edge and a matching horizon.",
         "look_for": ["Can you name your edge in one sentence? If not, you are paying fees for nothing", "Does your holding period match your edge (years for value, not days)?", "Is your 'edge' actually just last week's price action?"],
         "signals": ["DO: write down your thesis and your intended holding period BEFORE buying", "AVOID: switching between long-term investing and day-trading impulsively", "AVOID: trading on information everyone already has"],
         "pitfalls": ["Mistaking a rising price for an edge", "Holding a long-term thesis but selling on short-term noise", "Competing with professionals on speed instead of patience"],
         "masters": "Buffett (long-horizon business value) vs Jim Simons / Renaissance (short-horizon statistical patterns) - opposite edges, both enormously successful because each matched horizon to edge.",
         "apply": "Decide your game now: are you a long-term business owner or a short-term price trader? Write your edge in one sentence. If you cannot, default to indexing until you can."},
    ]},

    {"id": "m2", "title": "Value & Quality (Fundamentals)", "lessons": [
        {"id": "m2l1", "title": "Reading a business: statements & cash flow",
         "principle": "Earnings can be massaged with accounting choices; free cash flow is much harder to fake. Own businesses, not tickers.",
         "deep": "Three statements tell the story. The income statement shows profitability (revenue minus costs = earnings). The balance sheet shows what the company owns vs owes at a point in time (assets, liabilities, equity). The cash-flow statement shows actual cash moving in and out - the hardest to manipulate.\n\nThe number pros trust most is FREE CASH FLOW (FCF) = operating cash flow minus capital spending needed to maintain the business. It is the real cash left over that could be paid to owners. Earnings can be inflated by aggressive revenue recognition or one-time items; cash is cash. Also check the balance sheet for debt - a great business with too much debt can still go bankrupt in a downturn.",
         "example": "Company A reports $1.00 EPS but its free cash flow is only $0.40 - the gap is a red flag (heavy capex, or earnings propped up by accounting). Company B reports $0.80 EPS but generates $0.95 of free cash flow - it is converting profit into real cash, often the higher-quality business.",
         "look_for": ["Free cash flow that is positive and GROWING over years", "FCF roughly tracking or exceeding reported earnings (quality of earnings)", "Manageable debt: low debt/equity, interest comfortably covered by profit", "Rising revenue AND margins, not revenue bought with shrinking margins"],
         "signals": ["GREEN: consistent, growing free cash flow and a clean balance sheet", "YELLOW: earnings rising but cash flow flat or falling", "RED: heavy debt, negative FCF, and reliance on raising new money to survive"],
         "pitfalls": ["Trusting headline EPS without checking cash flow", "Ignoring debt until a downturn exposes it", "Being wowed by revenue growth funded by losses"],
         "masters": "Graham & Dodd founded security analysis on reading the statements. Buffett prizes durable, growing free cash flow above almost all else.",
         "apply": "For any stock, pull up its cash-flow statement first. Ask: is free cash flow positive, growing, and is the debt load safe? If you cannot answer, you are not ready to own it."},

        {"id": "m2l2", "title": "Valuation & margin of safety", "widget": "valuation",
         "principle": "A great company can be a terrible investment if you overpay. Buy below your estimate of value - that gap is your margin of safety.",
         "deep": "Valuation answers: what is this business worth, and am I paying less than that? Quick gauges: the P/E ratio (price / earnings per share) tells you how many years of current earnings you are paying for; P/FCF does the same with cash. A deeper tool is a discounted cash flow (DCF): estimate future cash flows and discount them back to today's value.\n\nPrecision is false comfort - nobody knows the exact value. So Graham's central idea is the MARGIN OF SAFETY: only buy when price is meaningfully BELOW your conservative estimate of value. If you think a business is worth $100 and you pay $70, a 30% margin of safety cushions your mistakes and bad luck. The bigger the uncertainty, the bigger the discount you should demand.",
         "example": "You estimate a stable company is worth about $100/share (say 20x its $5 of earnings is fair, and it earns $5). It trades at $70 - a 30% margin of safety, attractive. If instead it trades at $130 (26x earnings), you are paying up for optimism with no cushion; a small disappointment can hurt badly.",
         "look_for": ["Price clearly BELOW your conservative value estimate (a real discount)", "A P/E or P/FCF reasonable for the growth and stability of the business", "Whether today's high multiple already prices in years of perfect growth"],
         "signals": ["BUY: price at a meaningful discount (e.g. 25-40%) to a conservative value estimate", "WAIT: fair business at a fair-to-rich price - keep it on a watchlist for a pullback", "AVOID: paying a premium multiple that needs everything to go right"],
         "pitfalls": ["Falling in love with the company and ignoring the price", "Using rosy assumptions to justify a price you already want to pay", "Treating a single P/E number as gospel without context"],
         "masters": "Benjamin Graham gave us the margin of safety. Buffett: 'Price is what you pay; value is what you get' - and 'it is far better to buy a wonderful company at a fair price than a fair company at a wonderful price.'",
         "apply": "Use the margin-of-safety widget below: enter a price and your value estimate to see the discount. Never buy without a value estimate and a discount you are comfortable with."},

        {"id": "m2l3", "title": "Moats & quality",
         "principle": "A moat is a durable competitive advantage - brand, network effects, scale, switching costs, or low-cost production - that protects a company's profits from competitors.",
         "deep": "High profits attract competitors who compete them away - unless something stops them. That something is the moat. Types: BRAND (people pay more for Coca-Cola or Apple), NETWORK EFFECTS (each user makes Visa or a marketplace more valuable), SWITCHING COSTS (ripping out enterprise software is painful), SCALE/COST (Costco or a low-cost producer underprices rivals), and regulatory or patent protection.\n\nA wide-moat business can reinvest its profits at high returns for years, so its value compounds - which means time works FOR you and you depend less on perfect timing. Quality also means honest, capable management who allocate capital well. The combination - a moat plus a long runway plus good management - is what lets you buy and hold for years.",
         "example": "Two retailers both earn 15% returns on capital today. The one with a real cost moat (huge scale, supplier power) can keep earning 15% for a decade as it expands; the one without a moat sees competitors copy it and returns fade to 8%. Same starting profit, very different ten-year value.",
         "look_for": ["A clear reason competitors cannot easily steal the profits (name the moat)", "High and STABLE returns on invested capital over many years", "Pricing power: can it raise prices without losing customers?", "Management that reinvests wisely and is honest with shareholders"],
         "signals": ["GREEN: durable moat, long reinvestment runway, capable honest management", "YELLOW: good business but moat is narrowing or being disrupted", "RED: commodity business with no pricing power and eroding returns"],
         "pitfalls": ["Assuming a current high margin is a moat (it may just be temporary)", "Ignoring disruption that is quietly dissolving the moat", "Overpaying for quality so badly that even a great business disappoints"],
         "masters": "Charlie Munger pushed Buffett from buying 'cheap cigar-butts' to buying wonderful businesses with moats at fair prices - the shift that built Berkshire.",
         "apply": "Before buying, write one sentence naming the moat and why it lasts. No moat, no long-term hold - at best a cheap, short-term trade."},
    ]},

    {"id": "m3", "title": "Growth & Trend", "lessons": [
        {"id": "m3l1", "title": "Growth investing (know what you own)",
         "principle": "Pay up for businesses whose earnings can grow for years - but only growth you understand and can roughly value. GARP: growth at a reasonable price.",
         "deep": "A business growing earnings 20%/yr can justify a much higher P/E than a no-growth one, because future earnings will be far larger. The danger is paying ANY price for growth - 'story stocks' that soar on hype and collapse when growth slows. GARP threads the needle: buy real, durable growth, but refuse to overpay.\n\nPeter Lynch found his best ideas in everyday life - products he used and understood - then checked the fundamentals. His rule: know what you own and why. A useful sanity check is the PEG ratio (P/E divided by growth rate); roughly, a PEG near 1 is reasonable, well above 2 is getting expensive. But growth must be real and durable, not a single hot quarter.",
         "example": "A company grows earnings 25%/yr and trades at a P/E of 25 - a PEG of about 1.0, reasonable for that growth. Another grows 10%/yr but trades at a P/E of 40 - a PEG of 4, you are paying up for hope. Same 'growth stock' label, very different risk.",
         "look_for": ["Durable growth drivers you understand (a real reason it keeps growing)", "Growth in earnings and cash flow, not just revenue or user counts", "A valuation (PEG, P/E vs growth) that is not pricing in perfection", "A big addressable market the company can keep expanding into"],
         "signals": ["BUY: understandable, durable growth at a PEG near or below ~1.0", "WAIT: great growth story priced for flawless execution - wait for a stumble", "AVOID: hype stocks you cannot value, growing on narrative not profits"],
         "pitfalls": ["Paying any price because the story is exciting", "Confusing user/revenue growth with PROFIT growth", "Extrapolating one hot quarter into forever"],
         "masters": "Peter Lynch ('invest in what you know,' GARP) and Philip Fisher (deep 'scuttlebutt' research into great growth companies).",
         "apply": "For a growth pick, write the durable reason it grows and check its PEG. If you cannot explain the business simply, do not own it."},

        {"id": "m3l2", "title": "Momentum & trend-following", "strategy": "sma_cross",
         "principle": "Winners tend to keep winning for a while. Ride established trends, cut losers fast, and let winners run - the discipline matters more than the entry.",
         "deep": "Momentum is one of the most robust, widely-documented market effects: assets that have outperformed over the past 6-12 months tend to keep outperforming a bit longer, on average. Trend-following turns this into rules: go long when price is in a clear uptrend (e.g. above a long moving average, or a fast average above a slow one), and step aside when the trend breaks.\n\nThe catch you MUST understand: trend systems make their money in big, sustained moves and BLEED in choppy, sideways markets via 'whipsaws' - getting bought in and stopped out repeatedly, paying fees each time. They also lag: you never buy the exact bottom or sell the exact top. The edge is not prediction; it is discipline - cutting losers small and letting the occasional huge winner pay for many small losses.",
         "example": "In a strong bull market a trend system catches most of a stock's run and avoids the worst of the crash, beating buy-and-hold. But in a flat, choppy year it might trigger 6 trades, win 2, lose 4 small, and finish below a simple hold - exactly what we saw when SMA-cross LAGGED SPY in a steady uptrend (the chart above shows it).",
         "look_for": ["A clear, objective trend rule defined in advance (no eyeballing)", "Whether the market is trending (good) or chopping sideways (bad for trend)", "A predefined exit/stop so a loser stays small", "Your willingness to accept many small losses for rare big wins"],
         "signals": ["BUY: price breaks and holds above the trend filter (uptrend confirmed)", "SELL/STAND ASIDE: trend filter breaks - cut it, do not 'hope'", "AVOID: forcing trend trades in a sideways, choppy market"],
         "pitfalls": ["Whipsaws in range-bound markets quietly bleeding the account", "Overriding the rules with 'just this once' discretion", "Expecting to catch tops and bottoms - trend always lags"],
         "masters": "Jesse Livermore, Richard Donchian, and the Turtle Traders systematized trend-following; momentum is also one of the most cited academic 'factors' (Jegadeesh & Titman, AQR).",
         "apply": "Look at the worked example above - notice trend-following often LAGS in steady uptrends. If you trade trend, define the rule and the exit in advance, and only deploy it where trends actually run."},
    ]},

    {"id": "m4", "title": "Technical Analysis (tools & honest limits)", "lessons": [
        {"id": "m4l1", "title": "Price, trend & moving averages", "strategy": "sma_cross",
         "principle": "Moving averages smooth out noise to reveal the underlying trend; a fast average crossing a slow one is a simple, objective trend signal.",
         "deep": "A moving average is just the average price over the last N days, recalculated daily - it filters out the jitter so you can see direction. The classic signals: price above its 200-day average is a long-term uptrend; the '50-day crossing above the 200-day' (a GOLDEN CROSS) flags strengthening momentum, and the reverse (DEATH CROSS) flags weakening.\n\nBe honest about what technical analysis is: it describes crowd behavior and supply/demand, not the future. It works as a RISK and TIMING tool - 'am I with or against the trend, and where do I get out?' - not as a crystal ball. Used as discipline it is valuable; used as prophecy it fails. Combine it with fundamentals: technicals can tell you WHEN a fundamentally good idea has the wind at its back.",
         "example": "A stock you like fundamentally is below its falling 200-day average - catching that falling knife is risky. You wait; weeks later it reclaims the 200-day and the 50-day curls up through it (golden cross). Now you are buying a good business WITH the trend, not against it.",
         "look_for": ["Price relative to the 200-day average (above = uptrend, below = downtrend)", "50-day vs 200-day crossovers for shifts in momentum", "Whether the average is RISING (healthy) or falling", "Support/resistance: levels where price repeatedly bounces or stalls"],
         "signals": ["BUY/HOLD: price above a rising 200-day MA; golden cross confirms strength", "REDUCE/EXIT: price loses the 200-day, or a death cross forms", "AVOID: buying well below a falling average just because it 'looks cheap'"],
         "pitfalls": ["Treating MA signals as guarantees instead of probabilities", "Whipsaws when price hugs the average in a flat market", "Using technicals alone with no view on the business"],
         "masters": "Charles Dow (Dow Theory) is the grandfather of trend analysis. Technicals are hotly debated - best treated as disciplined risk management, not magic.",
         "apply": "Use a moving average to answer two questions before any trade: am I with or against the trend, and where exactly do I exit if I am wrong?"},

        {"id": "m4l2", "title": "Mean reversion & ranges", "strategy": "zscore_meanrev",
         "principle": "In range-bound markets, extremes tend to revert: an unusually cheap price snaps back up, an unusually rich price pulls back toward the average.",
         "deep": "Mean reversion is the OPPOSITE bet to trend-following. It assumes prices oscillate around a fair level, so you buy when price is unusually far BELOW its recent average and sell as it returns to normal. 'Unusually far' is measured with a z-score: how many standard deviations price sits from its 20-day average. A z-score of -2 means price is 2 standard deviations below normal - statistically stretched.\n\nThe deadly risk: mean reversion fails - badly - when a real trend or regime change begins, because 'cheap' keeps getting cheaper (a falling knife) and 'expensive' keeps climbing. So mean reversion needs a hard risk cap and works best in stable, liquid, range-bound markets, NOT in strong trends. It tends to have a high win rate with occasional large losses - the mirror image of trend-following's low win rate with occasional large wins.",
         "example": "A stable stock normally trades around $100 with small wiggles. It drops to $92 on no real news - a z-score of about -2. A mean-reversion trader buys, expecting a bounce to ~$100, with a stop at $88. Most of the time it works. But if $92 was the start of a real collapse, the stop saves the account - without it, 'cheap' becomes 'cheaper' becomes a disaster.",
         "look_for": ["A genuinely range-bound, stable market (not a trending one)", "A statistically stretched move (z-score near -2 or beyond)", "A clear reason it is noise, not a fundamental regime change", "A hard stop-loss - non-negotiable for mean reversion"],
         "signals": ["BUY: stable asset stretched well below its mean with a defined stop", "SELL/TRIM: price returns toward the average (take the reversion)", "AVOID: mean-reverting into a strong downtrend or a real news shock"],
         "pitfalls": ["Catching a falling knife - buying a true downtrend as if it were noise", "Skipping the stop-loss; one un-capped loss erases many small wins", "Using it in trending markets where it bleeds"],
         "masters": "Ed Thorp and statistical-arbitrage desks formalized mean reversion into rigorous, risk-capped systems.",
         "apply": "Only mean-revert in genuinely range-bound markets, only on stretched moves, and ALWAYS with a hard stop. 'Cheap' with no stop is how accounts blow up."},
    ]},

    {"id": "m5", "title": "Portfolio Construction & Risk", "lessons": [
        {"id": "m5l1", "title": "Diversification & allocation",
         "principle": "Do not bet the farm on one thing. Combining assets that do not move together cuts risk without a proportional cut in return - the closest thing to a free lunch in finance.",
         "deep": "If two assets do not move in lockstep (low correlation), holding both smooths your ride: when one zigs, the other often zags, so the combined portfolio is less volatile than either alone - yet you keep most of the return. That is the 'free lunch' of diversification, formalized by Markowitz's Modern Portfolio Theory.\n\nThe biggest driver of your long-run risk and return is not which stock you pick - it is your ASSET ALLOCATION: the mix of stocks, bonds, cash, and other assets. Studies attribute the majority of portfolio outcome variability to allocation, not security selection. Decide your allocation first (based on your goals and how much drawdown you can stomach), diversify within each bucket, and REBALANCE periodically - trimming what grew and adding to what lagged, which mechanically sells high and buys low.",
         "example": "A 100% one-stock portfolio can lose 50% on a single bad earnings report. A portfolio of 25 uncorrelated-ish holdings across sectors might drop 15% in the same shock - same market, far less damage - while still capturing the market's long-run climb. Rebalancing once a year back to target mechanically harvests winners and tops up losers.",
         "look_for": ["Your target allocation across asset classes BEFORE picking securities", "Correlation: are your holdings all the same bet in disguise?", "Concentration: is any one position big enough to sink you?", "A rebalancing schedule (e.g. once or twice a year)"],
         "signals": ["DO: set an allocation, diversify within it, rebalance on a schedule", "TRIM: any single position that grows into an outsized share of the portfolio", "AVOID: 20 holdings that are really one bet (all the same sector/theme)"],
         "pitfalls": ["Fake diversification - many tickers that all move together", "Never rebalancing, so winners quietly become dangerous concentrations", "Picking stocks before deciding the overall allocation"],
         "masters": "Harry Markowitz won a Nobel for Modern Portfolio Theory. Ray Dalio built 'All Weather' / risk parity around diversification across economic environments.",
         "apply": "Write your target allocation first. Then make sure your holdings are genuinely different bets, and set a rebalancing date so winners do not silently become your whole risk."},

        {"id": "m5l2", "title": "Position sizing, Kelly & drawdown", "widget": "drawdown",
         "principle": "How MUCH you bet matters more than what you bet. Size positions to survive losing streaks; a deep drawdown can end the game before your edge plays out.",
         "deep": "Even a great edge loses many times in a row eventually. If any single bet is large enough to cripple you, one bad streak ends your investing life before the math of your edge can work. So position sizing - the percent of capital per bet - is the real risk control.\n\nThe Kelly criterion gives the mathematically growth-optimal bet size from your edge and odds, but FULL Kelly is brutally volatile, so professionals bet a FRACTION (often a quarter to a half) to tame the swings. Watch MAX DRAWDOWN: the deepest peak-to-trough fall in your equity - it is your real-world pain test and the thing most likely to make you quit at the worst time. Remember the asymmetry: a -50% drawdown needs +100% to recover. Keeping drawdowns shallow is how you stay in the game long enough to compound.",
         "example": "Risk 2% of a $10,000 account per trade ($200). A painful streak of 5 losses costs about 10% - annoying but survivable. Now risk 20% per trade ($2,000): the same 5-loss streak takes you down roughly 67%, needing a +200% recovery. Same edge, same streak - sizing is the difference between a dip and a disaster.",
         "look_for": ["A fixed, small risk per position (commonly ~1-2% of capital)", "Total exposure and correlation across positions (hidden concentration)", "Your historical and tolerable MAX DRAWDOWN", "Whether any single loss could end your ability to keep playing"],
         "signals": ["DO: risk a small fixed fraction per position and cap total exposure", "DO: use a FRACTION of Kelly, never full Kelly", "AVOID: sizing up after a hot streak (that is when ruin strikes)"],
         "pitfalls": ["Betting big after wins (overconfidence right before the streak breaks)", "Ignoring correlation - ten positions that are secretly one giant bet", "Tolerating a drawdown so deep you panic-sell at the bottom"],
         "masters": "John Kelly (the criterion) and Ed Thorp (who used fractional Kelly to beat blackjack AND markets). Every serious risk manager obsesses over drawdown.",
         "apply": "Use the drawdown widget below to feel the asymmetry: see how much gain each loss requires to recover. Then pick a fixed, small risk-per-trade and never exceed it."},
    ]},

    {"id": "m6", "title": "Behavioral Edge", "lessons": [
        {"id": "m6l1", "title": "Biases that cost you money",
         "principle": "Your brain is wired against good investing: loss aversion, recency bias, overconfidence, herding, and FOMO quietly sabotage returns.",
         "deep": "Most underperformance is not bad analysis - it is bad behavior. LOSS AVERSION: losses hurt about twice as much as equivalent gains feel good, so we sell winners too early (to lock in the good feeling) and hold losers too long (to avoid the pain). RECENCY BIAS: we assume the recent past continues, so we buy after rallies and sell after crashes. OVERCONFIDENCE makes us trade too much. HERDING and FOMO drag us into bubbles at the top.\n\nThe fix is not to feel less - it is to build RULES that bind your future, panicking self. Decide your buy criteria, sell criteria, and position size in a calm moment and in writing, then follow them when emotions run hot. Automation (scheduled buying, preset stops, rebalancing dates) takes the in-the-moment decision - and the bias - out of your hands.",
         "example": "A stock you own drops 30% on a market panic. Loss aversion screams 'sell to stop the pain,' and recency bias whispers 'it will keep falling.' The investor with a written rule ('I hold quality through drawdowns unless the thesis breaks') stays put and recovers; the one acting on feeling sells the bottom and buys back higher - the textbook behavior gap.",
         "look_for": ["Your own urges: the itch to sell winners early and average down on losers", "Buying because something already went up a lot (recency/FOMO)", "Trading frequency creeping up (overconfidence)", "Following the crowd into whatever is hot"],
         "signals": ["DO: write buy, sell, and sizing rules in a calm moment", "DO: automate (scheduled buys, preset stops, rebalancing dates)", "AVOID: making big decisions in the heat of a rally or a crash"],
         "pitfalls": ["Selling winners early and clinging to losers (loss aversion)", "Chasing whatever just went up (recency/FOMO)", "Confusing a strong feeling with a strong reason"],
         "masters": "Kahneman & Tversky founded behavioral finance (prospect theory). Charlie Munger's 'Psychology of Human Misjudgment' catalogs the biases to guard against.",
         "apply": "Write your rules now, while calm. The whole point is to bind the future you who will be tempted to do something dumb when the screen is red or green."},

        {"id": "m6l2", "title": "Process over outcome",
         "principle": "Judge decisions by the quality of the PROCESS, not the result of any single trade - good process can lose short-term, and bad process can win short-term, purely by luck.",
         "deep": "In the short run, markets are noisy: a great decision can lose money and a reckless one can win, just from randomness. If you grade yourself only on outcomes, you will 'learn' the wrong lessons - getting rewarded for luck and punished for discipline - and start chasing whatever recently worked. That is how good investors drift into bad habits.\n\nThe professional's fix: separate process from outcome. A sound, repeatable process - a real edge, proper sizing, written rules - compounds over many decisions even though any one can lose. Keep a JOURNAL of your reasoning at the time of each decision (not just the P&L). Reviewing the journal lets you improve the process and tell skill from luck. Over a large number of bets, good process wins; over a single bet, anything can happen.",
         "example": "You make two trades. Trade A followed all your rules and lost money; Trade B broke your rules but happened to win. Outcome-thinking praises B and curses A - exactly backwards. Process-thinking keeps A's discipline and flags B as a lucky escape you should NOT repeat. Over 100 trades, the disciplined process wins.",
         "look_for": ["Did you follow your written process, regardless of the result?", "A journal entry capturing your reasoning at decision time", "Patterns across MANY trades, not conclusions from one", "Luck masquerading as skill (a reckless win) or skill mislabeled as failure (a disciplined loss)"],
         "signals": ["DO: log the reasoning behind every decision, then review periodically", "DO: keep a process even through a losing streak, if the edge is real", "AVOID: abandoning a sound process because of a few unlucky outcomes"],
         "pitfalls": ["Rewarding lucky, reckless wins and punishing disciplined losses", "Drawing big conclusions from a tiny sample of trades", "Changing the system every time it has a rough patch"],
         "masters": "Annie Duke ('Thinking in Bets') on separating decision quality from outcome; every serious quant shop measures process over single results.",
         "apply": "Start a trade journal: write WHY before each decision. Review it monthly to improve the process and to tell your skill apart from your luck."},
    ]},

    {"id": "m7", "title": "Quant & Validation", "lessons": [
        {"id": "m7l1", "title": "Backtesting pitfalls",
         "principle": "A backtest can lie. Overfitting, look-ahead bias, and survivorship bias make worthless strategies look brilliant on past data.",
         "deep": "Testing a strategy on history is powerful - and dangerous, because it is easy to fool yourself. OVERFITTING: tweak enough rules and parameters and you can fit any past perfectly, capturing random noise that will not repeat - the strategy dazzles in-sample and dies on new data. LOOK-AHEAD BIAS: accidentally using information you would not have had in real time (e.g. today's close to decide today's trade) inflates results. SURVIVORSHIP BIAS: testing only on companies that still exist ignores the ones that went bankrupt, flattering the result.\n\nThe defense is OUT-OF-SAMPLE testing: optimize on one slice of history, then test the SAME rules on a different slice the strategy never saw. If the edge survives unseen data, it might be real; if it collapses, it was a mirage. This harness already blocks look-ahead (it acts on the next bar) and charges realistic fees - but only out-of-sample results count as evidence.",
         "example": "A strategy tuned on 2015-2020 shows a glorious +200% with a perfect-looking equity curve. Run those exact rules on 2021-2024 (data it never saw) and it returns -5%. The in-sample brilliance was curve-fit noise. Our validator caught exactly this kind of mirage - in-sample alpha that vanishes out-of-sample.",
         "look_for": ["A genuine out-of-sample / walk-forward test, not just in-sample results", "How many parameters were tuned (more knobs = more overfitting risk)", "Whether bankrupt/delisted names were excluded (survivorship)", "Realistic fees, slippage, and no use of future information"],
         "signals": ["TRUST (cautiously): edge that HOLDS out-of-sample across periods and assets", "DISCARD: edge that vanishes or reverses on unseen data", "SUSPECT: a too-perfect equity curve from a heavily-tuned strategy"],
         "pitfalls": ["Optimizing until the past looks perfect (curve-fitting)", "Using data you would not have had in real time (look-ahead)", "Believing an in-sample backtest is proof of a real edge"],
         "masters": "Marcos Lopez de Prado ('Advances in Financial Machine Learning') details why most published backtests fail to survive reality.",
         "apply": "Never trust an in-sample backtest. Use the Validation panel: optimize in-sample, then test out-of-sample. Only an edge that survives unseen data is worth real money."},

        {"id": "m7l2", "title": "Measuring edge: alpha, Sharpe, drawdown",
         "principle": "Beating buy-and-hold (alpha) is the bar. Sharpe measures return per unit of risk; max drawdown measures the pain you must endure to earn it.",
         "deep": "Three numbers cut through the noise. ALPHA: your return MINUS the benchmark (buy-and-hold). A strategy that 'makes money' but trails the index added no value - you could have just bought the index and done less work. SHARPE RATIO: excess return divided by volatility - reward per unit of risk; higher is better, and roughly above 1 is good for a strategy, above 2 is excellent. MAX DRAWDOWN: the worst peak-to-trough fall - a strategy with a great return but a -60% drawdown may be untradeable in real life because you would quit before it recovered.\n\nJudge any strategy on all three together: did it beat buy-and-hold (alpha), how much risk did it take to do so (Sharpe), and could you actually stomach the worst stretch (drawdown)? A modest return with a high Sharpe and shallow drawdown often beats a flashy return that whipsaws your stomach and your account.",
         "example": "Strategy A returns +30% with a -15% max drawdown and Sharpe 1.2. Strategy B returns +45% but with a -55% drawdown and Sharpe 0.6. B's bigger number is a trap: almost nobody holds through a -55% fall, and its risk-adjusted return (Sharpe) is far worse. A is the better, actually-tradeable strategy.",
         "look_for": ["Positive ALPHA vs buy-and-hold (otherwise just index)", "A Sharpe ratio you can compare across strategies (higher = better risk-adjusted)", "A max drawdown you could genuinely live through without quitting", "Consistency, not one lucky outlier driving the whole result"],
         "signals": ["KEEP: positive alpha, solid Sharpe, and a drawdown you can stomach", "RECONSIDER: high return but brutal drawdown or poor Sharpe", "DROP: a strategy that fails to beat buy-and-hold after fees"],
         "pitfalls": ["Chasing raw return while ignoring the drawdown that comes with it", "Forgetting the benchmark - 'made money' is not the same as 'added value'", "Letting one lucky trade flatter the whole track record"],
         "masters": "William Sharpe (the Sharpe ratio, a Nobel) gave us risk-adjusted return; the whole industry benchmarks performance against an index for alpha.",
         "apply": "Judge every strategy on all three: alpha vs buy-and-hold, Sharpe, and max drawdown. If it does not beat the index after fees, just buy the index."},
    ]},

    {"id": "m8", "title": "Build Your Strategy", "lessons": [
        {"id": "m8l1", "title": "From principle to rules",
         "principle": "An edge you cannot write as objective rules is not a strategy - it is a feeling. Turn a principle into explicit entry, exit, and sizing rules.",
         "deep": "Every tradeable strategy has three parts: a THESIS (why this should work - the edge), MECHANICS (the exact, objective rules for entry, exit, and position size), and RISK CONTROL (the stop or cap that bounds the damage when you are wrong). If any part lives only in your head, you cannot test it, cannot follow it consistently, and cannot tell whether it works.\n\nStart simple - one clear idea at a time - because every extra rule is another chance to overfit. Write rules a stranger could follow without you: 'Buy when X; sell when Y; risk Z% per position.' Then backtest it, validate it out-of-sample, and only then consider it real. The goal is not a complicated system; it is a simple, honest one you can actually execute the same way every time.",
         "example": "Vague: 'I buy strong stocks when they look cheap.' Objective: 'Buy when price is above its 200-day MA AND the P/E is below the 5-year average; exit when price closes below the 200-day MA; risk 1.5% of capital per position.' Only the second can be backtested, validated, and followed without emotion.",
         "look_for": ["A one-sentence THESIS: why does this edge exist?", "Objective entry and exit rules a stranger could follow", "A defined risk cap / stop per position", "The fewest rules that capture the idea (avoid overfitting)"],
         "signals": ["BUILD: a simple rule set with thesis, mechanics, and risk control written down", "TEST: backtest, then validate out-of-sample before believing it", "AVOID: piling on parameters to make the backtest prettier"],
         "pitfalls": ["Keeping the 'rules' vague and in your head", "Over-complicating until you have curve-fit the past", "Skipping the risk-control rule entirely"],
         "masters": "Every systematic shop works thesis-first, then objective rules, then test - the same discipline whether you are Renaissance or a solo trader.",
         "apply": "Pick ONE principle you believe from this course. Write its thesis, entry/exit rules, and risk cap in plain language - then we backtest and validate it together."},

        {"id": "m8l2", "title": "Paper trade, journal, then go live",
         "principle": "Prove a strategy on paper with honest accounting before risking a dollar; journal every decision; and scale into real money slowly. Going live is the LAST step, not the first.",
         "deep": "The professional path is research -> paper -> small live -> scale. After a strategy survives an out-of-sample backtest, run it FORWARD on paper (or tiny size) to catch the gaps a backtest hides: real fees, slippage, your own discipline, and whether you can actually follow the rules when real emotions show up. Track a real forward equity curve and CLV-style honesty about whether the edge persists out of sample.\n\nKeep journaling - the reasoning, not just the P&L - so you can separate skill from luck. Only when the forward record earns your trust do you go live, and even then with SMALL size, scaling up only as the live results confirm the paper results. Most blow-ups come from skipping straight to large live size on the strength of a pretty backtest. Patience here is not timidity; it is what keeps you in the game long enough to compound.",
         "example": "A validated strategy goes to paper trading for 2-3 months. It still works, but you discover slippage shaves 1% and that you struggle to take the exits on time. You fix the process, then start live with quarter size, scaling up over months as live matches paper - instead of betting your whole account on day one and learning these lessons the expensive way.",
         "look_for": ["An out-of-sample backtest BEFORE any live money", "A forward paper-trade record with realistic fees and slippage", "A journal proving you can follow the rules under real emotion", "A plan to scale from small to full size as live confirms paper"],
         "signals": ["GO LIVE: only after paper/forward results confirm the edge, and start small", "STAY ON PAPER: if you cannot follow the rules or the edge is not showing", "AVOID: jumping straight to full live size off a backtest alone"],
         "pitfalls": ["Going live at full size on the strength of a backtest", "Skipping the forward/paper step that exposes real-world frictions", "Not journaling, so you never learn whether it was skill or luck"],
         "masters": "The standard professional progression - research, paper, small live, scale - is shared by virtually every serious systematic trader.",
         "apply": "Run your strategy in the Paper Track Record forward, journal every decision, and only graduate to real money - small at first - when the live record earns it."},
    ]},
]

_ALL = {l["id"]: (m, l) for m in CURRICULUM for l in m["lessons"]}
_TOTAL = len(_ALL)


def _load():
    try:
        return json.loads(_PROGRESS.read_text())
    except Exception:
        return {"completed": []}


def _save(d):
    _PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    _PROGRESS.write_text(json.dumps(d, indent=2))


def overview():
    done = set(_load().get("completed", []))
    mods = []
    for m in CURRICULUM:
        lessons = [{"id": l["id"], "title": l["title"], "done": l["id"] in done, "strategy": l.get("strategy")}
                   for l in m["lessons"]]
        mods.append({"id": m["id"], "title": m["title"], "lessons": lessons,
                     "complete": all(x["done"] for x in lessons)})
    pct = round(100 * len(done) / _TOTAL, 1) if _TOTAL else 0
    cert = "Certified" if pct >= 100 else ("In progress" if done else "Not started")
    return {"modules": mods, "completed": len(done), "total": _TOTAL,
            "progress_pct": pct, "certification": cert}


def lesson(lesson_id):
    hit = _ALL.get(lesson_id)
    if not hit:
        return {"error": f"unknown lesson '{lesson_id}'"}
    m, l = hit
    out = {"module": m["title"], **l, "done": lesson_id in set(_load().get("completed", [])),
           "quiz": QUIZ.get(lesson_id, [])}
    b = BUILDS.get(lesson_id)
    if b:
        out["builds"], out["builds_label"] = b
    return out


def next_lesson():
    done = set(_load().get("completed", []))
    for m in CURRICULUM:
        for l in m["lessons"]:
            if l["id"] not in done:
                return {"module": m["title"], **l, "done": False, "quiz": QUIZ.get(l["id"], [])}
    return {"message": "All lessons complete - you're certified. Time to build your own strategy."}


def complete(lesson_id):
    """TOGGLE a lesson's completion on/off."""
    if lesson_id not in _ALL:
        return {"error": f"unknown lesson '{lesson_id}'"}
    d = _load(); done = set(d.get("completed", []))
    if lesson_id in done:
        done.discard(lesson_id)
    else:
        done.add(lesson_id)
    d["completed"] = sorted(done); _save(d)
    return overview()

# Per-lesson interactive quiz: each item = {q, options, answer (index), why}.
QUIZ = {
    "m1l1": [{"q": "Why do most active traders underperform a simple index fund?",
              "options": ["The market is rigged against individuals", "Fees, taxes, overtrading, and buying high / selling low",
                          "Index funds secretly use leverage", "They never have enough capital"],
              "answer": 1, "why": "The drag is mostly self-inflicted - costs and emotional timing - not a rigged market."}],
    "m1l2": [{"q": "About how long does a 10%/yr return take to double your money?",
              "options": ["~2 years", "~7 years", "~15 years", "~25 years"],
              "answer": 1, "why": "Rule of 72: 72 / 10 is about 7 years."},
             {"q": "A 50% loss needs what gain just to break even?",
              "options": ["50%", "75%", "100%", "150%"],
              "answer": 2, "why": "You need +100% to recover a -50% - which is why avoiding big losses matters most."}],
    "m1l3": [{"q": "What is an investing 'edge'?",
              "options": ["A guaranteed win", "Knowing something the price doesn't already reflect",
                          "Using more leverage", "Simply trading more often"],
              "answer": 1, "why": "Edge comes from information, analysis, or behavior others lack. Without it you're just paying fees."}],
    "m2l1": [{"q": "Which number is hardest for a company to manipulate?",
              "options": ["Reported earnings", "Free cash flow", "Revenue guidance", "The press release"],
              "answer": 1, "why": "Free cash flow is real cash in and out - much harder to massage than accounting earnings."}],
    "m2l2": [{"q": "What is a 'margin of safety'?",
              "options": ["Buying well below your estimate of value", "A stop-loss order",
                          "Owning many sectors", "Keeping cash on the sideline"],
              "answer": 0, "why": "Graham's idea: the gap between price and value cushions your inevitable mistakes."}],
    "m2l3": [{"q": "A company's 'moat' is...",
              "options": ["A short-term chart pattern", "A durable competitive advantage",
                          "A kind of bond", "A tax shelter"],
              "answer": 1, "why": "Brand, network, scale, switching costs - what protects profits from competitors over time."}],
    "m3l1": [{"q": "GARP stands for...",
              "options": ["Growth At a Reasonable Price", "Guaranteed Annual Return Plan",
                          "Global Asset Risk Premium", "Greater Appreciation, Reduced Profit"],
              "answer": 0, "why": "Lynch's approach: pay up for real growth, but not at any price."}],
    "m3l2": [{"q": "Trend-following tends to LOSE money when the market...",
              "options": ["Trends strongly upward", "Chops sideways in a range",
                          "Makes new highs", "Has high volume"],
              "answer": 1, "why": "Choppy markets whipsaw it - paying fees on false signals. We watched it lag SPY for exactly this reason."}],
    "m4l1": [{"q": "A 'golden cross' happens when...",
              "options": ["Price hits an all-time high", "A fast moving average crosses ABOVE a slow one",
                          "Two stocks become correlated", "RSI rises above 70"],
              "answer": 1, "why": "e.g. the 50-day crossing above the 200-day - a classic trend-up signal."}],
    "m4l2": [{"q": "Mean reversion bets that...",
              "options": ["Trends continue indefinitely", "Extremes snap back toward the average",
                          "Volume predicts price", "Earnings always beat"],
              "answer": 1, "why": "Buy the unusually cheap, fade the unusually rich - the opposite bet to trend-following."}],
    "m5l1": [{"q": "Why is diversification called the only 'free lunch'?",
              "options": ["It guarantees a profit", "Combining assets that don't move together cuts risk without a proportional cut in return",
                          "It removes all risk", "It lets you use leverage safely"],
              "answer": 1, "why": "Markowitz: blending uncorrelated assets lowers portfolio volatility for the same return."}],
    "m5l2": [{"q": "Why do most pros bet a FRACTION of full Kelly?",
              "options": ["To pay less tax", "To cut volatility and survive losing streaks",
                          "It's legally required", "To boost raw returns"],
              "answer": 1, "why": "Full Kelly is wildly volatile; a fraction tames drawdowns so you stay in the game."}],
    "m6l1": [{"q": "Most investor underperformance comes from...",
              "options": ["Bad luck", "Behavioral mistakes - selling bottoms, chasing tops",
                          "High taxes", "Slow execution"],
              "answer": 1, "why": "Loss aversion, recency bias, FOMO - your own brain is the biggest risk."}],
    "m6l2": [{"q": "Why judge a decision by its PROCESS, not one outcome?",
              "options": ["Outcomes never matter", "Good process can lose short-term and bad process can win short-term - luck clouds single results",
                          "Process is just easier to measure", "Outcomes are always random"],
              "answer": 1, "why": "Short-term results are noisy; a sound, repeatable process is what compounds."}],
    "m7l1": [{"q": "'Overfitting' a backtest means...",
              "options": ["Testing on too much history", "Tuning a strategy to past noise so it fails on new data",
                          "Using live data", "Paying too many fees"],
              "answer": 1, "why": "It looks brilliant in-sample, then dies out-of-sample - the 'mirage' the validator catches."}],
    "m7l2": [{"q": "A strategy makes money but trails buy & hold. It...",
              "options": ["Is still great", "Added no value - beating the index (alpha) is the bar",
                          "Must have a high Sharpe", "Is risk-free"],
              "answer": 1, "why": "If you can't beat the index, just buy the index. Alpha is the bar."}],
    "m8l1": [{"q": "An edge you can't write as objective rules is...",
              "options": ["A real strategy", "A feeling, not a strategy",
                          "Automatically profitable", "Already a backtest"],
              "answer": 1, "why": "Entry, exit, and sizing rules are what make an idea testable and repeatable."}],
    "m8l2": [{"q": "The right order to deploy a new strategy is...",
              "options": ["Go live immediately at full size", "Research, then paper, then small live, then scale",
                          "Live first, test later", "Backtest then full size"],
              "answer": 1, "why": "Prove it on paper with honest accounting before risking real money - going live is the LAST step."}],
}


# Which model BLOCK each lesson contributes: id -> ({patch}, button label).
BUILDS = {
    "m1l3": ({"horizon": "long-term"}, "Set my horizon: long-term business owner"),
    "m2l2": ({"notes": "Only buy at a margin of safety vs my value estimate"}, "Add a margin-of-safety rule"),
    "m2l3": ({"notes": "Require a durable moat for any long-term hold"}, "Add a moat requirement"),
    "m3l1": ({"notes": "Prefer durable, understandable growth at a reasonable price (GARP)"}, "Add a growth (GARP) screen note"),
    "m3l2": ({"entry": "sma_cross"}, "Use trend-following (SMA cross) as my ENTRY signal"),
    "m4l1": ({"trend_filter": True}, "Add a 200-day trend FILTER (only go long with the trend)"),
    "m4l2": ({"entry": "zscore_meanrev"}, "Use mean-reversion (z-score) as my ENTRY signal"),
    "m5l1": ({"universe": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]}, "Diversify my model across 5 tickers"),
    "m5l2": ({"risk_pct": 1.5}, "Set RISK to 1.5% of capital per position"),
}
