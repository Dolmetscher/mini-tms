"""XLIFF 2.0 (профиль MVP): каждый <segment> внутри <unit> = сегмент.

Namespace urn:oasis:names:tc:xliff:document:2.0; разбор namespace-агностичный.
<ignorable> не переводится и в сегменты не попадает.

Inline-разметка нормализуется в токены сегмента:
- <ph id="N">…</ph>            -> <x id="N"/>            (native-код не показывается)
- <pc id="N">…</pc>            -> <g id="N"> … </g>      (текст внутри — переводится)
- <sc id="N"/> … <ec id="N"/>  -> <g id="N"> … </g>      (пара по общему id)
- <mrk> и прочее               -> прозрачный обход + note

При экспорте токены перевода восстанавливаются: <pc> создается заново с
атрибутами оригинала (текст перевода — внутрь), <ph>/<sc>/<ec> клонируются
из исходника по id (tail исходника не переносится). Остальной XML файла
сохраняется как был. Неподдерживаемые элементы фиксируются в notes.
"""
import io
import re
from copy import deepcopy

from lxml import etree

_TOKEN_RE = re.compile(r'(<x id="[^"]+"\s*/>|<g id="[^"]+">|</g>)')
_TOKEN_ID_RE = re.compile(r'id="([^"]+)"')


def _local(tag) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _new_tag_like(sample_tag, name: str):
    ns = etree.QName(sample_tag).namespace if isinstance(sample_tag, str) else None
    return f"{{{ns}}}{name}" if ns else name


def _child_by_local(element, name):
    for child in element:
        if _local(child.tag) == name:
            return child
    return None


def _inline_to_tokens(element) -> tuple[str, list[str]]:
    """XML-дерево <source> -> текст с токенами + заметки о неполной поддержке."""
    parts: list[str] = []
    notes: list[str] = []
    # id изолированных пар sc/ec: в токены идут только сбалансированные пары
    sc_ids = [c.get("id", "") for c in element.iter() if _local(c.tag) == "sc"]
    ec_ids = [c.get("id", "") for c in element.iter() if _local(c.tag) == "ec"]

    def walk(node) -> None:
        if node.text:
            parts.append(node.text)
        for child in node:
            name = _local(child.tag)
            tag_id = child.get("id", "")
            if name == "ph":
                parts.append(f'<x id="{tag_id}"/>')
            elif name == "pc":
                parts.append(f'<g id="{tag_id}">')
                walk(child)
                parts.append("</g>")
            elif name == "sc":
                if tag_id and tag_id in ec_ids:
                    parts.append(f'<g id="{tag_id}">')
                else:
                    notes.append(f"Isolated <sc id={tag_id or '-'}> without matching <ec> is dropped to text")
            elif name == "ec":
                if tag_id and tag_id in sc_ids:
                    parts.append("</g>")
                else:
                    notes.append(f"Isolated <ec id={tag_id or '-'}> without matching <sc> is dropped to text")
            else:
                if name == "mrk":
                    notes.append(f"Inline <mrk> annotation (id={tag_id or '-'}) is traversed as plain text")
                else:
                    notes.append(f"Inline element <{name}> is treated as opaque (id={tag_id or '-'})")
                walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(element)
    return "".join(parts), notes


def load_segments(content: bytes) -> tuple[list[str], list[str]]:
    tree = etree.parse(io.BytesIO(content))
    root = tree.getroot()
    notes: list[str] = []
    segments: list[str] = []
    for seg in root.iter():
        if _local(seg.tag) != "segment":
            continue  # в т.ч. <ignorable>: не переводится
        source = _child_by_local(seg, "source")
        if source is None:
            continue
        text, seg_notes = _inline_to_tokens(source)
        text = text.strip()
        if text:
            segments.append(text)
        for note in seg_notes:
            if note not in notes:
                notes.append(note)
    return segments, notes


def _find_by_local_id(element, names: tuple[str, ...], tag_id: str):
    for child in element.iter():
        if _local(child.tag) in names and child.get("id") == tag_id:
            return child
    return None


def _clone_without_tail(original):
    el = deepcopy(original)
    el.tail = None
    return el


def _fill(container, tokens: list[str], i: int, source, have_children: bool = False) -> int:
    """Записывает токены tokens[i:] в container (children/text).

    Возвращает индекс токена, на котором остановилась: '</g>' (закрытие
    обрабатывает вызывающий) или конец списка. Текст после вложенных
    элементов попадает в tail последнего child — модель lxml.
    have_children=True означает, что в container уже есть дети (повторный
    вход после sc или лишнего '</g>') — текст пишется в tail последнего.
    """
    while i < len(tokens):
        chunk = tokens[i]
        if chunk == "</g>":
            return i
        if chunk.startswith("<x"):
            tag_id = _TOKEN_ID_RE.search(chunk).group(1)
            original = _find_by_local_id(source, ("ph",), tag_id)
            el = _clone_without_tail(original) if original is not None else etree.Element(
                _new_tag_like(container.tag, "ph"), id=tag_id
            )
            container.append(el)
            have_children = True
            i += 1
        elif chunk.startswith("<g"):
            tag_id = _TOKEN_ID_RE.search(chunk).group(1)
            pc_original = _find_by_local_id(source, ("pc",), tag_id)
            sc_original = _find_by_local_id(source, ("sc",), tag_id)
            if pc_original is not None or sc_original is None:
                # парный контейнер: <pc> с атрибутами оригинала, текст — внутрь
                el = etree.Element(_new_tag_like(container.tag, "pc"))
                if pc_original is not None:
                    for key, value in pc_original.attrib.items():
                        el.set(key, value)
                else:
                    el.set("id", tag_id)
                container.append(el)
                have_children = True
                i += 1
                i = _fill(el, tokens, i, source)
                i += 1  # '</g>', закрывающий pc
            else:
                # изолированная пара sc/ec: содержимое — на уровне container
                container.append(_clone_without_tail(sc_original))
                have_children = True
                i += 1
                i = _fill(container, tokens, i, source, have_children=True)
                i += 1  # '</g>' между sc и ec
                ec_original = _find_by_local_id(source, ("ec",), tag_id)
                closing = (
                    _clone_without_tail(ec_original)
                    if ec_original is not None
                    else etree.Element(_new_tag_like(container.tag, "ec"), id=tag_id)
                )
                container.append(closing)
                have_children = True
        else:
            if have_children and len(container):
                last = container[-1]
                last.tail = (last.tail or "") + chunk
            else:
                container.text = (container.text or "") + chunk
            i += 1
    return i


def export_file(original: bytes, targets: list[str]) -> bytes:
    tree = etree.parse(io.BytesIO(original))
    root = tree.getroot()
    segments = [
        seg for seg in root.iter()
        if _local(seg.tag) == "segment" and _child_by_local(seg, "source") is not None
    ]
    for seg, translation in zip(segments, targets):
        source = _child_by_local(seg, "source")
        target = _child_by_local(seg, "target")
        if target is None:
            target = etree.SubElement(seg, _new_tag_like(seg.tag, "target"))
        else:
            for child in list(target):
                target.remove(child)
        target.text = None

        tokens = [c for c in _TOKEN_RE.split(translation) if c]
        i = 0
        while i < len(tokens):
            i = _fill(target, tokens, i, source, have_children=len(target) > 0)
            if i < len(tokens):  # лишний '</g>' без пары — пропускаем, текст сохраняем
                i += 1

    buffer = io.BytesIO()
    tree.write(buffer, xml_declaration=True, encoding=tree.docinfo.encoding or "UTF-8")
    return buffer.getvalue()
