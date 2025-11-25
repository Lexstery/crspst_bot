from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from datetime import datetime
from typing import Any, Optional

import requests
from flask import Flask, jsonify
from telegram import InputMediaPhoto, Message, ReplyKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

if __package__ in {None, ""}:
    import os
    import sys

    sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from crosspost_bot.config import Settings
from crosspost_bot.database import Database
from crosspost_bot.keyboards import (
    admin_main_keyboard,
    cancel_keyboard,
    channel_management_keyboard,
    channel_selection_keyboard,
    manage_admins_keyboard,
    manage_users_keyboard,
    schedule_date_keyboard,
    schedule_time_keyboard,
    user_main_keyboard,
)
from crosspost_bot.scheduler import ScheduledPostWorker
from crosspost_bot.services.vk_client import VKClient, extract_token_from_url

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
LOGGER = logging.getLogger("crosspost-bot")


STATE_IDLE = "idle"
STATE_POST_CHANNEL = "post_channel"
STATE_POST_CONTENT = "post_content"
STATE_SCHEDULE_CHANNEL = "schedule_channel"
STATE_SCHEDULE_DATE = "schedule_date"
STATE_SCHEDULE_TIME = "schedule_time"
STATE_SCHEDULE_CONTENT = "schedule_content"
STATE_CHANNEL_ADD_NAME = "channel_add_name"
STATE_CHANNEL_ADD_TG = "channel_add_tg"
STATE_CHANNEL_ADD_VK = "channel_add_vk"
STATE_CHANNEL_DEACTIVATE = "channel_deactivate"
STATE_CHANNEL_ACTIVATE = "channel_activate"

ALBUM_CACHE_KEY = "album_cache"
ALBUM_FLUSH_DELAY = 1.0
STATE_MANAGE_USERS = "manage_users"
STATE_MANAGE_ADMINS = "manage_admins"
STATE_TOKEN_UPDATE = "token_update"


flask_app = Flask(__name__)


@flask_app.route("/healthz", methods=["GET"])
def healthcheck():
    return jsonify({"status": "ok"}), 200


def start_flask_server(port: int) -> threading.Thread:
    thread = threading.Thread(
        target=lambda: flask_app.run(
            host="0.0.0.0",
            port=port,
            debug=False,
            use_reloader=False,
        ),
        name="flask-server",
        daemon=True,
    )
    thread.start()
    LOGGER.info("Flask keep-alive server started on port %s", port)
    return thread


async def self_ping_loop(url: str) -> None:
    target = url.rstrip("/") + "/healthz"
    LOGGER.info("Self ping loop targeting %s", target)
    while True:
        try:
            response = requests.get(target, timeout=10)
            LOGGER.debug("Self ping %s -> %s", target, response.status_code)
        except Exception as exc:
            LOGGER.warning("Self ping failed: %s", exc)
        await asyncio.sleep(600)


def get_main_keyboard(user: dict) -> ReplyKeyboardMarkup:
    if user.get("is_admin"):
        return admin_main_keyboard()
    return user_main_keyboard()


async def ensure_user(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> Optional[dict]:
    db: Database = context.application.bot_data["db"]
    telegram_user = update.effective_user
    if not telegram_user:
        return None
    record = await db.upsert_user(
        telegram_id=telegram_user.id,
        username=telegram_user.username,
        first_name=telegram_user.first_name,
        last_name=telegram_user.last_name,
    )
    if not await db.any_admins():
        await db.set_admin(telegram_user.id, True)
        await db.approve_user(telegram_user.id, True)
        LOGGER.info("First user %s promoted to admin automatically", telegram_user.id)
        record["is_admin"] = True
        record["is_approved"] = True
    return record


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await ensure_user(update, context)
    if not user:
        return
    db: Database = context.application.bot_data["db"]
    text_lines = [f"Привет, {update.effective_user.first_name}!"]
    if user["is_approved"]:
        text_lines.append("Вы уже можете пользоваться ботом.")
    else:
        text_lines.append("Ваш запрос отправлен администратору. Ожидайте одобрения.")
        if user["is_admin"]:
            text_lines.append("Как администратор вы одобрены автоматически.")
            await db.approve_user(user["telegram_id"], True)
    await update.message.reply_text(
        "\n".join(text_lines), reply_markup=get_main_keyboard(user)
    )
    context.user_data["state"] = STATE_IDLE


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user:
        return
    await update.message.reply_text(
        "Главное меню", reply_markup=get_main_keyboard(user)
    )
    context.user_data["state"] = STATE_IDLE


async def handle_hide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Меню скрыто. Введите /menu для возврата.")
    context.user_data["state"] = STATE_IDLE


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    vk_client: VKClient = context.application.bot_data["vk_client"]
    channels = await db.list_channels()
    pending = await db.list_pending_users()
    vk_status = "валиден" if await asyncio.to_thread(vk_client.validate) else "ошибка"
    text = (
        f"📊 Статус:\n"
        f"- Активных каналов: {len([c for c in channels if c['is_active']])}\n"
        f"- Отключенных каналов: {len([c for c in channels if not c['is_active']])}\n"
        f"- Ожидают одобрения: {len(pending)}\n"
        f"- VK токен: {vk_status}"
    )
    await update.message.reply_text(text)


async def handle_get_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user or not user.get("is_admin"):
        await update.message.reply_text("Команда доступна только администраторам.")
        return
    url = (
        "https://oauth.vk.com/authorize?client_id=6121396&display=page"
        "&redirect_uri=https://oauth.vk.com/blank.html&scope=offline,photos,wall,groups"
        "&response_type=token&revoke=1"
    )
    await update.message.reply_text(
        "Получите токен по ссылке и отправьте его через /update_token:\n" + url
    )


async def handle_update_token(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    db: Database = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user or not user.get("is_admin"):
        await update.message.reply_text("Команда доступна только администраторам.")
        return
    context.user_data["state"] = STATE_TOKEN_UPDATE
    await update.message.reply_text(
        "Отправьте новый VK токен или ссылку.", reply_markup=cancel_keyboard()
    )


async def handle_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user or not user.get("is_admin"):
        await update.message.reply_text("Команда доступна только администраторам.")
        return
    await update.message.reply_text("Бот останавливается по запросу администратора.")
    await context.application.stop()


def parse_channel_label(label: str) -> Optional[int]:
    if "(#" in label and label.endswith(")"):
        try:
            return int(label.split("(#")[-1].rstrip(")"))
        except ValueError:
            return None
    return None


async def require_approval(update: Update, context, user: dict) -> bool:
    if user.get("is_approved"):
        return True
    await update.message.reply_text(
        "Ваша учетная запись еще не одобрена администратором."
    )
    context.user_data["state"] = STATE_IDLE
    return False


async def start_post_flow(
    update: Update, context: ContextTypes.DEFAULT_TYPE, scheduled: bool = False
) -> None:
    db: Database = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user:
        return
    if not await require_approval(update, context, user):
        return
    channels = await db.list_user_channels(user["telegram_id"])
    if not channels:
        await update.message.reply_text(
            "У вас еще нет назначенных каналов. Обратитесь к администратору."
        )
        return
    context.user_data["pending_post"] = {
        "scheduled": scheduled,
        "user_id": user["telegram_id"],
    }
    next_state = STATE_SCHEDULE_CHANNEL if scheduled else STATE_POST_CHANNEL
    context.user_data["state"] = next_state
    await update.message.reply_text(
        "Выберите канал для публикации.",
        reply_markup=channel_selection_keyboard(channels),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    text = update.message.text.strip()
    state = context.user_data.get("state", STATE_IDLE)
    db: Database = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)

    if text in ("⬅️ Назад", "❌ Отмена"):
        context.user_data.clear()
        context.user_data["state"] = STATE_IDLE
        if user:
            await update.message.reply_text(
                "Действие отменено.", reply_markup=get_main_keyboard(user)
            )
        return

    if state == STATE_IDLE:
        await handle_menu_selection(update, context, text, user)
    elif state == STATE_POST_CHANNEL:
        await process_channel_selection(update, context, text, scheduled=False)
    elif state == STATE_POST_CONTENT:
        await process_post_content(update, context, text=text)
    elif state == STATE_SCHEDULE_CHANNEL:
        await process_channel_selection(update, context, text, scheduled=True)
    elif state == STATE_SCHEDULE_DATE:
        context.user_data.setdefault("pending_post", {})["date"] = text
        context.user_data["state"] = STATE_SCHEDULE_TIME
        await update.message.reply_text(
            "Выберите время публикации.", reply_markup=schedule_time_keyboard()
        )
    elif state == STATE_SCHEDULE_TIME:
        await process_schedule_time(update, context, text)
    elif state == STATE_SCHEDULE_CONTENT:
        await process_schedule_content(update, context, text=text)
    elif state == STATE_CHANNEL_ADD_NAME:
        context.user_data.setdefault("channel", {})["name"] = text
        context.user_data["state"] = STATE_CHANNEL_ADD_TG
        await update.message.reply_text(
            "Введите ссылку или @username Telegram-канала.", reply_markup=cancel_keyboard()
        )
    elif state == STATE_CHANNEL_ADD_TG:
        context.user_data.setdefault("channel", {})["telegram_channel"] = text
        context.user_data["state"] = STATE_CHANNEL_ADD_VK
        await update.message.reply_text(
            "Введите ID группы VK (например 123456 или club123456).",
            reply_markup=cancel_keyboard(),
        )
    elif state == STATE_CHANNEL_ADD_VK:
        await finalize_channel_creation(update, context, text)
    elif state == STATE_CHANNEL_DEACTIVATE:
        await finalize_channel_toggle(update, context, text, deactivate=True)
    elif state == STATE_CHANNEL_ACTIVATE:
        await finalize_channel_toggle(update, context, text, deactivate=False)
    elif state == STATE_MANAGE_USERS:
        await finalize_user_approval(update, context, text)
    elif state == STATE_MANAGE_ADMINS:
        await finalize_admin_toggle(update, context, text)
    elif state == STATE_TOKEN_UPDATE:
        await finalize_token_update(update, context, text)
    else:
        await update.message.reply_text("Неизвестное состояние. Введите /menu.")


async def handle_menu_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, user: Optional[dict]
) -> None:
    if text == "📢 Опубликовать пост":
        await start_post_flow(update, context, scheduled=False)
    elif text == "⏰ Отложенный пост":
        await start_post_flow(update, context, scheduled=True)
    elif text == "📋 Мои каналы":
        await show_user_channels(update, context)
    elif text == "ℹ️ Помощь":
        await show_help(update, context)
    elif text == "📊 Статус":
        await handle_status(update, context)
    elif text == "❌ Скрыть меню":
        await handle_hide(update, context)
    elif text == "🛑 Остановить бота":
        await handle_stop(update, context)
    elif text == "👥 Управление пользователями":
        await start_user_management(update, context)
    elif text == "👑 Управление админами":
        await start_admin_management(update, context)
    elif text == "⚙️ Управление каналами":
        await start_channel_management(update, context)
    elif text == "➕ Добавить канал":
        await start_channel_addition(update, context)
    elif text == "➖ Удалить канал":
        await start_channel_toggle(update, context, deactivate=True)
    elif text == "🔄 Активировать канал":
        await start_channel_toggle(update, context, deactivate=False)
    else:
        await update.message.reply_text("Неизвестная команда. Используйте /menu.")


async def show_user_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    channels = await db.list_user_channels(update.effective_user.id)
    if not channels:
        await update.message.reply_text("Каналы не назначены.", reply_markup=cancel_keyboard())
        return
    lines = ["Ваши каналы:"]
    for channel in channels:
        lines.append(
            f"- {channel['name']}: {channel['telegram_channel']} / VK {channel['vk_group_id']}"
        )
    await update.message.reply_text("\n".join(lines))


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "📘 Руководство по боту\n\n"
        "1️⃣ Основные команды:\n"
        "/start — регистрация и приветствие\n"
        "/menu — показать главное меню\n"
        "/hide — скрыть меню\n"
        "/status — статус каналов и VK токена (админы)\n"
        "/get_token — инструкция получения VK токена (админы)\n"
        "/update_token — обновить VK токен (админы)\n"
        "/stop — остановить бота (админы)\n\n"
        "2️⃣ Главное меню:\n"
        "📢 Опубликовать пост — выбор канала и мгновенная отправка текста/фото в Telegram и VK.\n"
        "⏰ Отложенный пост — выбор канала, даты, времени и содержимого. Пост хранится в планировщике.\n"
        "📋 Мои каналы — список каналов, куда у вас есть доступ.\n"
        "ℹ️ Помощь — это руководство.\n"
        "❌ Скрыть меню — убирает клавиатуру.\n\n"
        "3️⃣ Возможности админов:\n"
        "👥 Управление пользователями — одобрение новых пользователей и выдача доступов.\n"
        "👑 Управление админами — назначение/снятие статуса администратора.\n"
        "⚙️ Управление каналами — добавление, деактивация и повторная активация каналов.\n"
        "📊 Статус — проверка количества каналов, ожидающих пользователей и валидности VK токена.\n"
        "🛑 Остановить бота — плановое выключение сервиса.\n\n"
        "4️⃣ Публикация контента:\n"
        "- Сначала выберите канал.\n"
        "- Затем отправьте текст, одиночное фото или медиагруппу (несколько фото подряд).\n"
        "- При отложенной публикации дополнительно выберите дату и время в формате ДД.ММ.ГГГГ ЧЧ:ММ.\n"
        "- Бот автоматически публикует материалы в выбранном Telegram канале и связанном VK сообществе.\n\n"
        "5️⃣ Управление VK токеном:\n"
        "- /get_token выдаёт ссылку авторизации VK.\n"
        "- После получения токена используйте /update_token.\n"
        "- Бот проверит токен и сохранит его для публикаций.\n\n"
        "6️⃣ Безопасность:\n"
        "- Только одобренные пользователи могут публиковать.\n"
        "- Администраторы контролируют пользователей и каналы.\n"
        "- Все действия логируются, ошибки выводятся в статусе.\n\n"
        "Если возникают вопросы или ошибки — свяжитесь с администратором."
    )
    await update.message.reply_text(text)


async def process_channel_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    scheduled: bool,
) -> None:
    channel_id = parse_channel_label(text)
    db: Database = context.application.bot_data["db"]
    if not channel_id:
        await update.message.reply_text("Выберите канал из списка.")
        return
    channel = await db.get_channel(channel_id)
    if not channel:
        await update.message.reply_text("Канал не найден.")
        return
    context.user_data.setdefault("pending_post", {})["channel"] = channel
    if scheduled:
        context.user_data["state"] = STATE_SCHEDULE_DATE
        await update.message.reply_text(
            "Выберите дату публикации.", reply_markup=schedule_date_keyboard()
        )
    else:
        context.user_data["state"] = STATE_POST_CONTENT
        await update.message.reply_text(
            "Отправьте текст и/или фото для публикации.", reply_markup=cancel_keyboard()
        )


def build_media_payload(message: Message) -> list[dict[str, Any]]:
    if not message.photo:
        return []
    largest = message.photo[-1]
    return [
        {
            "file_id": largest.file_id,
            "file_unique_id": largest.file_unique_id,
        }
    ]


async def process_post_content(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    text: Optional[str] = None,
    media: Optional[list[dict[str, Any]]] = None,
) -> None:
    pending = context.user_data.get("pending_post")
    if not pending:
        await update.message.reply_text("Сначала выберите канал.")
        return
    channel = pending.get("channel")
    if not channel:
        await update.message.reply_text("Канал не выбран.")
        return
    if not text and not media:
        await update.message.reply_text("Не найден текст или фото.")
        return
    await publish_now(update, context, channel, text, media)
    context.user_data.clear()
    context.user_data["state"] = STATE_IDLE


async def publish_now(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    channel: dict,
    text: Optional[str],
    media: Optional[list[dict[str, Any]]],
) -> None:
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )
    bot = context.bot
    vk_client: VKClient = context.application.bot_data["vk_client"]

    telegram_channel = channel["telegram_channel"]
    vk_group_id = channel["vk_group_id"]

    if media:
        if len(media) == 1:
            await bot.send_photo(
                chat_id=telegram_channel,
                photo=media[-1]["file_id"],
                caption=text or "",
            )
        else:
            group = []
            for index, item in enumerate(media):
                caption = text if index == 0 else None
                group.append(
                    InputMediaPhoto(
                        media=item["file_id"],
                        caption=caption,
                    )
                )
            await bot.send_media_group(chat_id=telegram_channel, media=group)
    else:
        await bot.send_message(chat_id=telegram_channel, text=text or "")

    attachments = None
    if media:
        attachments = []
        for item in media:
            telegram_file = await bot.get_file(item["file_id"])
            data = await telegram_file.download_as_bytearray()
            attachments.append((f"{item['file_unique_id']}.jpg", bytes(data)))
    await asyncio.to_thread(
        vk_client.post_to_group,
        group_id=vk_group_id,
        message=text,
        photo_files=attachments,
    )

    await update.message.reply_text("Пост опубликован в Telegram и VK.")


async def process_schedule_time(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    pending = context.user_data.get("pending_post", {})
    date_str = pending.get("date")
    if not date_str:
        await update.message.reply_text("Сначала выберите дату.")
        return
    try:
        scheduled_datetime = datetime.strptime(
            f"{date_str} {text}", "%d.%m.%Y %H:%M"
        )
    except ValueError:
        await update.message.reply_text("Неверный формат времени.")
        return
    pending["scheduled_for"] = scheduled_datetime
    context.user_data["state"] = STATE_SCHEDULE_CONTENT
    await update.message.reply_text(
        f"Пост будет опубликован {scheduled_datetime}. "
        "Отправьте контент (текст и/или фото).",
        reply_markup=cancel_keyboard(),
    )


async def process_schedule_content(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    text: Optional[str] = None,
    media: Optional[list[dict[str, Any]]] = None,
) -> None:
    pending = context.user_data.get("pending_post")
    if not pending:
        await update.message.reply_text("Сначала выберите канал.")
        return
    channel = pending.get("channel")
    scheduled_for: datetime = pending.get("scheduled_for")
    if not scheduled_for:
        await update.message.reply_text("Не указана дата и время.")
        return
    if scheduled_for < datetime.now():
        await update.message.reply_text("Дата должна быть в будущем.")
        return
    if not text and not media:
        await update.message.reply_text("Нужен текст или фото для публикации.")
        return
    db: Database = context.application.bot_data["db"]
    await db.schedule_post(
        channel_id=channel["id"],
        user_id=pending.get("user_id"),
        text=text,
        media=media,
        scheduled_for=scheduled_for,
    )
    await update.message.reply_text(
        f"Пост запланирован на {scheduled_for}.", reply_markup=get_main_keyboard(
            await db.get_user(update.effective_user.id)
        )
    )
    context.user_data.clear()
    context.user_data["state"] = STATE_IDLE


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return
    if message.media_group_id:
        await _buffer_media_group(update, context)
        return
    state = context.user_data.get("state")
    media = build_media_payload(message)
    if state == STATE_POST_CONTENT:
        await process_post_content(update, context, text=message.caption, media=media)
    elif state == STATE_SCHEDULE_CONTENT:
        await process_schedule_content(update, context, text=message.caption, media=media)
    else:
        await message.reply_text("Отправьте команду из меню перед загрузкой медиа.")


async def _buffer_media_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.media_group_id:
        return
    cache = context.chat_data.setdefault(ALBUM_CACHE_KEY, {})
    entry = cache.setdefault(
        message.media_group_id,
        {"media": [], "caption": None, "task": None, "state": None},
    )
    entry["media"].extend(build_media_payload(message))
    if message.caption:
        entry["caption"] = message.caption
    entry["state"] = context.user_data.get("state")
    task: asyncio.Task | None = entry.get("task")
    if task:
        task.cancel()
    entry["task"] = context.application.create_task(
        _finalize_media_group(update, context, message.media_group_id)
    )


async def _finalize_media_group(
    update: Update, context: ContextTypes.DEFAULT_TYPE, media_group_id: str
) -> None:
    try:
        await asyncio.sleep(ALBUM_FLUSH_DELAY)
    except asyncio.CancelledError:
        return
    cache = context.chat_data.get(ALBUM_CACHE_KEY, {})
    entry = cache.pop(media_group_id, None)
    if not entry:
        return
    state = entry.get("state")
    caption = entry.get("caption")
    media = entry.get("media", [])
    if state == STATE_POST_CONTENT:
        await process_post_content(update, context, text=caption, media=media)
    elif state == STATE_SCHEDULE_CONTENT:
        await process_schedule_content(update, context, text=caption, media=media)
    else:
        await update.message.reply_text(
            "Отправьте команду из меню перед загрузкой медиа."
        )


async def start_user_management(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    db: Database = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user or not user.get("is_admin"):
        await update.message.reply_text("Доступно только администраторам.")
        return
    pending = await db.list_pending_users()
    if not pending:
        await update.message.reply_text("Нет ожидающих пользователей.")
        return
    context.user_data["state"] = STATE_MANAGE_USERS
    await update.message.reply_text(
        "Нажмите на ID пользователя для одобрения или '🚫 Отклонить' "
        "и укажите ID в следующем сообщении.",
        reply_markup=manage_users_keyboard(pending),
    )


async def finalize_user_approval(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    db: Database = context.application.bot_data["db"]
    if text.startswith("✅"):
        telegram_id = int(text.split("✅")[1].strip())
        await db.approve_user(telegram_id, True)
        await db.grant_all_channels(telegram_id)
        await update.message.reply_text(f"Пользователь {telegram_id} одобрен.")
    elif text.startswith("🚫"):
        await update.message.reply_text("Укажите ID пользователя после 🚫.")
    else:
        try:
            telegram_id = int(text)
        except ValueError:
            await update.message.reply_text("Введите числовой ID.")
            return
        await db.approve_user(telegram_id, False)
        await update.message.reply_text(f"Пользователь {telegram_id} возвращен в ожидание.")
    context.user_data["state"] = STATE_IDLE


async def start_admin_management(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    db: Database = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user or not user.get("is_admin"):
        await update.message.reply_text("Недостаточно прав.")
        return
    users = await db.list_users()
    context.user_data["state"] = STATE_MANAGE_ADMINS
    await update.message.reply_text(
        "Выберите пользователя для переключения прав администратора.",
        reply_markup=manage_admins_keyboard(users),
    )


async def finalize_admin_toggle(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    db: Database = context.application.bot_data["db"]
    try:
        telegram_id = int(text.split()[-1])
    except (ValueError, IndexError):
        await update.message.reply_text("Не удалось определить ID.")
        return
    user = await db.get_user(telegram_id)
    if not user:
        await update.message.reply_text("Пользователь не найден.")
        return
    if user["is_admin"]:
        admins = [u for u in await db.list_users() if u["is_admin"]]
        if len(admins) == 1:
            await update.message.reply_text("Нельзя удалить последнего администратора.")
            return
    await db.set_admin(telegram_id, not user["is_admin"])
    await update.message.reply_text(
        f"Пользователь {telegram_id} теперь "
        f"{'администратор' if not user['is_admin'] else 'пользователь'}."
    )
    context.user_data["state"] = STATE_IDLE


async def start_channel_management(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    db: Database = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user or not user.get("is_admin"):
        await update.message.reply_text("Недостаточно прав.")
        return
    context.user_data["state"] = STATE_IDLE
    await update.message.reply_text(
        "Выберите действие с каналами.", reply_markup=channel_management_keyboard()
    )


async def start_channel_addition(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    db: Database = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user or not user.get("is_admin"):
        await update.message.reply_text("Недостаточно прав.")
        return
    context.user_data["channel"] = {}
    context.user_data["state"] = STATE_CHANNEL_ADD_NAME
    await update.message.reply_text(
        "Введите название канала.", reply_markup=cancel_keyboard()
    )


async def start_channel_toggle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    deactivate: bool,
) -> None:
    db: Database = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user or not user.get("is_admin"):
        await update.message.reply_text("Недостаточно прав.")
        return
    if deactivate:
        channels = await db.list_channels(active_only=True)
    else:
        channels = [c for c in await db.list_channels(active_only=False) if not c["is_active"]]
    if not channels:
        await update.message.reply_text(
            "Нет каналов для изменения статуса.", reply_markup=get_main_keyboard(user)
        )
        return
    selection_state = (
        STATE_CHANNEL_DEACTIVATE if deactivate else STATE_CHANNEL_ACTIVATE
    )
    context.user_data["state"] = selection_state
    await update.message.reply_text(
        "Выберите канал из списка.",
        reply_markup=channel_selection_keyboard(channels),
    )


async def finalize_channel_creation(
    update: Update, context: ContextTypes.DEFAULT_TYPE, vk_group_id: str
) -> None:
    db: Database = context.application.bot_data["db"]
    channel_payload = context.user_data.get("channel", {})
    channel_payload["vk_group_id"] = vk_group_id
    record = await db.add_channel(
        channel_payload["name"],
        channel_payload["telegram_channel"],
        channel_payload["vk_group_id"],
    )
    await db.grant_channel_to_all(record["id"])
    await update.message.reply_text(f"Канал {record['name']} добавлен и активирован.")
    context.user_data["state"] = STATE_IDLE
    context.user_data.pop("channel", None)


async def finalize_channel_toggle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    deactivate: bool,
) -> None:
    db: Database = context.application.bot_data["db"]
    try:
        channel_id = int(text.split("#")[-1].rstrip(")")) if "(#" in text else int(text)
    except ValueError:
        await update.message.reply_text("Введите корректный ID канала.")
        return
    await db.deactivate_channel(channel_id, active=not deactivate)
    await update.message.reply_text(
        f"Канал {'деактивирован' if deactivate else 'активирован'}."
    )
    context.user_data["state"] = STATE_IDLE


async def finalize_token_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    vk_client: VKClient = context.application.bot_data["vk_client"]
    token = extract_token_from_url(text) or text.strip()
    if not token:
        await update.message.reply_text("Не удалось определить токен.")
        return
    await asyncio.to_thread(vk_client.update_token, token)
    if await asyncio.to_thread(vk_client.validate):
        await update.message.reply_text("VK токен обновлен.")
    else:
        await update.message.reply_text("Токен сохранен, но проверка провалена.")
    context.user_data["state"] = STATE_IDLE


async def post_init(application) -> None:
    settings: Settings = application.bot_data["settings"]
    db: Database = application.bot_data["db"]
    await db.connect()
    scheduler = ScheduledPostWorker(
        db=db, vk_client=application.bot_data["vk_client"], bot=application.bot
    )
    scheduler.start()
    application.bot_data["scheduler"] = scheduler
    application.bot_data["flask_thread"] = start_flask_server(settings.flask_port)
    if settings.render and settings.self_ping_url:
        task = asyncio.create_task(self_ping_loop(settings.self_ping_url))
        application.bot_data["self_ping_task"] = task


async def post_shutdown(application) -> None:
    scheduler: ScheduledPostWorker = application.bot_data.get("scheduler")
    if scheduler:
        await scheduler.stop()
    task: asyncio.Task | None = application.bot_data.get("self_ping_task")
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    db: Database = application.bot_data["db"]
    await db.close()


def build_application(settings: Settings) -> Any:
    application = (
        ApplicationBuilder()
        .token(settings.telegram_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    return application


def register_handlers(application) -> None:
    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("menu", handle_menu))
    application.add_handler(CommandHandler("hide", handle_hide))
    application.add_handler(CommandHandler("status", handle_status))
    application.add_handler(CommandHandler("get_token", handle_get_token))
    application.add_handler(CommandHandler("update_token", handle_update_token))
    application.add_handler(CommandHandler("stop", handle_stop))
    application.add_handler(
        MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, handle_photo)
    )
    application.add_handler(
        MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_text)
    )


def main() -> None:
    settings = Settings.load()
    db = Database(settings.database_url)
    vk_client = VKClient(settings.vk_token)
    application = build_application(settings)
    application.bot_data["settings"] = settings
    application.bot_data["db"] = db
    application.bot_data["vk_client"] = vk_client
    register_handlers(application)
    LOGGER.info("Starting bot...")
    application.run_polling(close_loop=False)


if __name__ == "__main__":
    main()


