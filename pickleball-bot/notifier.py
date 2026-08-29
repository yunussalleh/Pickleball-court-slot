"""
Sends Telegram messages via the Bot API.

Setup (one-time, ~2 minutes):
1. In Telegram, message @BotFather -> /newbot -> follow prompts.
   You'll get a token like 123456789:AAExampleTokenXXXXXXXXXXXXXXXXXXX
2. Message your new bot anything (e.g. "hi") so it's allowed to message you back.
3. Get your chat_id: visit
      https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   after step 2, and look for "chat":{"id": ...}
4. Set these as environment variables (or GitHub Actions secrets):
      TELEGRAM_BOT_TOKEN
      TELEGRAM_CHAT_ID
"""

import os
import urllib.request
import urllib.parse
import json


def send_telegram_message(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("[notifier] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; "
              "printing message instead:\n" + text)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()

    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
            if not body.get("ok"):
                print(f"[notifier] Telegram API error: {body}")
                return False
            return True
    except Exception as e:
        print(f"[notifier] Failed to send Telegram message: {e}")
        return False


if __name__ == "__main__":
    # Quick test: python notifier.py
    ok = send_telegram_message("🏓 Pickleball bot: test notification. If you see this, it works!")
    print("Sent OK" if ok else "Failed / not configured")
