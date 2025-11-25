from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


@dataclass(slots=True)
class Settings:
    telegram_token: str
    vk_token: str
    database_url: str
    render: bool = False
    self_ping_url: Optional[str] = None
    flask_port: int = 8000
    timezone: str = "UTC"

    @classmethod
    def load(cls) -> "Settings":
        env_path = Path(__file__).resolve().parents[1] / ".env"
        if env_path.exists():
            load_dotenv(env_path)
        load_dotenv(override=False)

        telegram_token = os.getenv("TELEGRAM_TOKEN")
        vk_token = os.getenv("VK_TOKEN")
        database_url = os.getenv("DATABASE_URL")

        missing = [name for name, value in
                   (("TELEGRAM_TOKEN", telegram_token),
                    ("VK_TOKEN", vk_token),
                    ("DATABASE_URL", database_url))
                   if not value]
        if missing:
            raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

        return cls(
            telegram_token=telegram_token,
            vk_token=vk_token,
            database_url=database_url,
            render=os.getenv("RENDER", "false").lower() == "true",
            self_ping_url=os.getenv("SELF_PING_URL")
            or os.getenv("RENDER_EXTERNAL_URL"),
            flask_port=int(os.getenv("PORT", "8000")),
            timezone=os.getenv("TIMEZONE", "Europe/Moscow"),
        )


