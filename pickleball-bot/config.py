"""
Configuration for the pickleball slot watcher.

Edit WANTED_DAYS / WANTED_START_HOUR / WANTED_END_HOUR to change what
you're watching for. Times are in 24h format, Singapore local time.
"""

# Days of week you want (0=Monday ... 5=Saturday, 6=Sunday)
WANTED_WEEKDAYS = {4, 5, 6}  # Friday, Saturday, Sunday

# The 2-hour window you want, e.g. 18 means 6:00pm, 20 means 8:00pm
WANTED_START_HOUR = 18
WANTED_END_HOUR = 20

# How many days ahead to check for each venue
DAYS_AHEAD = 10

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
