import os

from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.getenv("TELEGRAM_API_ID") or input("Enter API_ID: ").strip())
API_HASH = os.getenv("TELEGRAM_API_HASH") or input("Enter API_HASH: ").strip()

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    print("Logged in as:", client.session.save())
    # ВНИМАНИЕ: следующая строка выводит сам session string
    print("SESSION_STRING:")
    print(client.session.save())
