# Weekly paper-portfolio report: gaps found (2026-11-27)

**Finding:** the repo has no weekly report generator and no turnover code. The closest real code is
`autoportfolio.status()` (NAV vs SPY, alpha, nav_history) and `metrics.compute_metrics()` (max drawdown).
`weekly_report_completeness`, `paper_turnover_drawdown` and `data_gap_alarm` do not exist in this repo.
Tests: `backend/tests/test_weekly_report_failure_modes.py` (7 xfail(strict) = the gaps below).
Nothing was fixed; these are proposals only. No live strategy logic or parameters are touched.

| xfail test | Gap | Proposed fix |
|---|---|---|
| test_turnover_matches_numpy | No turnover metric. | Add `turnover(trades, nav_history)` = sum(abs(trade value)) / 2 / mean NAV per period, from the `trades` table. |
| test_missing_nav_file_fails_loudly | `_db()` creates an empty DB if missing; `status()` returns `{"error": ...}`. | In the report path, check the DB file exists and the book has `nav_history` rows; raise `ReportInputError("book 'core': <path> missing/empty")`. |
| test_missing_spy_fails_loudly | `_price` swallows errors and returns None, so alpha becomes None silently. | The report should raise naming book and `SPY` when SPY price or any `nav_history.spy` is None, and exit non-zero. |
| test_stale_last_date_fails_loudly | No check on the last `nav_history.ts`. | Raise `stale: book 'core' last NAV <date>` if the last ts is older than 2 days (weekly report: 3 days). |
| test_fewer_than_20_obs_flagged_not_computed | `compute_metrics` computes on any length. | Return `{"status": "insufficient data", "n": n}` if n < 20 (before computing). |
| test_nan_values_flagged_not_computed | NaN propagates into results. | Same flag if any equity/ret is non-finite. |
| test_date_misalignment_is_reported | Rows with NULL SPY, or dates absent from SPY, pass through. | Inner-join portfolio and SPY on date; report `misaligned_dates` (count and list) and compute only on the intersection, failing if it is below 20. |

Other note: the task named `tests/`, but the repo's tests live in `backend/tests/`, so the new file went there.
