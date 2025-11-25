from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import InputMediaPhoto
from telegram.error import TelegramError

from .database import Database
from .services.vk_client import VKClient

LOGGER = logging.getLogger(__name__)


class ScheduledPostWorker:
    def __init__(self, *, db: Database, vk_client: VKClient, bot):
        self.db = db
        self.vk_client = vk_client
        self.bot = bot
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if not self._task:
            self._task = asyncio.create_task(self._run(), name="scheduled-post-worker")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            await self._task
            self._task = None

    async def _run(self) -> None:
        LOGGER.info("Scheduled post worker started")
        try:
            while not self._stop_event.is_set():
                posts = await self.db.due_posts()
                for post in posts:
                    try:
                        await self._send_post(post)
                        await self.db.mark_post_sent(post["id"])
                    except Exception:
                        LOGGER.exception("Failed to send scheduled post %s", post["id"])
                        await self.db.mark_post_sent(post["id"], status="failed")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=60)
                except asyncio.TimeoutError:
                    continue
        finally:
            LOGGER.info("Scheduled post worker stopped")

    async def _send_post(self, post: dict[str, Any]) -> None:
        text = post.get("text")
        media = post.get("media") or []
        telegram_channel = post["telegram_channel"]
        vk_group_id = post["vk_group_id"]

        await self._send_to_telegram(telegram_channel, text, media)
        await self._send_to_vk(vk_group_id, text, media)

    async def _send_to_telegram(self, channel: str, text: str | None, media: list) -> None:
        try:
            if media:
                if len(media) == 1:
                    await self.bot.send_photo(
                        chat_id=channel,
                        photo=media[0]["file_id"],
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
                    await self.bot.send_media_group(chat_id=channel, media=group)
            else:
                await self.bot.send_message(chat_id=channel, text=text or "")
        except TelegramError as exc:
            LOGGER.error("Telegram send error: %s", exc)
            raise

    async def _send_to_vk(self, group_id: str, text: str | None, media: list) -> None:
        attachments = None
        if media:
            attachments = []
            for item in media:
                telegram_file = await self.bot.get_file(item["file_id"])
                data = await telegram_file.download_as_bytearray()
                attachments.append(
                    (f"{item.get('file_unique_id', 'photo')}.jpg", bytes(data))
                )
        await asyncio.to_thread(
            self.vk_client.post_to_group,
            group_id=group_id,
            message=text,
            photo_files=attachments,
        )


