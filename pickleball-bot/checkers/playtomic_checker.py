"""
Checks Pickle Padel Movement (PUB Recreation Club) via Playtomic.

Playtomic's app talks to an UNDOCUMENTED public endpoint:
    GET https://api.playtomic.io/v1/availability
        ?sport_id=PADEL&tenant_id=<id>&start_min=...&start_max=...

This is not an official/supported API -- it can change or start requiring
auth at any time without notice. If it stops working, fall back to
`checkers/smashing_style_browser_checker.py`'s approach (Playwright reading
the Playtomic web page directly) instead -- see NOTE at bottom of file.
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PLAYTOMIC_TENANT_ID, WANTED_WEEKDAYS, WANTED_START_HOUR, WANTED_END_HOUR, DAYS_AHEAD

API_URL = "https://api.playtomic.io/v1/availability"
VENUE_NAME = "Pickle Padel Movement (PUB Recreation Club)"


def _fetch_day(date_str: str):
    """date_str like '2026-09-04'. Returns parsed JSON list, or None on failure."""
    params = {
        "sport_id": "PICKLEBALL",
        "tenant_id": PLAYTOMIC_TENANT_ID,
        "start_min": f"{date_str}T00:00:00",
        "start_max": f"{date_str}T23:59:59",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{API_URL}?{query}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; PickleballSlotWatcher/1.0)",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"[playtomic] HTTP {e.code} for {date_str}: {e.read()[:300]}")
        return None
    except Exception as e:
        print(f"[playtomic] Error fetching {date_str}: {e}")
        return None


def check_playtomic():
    """
    Returns a list of dicts: {venue, date, start_time, court, url}
    for open slots that fall in the wanted weekday + hour window.
    """
    found = []
    today = datetime.now().date()

    for offset in range(DAYS_AHEAD):
        date = today + timedelta(days=offset)
        if date.weekday() not in WANTED_WEEKDAYS:
            continue

        date_str = date.isoformat()
        data = _fetch_day(date_str)
        if not data:
            continue

        # Response shape (per reverse-engineering write-ups): a list of
        # resources, each with a list of "slots" with start_time / duration.
        # This may need adjusting if Playtomic changes their schema --
        # print(json.dumps(data, indent=2)) to inspect if results look empty
        # unexpectedly.
        for resource in data:
            court_name = resource.get("resource_name") or resource.get("name", "Court")
            for slot in resource.get("slots", []):
                start_time = slot.get("start_time")  # "HH:MM:SS"
                if not start_time:
                    continue
                hour = int(start_time.split(":")[0])
                if WANTED_START_HOUR <= hour < WANTED_END_HOUR:
                    found.append({
                        "venue": VENUE_NAME,
                        "date": date_str,
                        "start_time": start_time[:5],
                        "court": court_name,
                        "url": f"https://playtomic.io/tenant/{PLAYTOMIC_TENANT_ID}",
                    })

    return found


if __name__ == "__main__":
    results = check_playtomic()
    print(json.dumps(results, indent=2))
    print(f"\nFound {len(results)} matching open slot(s).")
