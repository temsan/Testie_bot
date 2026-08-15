# -*- coding: utf-8 -*-
"""
Telegram-бот "консультант": ведёт диалог с потенциальным клиентом,
задаёт квалифицирующие вопросы (потребность, бюджет, сроки),
определяет целевой лид или нет, и либо предлагает следующий шаг
(запрашивает контакт), либо вежливо закрывает диалог.

Ниша и правила квалификации настраиваются в config.py без изменения кода.

Запуск:
    1) pip install -r requirements.txt
    2) создать .env на основе .env.example и указать BOT_TOKEN
    3) python bot.py
"""

import logging
import os

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

from config import NICHE, QUALIFICATION_RULES

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
    await update.message.reply_text(NICHE["greeting"], reply_markup=ReplyKeyboardRemove())
    return NEED


async def need_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["need"] = update.message.text
    await update.message.reply_text(
        NICHE["budget_question"],
        reply_markup=_keyboard(NICHE["budget_options"]),
    )
    return BUDGET


async def budget_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    answer = update.message.text
    context.user_data["budget"] = answer
    await update.message.reply_text(
        NICHE["timeline_question"],
        reply_markup=_keyboard(NICHE["timeline_options"]),
    )
    return TIMELINE


def _qualify(user_data: dict) -> bool:
    budget_score = QUALIFICATION_RULES["budget_scores"].get(user_data.get("budget"), 0)
    timeline_score = QUALIFICATION_RULES["timeline_scores"].get(user_data.get("timeline"), 0)
    total = budget_score + timeline_score
    user_data["score"] = total
    return total >= QUALIFICATION_RULES["QUALIFY_THRESHOLD"]


async def timeline_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["timeline"] = update.message.text

    is_qualified = _qualify(context.user_data)
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
            NICHE["qualified_message"], reply_markup=ReplyKeyboardRemove()
        )
        return CONTACT

    await update.message.reply_text(
        NICHE["not_qualified_message"], reply_markup=ReplyKeyboardRemove()
    )
    await update.message.reply_text(NICHE["restart_hint"])
    return ConversationHandler.END


async def contact_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["contact"] = update.message.text
    logger.info("Контакт целевого лида получен: %r", context.user_data.get("contact"))
    await update.message.reply_text(NICHE["thanks_after_contact"])
    await update.message.reply_text(NICHE["restart_hint"])
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Диалог прерван. " + NICHE["restart_hint"], reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


def main() -> None:
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Не найден BOT_TOKEN. Создайте .env на основе .env.example и укажите токен."
        )

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

    logger.info("Бот запущен (ниша: %s)", NICHE["name"])
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
