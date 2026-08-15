# -*- coding: utf-8 -*-
"""
Разовый ЛОКАЛЬНЫЙ скрипт (запускать на своей машине, не на хостинге):
сохраняет авторизованную сессию Avito (cookies + localStorage) в JSON-файл,
чтобы потом запускать avito_bot.py на хостинге без CDP и без повторного
логина/пароля/SMS.

Пароль и логин здесь нигде не участвуют — только cookies уже залогиненной
сессии, которую Playwright читает из вашего открытого Chrome через CDP.

Запуск:
    1) Chrome запущен с флагом --remote-debugging-port=9222, вы залогинены
       на avito.ru как продавец (страница https://www.avito.ru/profile
       открыта и доступна).
    2) python export_avito_session.py
    3) Файл avito_storage_state.json появится рядом (уже в .gitignore, не
       коммитить). Значение AVITO_STORAGE_STATE_B64 из вывода — вставить в
       переменные окружения хостинга (см. AVITO_BOT_PLAYBOOK.md, раздел
       «Деплой на Railway»).

Сессию Avito нужно переэкспортировать заново, когда она истечёт
(cookies разлогинят бота — тик начнёт падать с ошибками загрузки страницы).
"""

import base64
import os

from playwright.sync_api import sync_playwright

OUTPUT_PATH = "avito_storage_state.json"
CDP_URL = os.getenv("CDP_URL", "http://localhost:9222")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        if not browser.contexts:
            raise RuntimeError(
                "Не найден открытый контекст браузера — откройте avito.ru в Chrome "
                f"(CDP: {CDP_URL})"
            )
        context = browser.contexts[0]
        context.storage_state(path=OUTPUT_PATH)

    print(f"Сессия сохранена в {OUTPUT_PATH}")

    with open(OUTPUT_PATH, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    print("\nЗначение для переменной окружения AVITO_STORAGE_STATE_B64:\n")
    print(b64)


if __name__ == "__main__":
    main()
