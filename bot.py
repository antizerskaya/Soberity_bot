import os
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    CallbackQuery,
    ChatMemberUpdated
)
import aiosqlite
from datetime import datetime, timedelta
import pytz

# === НАСТРОЙКИ БОТА ===

BOT_TOKEN = "7926233927:AAFjSBeFDgrjENeTb-d8pxvUfb0hlv9YF94"  # Подставь реальный токен

# Путь к базе. Можно задать в переменных окружения (Render → Settings → Environment):
# Если не задано, по умолчанию /data/participants.db
DB_PATH = os.getenv("DB_PATH", "/data/participants.db")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Если нужен учёт времени по Москве
timezone_moscow = pytz.timezone("Europe/Moscow")

# Время отправки опроса (HH:MM, московское)
daily_poll_time = "16:55"

# === ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ===

async def init_db():
    """
    Создаёт нужные таблицы (если их ещё нет):
      1) chat_settings — для хранения дат челленджа и статуса чата
      2) participants  — для учёта участников по каждому чату
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица с настройками каждого чата
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id    INTEGER PRIMARY KEY,
                start_date TEXT,
                end_date   TEXT,
                conditions TEXT,
                active     INTEGER DEFAULT 0
            )
        """)
        # Таблица с участниками (у каждого чата своя статистика)
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

async def add_or_update_chat(chat_id: int, start_date_str: str, end_date_str: str, conditions: str = None):
    """
    Добавляем/обновляем запись о чате: (start_date, end_date, conditions).
    При этом ставим active=1 (то есть челлендж включён).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO chat_settings (chat_id, start_date, end_date, conditions, active)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(chat_id) DO UPDATE SET
                start_date=excluded.start_date,
                end_date=excluded.end_date,
                conditions=excluded.conditions,
                active=1
        """, (chat_id, start_date_str, end_date_str, conditions if conditions else ""))
        await db.commit()

async def load_chat_settings(chat_id: int):
    """
    Возвращаем (start_date, end_date, conditions, active) для чата, или None, если чата нет в базе.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT start_date, end_date, conditions, active FROM chat_settings WHERE chat_id = ?", (chat_id,))
        row = await cursor.fetchone()
        return row  # (start_date, end_date, conditions, active) или None

async def set_chat_active(chat_id: int, active: bool):
    """
    Устанавливаем active=1 или 0 для заданного чата.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        val = 1 if active else 0
        await db.execute("UPDATE chat_settings SET active = ? WHERE chat_id = ?", (val, chat_id))
        await db.commit()

async def get_all_active_chats():
    """
    Получаем список chat_id, где active=1 (туда шлём ежедневные опросы).
    """
    results = []
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT chat_id FROM chat_settings WHERE active = 1") as cursor:
            async for row in cursor:
                results.append(row[0])
    return results

# === Участники (таблица participants) ===

async def add_participant(chat_id: int, user_id: int, name: str):
    """
    Добавляем участника (chat_id + user_id). Если уже есть, игнорируем.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO participants (chat_id, user_id, name)
            VALUES (?, ?, ?)
        """, (chat_id, user_id, name))
        await db.commit()

async def update_stat(chat_id: int, user_id: int, column: str):
    """
    Увеличиваем drinks или check_ins на 1 для (chat_id, user_id).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE participants SET {column} = {column} + 1 WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id)
        )
        await db.commit()

async def get_stats_for_chat(chat_id: int):
    """
    Список участников заданного чата (chat_id).
    Возвращаем list[(user_id, name, drinks, check_ins), ...].
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, name, drinks, check_ins FROM participants WHERE chat_id = ?",
            (chat_id,)
        ) as cursor:
            return await cursor.fetchall()

# === ФУНКЦИЯ ЕЖЕДНЕВНОЙ РАССЫЛКИ ОПРОСА ===

async def send_daily_poll():
    """
    Бесконечный цикл: ждём времени daily_poll_time, потом рассылаем опрос во все чаты, где active=1.
    """
    while True:
        now = datetime.now(timezone_moscow)
        target_time = datetime.strptime(daily_poll_time, "%H:%M").time()
        target_datetime = timezone_moscow.localize(datetime.combine(now.date(), target_time))

        # Если текущее время уже позже целевого — переносим на следующий день
        if now.time() > target_time:
            target_datetime += timedelta(days=1)

        delay = (target_datetime - now).total_seconds()
        print(f"[LOG] Следующий опрос будет отправлен через {delay:.0f} секунд.")
        await asyncio.sleep(delay)

        # Достаём все активные чаты (active=1)
        active_chats = await get_all_active_chats()

        for chat_id in active_chats:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="Не пил 🍵", callback_data="not_drink"),
                    InlineKeyboardButton(text="Пил 🍺",    callback_data="drink")
                ]
            ])
            try:
                await bot.send_message(chat_id, "🔥 Ежедневный опрос: пил ли ты сегодня?", reply_markup=keyboard)
            except Exception as e:
                print(f"[ERROR] Не удалось отправить опрос в чат {chat_id}: {e}")

# === ХЕНДЛЕРЫ ===

@dp.my_chat_member()
async def bot_added_to_group(update: ChatMemberUpdated):
    """
    Когда бота добавляют в группу (или раскикивают).
    Создаём запись в chat_settings (если нет), но пока active=0.
    """
    if update.new_chat_member.user.id == (await bot.me()).id:
        old_status = update.old_chat_member.status
        new_status = update.new_chat_member.status
        if old_status in ("kicked", "left") and new_status in ("member", "administrator"):
            chat_id = update.chat.id
            row = await load_chat_settings(chat_id)
            if not row:
                # Создаём запись для этого чата, но не включаем (active=0).
                await add_or_update_chat(chat_id, "", "", "")
                await set_chat_active(chat_id, False)

            await bot.send_message(
                chat_id,
                "Привет! Я бот для трезвого челленджа.\n"
                "Запусти соревнование в этом чате командой: /start_challenge YYYY-MM-DD YYYY-MM-DD\n"
                "Или посмотри /help, чтобы узнать больше."
            )

@dp.message(Command(commands=["start"]))
async def cmd_start(message: Message):
    """
    Приветственное сообщение по /start (в ЛС или в группе).
    """
    await message.answer(
        "Привет! Я бот для трезвого челленджа. 🎉\n\n"
        "Команды:\n"
        "• /start_challenge YYYY-MM-DD YYYY-MM-DD — Запустить челлендж в этом чате\n"
        "• /join — Присоединиться к челленджу\n"
        "• /stats — Посмотреть статистику\n"
        "• /report — Зафиксировать приём алкоголя\n"
        "• /mark_sober — Отметить трезвый день\n"
        "• /conditions — Посмотреть или задать условия\n"
        "• /help — Показать список команд\n"
    )

@dp.message(Command(commands=["help"]))
async def cmd_help(message: Message):
    text = (
        "Команды бота:\n"
        "/start_challenge YYYY-MM-DD YYYY-MM-DD — Запуск/обновление челленджа (даты)\n"
        "/join — Присоединиться к челленджу в этом чате\n"
        "/stats — Статистика участников по этому чату\n"
        "/report — Сообщить о срыве (drinks + 1)\n"
        "/mark_sober — Отметить трезвый день (check_ins + 1)\n"
        "/conditions — Показать/изменить текст условий (например: /conditions Новые правила...)\n"
        "/help — Это сообщение\n"
    )
    await message.answer(text)

@dp.message(Command(commands=["start_challenge"]))
async def cmd_start_challenge(message: Message):
    """
    Команда для запуска (или обновления) челленджа в этом чате.
    Использование: /start_challenge YYYY-MM-DD YYYY-MM-DD
    Пример: /start_challenge 2025-01-15 2025-12-09
    """
    parts = message.text.strip().split()
    if len(parts) < 3:
        await message.answer("Формат: /start_challenge 2025-01-15 2025-12-09")
        return

    start_date_str = parts[1]
    end_date_str   = parts[2]
    # Можно проверить, что это валидные даты:
    try:
        datetime.strptime(start_date_str, "%Y-%m-%d")
        datetime.strptime(end_date_str,   "%Y-%m-%d")
    except ValueError:
        await message.answer("Неверный формат дат. Используй YYYY-MM-DD.")
        return

    chat_id = message.chat.id
    # Записываем в chat_settings
    await add_or_update_chat(chat_id, start_date_str, end_date_str)
    # Ставим active=1
    await set_chat_active(chat_id, True)

    await message.answer(
        f"🚀 Челлендж запущен!\n"
        f"Период: {start_date_str} - {end_date_str}.\n"
        f"Я буду присылать опрос каждый день в {daily_poll_time} (по Москве)."
    )

@dp.message(Command(commands=["join"]))
async def cmd_join(message: Message):
    """
    Пользователь регистрируется как участник в этом чате.
    """
    chat_id = message.chat.id
    user = message.from_user
    await add_participant(chat_id, user.id, user.full_name)
    await message.answer(f"🍵 {user.full_name}, добро пожаловать в трезвый челлендж этого чата!")

@dp.message(Command(commands=["stats"]))
async def cmd_stats(message: Message):
    """
    Показать статистику конкретно по текущему чату:
      - Даты челленджа (start_date, end_date)
      - Сколько дней прошло и осталось
      - Список участников
    """
    chat_id = message.chat.id
    chat_settings = await load_chat_settings(chat_id)
    if not chat_settings:
        await message.answer("В этом чате ещё не настроен челлендж (используй /start_challenge).")
        return

    start_date_str, end_date_str, conditions, active = chat_settings
    # Преобразуем строки в даты (если они не пустые)
    try:
        start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
    except:
        start_dt = datetime.now()

    try:
        end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
    except:
        end_dt = datetime.now()

    now = datetime.now()
    days_passed = (now - start_dt).days
    if days_passed < 0:
        days_passed = 0
    days_left = (end_dt - now).days
    if days_left < 0:
        days_left = 0

    # Выгружаем участников этого чата
    stats = await get_stats_for_chat(chat_id)
    if not stats:
        await message.answer("Пока никто не присоединился (/join).")
        return

    msg = (
        f"Статистика чата {chat_id}:\n"
        f"Период: {start_date_str} - {end_date_str}\n"
        f"⏳ Прошло: {days_passed} дней, осталось: {days_left} дней.\n\n"
    )
    for (user_id, name, drinks, check_ins) in stats:
        msg += f"• {name} — сорвался: {drinks}, трезвых дней: {check_ins}\n"

    await message.answer(msg)

@dp.message(Command(commands=["report"]))
async def cmd_report(message: Message):
    """
    Отмечаем срыв (drinks += 1) для пользователя в этом чате.
    """
    chat_id = message.chat.id
    user_id = message.from_user.id
    await update_stat(chat_id, user_id, "drinks")
    await message.answer("📉 Отметил срыв. Не сдавайся, завтра — новый день!")

@dp.message(Command(commands=["mark_sober"]))
async def cmd_mark_sober(message: Message):
    """
    Отмечаем трезвый день (check_ins += 1) для пользователя в этом чате.
    """
    chat_id = message.chat.id
    user_id = message.from_user.id
    await update_stat(chat_id, user_id, "check_ins")
    await message.answer("🎉 Трезвый день записан! Так держать!")

@dp.message(Command(commands=["conditions"]))
async def cmd_conditions(message: Message):
    """
    Посмотреть или задать условия челленджа в этом чате.
    Пример: /conditions => покажет текущие
             /conditions Новые условия... => запишет новые
    """
    chat_id = message.chat.id
    text = message.text.strip()
    parts = text.split(maxsplit=1)

    chat_settings = await load_chat_settings(chat_id)
    if not chat_settings:
        await message.answer("В этом чате ещё не настроен челлендж. Сначала /start_challenge.")
        return

    start_date_str, end_date_str, old_conditions, active = chat_settings

    if len(parts) > 1:
        new_conds = parts[1].strip()
        await add_or_update_chat(chat_id, start_date_str, end_date_str, new_conds)
        await message.answer(f"Условия обновлены:\n{new_conds}")
    else:
        if old_conditions:
            await message.answer(f"Условия челленджа:\n{old_conditions}")
        else:
            await message.answer(
                "Пока нет условий. Задай так:\n"
                '/conditions Условие 1, Условие 2, Штраф за срыв = 1 лимон...'
            )

@dp.callback_query(F.data.in_({"not_drink", "drink"}))
async def handle_poll_response(callback: CallbackQuery):
    """
    Обработка кнопок "Не пил 🍵" / "Пил 🍺".
    Отлично подходит для ежедневного опроса, где у каждого чата своя статистика.
    """
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id  # чат, из которого кнопка нажата

    if callback.data == "not_drink":
        await update_stat(chat_id, user_id, "check_ins")
        await callback.answer("🎉 Молодец, держись дальше!")
    else:  # "drink"
        await update_stat(chat_id, user_id, "drinks")
        await callback.answer("📉 Записал. Завтра — новый день!")

# === ЗАПУСК БОТА ===

async def main():
    await init_db()  # создаём таблицы (если нет)
    print("Бот запущен и готов к работе!")

    # Стартуем фоновую задачу, которая шлёт ежедневные опросы
    asyncio.create_task(send_daily_poll())

    # Запускаем поллинг (читаем обновления от Telegram)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
