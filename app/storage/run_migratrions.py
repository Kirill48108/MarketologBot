from collections.abc import Callable


def main() -> None:
    # важно: порядок
    from app.storage.migrations_init_channel_status import main as m_channel_status
    from app.storage.migrations_init_links import main as m_links  # теперь link_stat (вариант 2)
    from app.storage.migrations_init_message_log import main as m_message_log

    m_msg_bot_name: Callable[[], None] | None
    try:
        from app.storage.migrations_add_bot_name_to_message_log import main as _m_msg_bot_name

        m_msg_bot_name = _m_msg_bot_name
    except Exception:
        m_msg_bot_name = None

    m_message_log()
    if m_msg_bot_name is not None:
        m_msg_bot_name()
    m_channel_status()
    m_links()

    print("All migrations executed")


if __name__ == "__main__":
    main()
