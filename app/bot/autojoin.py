import asyncio
import logging
from typing import Any, Iterable, Set

from telethon import TelegramClient
from telethon.errors import FloodWaitError, UserAlreadyParticipantError
from telethon.tl.functions.channels import GetFullChannelRequest, JoinChannelRequest
from telethon.tl.types import Channel

logger = logging.getLogger("app.bot.autojoin")


async def _join_channel_and_discussion(
    client: TelegramClient,
    channel_ref: Any,
) -> None:
    """
    Подписаться на канал по ссылке/@username/ID и, если у него есть чат обсуждений,
    подписаться и на него.
    """
    try:
        entity = await client.get_entity(channel_ref)
    except Exception as e:
        logger.warning("AUTOJOIN: failed to get entity for %s: %s", channel_ref, e)
        return

    if not isinstance(entity, Channel):
        logger.info("AUTOJOIN: entity %s is not a Channel, skip", channel_ref)
        return

    # 1) подписываемся на канал
    try:
        await client(JoinChannelRequest(entity))
        logger.info(
            "AUTOJOIN: joined channel '%s' (id=%s)",
            getattr(entity, "title", channel_ref),
            getattr(entity, "id", channel_ref),
        )
    except UserAlreadyParticipantError:
        logger.info("AUTOJOIN: already participant of channel %s", channel_ref)
    except FloodWaitError as e:
        logger.warning(
            "AUTOJOIN: FloodWait while joining channel %s, seconds=%s",
            channel_ref,
            getattr(e, "seconds", None),
        )
        raise
    except Exception as e:
        logger.warning("AUTOJOIN: failed to join channel %s: %s", channel_ref, e)

    # 2) Получаем linked_chat_id НАДЁЖНО через FullChannel
    linked_id = None
    try:
        full = await client(GetFullChannelRequest(entity))
        linked_id = getattr(getattr(full, "full_chat", None), "linked_chat_id", None)
        logger.info(
            "AUTOJOIN: channel %s linked_chat_id=%s", getattr(entity, "id", channel_ref), linked_id
        )
    except Exception as e:
        logger.warning("AUTOJOIN: failed to load full channel for %s: %s", channel_ref, e)
        linked_id = getattr(entity, "linked_chat_id", None)

    if not linked_id:
        return

    try:
        discussion = await client.get_entity(linked_id)
    except Exception as e:
        logger.warning(
            "AUTOJOIN: failed to get discussion entity for linked_chat_id=%s (channel %s): %s",
            linked_id,
            channel_ref,
            e,
        )
        return

    try:
        await client(JoinChannelRequest(discussion))
        logger.info(
            "AUTOJOIN: joined discussion chat '%s' (id=%s) for channel '%s'",
            getattr(discussion, "title", linked_id),
            getattr(discussion, "id", linked_id),
            channel_ref,
        )
    except UserAlreadyParticipantError:
        logger.info(
            "AUTOJOIN: already participant of discussion chat id=%s (channel %s)",
            linked_id,
            channel_ref,
        )
    except FloodWaitError as e:
        logger.warning(
            "AUTOJOIN: FloodWait while joining discussion id=%s (channel %s), seconds=%s",
            linked_id,
            channel_ref,
            getattr(e, "seconds", None),
        )
        raise
    except Exception as e:
        logger.warning(
            "AUTOJOIN: failed to join discussion chat id=%s for channel %s: %s",
            linked_id,
            channel_ref,
            e,
        )


async def run_autojoin(
    client: TelegramClient,
    channel_ids: Iterable[Any],
    delay_seconds: int,
) -> None:
    """
    Запускает автоподписку:
    - для каждого channel_ref (int/@username/URL):
        * join самого канала,
        * join его чата обсуждений (если есть и доступен),
      потом ждём delay_seconds (например, 300 секунд).
    - При FloodWait ждём указанное API время и продолжаем со следующим каналом.
    """
    # Уберём дубликаты и отсортируем, чтобы процесс был детерминированным
    unique_refs: Set[str] = set(str(cid).strip() for cid in channel_ids if str(cid).strip())
    if not unique_refs:
        logger.info("AUTOJOIN: no channel ids provided, nothing to do")
        return

    delay_seconds = max(1, int(delay_seconds or 0))
    logger.info(
        "AUTOJOIN: starting auto-join for %s channels with delay=%s seconds",
        len(unique_refs),
        delay_seconds,
    )

    for ref in sorted(unique_refs):
        try:
            await _join_channel_and_discussion(client, ref)
        except FloodWaitError as e:
            # Если Telegram сказал подождать — ждём именно столько
            seconds = int(getattr(e, "seconds", delay_seconds) or delay_seconds)
            logger.warning(
                "AUTOJOIN: FloodWait while processing channel %s, sleep %s sec",
                ref,
                seconds,
            )
            await asyncio.sleep(seconds)

        # Переход к следующему каналу только после паузы
        logger.info(
            "AUTOJOIN: finished processing channel %s, sleeping %s seconds before next",
            ref,
            delay_seconds,
        )
        await asyncio.sleep(delay_seconds)

    logger.info("AUTOJOIN: completed for all channels")
