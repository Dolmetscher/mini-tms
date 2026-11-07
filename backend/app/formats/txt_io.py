"""TXT (UTF-8): непустая строка = сегмент (§9 профиль)."""


def load_segments(content: bytes) -> tuple[list[str], list[str]]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = content.decode("cp1252")
        except UnicodeDecodeError:
            text = content.decode("utf-8", errors="replace")
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line], []


def export_file(original: bytes, targets: list[str]) -> bytes:
    return ("\n".join(targets) + "\n").encode("utf-8")
