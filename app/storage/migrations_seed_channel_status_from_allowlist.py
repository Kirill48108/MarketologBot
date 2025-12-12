import ast
import os

from sqlalchemy import create_engine, text


def _parse_allowlist(raw: str) -> list[int]:
    """
    Разобрать ALLOWLIST_CHAT_IDS из строки вида "[ -1001, -1002 ]" или "[-1001,-1002]".
    Возвращает список int.
    """
    raw = (raw or "").strip()
    if not raw:
        return []

    try:
        val = ast.literal_eval(raw)
        if isinstance(val, (list, tuple, set)):
            return [int(x) for x in val]
    except Exception:
        pass

    # Фоллбек: CSV-строка
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    result: list[int] = []
    for p in parts:
        try:
            result.append(int(p))
        except Exception:
            continue
    return result


def main() -> None:
    """
    Одноразовая миграция:
    - читает ALLOWLIST_CHAT_IDS из окружения;
    - для каждого chat_id создаёт строку в channel_status со статусом 'ok'
      для текущего бота (BOT_NAME или 'bot0');
    - если такая пара (bot_name, chat_id) уже есть — пропускает.
    """
    dsn = os.getenv(
        "POSTGRES_DSN",
        "postgresql+psycopg2://user:password@postgres:5432/mb_db",
    )
    engine = create_engine(dsn)

    bot_name = os.getenv("BOT_NAME", "bot0")
    raw_allow = os.getenv("ALLOWLIST_CHAT_IDS", "[]")
    allowlist_ids = _parse_allowlist(raw_allow)

    if not allowlist_ids:
        print("No ALLOWLIST_CHAT_IDS in environment, nothing to seed")
        return

    ddl = """
          INSERT INTO channel_status (bot_name, chat_id, status)
          VALUES (:bot_name, :chat_id, 'ok')
              ON CONFLICT (bot_name, chat_id) DO NOTHING \
          """

    with engine.begin() as conn:
        for cid in allowlist_ids:
            conn.execute(
                text(ddl),
                {"bot_name": bot_name, "chat_id": cid},
            )

    print(f"Seeded channel_status for bot_name={bot_name}, chats={allowlist_ids}")


if __name__ == "__main__":
    main()
