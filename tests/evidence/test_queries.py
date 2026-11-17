import pytest

from pr_evidence_reviewer.domain.claims import Claim
from pr_evidence_reviewer.evidence.queries import (
    MAX_QUERIES_PER_CLAIM,
    definition_name,
    extract_queries,
    match_paths,
)


def claim(statement: str, quote: str = "") -> Claim:
    return Claim(id="c1", quote=quote or statement, statement=statement, kind="behavior")


def test_backticked_spans_come_first_then_code_shaped_tokens() -> None:
    queries = extract_queries(claim("Calls retry_upload before `send` via `client.post()`."))

    assert queries.literals == ("send", "client.post", "retry_upload")


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("Adds max_retries to the config.", ("max_retries",)),
        ("Renames getUser everywhere.", ("getUser",)),
        ("Uses HTTPClient for calls.", ("HTTPClient",)),
        ("Wraps os.path.join safely.", ("os.path.join",)),
        ("Now upload() retries.", ("upload",)),
    ],
)
def test_code_shaped_tokens_are_queries(statement: str, expected: tuple[str, ...]) -> None:
    assert extract_queries(claim(statement)).literals == expected


@pytest.mark.parametrize(
    "statement",
    ["The API is FAST.", "Refunds are idempotent now.", "Done, e.g. here.", "It is a.b"],
)
def test_plain_prose_gives_no_queries(statement: str) -> None:
    assert extract_queries(claim(statement)).literals == ()


def test_plain_words_count_only_when_they_name_a_changed_file_or_enclosing_symbol() -> None:
    statement = "The upload now retries and refund is unchanged."

    queries = extract_queries(claim(statement), vocabulary=frozenset({"upload", "retries_x"}))

    assert queries.literals == ("upload",)


def test_path_like_tokens_are_kept_apart_and_not_searched() -> None:
    queries = extract_queries(claim("Changes src/upload.py and README.md, see https://x.io/a."))

    assert queries.paths == ("src/upload.py", "README.md")
    assert queries.literals == ()


def test_the_quote_is_read_after_the_statement_and_duplicates_drop() -> None:
    queries = extract_queries(claim("Adds max_retries.", quote="`max_retries` and `backoff_ms`"))

    assert queries.literals == ("max_retries", "backoff_ms")


def test_at_most_six_queries_and_each_three_to_eighty_chars() -> None:
    names = " ".join(f"name_{n}" for n in range(10))
    statement = f"Uses `ab` and `{'x' * 81}` and {names}."

    queries = extract_queries(claim(statement))

    assert len(queries.literals) == MAX_QUERIES_PER_CLAIM
    assert queries.literals[0] == "name_0"
    assert all(3 <= len(query) <= 80 for query in queries.literals)


def test_a_backticked_span_across_lines_is_ignored() -> None:
    assert extract_queries(claim("Uses `a\nbcd` here.")).literals == ()


def test_paths_match_the_manifest_by_full_path_suffix_or_basename() -> None:
    files = ["src/upload.py", "lib/src/upload.py", "tests/test_upload.py", "README.md"]

    assert match_paths(("src/upload.py",), files) == ("lib/src/upload.py", "src/upload.py")
    assert match_paths(("README.md", "./tests/test_upload.py"), files) == (
        "README.md",
        "tests/test_upload.py",
    )
    assert match_paths(("load.py", "missing.py"), files) == ()


@pytest.mark.parametrize(
    ("query", "expected"),
    [("client.post", "post"), ("upload", "upload"), ("retry upload", None), ("a-b", None)],
)
def test_the_definition_name_is_the_last_dotted_part(query: str, expected: str | None) -> None:
    assert definition_name(query) == expected
