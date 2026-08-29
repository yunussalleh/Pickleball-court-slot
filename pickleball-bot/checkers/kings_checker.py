"""
Checks Kings Pickleball Arena via their Rezerv booking portal.

VERIFIED LIVE against the real site. Here's what was actually confirmed by
opening the page and inspecting it directly:

- The court booking page is https://kingspickleball.rezerv.co/appointment/386dc17b-9650-4ca6-8fad-1642c6b2629b
  (there's a *separate* apptId for "Paddle Rental" on the same site --
  don't mix them up).
- After clicking "Continue" once, you land on a persistent
  appointment-booking page with a react-datepicker calendar on the left
  and a 4-court x 15-hour (7am-9pm) slot grid on the right.
- Each bookable date IS a real react-datepicker day cell with class
      react-datepicker__day react-datepicker__day--0XX
  (zero-padded 3-digit day-of-month), and dates outside the booking window
  do carry an extra "react-datepicker__day--disabled" class -- this part
  was correct.
- HOWEVER: a real production bug was found and fixed here on 2026-08-30.
  The month label and prev/next navigation arrows do NOT use the standard
  react-datepicker__current-month / react-datepicker__navigation--next
  class names -- this is a heavily custom-skinned picker with anonymous,
  auto-generated CSS class names (e.g. "dgCmgm gnpHiA") that give no
  stable hook to select by class at all. The original code assumed the
  standard class names, which don't exist on this page; since the
  navigation silently failed every time, the checker was actually reading
  whatever month happened to already be displayed (e.g. checking "day 11"
  while still viewing August, landing on a PAST date that's correctly
  marked disabled) -- causing real, currently-open dates like a Sept
  Friday to be silently skipped as "outside booking window" when they
  were actually wide open. Confirmed via live testing with real evidence
  (the exact button/label elements the old selectors were looking for
  simply don't exist: document.querySelector() returned null for both).
  Fixed by finding the month label via its TEXT content (matches
  "Month YYYY") and finding the next/prev arrow buttons by their VISUAL
  POSITION relative to that label (same row, to the right = next, to the
  left = previous) instead of by class name.
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
- Kings' own picker only ever shows 2 months (current + next) -- once
  you've navigated to the far one, "next" becomes genuinely disabled.
  That's normal, not a bug.

If Kings changes their site layout again, this is the file to fix -- run
with DEBUG=1 to open a visible browser and see what it's finding:
    DEBUG=1 python checkers/kings_checker.py
"""

import os
import re
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


# Finds the month label (text like "September 2026") and the next/prev
# arrow buttons by POSITION relative to it, since this page's date picker
# has no stable class names for either -- confirmed live that the
# "standard" react-datepicker class names simply don't exist here.
_MONTH_NAV_JS = """
() => {
  const label = Array.from(document.querySelectorAll('p'))
    .find(e => /^[A-Z][a-z]+ \\d{4}$/.test(e.textContent.trim()));
  if (!label) return { error: 'month label not found' };
  const lr = label.getBoundingClientRect();
  const buttons = Array.from(document.querySelectorAll('button')).filter(b => {
    const r = b.getBoundingClientRect();
    return Math.abs(r.top - lr.top) < 20 && r.width < 50 && r.width > 10;
  });
  const nextBtn = buttons.find(b => b.getBoundingClientRect().x > lr.x);
  return {
    currentMonth: label.textContent.trim(),
    nextExists: !!nextBtn,
    nextDisabled: nextBtn ? nextBtn.disabled : null,
  };
}
"""

_CLICK_NEXT_MONTH_JS = """
() => {
  const label = Array.from(document.querySelectorAll('p'))
    .find(e => /^[A-Z][a-z]+ \\d{4}$/.test(e.textContent.trim()));
  if (!label) return false;
  const lr = label.getBoundingClientRect();
  const buttons = Array.from(document.querySelectorAll('button')).filter(b => {
    const r = b.getBoundingClientRect();
    return Math.abs(r.top - lr.top) < 20 && r.width < 50 && r.width > 10;
  });
  const nextBtn = buttons.find(b => b.getBoundingClientRect().x > lr.x);
  if (!nextBtn || nextBtn.disabled) return false;
  nextBtn.click();
  return true;
}
"""


def _reload_to_picker(page):
    """
    Full reset: reload the booking page from scratch and get back to the
    date/slot picker. Used for recovery when something goes wrong for one
    date, so a single glitch (slow network, one bad render) can't cascade
    into every subsequent date failing too -- confirmed via live testing
    that once the page gets into a bad state, it can stay that way for
    the rest of the run otherwise.
    """
    page.goto(KINGS_URL, wait_until="networkidle")
    page.get_by_text("Continue", exact=True).click()
    page.wait_for_selector(".react-datepicker__day", timeout=15000)


def check_kings():
    from playwright.sync_api import sync_playwright

    found = []
    today = datetime.now().date()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not DEBUG)
        page = browser.new_page()
        _reload_to_picker(page)

        checked = 0

        for offset in range(DAYS_AHEAD):
            date = today + timedelta(days=offset)
            if date.weekday() not in WANTED_WEEKDAYS:
                continue

            try:
                target_label = date.strftime("%B %Y")  # e.g. "September 2026"

                # Click "next month" until the calendar shows the month we
                # need, using position-based detection (see _MONTH_NAV_JS).
                reached_target = False
                for _ in range(4):
                    nav_info = page.evaluate(_MONTH_NAV_JS)
                    if nav_info.get("error"):
                        if DEBUG:
                            print(f"  {date}: {nav_info['error']}, skipping")
                        break
                    if nav_info["currentMonth"] == target_label:
                        reached_target = True
                        break
                    if nav_info["nextDisabled"] or not nav_info["nextExists"]:
                        if DEBUG:
                            print(f"  {date}: can't navigate further "
                                  f"(stuck on {nav_info['currentMonth']}), skipping")
                        break
                    page.evaluate(_CLICK_NEXT_MONTH_JS)
                    page.wait_for_timeout(400)

                if not reached_target:
                    # Not necessarily an error (could genuinely be outside
                    # the site's own booking window) -- no reload needed.
                    continue

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

                # Wait for the slot grid to ACTUALLY finish loading (up to
                # 60 real .appt_slot cells), rather than a fixed timer --
                # confirmed via live testing that a flat wait sometimes
                # wasn't long enough, reading a stale/empty grid instead.
                try:
                    page.wait_for_function(
                        "() => document.querySelectorAll('.appt_slot').length === 60",
                        timeout=8000,
                    )
                except Exception:
                    pass  # fall through and read whatever's there

                checked += 1
                class_lists = page.evaluate(_extract_slots_js())

                if DEBUG:
                    print(f"  {date}: got {len(class_lists)} slot cells")

                if len(class_lists) != 4 * len(HOURS):
                    # Grid didn't finish loading, or layout changed -- bail
                    # out for this date AND reload, so a stuck/slow page
                    # doesn't ruin every date that follows.
                    if DEBUG:
                        print(f"  {date}: unexpected cell count, reloading and skipping")
                    _reload_to_picker(page)
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

            except Exception as e:
                # Any unexpected error for this one date -- log it, reload
                # to a clean state, and move on rather than letting it
                # silently break every remaining date in this run.
                if DEBUG:
                    print(f"  {date}: error ({e}), reloading and skipping")
                try:
                    _reload_to_picker(page)
                except Exception:
                    pass

        if DEBUG:
            page.screenshot(path="kings_debug.png", full_page=True)
            print(f"Checked {checked} date(s). Screenshot saved to kings_debug.png")

        browser.close()

    return found


if __name__ == "__main__":
    results = check_kings()
    print(json.dumps(results, indent=2))
    print(f"\nFound {len(results)} matching open slot(s).")
