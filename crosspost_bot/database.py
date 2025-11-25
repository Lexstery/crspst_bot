from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Iterable, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from psycopg.types.json import Json


LOGGER = logging.getLogger(__name__)


class Database:
    """Async helper around psycopg connection pool."""

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: Optional[AsyncConnectionPool] = None

    async def connect(self) -> None:
        if self._pool:
            return
        self._pool = AsyncConnectionPool(
            conninfo=self._dsn,
            min_size=1,
            max_size=10,
            num_workers=3,
            kwargs={"autocommit": True},
        )
        await self.create_tables()
        LOGGER.info("Connected to PostgreSQL")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def connection(self):
        if not self._pool:
            raise RuntimeError("Database pool is not initialized")
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                yield cursor

    async def execute(
        self,
        query: str,
        params: Optional[Iterable[Any]] = None,
        *,
        fetchone: bool = False,
        fetchall: bool = False,
    ) -> Any:
        async with self.connection() as cursor:
            await cursor.execute(query, params or ())
            if fetchone:
                return await cursor.fetchone()
            if fetchall:
                return await cursor.fetchall()
            return None

    async def create_tables(self) -> None:
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_admin BOOLEAN DEFAULT FALSE,
                is_approved BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS channels (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                telegram_channel TEXT NOT NULL,
                vk_group_id TEXT NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS user_permissions (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT REFERENCES users (telegram_id) ON DELETE CASCADE,
                channel_id INT REFERENCES channels (id) ON DELETE CASCADE,
                UNIQUE (telegram_id, channel_id)
            );
            """
        )
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id SERIAL PRIMARY KEY,
                channel_id INT REFERENCES channels (id) ON DELETE CASCADE,
                user_id BIGINT REFERENCES users (telegram_id) ON DELETE SET NULL,
                text TEXT,
                media JSONB,
                scheduled_for TIMESTAMPTZ NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                sent_at TIMESTAMPTZ
            );
            """
        )

    # User helpers

    async def upsert_user(
        self,
        telegram_id: int,
        username: Optional[str],
        first_name: Optional[str],
        last_name: Optional[str],
    ) -> dict[str, Any]:
        record = await self.execute(
            """
            INSERT INTO users (telegram_id, username, first_name, last_name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (telegram_id)
            DO UPDATE SET username = EXCLUDED.username,
                          first_name = EXCLUDED.first_name,
                          last_name = EXCLUDED.last_name
            RETURNING *;
            """,
            (telegram_id, username, first_name, last_name),
            fetchone=True,
        )
        return record

    async def get_user(self, telegram_id: int) -> Optional[dict[str, Any]]:
        return await self.execute(
            "SELECT * FROM users WHERE telegram_id = %s;",
            (telegram_id,),
            fetchone=True,
        )

    async def list_users(self) -> list[dict[str, Any]]:
        return await self.execute(
            "SELECT * FROM users ORDER BY created_at;",
            fetchall=True,
        )

    async def list_approved_users(self) -> list[dict[str, Any]]:
        return await self.execute(
            "SELECT * FROM users WHERE is_approved = TRUE ORDER BY created_at;",
            fetchall=True,
        )

    async def any_admins(self) -> bool:
        record = await self.execute(
            "SELECT EXISTS (SELECT 1 FROM users WHERE is_admin = TRUE);",
            fetchone=True,
        )
        return bool(record and record["exists"])

    async def list_pending_users(self) -> list[dict[str, Any]]:
        return await self.execute(
            "SELECT * FROM users WHERE is_approved = FALSE ORDER BY created_at;",
            fetchall=True,
        )

    async def approve_user(self, telegram_id: int, approved: bool = True) -> None:
        await self.execute(
            "UPDATE users SET is_approved = %s WHERE telegram_id = %s;",
            (approved, telegram_id),
        )

    async def set_admin(self, telegram_id: int, is_admin: bool) -> None:
        await self.execute(
            "UPDATE users SET is_admin = %s WHERE telegram_id = %s;",
            (is_admin, telegram_id),
        )

    async def delete_user(self, telegram_id: int) -> None:
        await self.execute(
            "DELETE FROM users WHERE telegram_id = %s;",
            (telegram_id,),
        )

    # Channel helpers

    async def add_channel(
        self, name: str, telegram_channel: str, vk_group_id: str
    ) -> dict[str, Any]:
        record = await self.execute(
            """
            INSERT INTO channels (name, telegram_channel, vk_group_id)
            VALUES (%s, %s, %s)
            RETURNING *;
            """,
            (name, telegram_channel, vk_group_id),
            fetchone=True,
        )
        return record

    async def list_channels(self, active_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM channels"
        if active_only:
            query += " WHERE is_active = TRUE"
        query += " ORDER BY name;"
        return await self.execute(query, fetchall=True)

    async def get_channel(self, channel_id: int) -> Optional[dict[str, Any]]:
        return await self.execute(
            "SELECT * FROM channels WHERE id = %s;",
            (channel_id,),
            fetchone=True,
        )

    async def deactivate_channel(self, channel_id: int, active: bool = False) -> None:
        await self.execute(
            "UPDATE channels SET is_active = %s WHERE id = %s;",
            (active, channel_id),
        )

    # Permissions

    async def grant_permissions(self, telegram_id: int, channel_id: int) -> None:
        await self.execute(
            """
            INSERT INTO user_permissions (telegram_id, channel_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING;
            """,
            (telegram_id, channel_id),
        )

    async def revoke_permissions(self, telegram_id: int, channel_id: int) -> None:
        await self.execute(
            "DELETE FROM user_permissions WHERE telegram_id = %s AND channel_id = %s;",
            (telegram_id, channel_id),
        )

    async def list_user_channels(self, telegram_id: int) -> list[dict[str, Any]]:
        return await self.execute(
            """
            SELECT c.*
            FROM channels c
            JOIN user_permissions up ON up.channel_id = c.id
            WHERE up.telegram_id = %s AND c.is_active = TRUE
            ORDER BY c.name;
            """,
            (telegram_id,),
            fetchall=True,
        )

    async def grant_all_channels(self, telegram_id: int) -> None:
        channels = await self.list_channels(active_only=True)
        for channel in channels:
            await self.grant_permissions(telegram_id, channel["id"])

    async def grant_channel_to_all(self, channel_id: int) -> None:
        users = await self.list_approved_users()
        for user in users:
            await self.grant_permissions(user["telegram_id"], channel_id)

    # Scheduled posts

    async def schedule_post(
        self,
        *,
        channel_id: int,
        user_id: int,
        text: Optional[str],
        media: Optional[list[dict[str, Any]]],
        scheduled_for,
    ) -> dict[str, Any]:
        record = await self.execute(
            """
            INSERT INTO scheduled_posts (
                channel_id, user_id, text, media, scheduled_for
            )
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *;
            """,
            (channel_id, user_id, text, Json(media), scheduled_for),
            fetchone=True,
        )
        return record

    async def due_posts(self) -> list[dict[str, Any]]:
        return await self.execute(
            """
            SELECT sp.*, c.telegram_channel, c.vk_group_id
            FROM scheduled_posts sp
            JOIN channels c ON sp.channel_id = c.id
            WHERE sp.status = 'pending' AND sp.scheduled_for <= NOW()
            ORDER BY sp.scheduled_for
            LIMIT 25;
            """,
            fetchall=True,
        )

    async def mark_post_sent(self, post_id: int, status: str = "sent") -> None:
        await self.execute(
            """
            UPDATE scheduled_posts
            SET status = %s, sent_at = NOW()
            WHERE id = %s;
            """,
            (status, post_id),
        )


