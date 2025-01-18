import asyncio
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from datetime import datetime, timedelta

# Токен бота
BOT_TOKEN = "7926233927:AAFjSBeFDgrjENeTb-d8pxvUfb0hlv9YF94"

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Хранилище данных
participants = {}
start_date = datetime(2025, 1, 15)  # Дата начала челленджа
end_date = datetime(2025, 12, 9)  # Дата окончания челленджа
active_chats = set()
daily_poll_time = "20:00"  # Время отправки опроса (в формате HH:MM)

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
    if user.id not in participants:
        participants[user.id] = {"name": user.full_name, "drinks": 0, "check_ins": 0}
        await message.answer(f"🍵 {user.full_name}, добро пожаловать в клуб трезвых единорогов! 🦄")
    else:
        await message.answer("😎 Ты уже в деле, не хитри!")

# Хендлер для команды /stats
@dp.message(Command(commands=["stats"]))
async def stats(message: Message):
    if not participants:
        await message.answer("🤷 Пока никто не решился присоединиться к нашему трезвому движу.")
        return
    days_passed = (datetime.now() - start_date).days
    days_left = (end_date - datetime.now()).days
    stats_message = f"⏳ С начала челленджа прошло: {days_passed} дней.\nДо конца осталось: {days_left} дней.\n\n"
    for user_id, data in participants.items():
        stats_message += f"🍺 {data['name']}: {data['drinks']} раз(а) сорвался(ась), {data['check_ins']} отметок трезвости.\n"
    await message.answer(stats_message)

# Хендлер для команды /report
@dp.message(Command(commands=["report"]))
async def report(message: Message):
    user_id = message.from_user.id
    if user_id in participants:
        participants[user_id]["drinks"] += 1
        await message.answer("📉 Эх, ну ты и сорвался! Записал. Попробуй больше не пить. 🫣")
    else:
        await message.answer("🤔 Ты ещё не участвуешь. Напиши /join, чтобы вступить!")

# Хендлер для команды /mark_sober
@dp.message(Command(commands=["mark_sober"]))
async def mark_sober(message: Message):
    user_id = message.from_user.id
    if user_id in participants:
        participants[user_id]["check_ins"] += 1
        await message.answer("🎉 Трезвый день записан! Так держать! 🍵")
    else:
        await message.answer("🤔 Ты ещё не участвуешь. Напиши /join, чтобы вступить!")

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

# Опрос для ежедневной отметки
async def send_daily_poll():
    while True:
        now = datetime.now()
        target_time = datetime.strptime(daily_poll_time, "%H:%M").time()
        target_datetime = datetime.combine(now.date(), target_time)

        # Если текущее время уже позже целевого, переносим отправку на следующий день
        if now.time() > target_time:
            target_datetime += timedelta(days=1)

        # Рассчитать задержку до отправки
        delay = (target_datetime - now).total_seconds()
        await asyncio.sleep(delay)

        for chat_id in active_chats:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="Не пил 🍵", callback_data="not_drink"),
                    InlineKeyboardButton(text="Пил 🍺", callback_data="drink")
                ]
            ])
            await bot.send_message(chat_id, "🔥 Ежедневный опрос: пил ли ты сегодня?", reply_markup=keyboard)

# Обработка ответов на опрос
@dp.callback_query(lambda c: c.data in ["not_drink", "drink"])
async def handle_poll_response(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in participants:
        await callback.answer("🤔 Ты ещё не участвуешь. Напиши /join, чтобы вступить!", show_alert=True)
        return

    if callback.data == "not_drink":
        participants[user_id]["check_ins"] += 1
        await callback.answer("🎉 Молодец, держись дальше!")
    elif callback.data == "drink":
        participants[user_id]["drinks"] += 1
        await callback.answer("📉 Записал. Не сдавайся, завтра новый день!")

# Приветствие при добавлении бота в чат
@dp.message()
async def greet_new_chat_on_addition(message: Message):
    if message.chat.type in ["group", "supergroup"] and message.text:
        bot_info = await bot.get_me()
        chat_administrators = [admin.user.id for admin in await bot.get_chat_administrators(message.chat.id)]
        if bot_info.id not in chat_administrators:
            return

        if message.chat.id not in active_chats:
            active_chats.add(message.chat.id)
            await message.answer(
                "👋 Спасибо, что добавили меня в чат! Я помогу вам провести трезвый челлендж. 🎉\n"
                "Вот что я умею:\n"
                "/start_challenge - начать челлендж\n"
                "/join - присоединиться к челленджу\n"
                "/stats - посмотреть статистику\n"
                "/report - зафиксировать приём алкоголя\n"
                "/mark_sober - отметить трезвый день\n"
                "/conditions - посмотреть условия челленджа\n"
                "/help - показать список команд\n"
                "Добавьте участников и начнём!"
            )

# Основная функция для запуска бота
async def main():
    dp.message.register(send_welcome, Command(commands=["start"]))
    dp.message.register(send_help, Command(commands=["help"]))
    dp.message.register(start_challenge, Command(commands=["start_challenge"]))
    dp.message.register(join_challenge, Command(commands=["join"]))
    dp.message.register(stats, Command(commands=["stats"]))
    dp.message.register(report, Command(commands=["report"]))
    dp.message.register(mark_sober, Command(commands=["mark_sober"]))
    dp.message.register(show_conditions, Command(commands=["conditions"]))
    dp.callback_query.register(handle_poll_response, lambda c: c.data in ["not_drink", "drink"])

    print("Бот запущен!")
    asyncio.create_task(send_daily_poll())  # Запуск ежедневного опроса
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
