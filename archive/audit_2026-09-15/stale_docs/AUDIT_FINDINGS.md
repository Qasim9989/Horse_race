# Audit Findings: Lookahead, Backtest, and ROI Safety

Last reviewed: 2026-08-21

## Trust for profitability reporting

Use these as the clean profitability/audit entrypoints:

- `scripts/audit_5year_historical.py` for the Master Lay system.
- `scripts/hybrid_racingtv_proform_lay_audit.py` when you want RacingTV scraped sectionals/form as the scoring source and Proform only for BSP/result matching.
- `scripts/custom_date_audit.py` for single-date lay and B2L inspection.
- `scripts/ml_weight_and_oos_validator.py` for 2025/2026 split checks.
- `b2l/b2l_proper_backtest.py` or `b2l/b2l_audit.py` for B2L profitability. `b2l_audit.py` now delegates to the clean all-runners backtest.

Treat older `scripts/backtest_*` files as experiments unless they are explicitly updated to the documented rules.

## Issues found and fixed

- `scripts/backtest_fixed_liability_lay_system.py` used `DecSP <= 20.0` for lay bets even though the documented Master Lay rule is `BSP <= 6.0`. It now uses `<= 6.0`.
- `scripts/backtest_with_full_raceiq_sectionals.py` had the same `<= 20.0` lay cutoff. It now uses `<= 6.0`.
- Those two scripts reported ROI against exchange stake/profit stake instead of fixed liability. They now report strategy ROI against total liability risked: `number_of_bets * 15`.
- Those two scripts used tied ranking via `rank(method='min')`, which could select more than two runners from a race when scores tied. They now use deterministic ordering and `cumcount() + 1`.
- `b2l/b2l_audit.py` previously loaded only horses that finished 1st, 2nd, or 3rd. That is outcome pre-filtering and cannot produce a valid ROI. It now delegates to `b2l_proper_backtest.py`, which loads every high-odds handicap runner before scoring.
- `webapp/server.py` previously fell back to scraped same-race result comments and artificial prior-form placeholders when PRODB had no records. That could contaminate point-in-time scoring. It now refuses to run the audit unless clean PRODB prior-race data exists.
- `b2l/b2l_scanner.py` used `POSAFTUPG` in forward scoring while `b2l_proper_backtest.py` deliberately removed it. Forward B2L scoring now matches the clean B2L backtest feature set.

## Remaining cautions

- The Master Lay model still uses `LTO_POSAFTUPG` from the prior completed race in the strict PRODB audits. That is not same-race lookahead if `R2.RH_DateTime < RH.RH_DateTime` remains enforced, but it depends on `POSAFTUPG` being a historical post-race sectional field for the horse's previous run.
- Any headline ROI in older notes or generated reports should be regenerated after these fixes before being trusted.
- BSP filters are settlement-time filters. They are valid for a BSP-only strategy, but not proof that the bet was knowable at an earlier fixed pre-off price.
