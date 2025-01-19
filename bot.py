import os
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ChatMemberUpdated,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from datetime import datetime, date, time, timedelta
import pytz
import aiosqlite

# ---------- НАСТРОЙКИ ----------

BOT_TOKEN = "7926233927:AAFjSBeFDgrjENeTb-d8pxvUfb0hlv9YF94" 

# Храним базу в /data (если у тебя на Render примонтирован Volume)
DB_PATH = os.getenv("DB_PATH", "/data/participants.db")

# Московское время (если нужно)
timezone_moscow = pytz.timezone("Europe/Moscow")

# Создаём объекты бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ---------- ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ----------

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id        INTEGER PRIMARY KEY,
                start_date     TEXT,
                end_date       TEXT,
                conditions     TEXT,
                active         INTEGER DEFAULT 0,
                poll_time      TEXT DEFAULT '23:30',
                last_poll_date TEXT DEFAULT ''
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS participants (
                row_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    INTEGER,
                user_id    INTEGER,
                name       TEXT,
                drinks     INTEGER DEFAULT 0,
                check_ins  INTEGER DEFAULT 0,
                UNIQUE(chat_id, user_id)
            )
        """)
        await db.commit()

# ---------- ФУНКЦИИ ДЛЯ НАСТРОЕК ЧАТА ----------

async def add_or_update_chat(
    chat_id: int,
    start_date_str: str,
    end_date_str: str,
    poll_time_str: str = "23:30",
    conditions: str = ""
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO chat_settings (chat_id, start_date, end_date, conditions, active, poll_time)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                start_date  = excluded.start_date,
                end_date    = excluded.end_date,
                conditions  = excluded.conditions,
                active      = 1,
                poll_time   = excluded.poll_time
            """,
            (chat_id, start_date_str, end_date_str, conditions, poll_time_str)
        )
        await db.commit()

async def load_chat_settings(chat_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT start_date, end_date, conditions, active, poll_time, last_poll_date
            FROM chat_settings
            WHERE chat_id = ?
        """, (chat_id,))
        return await cursor.fetchone()

async def set_chat_active(chat_id: int, active: bool):
    val = 1 if active else 0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE chat_settings SET active=? WHERE chat_id=?", (val, chat_id))
        await db.commit()

async def set_chat_poll_time(chat_id: int, poll_time_str: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE chat_settings SET poll_time=? WHERE chat_id=?", (poll_time_str, chat_id))
        await db.commit()

async def set_chat_last_poll_date(chat_id: int, date_str: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE chat_settings SET last_poll_date=? WHERE chat_id=?", (date_str, chat_id))
        await db.commit()

async def get_all_active_chats():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT chat_id, poll_time, last_poll_date
            FROM chat_settings
            WHERE active=1
        """)
        return await cursor.fetchall()

# ---------- ФУНКЦИИ ДЛЯ УЧАСТНИКОВ ----------

async def add_participant(chat_id: int, user_id: int, name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO participants (chat_id, user_id, name)
            VALUES (?, ?, ?)
        """, (chat_id, user_id, name))
        await db.commit()

async def update_stat(chat_id: int, user_id: int, column: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE participants SET {column} = {column} + 1 WHERE chat_id=? AND user_id=?",
            (chat_id, user_id)
        )
        await db.commit()

async def get_stats_for_chat(chat_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT user_id, name, drinks, check_ins
            FROM participants
            WHERE chat_id=?
        """, (chat_id,))
        return await cursor.fetchall()

# ---------- ФОНОВАЯ РАССЫЛКА ОПРОСА ----------

async def schedule_polls_loop():
    while True:
        now = datetime.now(timezone_moscow)
        today_str = now.strftime("%Y-%m-%d")
        active_chats = await get_all_active_chats()
        for (chat_id, poll_time_str, last_poll_date) in active_chats:
            try:
                h, m = poll_time_str.split(":")
                poll_time_obj = time(int(h), int(m))
            except:
                poll_time_obj = time(23, 30)  # дефолт
            poll_today = datetime.combine(now.date(), poll_time_obj)
            poll_today = timezone_moscow.localize(poll_today)

            if last_poll_date != today_str and now >= poll_today:
                await send_poll(chat_id)
                await set_chat_last_poll_date(chat_id, today_str)

        await asyncio.sleep(60)

async def send_poll(chat_id: int):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Не пил 🍵", callback_data="not_drink"),
            InlineKeyboardButton(text="Пил 🍺",    callback_data="drink")
        ]]
    )
    try:
        await bot.send_message(chat_id, "🔥 Каждый день одно и то же — ну что, бухал?", reply_markup=keyboard)
    except Exception as e:
        print(f"[ERROR] {chat_id}: {e}")

# ---------- ХЕНДЛЕРЫ ----------

@dp.my_chat_member()
async def bot_added_to_group(update: ChatMemberUpdated):
    if update.new_chat_member.user.id == (await bot.me()).id:
        old_status = update.old_chat_member.status
        new_status = update.new_chat_member.status
        if old_status in ("kicked", "left") and new_status in ("member", "administrator"):
            chat_id = update.chat.id

            row = await load_chat_settings(chat_id)
            if not row:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("""
                        INSERT OR IGNORE INTO chat_settings (chat_id, active)
                        VALUES (?, 0)
                    """, (chat_id,))
                    await db.commit()

            await bot.send_message(
                chat_id,
                "Йоу, я тут, чтобы вести вашу трезвую жизнь (или указывать на вашу слабину).\n"
                "Хочешь начать челлендж? Введи /start_challenge YYYY-MM-DD YYYY-MM-DD HH:MM.\n"
                "Например так: /start_challenge 2025-01-01 2025-12-31 21:00"
            )

# ---------- КНОПКИ МЕНЮ С ТЕКСТАМИ (БЕЗ СЛЭШЕЙ) ----------

menu_buttons = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Запустить челлендж"),
            KeyboardButton(text="Редактировать челлендж")
        ],
        [
            KeyboardButton(text="Я в деле"),
            KeyboardButton(text="Статистика")
        ],
        [
            KeyboardButton(text="Срыв..."),
            KeyboardButton(text="Трезвый день")
        ],
        [
            KeyboardButton(text="Условия"),
            KeyboardButton(text="Поставить время")
        ],
        [
            KeyboardButton(text="Чё тут делать?")
        ]
    ],
    resize_keyboard=True
)

@dp.message(Command(commands=["start"]))
async def cmd_start(message: Message):
    await message.answer(
        "Ну приветики. Я тут веду ваши пьянки-пьянки.\n"
        "Если хотите поиздеваться над собой и запустить трезвый челлендж, набирайте /start_challenge <дата1> <дата2> <время>.\n"
        "А если лень что-то печатать, вот кнопочки (но даты/время всё равно придётся сообщать вручную).",
        reply_markup=menu_buttons
    )

@dp.message(Command(commands=["help"]))
async def cmd_help(message: Message):
    txt = (
        "Доступные команды:\n"
        "/start_challenge YYYY-MM-DD YYYY-MM-DD HH:MM — Начать трезвый марафон.\n"
        "/edit_challenge YYYY-MM-DD YYYY-MM-DD HH:MM — Поменять даты/время.\n"
        "/set_time HH:MM — Только время поменять.\n"
        "/join — Добавить себя в список несчастных.\n"
        "/report — Сказать «Да, я сегодня бухал, стыжусь».\n"
        "/mark_sober — Отметить трезвость.\n"
        "/stats — Посмотреть, кто тут сколько раз оступился.\n"
        "/conditions — Условия, которые вы сами придумали.\n"
        "/help — Ну, ты это уже видишь.\n\n"
        "Ладно, хватит читать — жми кнопки."
    )
    await message.answer(txt, reply_markup=menu_buttons)

# -- Связка кнопок (без слэшей) с соответствующими командами --

@dp.message(F.text == "Запустить челлендж")
async def btn_zapusk(message: Message):
    await message.answer("Чтобы меня не бесить, укажи даты и время: /start_challenge YYYY-MM-DD YYYY-MM-DD HH:MM")

@dp.message(F.text == "Редактировать челлендж")
async def btn_edit(message: Message):
    await message.answer("Серьёзно? Ладно, пиши: /edit_challenge YYYY-MM-DD YYYY-MM-DD HH:MM")

@dp.message(F.text == "Я в деле")
async def btn_join(message: Message):
    # просто вызываем команду /join
    await cmd_join(message)

@dp.message(F.text == "Статистика")
async def btn_stats(message: Message):
    await cmd_stats(message)

@dp.message(F.text == "Срыв...")
async def btn_sryv(message: Message):
    await cmd_report(message)

@dp.message(F.text == "Трезвый день")
async def btn_sober(message: Message):
    await cmd_mark_sober(message)

@dp.message(F.text == "Условия")
async def btn_conditions(message: Message):
    await cmd_conditions(message)

@dp.message(F.text == "Поставить время")
async def btn_set_time(message: Message):
    await message.answer("Ок, ну давай конкретику: /set_time HH:MM")

@dp.message(F.text == "Чё тут делать?")
async def btn_help(message: Message):
    await cmd_help(message)

# ---------- ОСНОВНЫЕ КОМАНДЫ ЧЕЛЛЕНДЖА ----------

@dp.message(Command(commands=["start_challenge"]))
async def cmd_start_challenge(message: Message):
    parts = message.text.strip().split()
    if len(parts) < 3:
        await message.answer("Слушай, напиши нормально: /start_challenge 2025-01-01 2025-12-31 [HH:MM]")
        return
    start_date_str = parts[1]
    end_date_str   = parts[2]
    poll_time_str  = "23:30"
    if len(parts) >= 4:
        poll_time_str = parts[3]

    try:
        datetime.strptime(start_date_str, "%Y-%m-%d")
        datetime.strptime(end_date_str, "%Y-%m-%d")
        datetime.strptime(poll_time_str, "%H:%M")
    except:
        await message.answer("Ты ошибся в формате. Вводи даты так: 2025-01-01 2025-12-31 21:00")
        return

    await add_or_update_chat(message.chat.id, start_date_str, end_date_str, poll_time_str)
    await set_chat_active(message.chat.id, True)
    await message.answer(
        f"Ну окей, вы с {start_date_str} по {end_date_str} будете строить из себя трезвенников.\n"
        f"Каждый день в {poll_time_str} я буду спрашивать, кто сорвался.\n"
        "Удачи, хоть кому-то."
    )

@dp.message(Command(commands=["edit_challenge"]))
async def cmd_edit_challenge(message: Message):
    parts = message.text.strip().split()
    if len(parts) < 3:
        await message.answer("Хочешь отредактировать? Нужны 2 даты и время: 2025-02-01 2025-12-31 [HH:MM]")
        return

    start_date_str = parts[1]
    end_date_str   = parts[2]
    poll_time_str  = "23:30"
    if len(parts) >= 4:
        poll_time_str = parts[3]

    try:
        datetime.strptime(start_date_str, "%Y-%m-%d")
        datetime.strptime(end_date_str, "%Y-%m-%d")
        datetime.strptime(poll_time_str, "%H:%M")
    except:
        await message.answer("Ты ввёл ерунду. Делай как просят: YYYY-MM-DD YYYY-MM-DD HH:MM.")
        return

    await add_or_update_chat(message.chat.id, start_date_str, end_date_str, poll_time_str)
    await set_chat_active(message.chat.id, True)
    await message.answer(
        f"Окей, новый период: {start_date_str} - {end_date_str}, время опроса: {poll_time_str}\n"
        "Продолжаем этот цирк."
    )

@dp.message(Command(commands=["set_time"]))
async def cmd_set_time(message: Message):
    parts = message.text.strip().split()
    if len(parts) < 2:
        await message.answer("Укажи время в формате HH:MM")
        return

    poll_time_str = parts[1]
    try:
        datetime.strptime(poll_time_str, "%H:%M")
    except:
        await message.answer("Это не похоже на время (HH:MM).")
        return

    await set_chat_poll_time(message.chat.id, poll_time_str)
    await set_chat_active(message.chat.id, True)
    await message.answer(f"Ладно, буду стучаться в {poll_time_str}, лишь бы ты не забыл, что у тебя челлендж.")

# ---------- УЧАСТИЕ / СТАТИСТИКА / УСЛОВИЯ ----------

@dp.message(Command(commands=["join"]))
async def cmd_join(message: Message):
    user = message.from_user
    await add_participant(message.chat.id, user.id, user.full_name)
    await message.answer(f"Ну ладно, {user.full_name}, входи в клуб. Надеюсь, ты не сдуешься?")

@dp.message(Command(commands=["stats"]))
async def cmd_stats(message: Message):
    row = await load_chat_settings(message.chat.id)
    if not row:
        await message.answer("Челлендж даже не запущен, а ты уже статистику хочешь?")
        return

    start_date_str, end_date_str, conditions, active, poll_time, last_poll_date = row

    try:
        sdt = datetime.strptime(start_date_str, "%Y-%m-%d")
    except:
        sdt = datetime.now()

    try:
        edt = datetime.strptime(end_date_str, "%Y-%m-%d")
    except:
        edt = datetime.now()

    now = datetime.now()
    days_passed = (now - sdt).days
    if days_passed < 0:
        days_passed = 0
    days_left = (edt - now).days
    if days_left < 0:
        days_left = 0

    participants = await get_stats_for_chat(message.chat.id)
    if not participants:
        await message.answer("Тут вообще нет участников. /join введите, если кто-то осмелится.")
        return

    text = (
        f"Ну что, вы с {start_date_str} по {end_date_str}, "
        f"прошло {days_passed} дней, осталось {days_left}.\n"
        f"Опрос в {poll_time}.\n\n"
        "Текущие герои (или позорники):\n"
    )
    for user_id, name, drinks, check_ins in participants:
        text += f"• {name}: {drinks} косяков, {check_ins} трезвых дней\n"

    await message.answer(text)

@dp.message(Command(commands=["report"]))
async def cmd_report(message: Message):
    uid = message.from_user.id
    await update_stat(message.chat.id, uid, "drinks")
    await message.answer("Ну что ж, записал твой срыв. Жаль, конечно...")

@dp.message(Command(commands=["mark_sober"]))
async def cmd_mark_sober(message: Message):
    uid = message.from_user.id
    await update_stat(message.chat.id, uid, "check_ins")
    await message.answer("Трезвый день? Шок-контент! Записано.")

@dp.message(Command(commands=["conditions"]))
async def cmd_conditions(message: Message):
    parts = message.text.strip().split(maxsplit=1)
    row = await load_chat_settings(message.chat.id)
    if not row:
        await message.answer("Ты бы хоть запустил челлендж для начала.")
        return

    start_date_str, end_date_str, old_conditions, active, poll_time, last_poll_date = row
    if len(parts) > 1:
        new_text = parts[1]
        await add_or_update_chat(message.chat.id, start_date_str, end_date_str, poll_time, new_text)
        await message.answer(f"Условия обновил(а). Посмотрим, как вы этого придерживаться будете:\n{new_text}")
    else:
        if old_conditions:
            await message.answer(f"Ваши нынешние «условия»:\n{old_conditions}")
        else:
            await message.answer("Пока нет никаких условий. Можете задать их так: /conditions текст...")

# ---------- ОБРАБОТКА КНОПОК ОПРОСА ----------

@dp.callback_query(F.data.in_({"not_drink", "drink"}))
async def handle_poll_response(call: CallbackQuery):
    uid = call.from_user.id
    cid = call.message.chat.id
    if call.data == "not_drink":
        await update_stat(cid, uid, "check_ins")
        await call.answer("Ура, хоть кто-то трезвый!")
    else:
        await update_stat(cid, uid, "drinks")
        await call.answer("Пичалька. Записал твой позор.")

# ---------- СТАРТ БОТА ----------

async def main():
    await init_db()
    # Стартуем фон
    asyncio.create_task(schedule_polls_loop())
    print("Бот запущен. Готов раздражать своими вопросами.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
