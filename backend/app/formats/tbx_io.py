"""TBX (TermBase eXchange, LISA TBX 1.0 / dialect TBX-Basic) — импорт/экспорт TB.

Индустриальный стандарт обмена терминологией: matif → text → body →
termEntry → langSet(xml:lang) → tig → term. Статус термина — termNote
type="normativeAuthorization" (preferredTerm/admittedTerm/forbiddenTerm);
примечание — descrip type="note" (на уровне termEntry).

Экспорт пишет TBX-Basic; импорт дополнительно терпим к упрощенным файлам
(без martifHeader, статусы preferred/admitted/forbidden без суффикса Term,
примечание в termNote type="note"). Языки сопоставляются по префиксу:
«en» в TB матчит и «en», и «en-US» в xml:lang.
"""
import io

from lxml import etree

_TB_NS = "http://www.w3.org/XML/1998/namespace"
_LANG = f"{{{_TB_NS}}}lang"

# Статус TB -> значение normativeAuthorization в TBX (стандартные токены LISA).
_STATUS_TO_TBX = {"preferred": "preferredTerm", "admitted": "admittedTerm", "forbidden": "forbiddenTerm"}
# Обратное отображение: принимает и полные токены TBX, и короткие статусы.
_STATUS_FROM_TBX = {
    "preferredterm": "preferred", "preferred": "preferred",
    "admittedterm": "admitted", "admitted": "admitted",
    "forbiddenterm": "forbidden", "forbidden": "forbidden",
}


def _lang_matches(tbx_lang: str | None, wanted: str) -> bool:
    if not tbx_lang:
        return False
    a, b = tbx_lang.strip().lower(), wanted.strip().lower()
    return a == b or a.startswith(b + "-") or b.startswith(a + "-")


def export_tbx(name: str, source_lang: str, target_lang: str, entries) -> bytes:
    """TB → TBX-Basic XML (UTF-8). entries — объекты TBEntry."""
    root = etree.Element("martif", type="TBX", attrib={_LANG: source_lang})
    header = etree.SubElement(root, "martifHeader")
    file_desc = etree.SubElement(header, "fileDesc")
    source_desc = etree.SubElement(file_desc, "sourceDesc")
    etree.SubElement(source_desc, "p").text = f"Exported from Mini-TMS: {name}"
    text_el = etree.SubElement(root, "text")
    body = etree.SubElement(text_el, "body")

    for entry in entries:
        term_entry = etree.SubElement(body, "termEntry", id=str(entry.id))
        if entry.note:
            etree.SubElement(term_entry, "descrip", type="note").text = entry.note
        for lang, term_text, with_status in (
            (source_lang, entry.term, True),
            (target_lang, entry.translation, False),
        ):
            lang_set = etree.SubElement(term_entry, "langSet", attrib={_LANG: lang})
            tig = etree.SubElement(lang_set, "tig")
            etree.SubElement(tig, "term").text = term_text
            if with_status:
                etree.SubElement(
                    tig, "termNote", type="normativeAuthorization"
                ).text = _STATUS_TO_TBX.get(entry.status, "preferredTerm")
    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", pretty_print=True
    )


def parse_tbx(data: bytes, source_lang: str, target_lang: str) -> list[dict]:
    """TBX → список {term, translation, note, status}.

    Поднимает ValueError на невалидном XML/структуре — вызывающий код
    превращает её в 422.
    """
    parser = etree.XMLParser(resolve_entities=False, no_network=True)  # noqa: S320 — XXE отключен явно
    try:
        root = etree.parse(io.BytesIO(data), parser).getroot()
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"Invalid XML: {exc}") from exc

    term_entries = root.iter("termEntry")
    result: list[dict] = []
    for term_entry in term_entries:
        source_term = target_term = None
        status = "preferred"
        note = ""
        for lang_set in term_entry.findall("langSet"):
            tig = lang_set.find("tig")
            if tig is None:
                continue
            term_el = tig.find("term")
            if term_el is None or not (term_el.text or "").strip():
                continue
            term = term_el.text.strip()
            for term_note in tig.findall("termNote"):
                if (term_note.get("type") or "").lower() == "normativeauthorization":
                    status = _STATUS_FROM_TBX.get(
                        (term_note.text or "").strip().lower(), "preferred"
                    )
                if (term_note.get("type") or "").lower() == "note":
                    note = (term_note.text or "").strip()
            if _lang_matches(lang_set.get(_LANG), source_lang):
                source_term = term
            elif _lang_matches(lang_set.get(_LANG), target_lang):
                target_term = term
        for descrip in term_entry.findall("descrip"):
            if (descrip.get("type") or "").lower() == "note" and not note:
                note = (descrip.text or "").strip()
        if source_term and target_term:
            result.append({
                "term": source_term,
                "translation": target_term,
                "note": note,
                "status": status,
            })
    if not result:
        raise ValueError(
            "No termEntry with both source and target terms found "
            f"(expected langSets for '{source_lang}' and '{target_lang}')"
        )
    return result
