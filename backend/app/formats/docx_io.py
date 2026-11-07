"""DOCX (профиль MVP): непустые абзацы = сегменты.

Форматирование внутри абзаца упрощается до plain text (runs объединяются);
таблицы, картинки, колонтитулы не импортируются — факты фиксируются в notes.
При экспорте текст абзаца заменяется переводом, стиль абзаца сохраняется.
"""
import io

from docx import Document as open_docx


def load_segments(content: bytes) -> tuple[list[str], list[str]]:
    doc = open_docx(io.BytesIO(content))
    notes: list[str] = []
    if doc.tables:
        notes.append(f"{len(doc.tables)} table(s) are not imported (unsupported in MVP profile)")
    segments = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            segments.append(text)
    return segments, notes


def export_file(original: bytes, targets: list[str]) -> bytes:
    doc = open_docx(io.BytesIO(original))
    index = 0
    for para in doc.paragraphs:
        if not para.text.strip():
            continue
        if index >= len(targets):
            break
        translation = targets[index]
        index += 1
        for run in list(para.runs[1:]):
            run._element.getparent().remove(run._element)
        if para.runs:
            para.runs[0].text = translation
        else:
            para.add_run(translation)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def render_bilingual(
    title: str,
    source_lang: str,
    target_lang: str,
    rows: list[tuple[int, str, str]],
) -> bytes:
    """Двуязычный DOCX для ревью/заказчика: таблица № / исходник / перевод.

    Пустой перевод показывается тире — ревьюер видит незавершенные места.
    """
    doc = open_docx()
    doc.add_heading(title, level=1)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    header = table.rows[0].cells
    header[0].text = "#"
    header[1].text = f"Source ({source_lang})"
    header[2].text = f"Translation ({target_lang})"
    for position, source, target in rows:
        cells = table.add_row().cells
        cells[0].text = str(position)
        cells[1].text = source
        cells[2].text = target if target.strip() else "—"
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
