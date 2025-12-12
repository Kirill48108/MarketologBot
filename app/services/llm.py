import logging
import os
import random
import re
from typing import Any, List, Optional

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

logger = logging.getLogger("app.services.llm")

DEFAULT_TOPICS: List[str] = [
    "Полезные жизненные советы и личный опыт",
    "Дискуссии о работе, карьере и саморазвитии",
    "Повседневные ситуации и реальные истории",
    "Обсуждение новостей, технологий и трендов",
    "Отношения, общение и психологический комфорт",
    "Хобби, отдых, путешествия и впечатления",
    "Финансы, экономия и осознанные покупки",
    "Здоровье, спорт и хорошее самочувствие",
    "Образование, обучение новому и мотивация",
    "Цели, планы и личная эффективность",
]

_SANITIZE_PREFIXES = (
    "ответ:",
    "ответ",
    "reply:",
    "reply",
    "тема:",
    "topic:",
    "system:",
    "user:",
    "assistant:",
)

_LATIN_RE = re.compile(r"[A-Za-z]")
_CJK_RE = re.compile(r"[\u3400-\u9FFF\uF900-\uFAFF]")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_EMOJI_RE = re.compile(
    r"[\u2600-\u27BF\u1F300-\u1F6FF\u1F700-\u1F77F\u1F780-\u1F7FF\u1F800-\u1F8FF\u1F900-\u1F9FF\u1FA00-\u1FAFF]",
    flags=re.UNICODE,
)
_URL_RE = re.compile(r"http[s]?://\S+|www\.\S+", flags=re.IGNORECASE)
_END_PUNCT_RE = re.compile(r"[.!?…]$")
_CYR_WORD_RE = re.compile(r"\b[А-Яа-яЁё][А-Яа-яЁё\-]{1,}\b")

# Эмодзи для тихих фолбэков
_FALLBACK_EMOJIS = ["🙂", "😊", "😉", "😄", "👍", "👌", "🤝", "🤔", "😁", "😌"]


def _sanitize(text: str) -> str:
    """
    Аккуратная чистка: убираем служебные штуки и код, но не трогаем сам текст и эмодзи.
    """
    if not text:
        return ""
    t = text.strip()
    # убрать блоки кода
    t = re.sub(r"```.+?```", " ", t, flags=re.S)
    # убрать кавычки по краям
    t = t.strip(" \"'“”«»")
    # убрать служебные префиксы (ответ:, reply:, user: и т.д.)
    low = t.lower()
    for p in _SANITIZE_PREFIXES:
        if low.startswith(p):
            t = re.sub(rf"(?i)^{re.escape(p)}\s*[:\-–—]?\s*", "", t).lstrip()
            break
    # нормализовать пробелы
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _normalize_emojis_to_end(text: str, max_emoji: int = 2) -> str:
    """
    Лёгкая нормализация: если эмодзи размазаны по тексту, соберём до max_emoji в конец.
    Если текст состоит только из эмодзи — оставляем как есть.
    """
    if not text:
        return text
    # Если в тексте нет букв/цифр, то это и так «эмодзи‑сообщение» — не трогаем
    if not re.search(r"\w", text, flags=re.UNICODE):
        return text

    emojis = _EMOJI_RE.findall(text)
    if not emojis:
        return text

    kept = emojis[:max_emoji]
    no_emoji_text = _EMOJI_RE.sub("", text).strip()
    if not no_emoji_text:
        # Всё было эмодзи — вернём исходное
        return text

    if not no_emoji_text.endswith(" "):
        no_emoji_text += " "
    no_emoji_text += "".join(kept)
    return no_emoji_text.strip()


def _soft_truncate(text: str, max_len: int) -> str:
    """
    Аккуратная обрезка: стараемся не рвать предложение посередине.
    1) если строка короче max_len — возвращаем как есть;
    2) ищем последнюю точку/воскл./вопрос в пределах max_len;
    3) если не нашли — ищем последний пробел;
    4) если тоже нет — режем жёстко по max_len.
    """
    if len(text) <= max_len:
        return text
    snippet = text[:max_len]
    # сначала ищем конец предложения
    for ch in ".!?…":
        idx = snippet.rfind(ch)
        if idx != -1 and idx >= max_len // 2:
            return snippet[: idx + 1].strip()
    # потом — ближайший пробел
    space_idx = snippet.rfind(" ")
    if space_idx != -1 and space_idx >= max_len // 2:
        return snippet[:space_idx].strip()
    # если ничего подходящего — жёсткая обрезка
    return snippet.strip()


def _basic_lang_ok(text: str) -> bool:
    if not _CYRILLIC_RE.search(text):
        return False
    if _LATIN_RE.search(text):
        return False
    if _CJK_RE.search(text):
        return False
    if _URL_RE.search(text):
        return False
    return True


def _is_valid(text: str, min_len: int, max_len: int) -> bool:
    if not text:
        return False
    t = text.strip()
    if len(t) < min_len or len(t) > max_len:
        return False
    if not _basic_lang_ok(t):
        return False
    if re.fullmatch(r"[\W_]+", t, flags=re.UNICODE):
        return False
    if "```" in t:
        return False
    if len(_CYR_WORD_RE.findall(t)) < 3:
        return False
    if not re.match(r"^[А-Яа-яЁё]", t):
        return False
    if not _END_PUNCT_RE.search(t):
        return False
    return True


def _safe_fallback(topic: str) -> str:
    """
    Безопасный фолбэк: 1–3 нейтральных эмодзи.
    Нужен, если модель вообще ничего не вернула или всё совсем сломалось.
    """
    count = random.randint(1, 3)
    emojis = random.sample(_FALLBACK_EMOJIS, k=count)
    return "".join(emojis)


def _is_offtopic(text: str, seed: str) -> bool:
    """
    Оффтоп не режем, позволяем говорить на любые темы.
    """
    return False


class LLMClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        style_prompt: Optional[str] = None,
        extra_topics: Optional[List[str]] = None,
        temperature: Optional[float] = None,
        min_len: Optional[int] = None,
        max_len: Optional[int] = None,
        max_emojis: Optional[int] = None,
    ):
        # OpenAI-compatible клиент (в т.ч. Ollama)
        self.client: Any = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.style_prompt = style_prompt or ""
        self.topics = (extra_topics or []) + DEFAULT_TOPICS

        def _env_float(k: str, d: float) -> float:
            try:
                v = os.getenv(k)
                return float(v) if v is not None else d
            except Exception:
                return d

        def _env_int(k: str, d: int) -> int:
            try:
                v = os.getenv(k)
                return int(v) if v is not None else d
            except Exception:
                return d

        def _env_bool(k: str, d: bool) -> bool:
            v = os.getenv(k)
            if v is None:
                return d
            return str(v).strip().lower() in ("1", "true", "yes", "y", "on")

        # параметры из .env
        self.temperature = (
            temperature if temperature is not None else _env_float("LLM_TEMPERATURE", 0.30)
        )
        self.min_len = min_len if min_len is not None else _env_int("LLM_MIN_LEN", 60)
        self.max_len = max_len if max_len is not None else _env_int("LLM_MAX_LEN", 250)
        self.max_emojis = max_emojis if max_emojis is not None else _env_int("LLM_MAX_EMOJIS", 2)
        self.strict_validate = _env_bool("LLM_STRICT_VALIDATE", True)

        # ручки разнообразия
        self.top_p = _env_float("LLM_TOP_P", 0.9)
        self.frequency_penalty = _env_float("LLM_FREQUENCY_PENALTY", 0.2)
        self.presence_penalty = _env_float("LLM_PRESENCE_PENALTY", 0.1)

        logger.info(
            "LLMClient init: strict_validate=%s, min_len=%s, max_len=%s",
            self.strict_validate,
            self.min_len,
            self.max_len,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential_jitter(initial=0.5, max=5))
    async def generate_random_message(self, seed_hint: Optional[str] = None) -> str:
        topic = (seed_hint or "").strip() or random.choice(self.topics)
        system = (
            "Ты пишешь одно короткое дружелюбное сообщение в обсуждении в Telegram. "
            "Отвечай только на русском, без ссылок и латиницы. Поддерживай тему, говори по делу, без флуда. "
            f"Пиши примерно {self.min_len}–{self.max_len} символов. "
            "Только текст."
        )
        if self.style_prompt:
            system += f" Стиль: {self.style_prompt}"
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"Оставь короткий естественный комментарий по теме: «{topic}». Без ссылок.",
            },
        ]
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            top_p=self.top_p,
            max_tokens=min(4096, int(self.max_len * 1.3)),
            presence_penalty=self.presence_penalty,
            frequency_penalty=self.frequency_penalty,
        )

        # Короткий лог «как есть» от LLM (random)
        try:
            raw_debug = (resp.choices[0].message.content or "").strip()
            logger.info("LLM raw (random) len=%s preview=%r", len(raw_debug), raw_debug[:120])
        except Exception as e:
            logger.warning("LLM raw (random) debug logging failed: %s", e)

        raw = (resp.choices[0].message.content or "").strip()

        # strict_validate = False → вообще не трогаем ответ модели, только мягко обрезаем по длине
        if not self.strict_validate:
            if raw:
                return _soft_truncate(raw, self.max_len)
            return _safe_fallback(topic)

        # strict_validate = True → строгая логика
        text = _sanitize(raw)
        text = _normalize_emojis_to_end(text, max_emoji=self.max_emojis)

        if _is_valid(text, self.min_len, self.max_len):
            return text
        if text and _basic_lang_ok(text):
            return text[: self.max_len]
        return _safe_fallback(topic)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential_jitter(initial=0.5, max=5))
    async def generate_contextual_message(
        self, post_text: str, comment_text: Optional[str] = None
    ) -> str:
        """
        Контекстная генерация: до 3 попыток + мягкий/жёсткий фолбэки.
        """
        post_excerpt = _sanitize(post_text or "")[:400]
        comment_excerpt = _sanitize(comment_text or "")[:200] if comment_text else None

        system = (
            "Ты пишешь одно короткое дружелюбное сообщение в обсуждении в Telegram. "
            "Отвечай только на русском, без ссылок и латиницы. Поддерживай тему поста или комментария, говори по делу, без флуда. "
            f"Пиши примерно {self.min_len}–{self.max_len} символов. "
            "Отвечай естественно, как живой человек. Только текст."
        )
        if self.style_prompt:
            system += f" Стиль: {self.style_prompt}"

        if comment_excerpt:
            user_base = f"Тема поста: «{post_excerpt}». Ответь на комментарий: «{comment_excerpt}»."
        else:
            user_base = f"Оставь короткий естественный комментарий по теме поста: «{post_excerpt}»."

        attempts = [
            (self.temperature, user_base),
            (max(0.0, self.temperature - 0.04), user_base + " Не меняй тему."),
            (
                min(1.0, self.temperature + 0.04),
                user_base + " Избегай общих фраз, будь конкретен и по теме.",
            ),
        ]
        last_sanitized = ""
        for temperature, user in attempts:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=temperature,
                top_p=self.top_p,
                max_tokens=min(4096, int(self.max_len * 1.3)),
                presence_penalty=self.presence_penalty,
                frequency_penalty=self.frequency_penalty,
            )

            # Короткий лог «как есть» от LLM (context)
            try:
                raw_debug = (resp.choices[0].message.content or "").strip()
                logger.info(
                    "LLM raw (context) len=%s preview=%r temp=%.2f",
                    len(raw_debug),
                    raw_debug[:120],
                    temperature,
                )
            except Exception as e:
                logger.warning("LLM raw (context) debug logging failed: %s", e)

            raw = (resp.choices[0].message.content or "").strip()

            # strict_validate=False — вообще не трогаем текст, сразу мягко обрезаем сырое
            if not self.strict_validate:
                if raw:
                    return _soft_truncate(raw, self.max_len)
                # если пусто — пробуем следующие попытки
                continue

            # strict_validate=True — строгая логика
            text = _sanitize(raw)
            text = _normalize_emojis_to_end(text, max_emoji=self.max_emojis)

            if _is_valid(text, self.min_len, self.max_len):
                return text
            if text:
                last_sanitized = text

        # strict_validate=False: если все попытки вернули пусто, используем фолбэк
        if not self.strict_validate:
            seed = post_excerpt or (comment_excerpt or "обсуждение")
            return _safe_fallback(seed)

        # strict_validate=True: мягкий фолбэк по последнему варианту
        if last_sanitized and _basic_lang_ok(last_sanitized):
            return last_sanitized[: self.max_len]
        seed = post_excerpt or (comment_excerpt or "обсуждение")
        return _safe_fallback(seed)

    @staticmethod
    def extract_seed_from_post(post_text: str) -> str:
        """
        Извлекает «семя» из текста поста: убирает ссылки и оставляет первые значимые слова.
        Нужен для фолбэка random-сценария, когда контекстная генерация вернула пусто.
        """
        t = (post_text or "").lower()
        t = re.sub(r"http\S+", " ", t)
        words = [w for w in re.findall(r"[a-zа-я0-9\-]+", t, flags=re.IGNORECASE) if len(w) > 2]
        return " ".join(words[:8]) if words else ""
