import os

from sqlalchemy import create_engine, text


def main() -> None:
    """
    Простая миграция для таблицы message_log.

    Хранит отправленные ботом сообщения:
    - chat_id (строкой, чтобы не заморачиваться с BIGINT и -100...),
    - текст сообщения,
    - время отправки.
    """
    dsn = os.getenv(
        "POSTGRES_DSN",
        "postgresql+psycopg2://user:password@postgres:5432/mb_db",
    )
    engine = create_engine(dsn)

    ddl = """
          CREATE TABLE IF NOT EXISTS message_log (
                                                     id SERIAL PRIMARY KEY,
                                                     chat_id TEXT NOT NULL,
                                                     message_text TEXT NOT NULL,
                                                     created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
              -- Если потом захочешь хранить, каким ботом отправлено:
              -- , bot_name TEXT
              ); \
          """

    with engine.begin() as conn:
        conn.execute(text(ddl))

    print("message_log table ensured")


if __name__ == "__main__":
    main()
