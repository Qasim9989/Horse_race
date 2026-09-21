"""
BETFAIR PRICE SNAPSHOTS INTO THE APP'S OWN DATABASE
===================================================
Writes one row per runner per capture into `racing_form.db`
(`betfair_price_snapshots`), so the app keeps its own hourly price history
instead of depending on the JSON captures in the cloud workflow.

Called two ways:
  * the 📸 button in the app - an immediate capture, whatever the time of day
  * `python price_snapshot.py --hourly` on a timer - writes at most once per hour
    per runner, so a timer firing every minute still yields exactly hourly data

    python price_snapshot.py                # capture now (skips this hour if done)
    python price_snapshot.py --force        # capture now regardless
    python price_snapshot.py --status       # what is stored for a date
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import betfair_ew_service as ew  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "racing_form.db")

PRICE_MIN = 1.00
PRICE_MAX = 1000.0     # Betfair's ladder caps at 1000, so 1000 means "no offer"


def usable(value: Any) -> float | None:
    """A decimal price, or None when it is not a price at all."""
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return round(price, 2) if PRICE_MIN < price < PRICE_MAX else None


def hour_key(when: dt.datetime | None = None) -> str:
    return (when or dt.datetime.now()).strftime("%Y-%m-%dT%H")


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS betfair_price_snapshots (
            capture_hour TEXT NOT NULL,
            captured_at  TEXT NOT NULL,
            race_date    TEXT,
            race_time    TEXT,
            meeting      TEXT,
            horse        TEXT NOT NULL,
            market_id    TEXT,
            market_name  TEXT,
            back         REAL,
            lay          REAL,
            ltp          REAL,
            volume       REAL,
            PRIMARY KEY (capture_hour, market_id, horse)
        )
    """)
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_bf_snap_date
                    ON betfair_price_snapshots (race_date, capture_hour)""")
    conn.commit()


def rows_for_hour(conn: sqlite3.Connection, hour: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM betfair_price_snapshots WHERE capture_hour = ?",
                        (hour,)).fetchone()[0]


def latest_status(date_str: str) -> list[tuple[str, int, int]]:
    """[(capture_hour, rows, distinct markets)] for a race date, newest first."""
    conn = sqlite3.connect(DB_PATH)
    ensure_table(conn)
    out = conn.execute("""
        SELECT capture_hour, COUNT(*), COUNT(DISTINCT market_id)
        FROM betfair_price_snapshots WHERE race_date = ?
        GROUP BY capture_hour ORDER BY capture_hour DESC
    """, (date_str,)).fetchall()
    conn.close()
    return [(r[0], r[1], r[2]) for r in out]


def capture_now(date_str: str | None = None, force: bool = False) -> dict[str, Any]:
    """Fetch today's Betfair win markets and store one row per runner for this hour."""
    now = dt.datetime.now()
    date_str = date_str or now.date().isoformat()
    hour = hour_key(now)

    if not ew.is_configured():
        return {"ok": False, "rows": 0,
                "message": "Betfair credentials are not configured - add them in the "
                           "Betfair connection panel, then try again."}

    conn = sqlite3.connect(DB_PATH)
    ensure_table(conn)
    already = rows_for_hour(conn, hour)
    if already and not force:
        conn.close()
        return {"ok": True, "rows": 0, "skipped": True, "hour": hour,
                "message": f"{hour} is already captured ({already} rows). Wait for the next hour, "
                           f"or use force to take another snapshot now."}

    try:
        token = ew.login()
        catalogue = ew.fetch_today_catalogue(date_str, token)
        win_markets = [m for m in catalogue
                       if m.get("description", {}).get("marketType") == "WIN"]
        if not win_markets:
            conn.close()
            return {"ok": False, "rows": 0, "message": f"No Betfair win markets found for {date_str}."}

        books = ew.fetch_market_books([m["marketId"] for m in win_markets], token=token)
        meta = {m["marketId"]: m for m in win_markets}
        stamp = now.isoformat(timespec="seconds")
        rows: list[tuple] = []

        for book in books:
            market_id = book.get("marketId")
            info = meta.get(market_id, {})
            description = info.get("description", {})
            event = info.get("event", {}) or {}
            market_time = description.get("marketTime") or description.get("marketStartTime") or ""
            meeting = (description.get("meetingName") or event.get("venue")
                       or event.get("name") or "")
            market_name = info.get("marketName") or book.get("marketName") or ""
            names = {str(r["selectionId"]): r.get("runnerName") for r in info.get("runners", [])}

            for runner in book.get("runners", []):
                if runner.get("status") != "ACTIVE":
                    continue
                horse = ew.normalize_name(names.get(str(runner.get("selectionId")), ""))
                if not horse:
                    continue
                ex = runner.get("ex", {})
                backs = ex.get("availableToBack") or []
                lays = ex.get("availableToLay") or []
                rows.append((hour, stamp, date_str, market_time, meeting, horse, market_id,
                             market_name,
                             usable(backs[0]["price"]) if backs else None,
                             usable(lays[0]["price"]) if lays else None,
                             usable(runner.get("lastPriceTraded")),
                             runner.get("totalMatched")))

        before = conn.execute("SELECT COUNT(*) FROM betfair_price_snapshots").fetchone()[0]
        conn.executemany("""INSERT INTO betfair_price_snapshots
            (capture_hour, captured_at, race_date, race_time, meeting, horse, market_id,
             market_name, back, lay, ltp, volume)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(capture_hour, market_id, horse) DO UPDATE SET
                captured_at = excluded.captured_at, race_time = excluded.race_time,
                meeting = excluded.meeting, market_name = excluded.market_name,
                back = excluded.back, lay = excluded.lay, ltp = excluded.ltp,
                volume = excluded.volume""", rows)
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM betfair_price_snapshots").fetchone()[0]
        conn.close()
        markets = len({r[6] for r in rows})
        new = max(after - before, 0)
        return {"ok": True, "rows": len(rows), "new": new, "hour": hour, "markets": markets,
                "message": f"Captured {len(rows)} price rows for {hour} across {markets} markets "
                           f"({new} new, the rest refreshed)."}
    except Exception as ex:                                   # noqa: BLE001 - surfaced to the UI
        conn.close()
        return {"ok": False, "rows": 0, "message": f"Capture failed: {ex}"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture Betfair prices into racing_form.db")
    parser.add_argument("--date", default=dt.date.today().isoformat())
    parser.add_argument("--force", action="store_true", help="write even if this hour is captured")
    parser.add_argument("--hourly", action="store_true", help="timer mode: at most once per hour")
    parser.add_argument("--status", action="store_true", help="just report what is stored")
    args = parser.parse_args()

    if args.status:
        stored = latest_status(args.date)
        if not stored:
            print(f"No snapshots stored for {args.date}.")
        for hour, rows, markets in stored:
            print(f"  {hour}  {rows:>5} rows  {markets:>3} markets")
        return

    result = capture_now(args.date, force=args.force or not args.hourly)
    print(result["message"])
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
