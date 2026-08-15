#!/bin/sh
# Chrome стартует не мгновенно — ждём, пока CDP на 9222 откликнется, прежде
# чем запускать avito_bot.py (иначе первая попытка connect_over_cdp упадёт).
until curl -sf http://127.0.0.1:9222/json/version >/dev/null 2>&1; do
    echo "avito_bot: жду Chrome CDP на :9222..."
    sleep 1
done
exec /opt/venv/bin/python /app/avito_bot.py
