"""
Checks Pixel Pickle (pixelpickle.skedda.com) for pickleball availability.

This site works OPPOSITE to the others: it shows a day schedule where
EXISTING bookings render as visible colored blocks with their exact time
range as text (e.g. "6:00 PM-8:00 PM"), and anything NOT covered by a
block is genuinely open. There's no explicit "available" marker to look
for -- availability is the absence of a booking block over a given hour.

VERIFIED LIVE against the real site:
- Direct URL navigation works with the `viewdate` parameter (NOT `date`,
  which is silently ignored):
    https://pixelpickle.skedda.com/booking?viewtype=0&viewdate=YYYY-MM-DD
  Confirmed by clicking a real date in the site's own date-picker and
  watching the URL update to reveal the correct parameter name.
- The on-screen date-picker widget visually greys out dates beyond about
  a week ahead, suggesting a short booking window -- but confirmed live
  that navigating directly via the `viewdate` URL parameter to a date
  three weeks out (well beyond what the picker allows clicking to) still
  returns real, correct schedule data. The picker's greying-out is a
  UI/UX limit on the click interface, not an actual data restriction.
- Each existing booking is a real `<td>` table cell (cellIndex 1 = Court
  1, cellIndex 2 = Court 2, confirmed by matching header cell text
  "PickleBall Court 1"/"PickleBall Court 2" to their cellIndex) containing
  a child with class "booking-div", whose text content is the exact
  booked time range, e.g. "6:00 PM-8:00 PM" (uses an en-dash, not a
  regular hyphen).
- A hidden decoy: there are also invisible <span class="fw-semibold">
  elements with the same time-range text but zero width/height (probably
  for screen readers or a different view mode) -- confirmed these are
  NOT the real visible elements; the real ones are the `.booking-div`
  cells found via the table structure instead.
- To determine if a specific hour is open on a specific court: check
  whether that hour falls inside ANY existing booking's time range for
  that court's cellIndex. If it doesn't overlap any booking, it's open.

If this site changes its layout, this is the file to fix -- run with
DEBUG=1 to open a visible browser and see what it's finding:
    DEBUG=1 python checkers/pixelpickle_checker.py
"""

import os
import re
import json
from datetime import timedelta

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import WANTED_WEEKDAYS, WANTED_START_HOUR, WANTED_END_HOUR, DAYS_AHEAD, get_today

DEBUG = os.environ.get("DEBUG") == "1"
BASE_URL = "https://pixelpickle.skedda.com/booking"
VENUE_NAME = "Pixel Pickle"

_EXTRACT_JS = """
() => {
  // Map cellIndex -> court name, from the header cells.
  const headerCells = Array.from(document.querySelectorAll('th, td')).filter(
    e => /PickleBall Court/i.test(e.textContent)
  );
  if (headerCells.length === 0) return { error: 'no court headers found' };
  const courtsByIndex = {};
  headerCells.forEach(h => { courtsByIndex[h.cellIndex] = h.textContent.trim(); });

  // Every existing booking block, with its court (via cellIndex) and its
  // raw visible time-range text.
  const blocks = Array.from(document.querySelectorAll('.booking-div'));
  const bookings = [];
  for (const b of blocks) {
    const td = b.closest('td');
    if (!td) continue;
    const courtName = courtsByIndex[td.cellIndex];
    if (!courtName) continue;
    const text = b.textContent.replace(/\\s+/g, ' ').trim();
    bookings.push({ court: courtName, text });
  }

  return {
    courts: Object.values(courtsByIndex),
    bookings,
  };
}
"""


def _parse_time_range(text: str):
    """
    '6:00 PM-8:00 PM' (or with an en-dash) -> (18, 20) as 24h hours.
    Returns None if it doesn't match the expected pattern.
    """
    # Normalize en-dash/em-dash to a plain hyphen first.
    normalized = text.replace("\u2013", "-").replace("\u2014", "-")
    m = re.match(
        r"(\d+):\d+\s*(AM|PM)\s*-\s*(\d+):\d+\s*(AM|PM)",
        normalized.strip(),
        re.IGNORECASE,
    )
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
    # Handle a booking that runs past midnight (e.g. "9:00 PM-12:00 AM")
    if end_hour == 0:
        end_hour = 24
    return start_hour, end_hour


def check_pixelpickle():
    """
    Returns a list of dicts: {venue, date, start_time, court, url}
    for open hours that fall in the wanted weekday + hour window.

    Raises an exception if every date's page load/extraction failed, so a
    real outage properly trips the failure-streak alert in main.py.
    """
    from playwright.sync_api import sync_playwright

    found = []
    today = get_today()

    wanted_dates = [
        today + timedelta(days=offset)
        for offset in range(DAYS_AHEAD)
        if (today + timedelta(days=offset)).weekday() in WANTED_WEEKDAYS
    ]

    failures = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not DEBUG)
        page = browser.new_page()

        for date in wanted_dates:
            date_str = date.isoformat()
            url = f"{BASE_URL}?viewtype=0&viewdate={date_str}"

            try:
                page.goto(url, wait_until="networkidle", timeout=20000)
                page.wait_for_selector("text=/PickleBall Court/i", timeout=10000)
            except Exception as e:
                print(f"[pixelpickle] Error loading {date_str}: {e}")
                failures += 1
                continue

            data = page.evaluate(_EXTRACT_JS)

            if data.get("error"):
                print(f"[pixelpickle] {date_str}: {data['error']}")
                failures += 1
                continue

            courts = data["courts"]
            bookings = data["bookings"]

            if DEBUG:
                print(f"  {date_str}: courts={courts}, {len(bookings)} existing booking(s)")

            # Build a set of (court, hour) pairs that are BOOKED, by
            # expanding each booking's time range into individual hours.
            booked = set()
            for b in bookings:
                parsed = _parse_time_range(b["text"])
                if not parsed:
                    continue
                start_hour, end_hour = parsed
                for h in range(start_hour, end_hour):
                    booked.add((b["court"], h))

            for court in courts:
                for hour in range(WANTED_START_HOUR, WANTED_END_HOUR):
                    is_open = (court, hour) not in booked
                    if DEBUG:
                        print(f"    {court} {hour}:00 open={is_open}")
                    if is_open:
                        found.append({
                            "venue": VENUE_NAME,
                            "date": date_str,
                            "start_time": f"{hour:02d}:00",
                            "court": court,
                            "url": url,
                        })

        if DEBUG:
            page.screenshot(path="pixelpickle_debug.png", full_page=True)
            print(f"Checked {len(wanted_dates)} date(s), {failures} failure(s). "
                  f"Screenshot saved to pixelpickle_debug.png")

        browser.close()

    if wanted_dates and failures == len(wanted_dates):
        raise RuntimeError(
            f"All {failures} date loads failed -- site may be down, "
            f"blocking automated browsers, or its layout changed."
        )

    return found


if __name__ == "__main__":
    results = check_pixelpickle()
    print(json.dumps(results, indent=2))
    print(f"\nFound {len(results)} matching open slot(s).")
