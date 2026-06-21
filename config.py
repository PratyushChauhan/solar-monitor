"""
ShineMonitor configuration
Loads credentials from .env file (not tracked in git)
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from same directory as this file
load_dotenv(Path(__file__).parent / ".env")

# Your ShineMonitor login credentials
usr = os.getenv("SHINEMONITOR_USR", "")
pwd = os.getenv("SHINEMONITOR_PWD", "")

# Company key - found in ksolare.shinemonitor.com login page source
companykey = os.getenv("SHINEMONITOR_COMPANY_KEY", "")

# Device identifiers (from API queryPlants + queryPlantDeviceDesignatedInformation)
plantId = os.getenv("PLANT_ID", "1250826")
devcode = os.getenv("DEV_CODE", "632")
pn = os.getenv("PRODUCT_NUMBER", "Q0029375976171")
sn = os.getenv("SERIAL_NUMBER", "KSY0424HT3322")

# Debug mode (set to 1 to see request URLs)
debug = 0
