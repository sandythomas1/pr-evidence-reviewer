from pathlib import Path

import httpx
import pytest

from pr_evidence_reviewer.checkout.archive import RepoName, git_blob_sha
from pr_evidence_reviewer.checkout.cache import CheckoutTarget
from pr_evidence_reviewer.errors import CheckoutUnavailableError, RepositoryTooLargeError
from pr_evidence_reviewer.github.archive import GitHubArchiveSource
from tests.checkout.fakes import HEAD_SHA, TARGET, TREE_SHA
from tests.fakes import TOKEN
from tests.github.fake_archive import FakeGitHubArchive, streamed

pytestmark = pytest.mark.anyio

FILES = {"src/app.py": b"print('hi')\n", "README.md": b"# shop\n"}
SHOP = RepoName("acme", "shop")


def source_for(fake: FakeGitHubArchive, **options: float) -> GitHubArchiveSource:
    return GitHubArchiveSource(lambda: TOKEN, transport=fake.transport(), **options)


async def test_reads_the_recursive_tree_at_the_commit() -> None:
    fake = FakeGitHubArchive(FILES)

    tree = await source_for(fake).fetch_tree(TARGET)

    assert tree.sha == TREE_SHA
    assert tree.source == SHOP
    assert sorted(e.path for e in tree.entries) == ["README.md", "src/app.py"]
    assert fake.requests[0].url.params["recursive"] == "1"
    assert fake.requests[0].headers["authorization"] == f"Bearer {TOKEN}"


async def test_a_truncated_tree_is_too_large() -> None:
    fake = FakeGitHubArchive(FILES)
    fake.truncated = True

    with pytest.raises(RepositoryTooLargeError):
        await source_for(fake).fetch_tree(TARGET)


async def test_falls_back_to_the_fork_when_the_base_repo_lacks_the_commit() -> None:
    fake = FakeGitHubArchive(FILES, repo="/repos/contributor/shop-fork")
    target = CheckoutTarget(
        "acme", "shop", HEAD_SHA, head_repo=RepoName("contributor", "shop-fork")
    )

    tree = await source_for(fake).fetch_tree(target)

    assert tree.source == RepoName("contributor", "shop-fork")
    assert [r.url.path.split("/")[3] for r in fake.requests] == ["shop", "shop-fork"]


async def test_a_commit_found_nowhere_is_reported_as_not_found() -> None:
    fake = FakeGitHubArchive(FILES, repo="/repos/someone/else")

    with pytest.raises(CheckoutUnavailableError, match="couldn't find"):
        await source_for(fake).fetch_tree(TARGET)


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"sha": "short", "tree": []},
        {"sha": TREE_SHA, "tree": [{"path": "a", "mode": "100644", "type": "blob", "sha": "x"}]},
        {"sha": TREE_SHA, "tree": [{"path": 1, "mode": "100644", "type": "blob", "sha": "a" * 40}]},
        {
            "sha": TREE_SHA,
            "tree": [{"path": "a", "mode": "100644", "type": "blob", "sha": "a" * 40}],
        },
        {"sha": TREE_SHA, "tree": [{"path": "a", "mode": "1", "type": "weird", "sha": "a" * 40}]},
    ],
)
async def test_a_malformed_tree_is_reported_not_crashed(body: object) -> None:
    fake = FakeGitHubArchive(FILES)
    fake.routes[f"/repos/acme/shop/git/trees/{HEAD_SHA}"] = lambda _: httpx.Response(200, json=body)

    with pytest.raises(CheckoutUnavailableError, match="unexpected way"):
        await source_for(fake).fetch_tree(TARGET)


async def test_downloads_the_archive_from_codeload_without_the_token(tmp_path: Path) -> None:
    fake = FakeGitHubArchive(FILES)
    destination = tmp_path / "archive.download"

    await source_for(fake).download_archive(SHOP, HEAD_SHA, destination, 10 * 1024 * 1024)

    assert destination.read_bytes() == fake.archive
    api, codeload = fake.requests
    assert api.url.path == f"/repos/acme/shop/tarball/{HEAD_SHA}"
    assert api.headers["authorization"] == f"Bearer {TOKEN}"
    assert codeload.url.host == "codeload.github.com"
    assert "authorization" not in codeload.headers
    assert codeload.headers["accept-encoding"] == "identity"


@pytest.mark.parametrize(
    "location",
    [
        None,
        "http://codeload.github.com/x",
        "https://evil.example/x",
        "https://codeload.github.com.evil.example/x",
        "https://user:pass@codeload.github.com/x",
        "https://codeload.github.com:8443/x",
        "https://169.254.169.254/latest",
        "not a url",
    ],
)
async def test_refuses_a_redirect_anywhere_but_codeload(
    tmp_path: Path, location: str | None
) -> None:
    fake = FakeGitHubArchive(FILES)
    fake.routes[f"/repos/acme/shop/tarball/{HEAD_SHA}"] = lambda _: httpx.Response(
        302, headers={} if location is None else {"location": location}
    )

    with pytest.raises(CheckoutUnavailableError):
        await source_for(fake).download_archive(SHOP, HEAD_SHA, tmp_path / "a", 1024 * 1024)

    assert all(r.url.host == "api.github.com" for r in fake.requests)


async def test_an_archive_answered_without_a_redirect_is_refused(tmp_path: Path) -> None:
    fake = FakeGitHubArchive(FILES)
    fake.routes[f"/repos/acme/shop/tarball/{HEAD_SHA}"] = lambda _: httpx.Response(200)

    with pytest.raises(CheckoutUnavailableError, match="unexpected way"):
        await source_for(fake).download_archive(SHOP, HEAD_SHA, tmp_path / "a", 1024 * 1024)


async def test_stops_an_archive_over_the_download_cap(tmp_path: Path) -> None:
    fake = FakeGitHubArchive(FILES)
    fake.codeload = lambda _: streamed(200, b"x" * 5000)

    with pytest.raises(RepositoryTooLargeError):
        await source_for(fake).download_archive(SHOP, HEAD_SHA, tmp_path / "a", 4096)


async def test_stops_at_the_overall_deadline(tmp_path: Path) -> None:
    fake = FakeGitHubArchive(FILES)

    async def never_ends(_: httpx.Request) -> httpx.Response:
        import anyio

        await anyio.sleep(10)
        return httpx.Response(200)

    fake.codeload = never_ends  # type: ignore[assignment]

    with pytest.raises(CheckoutUnavailableError, match="took too long") as raised:
        await source_for(fake, deadline_seconds=0.05).download_archive(
            SHOP, HEAD_SHA, tmp_path / "a", 1024
        )

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "didn't allow"), (403, "didn't allow"), (429, "didn't allow"), (500, "unexpected way")],
)
async def test_maps_github_refusals(tmp_path: Path, status: int, message: str) -> None:
    fake = FakeGitHubArchive(FILES)
    fake.routes[f"/repos/acme/shop/git/trees/{HEAD_SHA}"] = lambda _: httpx.Response(status)

    with pytest.raises(CheckoutUnavailableError, match=message):
        await source_for(fake).fetch_tree(TARGET)


async def test_network_failures_have_no_chained_details() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed {request.url}", request=request)

    source = GitHubArchiveSource(lambda: TOKEN, transport=httpx.MockTransport(broken))

    with pytest.raises(CheckoutUnavailableError, match="connection") as raised:
        await source.fetch_tree(TARGET)

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


async def fetched(source: GitHubArchiveSource, shas: list[str], max_bytes: int) -> dict[str, bytes]:
    """Every blob `fetch_blobs` hands over, in arrival order."""
    blobs: dict[str, bytes] = {}

    async def receive(sha: str, data: bytes) -> None:
        blobs[sha] = data

    await source.fetch_blobs(SHOP, shas, max_bytes, receive)
    return blobs


async def test_fetches_blobs_and_leaves_out_the_ones_github_lacks() -> None:
    fake = FakeGitHubArchive(FILES)
    wanted = [git_blob_sha(FILES["README.md"]), "e" * 40, "not-a-sha"]

    blobs = await fetched(source_for(fake), wanted, 1024)

    assert blobs == {git_blob_sha(FILES["README.md"]): FILES["README.md"]}
    assert len(fake.requests) == 2


async def test_a_blob_over_the_size_cap_is_left_out() -> None:
    fake = FakeGitHubArchive({"big.txt": b"x" * 100})

    blobs = await fetched(source_for(fake), [git_blob_sha(b"x" * 100)], 10)

    assert blobs == {}


async def test_a_rate_limited_blob_fails_the_whole_refetch() -> None:
    fake = FakeGitHubArchive(FILES)
    sha = git_blob_sha(FILES["README.md"])
    fake.routes[f"/repos/acme/shop/git/blobs/{sha}"] = lambda _: httpx.Response(429)

    with pytest.raises(CheckoutUnavailableError, match="didn't allow"):
        await fetched(source_for(fake), [sha, git_blob_sha(FILES["src/app.py"])], 1024)


async def test_refetch_asks_for_at_most_300_blobs() -> None:
    fake = FakeGitHubArchive({})

    await fetched(source_for(fake), [f"{i:040x}" for i in range(400)], 1024)

    assert len(fake.requests) == 300


async def test_only_get_requests_are_sent(tmp_path: Path) -> None:
    fake = FakeGitHubArchive(FILES)
    source = source_for(fake)

    await source.fetch_tree(TARGET)
    await source.download_archive(SHOP, HEAD_SHA, tmp_path / "a", 1024 * 1024)
    await fetched(source, [git_blob_sha(FILES["README.md"])], 1024)

    assert {r.method for r in fake.requests} == {"GET"}
