from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BotConfig:
    name: str
    base_url: str  # Базовый URL админ-API бота (как его видит центр)


# Пока фиксированный список. Потом можно будет увести в БД.
BOTS: dict[str, BotConfig] = {
    "bot0": BotConfig(name="bot0", base_url="http://bot:8000"),
    "bot1": BotConfig(name="bot1", base_url="http://bot1:8000"),
    "bot2": BotConfig(name="bot2", base_url="http://bot2:8000"),
    "bot3": BotConfig(name="bot3", base_url="http://bot3:8000"),
    "bot4": BotConfig(name="bot4", base_url="http://bot4:8000"),
    "bot5": BotConfig(name="bot5", base_url="http://bot5:8000"),
    "bot6": BotConfig(name="bot6", base_url="http://bot6:8000"),
    "bot7": BotConfig(name="bot7", base_url="http://bot7:8000"),
    "bot8": BotConfig(name="bot8", base_url="http://bot8:8000"),
    "bot9": BotConfig(name="bot9", base_url="http://bot9:8000"),
}
