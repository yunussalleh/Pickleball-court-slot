"""
Runs all three venue checkers, groups the individual hourly slots each one
finds into full back-to-back 2-hour blocks (on the SAME court), compares
against previously-seen open blocks (so you're not spammed every run), and
sends a Telegram alert for anything new.

Also tracks each checker's consecutive failure count. If a checker fails
several runs in a row, it sends you a separate warning message -- since a
long silent failure (site blocked us, layout changed, etc.) looks exactly
like "nothing's open" from the outside otherwise.

Why grouping matters: each checker reports individual 1-hour slots (e.g.
"6pm open", "7pm open" as separate entries). But a 2-hour session needs
BOTH consecutive hours open on the SAME court -- 6pm open with 7pm booked
is useless to you. This file is what turns "which hours are open" into
"which actual 2-hour sessions can you book right now".

Usage:
    python main.py

Meant to be run on a schedule (see .github/workflows/check_slots.yml for a
free GitHub Actions setup, or just cron this on your own machine/server).
"""

import json
import os
import traceback
from collections import defaultdict
from datetime import datetime

from config import STATE_FILE, WANTED_START_HOUR, WANTED_END_HOUR
from notifier import send_telegram_message
from checkers.smashing_checker import check_smashing
from checkers.kings_checker import check_kings
from checkers.theark_checker import check_theark
from checkers.pixelpickle_checker import check_pixelpickle

# The full set of consecutive hours a valid session needs, e.g. {18, 19}
# for a 6pm-8pm (2-hour) session. If WANTED_START_HOUR/END_HOUR in
# config.py ever change to a 3-hour window, this adjusts automatically.
REQUIRED_HOURS = set(range(WANTED_START_HOUR, WANTED_END_HOUR))

# How many consecutive failed runs before we warn you that a checker might
# be blocked or broken, rather than just quietly finding nothing.
FAILURE_THRESHOLD = 3

_state_dir = os.path.dirname(STATE_FILE) or "."
FAILURE_STATE_FILE = os.path.join(_state_dir, "failure_streaks.json")

# NOTE: Playtomic (Pickle Padel Movement) is deliberately NOT included
# here. Confirmed via real evidence (CloudFront's own "403 Request
# blocked" error page, captured automatically to debug_failures/ during
# testing) that Playtomic's CDN blocks GitHub Actions' IP ranges outright,
# before any of our code, headers, or browser rendering even comes into
# play. This isn't fixable by changing the checker -- see the detailed
# note at the top of checkers/playtomic_checker.py if you want to run it
# separately from a non-datacenter IP (e.g. your own computer).
#
# NOTE: Franklin Pickleball Singapore is ALSO deliberately not included
# here, disabled on 2026-09-02. Confirmed via a captured screenshot (same
# technique as above) that it hits an identical Cloudflare "Performing
# security verification" bot-challenge page from GitHub Actions' IP --
# see the note at the top of checkers/franklin_checker.py.
CHECKERS = {
    "smashing": check_smashing,
    "kings": check_kings,
    "theark": check_theark,  # Orchid Country Club
    "pixelpickle": check_pixelpickle,  # Pixel Pickle
    # "franklin": check_franklin,  # DISABLED -- Cloudflare bot block, see note above
}


def find_full_blocks(slots: list) -> list:
    """
    Groups individual hourly slots by (venue, date, court) and keeps only
    the groups where EVERY hour in REQUIRED_HOURS is open -- i.e. a real,
    bookable, back-to-back 2-hour session.
    """
    groups = defaultdict(set)
    sample = {}  # one representative slot dict per group, for venue/url info

    for s in slots:
        hour = int(s["start_time"].split(":")[0])
        key = (s["venue"], s["date"], s.get("court", "any"))
        groups[key].add(hour)
        sample[key] = s

    blocks = []
    for key, hours_open in groups.items():
        if REQUIRED_HOURS.issubset(hours_open):
            venue, date, court = key
            ref = sample[key]
            blocks.append({
                "venue": venue,
                "date": date,
                "court": court,
                "start_time": f"{WANTED_START_HOUR:02d}:00",
                "end_time": f"{WANTED_END_HOUR:02d}:00",
                "url": ref["url"],
            })
    return blocks


def block_key(block: dict) -> str:
    return f"{block['venue']}|{block['date']}|{block['court']}|{block['start_time']}-{block['end_time']}"


def load_json_set(path) -> set:
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_json_set(path, data: set):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(sorted(data), f, indent=2)


def load_failure_streaks() -> dict:
    if not os.path.exists(FAILURE_STATE_FILE):
        return {}
    try:
        with open(FAILURE_STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_failure_streaks(streaks: dict):
    os.makedirs(_state_dir, exist_ok=True)
    with open(FAILURE_STATE_FILE, "w") as f:
        json.dump(streaks, f, indent=2)


def run_all_checkers():
    """
    Runs each checker, tracks consecutive failures per checker, and returns
    (all_slots, warning_messages) -- warning_messages is non-empty only when
    a checker just crossed the failure threshold, or just recovered after
    being broken.
    """
    all_slots = []
    warnings = []
    streaks = load_failure_streaks()

    for name, fn in CHECKERS.items():
        try:
            results = fn()
            print(f"[{name}] {len(results)} open hourly slot(s) in window")
            all_slots += results

            prior_streak = streaks.get(name, 0)
            if prior_streak >= FAILURE_THRESHOLD:
                warnings.append(
                    f"✅ <b>{name}</b> checker is working again "
                    f"(had failed {prior_streak} run(s) in a row)."
                )
            streaks[name] = 0

        except Exception as e:
            streaks[name] = streaks.get(name, 0) + 1
            print(f"[{name}] FAILED (streak: {streaks[name]}): {e}")
            traceback.print_exc()

            if streaks[name] == FAILURE_THRESHOLD:
                warnings.append(
                    f"⚠️ <b>{name}</b> checker has failed {FAILURE_THRESHOLD} runs in a row.\n"
                    f"It might be blocked, or the site's layout changed. "
                    f"Worth checking the GitHub Actions log, or running:\n"
                    f"DEBUG=1 python checkers/{name}_checker.py"
                )
            # Deliberately don't re-warn every single run after the first
            # threshold-crossing -- one warning is enough until it recovers.

    save_failure_streaks(streaks)
    return all_slots, warnings


def main():
    all_slots, failure_warnings = run_all_checkers()

    for w in failure_warnings:
        print(w)
        send_telegram_message(w)

    full_blocks = find_full_blocks(all_slots)
    print(f"Found {len(full_blocks)} full back-to-back 2-hour block(s) across all venues.")

    seen = load_json_set(STATE_FILE)
    new_blocks = [b for b in full_blocks if block_key(b) not in seen]

    # Prune state entries for blocks that are no longer fully open (booked
    # again, or the date passed) so a block that closes then reopens will
    # correctly re-alert you next time.
    current_keys = {block_key(b) for b in full_blocks}
    seen = seen & current_keys

    if new_blocks:
        lines = ["🏓 <b>2-hour pickleball session available!</b>", ""]
        for b in sorted(new_blocks, key=lambda x: (x["date"], x["start_time"])):
            court_str = f" ({b['court']})" if b["court"] != "any" else ""
            day_name = datetime.strptime(b["date"], "%Y-%m-%d").strftime("%A")
            lines.append(
                f"• <b>{b['venue']}</b>{court_str}\n"
                f"  {day_name}, {b['date']}, {b['start_time']}\u2013{b['end_time']}\n"
                f"  {b['url']}"
            )
        message = "\n".join(lines)
        print(message)
        send_telegram_message(message)

        for b in new_blocks:
            seen.add(block_key(b))
    else:
        print("No new full 2-hour blocks this run.")

    save_json_set(STATE_FILE, seen)


if __name__ == "__main__":
    main()
