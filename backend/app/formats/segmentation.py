"""Импорт/экспорт документов: выбор обработчика по формату (§9)."""
from . import docx_io, txt_io, xliff_io

SUPPORTED_FORMATS = ("txt", "docx", "xliff")

_EXTENSION_MAP = {".txt": "txt", ".text": "txt", ".docx": "docx", ".xliff": "xliff", ".xlf": "xliff"}


def detect_format(filename: str) -> str | None:
    name = (filename or "").lower()
    for ext, fmt in _EXTENSION_MAP.items():
        if name.endswith(ext):
            return fmt
    return None


def load_segments(fmt: str, content: bytes) -> tuple[list[str], list[str]]:
    if fmt == "txt":
        return txt_io.load_segments(content)
    if fmt == "docx":
        return docx_io.load_segments(content)
    if fmt == "xliff":
        return xliff_io.load_segments(content)
    raise ValueError(f"Unsupported format: {fmt}")


def load_translations(fmt: str, content: bytes) -> list[str | None] | None:
    """Существующие переводы из файла (XLIFF state/approved), выровненные
    с load_segments; None для форматов без встроенных переводов.

    Практика CAT: файл из другого инструмента часто уже несет <target> —
    они импортируются как черновики (вычитка обязательна), как пред-перевод.
    """
    if fmt == "xliff":
        return xliff_io.load_translations(content)
    return None


def export_file(fmt: str, original: bytes, targets: list[str]) -> bytes:
    if fmt == "txt":
        return txt_io.export_file(original, targets)
    if fmt == "docx":
        return docx_io.export_file(original, targets)
    if fmt == "xliff":
        return xliff_io.export_file(original, targets)
    raise ValueError(f"Unsupported format: {fmt}")
