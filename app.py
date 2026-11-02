import asyncio
import os
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import database as db

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

STATUS_EMOJI = {
    "На парковке": "🅿️",
    "В работе": "🔧",
    "Ждёт запчасти": "📦",
    "Машина готова": "✅",
}

# ============================================================
# СОСТОЯНИЯ
# ============================================================

class AddCar(StatesGroup):
    custom_park = State()
    model = State()
    plate = State()
    works_menu = State()
    works_custom = State()

class SearchCar(StatesGroup):
    query = State()

class WaitingParts(StatesGroup):
    names = State()

class EditNote(StatesGroup):
    text = State()

class AddNorm(StatesGroup):
    name = State()
    hours = State()

class CarWorks(StatesGroup):
    custom = State()

class CarParts(StatesGroup):
    new = State()

class SetDigest(StatesGroup):
    hour = State()

# ============================================================
# КЛАВИАТУРЫ
# ============================================================

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить машину")],
        [KeyboardButton(text="📊 Сводка"), KeyboardButton(text="🚗 Машины")],
        [KeyboardButton(text="🔍 Найти"), KeyboardButton(text="⚙️ Нормо-часы")],
    ],
    resize_keyboard=True
)

cancel_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Отмена")]],
    resize_keyboard=True
)

# ============================================================
# ГЛОБАЛЬНАЯ ОТМЕНА
# ============================================================

@dp.message(F.text == "❌ Отмена")
async def global_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено. Возврат в меню.", reply_markup=main_kb)

# ============================================================
# ФОРМАТИРОВАНИЕ
# ============================================================

def is_attention_car(car) -> bool:
    car_id, model, plate, works, hours, status, since, note, urgent = car
    if status == "Машина готова":
        return False
    days = db.days_in_status(since)
    if urgent:
        return True
    if days >= 5:
        return True
    if hours and hours <= 2:
        return True
    return False


def is_urgent_only(car) -> bool:
    car_id, model, plate, works, hours, status, since, note, urgent = car
    return bool(urgent)


async def car_line_summary(car):
    car_id, model, plate, works, hours, status, since, note, urgent = car
    days = db.days_in_status(since)
    circle = db.days_circle(days)
    fire = "🔥 " if urgent else ""
    emoji = STATUS_EMOJI.get(status, "•")

    line = f"{fire}{circle} {emoji} <b>{model}</b> ({plate}) — {status}"
    if status != "Машина готова":
        line += f" · {days} дн."
    if status == "Ждёт запчасти":
        parts = await db.get_parts_waiting(car_id)
        pending = [p[1] for p in parts if not p[2]]
        if pending:
            line += f"\n    📦 {', '.join(pending)}"
    elif works and status != "Машина готова":
        line += f"\n    🔧 {works}"
        if hours:
            line += f" (~{hours:.1f} ч)"
    if note and not note.startswith("Ждём:") and status != "Машина готова":
        line += f"\n    📝 {note}"
    return line


def quick_status_row(car_id):
    return [
        InlineKeyboardButton(text="🅿️", callback_data=f"s_parking_{car_id}"),
        InlineKeyboardButton(text="🔧", callback_data=f"s_repair_{car_id}"),
        InlineKeyboardButton(text="📦", callback_data=f"s_wait_{car_id}"),
        InlineKeyboardButton(text="✅", callback_data=f"s_done_{car_id}"),
    ]


async def render_summary(send_to, edit_msg=None, urgent_only=False):
    stats = await db.get_stats_by_park()
    total = sum(len(p["cars"]) for p in stats)
    if total == 0:
        text = "📊 Пока нет машин. Нажми «➕ Добавить машину»."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_summary")]
        ])
        if edit_msg:
            try:
                await edit_msg.edit_text(text, reply_markup=kb)
                return
            except Exception:
                pass
        await send_to.answer(text, reply_markup=kb)
        return

    in_work = sum(1 for p in stats for c in p["cars"] if c[5] == "В работе")
    waiting = sum(1 for p in stats for c in p["cars"] if c[5] == "Ждёт запчасти")
    ready = sum(1 for p in stats for c in p["cars"] if c[5] == "Машина готова")
    urgent_count = sum(1 for p in stats for c in p["cars"] if c[8])

    header = "📊 <b>Сводка</b>"
    if urgent_only:
        header = "🔥 <b>Срочные машины</b>"
    header += f" · всего {total}"
    if in_work or waiting or ready:
        header += f" (в работе {in_work}"
        if waiting:
            header += f", ждут {waiting}"
        if ready:
            header += f", готовы {ready}"
        header += ")"
    text = header + "\n"

    rows = []
    shown = 0
    for p in stats:
        cars = p["cars"]
        if urgent_only:
            cars = [c for c in cars if c[8]]
        if not cars:
            continue
        text += f"\n🏢 <b>{p['park_name']}</b>\n"
        for car in cars:
            text += await car_line_summary(car) + "\n"
            rows.append(quick_status_row(car[0]))
            shown += 1

    if urgent_only and shown == 0:
        text += "\nНет срочных машин."
    elif not urgent_only:
        # кнопки фильтра
        filter_row = []
        if urgent_count:
            filter_row.append(InlineKeyboardButton(
                text=f"🔥 Только срочные ({urgent_count})", callback_data="filter_urgent"
            ))
        else:
            filter_row.append(InlineKeyboardButton(
                text="🔥 Только срочные", callback_data="filter_urgent"
            ))
        rows.append(filter_row)
    else:
        rows.append([InlineKeyboardButton(text="📊 Все машины", callback_data="filter_all")])
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_summary")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    if edit_msg:
        try:
            await edit_msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass
    await send_to.answer(text, reply_markup=kb, parse_mode="HTML")


# ============================================================
# СТАРТ / HELP / СВОДКА / ПОИСК
# ============================================================

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await db.add_subscriber(message.chat.id)
    await message.answer(
        "👋 Бот учёта машин таксопарков.\n\n"
        "• «📊 Сводка» — все машины, статус меняется кнопками под машиной\n"
        "• «🚗 Машины» — редактировать (работы, запчасти, заметка)\n"
        "• «🔍 Найти» — по номеру, модели или парку\n"
        "• «⚙️ Нормо-часы» — справочник\n"
        "• В 10:00 — утренняя сводка: только требующие внимания\n\n"
        "Подписка на утреннюю сводку включена.",
        reply_markup=main_kb
    )


@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    await message.answer(
        "📖 <b>Как пользоваться</b>\n\n"
        "📊 Сводка — все машины. Под каждой строкой 4 кнопки:\n"
        "  🅿️ на парковке · 🔧 в работе · 📦 ждёт запчасти · ✅ готова\n"
        "🚗 Машины — парк → машина → карточка (работы, запчасти, заметка, удаление)\n"
        "🔍 Найти — по номеру, модели или парку\n"
        "⚙️ Нормо-часы — справочник работ\n"
        "/set_digest 9 — изменить час утренней сводки\n\n"
        "Круги в сводке: 🟢 до 2 дней · 🟡 3–4 · 🔴 5+\n"
        "🔥 — срочная машина.",
        reply_markup=main_kb
    )


@dp.message(Command("set_digest"))
async def cmd_set_digest(message: Message, state: FSMContext):
    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit() or not (0 <= int(args[1]) <= 23):
        await message.answer("Использование: /set_digest 9 (час от 0 до 23)")
        return
    hour = int(args[1])
    await db.set_setting("digest_hour", str(hour))
    _reschedule_digest(hour)
    await message.answer(f"✅ Утренняя сводка будет приходить в {hour:02d}:00.")


@dp.message(F.text == "📊 Сводка")
async def show_summary(message: Message, state: FSMContext):
    await state.clear()
    await render_summary(message)


@dp.callback_query(F.data == "filter_urgent")
async def filter_urgent(callback: CallbackQuery):
    await render_summary(callback.message, edit_msg=callback.message, urgent_only=True)
    await callback.answer()


@dp.callback_query(F.data == "filter_all")
async def filter_all(callback: CallbackQuery):
    await render_summary(callback.message, edit_msg=callback.message, urgent_only=False)
    await callback.answer()


@dp.callback_query(F.data == "refresh_summary")
async def refresh_summary(callback: CallbackQuery):
    await render_summary(callback.message, edit_msg=callback.message)
    await callback.answer("Обновлено")


@dp.message(F.text == "🔍 Найти")
async def find_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Введи номер, модель или название парка:",
        reply_markup=cancel_kb
    )
    await state.set_state(SearchCar.query)


@dp.message(SearchCar.query)
async def search_do(message: Message, state: FSMContext):
    q = message.text.strip()
    await state.clear()
    cars = await db.find_car_by_plate(q)
    # если по номеру ничего — ищем по модели или парку
    if not cars:
        all_cars = await db.get_all_cars()
        ql = q.lower()
        cars = [c for c in all_cars if ql in c[1].lower() or ql in c[2].lower()]
    if not cars:
        # поиск по парку
        parks = await db.get_parks()
        park_ids = [p[0] for p in parks if ql in p[1].lower()] if False else []
        ql = q.lower()
        park_ids = [p[0] for p in parks if ql in p[1].lower()]
        if park_ids:
            cars = []
            for pid in park_ids:
                cars.extend(await db.get_cars_by_park(pid))
    if not cars:
        await message.answer("Ничего не найдено.", reply_markup=main_kb)
        return
    rows = [[InlineKeyboardButton(
        text=f"{c[1]} {c[2]} — {c[5]}", callback_data=f"car_{c[0]}"
    )] for c in cars]
    await message.answer(f"Найдено ({len(cars)}):", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

# ============================================================
# ВКЛАДКА «МАШИНЫ»
# ============================================================

@dp.message(F.text == "🚗 Машины")
async def cars_tab(message: Message, state: FSMContext):
    await state.clear()
    parks = await db.get_parks()
    rows = [[InlineKeyboardButton(text=p[1], callback_data=f"tabpark_{p[0]}")] for p in parks]
    await message.answer("Выбери таксопарк:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("tabpark_"))
async def tab_park(callback: CallbackQuery):
    park_id = int(callback.data.split("_")[1])
    cars = await db.get_cars_by_park(park_id)
    if not cars:
        await callback.message.edit_text("В этом парке нет машин.")
        await callback.answer()
        return
    rows = [[InlineKeyboardButton(
        text=f"{c[1]} {c[2]} — {c[5]}", callback_data=f"car_{c[0]}"
    )] for c in cars]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="tab_back")])
    await callback.message.edit_text("Машины в парке:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@dp.callback_query(F.data == "tab_back")
async def tab_back(callback: CallbackQuery):
    parks = await db.get_parks()
    rows = [[InlineKeyboardButton(text=p[1], callback_data=f"tabpark_{p[0]}")] for p in parks]
    await callback.message.edit_text("Выбери таксопарк:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()

# ============================================================
# ДОБАВЛЕНИЕ МАШИНЫ
# ============================================================

@dp.message(F.text == "➕ Добавить машину")
async def add_start(message: Message, state: FSMContext):
    await state.clear()
    parks = await db.get_parks()
    rows = [[InlineKeyboardButton(text=p[1], callback_data=f"pickpark_{p[0]}")] for p in parks]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_all")])
    await message.answer("Выбери таксопарк:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data == "cancel_all")
async def cancel_all(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text("Отменено.")
    except Exception:
        pass
    await callback.message.answer("Возврат в меню.", reply_markup=main_kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("pickpark_"))
async def pick_park(callback: CallbackQuery, state: FSMContext):
    park_id = int(callback.data.split("_", 1)[1])
    parks = await db.get_parks()
    park = next((p for p in parks if p[0] == park_id), None)
    if not park:
        await callback.answer("Парк не найден", show_alert=True)
        return
    if park[1] == "Другой таксопарк / Улица":
        await callback.message.edit_text("Введи название парка / улицы:")
        await callback.message.answer("Введи название:", reply_markup=cancel_kb)
        await state.set_state(AddCar.custom_park)
        await callback.answer()
        return
    await state.update_data(park_id=park[0], park_name=park[1])
    await callback.message.edit_text(f"Парк: {park[1]}")
    await callback.message.answer("Введи модель машины:", reply_markup=cancel_kb)
    await state.set_state(AddCar.model)
    await callback.answer()


@dp.message(AddCar.custom_park)
async def custom_park(message: Message, state: FSMContext):
    name = message.text.strip()
    if not name:
        await message.answer("Название пустое, попробуй ещё:")
        return
    park = await db.get_park_by_name(name)
    if not park:
        await db.add_park(name)
        park = await db.get_park_by_name(name)
    await state.update_data(park_id=park[0], park_name=park[1])
    await state.set_state(AddCar.model)
    await message.answer(f"Парк: {park[1]}\n\nВведи модель машины:", reply_markup=cancel_kb)


@dp.message(AddCar.model)
async def process_model(message: Message, state: FSMContext):
    await state.update_data(model=message.text.strip())
    await state.set_state(AddCar.plate)
    await message.answer("Введи госномер:", reply_markup=cancel_kb)


@dp.message(AddCar.plate)
async def process_plate(message: Message, state: FSMContext):
    plate = message.text.strip().upper()
    existing = await db.find_car_by_plate(plate)
    exact = [c for c in existing if c[3] == plate]
    if exact:
        prev = exact[0]
        await state.update_data(plate=plate, works=[], hours=[],
                                prev_works=prev[4], prev_hours=prev[5])
        await message.answer(
            f"Эта машина уже была. Работы: {prev[4]} (~{prev[5]:.1f} ч).\n"
            f"Повторить работы?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Повторить", callback_data="repeat_prev")],
                [InlineKeyboardButton(text="✍️ Выбрать заново", callback_data="works_pick")],
            ])
        )
        await state.set_state(AddCar.works_menu)
        return
    await state.update_data(plate=plate, works=[], hours=[])
    await state.set_state(AddCar.works_menu)
    await show_works_menu(message, state)


@dp.callback_query(F.data == "repeat_prev")
async def repeat_prev(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    works_str = data.get("prev_works", "")
    hours = data.get("prev_hours", 0)
    works = [w.strip() for w in works_str.split(";") if w.strip()]
    await state.update_data(works=works, hours=[0]*len(works),
                            final_works=works_str, final_hours=hours)
    await callback.message.edit_text(
        "Итого:\n" + "\n".join(f"• {w}" for w in works) +
        f"\n\nВремя: {hours:.1f} ч",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💾 Сохранить машину", callback_data="save_car")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_all")],
        ])
    )
    await callback.answer()


async def show_works_menu(message: Message, state: FSMContext):
    data = await state.get_data()
    selected = data.get("works", [])
    hours = data.get("hours", [])
    text = "Работы по машине\n\n"
    if selected:
        text += "Выбрано:\n" + "\n".join(f"• {w}" for w in selected)
        text += f"\nИтого: {sum(hours):.1f} ч\n\n"
    text += "Выбери действие:"
    rows = [
        [InlineKeyboardButton(text="📋 Из справочника", callback_data="works_pick")],
        [InlineKeyboardButton(text="✍️ Вписать вручную", callback_data="works_custom")],
        [InlineKeyboardButton(text="✅ Готово", callback_data="works_done")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_all")],
    ]
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data == "works_pick")
async def works_pick(callback: CallbackQuery, state: FSMContext):
    norms = await db.get_all_norms()
    rows = []
    for n in norms[:40]:
        nid, key, display, h, cat = n
        rows.append([InlineKeyboardButton(text=f"{display} ({h} ч)", callback_data=f"addnorm_{nid}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="works_back")])
    await callback.message.edit_text("Выбери работу:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@dp.callback_query(F.data == "works_back")
async def works_back(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await show_works_menu(callback.message, state)
    await callback.answer()


@dp.callback_query(F.data.startswith("addnorm_"))
async def add_norm_to_car(callback: CallbackQuery, state: FSMContext):
    nid = int(callback.data.split("_")[1])
    norms = await db.get_all_norms()
    norm = next((n for n in norms if n[0] == nid), None)
    if not norm:
        await callback.answer("Не найдено")
        return
    data = await state.get_data()
    works = data.get("works", [])
    hours = data.get("hours", [])
    works.append(norm[2])
    hours.append(norm[3])
    await state.update_data(works=works, hours=hours)
    await callback.answer(f"Добавлено: {norm[2]}")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await show_works_menu(callback.message, state)


@dp.callback_query(F.data == "works_custom")
async def works_custom_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Введи работу и часы через запятую. Пример: Замена фазорегулятора, 2.5")
    await callback.message.answer("Введи работу и часы:", reply_markup=cancel_kb)
    await state.set_state(AddCar.works_custom)
    await callback.answer()


@dp.message(AddCar.works_custom)
async def works_custom(message: Message, state: FSMContext):
    parts = message.text.rsplit(",", 1)
    if len(parts) != 2:
        await message.answer("Формат: Название, часы. Пример: Замена фазорегулятора, 2.5")
        return
    name = parts[0].strip()
    try:
        h = float(parts[1].strip().replace(",", "."))
    except ValueError:
        await message.answer("Часы числом. Пример: 2.5")
        return
    data = await state.get_data()
    works = data.get("works", [])
    hours = data.get("hours", [])
    works.append(name)
    hours.append(h)
    await state.update_data(works=works, hours=hours, temp_norm_name=name, temp_norm_hours=h)
    await message.answer(
        f"{name} ({h} ч) добавлено. Сохранить в справочник?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💾 Сохранить", callback_data="norm_yes")],
            [InlineKeyboardButton(text="Пропустить", callback_data="norm_no")],
        ])
    )
    await state.set_state(AddCar.works_menu)


@dp.callback_query(F.data == "norm_yes")
async def norm_yes(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    name = data.get("temp_norm_name")
    h = data.get("temp_norm_hours")
    if name and h is not None:
        await db.add_norm(name.lower(), name, h, "Прочее")
    await callback.answer("Сохранено")
    try:
        await callback.message.edit_text(f"«{name}» сохранено.")
    except Exception:
        pass
    await show_works_menu(callback.message, state)


@dp.callback_query(F.data == "norm_no")
async def norm_no(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Пропущено")
    try:
        await callback.message.edit_text("Пропущено.")
    except Exception:
        pass
    await show_works_menu(callback.message, state)


@dp.callback_query(F.data == "works_done")
async def works_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    works = data.get("works", [])
    hours = data.get("hours", [])
    if not works:
        await callback.answer("Добавь хотя бы одну работу", show_alert=True)
        return
    await state.update_data(final_works="; ".join(works), final_hours=sum(hours))
    await callback.message.edit_text(
        "Итого:\n" + "\n".join(f"• {w}" for w in works) +
        f"\n\nВремя: {sum(hours):.1f} ч",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💾 Сохранить машину", callback_data="save_car")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_all")],
        ])
    )
    await callback.answer()


@dp.callback_query(F.data == "save_car")
async def save_car(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    car_id = await db.add_car(data["park_id"], data["model"], data["plate"],
                              data["final_works"], data["final_hours"])
    works = data.get("works", [])
    hours = data.get("hours", [])
    for w, h in zip(works, hours):
        await db.add_car_work(car_id, w, h)
    await state.clear()
    await callback.message.edit_text(
        f"{data['model']} ({data['plate']}) добавлена.\n"
        f"Парк: {data['park_name']}\n"
        f"Время: ~{data['final_hours']:.1f} ч"
    )
    await callback.message.answer("Возврат в меню.", reply_markup=main_kb)
    await callback.answer()

# ============================================================
# КАРТОЧКА МАШИНЫ
# ============================================================

async def render_car(car_id):
    car = await db.get_car(car_id)
    if not car:
        return None, None
    _, park_id, model, plate, works, hours, status, since, note, urgent = car
    days = db.days_in_status(since)
    fire = "🔥 " if urgent else ""
    text = f"{fire}<b>{model}</b> ({plate})\n"
    text += f"Статус: {status}"
    if status != "Машина готова":
        text += f" · {days} дн."
    text += "\n"
    if works:
        text += f"Работы: {works}\n"
    if hours:
        text += f"Время: ~{hours:.1f} ч\n"
    if status == "Ждёт запчасти":
        parts = await db.get_parts_waiting(car_id)
        if parts:
            pending = [p[1] for p in parts if not p[2]]
            arrived = [p[1] for p in parts if p[2]]
            if pending:
                text += f"Ждём: {', '.join(pending)}\n"
            if arrived:
                text += f"Пришли: {', '.join(arrived)}\n"
    if note and not note.startswith("Ждём:"):
        text += f"Заметка: {note}\n"

    fire_label = "🔥 Убрать срочность" if urgent else "🔥 Сделать срочной"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🅿️", callback_data=f"s_parking_{car_id}"),
            InlineKeyboardButton(text="🔧", callback_data=f"s_repair_{car_id}"),
            InlineKeyboardButton(text="📦", callback_data=f"s_wait_{car_id}"),
            InlineKeyboardButton(text="✅", callback_data=f"s_done_{car_id}"),
        ],
        [InlineKeyboardButton(text=fire_label, callback_data=f"urgent_{car_id}")],
        [InlineKeyboardButton(text="🔧 Работы", callback_data=f"works_{car_id}"),
         InlineKeyboardButton(text="📦 Запчасти", callback_data=f"parts_{car_id}")],
        [InlineKeyboardButton(text="📝 Заметка", callback_data=f"note_{car_id}"),
         InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{car_id}")],
        [InlineKeyboardButton(text="⬅️ К парку", callback_data=f"tabpark_{park_id}")],
    ])
    return text, kb


@dp.callback_query(F.data.startswith("car_"))
async def show_car(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    car_id = int(callback.data.split("_")[1])
    text, kb = await render_car(car_id)
    if not text:
        await callback.answer("Машина не найдена", show_alert=True)
        return
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("urgent_"))
async def toggle_urgent(callback: CallbackQuery):
    car_id = int(callback.data.split("_")[1])
    car = await db.get_car(car_id)
    if not car:
        await callback.answer("Машина не найдена", show_alert=True)
        return
    new_state = 0 if car[9] else 1
    await db.set_urgent(car_id, new_state)
    await callback.answer("Срочность " + ("включена" if new_state else "снята"))
    text, kb = await render_car(car_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


@dp.callback_query(F.data.startswith("s_"))
async def set_status(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    action = parts[1]
    car_id = int(parts[2])
    smap = {"parking": "На парковке", "repair": "В работе",
            "wait": "Ждёт запчасти", "done": "Машина готова"}
    if action == "wait":
        await state.update_data(wait_car_id=car_id, from_summary=True)
        await callback.message.edit_text("Введи запчасти через запятую:")
        await callback.message.answer("Введи запчасти:", reply_markup=cancel_kb)
        await state.set_state(WaitingParts.names)
        await callback.answer()
        return
    await db.update_car_status(car_id, smap[action])
    await callback.answer(f"Статус: {smap[action]}")
    # перерисовываем сводку
    await render_summary(callback.message, edit_msg=callback.message)


@dp.message(WaitingParts.names)
async def process_wait(message: Message, state: FSMContext):
    data = await state.get_data()
    car_id = data.get("wait_car_id")
    if not car_id:
        await state.clear()
        await message.answer("Что-то пошло не так.", reply_markup=main_kb)
        return
    names = [x.strip() for x in message.text.split(",") if x.strip()]
    for n in names:
        await db.add_part_waiting(car_id, n)
    await db.update_car_status(car_id, "Ждёт запчасти")
    await state.clear()
    await message.answer(f"Записано: {', '.join(names)}", reply_markup=main_kb)


# ---------- Запчасти ----------

@dp.callback_query(F.data.startswith("parts_"))
async def parts_menu(callback: CallbackQuery):
    car_id = int(callback.data.split("_")[1])
    parts = await db.get_parts_waiting(car_id)
    rows = []
    for pid, name, arrived in parts:
        mark = "✅" if arrived else "⬜"
        rows.append([
            InlineKeyboardButton(text=f"{mark} {name}", callback_data=f"arrive_{pid}_{car_id}"),
            InlineKeyboardButton(text="🗑", callback_data=f"delpart_{pid}_{car_id}"),
        ])
    rows.append([InlineKeyboardButton(text="➕ Добавить запчасть", callback_data=f"addpart_{car_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"car_{car_id}")])
    await callback.message.edit_text(
        "Запчасти (нажми — отметить приход, 🗑 — удалить):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("arrive_"))
async def arrive_part(callback: CallbackQuery):
    _, pid, car_id = callback.data.split("_")
    pid, car_id = int(pid), int(car_id)
    await db.mark_part_arrived(pid)
    if await db.all_parts_arrived(car_id):
        await db.update_car_status(car_id, "На парковке")
        await db.update_car_note(car_id, "")
        await callback.answer("Все запчасти пришли → На парковке")
    else:
        await callback.answer("Отмечено")
    await parts_menu(callback)


@dp.callback_query(F.data.startswith("delpart_"))
async def del_part(callback: CallbackQuery):
    _, pid, car_id = callback.data.split("_")
    await db.delete_part(int(pid))
    await callback.answer("Удалено")
    await parts_menu(callback)


@dp.callback_query(F.data.startswith("addpart_"))
async def add_part_start(callback: CallbackQuery, state: FSMContext):
    car_id = int(callback.data.split("_")[1])
    await state.update_data(part_car_id=car_id)
    await callback.message.edit_text("Введи название запчасти:")
    await callback.message.answer("Введи название:", reply_markup=cancel_kb)
    await state.set_state(CarParts.new)
    await callback.answer()


@dp.message(CarParts.new)
async def add_part_save(message: Message, state: FSMContext):
    data = await state.get_data()
    car_id = data.get("part_car_id")
    if not car_id:
        await state.clear()
        await message.answer("Ошибка.", reply_markup=main_kb)
        return
    await db.add_part_waiting(car_id, message.text.strip())
    await state.clear()
    await message.answer("Запчасть добавлена.", reply_markup=main_kb)


# ---------- Работы машины ----------

@dp.callback_query(F.data.regexp(r"^works_\d+$"))
async def car_works_menu(callback: CallbackQuery):
    car_id = int(callback.data.split("_")[1])
    works = await db.get_car_works(car_id)
    rows = []
    for wid, name, h in works:
        rows.append([
            InlineKeyboardButton(text=f"{name} ({h} ч)", callback_data=f"noop"),
            InlineKeyboardButton(text="🗑", callback_data=f"delwork_{wid}_{car_id}"),
        ])
    rows.append([InlineKeyboardButton(text="➕ Из справочника", callback_data=f"pickwork_{car_id}")])
    rows.append([InlineKeyboardButton(text="✍️ Вписать вручную", callback_data=f"customwork_{car_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"car_{car_id}")])
    await callback.message.edit_text("Работы (🗑 — удалить):", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@dp.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery):
    await callback.answer()


@dp.callback_query(F.data.startswith("delwork_"))
async def del_work(callback: CallbackQuery):
    _, wid, car_id = callback.data.split("_")
    await db.delete_car_work(int(wid))
    await callback.answer("Удалено")
    await car_works_menu(callback)


@dp.callback_query(F.data.startswith("pickwork_"))
async def pick_work(callback: CallbackQuery, state: FSMContext):
    car_id = int(callback.data.split("_")[1])
    await state.update_data(work_car_id=car_id)
    norms = await db.get_all_norms()
    rows = []
    for n in norms[:40]:
        nid, key, display, h, cat = n
        rows.append([InlineKeyboardButton(text=f"{display} ({h} ч)", callback_data=f"addwork_{nid}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"works_{car_id}")])
    await callback.message.edit_text("Выбери работу:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@dp.callback_query(F.data.startswith("addwork_"))
async def add_work_norm(callback: CallbackQuery, state: FSMContext):
    nid = int(callback.data.split("_")[1])
    data = await state.get_data()
    car_id = data.get("work_car_id")
    if not car_id:
        await callback.answer("Ошибка", show_alert=True)
        return
    norms = await db.get_all_norms()
    norm = next((n for n in norms if n[0] == nid), None)
    if not norm:
        await callback.answer("Не найдено")
        return
    await db.add_car_work(car_id, norm[2], norm[3])
    await callback.answer(f"Добавлено: {norm[2]}")
    await car_works_menu(callback)


@dp.callback_query(F.data.startswith("customwork_"))
async def custom_work_start(callback: CallbackQuery, state: FSMContext):
    car_id = int(callback.data.split("_")[1])
    await state.update_data(work_car_id=car_id)
    await callback.message.edit_text("Введи работу и часы через запятую. Пример: Замена фазорегулятора, 2.5")
    await callback.message.answer("Введи:", reply_markup=cancel_kb)
    await state.set_state(CarWorks.custom)
    await callback.answer()


@dp.message(CarWorks.custom)
async def custom_work_save(message: Message, state: FSMContext):
    data = await state.get_data()
    car_id = data.get("work_car_id")
    if not car_id:
        await state.clear()
        await message.answer("Ошибка.", reply_markup=main_kb)
        return
    parts = message.text.rsplit(",", 1)
    if len(parts) != 2:
        await message.answer("Формат: Название, часы. Пример: Замена фазорегулятора, 2.5")
        return
    name = parts[0].strip()
    try:
        h = float(parts[1].strip().replace(",", "."))
    except ValueError:
        await message.answer("Часы числом. Пример: 2.5")
        return
    await db.add_car_work(car_id, name, h)
    await state.clear()
    await message.answer(f"Добавлено: {name} ({h} ч).", reply_markup=main_kb)


# ---------- Заметка ----------

@dp.callback_query(F.data.startswith("note_"))
async def note_start(callback: CallbackQuery, state: FSMContext):
    car_id = int(callback.data.split("_")[1])
    await state.update_data(note_car_id=car_id)
    await callback.message.edit_text("Введи заметку:")
    await callback.message.answer("Введи заметку:", reply_markup=cancel_kb)
    await state.set_state(EditNote.text)
    await callback.answer()


@dp.message(EditNote.text)
async def save_note(message: Message, state: FSMContext):
    data = await state.get_data()
    await db.update_car_note(data["note_car_id"], message.text.strip())
    await state.clear()
    await message.answer("Заметка сохранена.", reply_markup=main_kb)


# ---------- Удаление ----------

@dp.callback_query(F.data.regexp(r"^del_\d+$"))
async def del_confirm(callback: CallbackQuery):
    car_id = int(callback.data.split("_")[1])
    await callback.message.edit_text(
        "Удалить машину?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data=f"delok_{car_id}")],
            [InlineKeyboardButton(text="❌ Нет", callback_data=f"car_{car_id}")],
        ]))
    await callback.answer()


@dp.callback_query(F.data.startswith("delok_"))
async def del_do(callback: CallbackQuery):
    car_id = int(callback.data.split("_")[1])
    await db.delete_car(car_id)
    await callback.answer("Удалено")
    try:
        await callback.message.edit_text("Машина удалена.")
    except Exception:
        pass

# ============================================================
# НОРМО-ЧАСЫ
# ============================================================

@dp.message(F.text == "⚙️ Нормо-часы")
async def norms_menu(message: Message, state: FSMContext):
    await state.clear()
    norms = await db.get_all_norms()
    rows = [[InlineKeyboardButton(text=f"{n[2]} — {n[3]} ч", callback_data=f"delnorm_{n[0]}")] for n in norms[:40]]
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="add_norm")])
    await message.answer("Справочник. Нажми, чтобы удалить, или добавь.", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("delnorm_"))
async def del_norm(callback: CallbackQuery):
    nid = int(callback.data.split("_")[1])
    await db.delete_norm(nid)
    await callback.answer("Удалено")
    norms = await db.get_all_norms()
    rows = [[InlineKeyboardButton(text=f"{n[2]} — {n[3]} ч", callback_data=f"delnorm_{n[0]}")] for n in norms[:40]]
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="add_norm")])
    await callback.message.edit_text("Справочник:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data == "add_norm")
async def add_norm_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Введи название работы:")
    await callback.message.answer("Введи название:", reply_markup=cancel_kb)
    await state.set_state(AddNorm.name)
    await callback.answer()


@dp.message(AddNorm.name)
async def norm_name(message: Message, state: FSMContext):
    await state.update_data(norm_name=message.text.strip())
    await state.set_state(AddNorm.hours)
    await message.answer("Введи время в часах (например, 1.5):", reply_markup=cancel_kb)


@dp.message(AddNorm.hours)
async def norm_hours(message: Message, state: FSMContext):
    try:
        h = float(message.text.strip().replace(",", "."))
    except ValueError:
        await message.answer("Нужно число. Пример: 1.5")
        return
    data = await state.get_data()
    name = data["norm_name"]
    ok = await db.add_norm(name.lower(), name, h, "Прочее")
    await state.clear()
    await message.answer("Добавлено." if ok else "Уже есть.", reply_markup=main_kb)

# ============================================================
# УТРЕННЯЯ СВОДКА
# ============================================================

DIGEST_HOUR = 10

def _reschedule_digest(hour: int):
    global DIGEST_HOUR
    DIGEST_HOUR = hour
    try:
        for job in scheduler.get_jobs():
            if job.id == "morning_digest":
                scheduler.remove_job("morning_digest")
    except Exception:
        pass
    scheduler.add_job(
        morning_digest,
        CronTrigger(hour=hour, minute=0),
        id="morning_digest",
        misfire_grace_time=3600,
        coalesce=True,
    )
    print(f"Утренняя сводка запланирована на {hour:02d}:00")


async def morning_digest():
    subs = await db.get_subscribers()
    print(f"[digest] подписчиков: {len(subs)}")
    if not subs:
        return
    stats = await db.get_stats_by_park()
    total = sum(len(p["cars"]) for p in stats)
    print(f"[digest] всего машин: {total}")
    if total == 0:
        return

    text = f"🌅 Доброе утро! {datetime.now().strftime('%d.%m.%Y')}\n"
    text += "Требует внимания:\n"

    has_any = False
    # срочные / красные / до 2 часов
    for p in stats:
        attention = [c for c in p["cars"] if is_attention_car(c)]
        if not attention:
            continue
        has_any = True
        text += f"\n🏢 {p['park_name']}\n"
        for car in attention:
            text += await car_line_summary(car) + "\n"

    # готовые, но стоят больше 2 дней (пункт 10)
    ready_stuck = []
    for p in stats:
        for car in p["cars"]:
            if car[5] == "Машина готова" and db.days_in_status(car[6]) >= 2:
                ready_stuck.append((p["park_name"], car))
    if ready_stuck:
        has_any = True
        text += "\n✅ Готовы, но стоят больше 2 дней:\n"
        for park_name, car in ready_stuck:
            days = db.days_in_status(car[6])
            text += f"  {car[1]} {car[2]} — {park_name} · {days} дн.\n"

    if not has_any:
        text += "\nНет машин, требующих внимания."
    for chat_id in subs:
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML")
            print(f"[digest] отправлено {chat_id}")
        except Exception as e:
            print(f"[digest] НЕ отправлено {chat_id}: {e}")

# ============================================================
# ЗАПУСК
# ============================================================

async def main():
    await db.init_db()
    hour = 10
    try:
        saved = await db.get_setting("digest_hour")
        if saved and saved.isdigit():
            hour = int(saved)
    except Exception:
        pass
    _reschedule_digest(hour)
    scheduler.start()
    print("Бот запущен!")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())