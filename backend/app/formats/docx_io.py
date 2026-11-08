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


def render_quote(quote: dict, source_lang: str) -> bytes:
    """Котировка задания в DOCX — документ для заказчика.

    Таблица: категория совпадения / сегменты / слова / ставка / сумма;
    в конце — промежуточный итог, минимальная сумма (если применилась)
    и итог к оплате. Числа уже округлены сервисом котировок.
    """
    doc = open_docx()
    doc.add_heading("Translation Quote", level=1)
    doc.add_paragraph(f"Document: {quote['document_name']}")
    doc.add_paragraph(
        f"Language pair: {source_lang} \u2192 {quote['target_lang']}   ·   "
        f"Rate card: {quote['rate_card']}"
    )

    table = doc.add_table(rows=1, cols=5)
    table.style = "Table Grid"
    header = table.rows[0].cells
    header[0].text = "Category"
    header[1].text = "Segments"
    header[2].text = "Words"
    header[3].text = f"Rate, {quote['currency']}/word"
    header[4].text = f"Amount, {quote['currency']}"
    for line in quote["lines"]:
        cells = table.add_row().cells
        cells[0].text = line["label"]
        cells[1].text = str(line["segments"])
        cells[2].text = str(line["words"])
        cells[3].text = f"{line['rate']:.4f}".rstrip("0").rstrip(".") or "0"
        cells[4].text = f"{line['amount']:.2f}"

    doc.add_paragraph(f"Subtotal: {quote['subtotal']:.2f} {quote['currency']}")
    if quote["min_charge_applied"]:
        doc.add_paragraph(
            f"Minimum charge applied: {quote['min_charge']:.2f} {quote['currency']}"
        )
    total = doc.add_paragraph()
    total.add_run(f"Total: {quote['total']:.2f} {quote['currency']}").bold = True

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def render_invoice(invoice: dict, source_lang: str) -> bytes:
    """Инвойс проекта в DOCX — закрывающий документ для заказчика.

    Строки — по заданиям (документ, язык, прайс-лист, сумма); заказчику
    важна итоговая строка, категории котировки не разворачиваются. Итоги —
    по валютам, как в котировке проекта.
    """
    doc = open_docx()
    doc.add_heading("Invoice", level=1)
    doc.add_paragraph(f"Invoice no.: {invoice['invoice_no']}")
    doc.add_paragraph(f"Issue date: {invoice['issued_on']}")
    doc.add_paragraph(f"Project: {invoice['project_name']}")
    doc.add_paragraph(f"Source language: {source_lang}")

    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    header = table.rows[0].cells
    header[0].text = "Document"
    header[1].text = "Target language"
    header[2].text = "Rate card"
    header[3].text = "Amount"
    for job in invoice["jobs"]:
        cells = table.add_row().cells
        cells[0].text = job["document_name"]
        cells[1].text = job["target_lang"]
        cells[2].text = job["rate_card"]
        cells[3].text = f"{job['total']:.2f} {job['currency']}"
    for total in invoice["totals"]:
        paragraph = doc.add_paragraph()
        paragraph.add_run(f"Total due: {total['total']:.2f} {total['currency']}").bold = True

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
