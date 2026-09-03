"""
Checks Franklin Pickleball Singapore (src.franklinpickleball.com.sg) for
pickleball availability across all 9 courts (Court 1A/1B/1C, 2A/2B/2C,
3A/3B/3C).

VERIFIED LIVE against the real site:
- The booking page shows a row of ~6 date tabs (e.g. "FRI 04", "SAT 05",
  ...), each a clickable button with the day abbreviation and day-of-month
  on two lines.
- IMPORTANT: this site releases new bookable dates on a TIMED SCHEDULE,
  not a simple "N days ahead" rolling window. Confirmed live: clicking the
  furthest-out visible tab showed a literal countdown timer --
  "Booking for this day will open in: 00:36:15" -- meaning that date
  wasn't bookable yet even though its tab was visible. This is different
  from every other venue in this project. Practically: only ~5-6 days are
  ever available at once, and the exact number can vary run to run
  depending on whether a new day's release window has hit yet. This
  checker skips any date tab showing that countdown text rather than
  treating it as an error.
- Selecting a date, then clicking "VIEW CALENDAR" (confirmed via a real
  button/link with that exact text) opens a modal showing ALL 9 courts at
  once as real HTML table rows -- much more efficient than clicking
  through each hour individually. Confirmed structure via direct DOM
  inspection:
    - The whole grid is a <table class="calendarTable"> (deeply nested
      inside a "SimpleCalendarView" / "calendarArea" wrapper, but ordinary
      querySelectorAll reaches it fine -- this is NOT behind an iframe or
      shadow DOM, despite there being two unrelated iframes elsewhere on
      the page, e.g. for Stripe payment).
    - Each table row (<tr>) is one court, with a
      <td class="court_name"><span>Court 1A</span></td> identifying it.
    - Each existing booking in that row is a
      <div class="item_inner_container"> containing a child with class
      "reservation_kind" (literal lowercase text "reservation") and the
      time range as plain text, e.g. "8:00 AM -9:00 AM" (note: a space
      before the dash, confirmed exactly from live text content).
    - A hidden decoy to avoid: text matching is case-sensitive in a
      confusing way -- the on-screen text is visually uppercase
      ("RESERVATION") via CSS text-transform, but the real underlying DOM
      text is lowercase ("reservation"). Match case-insensitively.
    - Availability = the ABSENCE of a booking block over a given hour in
      that court's row, same "gap = available" model as Pixel Pickle.

If this site changes its layout, this is the file to fix -- run with
DEBUG=1 to open a visible browser and see what it's finding:
    DEBUG=1 python checkers/franklin_checker.py
"""

import os
import re
import json
from datetime import timedelta

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import WANTED_WEEKDAYS, WANTED_START_HOUR, WANTED_END_HOUR, DAYS_AHEAD, get_today

DEBUG = os.environ.get("DEBUG") == "1"
BASE_URL = "https://src.franklinpickleball.com.sg/book/FranklinPickleballSingapore"
VENUE_NAME = "Franklin Pickleball Singapore"

_EXTRACT_JS = """
() => {
  const table = document.querySelector('table.calendarTable');
  if (!table) return { error: 'calendar table not found' };
  const rows = Array.from(table.querySelectorAll('tr')).filter(
    tr => tr.querySelector('.court_name')
  );
  if (rows.length === 0) return { error: 'no court rows found' };

  const result = [];
  for (const row of rows) {
    const courtName = row.querySelector('.court_name').textContent.trim();
    const bookings = Array.from(row.querySelectorAll('.item_inner_container')).map(
      el => el.textContent.replace(/\\s+/g, ' ').trim()
    );
    result.push({ court: courtName, bookings });
  }
  return { courts: result };
}
"""


def _parse_booking_text(text: str):
    """
    'reservation8:00 AM -9:00 AM' -> (8, 9) as 24h hours.
    The word "reservation" (any case) may be glued directly onto the
    start of the time text with no space -- confirmed from live content.
    """
    # Strip a leading "reservation" label if present (case-insensitive).
    cleaned = re.sub(r"^reservation", "", text, flags=re.IGNORECASE).strip()
    m = re.match(r"(\d+):\d+\s*(AM|PM)\s*-\s*(\d+):\d+\s*(AM|PM)", cleaned, re.IGNORECASE)
    if not m:
        return None

    def to_24h(hour_str, ampm):
        hour = int(hour_str)
        ampm = ampm.upper()
        if ampm == "PM" and hour != 12:
            hour += 12
        if ampm == "AM" and hour == 12:
            hour = 0
        return hour

    start_hour = to_24h(m.group(1), m.group(2))
    end_hour = to_24h(m.group(3), m.group(4))
    if end_hour == 0:
        end_hour = 24
    return start_hour, end_hour


def check_franklin():
    """
    Returns a list of dicts: {venue, date, start_time, court, url}
    for open hours that fall in the wanted weekday + hour window.

    Raises an exception if every date's page load/extraction failed, so a
    real outage properly trips the failure-streak alert in main.py. Dates
    that simply haven't opened for booking yet (this site's own timed
    release schedule) are NOT treated as failures -- that's expected,
    normal behavior for this specific site.
    """
    from playwright.sync_api import sync_playwright

    found = []
    today = get_today()
    wanted_dates = {
        (today + timedelta(days=offset)).isoformat()
        for offset in range(DAYS_AHEAD)
        if (today + timedelta(days=offset)).weekday() in WANTED_WEEKDAYS
    }

    checked = 0
    hard_failures = 0
    attempts = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not DEBUG)
        page = browser.new_page()

        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("text=Select date and time", timeout=15000)
        except Exception as e:
            browser.close()
            raise RuntimeError(f"Failed to load Franklin booking page: {e}")

        # Date tabs show as two-line buttons, e.g. "FRI\n04". Collect them
        # once up front -- there are only ever a handful (~6) at a time.
        tab_buttons = page.locator("button", has_text=re.compile(r"^[A-Z]{3}\s*\d{2}$")).all()

        for i in range(len(tab_buttons)):
            tabs = page.locator("button", has_text=re.compile(r"^[A-Z]{3}\s*\d{2}$")).all()
            if i >= len(tabs):
                break
            tab = tabs[i]
            label = tab.inner_text().strip()
            m = re.search(r"(\d{2})$", label)
            if not m:
                continue
            day_num = int(m.group(1))

            today_date = today
            # Figure out the real calendar date this tab represents by
            # finding the nearest upcoming date with this day-of-month.
            candidate = None
            for offset in range(DAYS_AHEAD + 5):
                d = today_date.fromordinal(today_date.toordinal() + offset)
                if d.day == day_num:
                    candidate = d
                    break
            if candidate is None:
                continue
            date_str = candidate.isoformat()
            if date_str not in wanted_dates:
                continue

            attempts += 1
            try:
                tab.click()
                page.wait_for_timeout(500)

                # This site releases dates on a timed schedule -- a
                # visible tab may not be bookable yet. Detect and skip
                # cleanly rather than treating it as an error.
                if page.get_by_text("will open in", exact=False).count() > 0:
                    if DEBUG:
                        print(f"  {date_str}: not yet released for booking, skipping")
                    continue

                view_calendar = page.get_by_text("VIEW CALENDAR", exact=False)
                if view_calendar.count() == 0:
                    if DEBUG:
                        print(f"  {date_str}: 'View calendar' not found, skipping")
                    continue
                view_calendar.first.click()
                page.wait_for_selector("table.calendarTable", timeout=10000)
                page.wait_for_timeout(400)

                data = page.evaluate(_EXTRACT_JS)

                # Close the modal (click outside it) before moving on.
                page.mouse.click(50, 400)
                page.wait_for_timeout(300)

                if data.get("error"):
                    print(f"[franklin] {date_str}: {data['error']}")
                    hard_failures += 1
                    continue

                checked += 1
                courts_data = data["courts"]

                if DEBUG:
                    print(f"  {date_str}: {len(courts_data)} court(s)")

                for court_info in courts_data:
                    court_name = court_info["court"]
                    booked_hours = set()
                    for b_text in court_info["bookings"]:
                        parsed = _parse_booking_text(b_text)
                        if not parsed:
                            continue
                        start_hour, end_hour = parsed
                        for h in range(start_hour, end_hour):
                            booked_hours.add(h)

                    for hour in range(WANTED_START_HOUR, WANTED_END_HOUR):
                        is_open = hour not in booked_hours
                        if DEBUG:
                            print(f"    {court_name} {hour}:00 open={is_open}")
                        if is_open:
                            found.append({
                                "venue": VENUE_NAME,
                                "date": date_str,
                                "start_time": f"{hour:02d}:00",
                                "court": court_name,
                                "url": BASE_URL,
                            })

            except Exception as e:
                print(f"[franklin] Error processing {date_str}: {e}")
                hard_failures += 1
                continue

        if DEBUG:
            page.screenshot(path="franklin_debug.png", full_page=True)
            print(f"Checked {checked} date(s), {hard_failures} hard failure(s) "
                  f"out of {attempts} attempt(s). Screenshot saved to franklin_debug.png")

        browser.close()

    if attempts and hard_failures == attempts:
        raise RuntimeError(
            f"All {hard_failures} date checks failed -- site may be down, "
            f"blocking automated browsers, or its layout changed."
        )

    return found


if __name__ == "__main__":
    results = check_franklin()
    print(json.dumps(results, indent=2))
    print(f"\nFound {len(results)} matching open slot(s).")
