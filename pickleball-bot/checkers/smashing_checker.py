"""
Checks Smashing Pickle (Jurong Play Grounds) by reading the real booking
calendar at app.smashing.sg with a headless browser (Playwright).

Smashing doesn't have a public API, so this drives an actual browser,
reads the date tabs, clicks into the ones in our wanted weekday/date range,
and reads which hourly slot buttons are open vs. greyed out (booked).

If Smashing changes their site layout, this is the file to fix -- run with
DEBUG=1 to dump a screenshot + the raw button text/state it saw, e.g.:
    DEBUG=1 python checkers/smashing_checker.py
"""

import os
import re
import json
from datetime import datetime

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SMASHING_URL, WANTED_WEEKDAYS, WANTED_START_HOUR, WANTED_END_HOUR, DAYS_AHEAD, get_today

VENUE_NAME = "Smashing Pickle (Jurong Play Grounds)"
DEBUG = os.environ.get("DEBUG") == "1"


def _parse_time_label(label: str):
    """'6:00 pm' -> 18. '8:00 am' -> 8."""
    m = re.match(r"(\d+):(\d+)\s*(am|pm)", label.strip(), re.IGNORECASE)
    if not m:
        return None
    hour, _minute, ampm = int(m.group(1)), m.group(2), m.group(3).lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    return hour


def _load_venue_page(page, attempt_timeout_ms):
    """
    Navigates to Smashing and clicks into the venue page. Raises on
    failure so the caller can retry with a fresh page load.
    """
    page.goto(SMASHING_URL, wait_until="networkidle", timeout=attempt_timeout_ms)
    page.get_by_text("Jurong Gateway Road").first.click(timeout=attempt_timeout_ms)
    page.wait_for_selector("text=Select date", timeout=attempt_timeout_ms)


def check_smashing():
    from playwright.sync_api import sync_playwright

    found = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not DEBUG)
        page = browser.new_page()

        # Retry the initial load up to twice with a longer timeout on the
        # second attempt. Confirmed via live testing (same site, fresh
        # cookie-less browser, works instantly and consistently every
        # time from a normal connection) that this isn't a real site
        # change or a cookie/consent-banner issue -- production
        # (GitHub Actions) appears to have genuinely higher latency
        # reaching this site than a typical connection does, so a single
        # attempt with the default 30s timeout can occasionally time out
        # even though nothing is actually broken.
        last_error = None
        for attempt, timeout_ms in enumerate([30000, 60000]):
            try:
                _load_venue_page(page, timeout_ms)
                last_error = None
                break
            except Exception as e:
                last_error = e
                if DEBUG:
                    print(f"Attempt {attempt + 1} failed ({e}); retrying with a longer timeout")
        if last_error:
            raise last_error

        # Date tabs render as a row of buttons, each showing e.g. "Sat\n29/08".
        # We look at all of them, figure out which correspond to our wanted
        # weekdays within DAYS_AHEAD, and click into each one.
        tab_buttons = page.locator("button", has_text=re.compile(r"\d{2}/\d{2}")).all()

        today = get_today()
        checked = 0

        for i in range(len(tab_buttons)):
            # Re-query each loop since clicking can re-render the DOM.
            tabs = page.locator("button", has_text=re.compile(r"\d{2}/\d{2}")).all()
            if i >= len(tabs):
                break
            tab = tabs[i]
            label = tab.inner_text().strip()
            m = re.search(r"(\d{2})/(\d{2})", label)
            if not m:
                continue
            day, month = int(m.group(1)), int(m.group(2))
            year = today.year if month >= today.month else today.year + 1
            try:
                date = datetime(year, month, day).date()
            except ValueError:
                continue

            days_out = (date - today).days
            if days_out < 0 or days_out > DAYS_AHEAD:
                continue
            if date.weekday() not in WANTED_WEEKDAYS:
                continue

            tab.click()
            page.wait_for_timeout(600)  # let slot grid re-render
            checked += 1

            # Slot buttons show time + price, e.g. "6:00 pm\nS$32.00"
            slot_buttons = page.locator("button", has_text=re.compile(r"^\d+:\d{2}\s*(am|pm)", re.IGNORECASE)).all()

            for slot in slot_buttons:
                text = slot.inner_text().strip()
                first_line = text.splitlines()[0] if text else ""
                hour = _parse_time_label(first_line)
                if hour is None or not (WANTED_START_HOUR <= hour < WANTED_END_HOUR):
                    continue

                # VERIFIED via live click-testing (2026-08-30): the real
                # HTML `disabled` attribute is the correct, sufficient
                # signal here. Clicking a disabled slot does nothing;
                # clicking a non-disabled one correctly selects it (shows
                # up in a real "Selected slots" panel). An earlier version
                # of this file also had an opacity/pointer-events fallback
                # "just in case" -- removed after confirming live that
                # BOTH available and unavailable slots report identical
                # opacity (1) and pointer-events (auto) on this site, so
                # that fallback could never have done anything useful and
                # was just unverified guesswork.
                is_disabled = slot.is_disabled()

                if DEBUG:
                    print(f"  {date} {first_line}: disabled={is_disabled}")

                if not is_disabled:
                    found.append({
                        "venue": VENUE_NAME,
                        "date": date.isoformat(),
                        "start_time": f"{hour:02d}:00",
                        "court": "any",
                        "url": SMASHING_URL,
                    })

        if DEBUG:
            page.screenshot(path="smashing_debug.png", full_page=True)
            print(f"Checked {checked} date tab(s). Screenshot saved to smashing_debug.png")

        browser.close()

    return found


if __name__ == "__main__":
    results = check_smashing()
    print(json.dumps(results, indent=2))
    print(f"\nFound {len(results)} matching open slot(s).")
