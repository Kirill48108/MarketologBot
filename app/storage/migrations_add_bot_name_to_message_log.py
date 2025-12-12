import os

from sqlalchemy import create_engine, text


def main() -> None:
    dsn = os.getenv(
        "POSTGRES_DSN",
        "postgresql+psycopg2://user:password@postgres:5432/mb_db",
    )
    engine = create_engine(dsn)

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE message_log ADD COLUMN IF NOT EXISTS bot_name TEXT"))
        conn.execute(text("UPDATE message_log SET bot_name = 'bot0' WHERE bot_name IS NULL"))

    print("message_log.bot_name ensured and backfilled")


if __name__ == "__main__":
    main()
