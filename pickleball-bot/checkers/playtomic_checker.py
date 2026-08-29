"""
Checks Pickle Padel Movement (PUB Recreation Club) via Playtomic's public
booking web page -- NOT the direct API anymore.

HISTORY: this originally called Playtomic's undocumented
api.playtomic.io/v1/availability endpoint directly. That got 403'd by
Playtomic's server (confirmed via live testing on 2026-08-29) -- likely
blocked at the TLS/connection-fingerprint level, since changing the
User-Agent/headers alone didn't help. A real browser isn't distinguishable
from genuine traffic that way, so this now drives a real page load instead,
exactly like the Smashing and Kings checkers.

VERIFIED LIVE against the real site while rewriting this:
- The public booking page is:
    https://playtomic.com/clubs/pickle-padel-movement?sport=PICKLEBALL&date=YYYY-MM-DD
  Navigating directly to a specific date via the URL query param works
  cleanly -- no need to click "next day" repeatedly.
- Each real court row label is exact text "Pickleball 1".."Pickleball 4"
  (there's also a "Show Court" row that is NOT a real court -- confirmed by
  only matching the "Pickleball N" pattern).
- Each bookable HOUR shows as a `cursor-pointer` + `bg-white` div roughly
  47px wide (a 1-hour duration option) positioned absolutely within that
  court's row. I confirmed this by actually clicking one and watching it
  turn into a real selection, then compared its measured pixel width to
  the 1.5h/2h duration variants that render as wider overlapping siblings
  at the same start position.
- An UNAVAILABLE hour simply has NO such white element at that position --
  the base row track underneath is grey ("not available" per the page's
  own legend, confirmed by reading the legend swatch colors directly).
- IMPORTANT: don't trust a quick visual/screenshot read of grey vs white --
  a light grey "not available" hatch can look deceptively close to white
  in a compressed screenshot. Always confirm against the legend's actual
  computed background-color, like this file does.

If Playtomic changes their site layout, this is the file to fix -- run
with DEBUG=1 to open a visible browser and see what it's finding:
    DEBUG=1 python checkers/playtomic_checker.py
"""

import os
import json
from datetime import datetime, timedelta

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import WANTED_WEEKDAYS, WANTED_START_HOUR, WANTED_END_HOUR, DAYS_AHEAD

VENUE_NAME = "Pickle Padel Movement (PUB Recreation Club)"
BASE_URL = "https://playtomic.com/clubs/pickle-padel-movement"
DEBUG = os.environ.get("DEBUG") == "1"

# The JS that does the actual extraction, run inside the page via
# page.evaluate(). Returns {"Pickleball 1": [18, 19], "Pickleball 2": [], ...}
# -- a list of hours (24h, start-of-hour) that have a genuine 1-hour-wide
# open (white, cursor-pointer) slot for that court.
_EXTRACT_JS = """
(wantedHours) => {
  function leafTextEls(pattern) {
    return Array.from(document.querySelectorAll('div,span')).filter(
      e => e.children.length === 0 && pattern.test(e.textContent.trim())
    );
  }

  // Real court rows only -- excludes the non-court "Show Court" row.
  const courtLabels = leafTextEls(/^Pickleball \\d$/);
  const rows = courtLabels.map(el => {
    const r = el.getBoundingClientRect();
    return { name: el.textContent.trim(), top: r.top, bottom: r.bottom };
  });
  if (rows.length === 0) return { error: 'no court rows found' };

  // Find two of our wanted hour column headers to derive pixel scale.
  // wantedHours is e.g. [18, 19]; we need at least 2 distinct hour labels
  // anywhere on the page to compute px-per-hour, so widen the search to
  // any two consecutive small integers near the wanted range.
  const h0 = wantedHours[0];
  const h1 = wantedHours[0] + 1;
  const labelH0 = leafTextEls(new RegExp('^' + h0 + '$'))[0];
  const labelH1 = leafTextEls(new RegExp('^' + h1 + '$'))[0];
  if (!labelH0 || !labelH1) return { error: 'hour column labels not found' };

  const rH0 = labelH0.getBoundingClientRect();
  const rH1 = labelH1.getBoundingClientRect();
  const pxPerHour = rH1.left - rH0.left;
  const h0X = rH0.left + rH0.width / 2;

  // All genuinely OPEN 1-hour slot elements: white, clickable, ~1hr wide.
  const candidates = Array.from(document.querySelectorAll('div'));
  const oneHourSlots = candidates.filter(el => {
    const cs = getComputedStyle(el);
    if (cs.backgroundColor !== 'rgb(255, 255, 255)') return false;
    if (cs.cursor !== 'pointer') return false;
    const r = el.getBoundingClientRect();
    return r.width > pxPerHour * 0.8 && r.width < pxPerHour * 1.2;
  });

  const result = {};
  for (const row of rows) result[row.name] = [];

  for (const el of oneHourSlots) {
    const r = el.getBoundingClientRect();
    const row = rows.find(rw => r.top >= rw.top - 12 && r.top <= rw.bottom + 12);
    if (!row) continue;
    const elCenterX = r.left + r.width / 2;
    const hour = Math.round((elCenterX - h0X) / pxPerHour) + h0;
    if (wantedHours.includes(hour)) {
      result[row.name].push(hour);
    }
  }

  return result;
}
"""


def check_playtomic():
    """
    Returns a list of dicts: {venue, date, start_time, court, url}
    for open slots that fall in the wanted weekday + hour window.

    Raises an exception if every date's page load/extraction failed, so a
    real outage properly trips the failure-streak alert in main.py instead
    of silently reporting zero results.
    """
    from playwright.sync_api import sync_playwright

    found = []
    today = datetime.now().date()
    wanted_hours = list(range(WANTED_START_HOUR, WANTED_END_HOUR))

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
            url = f"{BASE_URL}?sport=PICKLEBALL&date={date_str}"
            try:
                page.goto(url, wait_until="networkidle", timeout=20000)
                page.wait_for_timeout(800)  # let the schedule grid render
                data = page.evaluate(_EXTRACT_JS, wanted_hours)
            except Exception as e:
                print(f"[playtomic] Error loading {date_str}: {e}")
                failures += 1
                continue

            if isinstance(data, dict) and data.get("error"):
                print(f"[playtomic] {date_str}: {data['error']}")
                failures += 1
                continue

            if DEBUG:
                print(f"  {date_str}: {data}")

            for court_name, hours in data.items():
                for hour in hours:
                    found.append({
                        "venue": VENUE_NAME,
                        "date": date_str,
                        "start_time": f"{hour:02d}:00",
                        "court": court_name,
                        "url": url,
                    })

        if DEBUG:
            page.screenshot(path="playtomic_debug.png", full_page=True)
            print(f"Checked {len(wanted_dates)} date(s), {failures} failure(s). "
                  f"Screenshot saved to playtomic_debug.png")

        browser.close()

    if wanted_dates and failures == len(wanted_dates):
        raise RuntimeError(
            f"All {failures} date loads failed -- site may be down, "
            f"blocking automated browsers, or its layout changed."
        )

    return found


if __name__ == "__main__":
    results = check_playtomic()
    print(json.dumps(results, indent=2))
    print(f"\nFound {len(results)} matching open slot(s).")
