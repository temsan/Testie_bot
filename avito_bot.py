# -*- coding: utf-8 -*-
"""
Автономный бот квалификации лидов через Avito Messenger.

Подключается к уже открытому Chrome пользователя (запущенному с флагом
--remote-debugging-port=9222) через Playwright CDP, переиспользуя
авторизованную сессию продавца на avito.ru — повторный логин не нужен.

Алгоритм по стадиям (NEED -> BUDGET -> TIMELINE -> CONTACT -> DONE) описан в
AVITO_BOT_PLAYBOOK.md. Скоринг считается детерминированно из
config.py/storage.py (как в bot.py); OpenRouter LLM используется только для
сопоставления свободного текста лида с одним из готовых вариантов ответа
(budget_options / timeline_options), не для математики.

СЕЛЕКТОРЫ Avito Messenger в блоке SELECTORS ниже подобраны по общим
конвенциям верстки Avito и формату URL чата
(/profile/messenger/channel/<id>), но не сверены с живой разметкой — ни эта
среда, ни эта сессия не имеют доступа к браузеру с авторизованной сессией
Avito. Перед первым реальным запуском откройте DevTools на
https://www.avito.ru/profile/messenger и поправьте SELECTORS при
необходимости.

Запуск:
    1) Открыть Chrome с флагом --remote-debugging-port=9222, залогиниться
       на avito.ru как продавец, оставить открытой любую вкладку.
    2) pip install -r requirements.txt
    3) .env: OPENROUTER_API_KEY (+ опционально OPENROUTER_MODEL, CDP_URL,
       POLL_INTERVAL_SECONDS) — см. .env.example
    4) python avito_bot.py
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional, Tuple

import requests
from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright

import storage

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("avito_bot")

STATE_PATH = Path(__file__).parent / "avito_state.json"
MESSENGER_URL = "https://www.avito.ru/profile/messenger"
CHAT_URL_RE = re.compile(r"/profile/messenger/channel/([\w-]+)")

# Системные/служебные чаты, которым бот не должен писать.
# a2u-191717891-444005368 — известный системный чат «Поддержка Авито»
# (см. CLAUDE.md / AVITO_BOT_PLAYBOOK.md), название дублируется на случай смены id.
SYSTEM_CHAT_IDS = {"a2u-191717891-444005368"}
SYSTEM_CHAT_TITLES = {"Поддержка Авито"}

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# --- Селекторы Avito Messenger ---------------------------------------------
# Не сверены с живой разметкой (см. докстринг модуля выше) — проверить/
# поправить через DevTools при первом запуске.
SELECTORS = {
    "unread_filter_button": "text=Непрочитанные",
    "chat_link": 'a[href*="/profile/messenger/channel/"]',
    "chat_title": '[class*="messenger-header"] h2, [class*="messenger-header"] [class*="title"]',
    "message_item": '[class*="messenger-message"], [class*="message-item"]',
    "message_input": '[contenteditable="true"], textarea[class*="messenger-input"]',
    "send_button": 'button[class*="messenger"][class*="send"], button[aria-label*="Отправ"]',
}


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Не удалось прочитать %s, начинаю с пустого состояния", STATE_PATH)
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# --- OpenRouter: классификация свободного текста в один из вариантов -------

def classify_option(user_text: str, question: str, options: list) -> str:
    """Сопоставляет свободный ответ лида с одним из готовых вариантов.

    Если лид написал номер варианта — сопоставляем без обращения к LLM.
    Иначе просим модель выбрать номер по смыслу. При недоступности
    OpenRouter/ошибке возвращаем последний вариант (по конвенции config.py
    это "не определился"/аналог), чтобы не терять диалог из-за сбоя API.
    """
    stripped = user_text.strip()
    if stripped.isdigit():
        idx = int(stripped) - 1
        if 0 <= idx < len(options):
            return options[idx]

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("OPENROUTER_API_KEY не задан — классификация недоступна, беру последний вариант")
        return options[-1]

    numbered = "\n".join(f"{i + 1}. {opt}" for i, opt in enumerate(options))
    prompt = (
        f"Вопрос клиенту: {question}\n"
        f"Варианты ответа:\n{numbered}\n\n"
        f"Ответ клиента: {user_text!r}\n\n"
        "Определи, какому варианту ближе всего по смыслу ответ клиента. "
        "Ответь только номером варианта, без пояснений."
    )
    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free"),
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 10,
                "temperature": 0,
            },
            timeout=20,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        match = re.search(r"\d+", content)
        if match:
            idx = int(match.group()) - 1
            if 0 <= idx < len(options):
                return options[idx]
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        logger.warning("OpenRouter classify_option failed: %s", exc)

    return options[-1]


# --- Скоринг (детерминированно, как в bot.py) -------------------------------

def qualify(cfg: dict, chat_state: dict) -> Tuple[int, bool]:
    budget_score = cfg["budget_scores"].get(chat_state.get("budget"), 0)
    timeline_score = cfg["timeline_scores"].get(chat_state.get("timeline"), 0)
    total = budget_score + timeline_score
    return total, total >= cfg["QUALIFY_THRESHOLD"]


# --- Playwright: чтение и отправка сообщений --------------------------------

def get_unread_chat_ids(page: Page) -> list:
    page.goto(MESSENGER_URL, wait_until="domcontentloaded")
    try:
        page.locator(SELECTORS["unread_filter_button"]).first.click(timeout=3000)
        page.wait_for_timeout(1000)
    except Exception:
        logger.info("Фильтр «Непрочитанные» не найден, беру все чаты из списка")

    hrefs = page.locator(SELECTORS["chat_link"]).evaluate_all(
        "els => els.map(e => e.getAttribute('href'))"
    )
    chat_ids = []
    for href in hrefs:
        if not href:
            continue
        m = CHAT_URL_RE.search(href)
        if m:
            chat_ids.append(m.group(1))
    return list(dict.fromkeys(chat_ids))  # без дублей, с сохранением порядка


def open_chat(page: Page, chat_id: str) -> None:
    page.goto(f"{MESSENGER_URL}/channel/{chat_id}", wait_until="domcontentloaded")
    page.wait_for_timeout(800)


def get_chat_title(page: Page) -> str:
    try:
        return page.locator(SELECTORS["chat_title"]).first.inner_text(timeout=2000).strip()
    except Exception:
        return ""


def get_last_incoming_message(page: Page) -> Optional[str]:
    """Возвращает текст последнего входящего (не нашего) сообщения в чате.

    Эвристика: свои сообщения обычно выровнены справа, входящие — слева
    (см. AVITO_BOT_PLAYBOOK.md) — сравниваем x-координату message-блоков
    вместо того чтобы полагаться на конкретный CSS-класс "своего" сообщения.
    """
    items = page.locator(SELECTORS["message_item"])
    count = items.count()
    if count == 0:
        return None

    viewport = page.viewport_size or {"width": 1280}
    mid_x = viewport["width"] / 2

    for i in range(count - 1, -1, -1):
        item = items.nth(i)
        text = item.inner_text().strip()
        if not text:
            continue
        box = item.bounding_box()
        if not box or box["x"] + box["width"] / 2 < mid_x:
            return text
    return None


def send_message(page: Page, text: str) -> None:
    input_box = page.locator(SELECTORS["message_input"]).first
    input_box.click()
    input_box.fill(text)
    try:
        page.locator(SELECTORS["send_button"]).first.click(timeout=2000)
    except Exception:
        input_box.press("Enter")


# --- Логика одного тика по одному чату --------------------------------------

def handle_chat(page: Page, chat_id: str, state: dict, cfg: dict) -> None:
    open_chat(page, chat_id)
    title = get_chat_title(page)

    if chat_id in SYSTEM_CHAT_IDS or title in SYSTEM_CHAT_TITLES:
        return

    incoming = get_last_incoming_message(page)
    if not incoming:
        return

    chat_state = state.setdefault(chat_id, {
        "title": title,
        "stage": "NEED",
        "need": None,
        "budget": None,
        "timeline": None,
        "contact": None,
        "score": 0,
        "qualified": None,
        "last_seen_text": None,
    })
    chat_state["title"] = title

    if incoming == chat_state.get("last_seen_text"):
        return  # лид ничего нового не написал

    stage = chat_state["stage"]

    if stage == "NEED":
        chat_state["need"] = incoming
        numbered = "\n".join(f"{i + 1}. {opt}" for i, opt in enumerate(cfg["budget_options"]))
        send_message(
            page,
            f"{cfg['budget_question']}\n{numbered}\n\n(напишите номер варианта или ответьте свободно)",
        )
        chat_state["stage"] = "BUDGET"

    elif stage == "BUDGET":
        chat_state["budget"] = classify_option(incoming, cfg["budget_question"], cfg["budget_options"])
        numbered = "\n".join(f"{i + 1}. {opt}" for i, opt in enumerate(cfg["timeline_options"]))
        send_message(
            page,
            f"{cfg['timeline_question']}\n{numbered}\n\n(напишите номер варианта или ответьте свободно)",
        )
        chat_state["stage"] = "TIMELINE"

    elif stage == "TIMELINE":
        chat_state["timeline"] = classify_option(incoming, cfg["timeline_question"], cfg["timeline_options"])
        score, qualified = qualify(cfg, chat_state)
        chat_state["score"] = score
        chat_state["qualified"] = qualified
        if qualified:
            send_message(page, cfg["qualified_message"])
            chat_state["stage"] = "CONTACT"
        else:
            send_message(page, cfg["not_qualified_message"])
            chat_state["stage"] = "DONE"

    elif stage == "CONTACT":
        chat_state["contact"] = incoming
        send_message(page, cfg["thanks_after_contact"])
        chat_state["stage"] = "DONE"

    elif stage == "DONE":
        send_message(page, "Ваши данные уже переданы менеджеру, мы скоро свяжемся с вами. Спасибо!")
        chat_state["last_seen_text"] = incoming
        return

    chat_state["last_seen_text"] = incoming
    logger.info("chat=%s stage %s -> %s", chat_id, stage, chat_state["stage"])


# --- Основной цикл ------------------------------------------------------------

def tick(page: Page, state: dict) -> None:
    cfg = storage.get_config()
    chat_ids = get_unread_chat_ids(page)
    processed = 0
    qualified_count = 0
    not_qualified_count = 0

    for chat_id in chat_ids:
        prior = state.get(chat_id, {})
        prior_stage = prior.get("stage")
        prior_qualified = prior.get("qualified")
        try:
            handle_chat(page, chat_id, state, cfg)
        except Exception:
            logger.exception("Ошибка обработки чата %s", chat_id)
            continue

        after = state.get(chat_id, {})
        if after.get("stage") != prior_stage:
            processed += 1
        if prior_qualified is None and after.get("qualified") is True:
            qualified_count += 1
        elif prior_qualified is None and after.get("qualified") is False:
            not_qualified_count += 1

    save_state(state)
    logger.info(
        "Тик завершён: чатов обработано=%d, квалифицировано=%d, не квалифицировано=%d",
        processed, qualified_count, not_qualified_count,
    )


def main() -> None:
    load_dotenv()
    cdp_url = os.getenv("CDP_URL", "http://localhost:9222")
    poll_interval = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))

    state = load_state()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()

        logger.info(
            "Подключено к Chrome по CDP (%s), старт мониторинга (интервал=%ss)",
            cdp_url, poll_interval,
        )
        try:
            while True:
                tick(page, state)
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            logger.info("Остановлено пользователем")


if __name__ == "__main__":
    main()
