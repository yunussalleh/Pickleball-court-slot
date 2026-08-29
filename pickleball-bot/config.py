"""
Configuration for the pickleball slot watcher.

Edit WANTED_DAYS / WANTED_START_HOUR / WANTED_END_HOUR to change what
you're watching for. Times are in 24h format, Singapore local time.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")


def get_today():
    """
    Returns "today" as a date, in Singapore local time, regardless of what
    timezone the machine actually running this script is set to.

    This matters more than it sounds like it should: GitHub Actions
    runners default to UTC, while a local Mac here was set to Singapore
    time (UTC+8). Between roughly midnight and 8am SGT, `datetime.now()`
    (which uses the system's own local time) disagreed between the two by
    a full calendar day -- e.g. GitHub Actions computed "today" as one day
    EARLIER than a local run did. Since DAYS_AHEAD counts forward from
    "today", this silently shifted production's entire lookout window
    back by a day, causing it to consistently miss the single
    furthest-out date every checker was supposed to check (confirmed via
    live side-by-side testing: a local run correctly found real,
    genuinely-open slots on a date that a same-moment production run
    never even looked at). Always compute "today" the same explicit way,
    everywhere, so local runs and GitHub Actions runs never disagree.
    """
    return datetime.now(SGT).date()


# Days of week you want (0=Monday ... 5=Saturday, 6=Sunday)
WANTED_WEEKDAYS = {4, 5, 6}  # Friday, Saturday, Sunday

# The 2-hour window you want, e.g. 18 means 6:00pm, 20 means 8:00pm
WANTED_START_HOUR = 18
WANTED_END_HOUR = 20

# How many days ahead to check for each venue. 14 matches Playtomic's own
# booking window; Smashing (7 days) and Kings (~11 days) will simply cap
# out at their own shorter limits -- no errors, just fewer dates checked.
DAYS_AHEAD = 14

# Playtomic tenant id for Pickle Padel Movement (PUB Recreation Club)
# Source: https://playtomic.io/tenant/2987e8fb-6130-4508-a3e1-fe7a329d446f
PLAYTOMIC_TENANT_ID = "2987e8fb-6130-4508-a3e1-fe7a329d446f"

# Smashing Pickle booking app
SMASHING_URL = "https://app.smashing.sg"

# Kings Pickleball Arena Rezerv booking page (court booking, verified live --
# not the paddle rental page, which lives at a different apptId)
KINGS_URL = "https://kingspickleball.rezerv.co/appointment/386dc17b-9650-4ca6-8fad-1642c6b2629b"

# Where we remember which slots we've already alerted on, so you don't
# get the same notification every run.
STATE_FILE = "state/seen_slots.json"
