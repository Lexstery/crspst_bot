from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from telegram import KeyboardButton, ReplyKeyboardMarkup


def build_keyboard(rows: list[list[str]], *, resize: bool = True) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(text) for text in row] for row in rows],
        resize_keyboard=resize,
        one_time_keyboard=False,
    )


def admin_main_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        ["📢 Опубликовать пост", "⏰ Отложенный пост"],
        ["📋 Мои каналы", "👥 Управление пользователями"],
        ["⚙️ Управление каналами", "👑 Управление админами"],
        ["📊 Статус", "ℹ️ Помощь"],
        ["🛑 Остановить бота", "❌ Скрыть меню"],
    ]
    return build_keyboard(rows)


def user_main_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        ["📢 Опубликовать пост", "⏰ Отложенный пост"],
        ["📋 Мои каналы", "ℹ️ Помощь"],
        ["❌ Скрыть меню"],
    ]
    return build_keyboard(rows)


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return build_keyboard([["⬅️ Назад", "❌ Отмена"]])


def channel_selection_keyboard(channels: Iterable[dict]) -> ReplyKeyboardMarkup:
    rows: list[list[str]] = []
    row: list[str] = []
    for channel in channels:
        label = f"{channel['name']} (#{channel['id']})"
        row.append(label)
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(["⬅️ Назад", "❌ Отмена"])
    return build_keyboard(rows)


def manage_users_keyboard(pending_users: Iterable[dict]) -> ReplyKeyboardMarkup:
    rows: list[list[str]] = []
    row: list[str] = []
    for user in pending_users:
        row.append(f"✅ {user['telegram_id']}")
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(["🚫 Отклонить", "⬅️ Назад"])
    return build_keyboard(rows)


def manage_admins_keyboard(users: Iterable[dict]) -> ReplyKeyboardMarkup:
    rows: list[list[str]] = []
    row: list[str] = []
    for user in users:
        prefix = "👑" if user["is_admin"] else "➕"
        row.append(f"{prefix} {user['telegram_id']}")
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(["⬅️ Назад"])
    return build_keyboard(rows)


def channel_management_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        ["➕ Добавить канал", "➖ Удалить канал"],
        ["🔄 Активировать канал", "⬅️ Назад"],
    ]
    return build_keyboard(rows)


def schedule_date_keyboard(days: int = 5) -> ReplyKeyboardMarkup:
    today = datetime.now()
    rows: list[list[str]] = []
    row: list[str] = []
    for offset in range(days):
        date = (today + timedelta(days=offset)).strftime("%d.%m.%Y")
        row.append(date)
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(["⬅️ Назад"])
    return build_keyboard(rows)


def schedule_time_keyboard(step_minutes: int = 30) -> ReplyKeyboardMarkup:
    rows: list[list[str]] = []
    row: list[str] = []
    hour = 0
    while hour < 24:
        for minute in range(0, 60, step_minutes):
            row.append(f"{hour:02d}:{minute:02d}")
            if len(row) == 4:
                rows.append(row)
                row = []
        hour += 1
    if row:
        rows.append(row)
    rows.append(["⬅️ Назад"])
    return build_keyboard(rows)


