import ast
import asyncio
import logging
import os

from telethon import TelegramClient
from telethon.sessions import StringSession

from app.bot.autojoin import run_autojoin

# Попробуем подхватить .env автоматически (для локальной разработки)
try:
    from dotenv import load_dotenv

    # Путь к .env в корне проекта; при запуске из корня это просто ".env"
    load_dotenv(".env")
except Exception:
    # Если dotenv не установлен — просто идём дальше, будем надеяться на уже выставленные ENV
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def main() -> None:
    api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    session_str = os.getenv("TELEGRAM_SESSION_STRING", "")

    if not api_id or not api_hash or not session_str:
        raise RuntimeError("TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_SESSION_STRING not set")

    raw = (os.getenv("AUTOJOIN_CHAT_IDS") or "").strip()
    if not raw:
        print("AUTOJOIN_CHAT_IDS is empty, nothing to do")
        return

    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, (list, tuple, set)):
            channel_ids = list(parsed)  # здесь могут быть и int, и str (@username)
        else:
            raise ValueError("AUTOJOIN_CHAT_IDS must be a list-like literal")
    except Exception as e:
        raise RuntimeError(f"Failed to parse AUTOJOIN_CHAT_IDS='{raw}': {e}") from e

    delay = int(os.getenv("AUTOJOIN_DELAY_SECONDS", "300") or "300")

    async with TelegramClient(StringSession(session_str), api_id, api_hash) as client:
        print(f"Running autojoin for {len(channel_ids)} channels, delay={delay} sec")
        await run_autojoin(client, channel_ids, delay_seconds=delay)
        print("Autojoin finished")


if __name__ == "__main__":
    asyncio.run(main())
