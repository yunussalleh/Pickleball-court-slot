"""
Sends Telegram messages (and now photos) via the Bot API.

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
import mimetypes
import uuid


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


def send_telegram_photo(photo_path: str, caption: str = "") -> bool:
    """
    Sends a local image file as a Telegram photo message, with an
    optional caption. Used so a checker can show visual proof of what it
    actually saw at the moment it decided a slot was open -- useful for
    spotting a false positive directly, instead of just trusting the text
    alert.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print(f"[notifier] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; "
              f"would have sent photo: {photo_path}")
        return False

    if not os.path.exists(photo_path):
        print(f"[notifier] Photo not found, skipping: {photo_path}")
        return False

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    boundary = uuid.uuid4().hex
    mime_type = mimetypes.guess_type(photo_path)[0] or "application/octet-stream"

    with open(photo_path, "rb") as f:
        file_bytes = f.read()

    def _field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()

    body = b""
    body += _field("chat_id", chat_id)
    if caption:
        body += _field("caption", caption)
        body += _field("parse_mode", "HTML")
    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="photo"; filename="{os.path.basename(photo_path)}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode()
    body += file_bytes
    body += f"\r\n--{boundary}--\r\n".encode()

    try:
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_body = json.loads(resp.read().decode())
            if not resp_body.get("ok"):
                print(f"[notifier] Telegram sendPhoto API error: {resp_body}")
                return False
            return True
    except Exception as e:
        print(f"[notifier] Failed to send Telegram photo: {e}")
        return False


if __name__ == "__main__":
    # Quick test: python notifier.py
    ok = send_telegram_message("🏓 Pickleball bot: test notification. If you see this, it works!")
    print("Sent OK" if ok else "Failed / not configured")
