import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

ROOT_DIR = Path(__file__).resolve().parents[2]  # корень проекта MarketologBot


def load_bot_configs() -> list[dict[str, Any]]:
    dsn = os.getenv(
        "POSTGRES_DSN",
        "postgresql+psycopg2://user:password@postgres:5432/mb_db",
    )
    engine = create_engine(dsn)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    name,
                    env_filename,
                    telegram_api_id,
                    telegram_api_hash,
                    session_string,
                    allowlist_chat_ids,
                    autojoin_chat_ids,
                    enabled
                FROM bot_configs
                ORDER BY name
                """
            )
        ).mappings()
        return [dict(r) for r in rows]


def render_env(cfg: dict[str, Any]) -> str:
    """
    Генерируем содержимое .env/.env.botX из записи bot_configs.
    Остальные настройки берём из .env.example вручную/по умолчанию.
    """
    enabled = "true" if cfg["enabled"] else "false"

    lines = [
        f"TELEGRAM_API_ID={cfg['telegram_api_id']}",
        f"TELEGRAM_API_HASH={cfg['telegram_api_hash']}",
        f"TELEGRAM_SESSION_STRING={cfg['session_string']}",
        "",
        f"BOT_ENABLED={enabled}",
        f"ALLOWLIST_CHAT_IDS={cfg['allowlist_chat_ids']}",
        f"AUTOJOIN_CHAT_IDS={cfg['autojoin_chat_ids']}",
        "",
        "# Остальные параметры задаются через .env.example или уже существуют в файле.",
    ]
    return "\n".join(lines) + "\n"


def write_env_files() -> None:
    configs = load_bot_configs()
    if not configs:
        print("No bot_configs found")
        return

    for cfg in configs:
        env_path = ROOT_DIR / cfg["env_filename"]
        env_path.write_text(render_env(cfg), encoding="utf-8")
        print(f"Written {env_path} for bot {cfg['name']}")


if __name__ == "__main__":
    write_env_files()
