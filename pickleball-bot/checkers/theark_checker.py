"""
Checks Orchid Country Club and Cuppage (both booked via "The ARK" website)
for pickleball availability. Ark Sports Village (the third venue on this
same site) is deliberately NOT checked -- not one of the wanted venues.

VERIFIED LIVE against the real site:
- Direct URL navigation works cleanly for both venue and date:
    https://theark.sg/pickleball/booking?venue=Orchid+Country+Club&date=YYYY-MM-DD
    https://theark.sg/pickleball/booking?venue=Cuppage&date=YYYY-MM-DD
  (confirmed by watching the URL update after using the venue dropdown,
  then reloading those exact URLs directly and getting the same result).
- The schedule renders as a simple table (technically a Vuetify/Vue app,
  not literal <table> markup) inside a container with class
  "bookingFormTimeSelector". Each row is one hourly time slot (e.g.
  "6pm - 7pm"), each column is a court ("Court 1", "Court 2" for both
  venues, confirmed for each individually).
- Availability is shown with Material Design Icons:
    available:   <i class="mdi-check-circle ...">
    unavailable: <i class="mdi-close-circle ... text-red">
  Icons appear in simple row-major order matching the visible table
  (row 0's courts first, then row 1's, etc.) -- confirmed by checking a
  known cell (Orchid, 6pm-7pm, Court 1 vs Court 2) against the computed
  class names directly.
- Requesting a date outside the site's own booking window doesn't error
  -- it silently strips the date from the URL and shows no schedule
  table at all. Confirmed the window covers at least 26 days ahead
  (comfortably more than our DAYS_AHEAD), so this mostly matters as a
  "just in case" guard rather than an expected everyday occurrence.
- No login is required to just VIEW availability.

If this site changes its layout, this is the file to fix -- run with
DEBUG=1 to open a visible browser and see what it's finding:
    DEBUG=1 python checkers/theark_checker.py
"""

import os
import re
import json
from datetime import timedelta

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import WANTED_WEEKDAYS, WANTED_START_HOUR, WANTED_END_HOUR, DAYS_AHEAD, get_today

DEBUG = os.environ.get("DEBUG") == "1"
BASE_URL = "https://theark.sg/pickleball/booking"

# venue query-param value -> display name used in Telegram messages
VENUES = {
    "Orchid Country Club": "Orchid Country Club (The ARK)",
    # Cuppage removed on request -- court quality wasn't good.
}

_EXTRACT_JS = """
() => {
  const container = document.querySelector('.bookingFormTimeSelector');
  if (!container) return { error: 'schedule table not found' };
  const headers = Array.from(container.querySelectorAll('*')).filter(
    e => e.children.length === 0 && /^Court \\d+$/.test(e.textContent.trim())
  );
  const timeLabels = Array.from(container.querySelectorAll('*')).filter(
    e => e.children.length === 0 && /^\\d+(am|pm) - \\d+(am|pm)$/.test(e.textContent.trim())
  );
  const icons = Array.from(container.querySelectorAll('i.v-icon'));
  const numCourts = headers.length;
  if (numCourts === 0 || timeLabels.length === 0) {
    return { error: 'no headers/time labels found' };
  }
  if (icons.length !== timeLabels.length * numCourts) {
    return { error: 'icon count mismatch' };
  }
  return {
    headers: headers.map(h => h.textContent.trim()),
    timeLabels: timeLabels.map(t => t.textContent.trim()),
    iconClasses: icons.map(ic => ic.className),
  };
}
"""


def _parse_start_hour(label: str):
    """'6pm - 7pm' -> 18. '7am - 8am' -> 7."""
    m = re.match(r"(\d+)(am|pm) - ", label.strip(), re.IGNORECASE)
    if not m:
        return None
    hour, ampm = int(m.group(1)), m.group(2).lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    return hour


def check_theark():
    """
    Returns a list of dicts: {venue, date, start_time, court, url}
    for open slots that fall in the wanted weekday + hour window, across
    both Orchid Country Club and Cuppage.

    Raises an exception if every single page load failed (not just dates
    outside the booking window, which is expected/normal), so a real
    outage properly trips the failure-streak alert in main.py.
    """
    from playwright.sync_api import sync_playwright

    found = []
    today = get_today()

    wanted_dates = [
        today + timedelta(days=offset)
        for offset in range(DAYS_AHEAD)
        if (today + timedelta(days=offset)).weekday() in WANTED_WEEKDAYS
    ]

    attempts = 0
    hard_failures = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not DEBUG)
        page = browser.new_page()

        for venue_param, venue_display in VENUES.items():
            for date in wanted_dates:
                date_str = date.isoformat()
                url = f"{BASE_URL}?venue={venue_param.replace(' ', '+')}&date={date_str}"
                attempts += 1

                try:
                    page.goto(url, wait_until="networkidle", timeout=20000)
                except Exception as e:
                    hard_failures += 1
                    if DEBUG:
                        print(f"[{venue_display}] {date_str}: page load error: {e}")
                    continue

                try:
                    page.wait_for_selector(".bookingFormTimeSelector", timeout=8000)
                except Exception:
                    # Most likely just outside this site's own booking
                    # window (confirmed live: out-of-range dates silently
                    # render no schedule table at all, not an error page).
                    if DEBUG:
                        print(f"[{venue_display}] {date_str}: no schedule table "
                              f"(likely outside booking window)")
                    continue

                data = page.evaluate(_EXTRACT_JS)

                if data.get("error"):
                    hard_failures += 1
                    if DEBUG:
                        print(f"[{venue_display}] {date_str}: {data['error']}")
                    continue

                headers = data["headers"]
                time_labels = data["timeLabels"]
                icon_classes = data["iconClasses"]
                num_courts = len(headers)

                if DEBUG:
                    print(f"[{venue_display}] {date_str}: {len(time_labels)} time slots, "
                          f"{num_courts} courts")

                for row_idx, label in enumerate(time_labels):
                    hour = _parse_start_hour(label)
                    if hour is None or not (WANTED_START_HOUR <= hour < WANTED_END_HOUR):
                        continue
                    for court_idx, court_name in enumerate(headers):
                        icon_cls = icon_classes[row_idx * num_courts + court_idx]
                        is_open = ("mdi-check-circle" in icon_cls) and ("text-red" not in icon_cls)
                        if DEBUG:
                            print(f"    {court_name} {hour}:00 open={is_open}")
                        if is_open:
                            found.append({
                                "venue": venue_display,
                                "date": date_str,
                                "start_time": f"{hour:02d}:00",
                                "court": court_name,
                                "url": url,
                            })

        if DEBUG:
            page.screenshot(path="theark_debug.png", full_page=True)
            print(f"Checked {attempts} (venue, date) combination(s), "
                  f"{hard_failures} hard failure(s). Screenshot saved to theark_debug.png")

        browser.close()

    if attempts and hard_failures == attempts:
        raise RuntimeError(
            f"All {hard_failures} page loads failed -- site may be down, "
            f"blocking automated browsers, or its layout changed."
        )

    return found


if __name__ == "__main__":
    results = check_theark()
    print(json.dumps(results, indent=2))
    print(f"\nFound {len(results)} matching open slot(s).")
