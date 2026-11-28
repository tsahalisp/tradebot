# Backtester lookahead / placebo check

Tool: `backend/tools/backtester_lookahead_check.py` (read-only; runs `backtest.evaluate_pos`).
Tests: `backend/tests/test_backtester_lookahead_check.py`. Run from `backend/`: `python -m pytest -q`.

## Pre-existing coverage (verified 2026-11-28)
Neither file existed (nor `tools/`). The only test file was `tests/test_data_loading_quality.py`
(ingest data quality). There were NO backtester tests for lookahead, signal shifting, fills or costs.

## Added
- input (empty, NaN/inf, non-positive, misaligned/unsorted dates), truncation lookahead,
  one-sided signal-shift check, noise placebo (40 sticky random signals, fee 10 bps, tol |Sharpe| 0.5),
  `<20` obs -> "insufficient data" (exit 3, not computed). Exit 1 = flagged, naming `file=` and `check=`.
- One fill test: position at bar i earns bar i+1's return (no same-bar fill).
- Fee drag is NOT directly unit-tested beyond the placebo; no dedicated cost test yet.

## Results
All six registry strategies pass on the seed-7 fixture; the leaky strategy is flagged. No real
backtester failure was found, so nothing is xfailed and no fix is proposed.

## Notes / risks
- Shift check is a heuristic (one-sided, limit 1.5 Sharpe); truncation is the decisive test.
  Mean-reversion strategies trip a two-sided shift check legitimately, hence one-sided.
- A churny placebo (flip ~50%/bar) shows Sharpe ~ -1.3 purely from 10 bps fees; the placebo
  is sticky (2%/bar flips) to isolate bias from cost drag.
- Single fixture/seed; `backtest.run_backtest` (data fetch) is not covered (network).
