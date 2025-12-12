import os

from sqlalchemy import create_engine, text


def main() -> None:
    dsn = os.getenv(
        "POSTGRES_DSN",
        "postgresql+psycopg2://user:password@postgres:5432/mb_db",
    )
    engine = create_engine(dsn)
    ddl = """
          CREATE TABLE IF NOT EXISTS bot_configs (
                                                     id SERIAL PRIMARY KEY,
                                                     name TEXT UNIQUE NOT NULL,          -- 'bot0', 'bot1', ..., 'bot8'
                                                     env_filename TEXT NOT NULL,         -- '.env', '.env.bot1', ...

                                                     telegram_api_id INTEGER NOT NULL,
                                                     telegram_api_hash TEXT NOT NULL,
                                                     session_string TEXT NOT NULL,

                                                     allowlist_chat_ids TEXT NOT NULL,   -- строка ([..]) как в .env
                                                     autojoin_chat_ids TEXT NOT NULL,    -- строка ([..]) как в .env

                                                     enabled BOOLEAN NOT NULL DEFAULT TRUE
          ); \
          """
    with engine.begin() as conn:
        conn.execute(text(ddl))
    print("bot_configs table ensured")


if __name__ == "__main__":
    main()
