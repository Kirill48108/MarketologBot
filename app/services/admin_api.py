import ast
import logging
import os
from pathlib import Path
from typing import Any, List, Set

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Channel

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILENAME = os.getenv("ENV_FILE", ".env")
ENV_PATH = PROJECT_ROOT / ENV_FILENAME

logger = logging.getLogger("app.services.admin_api")


# ---------------------------
# Авторизация
# ---------------------------


def check_auth(x_admin_token: str = Header(..., alias="X-Admin-Token")) -> None:
    expected = os.getenv("ADMIN_TOKEN", "change-me")
    if not expected or x_admin_token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ---------------------------
# DTO
# ---------------------------


class BotConfigDTO(BaseModel):
    bot_name: str = "bot0"
    autojoin_chat_ids: List[str]
    allowlist_chat_ids: List[int]
    bot_enabled: bool


class BotConfigUpdateDTO(BaseModel):
    autojoin_chat_ids: List[str] | None = None
    allowlist_chat_ids: List[int] | None = None
    bot_enabled: bool | None = None


class RefreshResultDTO(BaseModel):
    allowlist_chat_ids: List[int]


class ChatStatusDTO(BaseModel):
    chat_id: int
    in_allowlist: bool
    banned_local: bool


class ResolveChatResponseDTO(BaseModel):
    chat_id: int
    title: str | None = None
    is_channel: bool


class ChatsOverviewDTO(BaseModel):
    chats: List[ChatStatusDTO]


class AllowlistUpdateResultDTO(BaseModel):
    allowlist_chat_ids: List[int]


class AllowlistAddRequestDTO(BaseModel):
    peer: str | None = None
    chat_id: int | None = None


class ResolveChatRequestDTO(BaseModel):
    peer: str


class AllowlistAddResponseDTO(BaseModel):
    allowlist_chat_ids: List[int]
    added_chat_id: int


class SessionUpdateDTO(BaseModel):
    telegram_session_string: str


# ---------------------------
# Утилиты работы с .env
# ---------------------------


def _parse_list_literal(raw: str) -> list[Any]:
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        val = ast.literal_eval(raw)
        if isinstance(val, (list, tuple, set)):
            return list(val)
    except Exception:
        pass
    return []


def _read_env_config() -> BotConfigDTO:
    raw_auto = (os.getenv("AUTOJOIN_CHAT_IDS") or "[]").strip()
    raw_allow = (os.getenv("ALLOWLIST_CHAT_IDS") or "[]").strip()
    bot_enabled = (os.getenv("BOT_ENABLED") or "true").lower() == "true"

    autojoin = [str(x) for x in _parse_list_literal(raw_auto)]
    allowlist = [int(x) for x in _parse_list_literal(raw_allow)]

    return BotConfigDTO(
        bot_name="bot0",
        autojoin_chat_ids=autojoin,
        allowlist_chat_ids=allowlist,
        bot_enabled=bot_enabled,
    )


def _write_env_updates(updates: dict[str, str]) -> None:
    if not ENV_PATH.exists():
        raise HTTPException(status_code=500, detail=f"{ENV_PATH} not found")

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    out_lines: list[str] = []
    replaced_keys: set[str] = set()

    for line in lines:
        stripped = line.strip()
        updated = False
        for k, v in updates.items():
            if stripped.startswith(f"{k}="):
                out_lines.append(f"{k}={v}")
                replaced_keys.add(k)
                updated = True
                break
        if not updated:
            out_lines.append(line)

    # Добавим недостающие ключи
    for k, v in updates.items():
        if k not in replaced_keys:
            out_lines.append(f"{k}={v}")

    ENV_PATH.write_text("\n".join(out_lines) + "\n", encoding="utf-8")

    # Обновим os.environ, чтобы текущий процесс тоже видел новые значения
    for k, v in updates.items():
        os.environ[k] = v


def _make_telegram_client() -> TelegramClient:
    """
    Создать Telethon-клиент на основе текущих TELEGRAM_API_ID/HASH/SESSION_STRING из окружения.
    Используется для resolve_chat и добавления в allowlist по peer.
    """
    from telethon.sessions import StringSession

    api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    session_str = os.getenv("TELEGRAM_SESSION_STRING", "")

    if not api_id or not api_hash or not session_str:
        raise HTTPException(
            status_code=500,
            detail="TELEGRAM_API_ID/HASH/SESSION_STRING not set in environment",
        )

    return TelegramClient(StringSession(session_str), api_id, api_hash)


# ---------------------------
# Эндпоинты
# ---------------------------


@router.get("/config", response_model=BotConfigDTO)
def get_config(_: None = Depends(check_auth)) -> BotConfigDTO:
    """
    Получить текущие настройки бота (один бот, .env).
    """
    return _read_env_config()


@router.post("/config", response_model=BotConfigDTO)
def update_config(data: BotConfigUpdateDTO, _: None = Depends(check_auth)) -> BotConfigDTO:
    """
    Обновить автоджойн/аллоулист/включённость бота через .env.
    """
    updates: dict[str, str] = {}

    if data.autojoin_chat_ids is not None:
        literal = "[" + ", ".join(repr(s) for s in data.autojoin_chat_ids) + "]"
        updates["AUTOJOIN_CHAT_IDS"] = literal

    if data.allowlist_chat_ids is not None:
        literal = "[" + ", ".join(str(i) for i in data.allowlist_chat_ids) + "]"
        updates["ALLOWLIST_CHAT_IDS"] = literal

    if data.bot_enabled is not None:
        updates["BOT_ENABLED"] = "true" if data.bot_enabled else "false"

    if not updates:
        return _read_env_config()

    _write_env_updates(updates)
    return _read_env_config()


def _parse_autojoin_to_refs(raw: str) -> list[str]:
    """
    Мягкий парсер AUTOJOIN_CHAT_IDS:
    - нормальный literal списка ['https://...', '@chan'];
    - или CSV-строка https://...,@chan
    """
    raw = (raw or "").strip()
    if not raw:
        return []

    # сначала пробуем literal
    try:
        val = ast.literal_eval(raw)
        if isinstance(val, (list, tuple, set)):
            return [str(x).strip() for x in val if str(x).strip()]
    except Exception:
        pass

    # фоллбек: режем как CSV и убираем скобки
    cleaned = raw
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1]
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    return parts


@router.post("/allowlist/refresh", response_model=RefreshResultDTO)
async def refresh_allowlist(_: None = Depends(check_auth)) -> RefreshResultDTO:
    """
    Пересобрать ALLOWLIST_CHAT_IDS по текущему AUTOJOIN_CHAT_IDS:
    - резолвим каждый канал/ссылку/username;
    - добавляем id каналов и их чатов обсуждений;
    - обновляем .env и возвращаем новый allowlist.

    В ALLOWLIST_CHAT_IDS сохраняем именно peer-id (-100...), чтобы формат был единый.
    """
    api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    session_str = os.getenv("TELEGRAM_SESSION_STRING", "")

    if not api_id or not api_hash or not session_str:
        raise HTTPException(status_code=500, detail="TELEGRAM_API_ID/HASH/SESSION_STRING not set")

    raw_auto = (os.getenv("AUTOJOIN_CHAT_IDS") or "").strip()
    refs = _parse_autojoin_to_refs(raw_auto)
    if not refs:
        raise HTTPException(status_code=400, detail="AUTOJOIN_CHAT_IDS is empty or invalid")

    from telethon.utils import get_peer_id

    unique_ids: Set[int] = set()

    async with TelegramClient(StringSession(session_str), api_id, api_hash) as client:
        for ref in refs:
            try:
                ent = await client.get_entity(ref)
            except Exception as e:
                logger.warning("RESOLVE: failed to get entity for %s: %s", ref, e)
                continue

            if not isinstance(ent, Channel):
                logger.info("RESOLVE: entity %s is not a Channel, skip", ref)
                continue

            # peer_id вернёт -100... для каналов/суперчатов
            chan_peer_id = int(get_peer_id(ent))
            unique_ids.add(chan_peer_id)
            logger.info(
                "RESOLVE: channel '%s' -> peer_id=%s",
                getattr(ent, "title", ref),
                chan_peer_id,
            )

            linked_id = getattr(ent, "linked_chat_id", None)
            if linked_id:
                try:
                    discussion = await client.get_entity(linked_id)
                    if isinstance(discussion, Channel):
                        d_peer_id = int(get_peer_id(discussion))
                        unique_ids.add(d_peer_id)
                        logger.info(
                            "RESOLVE: discussion '%s' -> peer_id=%s",
                            getattr(discussion, "title", linked_id),
                            d_peer_id,
                        )
                except Exception as e:
                    logger.warning(
                        "RESOLVE: failed to get discussion entity for linked_chat_id=%s: %s",
                        linked_id,
                        e,
                    )

    if not unique_ids:
        raise HTTPException(
            status_code=400, detail="No channel IDs resolved from AUTOJOIN_CHAT_IDS"
        )

    sorted_ids = sorted(unique_ids)
    allowlist_literal = "[" + ", ".join(str(i) for i in sorted_ids) + "]"

    # Обновляем .env и окружение
    _write_env_updates({"ALLOWLIST_CHAT_IDS": allowlist_literal})

    return RefreshResultDTO(allowlist_chat_ids=sorted_ids)


@router.get("/chats/overview", response_model=ChatsOverviewDTO)
def chats_overview(_: None = Depends(check_auth)) -> ChatsOverviewDTO:
    config = _read_env_config()

    chat_items: List[ChatStatusDTO] = []
    for cid in config.allowlist_chat_ids:
        chat_items.append(
            ChatStatusDTO(
                chat_id=cid,
                in_allowlist=True,
                banned_local=False,
            )
        )

    return ChatsOverviewDTO(chats=chat_items)


@router.get("/dashboard/chats/overview", response_model=ChatsOverviewDTO)
def dashboard_chats_overview(_: None = Depends(check_auth)) -> ChatsOverviewDTO:
    config = _read_env_config()

    chat_items: List[ChatStatusDTO] = []
    for cid in config.allowlist_chat_ids:
        chat_items.append(
            ChatStatusDTO(
                chat_id=cid,
                in_allowlist=True,
                banned_local=False,
            )
        )

    return ChatsOverviewDTO(chats=chat_items)


@router.get("/old/chats/overview", response_model=ChatsOverviewDTO)
def old_chats_overview(_: None = Depends(check_auth)) -> ChatsOverviewDTO:
    config = _read_env_config()

    chat_items: List[ChatStatusDTO] = []
    for cid in config.allowlist_chat_ids:
        chat_items.append(
            ChatStatusDTO(
                chat_id=cid,
                in_allowlist=True,
                banned_local=False,
            )
        )

    return ChatsOverviewDTO(chats=chat_items)


@router.get("/resolve_chat", response_model=ResolveChatResponseDTO)
async def resolve_chat(
    peer: str,
    _: None = Depends(check_auth),
) -> ResolveChatResponseDTO:
    """
    Разрешить @username / t.me/ссылку / invite-link в числовой chat_id.
    Удобно для людей без тех. навыков: вводят @канал, а backend отдаёт id.
    """
    peer = (peer or "").strip()
    if not peer:
        raise HTTPException(status_code=400, detail="peer is required")

    # Нормализуем peer для красоты (можно вводить без @ и без https)
    if peer.startswith("https://t.me/") or peer.startswith("http://t.me/"):
        # Оставляем как есть — Telethon это понимает
        norm_peer = peer
    elif peer.startswith("@"):
        norm_peer = peer
    else:
        # Если просто имя — добавим @
        norm_peer = "@" + peer

    try:
        client = _make_telegram_client()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to init Telegram client: {e}")

    async with client:
        try:
            ent = await client.get_entity(norm_peer)
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"Failed to resolve peer '{peer}': {e}")

        is_channel = isinstance(ent, Channel)
        title = getattr(ent, "title", None) or getattr(ent, "username", None) or None
        chat_id = int(getattr(ent, "id", 0) or 0)

        if not chat_id:
            raise HTTPException(status_code=500, detail="Resolved entity has no valid id")

        return ResolveChatResponseDTO(
            chat_id=chat_id,
            title=title,
            is_channel=is_channel,
        )


@router.delete("/allowlist/{chat_id}", response_model=AllowlistUpdateResultDTO)
async def remove_from_allowlist(
    chat_id: int, _: None = Depends(check_auth)
) -> AllowlistUpdateResultDTO:
    """
    Удалить чат из ALLOWLIST_CHAT_IDS (в .env) и вернуть обновлённый список.

    Дополнительно:
    - если AUTOJOIN_CHAT_IDS содержит ссылки/юзернеймы, которые резолвятся в этот же chat_id
      (по peer-id -100... или по обычному ent.id), такие записи тоже удаляем из AUTOJOIN_CHAT_IDS.
    """
    # 1) Чистим ALLOWLIST_CHAT_IDS
    config = _read_env_config()
    new_ids = [cid for cid in config.allowlist_chat_ids if cid != chat_id]

    allow_literal = "[" + ", ".join(str(i) for i in new_ids) + "]"
    updates: dict[str, str] = {"ALLOWLIST_CHAT_IDS": allow_literal}

    # 2) Чистим AUTOJOIN_CHAT_IDS от ссылок, указывающих на этот же чат
    raw_auto = (os.getenv("AUTOJOIN_CHAT_IDS") or "[]").strip()
    refs = _parse_autojoin_to_refs(raw_auto)
    if refs:
        try:
            from telethon.utils import get_peer_id
        except Exception:
            get_peer_id = None  # на всякий случай

        if get_peer_id is not None:
            api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
            api_hash = os.getenv("TELEGRAM_API_HASH", "")
            session_str = os.getenv("TELEGRAM_SESSION_STRING", "")

            if api_id and api_hash and session_str:
                keep_refs: list[str] = []
                async with TelegramClient(StringSession(session_str), api_id, api_hash) as client:
                    for ref in refs:
                        ref_str = str(ref).strip()
                        if not ref_str:
                            continue
                        try:
                            ent = await client.get_entity(ref_str)
                        except Exception as e:
                            logger.warning(
                                "REMOVE_ALLOWLIST: failed to resolve %s while cleaning AUTOJOIN: %s",
                                ref_str,
                                e,
                            )
                            # если не смогли резолвить — на всякий случай сохраним ссылку
                            keep_refs.append(ref_str)
                            continue

                        if not isinstance(ent, Channel):
                            # не канал — оставляем как есть
                            keep_refs.append(ref_str)
                            continue

                        # Достаём и peer-id (-100...), и обычный id (>0)
                        try:
                            peer_id = int(get_peer_id(ent))
                        except Exception:
                            peer_id = int(getattr(ent, "id", 0) or 0)

                        ent_id = int(getattr(ent, "id", 0) or 0)

                        # если удаляемый chat_id совпадает с peer-id или ent.id — выкидываем ref
                        if chat_id == peer_id or chat_id == ent_id:
                            logger.info(
                                "REMOVE_ALLOWLIST: drop autojoin ref %s "
                                "(peer_id=%s, ent_id=%s, removed_chat_id=%s)",
                                ref_str,
                                peer_id,
                                ent_id,
                                chat_id,
                            )
                            continue

                        # иначе оставляем
                        keep_refs.append(ref_str)

                # Пересобираем AUTOJOIN_CHAT_IDS только из оставшихся ссылок
                auto_parts = [repr(s) for s in keep_refs]
                auto_literal = "[" + ", ".join(auto_parts) + "]"
                updates["AUTOJOIN_CHAT_IDS"] = auto_literal

    # 3) Записываем изменения в .env и окружение
    _write_env_updates(updates)

    return AllowlistUpdateResultDTO(allowlist_chat_ids=new_ids)


@router.post("/allowlist/add", response_model=AllowlistAddResponseDTO)
async def add_to_allowlist(
    data: AllowlistAddRequestDTO,
    _: None = Depends(check_auth),
) -> AllowlistAddResponseDTO:
    """
    Добавить новый канал/чат в ALLOWLIST_CHAT_IDS.

    Варианты вызова (для людей без навыков):
    - по @username или ссылке:
      { "peer": "@some_channel" }
      { "peer": "https://t.me/some_channel" }

    - по raw chat_id:
      { "chat_id": -1001234567890 }

    В ответе вернём добавленный chat_id и обновлённый список allowlist.

    Дополнительно:
    - если канал добавлен по peer (ссылка/@username), сохраняем этот peer в AUTOJOIN_CHAT_IDS
      в строковом виде (для автоджойна по ссылкам/юзернеймам).
    - при добавлении по peer в ALLOWLIST_CHAT_IDS сохраняем peer-id (-100...), чтобы формат был единый.
    """
    if not data.peer and data.chat_id is None:
        raise HTTPException(status_code=400, detail="peer or chat_id is required")

    # 1) Определяем chat_id и, при возможности, строковый peer для AUTOJOIN
    chat_id: int
    autojoin_peer: str | None = None  # то, что запишем в AUTOJOIN_CHAT_IDS (если есть)

    if data.chat_id is not None:
        # Добавление по "сырым" числовым chat_id – трогаем только ALLOWLIST_CHAT_IDS
        try:
            chat_id = int(data.chat_id)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid chat_id")
    else:
        peer = (data.peer or "").strip()
        if not peer:
            raise HTTPException(status_code=400, detail="peer is empty")

        # Нормализуем peer чуть-чуть (как в resolve_chat)
        if peer.startswith("https://t.me/") or peer.startswith("http://t.me/"):
            norm_peer = peer
        elif peer.startswith("@"):
            norm_peer = peer
        else:
            norm_peer = "@" + peer

        try:
            client = _make_telegram_client()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to init Telegram client: {e}")

        async with client:
            try:
                ent = await client.get_entity(norm_peer)
            except Exception as e:
                raise HTTPException(status_code=404, detail=f"Failed to resolve peer '{peer}': {e}")

            # Разрешаем только каналы/чаты (а не личные профили)

            try:
                from telethon.tl.types import Channel, Chat

                channel_type = Channel
                chat_type = Chat
            except Exception:
                channel_type = None
                chat_type = None

            if channel_type is not None and chat_type is not None:
                if not isinstance(ent, (channel_type, chat_type)):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Peer '{peer}' is not a channel/chat (resolved to user/profile).",
                    )

            # Сохраняем в ALLOWLIST именно peer-id (-100...), а не просто ent.id
            try:
                from telethon.utils import get_peer_id

                chat_id = int(get_peer_id(ent))
            except Exception:
                chat_id = int(getattr(ent, "id", 0) or 0)

            if not chat_id:
                raise HTTPException(status_code=500, detail="Resolved entity has no valid id")

            # Для AUTOJOIN запоминаем именно строковый peer (нормализованный)
            autojoin_peer = norm_peer

    # 2) Обновляем ALLOWLIST_CHAT_IDS в .env (список чисел, теперь peer-id)
    config = _read_env_config()
    ids = list(config.allowlist_chat_ids)

    if chat_id not in ids:
        ids.append(chat_id)

    ids_sorted = sorted(ids)
    allow_literal = "[" + ", ".join(str(i) for i in ids_sorted) + "]"

    updates: dict[str, str] = {"ALLOWLIST_CHAT_IDS": allow_literal}

    # 3) Если есть строковый peer (добавляли по ссылке/@), дополняем AUTOJOIN_CHAT_IDS
    if autojoin_peer:
        raw_auto = (os.getenv("AUTOJOIN_CHAT_IDS") or "[]").strip()
        auto_vals = _parse_list_literal(raw_auto)

        # Приводим всё к строкам для сравнения
        existing_strs: set[str] = set(str(v).strip() for v in auto_vals if str(v).strip())

        if autojoin_peer not in existing_strs:
            auto_vals.append(autojoin_peer)

        # Формируем литерал: строки как repr(...)
        auto_parts: list[str] = []
        for v in auto_vals:
            s = str(v)
            auto_parts.append(repr(s))

        auto_literal = "[" + ", ".join(auto_parts) + "]"
        updates["AUTOJOIN_CHAT_IDS"] = auto_literal

    # 4) Записываем обновления в .env и окружение
    _write_env_updates(updates)

    return AllowlistAddResponseDTO(
        allowlist_chat_ids=ids_sorted,
        added_chat_id=chat_id,
    )


@router.post("/session/update")
def update_session(
    data: SessionUpdateDTO,
    _: None = Depends(check_auth),
) -> dict[str, str]:
    """
    Обновить TELEGRAM_SESSION_STRING через .env и окружение.

    Шаги:
    - записываем новое значение в .env (через _write_env_updates);
    - обновляем os.environ["TELEGRAM_SESSION_STRING"];
    - сам перезапуск UserBot делает отдельный эндпоинт /admin/control/restart_bot.
    """
    session_str = (data.telegram_session_string or "").strip()
    if not session_str:
        raise HTTPException(status_code=400, detail="telegram_session_string is required")

    _write_env_updates({"TELEGRAM_SESSION_STRING": session_str})
    return {"status": "ok"}
