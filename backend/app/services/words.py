import re

from . import tags

_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+(?:[-'][0-9A-Za-zА-Яа-яЁё]+)*")


def count_words(text: str) -> int:
    """Правило подсчета слов MVP: слова без служебных тегов, по регулярному выражению.

    Токены-теги (<x id="1"/>, <g id="2"></g>) словами не считаются.
    """
    plain = tags.strip_tags(text)
    return len(_WORD_RE.findall(plain))
