import random

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.bot.userbot import UserBot
from app.services.llm import LLMClient


class DummyLLM(LLMClient):
    def __init__(self):  # type: ignore[override]
        # Не вызываем реальный __init__, чтобы не поднимать AsyncOpenAI
        pass


class DummyDB(sessionmaker):
    def __call__(self, *args, **kwargs) -> Session:  # type: ignore[override]
        # В этом тесте база не должна использоваться
        raise RuntimeError("DB should not be used in this test")


class DummyCache:
    async def get(self, key: str):
        return None

    async def set(self, key: str, value):
        return None


@pytest.mark.asyncio
async def test_in_allowlist_and_pick_chat():
    cache = DummyCache()
    dummy_db = DummyDB()  # type: ignore[assignment]

    allowlist = {1001, 1002}

    bot = UserBot(
        api_id=1,
        api_hash="hash",
        session_string=None,
        llm=DummyLLM(),  # type: ignore[arg-type]
        allowlist=allowlist,
        messages_per_hour=10,
        min_interval_global=0,
        min_interval_per_chat=0,
        cache=cache,
        db=dummy_db,
    )

    # _in_allowlist корректно различает свои/чужие каналы
    assert bot._in_allowlist(1001) is True
    assert bot._in_allowlist(1002) is True
    assert bot._in_allowlist(9999) is False

    # _pick_chat выбирает только из allowlist
    random.seed(0)
    picked = await bot._pick_chat()
    assert picked in allowlist
