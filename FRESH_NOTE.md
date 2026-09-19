# Fresh system note

The dashboard has been removed. The useful remaining work is Python-based:

- `advanced_sectional_system.py` — raw previous-run sectional signal logic.
- `scripts/audit_5year_historical.py` — Master Lay backtest foundation.
- `b2l/b2l_proper_backtest.py` — B2L backtest foundation.
- `inplay_sectional_analyzer/` — live telemetry tools, only when live TPD data is available.

Required standards for the next build:

- Use `E:\Test\PRODB.mdf` / SQL Server `PROFORM_RACING`.
- Use raw `SData` sectionals, par, speed, stride, and sectional-position fields.
- Enforce previous-race-only data for selections.
- Use 2% commission (`0.98`).
- Report bets, wins, losses, fixed-liability lay P&L, ROI, profit factor, losing streak, and sequential maximum drawdown.
- Do not use the old dashboard, generated JSON, scraped fallback data, or headline results without rerunning the audit.
