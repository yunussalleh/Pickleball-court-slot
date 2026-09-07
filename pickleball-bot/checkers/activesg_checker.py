"""
Checks ActiveSG Sport Village @ Jurong Town Outdoor Pickleball Courts for
BALLOT opportunities specifically -- not instant booking. This site works
fundamentally differently from every other venue in this project.

WHAT "BALLOT" MEANS HERE: ActiveSG uses a lottery/ballot system for
high-demand peak-hour slots rather than first-come-first-served booking.
Only ONE date is ever "the current ballot date" at a time (a rolling
window that advances by roughly a day at a time, confirmed live: on
2026-09-07, the single active ballot date was 2026-09-21 -- 14 days
ahead). This checker's job is narrow and specific: tell you the moment a
Friday/Saturday/Sunday date's 6pm/7pm slots become ballot-eligible, so
YOU can go log in with Singpass and submit your own ballot entry. It does
NOT and CANNOT tell you whether you'd win that ballot, and it does not
attempt to log in or submit anything on your behalf -- entering the
ballot itself requires Singpass login, which this script never touches.

VERIFIED LIVE against the real site:
- Direct URL navigation with a `date=YYYY-MM-DD` query param works
  cleanly -- confirmed by clicking a real date tab and watching the URL
  update to that exact pattern, then reloading that same URL directly.
    https://activesg.gov.sg/facility-bookings/activities/BPQihVHITc7IPGorVeB2Y/venues/SkHvZCbrLG5YKE1mDcUXV/timeslots?activityId=BPQihVHITc7IPGorVeB2Y&venueId=SkHvZCbrLG5YKE1mDcUXV&date=YYYY-MM-DD
- No login is required to VIEW which slots are ballot-eligible -- only to
  actually submit a ballot entry. Confirmed: the full slot grid renders
  with a "Log in with Singpass" prompt visible at the bottom, not a
  login wall blocking the content itself.
- Each hourly slot (e.g. "6:00 pm") is a Chakra UI card:
    <label class="chakra-card ..."> containing a <p> with the time text.
  Confirmed the exact distinguishing signal via live DOM inspection: a
  slot that's currently actionable (ballot-open, or instant-bookable) has
  NO `data-disabled` attribute at all, while a slot that isn't available
  yet (e.g. non-peak hours before their own separate scheduled release
  time) has `data-disabled=""` present. This is a clean boolean signal,
  not a class-name guess.
- This checker treats "the slot card has no data-disabled attribute" as
  the actionable signal, rather than trying to specifically detect the
  red "Ballot" badge UI element -- simpler and equally correct for our
  purposes, since a wanted date that's already past its ballot phase
  reliably shows its peak hours as fully gone/disabled anyway (confirmed
  live: a date past its ballot window showed no evening slots at all
  under the "Instant" section, only leftover off-peak hours).

If this site changes its layout, this is the file to fix -- run with
DEBUG=1 to open a visible browser and see what it's finding:
    DEBUG=1 python checkers/activesg_checker.py
"""

import os
import re
import json
from datetime import timedelta

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import WANTED_WEEKDAYS, WANTED_START_HOUR, WANTED_END_HOUR, get_today

DEBUG = os.environ.get("DEBUG") == "1"

# This venue's ballot date has been observed 14 days out and rolls
# forward roughly daily -- give it a generous, dedicated buffer rather
# than relying on (or changing) the shared global DAYS_AHEAD, so this
# checker keeps working even if the ballot lead time shifts by a few
# days, without affecting every other venue's checker.
ACTIVESG_DAYS_AHEAD = 21

BASE_URL = (
    "https://activesg.gov.sg/facility-bookings/activities/BPQihVHITc7IPGorVeB2Y"
    "/venues/SkHvZCbrLG5YKE1mDcUXV/timeslots"
    "?activityId=BPQihVHITc7IPGorVeB2Y&venueId=SkHvZCbrLG5YKE1mDcUXV"
)
VENUE_NAME = "ActiveSG Jurong Town Outdoor Pickleball Courts (Ballot)"

_EXTRACT_JS = """
(wantedTimeLabels) => {
  const results = {};
  for (const label of wantedTimeLabels) {
    const p = Array.from(document.querySelectorAll('p')).find(
      e => e.textContent.trim().toLowerCase() === label.toLowerCase()
    );
    if (!p) {
      results[label] = { found: false };
      continue;
    }
    const card = p.closest('label.chakra-card') || p.closest('[class*="chakra-card"]');
    if (!card) {
      results[label] = { found: false };
      continue;
    }
    results[label] = {
      found: true,
      disabled: card.hasAttribute('data-disabled'),
    };
  }
  return results;
}
"""


def _time_label_for_hour(hour: int) -> str:
    """18 -> '6:00 pm'. 9 -> '9:00 am'."""
    ampm = "am" if hour < 12 else "pm"
    display_hour = hour % 12
    if display_hour == 0:
        display_hour = 12
    return f"{display_hour}:00 {ampm}"


def check_activesg():
    """
    Returns a list of dicts: {venue, date, start_time, court, url} for
    wanted hours that are currently ballot-eligible (or otherwise
    actionable) on a wanted weekday. "court" is always "any" -- this
    venue doesn't expose separate courts the way others do.

    Raises an exception if every single date check failed outright (page
    wouldn't load at all), so a real outage trips the failure-streak
    alert in main.py. A date simply not showing the wanted slots yet is
    normal, expected, and not a failure.
    """
    from playwright.sync_api import sync_playwright

    found = []
    today = get_today()

    wanted_dates = [
        today + timedelta(days=offset)
        for offset in range(ACTIVESG_DAYS_AHEAD)
        if (today + timedelta(days=offset)).weekday() in WANTED_WEEKDAYS
    ]

    wanted_labels = [_time_label_for_hour(h) for h in range(WANTED_START_HOUR, WANTED_END_HOUR)]

    attempts = 0
    hard_failures = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not DEBUG)
        page = browser.new_page()

        evidence_captured = False

        for date in wanted_dates:
            date_str = date.isoformat()
            url = f"{BASE_URL}&date={date_str}"
            attempts += 1

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_selector("text=Select date", timeout=15000)
                page.wait_for_timeout(500)  # let the slot grid finish rendering
            except Exception as e:
                print(f"[activesg] Error loading {date_str}: {e}")
                hard_failures += 1
                # Every date failing identically suggests a site-wide
                # issue (e.g. a bot-detection block), not per-date
                # flakiness -- confirmed this exact pattern turned out to
                # be a Cloudflare challenge for both Smashing and Franklin
                # elsewhere in this project. Capture real evidence once
                # per run rather than guessing, and rather than
                # screenshotting on every single failed date.
                if not evidence_captured:
                    try:
                        os.makedirs("debug_failures", exist_ok=True)
                        page.screenshot(path="debug_failures/activesg_failure.png", full_page=True)
                        with open("debug_failures/activesg_failure.html", "w") as f:
                            f.write(page.content())
                        evidence_captured = True
                    except Exception as capture_err:
                        print(f"[activesg] Also failed to capture debug evidence: {capture_err}")
                continue

            data = page.evaluate(_EXTRACT_JS, wanted_labels)

            if DEBUG:
                print(f"  {date_str}: {data}")

            for hour, label in zip(range(WANTED_START_HOUR, WANTED_END_HOUR), wanted_labels):
                info = data.get(label, {})
                if not info.get("found"):
                    continue  # slot not shown at all for this date -- skip
                is_open = not info.get("disabled", True)
                if is_open:
                    found.append({
                        "venue": VENUE_NAME,
                        "date": date_str,
                        "start_time": f"{hour:02d}:00",
                        "court": "any",
                        "url": url,
                    })

        if DEBUG:
            page.screenshot(path="activesg_debug.png", full_page=True)
            print(f"Checked {attempts} date(s), {hard_failures} hard failure(s). "
                  f"Screenshot saved to activesg_debug.png")

        browser.close()

    if attempts and hard_failures == attempts:
        raise RuntimeError(
            f"All {hard_failures} date checks failed -- site may be down, "
            f"blocking automated browsers, or its layout changed."
        )

    return found


if __name__ == "__main__":
    results = check_activesg()
    print(json.dumps(results, indent=2))
    print(f"\nFound {len(results)} matching open slot(s).")
