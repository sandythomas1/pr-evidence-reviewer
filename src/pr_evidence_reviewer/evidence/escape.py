"""Make repository text safe to place on one line of the rendered evidence (threat T10).

A model may treat a lone carriage return, a next-line (NEL), a vertical tab, a form feed or a Unicode
line or paragraph separator as a line break, so file content or a name holding one could fake a
`===` header or a boundary line. Every C0 control except tab, DEL, every C1 control and U+2028/U+2029
is shown as a visible `\\uXXXX` escape instead.
"""

import json

_LINE_BREAKING = (
    *range(0x00, 0x09),
    *range(0x0A, 0x20),
    0x7F,
    *range(0x80, 0xA0),
    0x2028,
    0x2029,
)
_VISIBLE = {code: "\\" + "u" + format(code, "04x") for code in _LINE_BREAKING}


def visible(text: str) -> str:
    """Repository content with line-breaking and control characters escaped; tabs are kept."""
    return text.translate(_VISIBLE)


def inline(text: str) -> str:
    """A name (path, check, symbol) as JSON string content, plus the escapes `visible` adds."""
    return json.dumps(text, ensure_ascii=False)[1:-1].translate(_VISIBLE)
