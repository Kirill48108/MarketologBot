import asyncio
import logging
import os
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text as sa_text
from telethon.errors import RPCError

from app.bot.userbot import UserBot
from app.config import get_settings
from app.services.admin_api import router as admin_router
from app.services.cache import AsyncRedis, AsyncTTLCache, RedisTTLCache
from app.services.llm import LLMClient
from app.storage.repository import increment_click, init_db, upsert_link
from app.storage.stats_repository import (  # <-- добавь эту строку
    get_channel_stats,
    get_links_stats,
    get_stats_overview,
)

app = FastAPI()
logger = logging.getLogger("uvicorn.error")
_bot_task: Optional[asyncio.Task] = None
_userbot: Optional[UserBot] = None
_db_session = None
_settings = None
_cache = None

# Подключаем админский роутер (дашбордный бекенд)
app.include_router(admin_router, prefix="/admin", tags=["admin"])


def admin_auth(token: Optional[str] = None):
    """
    Простая проверка админ-токена для эндпоинтов в main.py.
    Используем тот же источник, что и admin_api.check_auth: переменную окружения ADMIN_TOKEN.
    """
    expected = os.getenv("ADMIN_TOKEN", "change-me")
    if not expected or token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/")
async def root():
    return {"message": "Hello World"}


# ... existing code ...
@app.get("/hello/{name}")
async def say_hello(name: str):
    return {"message": f"Hello {name}"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/status")
async def status():
    enabled = _userbot.is_enabled() if _userbot else False
    return {"bot_running": _bot_task is not None and not _bot_task.done(), "enabled": enabled}


@app.post("/control/enable")
async def enable_bot():
    if _userbot:
        _userbot.enable()
    return {"enabled": True}


@app.post("/control/disable")
async def disable_bot():
    if _userbot:
        _userbot.disable()
    return {"enabled": False}


@app.get("/metrics")
async def metrics():
    data = generate_latest()
    return PlainTextResponse(content=data, media_type=CONTENT_TYPE_LATEST)


# ... existing code ...
@app.post("/admin/links/{slug}")
async def admin_upsert_link(slug: str, request: Request):
    token = request.headers.get("x-admin-token")
    admin_auth(token)
    payload = await request.json()
    target = payload.get("target_url")
    if not target:
        raise HTTPException(400, "target_url is required")
    row = await upsert_link(_db_session, slug, target)
    return {"slug": row.slug, "target_url": row.target_url, "clicks": row.clicks}


# Последние отправки из БД
@app.get("/admin/recent_messages")
async def admin_recent_messages(
    limit: int = 20, x_admin_token: str = Header(default=None, alias="x-admin-token")
):
    admin_auth(x_admin_token)
    try:
        rows = []
        session_factory = _db_session
        with session_factory() as s:
            res = s.execute(
                sa_text(
                    "SELECT chat_id, message_text, created_at "
                    "FROM message_log ORDER BY created_at DESC LIMIT :lim"
                ),
                {"lim": limit},
            )
            for r in res:
                created_at = r[2]
                try:
                    created_at = created_at.isoformat()
                except Exception:
                    created_at = str(created_at)
                rows.append({"chat_id": r[0], "message_text": r[1], "created_at": created_at})
        return {"items": rows}
    except Exception as e:
        raise HTTPException(500, f"failed to fetch recent messages: {e}")


@app.get("/admin/stats/overview")
async def admin_stats_overview(
    x_admin_token: str = Header(default=None, alias="x-admin-token"),
):
    admin_auth(x_admin_token)
    if not _db_session:
        raise HTTPException(500, "DB session factory not initialized")

    bot_name = os.getenv("BOT_NAME", getattr(_settings, "bot_name", "bot0"))
    overview = get_stats_overview(_db_session, bot_name)

    # Берём каналы из allowlist текущего бота (это “истина”)
    allowlist_ids = list(getattr(_settings, "allowlist_chat_ids", []) or [])
    channels_total = len(allowlist_ids)

    return {
        "bot_name": bot_name,
        "channels_total": channels_total,
        "channels_banned": overview.channels_banned,
        "channels_flood_limited": overview.channels_flood_limited,
        "messages_last_24h": overview.messages_last_24h,
        "messages_last_7d": overview.messages_last_7d,
        "links_total_clicks": overview.links_total_clicks,
    }


@app.get("/admin/stats/channels")
async def admin_stats_channels(
    x_admin_token: str = Header(default=None, alias="x-admin-token"),
):
    admin_auth(x_admin_token)
    if not _db_session:
        raise HTTPException(500, "DB session factory not initialized")

    bot_name = os.getenv("BOT_NAME", getattr(_settings, "bot_name", "bot0"))

    # статусы из БД:
    items = get_channel_stats(_db_session, bot_name)
    by_id = {int(r.chat_id): r for r in items}

    # allowlist текущего бота:
    allowlist_ids = list(getattr(_settings, "allowlist_chat_ids", []) or [])

    # Отдаём статусы для ВСЕХ allowlist-чатов, даже если в channel_status ещё нет строки
    merged = []
    for cid in sorted(set(int(x) for x in allowlist_ids)):
        row = by_id.get(cid)
        if row is None:
            merged.append(
                {
                    "chat_id": cid,
                    "status": "ok",
                    "error_count_recent": 0,
                    "last_error_type": None,
                    "last_error_at": None,
                }
            )
        else:
            merged.append(
                {
                    "chat_id": row.chat_id,
                    "status": row.status,
                    "error_count_recent": row.error_count_recent,
                    "last_error_type": row.last_error_type,
                    "last_error_at": row.last_error_at.isoformat() if row.last_error_at else None,
                }
            )

    return {"items": merged}


@app.get("/admin/stats/links")
async def admin_stats_links(
    x_admin_token: str = Header(default=None, alias="x-admin-token"),
):
    """
    Статистика по трекаемым ссылкам (/r/{slug}).
    """
    admin_auth(x_admin_token)
    if not _db_session:
        raise HTTPException(500, "DB session factory not initialized")

    items = get_links_stats(_db_session)

    return {
        "items": [
            {
                "slug": row.slug,
                "target_url": row.target_url,
                "clicks": row.clicks,
            }
            for row in items
        ]
    }


# Принудительная отправка (для диагностики): коммент для канала / сообщение в чат
@app.post("/admin/send_test")
async def admin_send_test(
    payload: dict, x_admin_token: str = Header(default=None, alias="x-admin-token")
):
    admin_auth(x_admin_token)
    if not _userbot:
        raise HTTPException(503, "UserBot not ready")

    raw_peer = payload.get("peer")  # строковый идентификатор: @username / t.me/... / +7... / "me"
    chat_id = payload.get("chat_id")  # числовой id
    text = payload.get("text") or "Тестовое сообщение"

    if raw_peer is None and chat_id is None:
        raise HTTPException(400, "peer (str) or chat_id (int) is required")

    try:
        # Разрешаем цель: сначала строковый peer, иначе числовой chat_id
        if isinstance(raw_peer, str) and raw_peer.strip():
            target = raw_peer.strip()
        elif isinstance(chat_id, int):
            target = chat_id
        else:
            raise HTTPException(400, "Invalid peer/chat_id")

        entity = await _userbot.client.get_entity(target)
        is_channel = bool(getattr(entity, "broadcast", False))
        last_post_id = None
        if is_channel:
            # Ищем ближайший пост с обсуждениями среди последних ~20
            msgs = await _userbot.client.get_messages(entity, limit=20)
            candidate_id = None
            for m in msgs:
                if getattr(m, "post", False):
                    rep = getattr(m, "replies", None)
                    if rep and (getattr(rep, "comments", False) or getattr(rep, "replies", 0) >= 0):
                        candidate_id = m.id
                        break
            if candidate_id:
                last_post_id = candidate_id

        if is_channel and last_post_id is not None:
            await _userbot.client.send_message(entity, text, comment_to=last_post_id)
            where = {"mode": "comment", "post_id": last_post_id}
        else:
            if is_channel:
                raise HTTPException(
                    409,
                    "Нет доступного поста с включёнными обсуждениями, либо вы не состоите в чате обсуждений канала.",
                )
            await _userbot.client.send_message(entity, text)
            where = {"mode": "message"}

        # Логируем успешную отправку
        from app.storage.repository import add_message_log

        await add_message_log(
            _db_session,
            chat_id=str(getattr(entity, "id", chat_id or raw_peer)),
            text=text,
            bot_name=getattr(_userbot, "bot_name", "bot0"),
        )

        return {"ok": True, "peer": raw_peer, "chat_id": chat_id, "where": where}
    except RPCError as e:
        return {"ok": False, "peer": raw_peer, "chat_id": chat_id, "error": str(e)}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "peer": raw_peer, "chat_id": chat_id, "error": str(e)}


# Инспекция чата/канала (покажет broadcast и наличие поста)
@app.get("/admin/inspect_chat/{chat_id}")
async def admin_inspect_chat(
    chat_id: int, x_admin_token: str = Header(default=None, alias="x-admin-token")
):
    admin_auth(x_admin_token)
    try:
        if not _userbot:
            raise HTTPException(503, "UserBot not ready")
        entity = await _userbot.client.get_entity(chat_id)
        is_channel = bool(getattr(entity, "broadcast", False))
        title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(chat_id)
        last_post_id = None
        has_post = False
        if is_channel:
            msgs = await _userbot.client.get_messages(entity, limit=1)
            if msgs and getattr(msgs[0], "post", False):
                last_post_id = msgs[0].id
                has_post = True
        return {
            "chat_id": chat_id,
            "title": title,
            "is_channel": is_channel,
            "has_last_post": has_post,
            "last_post_id": last_post_id,
        }
    except Exception as e:
        raise HTTPException(500, f"inspect failed: {e}")


@app.get("/admin/overview")
async def admin_overview(request: Request):
    token = request.headers.get("x-admin-token")
    admin_auth(token)

    # Отключаем HTML-страницы по умолчанию (на VPS не надо).
    if os.getenv("EXPOSE_BOT_DASHBOARD", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="Not found")

    html = """
    <html><head><title>Admin Overview</title></head>
    <body>
      <h1>Admin</h1>
      <ul>
        <li><a href="/metrics">Prometheus metrics</a></li>
      </ul>
      <p>POST /admin/links/{slug} с JSON {"target_url": "..."} и заголовком x-admin-token.</p>
    </body></html>
    """
    return HTMLResponse(html)


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard():
    """
    Локальный HTML-дашборд бота.
    На VPS лучше выключать и пользоваться единым control-center.
    """
    if os.getenv("EXPOSE_BOT_DASHBOARD", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="Not found")

    html = """
    <!DOCTYPE html>
    <html lang="ru">
    <!-- ... existing code ... -->
    </html>
    """
    return HTMLResponse(html)


@app.get("/r/{slug}")
async def redirect_slug(slug: str):
    target = await increment_click(_db_session, slug)
    if not target:
        raise HTTPException(404, "Not found")
    return RedirectResponse(url=target, status_code=302)


@app.on_event("startup")
async def on_startup():
    global _bot_task, _userbot, _db_session, _settings, _cache
    _settings = get_settings()
    logging.basicConfig(level=logging.INFO)

    _db_session = init_db(_settings.postgres_dsn)

    if _settings.redis_url and AsyncRedis is not None:
        try:
            redis = await AsyncRedis.from_url(_settings.redis_url, decode_responses=False)
            _cache = RedisTTLCache(redis=redis, ttl_seconds=_settings.cache_ttl_seconds)
            logger.info("Using Redis cache")
        except Exception as e:
            logger.warning("Redis unavailable, fallback to memory cache: %s", e)
            _cache = AsyncTTLCache(ttl_seconds=_settings.cache_ttl_seconds)
    else:
        _cache = AsyncTTLCache(ttl_seconds=_settings.cache_ttl_seconds)

    llm = LLMClient(
        api_key=_settings.openai_api_key,
        model=_settings.llm_model,
        base_url=_settings.openai_base_url,
        style_prompt=_settings.style_prompt,
        extra_topics=_settings.default_topics,
    )

    allowlist = set(_settings.allowlist_chat_ids or [])

    _userbot = UserBot(
        api_id=_settings.telegram_api_id,
        api_hash=_settings.telegram_api_hash,
        session_string=_settings.telegram_session_string,
        llm=llm,
        allowlist=allowlist,
        messages_per_hour=_settings.messages_per_hour,
        min_interval_global=_settings.min_interval_between_messages_seconds,
        min_interval_per_chat=_settings.min_interval_per_chat_seconds,
        cache=_cache,
        db=_db_session,
        fresh_post_max_age_minutes=_settings.fresh_post_max_age_minutes,
    )

    if _settings.bot_enabled:
        _bot_task = asyncio.create_task(_userbot.start())
        logger.info("UserBot startup task created")


@app.on_event("shutdown")
async def on_shutdown():
    global _bot_task, _userbot
    if _userbot:
        await _userbot.stop()
    if _bot_task and not _bot_task.done():
        _bot_task.cancel()


def get_userbot() -> Optional[UserBot]:
    """
    Простая утилита для доступа к текущему UserBot из других модулей (admin_api).
    """
    global _userbot
    return _userbot


@app.post("/admin/control/enable")
async def admin_enable_bot(x_admin_token: str = Header(default=None, alias="x-admin-token")):
    admin_auth(x_admin_token)
    if _userbot:
        _userbot.enable()
    # Заодно обновим BOT_ENABLED в .env через admin_api, если хочешь, можно позже
    return {"enabled": True}


@app.post("/admin/control/disable")
async def admin_disable_bot(x_admin_token: str = Header(default=None, alias="x-admin-token")):
    admin_auth(x_admin_token)
    if _userbot:
        _userbot.disable()
    return {"enabled": False}


@app.post("/admin/control/restart_bot")
async def admin_restart_bot(x_admin_token: str = Header(default=None, alias="x-admin-token")):
    admin_auth(x_admin_token)
    global _bot_task, _userbot, _settings, _db_session, _cache

    # Остановим старый бот, если он есть
    if _userbot:
        try:
            await _userbot.stop()
        except Exception as e:
            logger.warning("Failed to stop existing UserBot on restart: %s", e)
    if _bot_task and not _bot_task.done():
        _bot_task.cancel()

    # Пересчитываем allowlist из текущего _settings и окружения
    # (на случай, если .env поменяли через admin_api)
    from app.config import get_settings as _reload_settings

    _settings = _reload_settings()
    allowlist = set(_settings.allowlist_chat_ids or [])

    # Создаём нового UserBot
    llm = LLMClient(
        api_key=_settings.openai_api_key,
        model=_settings.llm_model,
        base_url=_settings.openai_base_url,
        style_prompt=_settings.style_prompt,
        extra_topics=_settings.default_topics,
    )

    _userbot = UserBot(
        api_id=_settings.telegram_api_id,
        api_hash=_settings.telegram_api_hash,
        session_string=_settings.telegram_session_string,
        llm=llm,
        allowlist=allowlist,
        messages_per_hour=_settings.messages_per_hour,
        min_interval_global=_settings.min_interval_between_messages_seconds,
        min_interval_per_chat=_settings.min_interval_per_chat_seconds,
        cache=_cache,
        db=_db_session,
        fresh_post_max_age_minutes=_settings.fresh_post_max_age_minutes,
    )

    if _settings.bot_enabled:
        _bot_task = asyncio.create_task(_userbot.start())
        logger.info("UserBot restarted via /admin/control/restart_bot")

    return {
        "ok": True,
        "enabled": _settings.bot_enabled,
        "allowlist_chat_ids": list(allowlist),
    }
