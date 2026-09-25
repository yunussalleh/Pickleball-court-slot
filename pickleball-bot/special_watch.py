"""
ONE-OFF SPECIAL WATCH: Kings Pickleball Arena, October 16 2026, 4pm-6pm,
needing AT LEAST 2 of the 4 courts open (any 2, not specific ones).

This is separate from the main Fri/Sat/Sun 6-8pm system in main.py /
config.py on purpose -- it's a personal one-time need with a different
date, a different time window, and a different requirement (2 courts,
not just 1), so it doesn't make sense to bend the shared global config
for it. This file is self-contained: it reuses the exact same verified
Kings scraping approach as checkers/kings_checker.py (position-based
month navigation, react-datepicker day cells, .appt_slot extraction,
retry-with-reload resilience), just pointed at this specific
date/time/requirement instead.

HOW TO REMOVE THIS LATER: once you've booked Oct 16 (or no longer need
it), just remove the `import` and the call to `check_special_watch()` in
main.py -- this file can stay or be deleted, it won't run either way.

Usage (called automatically from main.py every run):
    from special_watch import check_special_watch
    check_special_watch()
"""

import os
from datetime import date

from config import KINGS_URL
from notifier import send_telegram_message

DEBUG = os.environ.get("DEBUG") == "1"

# ---- Edit these if your need changes ----
TARGET_DATE = date(2026, 10, 16)
START_HOUR = 16  # 4pm
END_HOUR = 18    # 6pm (i.e. checking the 4pm and 5pm hourly slots)
MIN_COURTS_NEEDED = 2
# -------------------------------------------

STATE_FILE = "state/special_watch_seen.json"
HOURS = list(range(7, 22))  # Kings' grid is always 7am-9pm, 15 hourly columns


def _reload_to_picker(page):
    page.goto(KINGS_URL, wait_until="networkidle")
    page.get_by_text("Continue", exact=True).click()
    page.wait_for_selector(".react-datepicker__day", timeout=15000)


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

_EXTRACT_SLOTS_JS = "() => Array.from(document.querySelectorAll('.appt_slot')).map(el => el.className)"


def _load_seen() -> bool:
    if not os.path.exists(STATE_FILE):
        return False
    try:
        with open(STATE_FILE) as f:
            return f.read().strip() == "true"
    except Exception:
        return False


def _save_seen(value: bool):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        f.write("true" if value else "false")


def check_special_watch():
    """
    Checks Kings for TARGET_DATE and alerts (once) if at least
    MIN_COURTS_NEEDED courts have every hour in [START_HOUR, END_HOUR)
    open. Won't re-alert every run once found -- only alerts again if it
    later drops below the threshold and then comes back up (e.g. someone
    grabs a court then cancels).
    """
    from playwright.sync_api import sync_playwright

    target_label = TARGET_DATE.strftime("%B %Y")
    day_str = f"{TARGET_DATE.day:03d}"

    open_courts = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not DEBUG)
            page = browser.new_page()
            _reload_to_picker(page)

            reached_target = False
            for _ in range(6):
                nav_info = page.evaluate(_MONTH_NAV_JS)
                if nav_info.get("error"):
                    if DEBUG:
                        print(f"[special_watch] {nav_info['error']}")
                    break
                if nav_info["currentMonth"] == target_label:
                    reached_target = True
                    break
                if nav_info["nextDisabled"] or not nav_info["nextExists"]:
                    if DEBUG:
                        print(f"[special_watch] can't navigate further "
                              f"(stuck on {nav_info['currentMonth']})")
                    break
                page.evaluate(_CLICK_NEXT_MONTH_JS)
                page.wait_for_timeout(400)

            if not reached_target:
                if DEBUG:
                    print(f"[special_watch] Oct 16 not reachable yet "
                          f"(outside Kings' own booking window)")
                browser.close()
                return

            day_cell = page.locator(
                f".react-datepicker__day--{day_str}:not(.react-datepicker__day--outside-month)"
            )
            if day_cell.count() == 0:
                if DEBUG:
                    print("[special_watch] day cell not found")
                browser.close()
                return

            cls = day_cell.first.get_attribute("class") or ""
            if "disabled" in cls:
                if DEBUG:
                    print("[special_watch] Oct 16 outside booking window (disabled)")
                browser.close()
                return

            day_cell.first.click()
            try:
                page.wait_for_function(
                    "() => document.querySelectorAll('.appt_slot').length === 60",
                    timeout=15000,
                )
            except Exception:
                pass

            class_lists = page.evaluate(_EXTRACT_SLOTS_JS)

            if len(class_lists) != 4 * len(HOURS):
                if DEBUG:
                    print(f"[special_watch] unexpected cell count: {len(class_lists)}")
                browser.close()
                return

            wanted_hours = set(range(START_HOUR, END_HOUR))
            for court_idx in range(4):
                court_num = court_idx + 1
                all_open = True
                for hour in wanted_hours:
                    hour_pos = HOURS.index(hour)
                    idx = court_idx * len(HOURS) + hour_pos
                    cls_str = class_lists[idx]
                    is_open = ("pointer-events-none" not in cls_str) and ("cursor-pointer" in cls_str)
                    if DEBUG:
                        print(f"[special_watch] Court {court_num} {hour}:00 open={is_open}")
                    if not is_open:
                        all_open = False
                        break
                if all_open:
                    open_courts.append(f"Court {court_num}")

            browser.close()

    except Exception as e:
        print(f"[special_watch] Error checking Kings for Oct 16: {e}")
        return

    print(f"[special_watch] Oct 16, 4-6pm: {len(open_courts)} court(s) open ({open_courts})")

    currently_meets_threshold = len(open_courts) >= MIN_COURTS_NEEDED
    was_seen = _load_seen()

    if currently_meets_threshold and not was_seen:
        courts_str = ", ".join(open_courts)
        message = (
            "🏓 <b>Kings Oct 16 special alert!</b>\n\n"
            f"{len(open_courts)} courts open 4pm\u20136pm: <b>{courts_str}</b>\n"
            f"{KINGS_URL}"
        )
        print(message)
        send_telegram_message(message)

    _save_seen(currently_meets_threshold)


if __name__ == "__main__":
    check_special_watch()
