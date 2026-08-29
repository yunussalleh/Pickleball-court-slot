"""
Runs all three venue checkers, compares results against previously-seen
open slots (so you're not spammed every run), and sends a Telegram alert
for anything new.

Usage:
    python main.py

Meant to be run on a schedule (see .github/workflows/check_slots.yml for a
free GitHub Actions setup, or just cron this on your own machine/server).
"""

import json
import os
import sys
import traceback

from config import STATE_FILE
from notifier import send_telegram_message
from checkers.playtomic_checker import check_playtomic
from checkers.smashing_checker import check_smashing
from checkers.kings_checker import check_kings


def slot_key(slot: dict) -> str:
    return f"{slot['venue']}|{slot['date']}|{slot['start_time']}"


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
        print(f"[{name}] {len(results)} open slot(s) in window")
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

    seen = load_seen()
    new_slots = [s for s in all_slots if slot_key(s) not in seen]

    # Also prune slots from `seen` that are no longer open (e.g. the date
    # has passed, or they got booked again) so state doesn't grow forever
    # and so a slot that opens/closes/opens again re-alerts you.
    current_keys = {slot_key(s) for s in all_slots}
    seen = seen & current_keys

    if new_slots:
        lines = ["🏓 <b>New pickleball slot(s) available!</b>", ""]
        for s in sorted(new_slots, key=lambda x: (x["date"], x["start_time"])):
            lines.append(f"• <b>{s['venue']}</b>\n  {s['date']} at {s['start_time']}\n  {s['url']}")
        message = "\n".join(lines)
        print(message)
        send_telegram_message(message)

        for s in new_slots:
            seen.add(slot_key(s))
    else:
        print("No new open slots this run.")

    save_seen(seen)


if __name__ == "__main__":
    main()
