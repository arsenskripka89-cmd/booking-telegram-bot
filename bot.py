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
CANCEL_TEXT = "❌ Відмінити"

# -------------------- STORAGE UTILS --------------------
def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# -------------------- KEYBOARDS --------------------
def main_menu(is_driver=False):
    buttons = [
        [KeyboardButton(text="🚐 Забронювати місце")],
        [KeyboardButton(text="📋 Мої бронювання")]
    ]
    if is_driver:
        buttons.append([KeyboardButton(text="👨‍✈️ Адмін-панель")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def rows_of(items, n=3):
    return [items[i:i+n] for i in range(0, len(items), n)]

# -------------------- DATE/TIME HELPERS --------------------
def get_week_dates():
    """7 днів уперед від сьогодні."""
    today = datetime.now().date()
    return [(today + timedelta(days=i)) for i in range(7)]

def base_times_for(direction: str):
    """Базовий розклад без фільтрів."""
    # Рокитне → Київ
    if ("Рокитне" in direction) and ("→ Київ" in direction):
        return ["05:00","05:30","06:00","07:00","08:00","09:00",
                "10:00","12:00","13:00","14:00","15:00","16:00","17:00"]
    # Київ → Рокитне
    return [f"{h:02d}:00" for h in range(8, 21)]

def get_times(direction: str, selected_date):
    """
    Повертає список годин з урахуванням:
    - обраного напрямку,
    - заборони бронювання, якщо до рейсу < 20 хв,
    - не показує вже минулі рейси.
    """
    now = datetime.now()
    base = base_times_for(direction)
    times = []

    for t in base:
        h, m = map(int, t.split(":"))
        dep_dt = datetime.combine(selected_date, datetime.min.time()) + timedelta(hours=h, minutes=m)
        if dep_dt > now + timedelta(minutes=20):
            times.append(t)

    return times

# -------------------- FSM STATES --------------------
class BookingStates(StatesGroup):
    waiting_for_seats = State()
    waiting_for_date = State()
    waiting_for_direction = State()
    waiting_for_time = State()
    waiting_for_comment = State()
    waiting_for_phone = State()

class AdminStates(StatesGroup):
    waiting_for_direction = State()
    waiting_for_date = State()
    waiting_for_time = State()

# -------------------- COMMON HANDLERS --------------------
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

@dp.message(F.text == CANCEL_TEXT)
async def cancel_any(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("Бронювання скасовано.", reply_markup=main_menu(msg.from_user.id in ADMINS))

# -------------------- BOOKING FLOW --------------------
@dp.message(F.text == "🚐 Забронювати місце")
async def book_start(msg: types.Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1"), KeyboardButton(text="2"), KeyboardButton(text="3")],
            [KeyboardButton(text=CANCEL_TEXT)]
        ],
        resize_keyboard=True
    )
    await msg.answer("Скільки місць хочете забронювати?", reply_markup=kb)
    await state.set_state(BookingStates.waiting_for_seats)

@dp.message(BookingStates.waiting_for_seats)
async def process_seats(msg: types.Message, state: FSMContext):
    seats = msg.text.strip()
    if not seats.isdigit() or int(seats) <= 0:
        await msg.answer("Введіть, будь ласка, число (1–9).")
        return
    await state.update_data(seats=seats)

    # Дата (7 днів уперед)
    dates = get_week_dates()
    kb = [[KeyboardButton(text=str(d))] for d in dates]
    kb.append([KeyboardButton(text=CANCEL_TEXT)])
    await msg.answer("Оберіть дату поїздки:", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))
    await state.set_state(BookingStates.waiting_for_date)

@dp.message(BookingStates.waiting_for_date)
async def process_date(msg: types.Message, state: FSMContext):
    try:
        selected_date = datetime.strptime(msg.text, "%Y-%m-%d").date()
    except:
        await msg.answer("Будь ласка, оберіть дату з кнопок.")
        return

    await state.update_data(date=str(selected_date))
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚐 Київ → Рокитне")],
            [KeyboardButton(text="🚌 Рокитне → Київ")],
            [KeyboardButton(text=CANCEL_TEXT)]
        ],
        resize_keyboard=True
    )
    await msg.answer("Оберіть напрямок:", reply_markup=kb)
    await state.set_state(BookingStates.waiting_for_direction)

@dp.message(BookingStates.waiting_for_direction)
async def process_direction(msg: types.Message, state: FSMContext):
    direction = msg.text
    data_user = await state.get_data()
    selected_date = datetime.strptime(data_user["date"], "%Y-%m-%d").date()

    times = get_times(direction, selected_date)
    if not times:
        await msg.answer("На обрану дату немає доступних рейсів.", reply_markup=main_menu(msg.from_user.id in ADMINS))
        await state.clear()
        return

    kb_rows = rows_of([KeyboardButton(text=t) for t in times], 3)
    kb_rows.append([KeyboardButton(text=CANCEL_TEXT)])
    await state.update_data(direction=direction)
    await msg.answer("Оберіть час:", reply_markup=ReplyKeyboardMarkup(keyboard=kb_rows, resize_keyboard=True))
    await state.set_state(BookingStates.waiting_for_time)

@dp.message(BookingStates.waiting_for_time)
async def process_time(msg: types.Message, state: FSMContext):
    await state.update_data(time=msg.text)

    # Підказки місця посадки залежать від напрямку
    data_user = await state.get_data()
    direction = data_user["direction"]
    if "Рокитне" in direction and "→ Київ" in direction:
        # Рокитне → Київ
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Біля автостанції")],
                [KeyboardButton(text=CANCEL_TEXT)]
            ],
            resize_keyboard=True
        )
    else:
        # Київ → Рокитне
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Автостанція Південна")],
                [KeyboardButton(text=CANCEL_TEXT)]
            ],
            resize_keyboard=True
        )

    await msg.answer(
        "Оберіть місце посадки або напишіть власний коментар:",
        reply_markup=kb
    )
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
                [KeyboardButton(text=CANCEL_TEXT)]
            ],
            resize_keyboard=True
        )
        await msg.answer("Надішліть свій номер телефону:", reply_markup=kb)
        await state.set_state(BookingStates.waiting_for_phone)
    else:
        await finalize_booking(msg, state, phone)

@dp.message(BookingStates.waiting_for_phone, F.contact)
async def process_contact(msg: types.Message, state: FSMContext):
    data = load_data()
    user_id = str(msg.from_user.id)
    phone = msg.contact.phone_number
    data[user_id]["phone"] = phone
    save_data(data)
    await finalize_booking(msg, state, phone)

async def finalize_booking(msg: types.Message, state: FSMContext, phone: str):
    data = load_data()
    user_id = str(msg.from_user.id)
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

# -------------------- MY BOOKINGS (auto-clean old) --------------------
@dp.message(F.text == "📋 Мої бронювання")
async def my_bookings(msg: types.Message):
    data = load_data()
    user_id = str(msg.from_user.id)
    user_data = data.get(user_id, {})
    bookings = user_data.get("bookings", [])

    now = datetime.now()
    upcoming = []
    for b in bookings:
        try:
            dep = datetime.strptime(f"{b['date']} {b['time']}", "%Y-%m-%d %H:%M")
            if dep > now:
                upcoming.append(b)
        except:
            continue

    data[user_id]["bookings"] = upcoming
    save_data(data)

    if not upcoming:
        await msg.answer("У вас немає активних бронювань.")
        return

    text = "Ваші бронювання:\n\n"
    for b in upcoming:
        text += f"📅 {b['date']} | 🕒 {b['time']} | {b['direction']} | {b['seats']} місць\n📍 {b['comment']}\n\n"
    await msg.answer(text)

# -------------------- ADMIN PANEL --------------------
@dp.message(F.text == "👨‍✈️ Адмін-панель")
async def admin_panel(msg: types.Message, state: FSMContext):
    if msg.from_user.id not in ADMINS:
        await msg.answer("⛔ Ця функція доступна лише водіям.")
        return

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚌 Обрати поїздку")],
            [KeyboardButton(text="🏠 Повернутись в головне меню")]
        ],
        resize_keyboard=True
    )
    await msg.answer("👨‍✈️ Адмін-панель: оберіть дію", reply_markup=kb)

@dp.message(F.text == "🏠 Повернутись в головне меню")
async def admin_back_to_main(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("Повернення в головне меню.", reply_markup=main_menu(True))

@dp.message(F.text == "🚌 Обрати поїздку")
async def admin_choose_trip(msg: types.Message, state: FSMContext):
    if msg.from_user.id not in ADMINS:
        await msg.answer("⛔ Недостатньо прав.")
        return
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚐 Київ → Рокитне")],
            [KeyboardButton(text="🚌 Рокитне → Київ")],
            [KeyboardButton(text="🏠 Повернутись в головне меню")]
        ],
        resize_keyboard=True
    )
    await msg.answer("Оберіть напрямок:", reply_markup=kb)
    await state.set_state(AdminStates.waiting_for_direction)

@dp.message(AdminStates.waiting_for_direction)
async def admin_select_date(msg: types.Message, state: FSMContext):
    direction = msg.text
    await state.update_data(direction=direction)

    today = datetime.now().date()
    dates = [(today - timedelta(days=i)) for i in range(3, 0, -1)] + \
            [(today + timedelta(days=i)) for i in range(0, 8)]
    kb = [[KeyboardButton(text=str(d))] for d in dates]
    kb.append([KeyboardButton(text="🏠 Повернутись в головне меню")])

    await msg.answer("Оберіть дату рейсу:", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))
    await state.set_state(AdminStates.waiting_for_date)

@dp.message(AdminStates.waiting_for_date)
async def admin_select_time(msg: types.Message, state: FSMContext):
    try:
        selected_date = datetime.strptime(msg.text, "%Y-%m-%d").date()
    except:
        await msg.answer("Будь ласка, оберіть дату з кнопок.")
        return

    data_user = await state.get_data()
    direction = data_user["direction"]
    times = base_times_for(direction) if selected_date > datetime.now().date() else get_times(direction, selected_date)

    kb_rows = rows_of([KeyboardButton(text=t) for t in times], 3)
    kb_rows.append([KeyboardButton(text="🏠 Повернутись в головне меню")])

    await state.update_data(date=str(selected_date))
    await msg.answer("Оберіть час:", reply_markup=ReplyKeyboardMarkup(keyboard=kb_rows, resize_keyboard=True))
    await state.set_state(AdminStates.waiting_for_time)

@dp.message(AdminStates.waiting_for_time)
async def admin_show_bookings(msg: types.Message, state: FSMContext):
    selected_time = msg.text
    data_user = await state.get_data()
    direction = data_user["direction"]
    date = data_user["date"]

    data = load_data()
    bookings_list = []
    for _uid, info in data.items():
        for b in info.get("bookings", []):
            if b["direction"] == direction and b["date"] == date and b["time"] == selected_time:
                bookings_list.append(b)

    if not bookings_list:
        await msg.answer("🚫 Немає бронювань на цей рейс.", reply_markup=main_menu(True))
        await state.clear()
        return

    total = sum(int(b["seats"]) for b in bookings_list)
    text = f"📅 {date} | 🕒 {selected_time} | {direction}\n—————————————\n"
    for b in bookings_list:
        text += f"📞 {b['phone']} | {b['seats']} місць | {b['comment']}\n"
    text += f"—————————————\nВсього заброньовано: {total} місць"

    await msg.answer(text, reply_markup=main_menu(True))
    await state.clear()

# -------------------- RUN --------------------
if __name__ == "__main__":
    import asyncio
    asyncio.run(dp.start_polling(bot))
