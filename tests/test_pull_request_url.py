import pytest

from pr_evidence_reviewer.domain.pull_request import PullRequestRef, parse_pull_request_url
from pr_evidence_reviewer.errors import InvalidPullRequestUrlError


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://github.com/acme/shop/pull/7",
            PullRequestRef(owner="acme", repo="shop", number=7),
        ),
        (
            "https://github.com/acme/shop/pull/7/files",
            PullRequestRef(owner="acme", repo="shop", number=7),
        ),
        (
            "https://github.com/acme/shop/pull/7?diff=split#r1",
            PullRequestRef(owner="acme", repo="shop", number=7),
        ),
        (
            "  https://www.github.com/Acme-1/my.repo_x/pull/123/  ",
            PullRequestRef(owner="Acme-1", repo="my.repo_x", number=123),
        ),
        (
            "https://GitHub.com/acme/shop.git/pull/7",
            PullRequestRef(owner="acme", repo="shop", number=7),
        ),
    ],
)
def test_parses_github_pull_request_links(url: str, expected: PullRequestRef) -> None:
    assert parse_pull_request_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "http://github.com/acme/shop/pull/7",
        "https://gitlab.com/acme/shop/pull/7",
        "https://github.com.evil.example/acme/shop/pull/7",
        "https://evil.example/github.com/acme/shop/pull/7",
        "https://user@github.com/acme/shop/pull/7",
        "https://github.com:8443/acme/shop/pull/7",
        "https://github.com/acme/shop/issues/7",
        "https://github.com/acme/shop/pull/",
        "https://github.com/acme/shop/pull/0",
        "https://github.com/acme/shop/pull/-3",
        "https://github.com/acme/shop/pull/7abc",
        "https://github.com/acme/shop/pull/١٢",
        "https://github.com/acme/shop/pull/99999999999",
        "https://github.com/-acme/shop/pull/7",
        "https://github.com/ac_me/shop/pull/7",
        "https://github.com/acme/../pull/7",
        "https://github.com/acme/sh%2Fop/pull/7",
        "https://github.com/acme",
        "javascript:alert(1)",
        "https://github.com/acme/shop/pull/" + "1" * 3000,
    ],
)
def test_rejects_anything_that_is_not_a_github_pull_request(url: str) -> None:
    with pytest.raises(InvalidPullRequestUrlError):
        parse_pull_request_url(url)


def test_slug_is_human_readable() -> None:
    assert PullRequestRef(owner="acme", repo="shop", number=7).slug == "acme/shop#7"
