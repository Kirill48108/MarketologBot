from app.config import Settings


def test_settings_parsing(monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "1")
    monkeypatch.setenv("TELEGRAM_API_HASH", "h")
    monkeypatch.setenv("POSTGRES_DSN", "postgresql+asyncpg://u:p@l:5432/db")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    # Pydantic v2 ожидает JSON-формат для списка
    monkeypatch.setenv("ALLOWLIST_CHAT_IDS", "[123,-10045]")
    s = Settings()  # type: ignore[call-arg]
    assert s.telegram_api_id == 1
    assert s.allowlist_chat_ids == [123, -10045]
