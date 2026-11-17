import pytest

from pr_evidence_reviewer.checkout.text import decode_strict, split_lines


def test_decodes_strict_utf8() -> None:
    assert decode_strict("héllo\n".encode()) == "héllo\n"


def test_strips_exactly_one_leading_bom() -> None:
    bom = b"\xef\xbb\xbf"

    assert decode_strict(bom + b"x") == "x"
    assert decode_strict(bom + bom + b"x") == "﻿x"


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xfe",
        b"caf\xe9",  # Latin-1
        b"\xed\xa0\x80",  # encoded surrogate
        b"\xc0\xaf",  # overlong
    ],
)
def test_returns_none_for_bytes_that_are_not_strict_utf8(data: bytes) -> None:
    assert decode_strict(data) is None


def test_empty_bytes_decode_to_empty_text() -> None:
    assert decode_strict(b"") == ""


@pytest.mark.parametrize(
    ("text", "lines"),
    [
        ("", []),
        ("a", ["a"]),
        ("a\n", ["a"]),
        ("a\nb", ["a", "b"]),
        ("a\nb\n", ["a", "b"]),
        ("\n", [""]),
        ("\n\n", ["", ""]),
        ("a\r\nb\r\n", ["a", "b"]),
        ("a\r\r\nb", ["a\r", "b"]),
        ("a\rb\nc", ["a\rb", "c"]),
        ("a\u2028b\u0085c\vd\fe\n", ["a\u2028b\u0085c\vd\fe"]),
        ("a\n\r", ["a", ""]),
    ],
)
def test_splits_on_newline_only_like_github_numbers_lines(text: str, lines: list[str]) -> None:
    assert split_lines(text) == lines
