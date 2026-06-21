#!/usr/bin/env python3
"""
Detect grid power outages from solar inverter data.
Uses the rule: 0W output during confirmed active sun = power cut.
"""

import sqlite3
import os
import sys
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Outage:
    start: str
    end: str
    duration_min: float
    pre_outage_power: float


DB_PATH = os.getenv("SOLAR_DB", os.path.expanduser(
    "~/.openclaw/workspace/solar-monitor/solar_data.db"))

# Tuning constants
POWER_ZERO_THRESHOLD = 10.0      # W — readings below this count as "no output"
ACTIVE_SUN_THRESHOLD = 200.0     # W — pre-outage lookback threshold
ACTIVE_SUN_LOOKBACK = 3          # readings — must see this many prior readings
MIN_CONSECUTIVE_ZEROS = 3        # readings — outage must last at least this long


def fetch_day(date: str, db_path: str = DB_PATH) -> List[tuple]:
    """Fetch readings for a given date."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        SELECT timestamp, power_now, energy_today, status
        FROM live_readings
        WHERE date(timestamp) = ?
        ORDER BY timestamp
    """, (date,))
    rows = c.fetchall()
    conn.close()
    return rows


def detect_outages(
    rows: List[tuple],
    zero_threshold: float = POWER_ZERO_THRESHOLD,
    active_threshold: float = ACTIVE_SUN_THRESHOLD,
    lookback: int = ACTIVE_SUN_LOOKBACK,
    min_zeros: int = MIN_CONSECUTIVE_ZEROS,
) -> List[Outage]:
    """
    Detect outages from ordered readings.

    Logic:
      - Zero-power block must be >= min_zeros consecutive readings
      - Must be preceded by confirmed active sun (>active_threshold within lookback)
      - End is first non-zero reading after the block
    """
    if not rows:
        return []

    outages = []
    zero_streak = 0
    streak_start_idx = None

    for i, row in enumerate(rows):
        ts, power, energy, status = row

        if power <= zero_threshold:
            if zero_streak == 0:
                streak_start_idx = i
            zero_streak += 1
        else:
            if zero_streak >= min_zeros and streak_start_idx is not None:
                # Check active-sun lookback
                look_start = max(0, streak_start_idx - lookback)
                active_before = any(
                    rows[j][1] > active_threshold for j in range(look_start, streak_start_idx)
                )
                if active_before:
                    pre_power = rows[streak_start_idx - 1][1] if streak_start_idx > 0 else 0
                    start_ts = rows[streak_start_idx][0]
                    end_ts = ts
                    duration = (
                        datetime.fromisoformat(end_ts) - datetime.fromisoformat(start_ts)
                    ).total_seconds() / 60.0
                    outages.append(Outage(
                        start=start_ts,
                        end=end_ts,
                        duration_min=round(duration, 1),
                        pre_outage_power=pre_power,
                    ))
            zero_streak = 0
            streak_start_idx = None

    # Handle ongoing outage at end of data (if day is still in progress)
    if zero_streak >= min_zeros and streak_start_idx is not None:
        look_start = max(0, streak_start_idx - lookback)
        active_before = any(
            rows[j][1] > active_threshold for j in range(look_start, streak_start_idx)
        )
        if active_before:
            pre_power = rows[streak_start_idx - 1][1] if streak_start_idx > 0 else 0
            start_ts = rows[streak_start_idx][0]
            end_ts = rows[-1][0]
            duration = (
                datetime.fromisoformat(end_ts) - datetime.fromisoformat(start_ts)
            ).total_seconds() / 60.0
            outages.append(Outage(
                start=start_ts,
                end=end_ts,
                duration_min=round(duration, 1),
                pre_outage_power=pre_power,
            ))

    return outages


def report(date: Optional[str] = None, db_path: str = DB_PATH) -> None:
    date = date or datetime.now().strftime("%Y-%m-%d")
    rows = fetch_day(date, db_path)

    if not rows:
        print(f"No data for {date}")
        return

    outages = detect_outages(rows)
    peak = max(r[1] for r in rows)
    zero_readings = sum(1 for r in rows if r[1] <= POWER_ZERO_THRESHOLD)

    print(f"{'='*60}")
    print(f"Solar Outage Report — {date}")
    print(f"{'='*60}")
    print(f"Readings:      {len(rows)}")
    print(f"Peak power:    {peak:.0f}W")
    print(f"Zero readings: {zero_readings}")
    print(f"Outages:       {len(outages)}")
    print()

    if outages:
        for idx, o in enumerate(outages, 1):
            print(f"  Outage #{idx}")
            print(f"    Start:          {o.start}")
            print(f"    End:            {o.end}")
            print(f"    Duration:       {o.duration_min:.1f} minutes")
            print(f"    Pre-outage:     {o.pre_outage_power:.0f}W")
            # Rough energy lost estimate: assume linear decay, avg of pre-outage and 0
            # over the outage duration. Rough but useful.
            hours = o.duration_min / 60.0
            est_kwh = (o.pre_outage_power / 2000) * hours  # rough: ~2kW avg → linear
            print(f"    Est. lost:      ~{est_kwh:.2f} kWh")
            print()
    else:
        print("  No outages detected.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Detect power outages from solar data")
    parser.add_argument("--date", help="YYYY-MM-DD (default: today)")
    parser.add_argument("--db", help="Path to solar_data.db", default=DB_PATH)
    parser.add_argument("--zero-threshold", type=float, default=POWER_ZERO_THRESHOLD)
    parser.add_argument("--active-threshold", type=float, default=ACTIVE_SUN_THRESHOLD)
    parser.add_argument("--lookback", type=int, default=ACTIVE_SUN_LOOKBACK)
    parser.add_argument("--min-zeros", type=int, default=MIN_CONSECUTIVE_ZEROS)
    parser.add_argument("--history", action="store_true", help="Show last 7 days")
    args = parser.parse_args()

    if args.history:
        for i in range(7):
            d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            rows = fetch_day(d, args.db)
            if rows:
                outages = detect_outages(
                    rows,
                    zero_threshold=args.zero_threshold,
                    active_threshold=args.active_threshold,
                    lookback=args.lookback,
                    min_zeros=args.min_zeros,
                )
                marker = "✗" if outages else "✓"
                print(f"{marker} {d}: {len(outages)} outage(s)")
        return

    report(args.date, args.db)


if __name__ == "__main__":
    main()
