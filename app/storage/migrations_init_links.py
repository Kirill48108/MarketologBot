import os

from sqlalchemy import create_engine, text


def main() -> None:
    """
    Миграция для таблицы link_stat (вариант 2).

    Хранит трекаемые ссылки для редиректа /r/{slug}:
    - slug (уникальный ключ),
    - target_url (куда ведём),
    - clicks (количество переходов),
    - created_at (когда создано).
    """
    dsn = os.getenv(
        "POSTGRES_DSN",
        "postgresql+psycopg2://user:password@postgres:5432/mb_db",
    )
    engine = create_engine(dsn)

    ddl = """
          CREATE TABLE IF NOT EXISTS link_stat (
                                                   id SERIAL PRIMARY KEY,
                                                   slug TEXT NOT NULL UNIQUE,
                                                   target_url TEXT NOT NULL,
                                                   clicks INTEGER NOT NULL DEFAULT 0,
                                                   created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
              );
          """

    # Если раньше у тебя уже была таблица links — перенесём данные в link_stat
    # (не удаляем links автоматически, чтобы не было сюрпризов).
    migrate_from_links = """
                         INSERT INTO link_stat (slug, target_url, clicks)
                         SELECT slug, target_url, clicks
                         FROM links
                             ON CONFLICT (slug) DO UPDATE SET
                             target_url = EXCLUDED.target_url,
                                                       clicks = GREATEST(link_stat.clicks, EXCLUDED.clicks); \
                         """

    with engine.begin() as conn:
        conn.execute(text(ddl))
        # Пытаемся мигрировать, но если таблицы links нет — просто пропускаем.
        try:
            conn.execute(text(migrate_from_links))
        except Exception:
            pass

    print("link_stat table ensured (and migrated from links if existed)")


if __name__ == "__main__":
    main()
