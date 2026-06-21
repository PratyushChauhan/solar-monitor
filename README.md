# Solar Monitor

Open-source solar power monitor using ShineMonitor cloud API.

## Quick Start

1. **Get credentials from ShineMonitor app/web:**
   - Username and password
   - Log into web.shinemonitor.com → check URL for `company-key=...`
   - Plant ID from app settings
   - Device serial number (SN), product number (PN), device code from device info

2. **Configure:**
   ```bash
   cd ~/.openclaw/workspace/solar-monitor
   nano config.py
   ```
   Fill in your credentials.

3. **Initialize database:**
   ```bash
   python3 poller.py --init
   ```

4. **Test:**
   ```bash
   python3 poller.py --status
   ```

5. **Run:**
   ```bash
   # Poll once
   python3 poller.py --once
   
   # Poll every 5 minutes continuously
   python3 poller.py
   
   # Poll every minute
   python3 poller.py --interval 60
   ```

## Database

SQLite file: `solar_data.db`

Tables:
- `live_readings` — timestamped power, voltage, energy readings
- `daily_summary` — daily/weekly/monthly/yearly totals
- `timeline` — hourly power curve data

## Files

| File | Purpose |
|------|---------|
| `poller.py` | Main polling script |
| `config.py` | Credentials (edit this) |
| `solar_data.db` | SQLite database |
| `reference-shinemonitor/` | Cloned reference implementation |

## To Do

- [ ] Set up Grafana dashboard
- [ ] Add historical timeline fetching
- [ ] Add alerting (low production, offline)
- [ ] Add web UI