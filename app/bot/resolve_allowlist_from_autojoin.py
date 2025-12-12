import ast
import logging
import os
from pathlib import Path
from typing import Any, Set

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Channel

# Для локального запуска подтянем .env
try:
    from dotenv import load_dotenv

    load_dotenv(".env")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app.bot.resolve_allowlist")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"


def _parse_autojoin_raw(raw: str) -> list[Any]:
    """
    Пытаемся распарсить AUTOJOIN_CHAT_IDS в несколько форматов:
    1) Нормальный Python-список: ['https://...', '@chan', -100123...]
    2) Простой CSV: https://...,@chan,-100123...
    """
    raw = raw.strip()
    if not raw:
        return []

    # Сначала пробуем как нормальный литерал списка
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, (list, tuple, set)):
            return [x for x in parsed if str(x).strip()]
    except Exception:
        pass

    # Фоллбек: разбираем как CSV, убирая скобки
    cleaned = raw.strip()
    # убираем ведущие/замыкающие скобки, если есть
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1]

    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    return parts


async def main() -> None:
    api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    session_str = os.getenv("TELEGRAM_SESSION_STRING", "")

    if not api_id or not api_hash or not session_str:
        raise RuntimeError("TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_SESSION_STRING not set")

    raw = (os.getenv("AUTOJOIN_CHAT_IDS") or "").strip()
    if not raw:
        print("AUTOJOIN_CHAT_IDS is empty, nothing to resolve")
        return

    refs = _parse_autojoin_raw(raw)
    if not refs:
        raise RuntimeError(f"Failed to parse AUTOJOIN_CHAT_IDS='{raw}' into a non-empty list")

    print(f"Parsed AUTOJOIN_CHAT_IDS -> {refs}")

    unique_ids: Set[int] = set()

    async with TelegramClient(StringSession(session_str), api_id, api_hash) as client:
        # Резолвим каждый канал/линк
        for ref in refs:
            ref_str = str(ref).strip()
            if not ref_str:
                continue
            try:
                ent = await client.get_entity(ref_str)
            except Exception as e:
                logger.warning("RESOLVE: failed to get entity for %s: %s", ref_str, e)
                continue

            if not isinstance(ent, Channel):
                logger.info("RESOLVE: entity %s is not a Channel, skip", ref_str)
                continue

            chan_id = int(ent.id)
            unique_ids.add(chan_id)
            logger.info("RESOLVE: channel '%s' -> id=%s", getattr(ent, "title", ref_str), chan_id)

            # Если у канала есть чат обсуждений, добавим и его id
            linked_id = getattr(ent, "linked_chat_id", None)
            if linked_id:
                try:
                    discussion = await client.get_entity(linked_id)
                    if isinstance(discussion, Channel):
                        d_id = int(discussion.id)
                        unique_ids.add(d_id)
                        logger.info(
                            "RESOLVE: discussion '%s' -> id=%s",
                            getattr(discussion, "title", linked_id),
                            d_id,
                        )
                except Exception as e:
                    logger.warning(
                        "RESOLVE: failed to get discussion entity for linked_chat_id=%s: %s",
                        linked_id,
                        e,
                    )

    if not unique_ids:
        print("No channel IDs resolved, nothing to update")
        return

    # Приведём к привычному виду: [-100..., -100...], отсортируем
    sorted_ids = sorted(unique_ids)
    allowlist_literal = "[" + ", ".join(str(i) for i in sorted_ids) + "]"

    print("\nResolved ALLOWLIST_CHAT_IDS value:")
    print(allowlist_literal)
    print("\nYou can paste this into .env as:")
    print(f"ALLOWLIST_CHAT_IDS={allowlist_literal}\n")

    # Если хочешь автообновление .env – раскомментируй ниже
    # _update_env_allowlist(ENV_PATH, allowlist_literal)
    # print(f".env updated with ALLOWLIST_CHAT_IDS={allowlist_literal}")


def _update_env_allowlist(env_path: Path, allowlist_literal: str) -> None:
    """Простейшее обновление/вставка строки ALLOWLIST_CHAT_IDS=... в .env."""
    if not env_path.exists():
        raise FileNotFoundError(f"{env_path} not found")

    lines = env_path.read_text(encoding="utf-8").splitlines()
    out_lines = []
    replaced = False
    for line in lines:
        if line.strip().startswith("ALLOWLIST_CHAT_IDS="):
            out_lines.append(f"ALLOWLIST_CHAT_IDS={allowlist_literal}")
            replaced = True
        else:
            out_lines.append(line)
    if not replaced:
        out_lines.append(f"ALLOWLIST_CHAT_IDS={allowlist_literal}")
    env_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
