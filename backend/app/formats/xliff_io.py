"""XLIFF 1.2 (профиль MVP): каждый <trans-unit> = сегмент.

Namespace-агностичный разбор: файлы с xmlns и без него обрабатываются одинаково.

Inline-элементы исходника нормализуются в токены сегмента:
- <x id="N"/> и <ph id="N">…</ph> -> <x id="N"/>
- <bpt id="N">…</bpt> … <ept id="N">…</ept> -> <g id="N"> … </g>

При экспорте токены перевода восстанавливаются в оригинальные inline-элементы
(атрибуты и содержимое bpt/ept/ph клонируются из исходника по id), остальной
XML файла сохраняется как был. Неподдерживаемые элементы фиксируются в notes.
"""
import io
import re
from copy import deepcopy

from lxml import etree

_TOKEN_RE = re.compile(r'(<x id="[^"]+"\s*/>|<g id="[^"]+">|</g>)')
_TOKEN_ID_RE = re.compile(r'id="([^"]+)"')


def _local(tag) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _ns(tag) -> str | None:
    return etree.QName(tag).namespace if isinstance(tag, str) else None


def _child_by_local(element, name):
    for child in element:
        if _local(child.tag) == name:
            return child
    return None


def _new_tag_like(sample_tag, name: str):
    ns = _ns(sample_tag)
    return f"{{{ns}}}{name}" if ns else name


def _inline_to_tokens(element) -> tuple[str, list[str]]:
    parts: list[str] = []
    notes: list[str] = []

    def walk(node) -> None:
        if node.text:
            parts.append(node.text)
        for child in node:
            name = _local(child.tag)
            tag_id = child.get("id", "")
            if name == "x":
                parts.append(f'<x id="{tag_id}"/>')
            elif name == "ph":
                parts.append(f'<x id="{tag_id}"/>')
                notes.append(f'Inline <ph id="{tag_id}"> content is kept for export, not shown in editor')
            elif name == "bpt":
                parts.append(f'<g id="{tag_id}">')
            elif name == "ept":
                parts.append("</g>")
            elif name in ("it", "sub", "mrk", "g"):
                notes.append(f"Inline element <{name}> is treated as opaque (id={tag_id or '-'})")
                if name == "g" and tag_id:
                    parts.append(f'<g id="{tag_id}">')
                walk(child)
                if name == "g" and tag_id:
                    parts.append("</g>")
            else:
                walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(element)
    return "".join(parts), notes


def _find_by_id(element, name: str, tag_id: str):
    for child in element.iter():
        if _local(child.tag) == name and child.get("id") == tag_id:
            return child
    return None


def _pick(first, second):
    return first if first is not None else second


def load_segments(content: bytes) -> tuple[list[str], list[str]]:
    tree = etree.parse(io.BytesIO(content))
    root = tree.getroot()
    notes: list[str] = []
    segments: list[str] = []
    for unit in root.iter():
        if _local(unit.tag) != "trans-unit":
            continue
        source = _child_by_local(unit, "source")
        if source is None:
            continue
        text, unit_notes = _inline_to_tokens(source)
        text = text.strip()
        if text:
            segments.append(text)
        for note in unit_notes:
            if note not in notes:
                notes.append(note)
    return segments, notes


def export_file(original: bytes, targets: list[str]) -> bytes:
    tree = etree.parse(io.BytesIO(original))
    root = tree.getroot()
    units = [
        unit for unit in root.iter()
        if _local(unit.tag) == "trans-unit" and _child_by_local(unit, "source") is not None
    ]
    for unit, translation in zip(units, targets):
        source = _child_by_local(unit, "source")

        target = _child_by_local(unit, "target")
        if target is None:
            target = etree.SubElement(unit, _new_tag_like(unit.tag, "target"))
        else:
            for child in list(target):
                target.remove(child)
        target.text = None

        # Модель XLIFF: bpt/ept/x — соседи внутри target; переводимый текст лежит
        # в target.text и хвостах (tail) этих элементов. Payload (внутренний текст
        # bpt/ept/ph) клонируется из исходника и не изменяется.
        text_host = target  # элемент, в tail которого пишется следующий текст
        g_stack: list[str] = []

        def append_text(chunk: str) -> None:
            if text_host is target:
                target.text = (target.text or "") + chunk
            else:
                text_host.tail = (text_host.tail or "") + chunk

        for chunk in (c for c in _TOKEN_RE.split(translation) if c):
            if chunk == "</g>":
                tag_id = g_stack.pop() if g_stack else ""
                original_el = _find_by_id(source, "ept", tag_id)
                if original_el is not None:
                    new_el = deepcopy(original_el)
                    new_el.tail = None
                else:
                    new_el = etree.Element(_new_tag_like(unit.tag, "ept"), id=tag_id)
                target.append(new_el)
                text_host = new_el
            elif chunk.startswith("<x"):
                tag_id = _TOKEN_ID_RE.search(chunk).group(1)
                original_el = _pick(
                    _find_by_id(source, "x", tag_id), _find_by_id(source, "ph", tag_id)
                )
                if original_el is not None:
                    new_el = deepcopy(original_el)
                    new_el.tail = None
                else:
                    new_el = etree.Element(_new_tag_like(unit.tag, "x"), id=tag_id)
                target.append(new_el)
                text_host = new_el
            elif chunk.startswith("<g"):
                tag_id = _TOKEN_ID_RE.search(chunk).group(1)
                g_stack.append(tag_id)
                original_el = _find_by_id(source, "bpt", tag_id)
                if original_el is not None:
                    new_el = deepcopy(original_el)
                    new_el.tail = None
                else:
                    new_el = etree.Element(_new_tag_like(unit.tag, "bpt"), id=tag_id)
                target.append(new_el)
                text_host = new_el
            else:
                append_text(chunk)

    buffer = io.BytesIO()
    tree.write(buffer, xml_declaration=True, encoding=tree.docinfo.encoding or "UTF-8")
    return buffer.getvalue()
