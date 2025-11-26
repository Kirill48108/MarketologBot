import re
from typing import Dict, Pattern, Tuple

RE_FLAGS = re.IGNORECASE | re.MULTILINE

# Базовые группы
BUY = r"(купить|покупа[юе]т?|ищу|возьм[ыу]|приобрет[еу]|подбор|подбер[еу]|)"
SELL = r"(прода[юе]т?|выстав(лю|ить)|объявлени[ея]|продажа)"
CAR = r"(авто(мобиль)?|машин[аиы]|тачк[аи]|коробк[аи])"
DETAILS = r"(бюджет|цена|торг|обмен|рассрочка|кредит|без дтп|после дтп|истори[ия]|vin|диагностик[аи]|осмотр|дилер|авторынок)"
BRANDS = r"(bmw|mercedes|a(udi|уди)|toyota|honda|hyundai|kia|volkswagen|vw|skoda|lada|renault|nissan|lexus|mazda|ford|chevrolet)"
NEGATIVE = r"(игруш(ечн|к)|видеоигр|модель\s?1:?43|детск(ая|ие)|лаборатори|авто(мат|деплой|скрипт)|autodeploy|gitlab)"

PATTERNS: Dict[str, Pattern[str]] = {
    "buy": re.compile(BUY, RE_FLAGS),
    "sell": re.compile(SELL, RE_FLAGS),
    "car": re.compile(CAR, RE_FLAGS),
    "details": re.compile(DETAILS, RE_FLAGS),
    "brands": re.compile(BRANDS, RE_FLAGS),
    "negative": re.compile(NEGATIVE, RE_FLAGS),
}



def normalize(text: str) -> str:
    return text.replace("ё", "е").strip()


def score(text: str) -> Tuple[int, Dict[str, bool]]:
    t = normalize(text)
    if PATTERNS["negative"].search(t):
        return 0, {"negative": True}

    s = 0
    hits: Dict[str, bool] = {}
    for k in ("buy", "sell", "car", "details", "brands"):
        m = PATTERNS[k].search(t)
        if m:
            hits[k] = True
            if k in ("buy", "sell"):
                s += 3
            elif k == "car":
                s += 2
            else:
                s += 1
    return s, hits

THRESHOLD = 3  # минимальный балл чтобы реагировать
