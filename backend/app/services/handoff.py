"""XLIFF-хендофф: работа с внешними переводчиками вне системы.

Экспорт задания в XLIFF 1.2 (source + текущий target + state) — файл открывается
в Trados/memoQ/OmegaT и любом CAT-инструменте; обратно привезенный XLIFF
мержится в то же задание:

- сопоставление по id trans-unit (позиция сегмента), фолбэк — по тексту
  исходника (нормализованному);
- target с state translated/signed-off/final (или approved=yes) подтверждается,
  как ручное Confirm: запись в TM с правом записи, автопропагация повторов;
  прочие target — черновики;
- сегмент под чужим свежим локом пропускается («кто редактирует — тот
  блокирует» распространяется и на пакетный импорт);
- каждое изменение текста попадает в историю правок (track changes), как при
  ручном редактировании.

Inline-теги сегментов в XLIFF становятся родными элементами (x / bpt / ept),
обратно — нормализуются в токены тем же кодом, что при импорте документов.
"""
import io
import re

from lxml import etree
from sqlalchemy.orm import Session

from .. import models
from ..fuzzy import normalize
from ..formats import xliff_io
from . import leveraging, locks

_XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"
_TOKEN_RE = re.compile(r'(<x id="[^"]+"\s*/>|<g id="[^"]+">|</g>)')
_TOKEN_ID_RE = re.compile(r'id="([^"]+)"')


def _fill_inline(parent, text: str) -> None:
    """Текст сегмента с литеральными токенами тегов -> дочерние inline-элементы.

    Текстовые куски ложатся в text/tail, `<x id/>` -> <x id/>, `<g id>` -> <bpt id>,
    `</g>` -> <ept id> (парный id из стека, как в XLIFF-модели).
    """
    g_stack: list[str] = []
    host_tail_of = None  # последний вставленный элемент

    def append_text(chunk: str) -> None:
        if host_tail_of is None:
            parent.text = (parent.text or "") + chunk
        else:
            host_tail_of.tail = (host_tail_of.tail or "") + chunk

    for chunk in (c for c in _TOKEN_RE.split(text) if c):
        if chunk == "</g>":
            tag_id = g_stack.pop() if g_stack else ""
            el = etree.SubElement(parent, f"{{{_XLIFF_NS}}}ept", id=tag_id)
        elif chunk.startswith("<x"):
            tag_id = _TOKEN_ID_RE.search(chunk).group(1)
            el = etree.SubElement(parent, f"{{{_XLIFF_NS}}}x", id=tag_id)
        elif chunk.startswith("<g"):
            tag_id = _TOKEN_ID_RE.search(chunk).group(1)
            g_stack.append(tag_id)
            el = etree.SubElement(parent, f"{{{_XLIFF_NS}}}bpt", id=tag_id)
        else:
            append_text(chunk)
            continue
        host_tail_of = el


def export_job_xliff(job: models.Job) -> bytes:
    """Хендофф-пакет задания: XLIFF 1.2, один file -> trans-unit на сегмент."""
    root = etree.Element(f"{{{_XLIFF_NS}}}xliff", version="1.2")
    file_el = etree.SubElement(
        root, f"{{{_XLIFF_NS}}}file",
        original=f"mini-tms-job-{job.id}",
        **{
            "source-language": job.project.source_lang,
            "target-language": job.target_lang,
            "datatype": "plaintext",
        },
    )
    etree.SubElement(file_el, f"{{{_XLIFF_NS}}}header")
    body = etree.SubElement(file_el, f"{{{_XLIFF_NS}}}body")
    for segment in job.segments:
        unit = etree.SubElement(body, f"{{{_XLIFF_NS}}}trans-unit", id=str(segment.position))
        source = etree.SubElement(unit, f"{{{_XLIFF_NS}}}source")
        _fill_inline(source, segment.source_text)
        target = etree.SubElement(unit, f"{{{_XLIFF_NS}}}target")
        if segment.target_text:
            _fill_inline(target, segment.target_text)
        if segment.confirmed and segment.translation_status == "translated":
            target.set("state", "translated")
    buffer = io.BytesIO()
    etree.ElementTree(root).write(buffer, xml_declaration=True, encoding="UTF-8")
    return buffer.getvalue()


def _load_units(content: bytes) -> list[dict]:
    """load_units с дружелюбной ошибкой: битый XML -> ValueError (роутер даст 422)."""
    try:
        return xliff_io.load_units(content)
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"Некорректный XLIFF: {exc}") from exc


def import_job_xliff(db: Session, job: models.Job, content: bytes, user_id: int) -> tuple[dict, list[models.Segment]]:
    """Влить возвращенный XLIFF в задание. Возвращает (статистика, измененные сегменты).

    Измененные сегменты (вкл. автопропагацию) нужны роутеру для WS-событий —
    редакторы с открытым заданием видят обновления сразу.
    """
    units = _load_units(content)
    by_position: dict[str, dict] = {}
    by_source: dict[str, dict] = {}
    for unit in units:
        if unit["id"].isdigit():
            by_position[unit["id"]] = unit
        by_source.setdefault(normalize(unit["source"]), unit)

    updated = confirmed = skipped = skipped_locked = 0
    changed: list[models.Segment] = []

    for segment in job.segments:
        unit = by_position.get(str(segment.position)) or by_source.get(normalize(segment.source_text))
        if unit is None:
            skipped += 1
            continue
        text = unit["target"]
        if not text:
            skipped += 1
            continue
        other = locks.fresh_lock(db, segment.id)
        if other is not None and other.user_id != user_id:
            skipped_locked += 1
            continue

        touched = False
        if text != segment.target_text:
            db.add(models.SegmentRevision(
                segment_id=segment.id, author_id=user_id, target_text=text,
            ))
            segment.target_text = text
            segment.translation_status = "translated"
            touched = True
        if unit["translated"]:
            if not segment.confirmed:
                segment.confirmed = True
                confirmed += 1
                touched = True
        elif text != segment.target_text and segment.confirmed:
            # правка подтвержденного без подтверждающего state — снимаем
            # подтверждение (то же правило, что при ручном редактировании)
            segment.confirmed = False
            touched = True
        if not touched:
            continue

        segment.updated_by = user_id
        updated += 1
        changed.append(segment)
        if segment.confirmed and segment.translation_status == "translated":
            leveraging.write_translation_to_tm(db, job, segment)
            changed.extend(leveraging.propagate_translation(db, job, segment))

    if updated and job.status == "accepted":
        job.status = "in_progress"
    return {
        "updated": updated,
        "confirmed": confirmed,
        "skipped": skipped,
        "skipped_locked": skipped_locked,
    }, changed
