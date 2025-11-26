import pytest

from app.services.llm import LLMClient


class DummyClient(LLMClient):
    def __init__(self):
        pass

    async def generate_random_message(self, seed_hint=None) -> str:
        return "short msg"


@pytest.mark.asyncio
async def test_llm_stub():
    llm = DummyClient()
    text = await llm.generate_random_message()
    assert isinstance(text, str)
    assert len(text) > 0
