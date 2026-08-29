"""
Runs all three venue checkers, groups the individual hourly slots each one
finds into full back-to-back 2-hour blocks (on the SAME court), compares
against previously-seen open blocks (so you're not spammed every run), and
sends a Telegram alert for anything new.

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

from config import STATE_FILE, WANTED_START_HOUR, WANTED_END_HOUR
from notifier import send_telegram_message
from checkers.playtomic_checker import check_playtomic
from checkers.smashing_checker import check_smashing
from checkers.kings_checker import check_kings

# The full set of consecutive hours a valid session needs, e.g. {18, 19}
# for a 6pm-8pm (2-hour) session. If WANTED_START_HOUR/END_HOUR in
# config.py ever change to a 3-hour window, this adjusts automatically.
REQUIRED_HOURS = set(range(WANTED_START_HOUR, WANTED_END_HOUR))


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


def load_seen() -> set:
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(seen: set):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(sorted(seen), f, indent=2)


def run_checker_safely(name, fn):
    try:
        results = fn()
        print(f"[{name}] {len(results)} open hourly slot(s) in window")
        return results
    except Exception as e:
        print(f"[{name}] FAILED: {e}")
        traceback.print_exc()
        return []


def main():
    all_slots = []
    all_slots += run_checker_safely("playtomic", check_playtomic)
    all_slots += run_checker_safely("smashing", check_smashing)
    all_slots += run_checker_safely("kings", check_kings)

    full_blocks = find_full_blocks(all_slots)
    print(f"Found {len(full_blocks)} full back-to-back 2-hour block(s) across all venues.")

    seen = load_seen()
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
            lines.append(
                f"• <b>{b['venue']}</b>{court_str}\n"
                f"  {b['date']}, {b['start_time']}\u2013{b['end_time']}\n"
                f"  {b['url']}"
            )
        message = "\n".join(lines)
        print(message)
        send_telegram_message(message)

        for b in new_blocks:
            seen.add(block_key(b))
    else:
        print("No new full 2-hour blocks this run.")

    save_seen(seen)


if __name__ == "__main__":
    main()
