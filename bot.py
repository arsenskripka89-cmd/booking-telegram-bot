import json
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from config import BOT_TOKEN, ADMINS

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

DATA_FILE = "bookings.json"

class BookingStates(StatesGroup):
    waiting_for_seats = State()
    waiting_for_direction = State()
    waiting_for_date = State()
    waiting_for_time = State()
    waiting_for_comment = State()
    waiting_for_phone = State()

def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def main_menu(is_driver=False):
    buttons = [
        [KeyboardButton(text="🚐 Забронювати місце")],
        [KeyboardButton(text="📋 Мої бронювання")]
    ]
    if is_driver:
        buttons.append([KeyboardButton(text="👨‍✈️ Адмін-панель")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_available_dates():
    """Повертає список дат на 7 днів вперед"""
    today = datetime.now().date()
    return [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

def get_times_for_direction(direction, selected_date):
    """Генерує список часу для напрямку + фільтр по 20 хвилинах"""
    now = datetime.now()
    today = now.date()
    times = []

    if "Рокитне" in direction and "Київ" in direction:
        times = [
            "05:00", "05:30", "06:00", "07:00", "08:00", "09:00",
            "10:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00"
        ]
    elif "Київ" in direction and "Рокитне" in direction:
        times = [f"{h:02d}:00" for h in range(8, 21)]

    # Якщо дата сьогодні — відфільтрувати минулі рейси і ті, що через <20 хв
    if selected_date == today.strftime("%Y-%m-%d"):
        valid_times = []
        current_time = now + timedelta(minutes=20)
        for t in times:
            dep = datetime.strptime(f"{selected_date} {t}", "%Y-%m-%d %H:%M")
            if dep > current_time:
                valid_times.append(t)
        times = valid_times

    return times

@dp.message(CommandStart())
async def start(msg: types.Message, state: FSMContext):
    await state.clear()
    data = load_data()
    user_id = str(msg.from_user.id)
    if user_id not in data:
        data[user_id] = {"bookings": [], "phone": None}
        save_data(data)
    is_driver = msg.from_user.id in ADMINS
    await msg.answer(
        "👋 Вітаємо у сервісі бронювання маршрутів Київ ↔️ Рокитне!",
        reply_markup=main_menu(is_driver)
    )

@dp.message(F.text == "🚐 Забронювати місце")
async def book_start(msg: types.Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1"), KeyboardButton(text="2"), KeyboardButton(text="3")],
            [KeyboardButton(text="❌ Відмінити")]
        ], resize_keyboard=True
    )
    await msg.answer("Скільки місць хочете забронювати?", reply_markup=kb)
    await state.set_state(BookingStates.waiting_for_seats)

@dp.message(F.text == "❌ Відмінити")
async def cancel_booking(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("Бронювання скасовано.", reply_markup=main_menu(msg.from_user.id in ADMINS))

@dp.message(BookingStates.waiting_for_seats)
async def process_seats(msg: types.Message, state: FSMContext):
    seats = msg.text.strip()
    if not seats.isdigit():
        await msg.answer("Введіть число.")
        return
    
    await state.update_data(seats=seats)
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚐 Київ → Рокитне")],
            [KeyboardButton(text="🚌 Рокитне → Київ")],
            [KeyboardButton(text="❌ Відмінити")]
        ], resize_keyboard=True
    )
    await msg.answer("Оберіть напрямок:", reply_markup=kb)
    await state.set_state(BookingStates.waiting_for_direction)

@dp.message(BookingStates.waiting_for_direction)
async def process_direction(msg: types.Message, state: FSMContext):
    direction = msg.text
    await state.update_data(direction=direction)
    available_dates = get_available_dates()
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    for date_str in available_dates:
        kb.add(KeyboardButton(text=date_str))
    kb.add(KeyboardButton(text="❌ Відмінити"))
    await msg.answer("Оберіть дату поїздки:", reply_markup=kb)
    await state.set_state(BookingStates.waiting_for_date)

@dp.message(BookingStates.waiting_for_date)
async def process_date(msg: types.Message, state: FSMContext):
    selected_date = msg.text.strip()
    user_data = await state.get_data()
    direction = user_data["direction"]
    times = get_times_for_direction(direction, selected_date)

    if not times:
        await msg.answer("🚫 Немає доступних рейсів на цю дату.")
        await state.clear()
        return

    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    for t in times:
        kb.add(KeyboardButton(text=t))
    kb.add(KeyboardButton(text="❌ Відмінити"))

    await state.update_data(date=selected_date)
    await msg.answer("Оберіть час рейсу:", reply_markup=kb)
    await state.set_state(BookingStates.waiting_for_time)

@dp.message(BookingStates.waiting_for_time)
async def process_time(msg: types.Message, state: FSMContext):
    await state.update_data(time=msg.text)
    await msg.answer("Напишіть коментар або місце посадки:")
    await state.set_state(BookingStates.waiting_for_comment)

@dp.message(BookingStates.waiting_for_comment)
async def process_comment(msg: types.Message, state: FSMContext):
    await state.update_data(comment=msg.text)
    
    data = load_data()
    user_id = str(msg.from_user.id)
    phone = data[user_id].get("phone")
    
    if not phone:
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📱 Надіслати свій номер", request_contact=True)],
                [KeyboardButton(text="❌ Відмінити")]
            ], resize_keyboard=True
        )
        await msg.answer("Надішліть свій номер телефону:", reply_markup=kb)
        await state.set_state(BookingStates.waiting_for_phone)
    else:
        user_data = await state.get_data()
        booking = {
            "date": user_data["date"],
            "time": user_data["time"],
            "direction": user_data["direction"],
            "seats": user_data["seats"],
            "comment": user_data["comment"],
            "phone": phone
        }
        data[user_id]["bookings"].append(booking)
        save_data(data)
        await state.clear()
        await msg.answer("✅ Ваше бронювання підтверджено!", reply_markup=main_menu(msg.from_user.id in ADMINS))

@dp.message(BookingStates.waiting_for_phone, F.contact)
async def process_contact(msg: types.Message, state: FSMContext):
    data = load_data()
    user_id = str(msg.from_user.id)
    phone = msg.contact.phone_number
    data[user_id]["phone"] = phone
    save_data(data)
    
    user_data = await state.get_data()
    booking = {
        "date": user_data["date"],
        "time": user_data["time"],
        "direction": user_data["direction"],
        "seats": user_data["seats"],
        "comment": user_data["comment"],
        "phone": phone
    }
    data[user_id]["bookings"].append(booking)
    save_data(data)
    
    await state.clear()
    await msg.answer("✅ Телефон збережено.")
    await msg.answer("Ваше бронювання підтверджено!", reply_markup=main_menu(msg.from_user.id in ADMINS))

@dp.message(F.text == "📋 Мої бронювання")
async def my_bookings(msg: types.Message):
    data = load_data()
    user_id = str(msg.from_user.id)
    bookings = data.get(user_id, {}).get("bookings", [])
    if not bookings:
        await msg.answer("У вас немає активних бронювань.")
        return

    text = "Ваші бронювання:\n"
    for b in bookings:
        text += f"📅 {b['date']} | 🕒 {b['time']} | {b['direction']} | {b['seats']} місць\n📍 {b['comment']}\n\n"
    await msg.answer(text)

if __name__ == "__main__":
    import asyncio
    asyncio.run(dp.start_polling(bot))
