# -*- coding: utf-8 -*-
"""
WSGI-обёртка бота для бесплатного хостинга на PythonAnywhere (карта не нужна).

PythonAnywhere free-тариф даёт один постоянно работающий веб-апп (WSGI,
через uWSGI) — но не позволяет запускать собственный процесс с event loop,
как это делает bot.py (application.run_webhook / run_polling). Поэтому
здесь Application из python-telegram-bot собирается один раз при импорте
модуля, инициализируется на общем asyncio event loop, а каждый входящий
POST-запрос от Telegram синхронно обрабатывается на этом же loop.

Как подключить на PythonAnywhere:
    1) Web -> Add a new web app -> Manual configuration -> Python 3.x.
    2) В консоли: pip install --user -r requirements.txt
    3) В .env (или Web -> Environment variables) указать BOT_TOKEN, ADMIN_IDS.
    4) В файле WSGI configuration file (ссылка есть на странице Web) заменить
       содержимое на:
           import sys
           path = "/home/<your-username>/<repo-dir>"
           if path not in sys.path:
               sys.path.insert(0, path)
           from wsgi_app import flask_app as application
    5) Reload web app.
    6) Один раз выполнить set_webhook.py (см. ниже), указав URL вида
       https://<your-username>.pythonanywhere.com/<BOT_TOKEN>
"""

import asyncio
import logging
import os

from dotenv import load_dotenv
from flask import Flask, request
from telegram import Update

from bot import build_application

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "Не найден BOT_TOKEN. Задайте его в .env или в Web -> Environment variables на PythonAnywhere."
    )

telegram_app = build_application(TOKEN)

# Один общий event loop на весь процесс — переиспользуется между запросами,
# чтобы не пересоздавать Application и не терять context.user_data между сообщениями.
_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)
_loop.run_until_complete(telegram_app.initialize())

flask_app = Flask(__name__)


@flask_app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    update = Update.de_json(request.get_json(force=True), telegram_app.bot)
    _loop.run_until_complete(telegram_app.process_update(update))
    return "ok"


@flask_app.route("/", methods=["GET"])
def index():
    return "Бот запущен."
