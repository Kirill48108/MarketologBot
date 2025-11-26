import asyncio
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Set, Any

import socks
from sqlalchemy.orm import Session, sessionmaker
from telethon import TelegramClient, events
from telethon.errors import (
    ChatWriteForbiddenError,
    FloodWaitError,
    PeerFloodError,
    UserBannedInChannelError,
)
from telethon.sessions import StringSession

from app.metrics import (
    cache_hits,
    cache_misses,
    generation_latency,
    messages_generated,
    messages_sent,
    send_failures,
    send_latency,
)
from app.services.cache import AsyncTTLCache
from app.services.llm import LLMClient
from app.storage.repository import add_message_log

logger = logging.getLogger("app.bot.userbot")


class UserBot:
    """
    UserBot — асинхронный Telegram-«пользователь», который:
    - авторизуется под обычным аккаунтом (Telethon + StringSession);
    - слушает новые посты в allowlist-каналах и при необходимости оставляет instant-комментарий;
    - по расписанию (scheduler) обходит allowlist и пишет осмысленные комментарии к свежим постам;
    - использует LLMClient для генерации текста;
    - хранит журнал сообщений в БД и использует кэш для экономии LLM-запросов.
    """

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_string: Optional[str],
        llm: LLMClient,
        allowlist: Set[int],
        messages_per_hour: int,
        min_interval_global: int,
        min_interval_per_chat: int,
        cache: AsyncTTLCache,
        db: sessionmaker[Session],
        fresh_post_max_age_minutes: int = 30,
    ):
        # Настройки прокси из окружения (опционально)
        proxy = None
        proxy_host = os.getenv("TG_PROXY_HOST")
        proxy_port = os.getenv("TG_PROXY_PORT")
        proxy_user = os.getenv("TG_PROXY_USER")
        proxy_pass = os.getenv("TG_PROXY_PASS")
        if proxy_host and proxy_port:
            try:
                proxy = (
                    socks.SOCKS5,
                    proxy_host,
                    int(proxy_port),
                    True if (proxy_user or proxy_pass) else False,
                    proxy_user or None,
                    proxy_pass or None,
                )
                logger.info(
                    "Using SOCKS5 proxy for TelegramClient host=%s port=%s user=%s",
                    proxy_host,
                    proxy_port,
                    "set" if proxy_user else "none",
                )
            except Exception as e:
                logger.warning("Failed to configure proxy, continue without it: %s", e)
                proxy = None

        self.client = TelegramClient(
            StringSession(session_string) if session_string else "anon",
            api_id,
            api_hash,
            proxy=proxy,
        )
        self.llm = llm
        # allowlist храним как список (для random.choice)
        self.allowlist = list(allowlist)
        self.messages_per_hour = messages_per_hour
        self.min_interval_global = min_interval_global
        self.min_interval_per_chat = min_interval_per_chat
        self.cache = cache
        self.db = db
        self.fresh_post_max_age_minutes = fresh_post_max_age_minutes

        self._enabled = True
        self._runner_task: Optional[asyncio.Task[None]] = None
        self._last_sent_global = 0.0
        self._last_sent_per_chat: Dict[int, float] = {}
        self._last_text_per_chat: Dict[int, str] = {}
        self._last_chat_id: Optional[int] = None
        self._last_commented_post_per_channel: Dict[int, int] = {}
        # Каналы, где аккаунт забанен на отправку сообщений
        self._banned_chats: Set[int] = set()

        self._llm_min_interval = 120.0  # кулдаун только для планировщика
        self._last_llm_call_ts = 0.0
        self._llm_lock = asyncio.Lock()

        self._self_id: Optional[int] = None

        # Задержка для мгновенного комментария (секунды), по умолчанию 0
        try:
            self.instant_delay_seconds = int(
                (asyncio.get_running_loop().get_debug() and 0)
                or int(os.getenv("INSTANT_COMMENT_DELAY_SECONDS", "0"))
            )
        except Exception:
            import os as _os

            try:
                self.instant_delay_seconds = int(_os.getenv("INSTANT_COMMENT_DELAY_SECONDS", "0"))
            except Exception:
                self.instant_delay_seconds = 0

        # Активные временные окна (часы локального времени) — напр. "5-10,18-24"
        self._active_windows = self._parse_active_windows(os.getenv("ACTIVE_WINDOWS", "5-10,18-24"))

        # Суточный лимит сообщений на аккаунт
        self._max_messages_per_day = int(os.getenv("MESSAGES_PER_DAY", "200"))
        self._daily_sent_count = 0
        self._daily_counter_date = datetime.now(timezone.utc).date()

        # Лимиты по окнам: если ровно два окна (утро/вечер), каждое получает <= половины дня
        if len(self._active_windows) == 2:
            self._max_per_window = max(1, self._max_messages_per_day // 2)
        else:
            self._max_per_window = self._max_messages_per_day
        self._per_window_sent: Dict[int, int] = {}

        # История instant-комментариев по каналам (монотонное время, сек)
        self._instant_history_per_chat: Dict[int, list[float]] = {}
        self._max_instant_per_chat_per_hour = int(os.getenv("MAX_INSTANT_PER_CHAT_PER_HOUR", "3"))

        # Анти‑спамблок (глобальный на аккаунт)
        self._spamblock_min_cooldown = int(os.getenv("SPAMBLOCK_MIN_COOLDOWN_SECONDS", "3600"))
        self._spamblock_max_cooldown = int(os.getenv("SPAMBLOCK_MAX_COOLDOWN_SECONDS", "86400"))
        self._spamblock_error_threshold = int(os.getenv("SPAMBLOCK_ERROR_THRESHOLD", "3"))
        self._spam_block_until_ts: float = 0.0  # time.monotonic(), до какого момента не шлём вообще
        self._spam_errors_recent: int = 0  # подряд серьёзных ошибок отправки

    def enable(self)-> None:
        self._enabled = True

    def disable(self)-> None:
        self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    # -------------------------------------------------------------------------
    # Вспомогательные методы лимитов/окон
    # -------------------------------------------------------------------------

    def _parse_active_windows(self, spec: str) -> list[tuple[int, int]]:
        """
        Парсит строку вида "5-10,18-24" в список окон [(5,10), (18,24)].
        Часы в 0–24, правая граница не включительно.
        """
        windows: list[tuple[int, int]] = []
        for part in (spec or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                a, b = part.split("-", 1)
                start = max(0, min(24, int(a)))
                end = max(0, min(24, int(b)))
                if end <= start:
                    continue
                windows.append((start, end))
            except Exception:
                continue
        return windows

    def _current_window_index(self, now: Optional[datetime] = None) -> Optional[int]:
        """
        Возвращает индекс активного окна для текущего локального времени или None.
        """
        if not self._active_windows:
            return None
        now = now or datetime.now()
        h = now.hour
        for idx, (start, end) in enumerate(self._active_windows):
            if start <= h < end:
                return idx
        return None

    def _is_within_active_window(self, now: Optional[datetime] = None) -> bool:
        """
        Проверяет, находится ли текущее локальное время внутри заданных окон.
        Если окна не заданы — всегда True.
        """
        if not self._active_windows:
            return True
        return self._current_window_index(now) is not None

    def _reset_daily_counters_if_needed(self) -> None:
        """
        Сбрасывает суточные счётчики, если наступил новый день (UTC).
        """
        today = datetime.now(timezone.utc).date()
        if today != self._daily_counter_date:
            self._daily_counter_date = today
            self._daily_sent_count = 0
            self._per_window_sent.clear()

    def _can_send_more_today(self) -> bool:
        self._reset_daily_counters_if_needed()
        return self._daily_sent_count < self._max_messages_per_day

    def _can_send_more_in_current_window(self) -> bool:
        """
        Проверяет, не превышен ли лимит для текущего окна (утро/вечер).
        Если окна не заданы — считаем, что ограничений по окнам нет.
        """
        self._reset_daily_counters_if_needed()
        idx = self._current_window_index()
        if idx is None:
            return True
        sent = self._per_window_sent.get(idx, 0)
        return sent < self._max_per_window

    def _inc_counters_for_now(self) -> None:
        """
        Увеличивает суточный счётчик и счётчик текущего окна (если есть).
        """
        self._reset_daily_counters_if_needed()
        self._daily_sent_count += 1
        idx = self._current_window_index()
        if idx is not None:
            self._per_window_sent[idx] = self._per_window_sent.get(idx, 0) + 1

    def _register_instant_send(self, chat_id: int) -> None:
        """
        Регистрирует успешный instant-комментарий в истории канала (для лимитов «в час»).
        """
        now_m = time.monotonic()
        hist = self._instant_history_per_chat.get(chat_id, [])
        one_hour_ago = now_m - 3600.0
        hist = [t for t in hist if t >= one_hour_ago]
        hist.append(now_m)
        self._instant_history_per_chat[chat_id] = hist

    def _instant_recent_count(self, chat_id: int) -> int:
        now_m = time.monotonic()
        one_hour_ago = now_m - 3600.0
        hist = self._instant_history_per_chat.get(chat_id, [])
        hist = [t for t in hist if t >= one_hour_ago]
        self._instant_history_per_chat[chat_id] = hist
        return len(hist)

    # -------------------------------------------------------------------------
    # Анти‑спамблок
    # -------------------------------------------------------------------------

    def _is_spamblocked(self) -> bool:
        """
        Проверка, находится ли аккаунт в режиме глобального спамблока.
        """
        return time.monotonic() < self._spam_block_until_ts

    def _maybe_clear_spam_errors(self) -> None:
        """
        Сбрасывает счётчик ошибок, если спамблок уже прошёл.
        """
        if not self._is_spamblocked():
            self._spam_errors_recent = 0

    def _handle_send_error(self, chat_id: int, exc: Exception) -> None:
        """
        Обработка ошибок отправки сообщений:
        - локальные баны/запреты → добавляем чат в _banned_chats;
        - PeerFlood/FloodWait → возможный глобальный спамблок.
        """
        # Локальные запреты писать в чат
        if isinstance(exc, (ChatWriteForbiddenError, UserBannedInChannelError)):
            logger.warning("Write forbidden/banned in chat %s: %s", chat_id, exc)
            self._banned_chats.add(chat_id)
            return

        # Подозрение на спамблок
        if isinstance(exc, PeerFloodError) or isinstance(exc, FloodWaitError):
            self._spam_errors_recent += 1
            flood_seconds = None
            if isinstance(exc, FloodWaitError):
                try:
                    flood_seconds = int(getattr(exc, "seconds", 0) or 0)
                except Exception:
                    flood_seconds = None

            logger.warning(
                "Possible spam/flood for chat %s: %s (recent errors=%s)",
                chat_id,
                exc,
                self._spam_errors_recent,
            )

            if self._spam_errors_recent >= self._spamblock_error_threshold:
                # Выставляем глобальный спамблок
                base = self._spamblock_min_cooldown
                if flood_seconds is not None and flood_seconds > 0:
                    base = max(base, flood_seconds)
                cooldown = min(self._spamblock_max_cooldown, base)
                self._spam_block_until_ts = time.monotonic() + cooldown
                logger.error(
                    "SPAMBLOCK: Account is spamblocked for ~%s seconds (chat_id=%s)",
                    cooldown,
                    chat_id,
                )
            return

        # Любые другие ошибки сюда не считаем как спамблок
        return

    # -------------------------------------------------------------------------
    # Старт/стоп
    # -------------------------------------------------------------------------

    async def start(self)-> None:
        await self.client.start()
        logger.info("Telegram client started")

        try:
            me = await self.client.get_me()
            self._self_id = getattr(me, "id", None)
        except Exception:
            self._self_id = None

        # Прогрев каналов (ускоряет появление апдейтов)
        try:
            for cid in self.allowlist:
                try:
                    await self.client.get_entity(cid)
                except Exception as e:
                    logger.info("Warmup get_entity failed for %s: %s", cid, e)
        except Exception:
            pass

        # Ловим ВСЕ входящие, фильтруем внутри
        try:
            self.client.add_event_handler(
                self._on_new_channel_post,
                events.NewMessage(incoming=True),
            )
        except Exception as e:
            logger.warning("Failed to set channel post handler: %s", e)

        self._runner_task = asyncio.create_task(self._scheduler_loop())
        await self.client.run_until_disconnected()

    async def stop(self)-> None:
        if self._runner_task and not self._runner_task.done():
            self._runner_task.cancel()
        await self.client.disconnect()

    async def _scheduler_loop(self)-> None:
        interval_base = max(2, int(3600 / max(1, self.messages_per_hour)))
        while True:
            try:
                self._reset_daily_counters_if_needed()
                self._maybe_clear_spam_errors()

                if not self._enabled or not self.allowlist:
                    await asyncio.sleep(5)
                    continue

                # Глобальный спамблок
                if self._is_spamblocked():
                    await asyncio.sleep(60)
                    continue

                if not self._is_within_active_window():
                    await asyncio.sleep(60)
                    continue

                if not self._can_send_more_today():
                    logger.info(
                        "Daily limit reached (%s messages), scheduler sleeps until next day",
                        self._max_messages_per_day,
                    )
                    await asyncio.sleep(300)
                    continue

                if not self._can_send_more_in_current_window():
                    logger.info(
                        "Current window limit reached (per-window max=%s), scheduler waits",
                        self._max_per_window,
                    )
                    await asyncio.sleep(120)
                    continue

                await self._tick_send(interval_base)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("Scheduler error: %s", e)
                await asyncio.sleep(5)

    # -------------------------------------------------------------------------
    # Служебные методы выбора чата/комментария
    # -------------------------------------------------------------------------

    async def _pick_chat(self) -> Optional[int]:
        """
        Выбор следующего чата из allowlist с учётом последнего использованного
        и blacklist'а забаненных чатов.
        """
        if not self.allowlist:
            return None
        candidates = [cid for cid in self.allowlist if cid not in self._banned_chats]
        if not candidates:
            return None
        if self._last_chat_id in candidates and len(candidates) > 1:
            candidates.remove(self._last_chat_id)
        return random.choice(candidates)

    async def _choose_user_comment(self, entity: Any, post_id: int) -> Optional[Any]:
        try:
            comments = await self.client.get_messages(entity, reply_to=post_id, limit=30)
            for c in comments:
                uid = getattr(getattr(c, "from_id", None), "user_id", None)
                if uid and uid != self._self_id and (c.message or "").strip():
                    return c
        except Exception:
            return None
        return None

    def _in_allowlist(self, channel_int_id: int) -> bool:
        if channel_int_id in self.allowlist:
            return True
        try:
            neg_form = int(f"-100{channel_int_id}")
            return neg_form in self.allowlist
        except Exception:
            return False

    # -------------------------------------------------------------------------
    # Instant-логика
    # -------------------------------------------------------------------------

    async def _on_new_channel_post(self, event: Any) -> None:
        """
        Обработка новых постов в канале (instant-сценарий).
        """

        try:
            msg = event.message
            if not getattr(msg, "post", False):
                return
            entity = await event.get_chat()
            if not bool(getattr(entity, "broadcast", False)):
                return

            real_id = int(entity.id)

            # Временные окна, суточный/оконный лимиты, глобальный спамблок
            if not self._is_within_active_window():
                return
            if self._is_spamblocked():
                logger.info(
                    "Instant skip (global spamblock) chat_id=%s post_id=%s",
                    real_id,
                    getattr(msg, "id", None),
                )
                return
            if not self._can_send_more_today() or not self._can_send_more_in_current_window():
                return

            if self.allowlist and not self._in_allowlist(real_id):
                return

            last_post_id = msg.id
            post_text = (msg.message or "").strip()

            # проверяем, есть ли доступный чат обсуждений
            ok_commentable = True
            try:
                _probe = await self.client.get_messages(entity, reply_to=last_post_id, limit=1)
            except Exception:
                ok_commentable = False
            if not ok_commentable:
                return

            # возраст поста
            if msg.date.tzinfo is None:
                msg_dt = msg.date.replace(tzinfo=timezone.utc)
            else:
                msg_dt = msg.date.astimezone(timezone.utc)
            age_sec = (datetime.now(timezone.utc) - msg_dt).total_seconds()

            target_comment = await self._choose_user_comment(entity, last_post_id)

            # Лимит instant-комментариев в час на канал
            recent_count = self._instant_recent_count(real_id)
            if recent_count >= self._max_instant_per_chat_per_hour:
                return

            # Динамическая задержка: 5,10,20,40,60 минут
            base_delays = [300, 600, 1200, 2400, 3600]  # сек
            idx = min(recent_count, len(base_delays) - 1)
            target_delay = float(base_delays[idx])

            delay = max(0.0, target_delay - age_sec)
            if delay > 0.0:
                asyncio.create_task(
                    self._instant_with_delay(
                        entity, real_id, msg, post_text, last_post_id, target_comment, delay
                    )
                )
                return

            await self._do_instant(entity, real_id, msg, post_text, last_post_id, target_comment)

        except Exception as e:
            send_failures.inc()
            logger.warning("Instant comment failed: %s", e)

    async def _instant_with_delay(
            self,
            entity: Any,
            real_id: int,
            msg: Any,
            post_text: str,
            last_post_id: int,
            target_comment: Any,
            delay: float,
    ) -> None:
        try:
            await asyncio.sleep(delay)
            await self._do_instant(entity, real_id, msg, post_text, last_post_id, target_comment)
        except Exception as e:
            send_failures.inc()
            logger.warning("Instant comment (delayed) failed: %s", e)

    async def _do_instant(
        self,
        entity: Any,
        real_id: int,
        msg: Any,
        post_text: str,
        last_post_id: int,
        target_comment: Any,
    ) -> None:
        try:
            # Повторная проверка лимитов и спамблока (на случай задержки)
            if not self._is_within_active_window():
                return
            if self._is_spamblocked():
                logger.info(
                    "Instant _do_instant skip (global spamblock) chat_id=%s post_id=%s",
                    real_id,
                    last_post_id,
                )
                return
            if not self._can_send_more_today() or not self._can_send_more_in_current_window():
                return

            logger.info(
                "GEN:instant start (chat_id=%s, post_id=%s, reply_to_comment=%s)",
                real_id,
                last_post_id,
                getattr(target_comment, "id", None),
            )
            async with self._llm_lock:
                with generation_latency.time():
                    text = await self.llm.generate_contextual_message(
                        post_text=post_text,
                        comment_text=(target_comment.message if target_comment else None),
                    )
            logger.info("GEN:instant done")
            if text:
                logger.info("Instant LLM text len=%s preview=%s", len(text), text[:80])

            if not text:
                logger.info("Instant: empty text, retry once")
                async with self._llm_lock:
                    with generation_latency.time():
                        text = await self.llm.generate_contextual_message(
                            post_text=post_text,
                            comment_text=(target_comment.message if target_comment else None),
                        )
                if not text:
                    seed = self.llm.extract_seed_from_post(post_text)
                    logger.info("Instant: contextual empty twice, try random with seed='%s'", seed)
                    async with self._llm_lock:
                        with generation_latency.time():
                            text = await self.llm.generate_random_message(seed_hint=seed)
                if not text:
                    logger.warning("Instant: random also empty, skip")
                    return

            try:
                if target_comment is not None and getattr(target_comment, "chat", None):
                    discussion = target_comment.chat
                    await self.client.send_message(discussion, text, reply_to=target_comment.id)
                    logger.info(
                        "Instant reply to user comment chat_id=%s comment_id=%s: %s",
                        getattr(discussion, "id", None),
                        target_comment.id,
                        (text or "")[:120],
                    )
                else:
                    await self.client.send_message(entity, text, comment_to=last_post_id)
                    logger.info(
                        "Instant comment for new channel post chat_id=%s post_id=%s: %s",
                        real_id,
                        last_post_id,
                        (text or "")[:120],
                    )
            except Exception as send_exc:
                self._handle_send_error(real_id, send_exc)
                send_failures.inc()
                logger.warning(
                    "Instant send failed chat_id=%s post_id=%s: %s", real_id, last_post_id, send_exc
                )
                return

            now_m = time.monotonic()
            self._last_sent_global = now_m
            self._last_sent_per_chat[real_id] = now_m
            self._last_text_per_chat[real_id] = text
            self._last_commented_post_per_channel[real_id] = last_post_id
            messages_sent.inc()

            self._inc_counters_for_now()
            self._register_instant_send(real_id)

            await add_message_log(self.db, chat_id=str(real_id), text=text)
        except Exception as e:
            send_failures.inc()
            logger.warning("Instant comment failed: %s", e)

    # -------------------------------------------------------------------------
    # Планировщик
    # -------------------------------------------------------------------------

    async def _tick_send(self, interval_base: int)-> None:
        now = time.monotonic()

        if not self._can_send_more_today():
            await asyncio.sleep(60)
            return
        if not self._can_send_more_in_current_window():
            await asyncio.sleep(60)
            return
        if self._is_spamblocked():
            await asyncio.sleep(60)
            return

        if now - self._last_sent_global < self.min_interval_global:
            await asyncio.sleep(1)
            return

        chat_id = await self._pick_chat()
        if chat_id is None:
            await asyncio.sleep(2)
            return

        if now - self._last_sent_per_chat.get(chat_id, 0.0) < self.min_interval_per_chat:
            await asyncio.sleep(1)
            return

        try:
            entity = await self.client.get_entity(chat_id)
            is_channel = bool(getattr(entity, "broadcast", False))
            last_post_id: Optional[int] = None
            post_text: str = ""
            if is_channel:
                msgs = await self.client.get_messages(entity, limit=20)
                candidate = None
                for m in msgs:
                    if getattr(m, "post", False):
                        rep = getattr(m, "replies", None)
                        if rep and bool(getattr(rep, "comments", False)):
                            candidate = m
                            break
                if candidate:
                    ok_commentable = True
                    try:
                        _probe = await self.client.get_messages(
                            entity, reply_to=candidate.id, limit=1
                        )
                    except Exception:
                        ok_commentable = False
                    if ok_commentable:
                        last_post_id = candidate.id
                        post_text = (candidate.message or "").strip()
                    else:
                        await asyncio.sleep(2)
                        return
                else:
                    await asyncio.sleep(2)
                    return

            target_comment = None
            if is_channel and last_post_id is not None:
                target_comment = await self._choose_user_comment(entity, last_post_id)

            seed = None
            cache_key = f"ctx:{chat_id}:{last_post_id}:{getattr(target_comment, 'id', 0)}"
            cached = await self.cache.get(cache_key)
            if cached:
                cache_hits.inc()
                text = cached
            else:
                cache_misses.inc()
                remain = self._llm_min_interval - (time.monotonic() - self._last_llm_call_ts)
                if remain > 0:
                    await asyncio.sleep(remain)
                    return

                logger.info(
                    "GEN:scheduler start (chat_id=%s, post_id=%s, reply_to_comment=%s)",
                    chat_id,
                    last_post_id,
                    getattr(target_comment, "id", None),
                )
                async with self._llm_lock:
                    with generation_latency.time():
                        text = await self.llm.generate_contextual_message(
                            post_text=post_text,
                            comment_text=(target_comment.message if target_comment else None),
                        )
                    self._last_llm_call_ts = time.monotonic()
                logger.info("GEN:scheduler done")
                if text:
                    logger.info("Scheduler LLM text len=%s preview=%s", len(text), text[:80])

                if not text:
                    logger.info("Scheduler: empty text, retry once")
                    async with self._llm_lock:
                        with generation_latency.time():
                            text = await self.llm.generate_contextual_message(
                                post_text=post_text,
                                comment_text=(target_comment.message if target_comment else None),
                            )
                        self._last_llm_call_ts = time.monotonic()

                if not text:
                    seed = self.llm.extract_seed_from_post(post_text)
                    logger.info(
                        "Scheduler: contextual empty twice, try random with seed='%s'", seed
                    )
                    async with self._llm_lock:
                        with generation_latency.time():
                            text = await self.llm.generate_random_message(seed_hint=seed)
                    self._last_llm_call_ts = time.monotonic()

                if not text:
                    await asyncio.sleep(2)
                    return

                await self.cache.set(cache_key, text)
                messages_generated.inc()

            # Анти‑дубликат
            if self._last_text_per_chat.get(chat_id) == text:
                async with self._llm_lock:
                    with generation_latency.time():
                        alt = await self.llm.generate_contextual_message(
                            post_text=post_text,
                            comment_text=(target_comment.message if target_comment else None),
                        )
                if not alt or alt == text:
                    seed2 = self.llm.extract_seed_from_post(post_text)
                    async with self._llm_lock:
                        with generation_latency.time():
                            alt = await self.llm.generate_random_message(seed_hint=seed2)
                    if not alt or alt == text:
                        async with self._llm_lock:
                            with generation_latency.time():
                                alt = await self.llm.generate_random_message()
                        if not alt or alt == text:
                            await asyncio.sleep(2)
                            return
                text = alt

            try:
                with send_latency.time():
                    if is_channel and last_post_id is not None:
                        await self.client.send_message(entity, text, comment_to=last_post_id)
                        logger.info(
                            "Commented channel post chat_id=%s post_id=%s: %s",
                            chat_id,
                            last_post_id,
                            (text or "")[:120],
                        )
                        self._last_commented_post_per_channel[chat_id] = last_post_id
                    else:
                        await self.client.send_message(entity, text)
                        logger.info("Message sent to chat_id=%s: %s", chat_id, (text or "")[:120])
            except Exception as send_exc:
                self._handle_send_error(chat_id, send_exc)
                send_failures.inc()
                logger.warning("Send failed for chat %s: %s", chat_id, send_exc)
                return

            now2 = time.monotonic()
            self._last_sent_global = now2
            self._last_sent_per_chat[chat_id] = now2
            self._last_text_per_chat[chat_id] = text
            self._last_chat_id = chat_id
            messages_sent.inc()

            self._inc_counters_for_now()
            await add_message_log(self.db, chat_id=str(chat_id), text=text)

        except Exception as e:
            send_failures.inc()
            logger.warning("Send failed for chat %s: %s", chat_id, e)

        jitter = random.uniform(0.3, 0.7)
        sleep_for = max(2, int(interval_base * jitter))
        await asyncio.sleep(sleep_for)
