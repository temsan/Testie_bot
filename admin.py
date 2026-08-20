# -*- coding: utf-8 -*-
"""
Админ-панель бота: мастер настройки ниши/сценария по шагам, с кнопками.

Доступ только для Telegram user_id из ADMIN_IDS (.env).
Открывается командой /admin.

Мастер последовательно спрашивает: название ниши, приветствие, вопрос и
варианты бюджета с весами, вопрос и варианты сроков с весами, порог
квалификации, и три финальных сообщения. Результат сохраняется через
storage.save_config() и сразу же применяется в bot.py (конфиг читается
заново при каждом новом диалоге с клиентом).
"""

import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import storage

# Состояния диалога админ-мастера
MENU, STEP_TEXT, STEP_LIST, STEP_WEIGHT, STEP_NUMBER, CONFIRM, EDIT_PICK, EDIT_VALUE = range(8)

# Поля, доступные для точечного редактирования (подмножество WIZARD_STEPS без списков/весов —
# их проще менять только целиком через мастер)
EDITABLE_FIELDS = [
    "name", "greeting", "budget_question", "timeline_question",
    "QUALIFY_THRESHOLD", "qualified_message", "not_qualified_message", "thanks_after_contact",
]

# Описание шагов мастера — порядок важен
WIZARD_STEPS = [
    {"key": "name", "type": "text", "title": "Название ниши",
     "prompt": "Введите название ниши (для внутренних логов), например:\n«Ремонт квартир»"},
    {"key": "greeting", "type": "text", "title": "Приветствие",
     "prompt": "Введите текст приветствия + вопрос о потребности клиента.\n"
               "Например:\n«Здравствуйте! Расскажите, что вас интересует?»"},
    {"key": "budget_question", "type": "text", "title": "Вопрос про бюджет",
     "prompt": "Введите текст вопроса про бюджет.\nНапример:\n«Какой бюджет вы рассматриваете?»"},
    {"key": "budget_options", "type": "list", "title": "Варианты бюджета",
     "prompt": "Отправляйте варианты ответа на вопрос о бюджете по одному "
               "(например: «До 50 000 ₽»). Когда добавите все — нажмите «✅ Готово»."},
    {"key": "budget_scores", "type": "weight_for", "source": "budget_options",
     "title": "Веса вариантов бюджета",
     "prompt": "Укажите вес (важность для квалификации, 0 — минимум, 3 — максимум) "
               "для варианта бюджета:"},
    {"key": "timeline_question", "type": "text", "title": "Вопрос про сроки",
     "prompt": "Введите текст вопроса про сроки.\nНапример:\n«Когда планируете начать?»"},
    {"key": "timeline_options", "type": "list", "title": "Варианты сроков",
     "prompt": "Отправляйте варианты ответа на вопрос о сроках по одному "
               "(например: «Срочно, в ближайшие дни»). Когда закончите — «✅ Готово»."},
    {"key": "timeline_scores", "type": "weight_for", "source": "timeline_options",
     "title": "Веса вариантов сроков",
     "prompt": "Укажите вес (0–3) для варианта срока:"},
    {"key": "QUALIFY_THRESHOLD", "type": "number", "title": "Порог квалификации",
     "prompt": "Начиная с какой суммы баллов (бюджет + сроки) лид считается целевым?",
     "min": 0, "max": 6},
    {"key": "qualified_message", "type": "text", "title": "Сообщение целевому лиду",
     "prompt": "Введите сообщение, которое увидит целевой лид (с предложением созвона)."},
    {"key": "not_qualified_message", "type": "text", "title": "Сообщение нецелевому лиду",
     "prompt": "Введите сообщение вежливого закрытия для нецелевого лида."},
    {"key": "thanks_after_contact", "type": "text", "title": "Благодарность после контакта",
     "prompt": "Введите сообщение-благодарность после того, как лид оставил контакт."},
]


def _admin_ids() -> set:
    raw = os.getenv("ADMIN_IDS", "")
    return {int(x) for x in raw.replace(" ", "").split(",") if x.strip().isdigit()}


def is_admin(user_id: int) -> bool:
    return user_id in _admin_ids()


def _progress(idx: int) -> str:
    return f"Шаг {idx + 1}/{len(WIZARD_STEPS)}"


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧙 Настроить нишу (мастер)", callback_data="wizard_start")],
        [InlineKeyboardButton("✏️ Изменить поле", callback_data="edit_field")],
        [InlineKeyboardButton("👀 Текущий конфиг", callback_data="view_config")],
        [InlineKeyboardButton("📋 Последние лиды", callback_data="view_leads")],
        [InlineKeyboardButton("🔄 Сбросить на дефолт", callback_data="reset_config")],
        [InlineKeyboardButton("❌ Закрыть", callback_data="close")],
    ])


def _step_by_key(key: str) -> dict:
    return next(s for s in WIZARD_STEPS if s["key"] == key)


async def admin_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ только для администратора.")
        return ConversationHandler.END
    await update.message.reply_text(
        "⚙️ *Админ-панель бота*\n\nВыберите действие:",
        parse_mode="Markdown",
        reply_markup=_menu_keyboard(),
    )
    return MENU


async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "close":
        await query.edit_message_text("Готово. Введите /admin, чтобы открыть панель снова.")
        return ConversationHandler.END

    if query.data == "view_config":
        cfg = storage.get_config()
        lines = [f"*{k}:* {v}" for k, v in cfg.items()]
        text = "📋 *Текущий конфиг:*\n\n" + "\n".join(lines)
        await query.edit_message_text(text[:4000], parse_mode="Markdown", reply_markup=_menu_keyboard())
        return MENU

    if query.data == "reset_config":
        storage.reset_config()
        await query.edit_message_text("✅ Конфиг сброшен на значения по умолчанию.", reply_markup=_menu_keyboard())
        return MENU

    if query.data == "view_leads":
        leads = storage.get_leads(limit=10)
        if not leads:
            text = "📋 Пока нет ни одного зафиксированного лида."
        else:
            lines = ["📋 *Последние лиды:*\n"]
            for lead in leads:
                mark = "✅" if lead.get("qualified") else "❌"
                lines.append(
                    f"{mark} {lead.get('timestamp', '')[:16]} @{lead.get('username') or lead.get('user_id')}\n"
                    f"   need: {lead.get('need')}\n"
                    f"   бюджет: {lead.get('budget')} | сроки: {lead.get('timeline')} | score: {lead.get('score')}\n"
                    f"   контакт: {lead.get('contact') or '—'}"
                )
            text = "\n\n".join(lines)
        await query.edit_message_text(text[:4000], parse_mode="Markdown", reply_markup=_menu_keyboard())
        return MENU

    if query.data == "wizard_start":
        context.user_data["wizard"] = {"step": 0, "draft": {}}
        return await _send_step(update, context)

    if query.data == "edit_field":
        rows = [
            [InlineKeyboardButton(_step_by_key(key)["title"], callback_data=f"editkey_{key}")]
            for key in EDITABLE_FIELDS
        ]
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_menu")])
        await query.edit_message_text(
            "✏️ *Какое поле изменить?*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows)
        )
        return EDIT_PICK

    return MENU


async def _send_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    wiz = context.user_data["wizard"]
    step = WIZARD_STEPS[wiz["step"]]
    text = f"🧙 *{_progress(wiz['step'])} — {step['title']}*\n\n{step['prompt']}"

    send = update.callback_query.edit_message_text if update.callback_query else update.message.reply_text

    if step["type"] == "text":
        await send(text, parse_mode="Markdown")
        return STEP_TEXT

    if step["type"] == "list":
        wiz["draft"].setdefault(step["key"], [])
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data="list_done")]])
        await send(text, parse_mode="Markdown", reply_markup=kb)
        return STEP_LIST

    if step["type"] == "weight_for":
        wiz["weight_idx"] = 0
        return await _ask_weight(update, context)

    if step["type"] == "number":
        kb = _number_keyboard(step.get("min", 0), step.get("max", 6))
        await send(text, parse_mode="Markdown", reply_markup=kb)
        return STEP_NUMBER

    return MENU


def _number_keyboard(lo: int, hi: int) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(str(n), callback_data=f"num_{n}") for n in range(lo, hi + 1)]
    rows = [row[i:i + 4] for i in range(0, len(row), 4)]
    return InlineKeyboardMarkup(rows)


async def _ask_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    wiz = context.user_data["wizard"]
    step = WIZARD_STEPS[wiz["step"]]
    items = wiz["draft"].get(step["source"], [])

    if wiz["weight_idx"] >= len(items):
        wiz["draft"].setdefault(step["key"], {})
        return await _advance(update, context)

    item = items[wiz["weight_idx"]]
    text = (f"🧙 *{_progress(wiz['step'])} — {step['title']}*\n\n"
            f"{step['prompt']}\n\n«{item}»")
    kb = _number_keyboard(0, 3)

    send = update.callback_query.edit_message_text if update.callback_query else update.message.reply_text
    await send(text, parse_mode="Markdown", reply_markup=kb)
    return STEP_WEIGHT


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    wiz = context.user_data["wizard"]
    step = WIZARD_STEPS[wiz["step"]]
    wiz["draft"][step["key"]] = update.message.text.strip()
    return await _advance(update, context)


async def on_list_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    wiz = context.user_data["wizard"]
    step = WIZARD_STEPS[wiz["step"]]
    wiz["draft"][step["key"]].append(update.message.text.strip())

    items = wiz["draft"][step["key"]]
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data="list_done")]])
    await update.message.reply_text(
        "Добавлено: «{}»\nВсего вариантов: {}\n\nОтправьте ещё или нажмите «✅ Готово».".format(
            items[-1], len(items)
        ),
        reply_markup=kb,
    )
    return STEP_LIST


async def on_list_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    wiz = context.user_data["wizard"]
    step = WIZARD_STEPS[wiz["step"]]

    if not wiz["draft"].get(step["key"]):
        await query.answer("Добавьте хотя бы один вариант!", show_alert=True)
        return STEP_LIST

    return await _advance(update, context)


async def on_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    value = int(query.data.split("_")[1])
    wiz = context.user_data["wizard"]
    step = WIZARD_STEPS[wiz["step"]]

    if step["type"] == "weight_for":
        items = wiz["draft"][step["source"]]
        item = items[wiz["weight_idx"]]
        wiz["draft"][step["key"]][item] = value
        wiz["weight_idx"] += 1
        return await _ask_weight(update, context)

    wiz["draft"][step["key"]] = value
    return await _advance(update, context)


async def _advance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    wiz = context.user_data["wizard"]
    wiz["step"] += 1

    if wiz["step"] >= len(WIZARD_STEPS):
        return await _show_confirm(update, context)

    return await _send_step(update, context)


async def _show_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data["wizard"]["draft"]
    summary = (
        f"✅ *Готово! Проверьте настройки:*\n\n"
        f"*Ниша:* {draft.get('name')}\n"
        f"*Приветствие:* {draft.get('greeting')}\n"
        f"*Варианты бюджета:* {', '.join(draft.get('budget_options', []))}\n"
        f"*Варианты сроков:* {', '.join(draft.get('timeline_options', []))}\n"
        f"*Порог квалификации:* {draft.get('QUALIFY_THRESHOLD')}\n"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💾 Сохранить", callback_data="save")],
        [InlineKeyboardButton("🔁 Начать заново", callback_data="wizard_start")],
        [InlineKeyboardButton("❌ Отмена", callback_data="close")],
    ])

    send = update.callback_query.edit_message_text if update.callback_query else update.message.reply_text
    await send(summary, parse_mode="Markdown", reply_markup=kb)
    return CONFIRM


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "wizard_start":
        context.user_data["wizard"] = {"step": 0, "draft": {}}
        return await _send_step(update, context)

    if query.data == "close":
        await query.edit_message_text("Отменено, изменения не сохранены. /admin — открыть панель снова.")
        return ConversationHandler.END

    if query.data == "save":
        cfg = storage.get_config()
        cfg.update(context.user_data["wizard"]["draft"])
        storage.save_config(cfg)
        await query.edit_message_text("💾 Сохранено! Новые настройки уже применяются к боту.")
        return ConversationHandler.END

    return CONFIRM


async def edit_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "back_to_menu":
        await query.edit_message_text("⚙️ *Админ-панель бота*\n\nВыберите действие:",
                                       parse_mode="Markdown", reply_markup=_menu_keyboard())
        return MENU

    key = query.data.removeprefix("editkey_")
    step = _step_by_key(key)
    context.user_data["edit_key"] = key
    current = storage.get_config().get(key)

    if step["type"] == "number":
        kb = _number_keyboard(step.get("min", 0), step.get("max", 6))
        await query.edit_message_text(
            f"✏️ *{step['title']}*\n\nТекущее значение: {current}\n\n{step['prompt']}",
            parse_mode="Markdown", reply_markup=kb,
        )
        return EDIT_VALUE

    await query.edit_message_text(
        f"✏️ *{step['title']}*\n\nТекущее значение:\n«{current}»\n\nВведите новое значение:",
        parse_mode="Markdown",
    )
    return EDIT_VALUE


async def edit_value_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    key = context.user_data["edit_key"]
    cfg = storage.get_config()
    cfg[key] = update.message.text.strip()
    storage.save_config(cfg)
    await update.message.reply_text(
        f"💾 Сохранено! «{_step_by_key(key)['title']}» обновлено.", reply_markup=_menu_keyboard()
    )
    return MENU


async def edit_value_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    key = context.user_data["edit_key"]
    value = int(query.data.split("_")[1])
    cfg = storage.get_config()
    cfg[key] = value
    storage.save_config(cfg)
    await query.edit_message_text(
        f"💾 Сохранено! «{_step_by_key(key)['title']}» обновлено на {value}.", reply_markup=_menu_keyboard()
    )
    return MENU


async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def build_admin_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("admin", admin_entry)],
        states={
            MENU: [CallbackQueryHandler(menu_router)],
            STEP_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_text)],
            STEP_LIST: [
                CallbackQueryHandler(on_list_done, pattern="^list_done$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_list_item),
            ],
            STEP_WEIGHT: [CallbackQueryHandler(on_number, pattern="^num_")],
            STEP_NUMBER: [CallbackQueryHandler(on_number, pattern="^num_")],
            CONFIRM: [CallbackQueryHandler(on_confirm)],
            EDIT_PICK: [CallbackQueryHandler(edit_pick)],
            EDIT_VALUE: [
                CallbackQueryHandler(edit_value_number, pattern="^num_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    )
