import asyncio
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from datetime import datetime, timedelta
import aiosqlite

# Токен бота
BOT_TOKEN = "7926233927:AAFjSBeFDgrjENeTb-d8pxvUfb0hlv9YF94"

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Дата начала и конца челленджа
start_date = datetime(2025, 1, 15)
end_date = datetime(2025, 12, 9)
active_chats = set()
daily_poll_time = "20:00"  # Время отправки опроса (в формате HH:MM)

# Инициализация базы данных
async def init_db():
    async with aiosqlite.connect("participants.db") as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS participants (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            drinks INTEGER DEFAULT 0,
            check_ins INTEGER DEFAULT 0
            )"""
        )
        await db.commit()

# Добавление участника
async def add_participant(user_id, name):
    async with aiosqlite.connect("participants.db") as db:
        await db.execute(
            "INSERT OR IGNORE INTO participants (user_id, name) VALUES (?, ?)",
            (user_id, name),
        )
        await db.commit()

# Обновление статистики
async def update_stat(user_id, column):
    async with aiosqlite.connect("participants.db") as db:
        await db.execute(f"UPDATE participants SET {column} = {column} + 1 WHERE user_id = ?", (user_id,))
        await db.commit()

# Получение статистики
async def get_stats():
    async with aiosqlite.connect("participants.db") as db:
        async with db.execute("SELECT * FROM participants") as cursor:
            return await cursor.fetchall()

# Приветственное сообщение
@dp.message(Command(commands=["start"]))
async def send_welcome(message: Message):
    await message.answer(
        "Привет! Я бот для трезвого челленджа. 🎉\n"
        "Вот что я умею:\n"
        "/start_challenge - начать челлендж\n"
        "/join - присоединиться к челленджу\n"
        "/stats - посмотреть статистику\n"
        "/report - зафиксировать приём алкоголя\n"
        "/mark_sober - отметить трезвый день\n"
        "/conditions - посмотреть условия челленджа\n"
        "/help - показать список команд\n"
        "Добавь меня в группу, и я буду помогать всем держаться!"
    )

# Список команд
@dp.message(Command(commands=["help"]))
async def send_help(message: Message):
    await message.answer(
        "Команды бота:\n"
        "/start_challenge - Запустить челлендж\n"
        "/join - Присоединиться к челленджу\n"
        "/stats - Статистика участников\n"
        "/report - Сообщить о приёме алкоголя\n"
        "/mark_sober - Отметить трезвый день\n"
        "/conditions - Посмотреть условия челленджа\n"
        "/help - Показать это сообщение"
    )

# Хендлер для команды /start_challenge
@dp.message(Command(commands=["start_challenge"]))
async def start_challenge(message: Message):
    global active_chats
    active_chats.add(message.chat.id)
    await message.answer(f"🚀 Челлендж начат! С {start_date.strftime('%d.%m.%Y')} до {end_date.strftime('%d.%m.%Y')} 🏁")

# Хендлер для команды /join
@dp.message(Command(commands=["join"]))
async def join_challenge(message: Message):
    user = message.from_user
    await add_participant(user.id, user.full_name)
    await message.answer(f"🍵 {user.full_name}, добро пожаловать в клуб трезвых единорогов! 🦄")

# Хендлер для команды /stats
@dp.message(Command(commands=["stats"]))
async def stats(message: Message):
    stats = await get_stats()
    if not stats:
        await message.answer("🤷 Пока никто не решился присоединиться к нашему трезвому движу.")
        return
    days_passed = (datetime.now() - start_date).days
    days_left = (end_date - datetime.now()).days
    stats_message = f"⏳ С начала челленджа прошло: {days_passed} дней.\nДо конца осталось: {days_left} дней.\n\n"
    for user_id, name, drinks, check_ins in stats:
        stats_message += f"🍺 {name}: {drinks} раз(а) сорвался(ась), {check_ins} отметок трезвости.\n"
    await message.answer(stats_message)

# Хендлер для команды /report
@dp.message(Command(commands=["report"]))
async def report(message: Message):
    user_id = message.from_user.id
    await update_stat(user_id, "drinks")
    await message.answer("📉 Эх, ну ты и сорвался! Записал. Попробуй больше не пить. 🫣")

# Хендлер для команды /mark_sober
@dp.message(Command(commands=["mark_sober"]))
async def mark_sober(message: Message):
    user_id = message.from_user.id
    await update_stat(user_id, "check_ins")
    await message.answer("🎉 Трезвый день записан! Так держать! 🍵")

# Хендлер для команды /conditions
@dp.message(Command(commands=["conditions"]))
async def show_conditions(message: Message):
    await message.answer(
        "💪 Условия челленджа:\n"
        f"Мы не пьём с {start_date.strftime('%d.%m.%Y')} до {end_date.strftime('%d.%m.%Y')}!\n"
        "Возможны отступления — не чаще 1 раза в 2 месяца, \n"
        "по очень уважительной причине (праздник, ужин с Бихером).\n"
        "Штраф: 1🍋 за каждое нарушение."
    )

# Основная функция для запуска бота
async def main():
    await init_db()
    dp.message.register(send_welcome, Command(commands=["start"]))
    dp.message.register(send_help, Command(commands=["help"]))
    dp.message.register(start_challenge, Command(commands=["start_challenge"]))
    dp.message.register(join_challenge, Command(commands=["join"]))
    dp.message.register(stats, Command(commands=["stats"]))
    dp.message.register(report, Command(commands=["report"]))
    dp.message.register(mark_sober, Command(commands=["mark_sober"]))
    dp.message.register(show_conditions, Command(commands=["conditions"]))

    print("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
