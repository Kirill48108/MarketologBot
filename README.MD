# MarketologBot

Асинхронный Telegram‑userbot, который от имени **обычных аккаунтов**:

- читает посты в заданных каналах;
- оставляет короткие осмысленные комментарии:
  - по расписанию (scheduler),
  - по событию — на новые посты (instant);
- использует LLM (через OpenAI‑совместимый API / Ollama) для генерации текста;
- учитывает лимиты Telegram и спамблоки:
  - окна активности (утро / вечер),
  - дневной лимит сообщений,
  - лимиты по каналам,
  - авто‑пауза при подозрении на спамблок;
- хранит логи отправленных сообщений в PostgreSQL и кэширует ответы в Redis.

Стек:

- **Python 3.13**, Poetry;
- **FastAPI + Uvicorn** (HTTP‑оболочка, health/metrics/admin);
- **Telethon + StringSession** — работа от обычных Telegram‑аккаунтов;
- **SQLAlchemy + PostgreSQL**, **Redis**;
- **Ollama / OpenAI‑совместимый LLM** (по умолчанию `llama3.1:8b`);
- Docker + docker‑compose.

---

## Архитектура

Основные компоненты:

- `app/bot/userbot.py` — логика одного Telegram‑аккаунта:
  - авторизация через Telethon и `StringSession`;
  - планировщик сообщений (`_scheduler_loop` + `_tick_send`);
  - instant‑ветка обработки новых постов (`_on_new_channel_post`);
  - учёт allowlist‑каналов;
  - лимиты по времени/суткам/каналам;
  - анти‑спамблок как по аккаунту, так и по отдельным каналам.
- `app/services/llm.py` — обёртка над OpenAI‑совместимым API:
  - работа с Ollama / любым совместимым endpoint;
  - retry через `tenacity`;
  - мягкая нормализация и валидация русскоязычного текста.
- `app/config.py` — конфигурация через `pydantic-settings` (`.env`, `.env.botN`).
- `main.py` — точка входа FastAPI:
  - HTTP‑эндпоинты (здоровье, метрики, при необходимости — простые админские ручки);
  - запуск фоновой задачи `UserBot.start()`.

Один Docker‑контейнер `bot` ≈ один userbot‑аккаунт.

---

## Установка и запуск (локально)

### 1. Установка через Poetry
- bash pip install poetry==1.8.4 poetry install

### 2. Настройка `.env`

- Скопируйте пример: bash cp .env.example .env


Заполните в `.env`:

- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` — с https://my.telegram.org;
- `TELEGRAM_SESSION_STRING` — строка сессии (см. ниже);
- `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `LLM_MODEL`:
  - для локальной Ollama по умолчанию:
    ```env
    OPENAI_BASE_URL=http://localhost:11434/v1
    OPENAI_API_KEY=ollama
    LLM_MODEL=llama3.1:8b
    ```
- `POSTGRES_DSN` — строка подключения к PostgreSQL;
- `REDIS_URL` — строка подключения к Redis;
- `ALLOWLIST_CHAT_IDS` — список `chat_id` каналов, где этот аккаунт может писать.

### 3. Генерация `TELEGRAM_SESSION_STRING`

Временно оставьте `TELEGRAM_SESSION_STRING` пустым в `.env` и запустите:
- bash poetry run python session_gen.py


Скрипт:

- попросит номер телефона и код из Telegram;
- после успешного входа выведет строку сессии;
- скопируйте её в `TELEGRAM_SESSION_STRING` в `.env`.

Аналогично можно сгенерировать сессии для `.env.bot1`, `.env.bot2` и т.д.

### 4. Локальный запуск без Docker

Если у вас уже есть Postgres, Redis и Ollama:
- bash poetry run uvicorn main:app --reload

Проверка: 
- bash curl http://127.0.0.1:8000/health


---

## Запуск через Docker

В репозитории есть `docker-compose.yml`, который поднимает:

- `postgres` (порт 5433 на хосте),
- `redis` (порт 6380),
- `ollama` (порт 11434),
- несколько ботов:
  - `bot`   — `.env`, API на `:8000`;
  - `bot1`  — `.env.bot1`, API на `:8001`;
  - `bot2`  — `.env.bot2`, API на `:8002`;
  - `bot3`  — `.env.bot3`, API на `:8003`;
  - …
  - `bot8`  — `.env.bot8`, API на `:8008`.

> В публичном репозитории `.env` / `.env.botN` хранить не нужно — достаточно `.env.example`.  
> Рабочие файлы `.env*` должны лежать только локально.

### 1. Подготовка `.env*`

Для каждого аккаунта создайте файл:

- `.env`      — основной бот,
- `.env.bot1` — второй аккаунт,
- `.env.bot2` — третий,
- …

В каждом:

- свой `TELEGRAM_SESSION_STRING`;
- свой `ALLOWLIST_CHAT_IDS` (желательно не более 15–20 каналов на аккаунт);
- при желании — разные тайминги и окна активности.

Пример см. в `.env.example`.

### 2. Старт инфраструктуры и ботов

Запуск Postgres/Redis/Ollama и основного бота:
- bash docker compose up -d --build postgres redis ollama bot


Если нужно несколько ботов:
- bash docker compose up -d --build bot bot1 bot2

# или сразу все
# docker compose up -d --build bot bot1 bot2 bot3 bot4 bot5 bot6 bot7 bot8

``` 
Просмотр логов:
```
bash docker compose logs -f bot docker compose logs -f bot1 docker compose logs -f bot2
``` 

---

## Конфигурация бота через `.env`

Ниже пример настроек для одного аккаунта (обобщённо). Для конкретного бота они лежат в `.env` или `.env.botN`.

### Telegram и базовые настройки
```
env TELEGRAM_API_ID=1234567 TELEGRAM_API_HASH=your_api_hash_here TELEGRAM_SESSION_STRING=your_string_session_here
BOT_ENABLED=true
# Список каналов, где этот аккаунт может писать
ALLOWLIST_CHAT_IDS=[-100..., -100...]
``` 

### Лимиты и окна активности
```
env
# Окна активности по локальному времени (часы: 0–24, правая граница не включительна)
ACTIVE_WINDOWS=5-10,18-24 # утро 5–10, вечер 18–24
# Дневной и часовой лимит
MESSAGES_PER_DAY=40 # максимум сообщений в сутки MESSAGES_PER_HOUR=8 # средняя целевая интенсивность
# Минимальный интервал между любыми двумя сообщениями аккаунта
MIN_INTERVAL_BETWEEN_MESSAGES_SECONDS=90
# Минимальный интервал между сообщениями в один и тот же канал
MIN_INTERVAL_PER_CHAT_SECONDS=2400 # 40 минут
# Максимальное количество instant-комментариев в час на один канал
MAX_INSTANT_PER_CHAT_PER_HOUR=2
# Максимальный возраст поста (минуты), для которого бот ещё готов писать комментарий
FRESH_POST_MAX_AGE_MINUTES=1440
``` 

### LLM / Ollama
```
env OPENAI_BASE_URL=[http://ollama:11434/v1](http://ollama:11434/v1) OPENAI_API_KEY=ollama LLM_MODEL=llama3.1:8b
LLM_TEMPERATURE=0.4 LLM_MIN_LEN=30 LLM_MAX_LEN=220 LLM_MAX_EMOJIS=1 LLM_STRICT_VALIDATE=false LLM_TOP_P=0.9 LLM_FREQUENCY_PENALTY=0.3 LLM_PRESENCE_PENALTY=0.2
# Задержка (секунды) перед instant-комментарием на новый пост
INSTANT_COMMENT_DELAY_SECONDS=300
``` 

### Анти‑спамблок
```
env
# Минимальное время "тишины" (сек), когда бот перестаёт писать после серии PeerFlood/FloodWait
SPAMBLOCK_MIN_COOLDOWN_SECONDS=3600
# Максимальное время "тишины" (сек)
SPAMBLOCK_MAX_COOLDOWN_SECONDS=86400
# Сколько подряд серьёзных ошибок (PeerFlood/FloodWait) нужно,
# чтобы считать аккаунт временно заблокированным
SPAMBLOCK_ERROR_THRESHOLD=3
``` 

> Для рисковых аккаунтов можно сделать:
> 
> ```env
> SPAMBLOCK_MIN_COOLDOWN_SECONDS=7200
> SPAMBLOCK_MAX_COOLDOWN_SECONDS=172800
> SPAMBLOCK_ERROR_THRESHOLD=2
> ```

### Хранилища и админ
```
env POSTGRES_DSN=postgresql+psycopg2://user:password@postgres:5432/mb_db REDIS_URL=redis://redis:6379/0 CACHE_TTL_SECONDS=900
ADMIN_TOKEN=change-me
``` 

### Прокси (опционально)
```
env
# TG_PROXY_HOST=your_proxy_host
# TG_PROXY_PORT=1080
# TG_PROXY_USER=your_proxy_user
# TG_PROXY_PASS=your_proxy_password
``` 

---

## Поведение и лимиты

Бот старается вести себя «по‑человечески»:

- пишет только в свои allowlist‑каналы;
- не пишет ночью (если не входит в `ACTIVE_WINDOWS`);
- не превышает:
  - дневной лимит `MESSAGES_PER_DAY`;
  - минимальные интервалы между сообщениями;
  - максимальное количество instant‑комментариев в час на канал;
- не пытается писать в канал, если получил `ChatWriteForbiddenError` / `UserBannedInChannelError`:
  - канал заносится в локальный blacklist `_banned_chats`;
- если подряд приходит несколько `PeerFloodError` / жёстких `FloodWaitError`:
  - выставляет глобальный спамблок на аккаунт на время между `SPAMBLOCK_MIN_COOLDOWN_SECONDS` и `SPAMBLOCK_MAX_COOLDOWN_SECONDS`;
  - до окончания этого времени бот не будет пытаться отправлять сообщения.

---

## Тесты и качество кода

Запуск тестов:
```
bash poetry run pytest
``` 

Проверка стиля/типов:
```
bash poetry run ruff check . poetry run black --check . poetry run mypy .
``` 

---

## Идеи для дальнейшего развития

- Веб‑панель для управления allowlist и лимитами по аккаунтам.
- Админ‑эндпоинты для:
  - паузы/перезапуска аккаунта;
  - ручного “размораживания” после спамблока;
  - просмотра логов отправленных сообщений.
- Более продвинутая аналитика по каналам (частота постов, CTR комментариев и т.п.).

Проект в таком виде удобно использовать как демо:

- работы с Telegram API на userbot‑уровне;
- аккуратной конфигурации и учёта лимитов;
- интеграции с LLM и окружением (Postgres/Redis/Ollama/Docker).
```
