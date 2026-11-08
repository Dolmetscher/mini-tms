"""Выравнивание параллельных текстов (alignment) — наполнение TM из архива.

Классическая задача CAT: у переводчика есть старые «исходник + перевод»
(прошлые версии сайта, договоры, документация), и этот багаж хочется вернуть
в TM. TMX/CSV нет — есть только два файла.

Алгоритм (легковесный, без моделей):

1. Оба файла режутся на единицы (те же сплиттеры, что при импорте документов:
   txt — непустые строки, docx — абзацы).
2. Из каждой единицы извлекаются «якоря» — числа и латинские слова с большой
   буквы (имена, бренды, аббревиатуры): они переживают перевод между почти
   любыми языками. Кемпинг слов не работает: лексика разных языков не совпадает.
3. Последовательности якорей выравниваются difflib.SequenceMatcher (как diff):
   совпавшие блоки отображаются обратно в индексы единиц — получаются
   пары (единица_исходника, единица_перевода) с числом совпавших якорей.
4. Оценка пары = совпало / max(якорей_у_исходника, якорей_у_перевода);
   для каждой единицы исходника выбирается лучшая пара.

Пары НЕ пишутся в TM автоматически: качество эвристики зависит от жанра текста
(технический текст с числами — отлично, художественный — хуже), поэтому
человек подтверждает пары в UI (галочками), остальные — мимо.
"""
import difflib
import re

_ANCHOR_RE = re.compile(r"\d+|[A-Z][A-Za-z0-9&.'\-]{1,}")


def _anchors(text: str) -> list[str]:
    return _ANCHOR_RE.findall(text)


def align_units(source_units: list[str], target_units: list[str]) -> list[dict]:
    """Предложить пары единиц. [{source, target, score, source_index, target_index}].

    Пары упорядочены по исходнику; score 0..1 (округлен до 2 знаков).
    Совсем без якорей и при равном числе единиц — позиционное предложение
    со score 0 (слабое: единицы подряд не обязаны быть парами).
    """
    src_tokens: list[tuple[str, int]] = []
    tgt_tokens: list[tuple[str, int]] = []
    src_anchor_counts: list[int] = []
    tgt_anchor_counts: list[int] = []
    for idx, unit in enumerate(source_units):
        anchors = _anchors(unit)
        src_anchor_counts.append(len(anchors))
        src_tokens.extend((t, idx) for t in anchors)
    for idx, unit in enumerate(target_units):
        anchors = _anchors(unit)
        tgt_anchor_counts.append(len(anchors))
        tgt_tokens.extend((t, idx) for t in anchors)

    matched: dict[tuple[int, int], int] = {}
    if src_tokens and tgt_tokens:
        matcher = difflib.SequenceMatcher(
            None,
            [t for t, _ in src_tokens],
            [t for t, _ in tgt_tokens],
            autojunk=False,
        )
        for a, b, size in matcher.get_matching_blocks():
            for k in range(size):
                si = src_tokens[a + k][1]
                ti = tgt_tokens[b + k][1]
                matched[(si, ti)] = matched.get((si, ti), 0) + 1

    # Лучшая пара на единицу исходника: score, затем близость позиций.
    best: dict[int, tuple[float, int]] = {}
    for (si, ti), count in matched.items():
        denom = max(src_anchor_counts[si], tgt_anchor_counts[ti], 1)
        score = round(count / denom, 2)
        current = best.get(si)
        if current is None or (score, -abs(si - ti)) > (current[0], -abs(si - current[1])):
            best[si] = (score, ti)

    if not best and len(source_units) == len(target_units) and source_units:
        # Нет якорей вовсе (художественный текст): позиционная гипотеза.
        return [
            {
                "source": source_units[i],
                "target": target_units[i],
                "score": 0.0,
                "source_index": i,
                "target_index": i,
            }
            for i in range(len(source_units))
        ]

    return [
        {
            "source": source_units[si],
            "target": target_units[ti],
            "score": score,
            "source_index": si,
            "target_index": ti,
        }
        for si, (score, ti) in sorted(best.items())
    ]
