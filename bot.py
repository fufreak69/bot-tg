import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)
import google.generativeai as genai

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Gemini setup ────────────────────────────────────────────────────────────
genai.configure(api_key=os.environ["GEMINI_API_KEY"])
gemini = genai.GenerativeModel(
    model_name="gemini-1.5-flash",  # бесплатный, быстрый
    system_instruction="""Ты — профессиональный академический ассистент, специализирующийся на написании студенческих работ на русском языке.

Ты помогаешь писать курсовые работы, дипломные работы, рефераты, отчёты по практике и магистерские диссертации.

Правила:
- Всегда пиши на академическом русском языке
- Соблюдай структуру научного текста: введение, основная часть, заключение
- Используй профессиональную терминологию по теме
- Ссылайся на актуальные источники (указывай реальные научные работы)
- Объём каждого раздела — не менее 400–600 слов
- В введении обязательно: актуальность, цель, задачи, объект, предмет, методы
- Пиши развёрнуто, структурированно, с подзаголовками внутри разделов
- Не используй воду, каждое предложение должно нести смысл
""",
)

# ─── States ─────────────────────────────────────────────────────────────────
(
    STATE_MENU,
    STATE_WORK_TYPE,
    STATE_TOPIC,
    STATE_DETAILS,
    STATE_GENERATING,
    STATE_CHAT,
) = range(6)

# ─── In-memory storage ───────────────────────────────────────────────────────
user_data_store: dict[int, dict] = {}

WORK_TYPES = {
    "course":  "📚 Курсовая работа",
    "diploma": "🎓 Дипломная работа",
    "thesis":  "🏛 Магистерская диссертация",
    "essay":   "📝 Реферат",
    "report":  "📋 Отчёт по практике",
}

SECTIONS = {
    "course":  ["Введение", "Теоретическая часть", "Практическая часть", "Заключение", "Список литературы"],
    "diploma": ["Введение", "Обзор литературы", "Теоретическая глава", "Практическая глава", "Экономическое обоснование", "Заключение", "Список литературы"],
    "thesis":  ["Введение", "Обзор литературы", "Методология", "Результаты исследования", "Обсуждение", "Заключение", "Библиография"],
    "essay":   ["Введение", "Основная часть", "Заключение", "Список литературы"],
    "report":  ["Введение", "Описание места практики", "Выполненные задания", "Анализ деятельности", "Заключение"],
}

# ─── Helpers ─────────────────────────────────────────────────────────────────

def get_user(uid: int) -> dict:
    if uid not in user_data_store:
        user_data_store[uid] = {
            "work_type": None,
            "topic": None,
            "details": None,
            "chat_session": None,       # Gemini chat session
            "generated_sections": {},
        }
    return user_data_store[uid]


def get_chat_session(user: dict):
    """Return existing Gemini chat session or create a new one."""
    if user["chat_session"] is None:
        user["chat_session"] = gemini.start_chat(history=[])
    return user["chat_session"]


def reset_chat(user: dict):
    user["chat_session"] = None


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Новая работа", callback_data="new_work")],
        [InlineKeyboardButton("💬 Чат с ассистентом", callback_data="open_chat")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")],
    ])


def work_type_keyboard():
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"type_{key}")]
        for key, label in WORK_TYPES.items()
    ]
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")])
    return InlineKeyboardMarkup(buttons)


def sections_keyboard(work_type: str, generated: set):
    sections = SECTIONS.get(work_type, [])
    buttons = []
    for i, sec in enumerate(sections):
        icon = "✅" if sec in generated else "📄"
        buttons.append([InlineKeyboardButton(f"{icon} {sec}", callback_data=f"gen_{i}")])
    buttons.append([InlineKeyboardButton("📥 Скачать всё", callback_data="download_all")])
    buttons.append([InlineKeyboardButton("💬 Чат по работе", callback_data="open_chat")])
    buttons.append([InlineKeyboardButton("🔄 Начать заново", callback_data="back_menu")])
    return InlineKeyboardMarkup(buttons)


def split_text(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    return chunks


async def gemini_ask(prompt: str) -> str:
    """One-shot generation (for section writing)."""
    response = gemini.generate_content(prompt)
    return response.text


async def gemini_chat(user: dict, message: str) -> str:
    """Multi-turn chat with session memory."""
    session = get_chat_session(user)
    response = session.send_message(message)
    return response.text


# ─── Handlers ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    get_user(uid)
    name = update.effective_user.first_name or "студент"
    await update.message.reply_text(
        f"👋 Привет, {name}!\n\n"
        "Я твой академический ассистент — помогу написать курсовую, диплом или реферат.\n\n"
        "Что хочешь сделать?",
        reply_markup=main_menu_keyboard(),
    )
    return STATE_MENU


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data
    user = get_user(uid)

    # ── Main menu ──
    if data in ("back_menu", "new_work"):
        user["work_type"] = None
        user["topic"] = None
        user["details"] = None
        user["generated_sections"] = {}
        reset_chat(user)
        await query.edit_message_text(
            "📚 Выбери тип работы:",
            reply_markup=work_type_keyboard(),
        )
        return STATE_WORK_TYPE

    if data == "help":
        await query.edit_message_text(
            "❓ *Как пользоваться ботом:*\n\n"
            "1\\. Нажми «Новая работа» и выбери тип\n"
            "2\\. Введи тему работы\n"
            "3\\. Укажи дополнительные детали \\(дисциплина, вуз, требования\\)\n"
            "4\\. Генерируй разделы по одному\n"
            "5\\. Скачай готовую работу или используй чат для правок\n\n"
            "💡 Чем подробнее опишешь тему — тем лучше результат\\!",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")
            ]]),
        )
        return STATE_MENU

    # ── Work type ──
    if data.startswith("type_"):
        wtype = data[5:]
        user["work_type"] = wtype
        label = WORK_TYPES[wtype]
        await query.edit_message_text(
            f"Выбрано: *{label}*\n\n📝 Введи тему работы:",
            parse_mode="Markdown",
        )
        return STATE_TOPIC

    # ── Section generation ──
    if data.startswith("gen_"):
        idx = int(data[4:])
        sections = SECTIONS.get(user["work_type"], [])
        if idx >= len(sections):
            await query.answer("Раздел не найден", show_alert=True)
            return STATE_GENERATING

        section_name = sections[idx]
        await query.edit_message_text(f"⏳ Генерирую раздел *{section_name}*...\n\nЭто займёт 10–20 секунд.", parse_mode="Markdown")

        prompt = (
            f"Напиши раздел «{section_name}» для {WORK_TYPES[user['work_type']]}.\n"
            f"Тема работы: {user['topic']}\n"
            f"Дополнительные сведения: {user['details'] or 'не указаны'}\n\n"
            f"Требования: развёрнуто, академическим языком, минимум 500 слов, "
            f"с подзаголовками внутри раздела."
        )

        try:
            text = await gemini_ask(prompt)
        except Exception as e:
            logger.error(f"Gemini error: {e}")
            await query.edit_message_text(
                "⚠️ Ошибка при генерации. Попробуй ещё раз.",
                reply_markup=sections_keyboard(user["work_type"], set(user["generated_sections"].keys())),
            )
            return STATE_GENERATING

        user["generated_sections"][section_name] = text

        header = f"📄 *{section_name}*\n\n"
        chunks = split_text(header + text, 4000)
        for i, chunk in enumerate(chunks):
            if i == 0:
                await query.edit_message_text(chunk, parse_mode="Markdown")
            else:
                await context.bot.send_message(uid, chunk, parse_mode="Markdown")

        await context.bot.send_message(
            uid,
            "Выбери следующий раздел:",
            reply_markup=sections_keyboard(user["work_type"], set(user["generated_sections"].keys())),
        )
        return STATE_GENERATING

    # ── Download all ──
    if data == "download_all":
        generated = user["generated_sections"]
        if not generated:
            await query.answer("Сначала сгенерируй хотя бы один раздел!", show_alert=True)
            return STATE_GENERATING

        sections = SECTIONS.get(user["work_type"], [])
        full_text = (
            f"{'='*60}\n"
            f"{WORK_TYPES[user['work_type']].upper()}\n"
            f"Тема: {user['topic']}\n"
            f"{'='*60}\n"
        )
        for sec in sections:
            if sec in generated:
                full_text += f"\n\n{'─'*40}\n{sec.upper()}\n{'─'*40}\n\n{generated[sec]}"

        filename = f"/tmp/thesis_{uid}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(full_text)

        await context.bot.send_document(
            uid,
            document=open(filename, "rb"),
            filename=f"работа_{user['topic'][:30]}.txt",
            caption="📥 Готово! Открой в текстовом редакторе и оформи по требованиям вуза.",
        )
        return STATE_GENERATING

    # ── Open chat ──
    if data == "open_chat":
        topic_info = f" по теме «{user['topic']}»" if user.get("topic") else ""
        await query.edit_message_text(
            f"💬 *Режим чата*{topic_info}\n\n"
            "Задавай вопросы, проси правки или уточнения.\n"
            "Напиши /menu чтобы вернуться в меню.",
            parse_mode="Markdown",
        )
        return STATE_CHAT

    return STATE_MENU


async def receive_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = get_user(uid)
    user["topic"] = update.message.text.strip()

    await update.message.reply_text(
        f"✅ Тема: *{user['topic']}*\n\n"
        "📋 Укажи дополнительные детали:\n"
        "• Дисциплина / специальность\n"
        "• Вуз и кафедра\n"
        "• Особые требования к работе\n\n"
        "Или напиши *нет* если деталей нет.",
        parse_mode="Markdown",
    )
    return STATE_DETAILS


async def receive_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = get_user(uid)
    details = update.message.text.strip()
    user["details"] = None if details.lower() in ("нет", "no", "-") else details

    # Start chat session with context
    session = get_chat_session(user)
    context_msg = (
        f"Запомни контекст нашей работы: "
        f"{WORK_TYPES[user['work_type']]} на тему «{user['topic']}». "
        f"Детали: {user['details'] or 'не указаны'}. "
        f"Когда я буду просить правки или задавать вопросы — учитывай этот контекст."
    )
    try:
        session.send_message(context_msg)
    except Exception:
        pass  # не критично если не прошло

    sections = SECTIONS.get(user["work_type"], [])
    sections_list = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(sections))

    await update.message.reply_text(
        f"🎯 *Настройка завершена!*\n\n"
        f"*{WORK_TYPES[user['work_type']]}*\n"
        f"Тема: _{user['topic']}_\n\n"
        f"Структура:\n{sections_list}\n\n"
        f"Нажми на раздел чтобы сгенерировать его 👇",
        parse_mode="Markdown",
        reply_markup=sections_keyboard(user["work_type"], set()),
    )
    return STATE_GENERATING


async def chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = get_user(uid)
    text = update.message.text.strip()

    await context.bot.send_chat_action(uid, "typing")

    try:
        reply = await gemini_chat(user, text)
    except Exception as e:
        logger.error(f"Gemini chat error: {e}")
        # Reset session on error and retry
        reset_chat(user)
        try:
            reply = await gemini_chat(user, text)
        except Exception:
            await update.message.reply_text("⚠️ Ошибка. Попробуй ещё раз или напиши /menu.")
            return STATE_CHAT

    for chunk in split_text(reply, 4000):
        await update.message.reply_text(chunk)

    return STATE_CHAT


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Главное меню:", reply_markup=main_menu_keyboard())
    return STATE_MENU


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            STATE_MENU:       [CallbackQueryHandler(button_handler)],
            STATE_WORK_TYPE:  [CallbackQueryHandler(button_handler)],
            STATE_TOPIC:      [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_topic),
                CallbackQueryHandler(button_handler),
            ],
            STATE_DETAILS:    [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_details),
                CallbackQueryHandler(button_handler),
            ],
            STATE_GENERATING: [
                CallbackQueryHandler(button_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message),
            ],
            STATE_CHAT:       [
                MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message),
                CallbackQueryHandler(button_handler),
                CommandHandler("menu", menu_command),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("menu", menu_command),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)
    logger.info("🤖 Бот запущен на Gemini!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
