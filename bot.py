import logging
import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)
from google import genai
from google.genai import types
 
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
 
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
 
SYSTEM_PROMPT = """Ты — профессиональный академический ассистент, специализирующийся на написании студенческих работ на русском языке.
Правила:
- Всегда пиши на академическом русском языке
- Соблюдай структуру научного текста
- Используй профессиональную терминологию
- Объём каждого раздела — не менее 500 слов
- В введении обязательно: актуальность, цель, задачи, объект, предмет, методы
- Пиши развёрнуто, структурированно, с подзаголовками
"""
 
(STATE_MENU, STATE_WORK_TYPE, STATE_TOPIC, STATE_DETAILS, STATE_GENERATING, STATE_CHAT) = range(6)
 
user_data_store: dict = {}
 
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
 
def get_user(uid):
    if uid not in user_data_store:
        user_data_store[uid] = {"work_type": None, "topic": None, "details": None, "history": [], "generated_sections": {}}
    return user_data_store[uid]
 
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Новая работа", callback_data="new_work")],
        [InlineKeyboardButton("💬 Чат с ассистентом", callback_data="open_chat")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")],
    ])
 
def work_type_keyboard():
    buttons = [[InlineKeyboardButton(label, callback_data=f"type_{key}")] for key, label in WORK_TYPES.items()]
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")])
    return InlineKeyboardMarkup(buttons)
 
def sections_keyboard(work_type, generated):
    sections = SECTIONS.get(work_type, [])
    buttons = []
    for i, sec in enumerate(sections):
        icon = "✅" if sec in generated else "📄"
        buttons.append([InlineKeyboardButton(f"{icon} {sec}", callback_data=f"gen_{i}")])
    buttons.append([InlineKeyboardButton("📥 Скачать всё", callback_data="download_all")])
    buttons.append([InlineKeyboardButton("💬 Чат по работе", callback_data="open_chat")])
    buttons.append([InlineKeyboardButton("🔄 Начать заново", callback_data="back_menu")])
    return InlineKeyboardMarkup(buttons)
 
def split_text(text, max_len=4000):
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
 
def gemini_generate(prompt):
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
        contents=prompt,
    )
    return response.text
 
def gemini_chat_reply(history, message, context_info=""):
    system = SYSTEM_PROMPT + context_info
    contents = []
    for msg in history[-8:]:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))
    contents.append(types.Content(role="user", parts=[types.Part(text=message)]))
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        config=types.GenerateContentConfig(system_instruction=system),
        contents=contents,
    )
    return response.text
 
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    get_user(uid)
    name = update.effective_user.first_name or "студент"
    await update.message.reply_text(
        f"👋 Привет, {name}!\n\nЯ твой академический ассистент — помогу написать курсовую, диплом или реферат.\n\nЧто хочешь сделать?",
        reply_markup=main_menu_keyboard(),
    )
    return STATE_MENU
 
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data
    user = get_user(uid)
 
    if data in ("back_menu", "new_work"):
        user.update({"work_type": None, "topic": None, "details": None, "generated_sections": {}, "history": []})
        await query.edit_message_text("📚 Выбери тип работы:", reply_markup=work_type_keyboard())
        return STATE_WORK_TYPE
 
    if data == "help":
        await query.edit_message_text(
            "❓ Как пользоваться:\n\n1. Нажми «Новая работа» и выбери тип\n2. Введи тему\n3. Укажи детали (вуз, дисциплина)\n4. Генерируй разделы по одному\n5. Скачай готовую работу\n\n💡 Чем подробнее опишешь — тем лучше результат!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]]),
        )
        return STATE_MENU
 
    if data.startswith("type_"):
        user["work_type"] = data[5:]
        await query.edit_message_text(f"Выбрано: {WORK_TYPES[user['work_type']]}\n\n📝 Введи тему работы:")
        return STATE_TOPIC
 
    if data.startswith("gen_"):
        idx = int(data[4:])
        sections = SECTIONS.get(user["work_type"], [])
        if idx >= len(sections):
            return STATE_GENERATING
        section_name = sections[idx]
        await query.edit_message_text(f"⏳ Генерирую: {section_name}...\n\nЭто займёт 15–30 секунд.")
        prompt = (
            f"Напиши раздел «{section_name}» для {WORK_TYPES[user['work_type']]}.\n"
            f"Тема: {user['topic']}\nДетали: {user['details'] or 'не указаны'}\n\n"
            f"Минимум 500 слов, с подзаголовками, академическим языком."
        )
        try:
            text = gemini_generate(prompt)
        except Exception as e:
            logger.error(f"Gemini error: {e}")
            await query.edit_message_text("⚠️ Ошибка генерации. Попробуй ещё раз.",
                reply_markup=sections_keyboard(user["work_type"], set(user["generated_sections"].keys())))
            return STATE_GENERATING
        user["generated_sections"][section_name] = text
        chunks = split_text(f"📄 {section_name}\n\n{text}", 4000)
        for i, chunk in enumerate(chunks):
            if i == 0:
                await query.edit_message_text(chunk)
            else:
                await context.bot.send_message(uid, chunk)
        await context.bot.send_message(uid, "Выбери следующий раздел:",
            reply_markup=sections_keyboard(user["work_type"], set(user["generated_sections"].keys())))
        return STATE_GENERATING
 
    if data == "download_all":
        generated = user["generated_sections"]
        if not generated:
            await query.answer("Сначала сгенерируй хотя бы один раздел!", show_alert=True)
            return STATE_GENERATING
        sections = SECTIONS.get(user["work_type"], [])
        full_text = f"{'='*60}\n{WORK_TYPES[user['work_type']].upper()}\nТема: {user['topic']}\n{'='*60}\n"
        for sec in sections:
            if sec in generated:
                full_text += f"\n\n{'─'*40}\n{sec.upper()}\n{'─'*40}\n\n{generated[sec]}"
        filename = f"/tmp/thesis_{uid}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(full_text)
        await context.bot.send_document(uid, document=open(filename, "rb"),
            filename=f"работа_{user['topic'][:30]}.txt",
            caption="📥 Готово! Оформи по требованиям вуза.")
        return STATE_GENERATING
 
    if data == "open_chat":
        topic_info = f" по теме «{user['topic']}»" if user.get("topic") else ""
        await query.edit_message_text(
            f"💬 Режим чата{topic_info}\n\nЗадавай вопросы, проси правки.\nНапиши /menu чтобы вернуться в меню.")
        return STATE_CHAT
 
    return STATE_MENU
 
async def receive_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = get_user(uid)
    user["topic"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Тема: {user['topic']}\n\n📋 Укажи детали:\n• Дисциплина / специальность\n• Вуз и кафедра\n• Особые требования\n\nИли напиши нет.")
    return STATE_DETAILS
 
async def receive_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = get_user(uid)
    details = update.message.text.strip()
    user["details"] = None if details.lower() in ("нет", "no", "-") else details
    sections = SECTIONS.get(user["work_type"], [])
    sections_list = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(sections))
    await update.message.reply_text(
        f"🎯 Настройка завершена!\n\n{WORK_TYPES[user['work_type']]}\nТема: {user['topic']}\n\nСтруктура:\n{sections_list}\n\nНажми на раздел 👇",
        reply_markup=sections_keyboard(user["work_type"], set()),
    )
    return STATE_GENERATING
 
async def chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = get_user(uid)
    text = update.message.text.strip()
    await context.bot.send_chat_action(uid, "typing")
    context_info = ""
    if user.get("topic"):
        context_info = f"\n\nКонтекст: {WORK_TYPES.get(user.get('work_type',''), '')} на тему «{user['topic']}»."
    try:
        reply = gemini_chat_reply(user["history"], text, context_info)
    except Exception as e:
        logger.error(f"Chat error: {e}")
        await update.message.reply_text("⚠️ Ошибка. Попробуй ещё раз.")
        return STATE_CHAT
    user["history"].append({"role": "user", "content": text})
    user["history"].append({"role": "assistant", "content": reply})
    for chunk in split_text(reply, 4000):
        await update.message.reply_text(chunk)
    return STATE_CHAT
 
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Главное меню:", reply_markup=main_menu_keyboard())
    return STATE_MENU
 
async def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            STATE_MENU:       [CallbackQueryHandler(button_handler)],
            STATE_WORK_TYPE:  [CallbackQueryHandler(button_handler)],
            STATE_TOPIC:      [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_topic), CallbackQueryHandler(button_handler)],
            STATE_DETAILS:    [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_details), CallbackQueryHandler(button_handler)],
            STATE_GENERATING: [CallbackQueryHandler(button_handler), MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message)],
            STATE_CHAT:       [MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message), CallbackQueryHandler(button_handler), CommandHandler("menu", menu_command)],
        },
        fallbacks=[CommandHandler("start", start), CommandHandler("menu", menu_command)],
        allow_reentry=True,
    )
    app.add_handler(conv)
    logger.info("🤖 Бот запущен!")
    await app.run_polling(drop_pending_updates=True)
 
if __name__ == "__main__":
    asyncio.run(main())