import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.bot.userbot import UserBot
from app.services.llm import LLMClient


class DummyLLM(LLMClient):
    def __init__(self):  # type: ignore[override]
        # не вызываем реальный __init__
        pass

    async def generate_contextual_message(  # type: ignore[override]
        self, post_text: str, comment_text=None
    ) -> str:
        return "hello"


class DummyDB(sessionmaker):
    def __call__(self, *args, **kwargs) -> Session:  # type: ignore[override]
        raise RuntimeError("DB should not be used in this test")


class DummyCache:
    async def get(self, key: str):
        return None

    async def set(self, key: str, value):
        return None


@pytest.mark.asyncio
async def test_userbot_scheduler_intervals(monkeypatch):
    # Подменяем TelegramClient методами-заглушками
    class DummyClient:
        async def start(self):
            pass

        async def run_until_disconnected(self):
            await asyncio.sleep(0.01)

        async def disconnect(self):
            pass

        async def get_entity(self, chat_id):
            return chat_id

        async def get_messages(self, *args, **kwargs):
            # имитируем отсутствие постов с обсуждениями, чтобы _tick_send рано выходил
            return []

        async def send_message(self, entity, text):
            pass

    ub = UserBot.__new__(UserBot)  # обходим __init__
    ub.client = DummyClient()
    ub.llm = DummyLLM()  # type: ignore[assignment]
    ub.allowlist = [1, 2]
    ub.messages_per_hour = 30
    ub.min_interval_global = 0
    ub.min_interval_per_chat = 0
    ub.cache = DummyCache()
    ub.db = DummyDB()  # type: ignore[assignment]
    ub._enabled = True
    ub._runner_task = None
    ub._last_sent_global = 0.0
    ub._last_sent_per_chat = {}
    ub._last_text_per_chat = {}
    ub._last_chat_id = None
    ub._last_commented_post_per_channel = {}
    ub._banned_chats = set()

    # Инициализируем новые поля, связанные с лимитами и окнами
    ub._active_windows = []  # пустой список -> нет ограничений по часам в тесте
    ub._max_messages_per_day = 100
    ub._daily_sent_count = 0
    ub._daily_counter_date = datetime.now(timezone.utc).date()
    ub._per_window_sent = {}
    ub._instant_history_per_chat = {}
    ub._max_instant_per_chat_per_hour = 3
    ub._llm_min_interval = 0.0
    ub._last_llm_call_ts = 0.0
    ub._self_id = None
    ub._llm_lock = asyncio.Lock()

    # Поля анти-спамблока
    ub._spamblock_min_cooldown = 3600
    ub._spamblock_max_cooldown = 86400
    ub._spamblock_error_threshold = 3
    ub._spam_block_until_ts = 0.0
    ub._spam_errors_recent = 0

    # Выполним один тик отправки
    await ub._tick_send(interval_base=1)
    # Повторный тик тоже должен пройти (интервалы нулевые)
    await ub._tick_send(interval_base=1)
