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
from datetime import datetime, timedelta
import pytz
import aiosqlite

BOT_TOKEN = "7926233927:AAFjSBeFDgrjENeTb-d8pxvUfb0hlv9YF94"  # Вставь сюда свой токен
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Параметры челленджа ---
start_date = datetime(2025, 1, 15)
end_date   = datetime(2025, 12, 9)

# Часовой пояс Москвы
timezone_moscow = pytz.timezone("Europe/Moscow")

# Время отправки опроса (формат "HH:MM")
daily_poll_time = "15:30"

# Множество чатов, куда шлём ежедневный опрос
# Оно будет загружаться из базы при старте, чтобы не теряться при перезапусках.
active_chats = set()

# --- ИНИЦИАЛИЗАЦИЯ БД ---

async def init_db():
    """Создаём таблицы, если ещё не созданы."""
    async with aiosqlite.connect("participants.db") as db:
        # Таблица с участниками
        await db.execute("""
            CREATE TABLE IF NOT EXISTS participants (
                user_id   INTEGER PRIMARY KEY,
                name      TEXT,
                drinks    INTEGER DEFAULT 0,
                check_ins INTEGER DEFAULT 0
            )
        """)
        # Таблица с активными чатами
        await db.execute("""
            CREATE TABLE IF NOT EXISTS active_chats (
                chat_id INTEGER PRIMARY KEY
            )
        """)
        await db.commit()

async def load_active_chats():
    """Загружаем список чатов из таблицы active_chats в память."""
    loaded_chats = set()
    async with aiosqlite.connect("participants.db") as db:
        async with db.execute("SELECT chat_id FROM active_chats") as cursor:
            async for row in cursor:
                loaded_chats.add(row[0])
    return loaded_chats

async def add_chat(chat_id: int):
    """Добавляем чат в БД (active_chats)."""
    async with aiosqlite.connect("participants.db") as db:
        await db.execute("INSERT OR IGNORE INTO active_chats (chat_id) VALUES (?)", (chat_id,))
        await db.commit()

# --- РАБОТА С УЧАСТНИКАМИ ---

async def add_participant(user_id: int, name: str):
    """Добавляем участника в базу (participants), если его там нет."""
    async with aiosqlite.connect("participants.db") as db:
        await db.execute(
            "INSERT OR IGNORE INTO participants (user_id, name) VALUES (?, ?)",
            (user_id, name),
        )
        await db.commit()

async def update_stat(user_id: int, column: str):
    """Увеличиваем drinks или check_ins на 1 для конкретного участника."""
    async with aiosqlite.connect("participants.db") as db:
        await db.execute(f"UPDATE participants SET {column} = {column} + 1 WHERE user_id = ?", (user_id,))
        await db.commit()

async def get_stats():
    """Возвращаем полный список участников: (user_id, name, drinks, check_ins)."""
    async with aiosqlite.connect("participants.db") as db:
        async with db.execute("SELECT * FROM participants") as cursor:
            return await cursor.fetchall()

# --- ФУНКЦИЯ ДЛЯ ЕЖЕДНЕВНОЙ РАССЫЛКИ ОПРОСА ---

async def send_daily_poll():
    """Бесконечный цикл: ждёт до daily_poll_time по Мск, рассылает опросы."""
    while True:
        now = datetime.now(timezone_moscow)
        target_time = datetime.strptime(daily_poll_time, "%H:%M").time()
        target_datetime = timezone_moscow.localize(datetime.combine(now.date(), target_time))

        # Если текущее время уже позже целевого — сдвигаем отправку на сутки вперёд
        if now.time() > target_time:
            target_datetime += timedelta(days=1)

        delay = (target_datetime - now).total_seconds()
        print(f"[LOG] Следующий опрос будет отправлен через {delay} секунд.")
        await asyncio.sleep(delay)

        # Рассылка во все активные чаты
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
                print(f"[ERROR] Не удалось отправить сообщение в чат {chat_id}: {e}")

# --- ХЕНДЛЕРЫ ---

@dp.my_chat_member()
async def bot_added_to_group(update: ChatMemberUpdated):
    """
    Срабатывает, когда статус бота в группе меняется.
    Если бот был кикнут/left, а теперь member/administrator — значит, бота добавили в группу.
    Здесь же можно автоматически добавлять чат в список рассылок.
    """
    if update.new_chat_member.user.id == (await bot.me()).id:
        old_status = update.old_chat_member.status
        new_status = update.new_chat_member.status
        if old_status in ("kicked", "left") and new_status in ("member", "administrator"):
            chat_id = update.chat.id

            # Добавляем чат в список активных
            active_chats.add(chat_id)
            await add_chat(chat_id)

            # Приветственное сообщение
            await bot.send_message(
                chat_id,
                "Всем привет! Я бот для трезвого челленджа.\n"
                "Я уже занёс этот чат в список для ежедневных опросов."
            )

@dp.message(Command(commands=["start"]))
async def cmd_start(message: Message):
    """Приветствие по команде /start."""
    await message.answer(
        "Привет! Я бот для трезвого челленджа. 🎉\n\n"
        "Вот что я умею:\n"
        "• /start_challenge — начать челлендж (добавляет группу в список для ежедневных опросов)\n"
        "• /join — присоединиться к челленджу\n"
        "• /stats — посмотреть статистику\n"
        "• /report — зафиксировать приём алкоголя\n"
        "• /mark_sober — отметить трезвый день\n"
        "• /conditions — посмотреть условия челленджа\n"
        "• /help — показать список команд\n\n"
        "Добавь меня в группу, и я буду помогать всем держаться!"
    )

@dp.message(Command(commands=["help"]))
async def cmd_help(message: Message):
    """Справка по команде /help."""
    await message.answer(
        "Команды бота:\n"
        "/start_challenge — Запустить челлендж (начать ежедневные опросы в этом чате)\n"
        "/join — Присоединиться к челленджу\n"
        "/stats — Статистика участников\n"
        "/report — Сообщить о приёме алкоголя\n"
        "/mark_sober — Отметить трезвый день\n"
        "/conditions — Посмотреть условия челленджа\n"
        "/help — Показать это сообщение\n"
    )

@dp.message(Command(commands=["start_challenge"]))
async def cmd_start_challenge(message: Message):
    """
    Команда для ручного запуска рассылки в данном чате.  
    Если хочешь, чтобы чат автоматически добавлялся при добавлении бота,
    в принципе, можно не вызывать эту команду. Но пусть будет.
    """
    chat_id = message.chat.id
    active_chats.add(chat_id)
    await add_chat(chat_id)
    await message.answer(
        f"🚀 Челлендж запущен!\n"
        f"Период: c {start_date.strftime('%d.%m.%Y')} по {end_date.strftime('%d.%m.%Y')}. 🏁\n"
        f"Каждый день я буду присылать опрос в {daily_poll_time} по Москве."
    )

@dp.message(Command(commands=["join"]))
async def cmd_join(message: Message):
    """Команда /join — пользователь регистрируется как участник."""
    user = message.from_user
    await add_participant(user.id, user.full_name)
    await message.answer(
        f"🍵 {user.full_name}, добро пожаловать в клуб трезвых единорогов! 🦄"
    )

@dp.message(Command(commands=["stats"]))
async def cmd_stats(message: Message):
    """Команда /stats — показать общую статистику."""
    all_stats = await get_stats()
    if not all_stats:
        await message.answer("🤷 Пока никто не присоединился к нашему трезвому движу.")
        return

    now = datetime.now()
    days_passed = (now - start_date).days
    if days_passed < 0:
        days_passed = 0
    days_left = (end_date - now).days
    if days_left < 0:
        days_left = 0

    stats_msg = (
        f"⏳ С начала челленджа прошло: {days_passed} дней.\n"
        f"До конца осталось: {days_left} дней.\n\n"
    )

    for user_id, name, drinks, check_ins in all_stats:
        stats_msg += f"🍺 {name}: {drinks} раз(а) сорвался(ась), {check_ins} трезвых отметок.\n"

    await message.answer(stats_msg)

@dp.message(Command(commands=["report"]))
async def cmd_report(message: Message):
    """Команда /report — пользователь признаёт «срыв» (drinks += 1)."""
    user_id = message.from_user.id
    await update_stat(user_id, "drinks")
    await message.answer("📉 Факт распития отмечен! Старайся держаться дальше. 🫣")

@dp.message(Command(commands=["mark_sober"]))
async def cmd_mark_sober(message: Message):
    """Команда /mark_sober — пользователь отмечает трезвый день (check_ins += 1)."""
    user_id = message.from_user.id
    await update_stat(user_id, "check_ins")
    await message.answer("🎉 Отлично, трезвый день записан! Так держать! 🍵")

@dp.message(Command(commands=["conditions"]))
async def cmd_conditions(message: Message):
    """Команда /conditions — показать условия челленджа."""
    await message.answer(
        "💪 Условия челленджа:\n"
        f"Мы не пьём с {start_date.strftime('%d.%m.%Y')} по {end_date.strftime('%d.%m.%Y')}!\n\n"
        "• Возможны отступления: не чаще 1 раза в 2 месяца,\n"
        "  по очень уважительной причине (праздник, ужин с Бихером).\n"
        "• За каждый срыв — штраф: 1🍋!!!"
    )

@dp.callback_query(F.data.in_({"not_drink", "drink"}))
async def handle_poll_response(callback: CallbackQuery):
    """
    Обработчик инлайн-кнопок «Не пил 🍵» / «Пил 🍺» из ежедневного опроса.
    При нажатии обновляет статистику (check_ins или drinks).
    """
    user_id = callback.from_user.id
    action = callback.data

    if action == "not_drink":
        await update_stat(user_id, "check_ins")
        await callback.answer("🎉 Молодец, держись дальше!")
    elif action == "drink":
        await update_stat(user_id, "drinks")
        await callback.answer("📉 Записал. Завтра — новый день!")

# --- ТОЧКА ВХОДА ---

async def main():
    await init_db()  # Создаём таблицы, если надо
    global active_chats
    active_chats = await load_active_chats()  # Загружаем из БД список чатов

    print("Бот запущен и готов к работе!")
    # Отдельной задачей запускаем цикл, который ежедневно шлёт опрос
    asyncio.create_task(send_daily_poll())

    # Запускаем поллинг (бот будет получать апдейты)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
