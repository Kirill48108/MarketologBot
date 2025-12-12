from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

SessionFactory = Callable[[], Any]


@dataclass
class ChannelStatusRow:
    chat_id: int
    status: str
    error_count_recent: int
    last_error_type: str | None
    last_error_at: datetime | None


@dataclass
class LinkStatsRow:
    slug: str
    target_url: str
    clicks: int


@dataclass
class StatsOverview:
    bot_name: str
    channels_total: int
    channels_banned: int
    channels_flood_limited: int
    messages_last_24h: int
    messages_last_7d: int
    links_total_clicks: int


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------- Вспомогательные функции для репозитория ----------


def get_channel_stats(session_factory: SessionFactory, bot_name: str) -> list[ChannelStatusRow]:
    """
    Вернёт список статусов каналов для данного бота из таблицы channel_status.
    """
    from sqlalchemy import text as sa_text

    items: list[ChannelStatusRow] = []
    with session_factory() as s:
        res = s.execute(
            sa_text(
                """
                SELECT chat_id, status, error_count_recent, last_error_type, last_error_at
                FROM channel_status
                WHERE bot_name = :bot_name
                ORDER BY chat_id
                """
            ),
            {"bot_name": bot_name},
        )
        for row in res:
            items.append(
                ChannelStatusRow(
                    chat_id=int(row[0]),
                    status=str(row[1]),
                    error_count_recent=int(row[2]),
                    last_error_type=row[3],
                    last_error_at=row[4],
                )
            )
    return items


def get_messages_count(
    session_factory: SessionFactory,
    bot_name: str,
    since: datetime,
) -> int:
    from sqlalchemy import text as sa_text

    with session_factory() as s:
        res = s.execute(
            sa_text(
                """
                SELECT COUNT(*)
                FROM message_log
                WHERE created_at >= :since
                  AND bot_name = :bot_name
                """
            ),
            {"since": since, "bot_name": bot_name},
        )
        return int(res.scalar_one() or 0)


def get_links_stats(session_factory: SessionFactory) -> list[LinkStatsRow]:
    from sqlalchemy import text as sa_text

    items: list[LinkStatsRow] = []
    with session_factory() as s:
        res = s.execute(
            sa_text(
                """
                SELECT slug, target_url, clicks
                FROM link_stat
                ORDER BY slug
                """
            )
        )
        for row in res:
            items.append(
                LinkStatsRow(
                    slug=str(row[0]),
                    target_url=str(row[1]),
                    clicks=int(row[2] or 0),
                )
            )
    return items


def get_stats_overview(session_factory: SessionFactory, bot_name: str) -> StatsOverview:
    """
    Сводная статистика для /admin/stats/overview.
    """
    now = _now_utc()
    last_24h = now - timedelta(hours=24)
    last_7d = now - timedelta(days=7)

    channels = get_channel_stats(session_factory, bot_name)
    links = get_links_stats(session_factory)

    channels_total = len(channels)
    channels_banned = sum(1 for c in channels if c.status == "banned_local")
    channels_flood_limited = sum(1 for c in channels if c.status == "flood_limited")

    messages_last_24h = get_messages_count(session_factory, bot_name, since=last_24h)
    messages_last_7d = get_messages_count(session_factory, bot_name, since=last_7d)

    links_total_clicks = sum(link.clicks for link in links)

    return StatsOverview(
        bot_name=bot_name,
        channels_total=channels_total,
        channels_banned=channels_banned,
        channels_flood_limited=channels_flood_limited,
        messages_last_24h=messages_last_24h,
        messages_last_7d=messages_last_7d,
        links_total_clicks=links_total_clicks,
    )


# ---------- (опционально) функции для обновления статусов каналов ----------


def register_channel_error(
    session_factory: SessionFactory,
    bot_name: str,
    chat_id: int,
    error_type: str,
    threshold: int = 3,
) -> None:
    """
    Зарегистрировать серьёзную ошибку по каналу.
    Увеличивает error_count_recent, обновляет last_error_*.
    При threshold и выше может ставить status='banned_local' или 'flood_limited' (на твой вкус).
    """
    from sqlalchemy import text as sa_text

    now = _now_utc()

    with session_factory() as s:
        # SELECT + INSERT/UPDATE наивно, без upsert — для простоты.
        res = s.execute(
            sa_text(
                """
                SELECT id, error_count_recent, status
                FROM channel_status
                WHERE bot_name = :bot_name AND chat_id = :chat_id
                """
            ),
            {"bot_name": bot_name, "chat_id": chat_id},
        ).first()

        if res is None:
            new_count = 1
            new_status = "ok"
            if new_count >= threshold:
                new_status = "banned_local"

            s.execute(
                sa_text(
                    """
                    INSERT INTO channel_status (
                        bot_name, chat_id, status, last_error_type, last_error_at, error_count_recent
                    ) VALUES (
                                 :bot_name, :chat_id, :status, :last_error_type, :last_error_at, :error_count_recent
                             )
                    """
                ),
                {
                    "bot_name": bot_name,
                    "chat_id": chat_id,
                    "status": new_status,
                    "last_error_type": error_type,
                    "last_error_at": now,
                    "error_count_recent": new_count,
                },
            )
        else:
            row_id = res[0]
            old_count = int(res[1] or 0)
            old_status = str(res[2] or "ok")

            new_count = old_count + 1
            new_status = old_status
            if new_count >= threshold and old_status == "ok":
                new_status = "banned_local"

            s.execute(
                sa_text(
                    """
                    UPDATE channel_status
                    SET status = :status,
                        last_error_type = :last_error_type,
                        last_error_at = :last_error_at,
                        error_count_recent = :error_count_recent
                    WHERE id = :id
                    """
                ),
                {
                    "id": row_id,
                    "status": new_status,
                    "last_error_type": error_type,
                    "last_error_at": now,
                    "error_count_recent": new_count,
                },
            )
        s.commit()


def reset_channel_error_counter(
    session_factory: SessionFactory,
    bot_name: str,
    chat_id: int,
) -> None:
    """
    Сбросить счётчик ошибок и статус канала в 'ok'.
    Вызывается, например, при успешной отправке сообщения.
    """
    from sqlalchemy import text as sa_text

    with session_factory() as s:
        s.execute(
            sa_text(
                """
                UPDATE channel_status
                SET status = 'ok',
                    error_count_recent = 0
                WHERE bot_name = :bot_name AND chat_id = :chat_id
                """
            ),
            {"bot_name": bot_name, "chat_id": chat_id},
        )
        s.commit()
