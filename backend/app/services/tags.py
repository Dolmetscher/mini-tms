"""Служебные теги сегмента (FR-05).

Поддерживаемый профиль inline-тегов:
- `<x id="N"/>` — отдельный (placeholder) тег;
- `<g id="N">` / `</g>` — парный тег-обертка.

Теги хранятся в тексте сегмента как literal-токены. При экспорте в XLIFF они
восстанавливаются в оригинальные inline-элементы по id из исходного файла.
"""
import re

X_TOKEN_RE = re.compile(r'<x id="[^"]+"\s*/>')
G_OPEN_RE = re.compile(r'<g id="[^"]+">')
G_CLOSE_RE = re.compile(r"</g>")
ANY_TAG_RE = re.compile(r'<x id="[^"]+"\s*/>|<g id="[^"]+">|</g>')
_ID_RE = re.compile(r'id="([^"]+)"')


def strip_tags(text: str) -> str:
    return ANY_TAG_RE.sub("", text)


def extract_x_ids(text: str) -> set[str]:
    """Все id standalone-тегов <x id="..."/> в тексте."""
    return set(_ID_RE.findall(" ".join(X_TOKEN_RE.findall(text))))


def g_open_ids_in_order(text: str) -> list[str]:
    return _ID_RE.findall(" ".join(G_OPEN_RE.findall(text)))


def validate_tags(source: str, target: str) -> list[str]:
    """Ошибки тегов перевода относительно исходника. Пустой список = ошибок нет."""
    errors: list[str] = []

    src_x, tgt_x = extract_x_ids(source), extract_x_ids(target)
    for missing in sorted(src_x - tgt_x):
        errors.append(f'Отсутствует тег <x id="{missing}"/>')
    for extra in sorted(tgt_x - src_x):
        errors.append(f'Лишний тег <x id="{extra}"/>')

    src_g = sorted(g_open_ids_in_order(source))
    tgt_g = sorted(g_open_ids_in_order(target))
    for missing in sorted(set(src_g) - set(tgt_g)):
        errors.append(f'Отсутствует парный тег <g id="{missing}">')
    for extra in sorted(set(tgt_g) - set(src_g)):
        errors.append(f'Лишний парный тег <g id="{extra}">')

    # Баланс и корректность вложенности <g> в переводе.
    stack: list[str] = []
    position = 0
    token_re = re.compile(r'<g id="([^"]+)">|</g>')
    for match in token_re.finditer(target):
        position += 1
        open_id = match.group(1)
        if open_id is not None:
            stack.append(open_id)
        else:
            if not stack:
                errors.append(f'Лишний </g> в позиции тега {position}')
                break
            stack.pop()
    if stack:
        errors.append(f'Незакрытый <g id="{stack[-1]}">')

    return errors


def has_tags(text: str) -> bool:
    return bool(ANY_TAG_RE.search(text))
