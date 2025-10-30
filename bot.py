import json
import re
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardMarkup, InlineKeyboardButton,
                           CallbackQuery)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from config import BOT_TOKEN, ADMINS

# ====================== BOOTSTRAP ======================
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

DATA_FILE = "bookings.json"
DRIVERS_FILE = "drivers.json"
ROUTES_FILE = "routes.json"
ADMINS_FILE = "admins.json"
LOCKS_FILE = "locks.json"
CANCEL_TEXT = "❌ Відмінити"


# ====================== UTILS: JSON ======================
def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_data():
    return _load_json(DATA_FILE, {})


def save_data(d):
    _save_json(DATA_FILE, d)


def load_routes():
    return _load_json(ROUTES_FILE, {})


def save_routes(r):
    _save_json(ROUTES_FILE, r)


def load_admins():
    return _load_json(ADMINS_FILE, {"admins": ADMINS})


def save_admins(d):
    _save_json(ADMINS_FILE, d)


def load_locks():
    return _load_json(LOCKS_FILE, {"locked": []})


def save_locks(d):
    _save_json(LOCKS_FILE, d)


# ---- Функції блокування ----
def lock_route(route_key):
    locks = load_locks()
    if route_key not in locks["locked"]:
        locks["locked"].append(route_key)
        save_locks(locks)


def unlock_route(route_key):
    locks = load_locks()
    if route_key in locks["locked"]:
        locks["locked"].remove(route_key)
        save_locks(locks)


def is_route_locked(route_key):
    return route_key in load_locks().get("locked", [])


# ---- Drivers helpers (+ авто-міграція старого формату [ids]) ----
def _normalize_drivers(dr):
    norm, changed = [], False
    for x in dr:
        if isinstance(x, dict):
            norm.append({
                "id": int(x.get("id")),
                "name": (x.get("name") or "Без імені").strip(),
                "phone": (x.get("phone") or "—").strip()
            })
        else:
            norm.append({"id": int(x), "name": "Без імені", "phone": "—"})
            changed = True
    return norm, changed


def load_drivers():
    raw = _load_json(DRIVERS_FILE, {"drivers": []})
    lst, changed = _normalize_drivers(raw.get("drivers", []))
    if changed:
        _save_json(DRIVERS_FILE, {"drivers": lst})
    return {"drivers": lst}


def save_drivers(d):
    lst, _ = _normalize_drivers(d.get("drivers", []))
    _save_json(DRIVERS_FILE, {"drivers": lst})


def drivers_list():
    return load_drivers().get("drivers", [])


def find_driver_by_id(did: int):
    for d in drivers_list():
        if d["id"] == did:
            return d
    return None


def driver_label(d: dict) -> str:
    return f"{d['id']} — {d.get('name','Без імені')} ({d.get('phone','—')})"


# ====================== ROLES & MENUS ======================
def is_admin(uid: int) -> bool:
    admins = load_admins().get("admins", [])
    return uid in admins or uid in ADMINS


def is_driver(uid: int) -> bool:
    return any(x["id"] == uid for x in drivers_list()) or is_admin(uid)


def main_menu(uid: int) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text="🚐 Забронювати місце")],
            [KeyboardButton(text="📋 Мої бронювання")]]
    if is_driver(uid):
        rows.append([KeyboardButton(text="👨‍✈️ Адмін-панель")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def rows_of(items, n=3):
    return [items[i:i + n] for i in range(0, len(items), n)]


# ====================== SCHEDULE HELPERS ======================
def base_times_for(direction: str):
    # Рокитне → Київ — частіше
    if ("Рокитне" in direction) and ("→ Київ" in direction):
        return [
            "05:00", "05:30", "06:00", "07:00", "08:00", "09:00", "10:00",
            "12:00", "13:00", "14:00", "15:00", "16:00", "17:00"
        ]
    # Київ → Рокитне — щогодини
    return [f"{h:02d}:00" for h in range(8, 21)]


def user_dates_7days():
    today = datetime.now().date()
    return [(today + timedelta(days=i)) for i in range(7)]


def driver_dates_minus3_plus7():
    today = datetime.now().date()
    return [(today - timedelta(days=i)) for i in range(3, 0, -1)] + \
           [(today + timedelta(days=i)) for i in range(0, 8)]


def filtered_times_for_user(direction: str, selected_date):
    """Для користувача приховуємо рейси, що стартують менше ніж за 20 хв."""
    now = datetime.now()
    res = []
    for t in base_times_for(direction):
        h, m = map(int, t.split(":"))
        dep = datetime.combine(selected_date, datetime.min.time()) + timedelta(
            hours=h, minutes=m)
        if dep > now + timedelta(minutes=20):
            res.append(t)
    return res


def trip_key(date_str: str, time_str: str, direction: str) -> str:
    return f"{date_str} {time_str} {direction}"


# ====================== STATES ======================
class BookingStates(StatesGroup):
    waiting_for_seats = State()
    waiting_for_date = State()
    waiting_for_direction = State()
    waiting_for_time = State()
    waiting_for_comment = State()
    waiting_for_phone = State()
    driver_wait_phone = State()


class AdminStates(StatesGroup):
    waiting_for_direction = State()
    waiting_for_date = State()
    waiting_for_time = State()


class DriverMgmtStates(StatesGroup):
    waiting_for_action = State()
    waiting_for_new_driver = State()
    waiting_for_remove_driver = State()


class RoutesStates(StatesGroup):
    pick_date = State()
    pick_direction = State()
    pick_time = State()
    pick_driver = State()


class MyRoutesStates(StatesGroup):
    manual_date = State()
    manual_direction = State()
    manual_time = State()


class AdminAdminStates(StatesGroup):
    add_admin_wait = State()
    remove_admin_wait = State()


# ====================== START / CANCEL / HOME ======================
@dp.message(CommandStart())
async def start(msg: types.Message, state: FSMContext):
    await state.clear()
    data = load_data()
    uid = str(msg.from_user.id)
    if uid not in data:
        data[uid] = {"bookings": [], "phone": None}
        save_data(data)
    await msg.answer(
        "👋 Вітаємо у сервісі бронювання маршрутів Київ ↔️ Рокитне!",
        reply_markup=main_menu(msg.from_user.id))


@dp.message(F.text == CANCEL_TEXT)
async def cancel_any(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("Дію скасовано.",
                     reply_markup=main_menu(msg.from_user.id))


@dp.message(F.text == "🏠 Повернутись в головне меню")
async def back_to_main(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("🏠 Головне меню:",
                     reply_markup=main_menu(msg.from_user.id))


# ====================== BOOKING (USER + DRIVER) ======================
@dp.message(F.text == "🚐 Забронювати місце")
async def book_start(msg: types.Message, state: FSMContext):
    await state.update_data(driver_mode=False)
    kb = ReplyKeyboardMarkup(keyboard=[[
        KeyboardButton(text="1"),
        KeyboardButton(text="2"),
        KeyboardButton(text="3")
    ], [KeyboardButton(text=CANCEL_TEXT)]],
                             resize_keyboard=True)
    await msg.answer("Скільки місць хочете забронювати?", reply_markup=kb)
    await state.set_state(BookingStates.waiting_for_seats)


@dp.message(BookingStates.waiting_for_seats)
async def process_seats(msg: types.Message, state: FSMContext):
    seats = msg.text.strip()
    if not seats.isdigit() or int(seats) <= 0:
        await msg.answer("Введіть число місць (1–9).")
        return
    await state.update_data(seats=seats)

    is_driver_mode = (await state.get_data()).get("driver_mode", False)
    dates = driver_dates_minus3_plus7(
    ) if is_driver_mode else user_dates_7days()
    kb = [[KeyboardButton(text=str(d))] for d in dates]
    kb.append([KeyboardButton(text=CANCEL_TEXT)])
    await msg.answer("Оберіть дату поїздки:",
                     reply_markup=ReplyKeyboardMarkup(keyboard=kb,
                                                      resize_keyboard=True))
    await state.set_state(BookingStates.waiting_for_date)


@dp.message(BookingStates.waiting_for_date)
async def process_date(msg: types.Message, state: FSMContext):
    try:
        selected_date = datetime.strptime(msg.text, "%Y-%m-%d").date()
    except:
        await msg.answer("Будь ласка, оберіть дату із кнопок.")
        return

    await state.update_data(date=str(selected_date))
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🚐 Київ → Рокитне")],
                  [KeyboardButton(text="🚌 Рокитне → Київ")],
                  [KeyboardButton(text=CANCEL_TEXT)]],
        resize_keyboard=True)
    await msg.answer("Оберіть напрямок:", reply_markup=kb)
    await state.set_state(BookingStates.waiting_for_direction)


@dp.message(BookingStates.waiting_for_direction)
async def process_direction(msg: types.Message, state: FSMContext):
    direction = msg.text
    ud = await state.get_data()
    selected_date = datetime.strptime(ud["date"], "%Y-%m-%d").date()

    times = base_times_for(direction) if ud.get(
        "driver_mode") else filtered_times_for_user(direction, selected_date)
    if not times:
        await msg.answer("На обрану дату немає доступних рейсів.",
                         reply_markup=main_menu(msg.from_user.id))
        await state.clear()
        return

    kb_rows = rows_of([
        KeyboardButton(
            text=
            f"{'✅' if not is_route_locked(trip_key(str(selected_date), t, direction)) else '❌'} {t}"
        ) for t in times
    ], 3)

    kb_rows.append([KeyboardButton(text=CANCEL_TEXT)])
    await state.update_data(direction=direction)
    await msg.answer("Оберіть час:",
                     reply_markup=ReplyKeyboardMarkup(keyboard=kb_rows,
                                                      resize_keyboard=True))
    await state.set_state(BookingStates.waiting_for_time)


@dp.message(BookingStates.waiting_for_time)
async def process_time(msg: types.Message, state: FSMContext):
    ud = await state.get_data()
    # 🔥 перевірка, чи рейс заблокований
    if is_route_locked(trip_key(ud["date"], msg.text.strip(),
                                ud["direction"])):
        await msg.answer(
            "🚫 Нажаль, на цей рейс місць немає. Уточніть у водія.",
            reply_markup=main_menu(msg.from_user.id))
        await state.clear()
        return
    await state.update_data(time=msg.text)
    direction = (await state.get_data())["direction"]
    if "Рокитне" in direction and "→ Київ" in direction:
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Біля автостанції")],
                      [KeyboardButton(text=CANCEL_TEXT)]],
            resize_keyboard=True)
    else:
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Автостанція Південна")],
                      [KeyboardButton(text=CANCEL_TEXT)]],
            resize_keyboard=True)
    await msg.answer("Оберіть місце посадки або напишіть власний коментар:",
                     reply_markup=kb)
    await state.set_state(BookingStates.waiting_for_comment)


@dp.message(BookingStates.waiting_for_comment)
async def process_comment(msg: types.Message, state: FSMContext):
    await state.update_data(comment=msg.text)
    ud = await state.get_data()

    if ud.get("driver_mode"):
        await msg.answer(
            "Введіть номер телефону пасажира (+380XXXXXXXXX) або інший опис.")
        await state.set_state(BookingStates.driver_wait_phone)
        return

    data = load_data()
    uid = str(msg.from_user.id)
    phone = data.get(uid, {}).get("phone")
    if not phone:
        kb = ReplyKeyboardMarkup(keyboard=[[
            KeyboardButton(text="📱 Надіслати свій номер", request_contact=True)
        ], [KeyboardButton(text=CANCEL_TEXT)]],
                                 resize_keyboard=True)
        await msg.answer("Надішліть свій номер телефону:", reply_markup=kb)
        await state.set_state(BookingStates.waiting_for_phone)
    else:
        await finalize_booking(msg, state, phone, created_by_driver=False)


@dp.message(BookingStates.waiting_for_phone, F.contact)
async def process_contact(msg: types.Message, state: FSMContext):
    data = load_data()
    uid = str(msg.from_user.id)
    phone = msg.contact.phone_number
    if uid not in data:
        data[uid] = {"bookings": [], "phone": None}
    data[uid]["phone"] = phone
    save_data(data)
    await finalize_booking(msg, state, phone, created_by_driver=False)


@dp.message(BookingStates.driver_wait_phone)
async def process_driver_phone(msg: types.Message, state: FSMContext):
    phone = msg.text.strip()
    await finalize_booking(msg, state, phone, created_by_driver=True)


async def finalize_booking(msg: types.Message, state: FSMContext, phone: str,
                           created_by_driver: bool):
    data = load_data()
    uid = str(msg.from_user.id)
    ud = await state.get_data()

    comment = ud["comment"]
    if created_by_driver:
        comment = f"{comment} (створено водієм)"

    booking = {
        "date": ud["date"],
        "time": ud["time"],
        "direction": ud["direction"],
        "seats": ud["seats"],
        "comment": comment,
        "phone": phone,
        "created_by_driver": created_by_driver,
        "driver_id": msg.from_user.id if created_by_driver else None,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    if uid not in data:
        data[uid] = {
            "bookings": [],
            "phone": None if created_by_driver else phone
        }
    data[uid]["bookings"].append(booking)
    save_data(data)

    await state.clear()
    await msg.answer("✅ Бронювання підтверджено!",
                     reply_markup=main_menu(msg.from_user.id))


# ====================== МОЇ БРОНЮВАННЯ ======================
def clean_and_get_upcoming(user_id: str):
    data = load_data()
    user = data.get(user_id, {"bookings": [], "phone": None})
    now = datetime.now()
    upcoming = []
    for b in user.get("bookings", []):
        try:
            dep = datetime.strptime(f"{b['date']} {b['time']}",
                                    "%Y-%m-%d %H:%M")
            if dep > now:
                upcoming.append(b)
        except:
            continue
    data[user_id]["bookings"] = upcoming
    save_data(data)
    return upcoming


@dp.message(F.text == "📋 Мої бронювання")
async def my_bookings(msg: types.Message):
    uid = str(msg.from_user.id)
    upcoming = clean_and_get_upcoming(uid)
    if not upcoming:
        await msg.answer("У вас немає активних бронювань.")
        return
    for b in upcoming:
        text = (
            f"📅 {b['date']} | 🕒 {b['time']} | {b['direction']} | {b['seats']} місць\n"
            f"📍 {b['comment']}\n"
            f"🕒 Створено: {b.get('created_at','?')}")
        cb = f"cancel:{b['date']}|{b['time']}|{b['direction']}"
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Скасувати", callback_data=cb)
        ]])
        await msg.answer(text, reply_markup=kb)


@dp.callback_query(F.data.startswith("cancel:"))
async def cancel_booking_cb(call: CallbackQuery):
    _, payload = call.data.split(":", 1)
    date_str, time_str, direction = payload.split("|", 2)
    uid = str(call.from_user.id)
    data = load_data()
    user = data.get(uid, {"bookings": []})
    before = len(user["bookings"])
    user["bookings"] = [
        b for b in user["bookings"]
        if not (b["date"] == date_str and b["time"] == time_str
                and b["direction"] == direction)
    ]
    data[uid] = user
    save_data(data)
    if len(user["bookings"]) < before:
        await call.message.edit_text("✅ Бронювання скасовано.")
    else:
        await call.answer("Бронювання не знайдено.", show_alert=True)


# ====================== ADMIN / DRIVER PANEL ======================
@dp.message(F.text == "👨‍✈️ Адмін-панель")
async def admin_panel(msg: types.Message, state: FSMContext):
    uid = msg.from_user.id
    if not is_driver(uid):
        await msg.answer("⛔ Доступ лише для водіїв/адміністраторів.")
        return

    rows = [[KeyboardButton(text="🚌 Обрати поїздку")],
            [KeyboardButton(text="🚐 Додати бронювання вручну")],
            [KeyboardButton(text="📋 Мої рейси")],
            [KeyboardButton(text="🕒 Переглянути рейс вручну")]]
    if is_admin(uid):
        rows.insert(3, [KeyboardButton(text="📅 Керування рейсами")])
        rows.insert(4, [KeyboardButton(text="👨‍✈️ Керування водіями")])
    rows.append([KeyboardButton(text="🏠 Повернутись в головне меню")])

    await msg.answer("👨‍✈️ Адмін-панель: оберіть дію",
                     reply_markup=ReplyKeyboardMarkup(keyboard=rows,
                                                      resize_keyboard=True))


# ---- Перегляд рейсу (всі бронювання по рейсу) ----
@dp.message(F.text == "🚌 Обрати поїздку")
async def picker_direction(msg: types.Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🚐 Київ → Рокитне")],
                  [KeyboardButton(text="🚌 Рокитне → Київ")],
                  [KeyboardButton(text="🏠 Повернутись в головне меню")]],
        resize_keyboard=True)
    await msg.answer("Оберіть напрямок:", reply_markup=kb)
    await state.set_state(AdminStates.waiting_for_direction)


@dp.message(AdminStates.waiting_for_direction)
async def picker_date(msg: types.Message, state: FSMContext):
    direction = msg.text
    await state.update_data(direction=direction)
    dates = driver_dates_minus3_plus7()
    kb = [[KeyboardButton(text=str(d))] for d in dates]
    kb.append([KeyboardButton(text="🏠 Повернутись в головне меню")])
    await msg.answer("Оберіть дату рейсу:",
                     reply_markup=ReplyKeyboardMarkup(keyboard=kb,
                                                      resize_keyboard=True))
    await state.set_state(AdminStates.waiting_for_date)


@dp.message(AdminStates.waiting_for_date)
async def picker_time(msg: types.Message, state: FSMContext):
    try:
        selected_date = datetime.strptime(msg.text, "%Y-%m-%d").date()
    except:
        await msg.answer("Оберіть дату із кнопок.")
        return
    ud = await state.get_data()
    times = base_times_for(ud["direction"])
    kb = rows_of([KeyboardButton(text=t) for t in times], 3)
    kb.append([KeyboardButton(text="🏠 Повернутись в головне меню")])
    await state.update_data(date=str(selected_date))
    await msg.answer("Оберіть час:",
                     reply_markup=ReplyKeyboardMarkup(keyboard=kb,
                                                      resize_keyboard=True))
    await state.set_state(AdminStates.waiting_for_time)


@dp.message(AdminStates.waiting_for_time)
async def show_trip_bookings(msg: types.Message, state: FSMContext):
    ud = await state.get_data()
    direction, date_str, time_str = ud["direction"], ud["date"], msg.text

    data = load_data()
    bookings_list = []
    for _uid, info in data.items():
        for b in info.get("bookings", []):
            if b["direction"] == direction and b["date"] == date_str and b[
                    "time"] == time_str:
                bookings_list.append(b)

    if not bookings_list:
        await msg.answer("🚫 Немає бронювань на цей рейс.",
                         reply_markup=main_menu(msg.from_user.id))
        await state.clear()
        return

    bookings_list.sort(key=lambda x: x.get("created_at", ""))
    total = sum(int(b["seats"]) for b in bookings_list)
    text = f"📅 {date_str} | 🕒 {time_str} | {direction}\n—————————————\n"
    for b in bookings_list:
        mark = " (водій)" if b.get("created_by_driver") else ""
        text += f"🕒 {b.get('created_at','?')} | 📞 {b['phone']} | {b['seats']} місць | {b['comment']}{mark}\n"
    text += f"—————————————\nВсього заброньовано: {total} місць"

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="📋 Повний список бронювань",
            callback_data=f"list:{date_str}|{time_str}|{direction}")
    ]])
    await msg.answer(text, reply_markup=kb)
    await state.clear()


@dp.callback_query(F.data.startswith("list:"))
async def show_detailed_list(call: CallbackQuery):
    _, payload = call.data.split(":", 1)
    date_str, time_str, direction = payload.split("|", 2)
    data = load_data()
    bookings = []
    for _uid, info in data.items():
        for b in info.get("bookings", []):
            if b["date"] == date_str and b["time"] == time_str and b[
                    "direction"] == direction:
                bookings.append(b)
    if not bookings:
        await call.answer("Немає бронювань.")
        return
    bookings.sort(key=lambda x: x.get("created_at", ""))
    text = f"📅 {date_str} | 🕒 {time_str} | {direction}\n\n"
    for b in bookings:
        text += f"🕒 {b.get('created_at','?')} | 📞 {b['phone']} | {b['seats']} місць | {b['comment']}\n"
    await call.message.answer(text)


# ---- Ручне бронювання водієм ----
@dp.message(F.text == "🚐 Додати бронювання вручну")
async def driver_manual_booking(msg: types.Message, state: FSMContext):
    if not is_driver(msg.from_user.id):
        await msg.answer("⛔ Доступ лише для водіїв.")
        return
    await state.update_data(driver_mode=True)
    kb = ReplyKeyboardMarkup(keyboard=[[
        KeyboardButton(text="1"),
        KeyboardButton(text="2"),
        KeyboardButton(text="3")
    ], [KeyboardButton(text=CANCEL_TEXT)]],
                             resize_keyboard=True)
    await msg.answer("Скільки місць для клієнта?", reply_markup=kb)
    await state.set_state(BookingStates.waiting_for_seats)


# ---- Керування водіями (лише адміни) ----
@dp.message(F.text == "👨‍✈️ Керування водіями")
async def manage_drivers_menu(msg: types.Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Доступ лише для адміністраторів.")
        return
    lst = drivers_list()
    text = "👨‍✈️ <b>Поточні водії:</b>\n" + ("\n".join(
        [f"• {driver_label(x)}" for x in lst]) if lst else "Немає")
    kb = ReplyKeyboardMarkup(keyboard=[[
        KeyboardButton(text="➕ Додати водія"),
        KeyboardButton(text="➖ Видалити водія")
    ], [KeyboardButton(text="🏠 Повернутись в головне меню")]],
                             resize_keyboard=True)
    await msg.answer(text, parse_mode="HTML", reply_markup=kb)
    await state.set_state(DriverMgmtStates.waiting_for_action)


@dp.message(DriverMgmtStates.waiting_for_action, F.text == "➕ Додати водія")
async def add_driver_start(msg: types.Message, state: FSMContext):
    await msg.answer(
        "Надішліть у форматі:\n<id> <Ім’я (може бути з пробілами)> <Телефон>\n\nПриклад:\n123456789 Іван Петров +380501112233\nАБО перешліть повідомлення користувача, щоб підставити ID."
    )
    await state.set_state(DriverMgmtStates.waiting_for_new_driver)


@dp.message(DriverMgmtStates.waiting_for_new_driver, F.forward_from)
async def add_driver_by_forward(msg: types.Message, state: FSMContext):
    new_id = msg.forward_from.id
    d = {"drivers": drivers_list()}
    if any(x["id"] == new_id for x in d["drivers"]):
        await msg.answer("Цей користувач уже є водієм.")
    else:
        d["drivers"].append({"id": new_id, "name": "Без імені", "phone": "—"})
        save_drivers(d)
        await msg.answer(f"✅ Додано водія: {new_id}")
    await state.clear()
    await msg.answer("Готово.", reply_markup=main_menu(msg.from_user.id))


@dp.message(DriverMgmtStates.waiting_for_new_driver)
async def add_driver_by_text(msg: types.Message, state: FSMContext):
    parts = msg.text.strip().split()
    try:
        new_id = int(parts[0])
    except:
        await msg.answer("❌ Перше значення має бути числовим ID.")
        return
    phone = "—"
    if len(parts) >= 2:
        last = parts[-1]
        if last.startswith("+") or last.replace("+", "").replace("-",
                                                                 "").isdigit():
            phone = last
            name_tokens = parts[1:-1]
        else:
            name_tokens = parts[1:]
    else:
        name_tokens = []
    name = " ".join(name_tokens).strip() or "Без імені"

    d = {"drivers": drivers_list()}
    if any(x["id"] == new_id for x in d["drivers"]):
        await msg.answer("Цей користувач уже є водієм.")
    else:
        d["drivers"].append({"id": new_id, "name": name, "phone": phone})
        save_drivers(d)
        await msg.answer(f"✅ Додано водія:\nID: {new_id}\n👤 {name}\n📞 {phone}")
    await state.clear()
    await msg.answer("Готово.", reply_markup=main_menu(msg.from_user.id))


@dp.message(DriverMgmtStates.waiting_for_action, F.text == "➖ Видалити водія")
async def remove_driver_start(msg: types.Message, state: FSMContext):
    await msg.answer("Введіть ID водія, якого потрібно видалити:")
    await state.set_state(DriverMgmtStates.waiting_for_remove_driver)


@dp.message(DriverMgmtStates.waiting_for_remove_driver)
async def remove_driver(msg: types.Message, state: FSMContext):
    try:
        rid = int(msg.text.strip())
    except:
        await msg.answer("❌ Введіть числовий ID.")
        return
    d = {"drivers": [x for x in drivers_list() if x["id"] != rid]}
    save_drivers(d)
    await msg.answer(f"🗑 Якщо водій існував — видалено ID {rid}.")
    await state.clear()
    await msg.answer("Готово.", reply_markup=main_menu(msg.from_user.id))


# ---- Керування адміністраторами ----
@dp.message(F.text == "🛠 Керування адміністраторами")
async def manage_admins_menu(msg: types.Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Доступ лише для адміністраторів.")
        return
    a = load_admins()
    lst = a.get("admins", [])
    text = "👑 <b>Поточні адміністратори:</b>\n" + ("\n".join(
        [f"• {x}" for x in lst]) if lst else "Немає")
    kb = ReplyKeyboardMarkup(keyboard=[[
        KeyboardButton(text="➕ Додати адміністратора"),
        KeyboardButton(text="➖ Видалити адміністратора")
    ], [KeyboardButton(text="🏠 Повернутись в головне меню")]],
                             resize_keyboard=True)
    await msg.answer(text, parse_mode="HTML", reply_markup=kb)
    await state.set_state("admin_mgmt_wait")


@dp.message(F.text == "➕ Додати адміністратора")
async def add_admin_prompt(msg: types.Message, state: FSMContext):
    await msg.answer(
        "Надішліть ID користувача (числом) або перешліть його повідомлення.")
    await state.set_state(AdminAdminStates.add_admin_wait)

    # ---- Додавання адміністратора ----
    @dp.message(F.forward_from, AdminAdminStates.add_admin_wait)
    async def add_admin_by_forward(msg: types.Message, state: FSMContext):
        new_id = msg.forward_from.id
        a = load_admins()
        if new_id not in a["admins"]:
            a["admins"].append(new_id)
            save_admins(a)
            await msg.answer(f"✅ Додано адміністратора: {new_id}")
        else:
            await msg.answer("❗ Цей користувач уже адміністратор.")
        await state.clear()
        await msg.answer("Готово ✅", reply_markup=main_menu(msg.from_user.id))

    @dp.message(AdminAdminStates.add_admin_wait)
    async def add_admin_by_id(msg: types.Message, state: FSMContext):
        try:
            new_id = int(msg.text.strip())
            a = load_admins()
            if new_id not in a["admins"]:
                a["admins"].append(new_id)
                save_admins(a)
                await msg.answer(f"✅ Додано адміністратора: {new_id}")
            else:
                await msg.answer("Цей користувач уже адміністратор.")
        except ValueError:
            await msg.answer("❌ Введіть числовий ID.")
        await state.clear()
        await msg.answer("Готово ✅", reply_markup=main_menu(msg.from_user.id))


@dp.message(F.text == "➖ Видалити адміністратора")
async def remove_admin_prompt(msg: types.Message, state: FSMContext):
    await msg.answer("Введіть ID адміністратора, якого потрібно видалити:")
    await state.set_state(AdminAdminStates.remove_admin_wait)


@dp.message(AdminAdminStates.remove_admin_wait)
async def remove_admin(msg: types.Message, state: FSMContext):
    try:
        rid = int(msg.text.strip())
        a = load_admins()
        if rid in a["admins"]:
            a["admins"].remove(rid)
            save_admins(a)
            await msg.answer(f"🗑 Видалено адміністратора: {rid}")
        else:
            await msg.answer("Такого адміністратора немає.")
    except:
        await msg.answer("❌ Невірний формат ID.")
    await state.clear()


# ---- Керування рейсами (призначення водіїв) ----
@dp.message(F.text == "📅 Керування рейсами")
async def routes_manage_entry(msg: types.Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Доступ лише для адміністраторів.")
        return
    dates = driver_dates_minus3_plus7()
    kb = [[KeyboardButton(text=str(d))] for d in dates]
    kb.append([KeyboardButton(text="🏠 Повернутись в головне меню")])
    await msg.answer("Оберіть дату рейсу:",
                     reply_markup=ReplyKeyboardMarkup(keyboard=kb,
                                                      resize_keyboard=True))
    await state.set_state(RoutesStates.pick_date)


@dp.message(RoutesStates.pick_date)
async def routes_pick_direction(msg: types.Message, state: FSMContext):
    try:
        sel_date = datetime.strptime(msg.text, "%Y-%m-%d").date()
    except:
        await msg.answer("Оберіть дату з кнопок.")
        return
    await state.update_data(date=str(sel_date))
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🚐 Київ → Рокитне")],
                  [KeyboardButton(text="🚌 Рокитне → Київ")],
                  [KeyboardButton(text="🏠 Повернутись в головне меню")]],
        resize_keyboard=True)
    await msg.answer("Оберіть напрямок:", reply_markup=kb)
    await state.set_state(RoutesStates.pick_direction)


@dp.message(RoutesStates.pick_direction)
async def routes_pick_time(msg: types.Message, state: FSMContext):
    direction = msg.text
    await state.update_data(direction=direction)
    times = base_times_for(direction)
    kb = rows_of([KeyboardButton(text=t) for t in times], 3)
    kb.append([KeyboardButton(text="🏠 Повернутись в головне меню")])
    await msg.answer("Оберіть час:",
                     reply_markup=ReplyKeyboardMarkup(keyboard=kb,
                                                      resize_keyboard=True))
    await state.set_state(RoutesStates.pick_time)


@dp.message(RoutesStates.pick_time)
async def routes_pick_driver(msg: types.Message, state: FSMContext):
    time_str = msg.text
    await state.update_data(time=time_str)

    lst = drivers_list()
    if not lst:
        await msg.answer(
            "Немає доданих водіїв. Додайте у «👨‍✈️ Керування водіями».")
        await state.clear()
        return

    # показуємо "id — Ім'я (телефон)"; парсимо перше число
    labels = [driver_label(d) for d in lst]
    kb = rows_of([KeyboardButton(text=lbl) for lbl in labels], 1)
    kb.append([KeyboardButton(text="🏠 Повернутись в головне меню")])
    await msg.answer("Вкажіть водія (натисніть кнопку):",
                     reply_markup=ReplyKeyboardMarkup(keyboard=kb,
                                                      resize_keyboard=True))
    await state.set_state(RoutesStates.pick_driver)


@dp.message(RoutesStates.pick_driver)
async def routes_assign_driver(msg: types.Message, state: FSMContext):
    m = re.match(r"^\s*(\d+)", msg.text.strip())
    if not m:
        await msg.answer("Введіть або виберіть кнопку із ID водія.")
        return
    driver_id = int(m.group(1))
    if not find_driver_by_id(driver_id):
        await msg.answer("Це не ID водія зі списку.")
        return

    ud = await state.get_data()
    date_str, time_str, direction = ud["date"], ud["time"], ud["direction"]

    routes = load_routes()
    key = trip_key(date_str, time_str, direction)
    routes[key] = {
        "driver_id": driver_id,
        "date": date_str,
        "time": time_str,
        "direction": direction
    }
    save_routes(routes)

    await state.clear()
    drv = find_driver_by_id(driver_id)
    await msg.answer(
        f"✅ Водія призначено:\n"
        f"👤 {drv['name']} ({drv['phone']})\n"
        f"📅 {date_str} | 🕒 {time_str} | {direction}",
        reply_markup=main_menu(msg.from_user.id))


# ---- Блокування рейсів (водій або адмін) ----
@dp.message(F.text == "🚫 Заблокувати рейс")
async def lock_trip(msg: types.Message):
    if not is_driver(msg.from_user.id) and not is_admin(msg.from_user.id):
        await msg.answer("⛔ У вас немає прав блокувати рейси.")
        return
    await msg.answer("Введіть ключ рейсу у форматі: YYYY-MM-DD HH:MM Напрямок")
    await dp.storage.set_state(msg.from_user.id, "lock_route_wait")


@dp.message(state="lock_route_wait")
async def do_lock_trip(msg: types.Message, state: FSMContext):
    route_key = msg.text.strip()
    lock_route(route_key)
    await msg.answer(f"🔒 Рейс {route_key} заблоковано для бронювання.")
    await state.clear()


@dp.message(F.text == "✅ Розблокувати рейс")
async def unlock_trip(msg: types.Message):
    if not is_driver(msg.from_user.id) and not is_admin(msg.from_user.id):
        await msg.answer("⛔ У вас немає прав.")
        return
    await msg.answer("Введіть ключ рейсу для розблокування:")
    await dp.storage.set_state(msg.from_user.id, "unlock_route_wait")


@dp.message(state="unlock_route_wait")
async def do_unlock_trip(msg: types.Message, state: FSMContext):
    route_key = msg.text.strip()
    unlock_route(route_key)
    await msg.answer(f"🔓 Рейс {route_key} розблоковано.")
    await state.clear()


# ---- Мої рейси (водій) ----
@dp.message(F.text == "📋 Мої рейси")
async def my_routes(msg: types.Message, state: FSMContext):
    if not is_driver(msg.from_user.id):
        await msg.answer("⛔ Доступ лише для водіїв.")
        return
    routes = load_routes()
    my = []
    today = datetime.now().date()
    for _, r in routes.items():
        if r.get("driver_id") == msg.from_user.id:
            d = datetime.strptime(r["date"], "%Y-%m-%d").date()
            if (today - timedelta(days=1)) <= d <= (today + timedelta(days=7)):
                my.append(r)
    if not my:
        await msg.answer("Немає призначених рейсів у найближчі дні.")
        return

    my.sort(key=lambda x: (x["date"], x["time"]))
    text = "📋 Ваші рейси:\n\n" + "\n".join(
        [f"• {r['date']} | {r['time']} | {r['direction']}" for r in my])
    await msg.answer(text)


# ---- Переглянути рейс вручну (водій) ----
@dp.message(F.text == "🕒 Переглянути рейс вручну")
async def driver_manual_view_date(msg: types.Message, state: FSMContext):
    if not is_driver(msg.from_user.id):
        await msg.answer("⛔ Доступ лише для водіїв.")
        return
    dates = driver_dates_minus3_plus7()
    kb = [[KeyboardButton(text=str(d))] for d in dates]
    kb.append([KeyboardButton(text="🏠 Повернутись в головне меню")])
    await msg.answer("Оберіть дату:",
                     reply_markup=ReplyKeyboardMarkup(keyboard=kb,
                                                      resize_keyboard=True))
    await state.set_state(MyRoutesStates.manual_date)


@dp.message(MyRoutesStates.manual_date)
async def driver_manual_view_direction(msg: types.Message, state: FSMContext):
    try:
        sel = datetime.strptime(msg.text, "%Y-%m-%d").date()
    except:
        await msg.answer("Оберіть дату із кнопок.")
        return
    await state.update_data(date=str(sel))
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🚐 Київ → Рокитне")],
                  [KeyboardButton(text="🚌 Рокитне → Київ")],
                  [KeyboardButton(text="🏠 Повернутись в головне меню")]],
        resize_keyboard=True)
    await msg.answer("Оберіть напрямок:", reply_markup=kb)
    await state.set_state(MyRoutesStates.manual_direction)


@dp.message(MyRoutesStates.manual_direction)
async def driver_manual_view_time(msg: types.Message, state: FSMContext):
    direction = msg.text
    await state.update_data(direction=direction)
    times = base_times_for(direction)
    kb = rows_of([KeyboardButton(text=t) for t in times], 3)
    kb.append([KeyboardButton(text="🏠 Повернутись в головне меню")])
    await msg.answer("Оберіть час:",
                     reply_markup=ReplyKeyboardMarkup(keyboard=kb,
                                                      resize_keyboard=True))
    await state.set_state(MyRoutesStates.manual_time)


@dp.message(MyRoutesStates.manual_time)
async def driver_manual_view_show(msg: types.Message, state: FSMContext):
    ud = await state.get_data()
    date_str, direction, time_str = ud["date"], ud["direction"], msg.text

    data = load_data()
    bookings = []
    for _uid, info in data.items():
        for b in info.get("bookings", []):
            if b["date"] == date_str and b["time"] == time_str and b[
                    "direction"] == direction:
                bookings.append(b)

    if not bookings:
        await msg.answer("🚫 Немає бронювань на цей рейс.",
                         reply_markup=main_menu(msg.from_user.id))
        await state.clear()
        return

    bookings.sort(key=lambda x: x.get("created_at", ""))
    total = sum(int(b["seats"]) for b in bookings)
    text = f"📅 {date_str} | 🕒 {time_str} | {direction}\n—————————————\n"
    for b in bookings:
        mark = " (водій)" if b.get("created_by_driver") else ""
        text += f"🕒 {b.get('created_at','?')} | 📞 {b['phone']} | {b['seats']} місць | {b['comment']}{mark}\n"
    text += f"—————————————\nВсього заброньовано: {total} місць"
    await msg.answer(text, reply_markup=main_menu(msg.from_user.id))
    await state.clear()


# ====================== RUN ======================
if __name__ == "__main__":
    import asyncio
    asyncio.run(dp.start_polling(bot))
