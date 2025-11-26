import pytest

from app.services.llm import (
    LLMClient,
    _normalize_emojis_to_end,
    _sanitize,
    _soft_truncate,
)


def test_sanitize_removes_code_blocks_and_prefix():
    raw = "Ответ: ```python\nprint('x')\n```   Привет, как дела?"
    cleaned = _sanitize(raw)
    assert "```" not in cleaned
    assert "print" not in cleaned
    assert cleaned.startswith("Привет")


def test_sanitize_strips_quotes_and_spaces():
    raw = '  "«Привет мир!»"  '
    cleaned = _sanitize(raw)
    assert cleaned == "Привет мир!"


def test_normalize_emojis_to_end_keeps_text_without_emojis():
    # В этом тексте нет эмодзи, функция должна вернуть его как есть
    text = "Привет  как дела? "
    normalized = _normalize_emojis_to_end(text, max_emoji=1)
    assert normalized == text


def test_normalize_emojis_to_end_keeps_pure_emojis():
    text = ""
    normalized = _normalize_emojis_to_end(text, max_emoji=2)
    # чисто эмодзи-сообщение не меняется
    assert normalized == text


@pytest.mark.parametrize(
    "text,max_len,expected",
    [
        ("Короткое сообщение.", 100, "Короткое сообщение."),
        (
            "Очень длинное сообщение. Которое мы обрежем где-то после первой точки.",
            40,
            "Очень длинное сообщение.",
        ),
        (
            "Фраза без точки и вопроса но с пробелами",
            25,
            "Фраза без точки и",
        ),
    ],
)
def test_soft_truncate(text, max_len, expected):
    truncated = _soft_truncate(text, max_len)
    assert truncated == expected


def test_extract_seed_from_post_drops_links_and_keeps_words():
    post = "Смотрите обзор: https://example.com/test на новый электромобиль Tesla model 3."
    seed = LLMClient.extract_seed_from_post(post)
    assert "http" not in seed
    # должен начинаться со слова "смотрите"
    assert seed.split()[0] == "смотрите"
    # и содержать слово "обзор"
    assert "обзор" in seed
