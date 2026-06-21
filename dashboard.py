#!/usr/bin/env python3
"""
Solar Dashboard - Enhanced web UI with improved charts
"""

import sqlite3
import json
import math
from datetime import datetime, timedelta, timezone

# IST timezone (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))

# ---------- Outage Detection ----------

def detect_outages(rows, zero_thresh=10.0, active_thresh=200.0, lookback=3, min_zeros=3):
    """
    Detect power outages from ordered (timestamp, power, ...) rows.
    A grid outage = sustained 0W during confirmed active sun hours.
    """
    if not rows:
        return []

    outages = []
    zero_streak = 0
    streak_start = None

    for i, row in enumerate(rows):
        ts, power = row[0], row[1]
        if power is None:
            power = 0

        if power <= zero_thresh:
            if zero_streak == 0:
                streak_start = i
            zero_streak += 1
        else:
            if zero_streak >= min_zeros and streak_start is not None:
                look_start = max(0, streak_start - lookback)
                active_before = any(rows[j][1] > active_thresh for j in range(look_start, streak_start))
                if active_before:
                    start_ts = rows[streak_start][0]
                    end_ts = ts
                    duration = (datetime.fromisoformat(end_ts) - datetime.fromisoformat(start_ts)).total_seconds() / 60.0
                    pre_power = rows[streak_start - 1][1] if streak_start > 0 else 0
                    outages.append({
                        "start": start_ts, "end": end_ts,
                        "duration_min": round(duration, 1),
                        "pre_power": pre_power,
                    })
            zero_streak = 0
            streak_start = None

    # Ongoing at end
    if zero_streak >= min_zeros and streak_start is not None:
        look_start = max(0, streak_start - lookback)
        active_before = any(rows[j][1] > active_thresh for j in range(look_start, streak_start))
        if active_before:
            start_ts = rows[streak_start][0]
            end_ts = rows[-1][0]
            duration = (datetime.fromisoformat(end_ts) - datetime.fromisoformat(start_ts)).total_seconds() / 60.0
            pre_power = rows[streak_start - 1][1] if streak_start > 0 else 0
            outages.append({
                "start": start_ts, "end": end_ts,
                "duration_min": round(duration, 1),
                "pre_power": pre_power,
            })

    return outages


def get_day_outages(target_date=None):
    """Return outage list + count for a given date (default today IST)."""
    if target_date is None:
        target_date = today_ist()

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT timestamp, power_now FROM live_readings
        WHERE date(timestamp) = ?
        ORDER BY timestamp
    """, (target_date,))
    rows = c.fetchall()
    conn.close()
    outages = detect_outages(rows)
    return {"date": target_date, "count": len(outages), "outages": outages}


def get_outage_history(days=7, start_date=None, end_date=None):
    """Return outage summaries for a date range or last N days."""
    if start_date and end_date:
        # Single query for the entire range, then group by day (efficient for large ranges)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT timestamp, power_now FROM live_readings
            WHERE date(timestamp) BETWEEN ? AND ?
            ORDER BY timestamp
        """, (start_date, end_date))
        all_rows = c.fetchall()
        conn.close()

        from collections import defaultdict
        day_rows = defaultdict(list)
        for ts, power in all_rows:
            day = ts[:10]  # YYYY-MM-DD
            day_rows[day].append((ts, power))

        results = []
        # Return newest-first to maintain backward compatibility
        for day in sorted(day_rows.keys(), reverse=True):
            outages = detect_outages(day_rows[day])
            results.append({"date": day, "count": len(outages), "outages": outages})
        return results

    # Fallback: day-by-day for small N-days queries
    results = []
    for i in range(days):
        d = (datetime.now(IST).date() - timedelta(days=i)).strftime("%Y-%m-%d")
        day_data = get_day_outages(d)
        results.append(day_data)
    return results


def get_outage_chart_data(days=14, start_date=None, end_date=None):
    """Return labels and minutes arrays for outage history chart."""
    history = get_outage_history(days, start_date, end_date)
    labels = []
    minutes = []
    counts = []
    for h in reversed(history):  # oldest first for charts
        labels.append(h["date"][5:])  # MM-DD
        total_min = sum(o["duration_min"] for o in h["outages"])
        minutes.append(total_min)
        counts.append(h["count"])
    return labels, minutes, counts

# ---------- Timezone Helpers ----------

def now_ist():
    """Get current time in IST."""
    return datetime.now(IST)

def utc_to_ist(dt_str):
    """Convert UTC timestamp string to IST."""
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST)
    except:
        return dt_str

def fmt_ist(dt_str):
    """Format timestamp as IST HH:MM."""
    ist = utc_to_ist(dt_str)
    if isinstance(ist, str):
        return ist[11:16] if len(ist) > 16 else ist
    return ist.strftime("%H:%M")

def fmt_ist_date(dt_str):
    """Format date as IST DD-MM."""
    ist = utc_to_ist(dt_str)
    if isinstance(ist, str):
        return ist[5:10] if len(ist) > 10 else ist
    return ist.strftime("%d-%m")

def today_ist():
    """Get today's date in IST as YYYY-MM-DD."""
    return now_ist().strftime("%Y-%m-%d")

def date_range_ist(days_back):
    """Get date range for IST."""
    end = now_ist().date()
    start = end - timedelta(days=days_back)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

DB_PATH = Path(__file__).parent / "solar_data.db"
PORT = 8765
CAPACITY_KW = 5.0


def get_latest_data():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    today = today_ist()
    
    cursor.execute("""
        SELECT timestamp, power_now, energy_today, energy_total, status
        FROM live_readings
        WHERE date(timestamp) = ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (today,))
    row = cursor.fetchone()
    
    cursor.execute("""
        SELECT energy_month, energy_year FROM daily_summary
        WHERE date = ?
    """, (today,))
    summary = cursor.fetchone()
    
    conn.close()
    
    if row:
        power = row[1] or 0
        outages = get_day_outages(today)
        return {
            "timestamp": fmt_ist(row[0]),
            "power_now": power,
            "today_kwh": row[2] or 0,
            "month_kwh": summary[0] if summary else 0,
            "year_kwh": summary[1] if summary else 0,
            "total_kwh": row[3] or 0,
            "status": row[4] or "Unknown",
            "efficiency": (power / (CAPACITY_KW * 1000)) * 100,
            "yesterday_kwh": get_yesterday_energy(),
            "outage_count": outages["count"],
            "outages": outages["outages"],
        }
    return None


def get_day_curve(target_date=None):
    """Get power curve for a specific day (default: today in IST)."""
    if target_date is None:
        target_date = today_ist()
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT timestamp, power_now
        FROM live_readings
        WHERE date(timestamp) = ?
        ORDER BY timestamp
    """, (target_date,))
    rows = cursor.fetchall()
    conn.close()
    
    labels = [fmt_ist(r[0]) for r in rows]
    data = [r[1] for r in rows]
    return labels, data, len(rows)


def get_daily_history(days=14, start_date=None, end_date=None):
    """Get daily energy from live_readings (max energy_today per day)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if start_date and end_date:
        cursor.execute("""
            SELECT date(timestamp) as day, MAX(energy_today) as max_energy
            FROM live_readings
            WHERE date(timestamp) BETWEEN ? AND ?
            GROUP BY day
            ORDER BY day
        """, (start_date, end_date))
    else:
        cursor.execute("""
            SELECT date(timestamp) as day, MAX(energy_today) as max_energy
            FROM live_readings
            WHERE date(timestamp) >= date('now', '-{} days')
            GROUP BY day
            ORDER BY day
        """.format(days))

    rows = cursor.fetchall()
    conn.close()

    labels = [r[0][5:] for r in rows]  # MM-DD
    data = [r[1] or 0 for r in rows]
    return labels, data


def get_day_summary(target_date=None):
    """Get summary for a specific day."""
    if target_date is None:
        target_date = today_ist()
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            MIN(power_now), MAX(power_now), AVG(power_now),
            MAX(energy_today), COUNT(*)
        FROM live_readings
        WHERE date(timestamp) = ?
    """, (target_date,))
    row = cursor.fetchone()
    
    cursor.execute("""
        SELECT energy_today, energy_month, energy_year, energy_total
        FROM daily_summary WHERE date = ?
    """, (target_date,))
    summary = cursor.fetchone()
    conn.close()
    
    if not row or row[4] == 0:
        return None
    
    # Use daily_summary if it has valid data, otherwise fall back to live_readings max
    live_energy_today = row[3] or 0
    summary_energy = summary[0] if summary else 0
    # daily_summary can have stale 0.0 when live_readings captured actual generation
    total_kwh = summary_energy if summary_energy > 0 else live_energy_today
    
    return {
        "date": target_date,
        "min_w": row[0] or 0,
        "max_w": row[1] or 0,
        "avg_w": round(row[2] or 0, 1),
        "readings": row[4],
        "total_kwh": total_kwh,
        "month_kwh": summary[1] if summary else 0,
        "year_kwh": summary[2] if summary else 0,
        "lifetime_kwh": summary[3] if summary else 0,
    }


def get_available_days():
    """Get list of days with data."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT date(timestamp) FROM live_readings ORDER BY date(timestamp) DESC")
    rows = [r[0] for r in cursor.fetchall()]
    conn.close()
    return rows


def get_weekly_data(days=30, start_date=None, end_date=None):
    """Get weekly energy totals from live_readings (max energy_today per day, summed by week)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if start_date and end_date:
        cursor.execute("""
            SELECT week, SUM(daily_max) as weekly_energy
            FROM (
                SELECT strftime('%Y-W%W', date(timestamp)) as week,
                       MAX(energy_today) as daily_max
                FROM live_readings
                WHERE date(timestamp) BETWEEN ? AND ?
                GROUP BY date(timestamp)
            )
            GROUP BY week
            ORDER BY week
        """, (start_date, end_date))
    else:
        cursor.execute("""
            SELECT week, SUM(daily_max) as weekly_energy
            FROM (
                SELECT strftime('%Y-W%W', date(timestamp)) as week,
                       MAX(energy_today) as daily_max
                FROM live_readings
                WHERE date(timestamp) >= date('now', '-{} days')
                GROUP BY date(timestamp)
            )
            GROUP BY week
            ORDER BY week
        """.format(days))

    rows = cursor.fetchall()
    conn.close()

    labels = [r[0] for r in rows]
    data = [r[1] or 0 for r in rows]
    return labels, data


def get_hourly_averages(target_date=None):
    """Average power (W) per IST hour (0-23) for a day. Returns (labels, data, has_real_data)."""
    if target_date is None:
        target_date = today_ist()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, power_now FROM live_readings
        WHERE date(timestamp) = ? ORDER BY timestamp
    """, (target_date,))
    rows = cursor.fetchall()
    conn.close()

    buckets = {h: [] for h in range(24)}
    for ts, power in rows:
        ist = utc_to_ist(ts)
        if isinstance(ist, datetime):
            buckets[ist.hour].append(power or 0)

    labels = [f"{h}h" for h in range(24)]
    data = [round(sum(buckets[h]) / len(buckets[h]), 1) if buckets[h] else 0 for h in range(24)]
    has_real = any(v > 0 for v in data)
    return labels, data, has_real


def mock_peak_data():
    """Deterministic typical solar curve when no hourly data exists."""
    labels, data = [], []
    for i in range(24):
        labels.append(f"{i}h")
        if i < 6 or i > 19:
            data.append(0)
        else:
            dist = math.sin((i - 6) / 13 * math.pi)
            data.append(round(5000 * dist * 0.85))
    return labels, data


def get_yesterday_energy():
    """Get yesterday's total energy from live_readings (max energy_today for that day)."""
    yesterday = (now_ist().date() - timedelta(days=1)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(energy_today) FROM live_readings WHERE date(timestamp) = ?", (yesterday,))
    row = cursor.fetchone()
    conn.close()
    # Filter out bogus 0 values — if the max is 0, there were no real readings
    return row[0] if row and row[0] and row[0] > 0 else None


def format_yield_delta(today_kwh, yesterday_kwh):
    if yesterday_kwh is None:
        return ""
    delta = today_kwh - yesterday_kwh
    if abs(delta) < 0.05:
        return '<div class="yield-delta yield-neutral">→ same as yesterday</div>'
    if delta > 0:
        return f'<div class="yield-delta yield-up">↑ +{delta:.1f} kWh vs yesterday</div>'
    return f'<div class="yield-delta yield-down">↓ {delta:.1f} kWh vs yesterday</div>'


def get_available_date_range():
    """Return (min_date, max_date) from live_readings, or (today, today) if empty."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT MIN(date(timestamp)), MAX(date(timestamp)) FROM live_readings")
    row = cursor.fetchone()
    conn.close()
    min_date = row[0] if row and row[0] else today_ist()
    max_date = row[1] if row and row[1] else today_ist()
    return min_date, max_date


def resolve_range(params):
    """Convert frontend range preset to (start_date, end_date, range_label, range_preset)."""
    preset = params.get("range", "14d")
    today = today_ist()

    if preset == "custom":
        start = params.get("start", today)
        end = params.get("end", today)
        if start > end:
            start, end = end, start
        label = f"{start} to {end}"
        return start, end, label, preset

    if preset == "7d":
        start = (now_ist().date() - timedelta(days=6)).strftime("%Y-%m-%d")
        return start, today, "Last 7 Days", preset
    if preset == "14d":
        start = (now_ist().date() - timedelta(days=13)).strftime("%Y-%m-%d")
        return start, today, "Last 14 Days", preset
    if preset == "1m":
        start = (now_ist().date() - timedelta(days=29)).strftime("%Y-%m-%d")
        return start, today, "Last 30 Days", preset
    if preset == "6m":
        start = (now_ist().date() - timedelta(days=182)).strftime("%Y-%m-%d")
        return start, today, "Last 6 Months", preset
    if preset == "1y":
        start = (now_ist().date() - timedelta(days=364)).strftime("%Y-%m-%d")
        return start, today, "Last Year", preset
    if preset == "5y":
        start = (now_ist().date() - timedelta(days=1825)).strftime("%Y-%m-%d")
        return start, today, "Last 5 Years", preset
    if preset == "ytd":
        year = today[:4]
        start = f"{year}-01-01"
        return start, today, "Year to Date", preset
    if preset == "all":
        start, end = get_available_date_range()
        return start, end, "All Time", preset

    # Default: 14 days
    start = (now_ist().date() - timedelta(days=13)).strftime("%Y-%m-%d")
    return start, today, "Last 14 Days", "14d"


def build_main_page(data, power_labels, power_data, daily_labels, daily_data, weekly_labels, weekly_data, available_days, peak_labels, peak_data, yesterday_kwh=None, outage_labels=None, outage_minutes=None, outage_counts=None, days=14, range_label="Last 14 Days", range_preset="14d", start_date=None, end_date=None):
    pl = json.dumps(power_labels)
    pd = json.dumps(power_data)
    dl = json.dumps(daily_labels)
    dd = json.dumps(daily_data)
    wl = json.dumps(weekly_labels)
    wd = json.dumps(weekly_data)
    phl = json.dumps(peak_labels)
    phd = json.dumps(peak_data)
    days_json = json.dumps(available_days)
    ol = json.dumps(outage_labels) if outage_labels else "[]"
    om = json.dumps(outage_minutes) if outage_minutes else "[]"
    oc = json.dumps(outage_counts) if outage_counts else "[]"
    yield_delta = format_yield_delta(data.get("today_kwh", 0), yesterday_kwh)
    today_date = today_ist()
    start_val = start_date or today_date
    end_val = end_date or today_date
    
    sel_7d = ' selected' if range_preset == '7d' else ''
    sel_14d = ' selected' if range_preset == '14d' else ''
    sel_1m = ' selected' if range_preset == '1m' else ''
    sel_6m = ' selected' if range_preset == '6m' else ''
    sel_1y = ' selected' if range_preset == '1y' else ''
    sel_5y = ' selected' if range_preset == '5y' else ''
    sel_ytd = ' selected' if range_preset == 'ytd' else ''
    sel_all = ' selected' if range_preset == 'all' else ''
    sel_custom = ' selected' if range_preset == 'custom' else ''
    
    days_options = "\n".join(
        '<option value="{}"{}>{}</option>'.format(d, " selected" if d == today_date else "", d)
        for d in available_days
    )
    
    gauge_pct = data.get("efficiency", 0)
    status_cls = "status-online" if data.get("status") == "Online" else "status-offline"
    
    outages = data.get("outages", [])
    outage_last = ""
    if outages:
        outage_last = '<div class="yield-delta yield-down">Last: {} IST</div>'.format(fmt_ist(outages[-1]["start"]))
    
    html = '''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Solar Monitor - Chauhan Residence</title>
    <link rel="manifest" href="manifest.json">
    <meta name="theme-color" content="#0f172a">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <script>
        if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js');
        let deferredPrompt;
        window.addEventListener('beforeinstallprompt', (e) => { deferredPrompt = e; document.getElementById('install-btn').style.display = 'inline-block'; });
        function installPWA() { if (deferredPrompt) { deferredPrompt.prompt(); deferredPrompt = null; } }
    </script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; padding: 16px; }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 { font-size: 1.5rem; margin-bottom: 4px; color: #fbbf24; }
        .hero { display: flex; align-items: center; gap: 24px; margin-bottom: 24px; flex-wrap: wrap; }
        .gauge-container { position: relative; width: 200px; height: 150px; flex-shrink: 0; }
        .gauge-value { position: absolute; top: 55%; left: 50%; transform: translate(-50%, -50%); text-align: center; }
        .gauge-watts { font-size: 2rem; font-weight: 700; color: #fbbf24; }
        .gauge-label { font-size: 0.75rem; color: #64748b; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; flex: 1; }
        .stat-card { background: #1e293b; border-radius: 10px; padding: 16px; border: 1px solid #334155; }
        .stat-label { font-size: 0.7rem; color: #64748b; text-transform: uppercase; margin-bottom: 6px; }
        .stat-value { font-size: 1.5rem; font-weight: 700; color: #fbbf24; }
        .stat-unit { font-size: 0.8rem; color: #64748b; }
        .status-online { color: #4ade80; }
        .status-offline { color: #ef4444; }
        .outage-card { border-left: 3px solid #ef4444; }
        .yield-delta { font-size: 0.75rem; margin-top: 4px; }
        .yield-up { color: #4ade80; } .yield-down { color: #ef4444; } .yield-neutral { color: #94a3b8; }
        .efficiency-bar { width: 100%; height: 8px; background: #334155; border-radius: 4px; overflow: hidden; margin-top: 8px; }
        .efficiency-fill { height: 100%; background: linear-gradient(90deg, #4ade80, #fbbf24, #ef4444); border-radius: 4px; }
        .charts-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 16px; margin-bottom: 16px; }
        .chart-card { background: #1e293b; border-radius: 12px; padding: 20px; border: 1px solid #334155; }
        .chart-title { font-size: 0.85rem; color: #94a3b8; margin-bottom: 12px; }
        .info-footer { color: #475569; font-size: 0.75rem; margin-top: 16px; text-align: center; }
        select { background: #1e293b; color: #e2e8f0; border: 1px solid #334155; padding: 8px 12px; border-radius: 6px; font-size: 0.9rem; cursor: pointer; }
        button { background: #4ade80; color: #0f172a; border: none; padding: 8px 16px; border-radius: 6px; font-weight: 600; cursor: pointer; }
        @media (max-width: 600px) { .hero { flex-direction: column; } .gauge-container { width: 160px; height: 120px; } .charts-grid { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
    <div class="container">
        <h1>☀️ Solar Monitor</h1>
        <div style="margin-bottom: 16px;">
            <form method="get" action="./day" style="display: inline;">
                <select name="date" onchange="this.form.submit()">
                    <option value="">📅 Browse historical days...</option>
''' + days_options + '''
                </select>
            </form>
            <button id="install-btn" onclick="installPWA()" style="display: none; margin-left: 8px;">📲 Install</button>
            <form method="get" action="." style="display: inline; margin-left: 8px;">
                <select name="range" onchange="handleRangeChange(this)">
                    <option value="7d"''' + sel_7d + '''>7 Days</option>
                    <option value="14d"''' + sel_14d + '''>14 Days</option>
                    <option value="1m"''' + sel_1m + '''>1 Month</option>
                    <option value="6m"''' + sel_6m + '''>6 Months</option>
                    <option value="1y"''' + sel_1y + '''>1 Year</option>
                    <option value="5y"''' + sel_5y + '''>5 Years</option>
                    <option value="ytd"''' + sel_ytd + '''>YTD</option>
                    <option value="all"''' + sel_all + '''>All Time</option>
                    <option value="custom"''' + sel_custom + '''>Custom Range</option>
                </select>
                <span id="custom-range" style="display: none; margin-left: 4px;">
                    <input type="date" name="start" value="''' + start_val + '''" style="background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:6px 8px;font-size:0.85rem;">
                    <span style="color:#64748b;">to</span>
                    <input type="date" name="end" value="''' + end_val + '''" style="background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:6px 8px;font-size:0.85rem;">
                </span>
                <button type="submit" style="padding: 6px 12px; margin-left: 4px; font-size: 0.8rem;">Apply</button>
            </form>
            <span style="color: #64748b; font-size: 0.8rem; margin-left: 8px;">IST (UTC+5:30)</span>
            <script>
                function handleRangeChange(select) {
                    document.getElementById('custom-range').style.display = select.value === 'custom' ? 'inline' : 'none';
                }
                // Initialize on load
                if (document.querySelector('select[name="range"]')) {
                    handleRangeChange(document.querySelector('select[name="range"]'));
                }
            </script>
        </div>
        <div class="hero">
            <div class="gauge-container">
                <canvas id="gaugeChart"></canvas>
                <div class="gauge-value">
                    <div class="gauge-watts">''' + '{:.0f}W'.format(data.get("power_now", 0)) + '''</div>
                    <div class="gauge-label">''' + '{:.1f}% of 5kW'.format(data.get("efficiency", 0)) + '''</div>
                </div>
            </div>
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-label">Today's Energy</div>
                    <div class="stat-value">''' + '{:.1f}'.format(data.get("today_kwh", 0)) + '''<span class="stat-unit">kWh</span></div>
                    ''' + yield_delta + '''
                    <div class="efficiency-bar"><div class="efficiency-fill" style="width: ''' + '{:.0f}'.format(min(data.get("efficiency", 0) * 5, 100)) + '''%"></div></div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">This Month</div>
                    <div class="stat-value">''' + '{:.0f}'.format(data.get("month_kwh", 0)) + '''<span class="stat-unit">kWh</span></div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">This Year</div>
                    <div class="stat-value">''' + '{:.0f}'.format(data.get("year_kwh", 0)) + '''<span class="stat-unit">kWh</span></div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Lifetime</div>
                    <div class="stat-value">''' + '{:.0f}'.format(data.get("total_kwh", 0)) + '''<span class="stat-unit">kWh</span></div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Status</div>
                    <div class="stat-value ''' + status_cls + '''">&#9679; ''' + (data.get("status", "Unknown")) + '''</div>
                </div>
                <div class="stat-card outage-card">
                    <div class="stat-label">Power Cuts Today</div>
                    <div class="stat-value">''' + str(data.get("outage_count", 0)) + '''</div>
                    ''' + outage_last + '''
                </div>
            </div>
        </div>
        <div class="charts-grid">
            <div class="chart-card"><div class="chart-title">Today's Power Curve</div><canvas id="powerChart" height="120"></canvas></div>
            <div class="chart-card"><div class="chart-title">Daily Energy (''' + range_label + ''')</div><canvas id="dailyChart" height="120"></canvas></div>
            <div class="chart-card"><div class="chart-title">Weekly Totals (''' + range_label + ''')</div><canvas id="weeklyChart" height="120"></canvas></div>
            <div class="chart-card"><div class="chart-title">Peak Hours Analysis</div><canvas id="peakChart" height="120"></canvas></div>
            <div class="chart-card"><div class="chart-title">Outage History (''' + range_label + ''')</div><canvas id="outageChart" height="80"></canvas></div>
        </div>
        <p class="info-footer">Last update: ''' + str(data.get("timestamp", "")) + ''' | Plant ID: 1250826 | SN: KSY0424HT3322<br><a href="./outages" style="color: #64748b;">View full outage log →</a></p>
    </div>
    <script>
        Chart.defaults.color = '#94a3b8'; Chart.defaults.borderColor = '#334155';
        new Chart(document.getElementById('gaugeChart').getContext('2d'), {
            type: 'doughnut', data: { labels: ['Generating','Idle'], datasets: [{ data: [''' + '{:.1f},{:.1f}'.format(gauge_pct, 100-gauge_pct) + '''], backgroundColor: ['#fbbf24','#1e293b'], borderWidth: 0, circumference: 180, rotation: 270 }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, cutout: '85%' }
        });
        const pCtx = document.getElementById('powerChart').getContext('2d');
        const pGrad = pCtx.createLinearGradient(0,0,0,300); pGrad.addColorStop(0,'rgba(251,191,36,0.4)'); pGrad.addColorStop(1,'rgba(251,191,36,0)');
        new Chart(pCtx, { type: 'line', data: { labels: ''' + pl + ''', datasets: [{ label:'Power (W)', data:''' + pd + ''', borderColor:'#fbbf24', backgroundColor:pGrad, fill:true, tension:0.4, pointRadius:0 }] },
            options: { responsive:true, plugins:{legend:{display:false}}, scales:{y:{beginAtZero:true,max:5500},x:{ticks:{maxTicksLimit:8}}}, interaction:{intersect:false,mode:'index'} }
        });
        new Chart(document.getElementById('dailyChart').getContext('2d'), { type: 'bar', data: { labels: ''' + dl + ''', datasets: [{ label:'Energy (kWh)', data:''' + dd + ''', backgroundColor:'#4ade80', borderRadius:4 }] }, options: { responsive:true, plugins:{legend:{display:false}}, scales:{y:{beginAtZero:true}} }});
        new Chart(document.getElementById('weeklyChart').getContext('2d'), { type: 'bar', data: { labels: ''' + wl + ''', datasets: [{ label:'Weekly (kWh)', data:''' + wd + ''', backgroundColor:'#60a5fa', borderRadius:4 }] }, options: { responsive:true, plugins:{legend:{display:false}}, indexAxis:'y' }});
        function peakBarColor(v){ return v>4000?'#fbbf24':v>2000?'#4ade80':'#64748b'; }
        new Chart(document.getElementById('peakChart').getContext('2d'), { type: 'bar', data: { labels: ''' + phl + ''', datasets: [{ label:'Avg Power (W)', data:''' + phd + ''', backgroundColor:(ctx)=>peakBarColor(ctx.raw), borderRadius:2 }] }, options: { responsive:true, plugins:{legend:{display:false}}, scales:{y:{beginAtZero:true,display:false},x:{ticks:{maxTicksLimit:8}}} }});
        const outColors = ''' + oc + '''.map(c => c>0 ? '#ef4444' : '#334155');
        new Chart(document.getElementById('outageChart').getContext('2d'), { type: 'bar', data: { labels: ''' + ol + ''', datasets: [{ label:'Outage Minutes', data:''' + om + ''', backgroundColor:outColors, borderRadius:4 }] },
            options: { responsive:true, onClick:(e,el)=>{ if(el.length>0){ const idx=el[0].index; const d=''' + ol + '''[idx]; if(d) location.href='./day?date=2026-'+d; } }, plugins:{legend:{display:false},tooltip:{callbacks:{label:(ctx)=>{ const c=''' + oc + '''[ctx.dataIndex]; const m=ctx.raw; if(m===0) return 'No outages'; return c+' cut(s), '+m+' min ('+(m/60).toFixed(1)+'h)'; }} }}, scales:{y:{beginAtZero:true,display:false},x:{ticks:{maxTicksLimit:14,font:{size:10}}}} }
        });
        setTimeout(()=>location.reload(),60000);
    </script>
</body>
</html>'''
    return html


def build_outages_page(history, total_minutes, avg_per_day, max_day, days=14, range_label="Last 14 Days"):
    rows = []
    for h in history:
        for o in h["outages"]:
            rows.append({"date": h["date"], "start": fmt_ist(o["start"]), "end": fmt_ist(o["end"]), "duration_min": o["duration_min"], "pre_power": o["pre_power"]})
    ol = json.dumps([h["date"][5:] for h in reversed(history)])
    om = json.dumps([sum(o["duration_min"] for o in h["outages"]) for h in reversed(history)])
    oc = json.dumps([h["count"] for h in reversed(history)])
    if rows:
        table_rows = "\n".join('<tr><td style="padding:10px 8px;border-bottom:1px solid #1e293b;">{}</td><td style="padding:10px 8px;border-bottom:1px solid #1e293b;">{}</td><td style="padding:10px 8px;border-bottom:1px solid #1e293b;">{}</td><td style="padding:10px 8px;border-bottom:1px solid #1e293b;">{:.0f} min</td><td style="padding:10px 8px;border-bottom:1px solid #1e293b;">{:.0f}W</td></tr>'.format(r["date"], r["start"], r["end"], r["duration_min"], r["pre_power"]) for r in rows)
    else:
        table_rows = '<tr><td colspan="5" style="text-align:center;padding:30px;color:#64748b;">No outages recorded</td></tr>'
    return '''<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Power Outages - Solar Monitor</title><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script><style>*{margin:0;padding:0;box-sizing:border-box;}body{background:#0f172a;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;padding:20px;}.header{text-align:center;margin-bottom:24px;}.header h1{font-size:1.6rem;color:#fbbf24;}.subtitle{color:#94a3b8;font-size:0.85rem;}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:24px;}.stat-card{background:#1e293b;border-radius:12px;padding:16px;text-align:center;}.stat-label{color:#94a3b8;font-size:0.75rem;text-transform:uppercase;margin-bottom:6px;}.stat-value{font-size:1.4rem;font-weight:700;color:#fbbf24;}.chart-card{background:#1e293b;border-radius:12px;padding:16px;margin-bottom:20px;}.chart-title{font-size:0.85rem;color:#94a3b8;margin-bottom:12px;}table{width:100%;border-collapse:collapse;font-size:0.85rem;}th{text-align:left;padding:10px 8px;color:#94a3b8;border-bottom:2px solid #334155;font-weight:600;}a{color:#fbbf24;text-decoration:none;}.back{display:inline-block;margin-bottom:16px;color:#94a3b8;}</style></head><body><a href="." class="back">&larr; Back to Dashboard</a><div class="header"><h1>Power Outages</h1><p class="subtitle">Chauhan Residence | ''' + range_label + '''</p></div><div class="stats"><div class="stat-card"><div class="stat-label">Total Outages</div><div class="stat-value">''' + str(len(rows)) + '''<span style="color:#64748b;font-size:0.85rem;margin-left:2px;">events</span></div></div><div class="stat-card"><div class="stat-label">Total Duration</div><div class="stat-value">''' + '{:.0f}'.format(total_minutes) + '''<span style="color:#64748b;font-size:0.85rem;margin-left:2px;">min</span></div></div><div class="stat-card"><div class="stat-label">Avg per Day</div><div class="stat-value">''' + '{:.1f}'.format(avg_per_day) + '''<span style="color:#64748b;font-size:0.85rem;margin-left:2px;">outages</span></div></div><div class="stat-card"><div class="stat-label">Worst Day</div><div class="stat-value">''' + str(max_day) + '''<span style="color:#64748b;font-size:0.85rem;margin-left:2px;">events</span></div></div></div><div class="chart-card"><div class="chart-title">Outage History (Minutes)</div><canvas id="outageChart"></canvas></div><div class="chart-card"><div class="chart-title">Detailed Log</div><table><thead><tr><th>Date</th><th>Start</th><th>End</th><th>Duration</th><th>Pre-Power</th></tr></thead><tbody>''' + table_rows + '''</tbody></table></div><script>const outColors=''' + oc + '''.map(c=>c>0?'#ef4444':'#334155');new Chart(document.getElementById('outageChart').getContext('2d'),{type:'bar',data:{labels:''' + ol + ''',datasets:[{label:'Minutes',data:''' + om + ''',backgroundColor:outColors,borderRadius:4}]},options:{responsive:true,plugins:{legend:{display:false},tooltip:{callbacks:{label:(ctx)=>{const c=''' + oc + '''[ctx.dataIndex];const m=ctx.raw;if(m===0)return'No outages';return c+' cut(s), '+m+' min ('+(m/60).toFixed(1)+'h)';}}}},scales:{y:{beginAtZero:true,display:false},x:{ticks:{maxTicksLimit:14,font:{size:10}}}}}});</script></body></html>'''


def build_day_page(date, labels, data, outages, summary, prev_day, next_day, available_days):
    prev_link = '<a href="./day?date={}" style="color:#94a3b8;">&larr; Prev</a>'.format(prev_day) if prev_day else '<span style="color:#334155;">&larr; Prev</span>'
    next_link = '<a href="./day?date={}" style="color:#94a3b8;">Next &rarr;</a>'.format(next_day) if next_day else '<span style="color:#334155;">Next &rarr;</span>'
    days_opts = "\n".join('<option value="{}"{}>{}</option>'.format(d, " selected" if d == date else "", d) for d in available_days)
    if outages:
        out_rows = "\n".join('<tr><td>{}</td><td>{}</td><td>{:.0f} min</td></tr>'.format(fmt_ist(o["start"]), fmt_ist(o["end"]), o["duration_min"]) for o in outages)
    else:
        out_rows = "<tr><td colspan='3' style='text-align:center;color:#64748b;'>No power cuts detected</td></tr>"
    pl = json.dumps(labels)
    pd = json.dumps(data)
    return '''<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>''' + date + ''' - Solar Monitor</title><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script><style>*{margin:0;padding:0;box-sizing:border-box;}body{background:#0f172a;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;padding:20px;}.header{text-align:center;margin-bottom:20px;}.header h1{font-size:1.4rem;color:#fbbf24;}.nav{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:20px;}.stat-card{background:#1e293b;border-radius:10px;padding:14px;text-align:center;}.stat-label{color:#64748b;font-size:0.7rem;text-transform:uppercase;margin-bottom:4px;}.stat-value{font-size:1.2rem;font-weight:700;color:#fbbf24;}.chart-card{background:#1e293b;border-radius:10px;padding:14px;margin-bottom:16px;}.chart-title{font-size:0.8rem;color:#94a3b8;margin-bottom:10px;}table{width:100%;border-collapse:collapse;font-size:0.8rem;}th{text-align:left;padding:8px;color:#94a3b8;border-bottom:2px solid #334155;}td{padding:8px;border-bottom:1px solid #1e293b;}select{background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:6px 10px;font-size:0.85rem;}</style></head><body><div class="nav">''' + prev_link + '''<form method="get" action="./day" style="margin:0;"><select name="date" onchange="this.form.submit()">''' + days_opts + '''</select></form>''' + next_link + '''</div><div class="header"><h1>''' + date + '''</h1><p style="color:#94a3b8;font-size:0.8rem;">Chauhan Residence | Historical Day View</p></div><div class="stats"><div class="stat-card"><div class="stat-label">Peak Power</div><div class="stat-value">''' + '{:.0f}'.format(summary.get("max_w", 0)) + '''W</div></div><div class="stat-card"><div class="stat-label">Avg Power</div><div class="stat-value">''' + '{:.0f}'.format(summary.get("avg_w", 0)) + '''W</div></div><div class="stat-card"><div class="stat-label">Min Power</div><div class="stat-value">''' + '{:.0f}'.format(summary.get("min_w", 0)) + '''W</div></div><div class="stat-card"><div class="stat-label">Energy</div><div class="stat-value">''' + '{:.1f}'.format(summary.get("total_kwh", 0)) + '''kWh</div></div><div class="stat-card"><div class="stat-label">Readings</div><div class="stat-value">''' + str(summary.get("readings", 0)) + '''</div></div><div class="stat-card"><div class="stat-label">Outages</div><div class="stat-value">''' + str(len(outages)) + '''</div></div></div><div class="chart-card"><div class="chart-title">Power Curve</div><canvas id="dayChart" height="120"></canvas></div><div class="chart-card"><div class="chart-title">Power Cuts</div><table><thead><tr><th>Start</th><th>End</th><th>Duration</th></tr></thead><tbody>''' + out_rows + '''</tbody></table></div><div style="text-align:center;margin-top:20px;"><a href="." style="color:#94a3b8;">&larr; Back to Dashboard</a></div><script>new Chart(document.getElementById('dayChart').getContext('2d'),{type:'line',data:{labels:''' + pl + ''',datasets:[{label:'Power (W)',data:''' + pd + ''',borderColor:'#fbbf24',backgroundColor:'rgba(251,191,36,0.15)',fill:true,tension:0.4,pointRadius:0}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{y:{beginAtZero:true}}}});</script></body></html>'''


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        query = self.path.split("?")[1] if "?" in self.path else ""
        params = {}
        for p in query.split("&"):
            if "=" in p:
                k, v = p.split("=", 1)
                params[k] = v

        # Removed auth check - open access
        
        if path in ("./", "/", "/index.html", "/solar", "/solar/"):
            start_date, end_date, range_label, range_preset = resolve_range(params)
            days = (datetime.strptime(end_date, "%Y-%m-%d").date() - datetime.strptime(start_date, "%Y-%m-%d").date()).days + 1
            data = get_latest_data()
            if not data:
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(b"<h1>No data yet</h1>")
                return
            
            power_labels, power_data, _ = get_day_curve()
            daily_labels, daily_data = get_daily_history(start_date=start_date, end_date=end_date)
            weekly_labels, weekly_data = get_weekly_data(start_date=start_date, end_date=end_date)
            peak_labels, peak_data, _ = get_hourly_averages()
            if not peak_data or not any(peak_data):
                peak_labels, peak_data = mock_peak_data()
            available_days = get_available_days()
            outage_labels, outage_minutes, outage_counts = get_outage_chart_data(start_date=start_date, end_date=end_date)
            yesterday_kwh = get_yesterday_energy()
            
            html = build_main_page(data, power_labels, power_data, daily_labels, daily_data,
                                   weekly_labels, weekly_data, available_days, peak_labels, peak_data,
                                   yesterday_kwh, outage_labels, outage_minutes, outage_counts,
                                   days=days, range_label=range_label, range_preset=range_preset,
                                   start_date=start_date, end_date=end_date)
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/day"):
            date = params.get("date", today_ist())
            labels, data, count = get_day_curve(date)
            outages = get_day_outages(date)
            summary = get_day_summary(date)
            available_days = get_available_days()
            idx = available_days.index(date) if date in available_days else -1
            prev_day = available_days[idx + 1] if idx >= 0 and idx + 1 < len(available_days) else None
            next_day = available_days[idx - 1] if idx > 0 else None
            
            if summary:
                html = build_day_page(date, labels, data, outages["outages"], summary,
                                       prev_day, next_day, available_days)
            else:
                html = "<h1>No data for {}</h1><a href='.'>&larr; Back</a>".format(date)
            
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/outages"):
            start_date, end_date, range_label, _ = resolve_range(params)
            days = (datetime.strptime(end_date, "%Y-%m-%d").date() - datetime.strptime(start_date, "%Y-%m-%d").date()).days + 1
            history = get_outage_history(start_date=start_date, end_date=end_date)
            total_minutes = sum(
                o["duration_min"] for h in history for o in h["outages"]
            )
            avg_per_day = sum(h["count"] for h in history) / max(days, 1)
            max_day = max((h["count"] for h in history), default=0)
            html = build_outages_page(history, total_minutes, avg_per_day, max_day, days, range_label)
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/api/data"):
            data = get_latest_data()
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))

        elif path.startswith("/api/outages"):
            start_date, end_date, _, _ = resolve_range(params)
            data = get_outage_history(start_date=start_date, end_date=end_date)
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))

        elif path == "/manifest.json":
            manifest = {
                "name": "Solar Monitor",
                "short_name": "Solar",
                "description": "Monitor solar power generation at Chauhan Residence",
                "start_url": ".",
                "display": "standalone",
                "background_color": "#0f172a",
                "theme_color": "#0f172a",
                "orientation": "portrait",
                "scope": ".",
                "icons": [
                    {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                    {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
                ]
            }
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(manifest).encode("utf-8"))

        elif path == "/icon-192.png":
            try:
                with open("icon-192.png", "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-type", "image/png")
                self.send_header("Content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()

        elif path == "/icon-512.png":
            try:
                with open("icon-512.png", "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-type", "image/png")
                self.send_header("Content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()

        elif path == "/sw.js":
            js = b"""const CACHE_NAME = 'solar-monitor-v1';
const CACHE_URLS = ['./','./manifest.json','./icon-192.png','./icon-512.png'];
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(CACHE_URLS))); self.skipWaiting(); });
self.addEventListener('fetch', e => { e.respondWith(caches.match(e.request).then(r => r || fetch(e.request))); });"""
            self.send_response(200)
            self.send_header("Content-type", "application/javascript")
            self.send_header("Content-length", str(len(js)))
            self.end_headers()
            self.wfile.write(js)

        else:
            self.send_response(404)
            self.end_headers()


def run_server(port=PORT):
    from socketserver import ThreadingMixIn
    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        pass
    server = ThreadedHTTPServer(("0.0.0.0", port), DashboardHandler)
    print("Solar dashboard: http://0.0.0.0:{}".format(port))
    print("Tailscale: http://ciphersserver:{}".format(port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=PORT)
    args = parser.parse_args()
    run_server(args.port)
