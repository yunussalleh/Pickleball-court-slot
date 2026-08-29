"""
Checks Kings Pickleball Arena via their Rezerv booking portal.

VERIFIED LIVE against the real site (unlike the first draft of this file,
which was an unverified best-guess). Here's what was actually confirmed by
opening the page and inspecting it directly:

- The court booking page is https://kingspickleball.rezerv.co/appointment/386dc17b-9650-4ca6-8fad-1642c6b2629b
  (there's a *separate* apptId for "Paddle Rental" on the same site --
  don't mix them up).
- After clicking "Continue" once, you land on a persistent
  appointment-booking page with a react-datepicker calendar on the left
  and a 4-court x 15-hour (7am-9pm) slot grid on the right.
- Each bookable date is a real HTML calendar day cell:
      .react-datepicker__day.react-datepicker__day--0XX
  Dates outside the booking window carry an extra
      react-datepicker__day--disabled
  class -- confirmed the window is roughly ~11 days ahead.
- Each of the 60 grid cells has class "appt_slot", in DOM order
  Court1[7am..9pm], Court2[7am..9pm], Court3[...], Court4[...].
- An OPEN/bookable slot's class list includes "cursor-pointer" and does
  NOT include "pointer-events-none". I confirmed this by actually
  clicking one (it turned black/selected, exactly like a real booking
  flow). A slot that's unavailable/blocked instead carries
  "pointer-events-none" and does nothing when clicked -- confirmed by
  testing both states side by side.
- Selected = black background, Booked-by-someone-else = green background;
  neither of those count as "open" for our purposes.
"""

import os
import json
from datetime import datetime, timedelta

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import KINGS_URL, WANTED_WEEKDAYS, WANTED_START_HOUR, WANTED_END_HOUR, DAYS_AHEAD

VENUE_NAME = "Kings Pickleball Arena (Havelock)"
DEBUG = os.environ.get("DEBUG") == "1"

# Grid is always these 15 hourly columns, 7am through 9pm, in this order.
HOURS = list(range(7, 22))  # 7,8,...,21 (24h clock)


def _extract_slots_js():
    return """
    () => Array.from(document.querySelectorAll('.appt_slot')).map(el => el.className)
    """


def check_kings():
    from playwright.sync_api import sync_playwright

    found = []
    today = datetime.now().date()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not DEBUG)
        page = browser.new_page()
        page.goto(KINGS_URL, wait_until="networkidle")

        # Land on the appointment info page; click Continue to reach the
        # actual date/slot picker.
        page.get_by_text("Continue", exact=True).click()
        page.wait_for_selector(".react-datepicker__day", timeout=15000)

        checked = 0
        current_month_label = None

        for offset in range(DAYS_AHEAD):
            date = today + timedelta(days=offset)
            if date.weekday() not in WANTED_WEEKDAYS:
                continue

            target_label = date.strftime("%B %Y")  # e.g. "September 2026"

            # Click "next month" arrow until the calendar shows the month
            # we need (rarely more than once, since our window is short).
            for _ in range(3):
                month_el = page.locator(".react-datepicker__current-month")
                if month_el.count() == 0:
                    break
                label = month_el.first.inner_text().strip()
                if label == target_label:
                    break
                next_btn = page.locator(".react-datepicker__navigation--next")
                if next_btn.count() == 0:
                    break
                next_btn.first.click()
                page.wait_for_timeout(300)

            day_str = f"{date.day:03d}"
            day_cell = page.locator(
                f".react-datepicker__day--{day_str}:not(.react-datepicker__day--outside-month)"
            )
            if day_cell.count() == 0:
                if DEBUG:
                    print(f"  {date}: day cell not found, skipping")
                continue

            cls = day_cell.first.get_attribute("class") or ""
            if "disabled" in cls:
                if DEBUG:
                    print(f"  {date}: outside booking window (disabled), skipping")
                continue

            day_cell.first.click()
            page.wait_for_timeout(700)  # let slot grid re-render
            checked += 1

            class_lists = page.evaluate(_extract_slots_js())

            if DEBUG:
                print(f"  {date}: got {len(class_lists)} slot cells")

            if len(class_lists) != 4 * len(HOURS):
                # Layout changed (different court count?) -- bail out for
                # this date rather than mis-map hours to the wrong court.
                if DEBUG:
                    print(f"  {date}: unexpected cell count, skipping")
                continue

            for idx, cls_str in enumerate(class_lists):
                hour = HOURS[idx % len(HOURS)]
                if not (WANTED_START_HOUR <= hour < WANTED_END_HOUR):
                    continue
                court_num = (idx // len(HOURS)) + 1
                is_open = ("pointer-events-none" not in cls_str) and ("cursor-pointer" in cls_str)
                if DEBUG:
                    print(f"    Court{court_num} {hour}:00 open={is_open}")
                if is_open:
                    found.append({
                        "venue": VENUE_NAME,
                        "date": date.isoformat(),
                        "start_time": f"{hour:02d}:00",
                        "court": f"Court {court_num}",
                        "url": KINGS_URL,
                    })

        if DEBUG:
            page.screenshot(path="kings_debug.png", full_page=True)
            print(f"Checked {checked} date(s). Screenshot saved to kings_debug.png")

        browser.close()

    return found


if __name__ == "__main__":
    results = check_kings()
    print(json.dumps(results, indent=2))
    print(f"\nFound {len(results)} matching open slot(s).")
