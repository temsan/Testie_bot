#!/bin/sh
# Запускает Chromium на виртуальном дисплее Xvfb с CDP на 127.0.0.1:9222
# (доступен только внутри контейнера — avito_bot.py подключается к нему
# локально, наружу этот порт не публикуется).
set -e

PROFILE_DIR="${CHROME_USER_DATA_DIR:-/data/chrome-profile}"
mkdir -p "$PROFILE_DIR"

exec chromium \
    --no-sandbox \
    --disable-gpu \
    --disable-dev-shm-usage \
    --window-size=1280,800 \
    --user-data-dir="$PROFILE_DIR" \
    --remote-debugging-port=9222 \
    --remote-debugging-address=127.0.0.1 \
    --no-first-run \
    --no-default-browser-check \
    --password-store=basic \
    "https://www.avito.ru/profile/messenger"
