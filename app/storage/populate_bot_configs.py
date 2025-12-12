import os
from typing import Any, Iterable

from sqlalchemy import Engine, create_engine, text


def get_engine() -> Engine:
    dsn = os.getenv(
        "POSTGRES_DSN",
        "postgresql+psycopg2://user:password@localhost:5433/mb_db",
    )
    return create_engine(dsn)


def upsert_bot_configs(configs: Iterable[dict[str, Any]]) -> None:
    engine = get_engine()
    upsert_sql = text(
        """
        INSERT INTO bot_configs (
            name,
            env_filename,
            telegram_api_id,
            telegram_api_hash,
            session_string,
            allowlist_chat_ids,
            autojoin_chat_ids,
            enabled
        )
        VALUES (
                   :name,
                   :env_filename,
                   :telegram_api_id,
                   :telegram_api_hash,
                   :session_string,
                   :allowlist_chat_ids,
                   :autojoin_chat_ids,
                   :enabled
               )
            ON CONFLICT (name) DO UPDATE SET
            env_filename = EXCLUDED.env_filename,
                                      telegram_api_id = EXCLUDED.telegram_api_id,
                                      telegram_api_hash = EXCLUDED.telegram_api_hash,
                                      session_string = EXCLUDED.session_string,
                                      allowlist_chat_ids = EXCLUDED.allowlist_chat_ids,
                                      autojoin_chat_ids = EXCLUDED.autojoin_chat_ids,
                                      enabled = EXCLUDED.enabled
        """
    )

    with engine.begin() as conn:
        for cfg in configs:
            conn.execute(upsert_sql, cfg)
            print(f"Upserted bot_config for {cfg['name']}")


def main() -> None:
    """
    Здесь заполняем конфиги ботов.
    """
    telegram_api_id = int(os.getenv("TELEGRAM_API_ID", "1234567"))
    telegram_api_hash = os.getenv("TELEGRAM_API_HASH", "your_api_hash_here")

    configs: list[dict[str, Any]] = [
        {
            "name": "bot0",
            "env_filename": ".env",  # основной бот
            "telegram_api_id": telegram_api_id,
            "telegram_api_hash": telegram_api_hash,
            "session_string": os.getenv("TELEGRAM_SESSION_STRING", ""),
            "allowlist_chat_ids": "[-1001111111111, -1002222222222]",
            "autojoin_chat_ids": "[-1001111111111, -1002222222222]",
            "enabled": True,
        },
        # Пример второго бота. Потом скопируешь/подправишь под bot1..bot8.
        # {
        #     "name": "bot1",
        #     "env_filename": ".env.bot1",
        #     "telegram_api_id": TELEGRAM_API_ID,
        #     "telegram_api_hash": TELEGRAM_API_HASH,
        #     "session_string": "SESSION_STRING_ДЛЯ_BOT1",
        #     "allowlist_chat_ids": "[-1003333333333, -1004444444444]",
        #     "autojoin_chat_ids": "[-1003333333333, -1004444444444]",
        #     "enabled": True,
        # },
    ]

    # Отфильтруем пустой session_string, чтобы не залить мусор
    configs = [c for c in configs if c["session_string"]]
    if not configs:
        raise RuntimeError(
            "Нет конфигов с непустым session_string. Заполни TELEGRAM_SESSION_STRING в локальном .env "
            "или пропиши строки сессий прямо в этом файле."
        )

    upsert_bot_configs(configs)


if __name__ == "__main__":
    main()
