import asyncio
import csv
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove


BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / "state"
DATA_FILE = STATE_DIR / "entries.json"
DEFAULT_CURRENCY = "₽"


@dataclass
class Entry:
    id: str
    user_id: int
    created_at: str
    entry_date: str
    marketplace: str
    product: str
    units: float
    price: float
    cost: float
    commission_percent: float
    acquiring_percent: float
    logistics: float
    storage: float
    ads: float
    tax_percent: float
    returns: float
    other: float
    note: str = ""


class AddEntry(StatesGroup):
    marketplace = State()
    product = State()
    entry_date = State()
    units = State()
    price = State()
    cost = State()
    commission_percent = State()
    acquiring_percent = State()
    logistics = State()
    storage = State()
    ads = State()
    tax_percent = State()
    returns = State()
    other = State()
    note = State()


router = Router()


MARKET_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="WB"), KeyboardButton(text="Ozon")],
        [KeyboardButton(text="Отмена")],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)


def ensure_storage() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        DATA_FILE.write_text("[]", encoding="utf-8")


def load_entries() -> list[dict[str, Any]]:
    ensure_storage()
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_entries(entries: list[dict[str, Any]]) -> None:
    ensure_storage()
    temp_path = DATA_FILE.with_suffix(".tmp")
    temp_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(DATA_FILE)


def user_entries(user_id: int) -> list[dict[str, Any]]:
    return [entry for entry in load_entries() if entry["user_id"] == user_id]


def parse_float(value: str) -> float:
    cleaned = value.strip().replace(" ", "").replace(",", ".")
    if cleaned in {"", "-", "нет"}:
        return 0.0
    return float(cleaned)


def parse_date(value: str) -> str:
    text = value.strip().lower()
    if text in {"", "сегодня", "today"}:
        return date.today().isoformat()
    if text in {"вчера", "yesterday"}:
        return date.fromordinal(date.today().toordinal() - 1).isoformat()
    return datetime.strptime(text, "%Y-%m-%d").date().isoformat()


def money(value: float) -> str:
    return f"{value:,.0f} {DEFAULT_CURRENCY}".replace(",", " ")


def percent(value: float) -> str:
    return f"{value:.1f}%".replace(".", ",")


def calc(entry: dict[str, Any]) -> dict[str, float]:
    sold_units = max(0.0, float(entry["units"]) - float(entry["returns"]))
    revenue = sold_units * float(entry["price"])
    goods_cost = sold_units * float(entry["cost"])
    commission = revenue * float(entry["commission_percent"]) / 100
    acquiring = revenue * float(entry["acquiring_percent"]) / 100
    tax = revenue * float(entry["tax_percent"]) / 100
    expenses = (
        goods_cost
        + commission
        + acquiring
        + tax
        + float(entry["logistics"])
        + float(entry["storage"])
        + float(entry["ads"])
        + float(entry["other"])
    )
    profit = revenue - expenses
    margin = profit / revenue * 100 if revenue else 0.0
    return {
        "sold_units": sold_units,
        "revenue": revenue,
        "goods_cost": goods_cost,
        "commission": commission,
        "acquiring": acquiring,
        "tax": tax,
        "expenses": expenses,
        "profit": profit,
        "margin": margin,
    }


def totals(entries: list[dict[str, Any]]) -> dict[str, Any]:
    result = {
        "revenue": 0.0,
        "expenses": 0.0,
        "profit": 0.0,
        "units": 0.0,
        "days": set(),
        "count": len(entries),
    }
    for entry in entries:
        values = calc(entry)
        result["revenue"] += values["revenue"]
        result["expenses"] += values["expenses"]
        result["profit"] += values["profit"]
        result["units"] += values["sold_units"]
        result["days"].add(entry["entry_date"])
    result["margin"] = result["profit"] / result["revenue"] * 100 if result["revenue"] else 0.0
    result["avg_day_profit"] = result["profit"] / len(result["days"]) if result["days"] else 0.0
    return result


def entry_report(entry: dict[str, Any]) -> str:
    values = calc(entry)
    sign = "прибыль" if values["profit"] >= 0 else "убыток"
    lines = [
        f"Готово: {entry['marketplace']} / {entry['product']}",
        f"Дата: {entry['entry_date']}",
        f"Продано: {values['sold_units']:g} шт.",
        f"Выручка: {money(values['revenue'])}",
        f"Расходы: {money(values['expenses'])}",
        f"Чистая {sign}: {money(values['profit'])}",
        f"Маржа: {percent(values['margin'])}",
        f"ID: {entry['id'][:8]}",
    ]
    if entry.get("note"):
        lines.append(f"Заметка: {entry['note']}")
    return "\n".join(lines)


def summary_report(entries: list[dict[str, Any]], title: str) -> str:
    if not entries:
        return f"{title}\n\nЗаписей пока нет."

    total = totals(entries)
    wb = totals([entry for entry in entries if entry["marketplace"] == "WB"])
    ozon = totals([entry for entry in entries if entry["marketplace"] == "Ozon"])
    return "\n".join(
        [
            title,
            "",
            f"Записей: {total['count']}",
            f"Продано: {total['units']:g} шт.",
            f"Выручка: {money(total['revenue'])}",
            f"Расходы: {money(total['expenses'])}",
            f"Чистая прибыль: {money(total['profit'])}",
            f"Маржа: {percent(total['margin'])}",
            f"Средняя прибыль в день: {money(total['avg_day_profit'])}",
            "",
            "По площадкам:",
            f"WB: {money(wb['profit'])} прибыли, {money(wb['revenue'])} выручки",
            f"Ozon: {money(ozon['profit'])} прибыли, {money(ozon['revenue'])} выручки",
        ]
    )


def help_text() -> str:
    return (
        "Я считаю ежедневную прибыль по WB и Ozon.\n\n"
        "Команды:\n"
        "/add - добавить день пошагово\n"
        "/quick - пример быстрого ввода\n"
        "/today - прибыль за сегодня\n"
        "/summary - общая сводка\n"
        "/month - сводка за текущий месяц\n"
        "/history - последние записи\n"
        "/delete ID - удалить запись\n"
        "/export - выгрузить CSV\n\n"
        "Быстрый ввод:\n"
        "/quick WB; триммер; 2026-06-29; 10; 990; 420; 18; 1.5; 600; 120; 800; 6; 1; 0; акция"
    )


def parse_quick_payload(user_id: int, payload: str) -> Entry:
    parts = [part.strip() for part in payload.split(";")]
    if len(parts) < 14:
        raise ValueError("Нужно минимум 14 полей, разделенных точкой с запятой.")

    marketplace = parts[0]
    if marketplace.lower() in {"wildberries", "вб", "wb"}:
        marketplace = "WB"
    elif marketplace.lower() in {"ozon", "озон"}:
        marketplace = "Ozon"
    else:
        raise ValueError("Площадка должна быть WB или Ozon.")

    return Entry(
        id=uuid4().hex,
        user_id=user_id,
        created_at=datetime.now().isoformat(timespec="seconds"),
        marketplace=marketplace,
        product=parts[1],
        entry_date=parse_date(parts[2]),
        units=parse_float(parts[3]),
        price=parse_float(parts[4]),
        cost=parse_float(parts[5]),
        commission_percent=parse_float(parts[6]),
        acquiring_percent=parse_float(parts[7]),
        logistics=parse_float(parts[8]),
        storage=parse_float(parts[9]),
        ads=parse_float(parts[10]),
        tax_percent=parse_float(parts[11]),
        returns=parse_float(parts[12]),
        other=parse_float(parts[13]),
        note=parts[14] if len(parts) > 14 else "",
    )


def add_entry(entry: Entry) -> dict[str, Any]:
    entries = load_entries()
    payload = asdict(entry)
    entries.append(payload)
    save_entries(entries)
    return payload


def current_month_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prefix = date.today().isoformat()[:7]
    return [entry for entry in entries if entry["entry_date"].startswith(prefix)]


def today_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    today_text = date.today().isoformat()
    return [entry for entry in entries if entry["entry_date"] == today_text]


@router.message(Command("start", "help"))
async def start(message: Message) -> None:
    await message.answer(help_text())


@router.message(Command("add"))
async def add_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AddEntry.marketplace)
    await message.answer("Выбери площадку:", reply_markup=MARKET_KEYBOARD)


@router.message(F.text.casefold() == "отмена")
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ок, отменил ввод.", reply_markup=ReplyKeyboardRemove())


@router.message(AddEntry.marketplace)
async def add_marketplace(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if text not in {"WB", "Ozon"}:
        await message.answer("Нужно выбрать WB или Ozon.")
        return
    await state.update_data(marketplace=text)
    await state.set_state(AddEntry.product)
    await message.answer("Название товара:", reply_markup=ReplyKeyboardRemove())


@router.message(AddEntry.product)
async def add_product(message: Message, state: FSMContext) -> None:
    await state.update_data(product=message.text.strip())
    await state.set_state(AddEntry.entry_date)
    await message.answer("Дата. Можно написать: сегодня, вчера или 2026-06-29")


@router.message(AddEntry.entry_date)
async def add_date(message: Message, state: FSMContext) -> None:
    try:
        value = parse_date(message.text)
    except ValueError:
        await message.answer("Не понял дату. Пример: 2026-06-29")
        return
    await state.update_data(entry_date=value)
    await state.set_state(AddEntry.units)
    await message.answer("Сколько продано, шт.?")


async def handle_float_step(message: Message, state: FSMContext, field: str, next_state: State, prompt: str) -> None:
    try:
        value = parse_float(message.text)
    except ValueError:
        await message.answer("Нужно число. Можно писать через точку или запятую.")
        return
    await state.update_data(**{field: value})
    await state.set_state(next_state)
    await message.answer(prompt)


@router.message(AddEntry.units)
async def add_units(message: Message, state: FSMContext) -> None:
    await handle_float_step(message, state, "units", AddEntry.price, "Цена продажи за штуку?")


@router.message(AddEntry.price)
async def add_price(message: Message, state: FSMContext) -> None:
    await handle_float_step(message, state, "price", AddEntry.cost, "Себестоимость за штуку?")


@router.message(AddEntry.cost)
async def add_cost(message: Message, state: FSMContext) -> None:
    await handle_float_step(message, state, "cost", AddEntry.commission_percent, "Комиссия маркетплейса, %?")


@router.message(AddEntry.commission_percent)
async def add_commission(message: Message, state: FSMContext) -> None:
    await handle_float_step(message, state, "commission_percent", AddEntry.acquiring_percent, "Эквайринг, %? Если нет, напиши 0.")


@router.message(AddEntry.acquiring_percent)
async def add_acquiring(message: Message, state: FSMContext) -> None:
    await handle_float_step(message, state, "acquiring_percent", AddEntry.logistics, "Логистика за день?")


@router.message(AddEntry.logistics)
async def add_logistics(message: Message, state: FSMContext) -> None:
    await handle_float_step(message, state, "logistics", AddEntry.storage, "Хранение / приемка за день?")


@router.message(AddEntry.storage)
async def add_storage(message: Message, state: FSMContext) -> None:
    await handle_float_step(message, state, "storage", AddEntry.ads, "Реклама за день?")


@router.message(AddEntry.ads)
async def add_ads(message: Message, state: FSMContext) -> None:
    await handle_float_step(message, state, "ads", AddEntry.tax_percent, "Налог, %?")


@router.message(AddEntry.tax_percent)
async def add_tax(message: Message, state: FSMContext) -> None:
    await handle_float_step(message, state, "tax_percent", AddEntry.returns, "Возвраты, шт.?")


@router.message(AddEntry.returns)
async def add_returns(message: Message, state: FSMContext) -> None:
    await handle_float_step(message, state, "returns", AddEntry.other, "Прочие расходы?")


@router.message(AddEntry.other)
async def add_other(message: Message, state: FSMContext) -> None:
    try:
        value = parse_float(message.text)
    except ValueError:
        await message.answer("Нужно число. Если расходов нет, напиши 0.")
        return
    await state.update_data(other=value)
    await state.set_state(AddEntry.note)
    await message.answer("Заметка. Если не нужна, напиши '-'")


@router.message(AddEntry.note)
async def add_note(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    note = "" if message.text.strip() == "-" else message.text.strip()
    entry = Entry(
        id=uuid4().hex,
        user_id=message.from_user.id,
        created_at=datetime.now().isoformat(timespec="seconds"),
        note=note,
        **data,
    )
    saved = add_entry(entry)
    await state.clear()
    await message.answer(entry_report(saved))


@router.message(Command("quick"))
async def quick(message: Message) -> None:
    payload = message.text.removeprefix("/quick").strip()
    if not payload:
        await message.answer(
            "Формат:\n"
            "/quick площадка; товар; дата; шт; цена; себестоимость; комиссия%; эквайринг%; "
            "логистика; хранение; реклама; налог%; возвраты; прочее; заметка\n\n"
            "Пример:\n"
            "/quick WB; триммер; сегодня; 10; 990; 420; 18; 1.5; 600; 120; 800; 6; 1; 0; акция"
        )
        return
    try:
        entry = parse_quick_payload(message.from_user.id, payload)
    except ValueError as error:
        await message.answer(f"Не получилось добавить запись.\n{error}")
        return
    saved = add_entry(entry)
    await message.answer(entry_report(saved))


@router.message(Command("today"))
async def today_summary(message: Message) -> None:
    entries = today_entries(user_entries(message.from_user.id))
    await message.answer(summary_report(entries, "Сегодня"))


@router.message(Command("summary"))
async def summary(message: Message) -> None:
    await message.answer(summary_report(user_entries(message.from_user.id), "Общая сводка"))


@router.message(Command("month"))
async def month(message: Message) -> None:
    entries = current_month_entries(user_entries(message.from_user.id))
    await message.answer(summary_report(entries, "Текущий месяц"))


@router.message(Command("history"))
async def history(message: Message) -> None:
    entries = sorted(user_entries(message.from_user.id), key=lambda item: item["entry_date"], reverse=True)[:10]
    if not entries:
        await message.answer("Записей пока нет.")
        return
    lines = ["Последние записи:"]
    for entry in entries:
        values = calc(entry)
        lines.append(
            f"{entry['id'][:8]} | {entry['entry_date']} | {entry['marketplace']} | "
            f"{entry['product']} | прибыль {money(values['profit'])}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("delete"))
async def delete(message: Message) -> None:
    prefix = message.text.removeprefix("/delete").strip()
    if not prefix:
        await message.answer("Напиши ID записи. Например: /delete a1b2c3d4")
        return

    entries = load_entries()
    before = len(entries)
    entries = [
        entry
        for entry in entries
        if not (entry["user_id"] == message.from_user.id and entry["id"].startswith(prefix))
    ]
    if len(entries) == before:
        await message.answer("Не нашел такую запись.")
        return
    save_entries(entries)
    await message.answer("Запись удалена.")


@router.message(Command("export"))
async def export(message: Message) -> None:
    entries = user_entries(message.from_user.id)
    if not entries:
        await message.answer("Пока нечего выгружать.")
        return

    with tempfile.NamedTemporaryFile("w", newline="", encoding="utf-8", suffix=".csv", delete=False) as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "id",
                "date",
                "marketplace",
                "product",
                "units_sold",
                "revenue",
                "expenses",
                "profit",
                "margin_percent",
                "note",
            ]
        )
        for entry in entries:
            values = calc(entry)
            writer.writerow(
                [
                    entry["id"],
                    entry["entry_date"],
                    entry["marketplace"],
                    entry["product"],
                    values["sold_units"],
                    round(values["revenue"], 2),
                    round(values["expenses"], 2),
                    round(values["profit"], 2),
                    round(values["margin"], 2),
                    entry.get("note", ""),
                ]
            )
        path = file.name

    await message.answer_document(FSInputFile(path, filename=f"wb-ozon-profit-{date.today().isoformat()}.csv"))
    Path(path).unlink(missing_ok=True)


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Не понял команду. Напиши /help")


async def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Нужно указать BOT_TOKEN в переменных окружения.")

    ensure_storage()
    bot = Bot(token=token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
