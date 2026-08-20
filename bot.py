# -*- coding: utf-8 -*-
"""
Telegram-бот "консультант": ведёт диалог с потенциальным клиентом,
задаёт квалифицирующие вопросы (потребность, бюджет, сроки),
определяет целевой лид или нет, и либо предлагает следующий шаг
(запрашивает контакт), либо вежливо закрывает диалог.

Ниша и правила квалификации настраиваются либо в config.py, либо (удобнее)
через админ-панель бота: команда /admin -> мастер по шагам с кнопками.
Изменения из мастера сохраняются в niche_config.json и применяются сразу.

Запуск:
    1) pip install -r requirements.txt
    2) создать .env на основе .env.example и указать BOT_TOKEN (и ADMIN_IDS)
    3) python bot.py
"""

import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import storage
from admin import build_admin_conversation

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Состояния диалога
NEED, BUDGET, TIMELINE, CONTACT = range(4)


def _keyboard(options):
    return ReplyKeyboardMarkup(
        [[opt] for opt in options], one_time_keyboard=True, resize_keyboard=True
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    cfg = storage.get_config()
    context.user_data["cfg"] = cfg  # фиксируем конфиг на время диалога
    await update.message.reply_text(cfg["greeting"], reply_markup=ReplyKeyboardRemove())
    return NEED


async def need_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cfg = context.user_data["cfg"]
    context.user_data["need"] = update.message.text
    await update.message.reply_text(
        cfg["budget_question"],
        reply_markup=_keyboard(cfg["budget_options"]),
    )
    return BUDGET


async def budget_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cfg = context.user_data["cfg"]
    context.user_data["budget"] = storage.match_option(update.message.text, cfg["budget_options"])
    await update.message.reply_text(
        cfg["timeline_question"],
        reply_markup=_keyboard(cfg["timeline_options"]),
    )
    return TIMELINE


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


async def timeline_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cfg = context.user_data["cfg"]
    context.user_data["timeline"] = storage.match_option(update.message.text, cfg["timeline_options"])

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
        cfg["not_qualified_message"], reply_markup=ReplyKeyboardRemove()
    )
    await update.message.reply_text(cfg["restart_hint"])
    return ConversationHandler.END


async def contact_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cfg = context.user_data["cfg"]
    context.user_data["contact"] = update.message.text
    logger.info("Контакт целевого лида получен: %r", context.user_data.get("contact"))
    _log_lead(update, context.user_data, contact=context.user_data["contact"])
    await update.message.reply_text(cfg["thanks_after_contact"])
    await update.message.reply_text(cfg["restart_hint"])
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cfg = context.user_data.get("cfg") or storage.get_config()
    await update.message.reply_text(
        "Диалог прерван. " + cfg["restart_hint"], reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


def build_application(token: str) -> Application:
    """Собирает Application бота: сценарий диалога + админ-панель.

    Общая точка входа и для polling/Render-webhook (main() ниже), и для
    WSGI-обёртки на PythonAnywhere (wsgi_app.py), чтобы обработчики не дублировались.
    """
    application = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NEED: [MessageHandler(filters.TEXT & ~filters.COMMAND, need_received)],
            BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, budget_received)],
            TIMELINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, timeline_received)],
            CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, contact_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)
    application.add_handler(build_admin_conversation())
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
