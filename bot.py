# -*- coding: utf-8 -*-
"""
Telegram-бот "консультант": ведёт живой диалог с потенциальным клиентом
через LLM (OpenRouter, бесплатная модель) — выясняет потребность, бюджет и
сроки естественным языком, а не жёсткой анкетой с кнопками. Скоринг
(целевой лид или нет) считается детерминированно в Python по config.py,
LLM только ведёт беседу и сообщает, что удалось выяснить.

Ниша и правила квалификации настраиваются либо в config.py, либо (удобнее)
через админ-панель бота: команда /admin -> мастер по шагам с кнопками.
Изменения из мастера сохраняются в niche_config.json и применяются сразу.

Запуск:
    1) pip install -r requirements.txt
    2) создать .env на основе .env.example и указать BOT_TOKEN, ADMIN_IDS,
       OPENROUTER_API_KEY (бесплатный ключ — openrouter.ai/keys)
    3) python bot.py
"""

import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import BotCommand, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import llm
import storage
from admin import build_admin_conversation

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Состояния диалога: CHATTING — LLM выясняет потребность/бюджет/сроки,
# CONTACT — ждём контакт от уже квалифицированного лида (без LLM).
CHATTING, CONTACT = range(2)

START_BUTTON = "🚀 Оставить заявку"


def _main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[START_BUTTON]], resize_keyboard=True)


def _build_system_prompt(cfg: dict) -> str:
    budget_lines = "\n".join(f'- "{opt}"' for opt in cfg["budget_options"])
    timeline_lines = "\n".join(f'- "{opt}"' for opt in cfg["timeline_options"])
    return (
        f'Ты — бот-консультант в нише «{cfg["name"]}». Веди с клиентом живой, '
        "дружелюбный диалог в Telegram — не анкету по шаблону, не перечисляй "
        "сразу все вопросы, задавай по одному за раз.\n\n"
        "Иди по шагам:\n"
        "1. Сначала выясни потребность клиента — что именно ему нужно. Если "
        f'ответ слишком общий, уточни своими словами в духе: «{cfg["need_clarify_question"]}»\n'
        "2. Когда потребность понятна, спроси про бюджет. Ответ клиента должен "
        "быть сведён РОВНО к одному из вариантов ниже (дословно, ничего не "
        f"выдумывай сверх списка):\n{budget_lines}\n"
        "3. После бюджета спроси про сроки/срочность — тоже ровно один из "
        f"вариантов:\n{timeline_lines}\n\n"
        "Правила: только один вопрос за раз, не забегай вперёд; не выдумывай "
        "ничего о продукте/услуге сверх того, что здесь написано; пиши "
        "коротко и по-человечески, без канцелярита, можно с лёгкими эмодзи."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    cfg = storage.get_config()
    context.user_data["cfg"] = cfg  # фиксируем конфиг на время диалога
    context.user_data["messages"] = [
        {"role": "system", "content": _build_system_prompt(cfg)},
        {"role": "assistant", "content": cfg["greeting"]},
    ]
    await update.message.reply_text(cfg["greeting"], reply_markup=ReplyKeyboardRemove())
    return CHATTING


async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывается, если пользователь пишет боту, не начав диалог кнопкой/командой."""
    await update.message.reply_text(
        f"Нажмите «{START_BUTTON}», чтобы начать 👇", reply_markup=_main_menu_keyboard()
    )


def _qualify(cfg: dict, user_data: dict) -> bool:
    budget_score = cfg["budget_scores"].get(user_data.get("budget"), 0)
    timeline_score = cfg["timeline_scores"].get(user_data.get("timeline"), 0)
    total = budget_score + timeline_score
    user_data["score"] = total
    return total >= cfg["QUALIFY_THRESHOLD"]


def _log_lead(update: Update, user_data: dict, contact: str | None = None) -> None:
    lead = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_id": update.effective_user.id,
        "username": update.effective_user.username,
        "need": user_data.get("need"),
        "budget": user_data.get("budget"),
        "timeline": user_data.get("timeline"),
        "score": user_data.get("score"),
        "qualified": user_data.get("qualified"),
        "contact": contact,
    }
    storage.append_lead(lead)


async def _finish_if_ready(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """Если бюджет и сроки уже известны — считает скоринг и отвечает
    (целевой лид / нет), иначе возвращает None (диалог продолжается)."""
    cfg = context.user_data["cfg"]
    if not (context.user_data.get("budget") and context.user_data.get("timeline")):
        return None

    is_qualified = _qualify(cfg, context.user_data)
    context.user_data["qualified"] = is_qualified

    logger.info(
        "Лид: need=%r budget=%r timeline=%r score=%s qualified=%s",
        context.user_data.get("need"),
        context.user_data.get("budget"),
        context.user_data.get("timeline"),
        context.user_data.get("score"),
        is_qualified,
    )

    if is_qualified:
        await update.message.reply_text(
            cfg["qualified_message"], reply_markup=ReplyKeyboardRemove()
        )
        return CONTACT

    _log_lead(update, context.user_data)
    await update.message.reply_text(
        cfg["not_qualified_message"], reply_markup=_main_menu_keyboard()
    )
    return ConversationHandler.END


async def chatting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cfg = context.user_data["cfg"]
    text = update.message.text

    # Дешёвый путь без LLM: если уже выяснена потребность и ждём именно
    # бюджет/сроки, а ответ клиента дословно (или почти) совпадает с одним
    # из готовых вариантов — не тратим вызов LLM, отвечаем тем же текстом,
    # что и раньше в кнопочном сценарии. LLM нужен только для по-настоящему
    # свободных формулировок.
    if context.user_data.get("need") and not context.user_data.get("budget"):
        matched = storage.exact_match(text, cfg["budget_options"])
        if matched:
            context.user_data["budget"] = matched
            context.user_data["messages"].append({"role": "user", "content": text})
            result = await _finish_if_ready(update, context)
            if result is not None:
                return result
            context.user_data["messages"].append(
                {"role": "assistant", "content": cfg["timeline_question"]}
            )
            await update.message.reply_text(cfg["timeline_question"])
            return CHATTING
    elif context.user_data.get("budget") and not context.user_data.get("timeline"):
        matched = storage.exact_match(text, cfg["timeline_options"])
        if matched:
            context.user_data["timeline"] = matched
            context.user_data["messages"].append({"role": "user", "content": text})
            result = await _finish_if_ready(update, context)
            if result is not None:
                return result

    context.user_data["messages"].append({"role": "user", "content": text})

    try:
        reply_text = await llm.reply(context.user_data["messages"])
    except Exception:
        logger.exception("Ошибка обращения к LLM")
        await update.message.reply_text(
            "Извините, сейчас не получается ответить — попробуйте, пожалуйста, ещё раз."
        )
        return CHATTING

    context.user_data["messages"].append({"role": "assistant", "content": reply_text})
    await update.message.reply_text(reply_text)

    try:
        state = await llm.extract(context.user_data["messages"], cfg)
    except Exception:
        logger.exception("Ошибка извлечения данных из диалога")
        state = {}

    if state.get("need"):
        context.user_data["need"] = state["need"]
    if state.get("budget"):
        context.user_data["budget"] = storage.match_option(state["budget"], cfg["budget_options"])
    if state.get("timeline"):
        context.user_data["timeline"] = storage.match_option(state["timeline"], cfg["timeline_options"])

    return await _finish_if_ready(update, context) or CHATTING


async def contact_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cfg = context.user_data["cfg"]
    context.user_data["contact"] = update.message.text
    logger.info("Контакт целевого лида получен: %r", context.user_data.get("contact"))
    _log_lead(update, context.user_data, contact=context.user_data["contact"])
    await update.message.reply_text(cfg["thanks_after_contact"], reply_markup=_main_menu_keyboard())
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Диалог прерван.", reply_markup=_main_menu_keyboard())
    return ConversationHandler.END


async def setup_commands(application: Application) -> None:
    """Меню команд (кнопка «/» в Telegram) — красивые подписи вместо голых команд."""
    await application.bot.set_my_commands([
        BotCommand("start", "🚀 Оставить заявку"),
        BotCommand("cancel", "❌ Прервать диалог"),
        BotCommand("admin", "⚙️ Панель администратора"),
    ])


def build_application(token: str) -> Application:
    """Собирает Application бота: сценарий диалога + админ-панель.

    Общая точка входа и для polling/Render-webhook (main() ниже), и для
    WSGI-обёртки на PythonAnywhere (wsgi_app.py), чтобы обработчики не дублировались.
    """
    application = Application.builder().token(token).post_init(setup_commands).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex(f"^{START_BUTTON}$"), start),
        ],
        states={
            CHATTING: [MessageHandler(filters.TEXT & ~filters.COMMAND, chatting)],
            CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, contact_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Админ-панель регистрируется в отдельной, более приоритетной группе (-1):
    # иначе, пока у администратора идёт диалог квалификации лида (conv_handler
    # ниже "держит" все его сообщения в своём текущем состоянии), команда
    # /admin до неё просто не доходит и обновление тихо отбрасывается.
    application.add_handler(build_admin_conversation(), group=-1)

    application.add_handler(conv_handler)
    # Ловит сообщения вне диалога (первый заход без /start) — показывает кнопку запуска.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, welcome))
    return application


def main() -> None:
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Не найден BOT_TOKEN. Создайте .env на основе .env.example и укажите токен."
        )

    application = build_application(token)

    logger.info("Бот запущен (ниша: %s)", storage.get_config()["name"])

    # На Render (и подобных PaaS) публичный URL сервиса и порт приходят через
    # переменные окружения — если они заданы, работаем через webhook, иначе
    # (локальная разработка) — обычный polling.
    external_url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("WEBHOOK_URL")
    if external_url:
        port = int(os.getenv("PORT", "10000"))
        webhook_path = token  # секретный путь = токен, чтобы левые запросы не долетали до бота
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=webhook_path,
            webhook_url=f"{external_url.rstrip('/')}/{webhook_path}",
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
