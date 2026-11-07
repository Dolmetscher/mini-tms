import re

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Приводит текст к каноническому виду для сравнения: нижний регистр, схлопнутые пробелы."""
    return _WHITESPACE.sub(" ", text.strip().casefold())


def levenshtein_distance(s1: str, s2: str) -> int:
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)
    previous = list(range(len(s2) + 1))
    for i, ch1 in enumerate(s1, start=1):
        current = [i]
        for j, ch2 in enumerate(s2, start=1):
            cost = 0 if ch1 == ch2 else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        previous = current
    return previous[-1]


def similarity_ratio(s1: str, s2: str) -> int:
    """Схожесть двух строк в процентах (0-100), нормализованных по регистру и пробелам."""
    n1, n2 = normalize(s1), normalize(s2)
    if not n1 and not n2:
        return 100
    longest = max(len(n1), len(n2))
    if longest == 0:
        return 100
    return int(round((longest - levenshtein_distance(n1, n2)) / longest * 100))
