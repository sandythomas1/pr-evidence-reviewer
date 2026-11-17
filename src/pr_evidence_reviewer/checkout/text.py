"""Decoding and line splitting shared by the renderer, search, symbols and the citation checker.

There is exactly one line splitter so that line N shown to the model is line N the validator checks
(threat T7). It follows GitHub's numbering: lines end at `\\n` only, and a final newline does not
start an extra empty line. `str.splitlines()` must never be used for repository text: it also splits
on `\\r`, U+2028, U+0085, `\\v` and `\\f`, which would shift line numbers.
"""

_UTF8_BOM = "\ufeff"


def decode_strict(data: bytes) -> str | None:
    """Strict UTF-8 with one leading BOM removed, or None if the bytes aren't valid UTF-8."""
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    return text.removeprefix(_UTF8_BOM)


def split_lines(text: str) -> list[str]:
    """Split on `\\n` only, strip one trailing `\\r` per line, and add no phantom last line."""
    if not text:
        return []
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return [line.removesuffix("\r") for line in lines]
