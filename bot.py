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

# =================== НАСТРОЙКИ ===================

BOT_TOKEN = "7926233927:AAFjSBeFDgrjENeTb-d8pxvUfb0hlv9YF94
"

# Путь к базе. Если на Render сделан Volume с mount path /data,
# то удобно хранить базу в "/data/participants.db".
# Можно переопределять через переменную окружения DB_PATH в Settings Render.
DB_PATH = os.getenv("DB_PATH", "/data/participants.db")

# Часовой пояс 
timezone_moscow = pytz.timezone("Europe/Moscow")

# =================== ИНИЦИАЛИЗАЦИЯ БД ===================

async def init_db():
    """
    Создаём нужные таблицы, если их нет.
    'chat_settings' — хранит настройки для каждого чата.
    'participants'  — хранит участников (счётчики по каждому чату).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Создаём таблицу настроек чата
        # Добавляем поля poll_time (DEFAULT '23:30') и last_poll_date (DEFAULT '')
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

        # Таблица участников: (chat_id, user_id) => drinks, check_ins
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

# --- Работа с chat_settings ---

async def add_or_update_chat(chat_id: int,
                             start_date_str: str,
                             end_date_str: str,
                             poll_time_str: str = "23:30",
                             conditions: str = ""):
    """
    Записываем/обновляем запись о чате, выставляя сразу active=1.
    Если запись уже была — обновим данные.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO chat_settings (chat_id, start_date, end_date, conditions, active, poll_time)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                start_date  = excluded.start_date,
                end_date    = excluded.end_date,
                conditions  = excluded.conditions,
                active      = 1,
                poll_time   = excluded.poll_time
        """, (chat_id, start_date_str, end_date_str, conditions, poll_time_str))
        await db.commit()

async def load_chat_settings(chat_id: int):
    """
    Достаём запись (start_date, end_date, conditions, active, poll_time, last_poll_date).
    Вернёт кортеж или None.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT start_date, end_date, conditions, active, poll_time, last_poll_date
            FROM chat_settings
            WHERE chat_id = ?
        """, (chat_id,))
        row = await cursor.fetchone()
        return row

async def set_chat_active(chat_id: int, active: bool):
    """
    Меняем active=1 или 0 для чата.
    """
    val = 1 if active else 0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE chat_settings SET active=? WHERE chat_id=?", (val, chat_id))
        await db.commit()

async def set_chat_poll_time(chat_id: int, poll_time_str: str):
    """
    Обновляем время опроса для чата.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE chat_settings SET poll_time=? WHERE chat_id=?", (poll_time_str, chat_id))
        await db.commit()

async def set_chat_last_poll_date(chat_id: int, date_str: str):
    """
    Записываем дату, когда последний раз слали опрос (чтобы не слать дважды в один день).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE chat_settings SET last_poll_date=? WHERE chat_id=?", (date_str, chat_id))
        await db.commit()

async def get_all_active_chats():
    """
    Список всех chat_id, где active=1.
    Вернём [(chat_id, poll_time, last_poll_date), ...].
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT chat_id, poll_time, last_poll_date
            FROM chat_settings
            WHERE active=1
        """)
        rows = await cursor.fetchall()
        return rows  # список кортежей (chat_id, poll_time, last_poll_date)

# --- Работа с участниками ---

async def add_participant(chat_id: int, user_id: int, name: str):
    """
    Записываем участника, если ещё нет.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO participants (chat_id, user_id, name)
            VALUES (?, ?, ?)
        """, (chat_id, user_id, name))
        await db.commit()

async def update_stat(chat_id: int, user_id: int, column: str):
    """
    Увеличиваем drinks или check_ins на 1 у (chat_id, user_id).
    column может быть 'drinks' или 'check_ins'.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE participants SET {column} = {column} + 1 WHERE chat_id=? AND user_id=?",
            (chat_id, user_id)
        )
        await db.commit()

async def get_stats_for_chat(chat_id: int):
    """
    Список (user_id, name, drinks, check_ins) для данного чата.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT user_id, name, drinks, check_ins
            FROM participants
            WHERE chat_id=?
        """, (chat_id,))
        rows = await cursor.fetchall()
        return rows

# ================== ОПРОС (расписание) ==================

async def schedule_polls_loop():
    """
    Фоновая задача (крутится в while True):
    Каждую минуту проверяем все чаты, где active=1:
      - если текущее время >= poll_time чата,
      - и 'last_poll_date' не совпадает с сегодняшней датой,
      => шлём опрос и ставим last_poll_date = сегодня.
    """
    while True:
        now = datetime.now(timezone_moscow)
        today_str = now.strftime("%Y-%m-%d")

        # Достаём все активные чаты
        active_chats = await get_all_active_chats()  # [(chat_id, poll_time, last_poll_date), ...]

        for (chat_id, poll_time_str, last_poll_date) in active_chats:
            # Парсим poll_time_str типа "23:30"
            try:
                h, m = poll_time_str.split(":")
                poll_time_obj = time(hour=int(h), minute=int(m))
            except:
                # Если вдруг кривая запись, ставим дефолт 23:30
                poll_time_obj = time(23, 30)

            poll_today = datetime.combine(now.date(), poll_time_obj)
            # Привязываем к московскому часовому поясу
            poll_today = timezone_moscow.localize(poll_today)

            # Если мы ещё не слали сегодня (last_poll_date != today_str) и текущее время >= poll_today
            if last_poll_date != today_str and now >= poll_today:
                # Шлём опрос
                await send_poll(chat_id)
                # Обновляем last_poll_date, чтобы сегодня не слать повторно
                await set_chat_last_poll_date(chat_id, today_str)

        # Ждём минуту и повторяем
        await asyncio.sleep(60)

async def send_poll(chat_id: int):
    """
    Посылаем в указанный чат сообщение-опрос с кнопками «Не пил» / «Пил».
    """
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Не пил 🍵", callback_data="not_drink"),
            InlineKeyboardButton(text="Пил 🍺",    callback_data="drink")
        ]
    ])
    try:
        await bot.send_message(chat_id, "🔥 Ежедневный опрос: пил ли ты сегодня?", reply_markup=keyboard)
        print(f"[LOG] Опрос отправлен в чат {chat_id}")
    except Exception as e:
        print(f"[ERROR] Не удалось отправить опрос в чат {chat_id}: {e}")

# ================== ХЕНДЛЕРЫ ==================

dp = Dispatcher()

# --- Автодобавление записи о чате, если бота пригласили ---

@dp.my_chat_member()
async def bot_added_to_group(update: ChatMemberUpdated):
    """
    Срабатывает, когда бота добавляют/возвращают в чат.
    Создадим дефолтную запись в chat_settings (active=0, без дат), если ещё нет.
    """
    if update.new_chat_member.user.id == (await bot.me()).id:
        old_status = update.old_chat_member.status
        new_status = update.new_chat_member.status
        if old_status in ("kicked", "left") and new_status in ("member", "administrator"):
            chat_id = update.chat.id

            row = await load_chat_settings(chat_id)
            if not row:
                # создадим запись, но пока не активируем (active=0)
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("""
                        INSERT OR IGNORE INTO chat_settings (chat_id, active)
                        VALUES (?, 0)
                    """, (chat_id,))
                    await db.commit()

            # Приветственное сообщение
            await bot.send_message(
                chat_id,
                "Привет! Я бот для трезвого челленджа.\n"
                "Чтобы запустить челлендж в этом чате: /start_challenge <start_date> <end_date> <poll_time>\n"
                "Например: /start_challenge 2025-01-15 2025-12-09 22:00"
            )

# --- Кнопочки в виде Reply-клавиатуры ---

# Это раскладка команд, которая будет показываться при /start и /help
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="/start_challenge"), KeyboardButton(text="/edit_challenge")],
        [KeyboardButton(text="/join"), KeyboardButton(text="/stats")],
        [KeyboardButton(text="/report"), KeyboardButton(text="/mark_sober")],
        [KeyboardButton(text="/conditions"), KeyboardButton(text="/set_time")],
        [KeyboardButton(text="/help")]
    ],
    resize_keyboard=True
)

@dp.message(Command(commands=["start"]))
async def cmd_start(message: Message):
    """
    /start — приветственное сообщение + показываем меню кнопок.
    """
    await message.answer(
        "Привет! Я бот для трезвого челленджа.\n\n"
        "Нажми команду на клавиатуре или введи вручную:\n"
        "/start_challenge <start> <end> <time>  — запустить/обновить челлендж\n"
        "/join — присоединиться\n"
        "/stats — посмотреть статистику\n"
        "/report — сообщить о срыве\n"
        "/mark_sober — отметить трезвый день\n"
        "/conditions — посмотреть/изменить условия\n"
        "/set_time <HH:MM> — изменить время ежедневного опроса\n"
        "/edit_challenge <start> <end> <time> — отредактировать даты/время\n"
        "/help — показать это же меню",
        reply_markup=main_menu
    )

@dp.message(Command(commands=["help"]))
async def cmd_help(message: Message):
    """
    /help — то же самое, плюс выводим меню кнопок.
    """
    text = (
        "Команды:\n"
        "/start_challenge YYYY-MM-DD YYYY-MM-DD HH:MM\n"
        "   — Запустить (или обновить) челлендж с датами и временем.\n\n"
        "/edit_challenge YYYY-MM-DD YYYY-MM-DD HH:MM\n"
        "   — Отредактировать уже запущенный челлендж (даты/время).\n\n"
        "/set_time HH:MM\n"
        "   — Изменить только время отправки опроса.\n\n"
        "/join\n"
        "   — Присоединиться к челленджу.\n\n"
        "/report\n"
        "   — Сообщить, что сорвался (алкоголь).\n\n"
        "/mark_sober\n"
        "   — Отметить трезвый день.\n\n"
        "/stats\n"
        "   — Посмотреть статистику по участникам в этом чате.\n\n"
        "/conditions\n"
        "   — Посмотреть или изменить условия челленджа.\n\n"
        "/help\n"
        "   — Показать это сообщение."
    )
    await message.answer(text, reply_markup=main_menu)

# --- Запуск/редактирование челленджа ---

@dp.message(Command(commands=["start_challenge"]))
async def cmd_start_challenge(message: Message):
    """
    /start_challenge <start_date> <end_date> <poll_time>
    Пример: /start_challenge 2025-01-15 2025-12-09 22:00
    Если poll_time не указали, по умолчанию берём 23:30.
    """
    parts = message.text.strip().split()
    if len(parts) < 3:
        await message.answer("Формат: /start_challenge 2025-01-15 2025-12-09 [HH:MM]")
        return

    start_date_str = parts[1]
    end_date_str   = parts[2]
    poll_time_str  = "23:30"
    if len(parts) >= 4:
        poll_time_str = parts[3]

    # Можно проверить, что даты валидные
    # (для простоты просто обернём в try)
    try:
        datetime.strptime(start_date_str, "%Y-%m-%d")
        datetime.strptime(end_date_str, "%Y-%m-%d")
        # Время тоже проверяем (HH:MM)
        if poll_time_str != "":
            datetime.strptime(poll_time_str, "%H:%M")
    except ValueError:
        await message.answer("Неверный формат (дата или время). Пример: 2025-01-15 2025-12-09 22:00")
        return

    chat_id = message.chat.id
    # Добавляем/обновляем запись для чата
    await add_or_update_chat(chat_id, start_date_str, end_date_str, poll_time_str)
    # Челлендж активируем
    await set_chat_active(chat_id, True)

    await message.answer(
        f"Челлендж запущен/обновлён!\n"
        f"Период: {start_date_str} - {end_date_str}\n"
        f"Время опроса: {poll_time_str}\n"
        f"Теперь я буду слать ежедневный опрос в это время (по Москве)."
    )

@dp.message(Command(commands=["edit_challenge"]))
async def cmd_edit_challenge(message: Message):
    """
    Аналогично /start_challenge, но предполагается, что челлендж уже есть.
    По сути, делает то же самое, только логически "обновляет".
    """
    parts = message.text.strip().split()
    if len(parts) < 3:
        await message.answer("Формат: /edit_challenge 2025-02-01 2025-12-31 [HH:MM]")
        return

    start_date_str = parts[1]
    end_date_str   = parts[2]
    poll_time_str  = "23:30"
    if len(parts) >= 4:
        poll_time_str = parts[3]

    try:
        datetime.strptime(start_date_str, "%Y-%m-%d")
        datetime.strptime(end_date_str, "%Y-%m-%d")
        if poll_time_str != "":
            datetime.strptime(poll_time_str, "%H:%M")
    except ValueError:
        await message.answer("Неверный формат (даты или времени).")
        return

    chat_id = message.chat.id
    await add_or_update_chat(chat_id, start_date_str, end_date_str, poll_time_str)
    await set_chat_active(chat_id, True)
    await message.answer(
        f"Челлендж обновлён!\n"
        f"Новые даты: {start_date_str} - {end_date_str}\n"
        f"Новое время опроса: {poll_time_str}"
    )

@dp.message(Command(commands=["set_time"]))
async def cmd_set_time(message: Message):
    """
    Меняем только время опроса (в already активном чате).
    Пример: /set_time 21:00
    """
    parts = message.text.strip().split()
    if len(parts) < 2:
        await message.answer("Формат: /set_time HH:MM")
        return

    poll_time_str = parts[1]
    try:
        datetime.strptime(poll_time_str, "%H:%M")
    except ValueError:
        await message.answer("Неверный формат времени (HH:MM).")
        return

    chat_id = message.chat.id
    # обновим поле poll_time
    await set_chat_poll_time(chat_id, poll_time_str)
    # включаем чёллендж, если вдруг был неактивен (по желанию)
    await set_chat_active(chat_id, True)

    await message.answer(f"Время опроса обновлено на {poll_time_str} (по Москве).")

# --- Участие и статистика ---

@dp.message(Command(commands=["join"]))
async def cmd_join(message: Message):
    """
    /join — юзер добавляется к челленджу в этом чате.
    """
    chat_id = message.chat.id
    user = message.from_user
    await add_participant(chat_id, user.id, user.full_name)
    await message.answer(f"🍵 {user.full_name}, добро пожаловать в челлендж этого чата!")

@dp.message(Command(commands=["stats"]))
async def cmd_stats(message: Message):
    """
    /stats — показать участников и прогресс по этому чату.
    """
    chat_id = message.chat.id
    settings = await load_chat_settings(chat_id)
    if not settings:
        await message.answer("В этом чате нет настроек челленджа. Попробуй /start_challenge.")
        return

    start_date_str, end_date_str, conditions, active, poll_time, last_poll_date = settings
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

    participants = await get_stats_for_chat(chat_id)
    if not participants:
        await message.answer("Пока никто не присоединился к этому челленджу (/join).")
        return

    msg = (
        f"Челлендж в этом чате:\n"
        f"Период: {start_date_str} - {end_date_str}\n"
        f"Время опроса: {poll_time}\n"
        f"Прошло дней: {days_passed}, осталось: {days_left}\n\n"
        "Участники:\n"
    )
    for user_id, name, drinks, check_ins in participants:
        msg += f"• {name}: сорвался {drinks} раз(а), трезвых дней {check_ins}\n"

    await message.answer(msg)

# --- Срыв и трезвость ---

@dp.message(Command(commands=["report"]))
async def cmd_report(message: Message):
    """
    /report — увеличить счётчик срывов (drinks).
    """
    chat_id = message.chat.id
    user_id = message.from_user.id
    await update_stat(chat_id, user_id, "drinks")
    await message.answer("📉 Записал срыв. Мы все тебя презираем!)))")

@dp.message(Command(commands=["mark_sober"]))
async def cmd_mark_sober(message: Message):
    """
    /mark_sober — увеличить счётчик трезвых дней (check_ins).
    """
    chat_id = message.chat.id
    user_id = message.from_user.id
    await update_stat(chat_id, user_id, "check_ins")
    await message.answer("🎉 Трезвый день отмечен! Отлично!")

# --- Условия ---

@dp.message(Command(commands=["conditions"]))
async def cmd_conditions(message: Message):
    """
    /conditions — показывает или меняет текст условий.
    Пример:
      /conditions -> показать текущие
      /conditions Новые правила... -> записать новые
    """
    chat_id = message.chat.id
    text = message.text.strip()
    parts = text.split(maxsplit=1)

    row = await load_chat_settings(chat_id)
    if not row:
        await message.answer("Здесь нет настроек челленджа. Попробуй /start_challenge.")
        return
    start_date_str, end_date_str, old_conditions, active, poll_time, last_poll_date = row

    if len(parts) > 1:
        new_conds = parts[1].strip()
        # Запишем новые условия
        await add_or_update_chat(chat_id, start_date_str, end_date_str, poll_time, new_conds)
        await message.answer(f"Условия обновлены:\n{new_conds}")
    else:
        if old_conditions:
            await message.answer(f"Текущие условия:\n{old_conditions}")
        else:
            await message.answer("Пока условий нет. Введи /conditions <текст> чтобы задать.")

# --- Обработка кнопок опроса ---

@dp.callback_query(F.data.in_({"not_drink", "drink"}))
async def handle_poll_response(callback: CallbackQuery):
    """
    Когда пользователь жмёт «Не пил 🍵» или «Пил 🍺» в опросе.
    """
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id  # тот чат, где был опрос

    if callback.data == "not_drink":
        await update_stat(chat_id, user_id, "check_ins")
        await callback.answer("🎉 Молодец, держись дальше!")
    else:
        await update_stat(chat_id, user_id, "drinks")
        await callback.answer("📉 Записал. Не горжусь!")

# ================== ЗАПУСК БОТА ==================

async def main():
    # 1. Инициализируем базу
    await init_db()

    # 2. Запускаем фоновую задачу, которая каждые 60 секунд проверяет, не пора ли слать опрос.
    asyncio.create_task(schedule_polls_loop())

    # 3. Стартуем поллинг
    print("Бот запущен!")
    await dp.start_polling(Bot(token=BOT_TOKEN))

if __name__ == "__main__":
    asyncio.run(main())
