# -*- coding: utf-8 -*-
"""
Одноразовый скрипт: сообщает Telegram, куда слать обновления (webhook URL).
Запускается один раз после деплоя на PythonAnywhere (в Bash-консоли):

    python set_webhook.py https://<your-username>.pythonanywhere.com

URL webhook'а собирается как <base_url>/<BOT_TOKEN> — тот же путь,
который слушает wsgi_app.py.
"""

import json
import os
import sys
import urllib.request

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    if len(sys.argv) != 2:
        print("Использование: python set_webhook.py https://<your-username>.pythonanywhere.com")
        sys.exit(1)

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Не найден BOT_TOKEN в .env")

    base_url = sys.argv[1].rstrip("/")
    webhook_url = f"{base_url}/{token}"

    payload = json.dumps({
        "url": webhook_url,
        "allowed_updates": ["message", "callback_query"],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/setWebhook",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(resp.read().decode("utf-8"))


if __name__ == "__main__":
    main()
