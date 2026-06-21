#!/usr/bin/env python3
"""
Solar Monitor - ShineMonitor Cloud API Poller
Polls ShineMonitor API and stores data in SQLite.
"""

import hashlib
import requests
import json
import time
import sqlite3
import os
from datetime import datetime, timedelta
from pathlib import Path

# Try to import config, fall back to env vars
try:
    import config
except ImportError:
    class config:
        usr = os.getenv("SHINEMONITOR_USER", "")
        pwd = os.getenv("SHINEMONITOR_PASS", "")
        companykey = os.getenv("SHINEMONITOR_KEY", "")
        plantId = os.getenv("SHINEMONITOR_PLANT", "")
        devcode = os.getenv("SHINEMONITOR_DEVCODE", "")
        pn = os.getenv("SHINEMONITOR_PN", "")
        sn = os.getenv("SHINEMONITOR_SN", "")
        debug = int(os.getenv("DEBUG", "0"))

BASE_URL = "http://web.shinemonitor.com/public/"
DB_PATH = Path(__file__).parent / "solar_data.db"
TOKEN_FILE = Path(__file__).parent / ".token"


def init_db():
    """Create SQLite tables if not exist."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS live_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            plant_id TEXT,
            power_now REAL,
            energy_today REAL,
            energy_total REAL,
            status TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            energy_today REAL,
            energy_month REAL,
            energy_year REAL,
            energy_total REAL,
            peak_power REAL,
            peak_time TEXT,
            recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()
    print(f"Database initialized: {DB_PATH}")


def salt():
    return int(round(time.time() * 1000))


def request_token():
    """Authenticate and get token/secret."""
    s = salt()
    sha1_pwd = hashlib.sha1(config.pwd.encode("utf-8")).hexdigest()
    action = f"&action=auth&usr={config.usr}&company-key={config.companykey}"
    auth_string = f"{s}{sha1_pwd}{action}"
    sign = hashlib.sha1(auth_string.encode("utf-8")).hexdigest()
    url = f"{BASE_URL}?sign={sign}&salt={s}{action}"
    
    if config.debug:
        print(f"Auth URL: {url}")
    
    r = requests.get(url, timeout=30)
    data = r.json()
    
    if data.get("err") != 0:
        raise Exception(f"Auth failed: {data}")
    
    token = data["dat"]["token"]
    secret = data["dat"]["secret"]
    expires_in = data["dat"]["expire"]
    expires_at = datetime.now() + timedelta(seconds=expires_in)
    
    with open(TOKEN_FILE, "w") as f:
        f.write(f"{token}\n{secret}\n{expires_at}\n")
    
    return token, secret, expires_at


def get_token():
    """Get cached token or request new one."""
    try:
        with open(TOKEN_FILE, "r") as f:
            lines = f.readlines()
            token = lines[0].strip()
            secret = lines[1].strip()
            expires = datetime.strptime(lines[2].strip(), "%Y-%m-%d %H:%M:%S.%f")
        if datetime.now() < expires:
            return token, secret
    except (FileNotFoundError, IndexError, ValueError):
        pass
    return request_token()[:2]


def make_request(action_params):
    """Make authenticated API request."""
    token, secret = get_token()
    s = salt()
    req_string = f"{s}{secret}{token}{action_params}"
    sign = hashlib.sha1(req_string.encode("utf-8")).hexdigest()
    url = f"{BASE_URL}?sign={sign}&salt={s}&token={token}{action_params}"
    
    if config.debug:
        print(f"Request: {url}")
    
    r = requests.get(url, timeout=30)
    data = r.json()
    
    if data.get("err") != 0:
        if data.get("err") == 105:
            os.remove(TOKEN_FILE)
            return make_request(action_params)
        raise Exception(f"API error {data.get('err')}: {data}")
    
    return data["dat"]


def fetch_summary():
    """Fetch energy summary."""
    action = f"&action=queryPlantCurrentData&plantid={config.plantId}&par=ENERGY_TODAY,ENERGY_MONTH,ENERGY_YEAR,ENERGY_TOTAL"
    data = make_request(action)
    return {
        "today": float(data[0]["val"]),
        "month": float(data[1]["val"]),
        "year": float(data[2]["val"]),
        "total": float(data[3]["val"]),
    }


def fetch_live_power():
    """Fetch current power output."""
    today = datetime.now().strftime("%Y-%m-%d")
    action = f"&action=queryDeviceDataOneDayPaging&devaddr=1&pn={config.pn}&devcode={config.devcode}&sn={config.sn}&date={today}"
    data = make_request(action)
    
    rows = data.get("row", [])
    if not rows:
        return None
    
    latest = rows[-1]
    fields = latest.get("field", [])
    
    # Field mapping from Ksolare 5G Pro:
    # fields[0] = hash, [1] = timestamp, [2] = SN, [3] = power (W)
    # [4] = today's energy (kWh), [5] = total energy (kWh)
    return {
        "timestamp": fields[1],
        "power": float(fields[3]) if len(fields) > 3 else 0,
        "energy_today": float(fields[4]) if len(fields) > 4 else 0,
        "energy_total": float(fields[5]) if len(fields) > 5 else 0,
    }


def fetch_status():
    """Fetch inverter status."""
    action = f"&action=queryPlantDeviceDesignatedInformation&plantid={config.plantId}&devtype=512"
    try:
        data = make_request(action)
        device = data["device"][0]
        status = "Online" if device["status"] == 0 else "Offline"
        return status
    except:
        return "Unknown"


def store_reading(summary, power_data, status):
    """Store reading in database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO live_readings 
        (plant_id, power_now, energy_today, energy_total, status)
        VALUES (?, ?, ?, ?, ?)
    """, (
        config.plantId,
        power_data["power"] if power_data else 0,
        summary["today"],
        summary["total"],
        status
    ))
    
    conn.commit()
    conn.close()
    
    print(f"[{datetime.now()}] Stored: {power_data['power'] if power_data else 0}W, Today: {summary['today']}kWh")


def store_daily_summary(summary):
    """Store/update daily summary."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
        INSERT OR REPLACE INTO daily_summary 
        (date, energy_today, energy_month, energy_year, energy_total)
        VALUES (?, ?, ?, ?, ?)
    """, (today, summary["today"], summary["month"], summary["year"], summary["total"]))
    conn.commit()
    conn.close()


def poll_once():
    """Single poll cycle."""
    try:
        summary = fetch_summary()
        power_data = fetch_live_power()
        status = fetch_status()
        store_reading(summary, power_data, status)
        store_daily_summary(summary)
        return True
    except Exception as e:
        print(f"[{datetime.now()}] Error: {e}")
        return False


def poll_loop(interval=300):
    """Continuous polling loop."""
    print(f"Starting solar monitor. Polling every {interval}s.")
    print(f"Database: {DB_PATH}")
    print("Ctrl+C to stop.\n")
    while True:
        poll_once()
        time.sleep(interval)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Solar Monitor - ShineMonitor poller")
    parser.add_argument("--init", action="store_true", help="Initialize database")
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument("--interval", type=int, default=300, help="Polling interval seconds")
    parser.add_argument("--status", action="store_true", help="Show current status")
    args = parser.parse_args()
    
    if args.init:
        init_db()
        return
    
    if not DB_PATH.exists():
        print("Database not found. Run with --init first.")
        return
    
    if args.status:
        try:
            summary = fetch_summary()
            power = fetch_live_power()
            status = fetch_status()
            print(f"Status: {status}")
            print(f"Current Power: {power['power'] if power else 'N/A'}W")
            print(f"Today: {summary['today']}kWh")
            print(f"Month: {summary['month']}kWh")
            print(f"Year: {summary['year']}kWh")
            print(f"Total: {summary['total']}kWh")
        except Exception as e:
            print(f"Error: {e}")
        return
    
    if args.once:
        poll_once()
    else:
        try:
            poll_loop(args.interval)
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()