from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Разрешаем "лишние" ключи в .env, чтобы не падать на новых переменных
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Telegram
    telegram_api_id: int = Field(..., alias="TELEGRAM_API_ID")
    telegram_api_hash: str = Field(..., alias="TELEGRAM_API_HASH")
    telegram_session_string: Optional[str] = Field(default=None, alias="TELEGRAM_SESSION_STRING")

    # Бот
    bot_enabled: bool = Field(True, alias="BOT_ENABLED")
    allowlist_chat_ids: List[int] = Field(default_factory=list, alias="ALLOWLIST_CHAT_IDS")

    # Тайминги
    messages_per_hour: int = Field(60, alias="MESSAGES_PER_HOUR")
    min_interval_between_messages_seconds: int = Field(
        60, alias="MIN_INTERVAL_BETWEEN_MESSAGES_SECONDS"
    )
    min_interval_per_chat_seconds: int = Field(
        3600, alias="MIN_INTERVAL_PER_CHAT_SECONDS"
    )  # 1 раз в час на канал

    # LLM / OpenAI-совместимый API
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    llm_model: str = Field("llama3.1", alias="LLM_MODEL")
    openai_base_url: Optional[str] = Field(None, alias="OPENAI_BASE_URL")

    # Опционально: параметры генерации из .env (могут отсутствовать)
    llm_temperature: Optional[float] = Field(default=None, alias="LLM_TEMPERATURE")
    llm_min_len: Optional[int] = Field(default=None, alias="LLM_MIN_LEN")
    llm_max_len: Optional[int] = Field(default=None, alias="LLM_MAX_LEN")
    llm_max_emojis: Optional[int] = Field(default=None, alias="LLM_MAX_EMOJIS")

    # Хранилища
    postgres_dsn: str = Field(..., alias="POSTGRES_DSN")
    cache_ttl_seconds: int = Field(86400, alias="CACHE_TTL_SECONDS")
    redis_url: Optional[str] = Field(default=None, alias="REDIS_URL")

    # Безопасность/админ
    admin_token: str = Field("change-me", alias="ADMIN_TOKEN")

    # Стиль
    style_prompt: str = Field(
        "Дружелюбный, тактичный тон, 1–2 предложения, без навязчивости, без обещаний, "
        "без персональных данных, нейтральные формулировки.",
        alias="STYLE_PROMPT",
    )

    # Топики по умолчанию (можно переопределить)
    default_topics: List[str] = Field(default_factory=list, alias="DEFAULT_TOPICS")

    # Максимальный возраст поста для комментария (минуты)
    fresh_post_max_age_minutes: int = Field(30, alias="FRESH_POST_MAX_AGE_MINUTES")


def get_settings() -> Settings:
    # Settings читает значения из .env, mypy не видит окружение и считает,
    # что надо передавать все поля явно.
    return Settings()  # type: ignore[call-arg]
