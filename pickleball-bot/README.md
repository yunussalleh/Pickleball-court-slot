# Pickleball Slot Watcher (SG)

Watches for **Fri/Sat/Sun 6–8pm** openings at:

- **Pickle Padel Movement** (PUB Recreation Club, Woodleigh) — via Playtomic
- **Smashing Pickle** (Jurong Play Grounds) — via app.smashing.sg
- **Kings Pickleball Arena** (Havelock) — via their Rezerv booking portal

and sends you a **Telegram message** the moment a matching slot opens up.
It does **not** auto-book — you still tap through to book it yourself once notified.

## How it actually finds slots

- **Playtomic**: calls an undocumented (but widely used) read-only endpoint
  Playtomic's own app uses. This could break or start requiring auth without
  notice — it's not an official API.
- **Smashing & Kings**: neither has a public API, so the script opens a real
  (headless) browser with [Playwright](https://playwright.dev) and reads the
  actual booking calendar, the same way you would with your eyes. This is
  slower but far more reliable long-term, since it doesn't depend on
  reverse-engineered internals.

**Update:** all three checkers, including Kings, have now been verified
against the live sites — I opened each booking page directly, inspected the
real HTML, and (for Kings) actually clicked a real slot to confirm which
CSS classes mean "open" vs. "blocked" before writing the matching code. If
any site changes its layout in the future, `DEBUG=1 python
checkers/kings_checker.py` (or `smashing_checker.py`) opens a visible
browser and dumps what it's seeing, screenshot included, so you can spot
what changed.

## 1. One-time setup

### a) Create a Telegram bot (2 minutes)
1. In Telegram, message **@BotFather** → send `/newbot` → follow the prompts.
   You'll get a token like `123456789:AAExampleTokenXXXXXXXXXXXXXXXXXXX`.
2. Send your new bot any message (e.g. "hi") so it's allowed to reply to you.
3. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser
   and find `"chat":{"id": ...}` — that number is your chat ID.

### b) Put this project on GitHub
1. Create a new **private** GitHub repo and push this folder to it.
2. In the repo, go to **Settings → Secrets and variables → Actions** and add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. That's it — `.github/workflows/check_slots.yml` will run automatically
   every 15 minutes, for free, using GitHub's infrastructure. You don't need
   to keep any computer running.

### c) Test it once manually
In the repo's **Actions** tab, open "Check pickleball slots" → **Run workflow**.
Check the run logs, and check your Telegram.

## 2. Running it yourself instead (no GitHub)

```bash
pip install -r requirements.txt
playwright install --with-deps chromium

export TELEGRAM_BOT_TOKEN="123456789:AAE..."
export TELEGRAM_CHAT_ID="123456789"

python main.py
```

Run this on a schedule with `cron` (Mac/Linux) or Task Scheduler (Windows) —
e.g. `*/15 * * * *` for every 15 minutes. The machine needs to be on and
online for each scheduled run.

## 3. Customizing

Edit `config.py`:
- `WANTED_WEEKDAYS` — which days to watch (0=Mon ... 6=Sun)
- `WANTED_START_HOUR` / `WANTED_END_HOUR` — the time window
- `DAYS_AHEAD` — how far ahead to look

## 4. If a checker breaks

Booking sites change their layouts. If you stop getting alerts you'd expect:

```bash
DEBUG=1 python checkers/smashing_checker.py
DEBUG=1 python checkers/kings_checker.py
```

`DEBUG=1` opens a visible browser window, prints every slot button and
whether it was read as open/closed, and saves a screenshot — compare that
against what the real site shows and adjust the selectors.

## 5. A note on terms of service

This checks public availability pages at a modest frequency (default every
15 min) — it doesn't log in, doesn't book, and doesn't hammer the sites. That
said, automated access can still brush up against a site's terms of service.
Use it for personal convenience, not at high frequency or at scale.
