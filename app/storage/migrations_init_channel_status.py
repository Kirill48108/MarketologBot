import os

from sqlalchemy import create_engine, text


def main() -> None:
    """
    Простая миграция для таблицы channel_status.
    Хранит локальный статус каналов по ботам (ok / banned_local / flood_limited / forbidden и т.п.).
    """
    dsn = os.getenv(
        "POSTGRES_DSN",
        "postgresql+psycopg2://user:password@postgres:5432/mb_db",
    )
    engine = create_engine(dsn)

    ddl = """
          CREATE TABLE IF NOT EXISTS channel_status (
                                                        id SERIAL PRIMARY KEY,
                                                        bot_name TEXT NOT NULL,             -- 'bot0', 'bot1', ...
                                                        chat_id BIGINT NOT NULL,
                                                        status TEXT NOT NULL DEFAULT 'ok',  -- 'ok', 'banned_local', 'flood_limited', 'forbidden', ...
                                                        last_error_type TEXT,
                                                        last_error_at TIMESTAMPTZ,
                                                        error_count_recent INTEGER NOT NULL DEFAULT 0,
                                                        CONSTRAINT uq_channel_status_bot_chat UNIQUE (bot_name, chat_id)
              ); \
          """

    with engine.begin() as conn:
        conn.execute(text(ddl))

    print("channel_status table ensured")


if __name__ == "__main__":
    main()
