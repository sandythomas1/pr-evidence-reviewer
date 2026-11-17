"""The `prev` command."""

import argparse
import dataclasses
import sys
from collections.abc import Callable, Sequence

import uvicorn

from pr_evidence_reviewer import __version__
from pr_evidence_reviewer.api.app import create_app
from pr_evidence_reviewer.checkout.cache import CheckoutCache, ClearResult
from pr_evidence_reviewer.config import DEFAULT_HOST, DEFAULT_PORT, Settings
from pr_evidence_reviewer.errors import CheckoutUnavailableError
from pr_evidence_reviewer.github.archive import GitHubArchiveSource
from pr_evidence_reviewer.github.token import GhCliTokenProvider

NON_LOOPBACK_WARNING = (
    "Warning: binding to {host} makes the reviewer reachable from other machines. "
    "It reads GitHub with your gh sign-in, so keep it on 127.0.0.1 unless you know you need this."
)

Server = Callable[[Settings], None]


def _run_uvicorn(settings: Settings) -> None:
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prev", description="PR Evidence Reviewer local runner.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="Start the reviewer on localhost.")
    serve.add_argument(
        "--host", default=DEFAULT_HOST, help=f"Interface to bind (default {DEFAULT_HOST})."
    )
    serve.add_argument(
        "--port", type=_port, default=DEFAULT_PORT, help=f"Port (default {DEFAULT_PORT})."
    )

    cache = commands.add_parser("cache", help="Manage the reviewer's saved copies of repositories.")
    cache_commands = cache.add_subparsers(dest="cache_command", required=True)
    clear = cache_commands.add_parser(
        "clear", help="Delete every saved repository copy (they are downloaded again when needed)."
    )
    clear.add_argument("--packets", action="store_true", help="Also delete saved evidence packets.")
    return parser


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def main(
    argv: Sequence[str] | None = None,
    server: Server = _run_uvicorn,
    base_settings: Settings | None = None,
) -> int:
    args = _build_parser().parse_args(argv)
    base = base_settings or Settings()
    if args.command == "cache":
        return _clear_cache(base, include_packets=args.packets)
    return _serve(dataclasses.replace(base, host=args.host, port=args.port), server)


def _clear_cache(settings: Settings, *, include_packets: bool) -> int:
    """Delete saved repository copies (and packets if asked). Prints counts and sizes only."""
    cache = CheckoutCache(settings.cache_dir, GitHubArchiveSource(GhCliTokenProvider()))
    try:
        result = cache.clear(settings.packets_dir if include_packets else None)
    except CheckoutUnavailableError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(_cleared_message(result, include_packets=include_packets))
    if result.failed:
        print(_not_cleared_message(result.failed), file=sys.stderr)
        return 1
    return 0


def _not_cleared_message(failed: int) -> str:
    copies = "copy" if failed == 1 else "copies"
    return (
        f"Couldn't delete {failed} saved repository {copies}. "
        "Close any program using the cache folder and run the command again."
    )


def _cleared_message(result: ClearResult, *, include_packets: bool) -> str:
    copies = "copy" if result.checkouts == 1 else "copies"
    lines = [
        f"Deleted {result.checkouts} saved repository {copies} ({_size(result.checkout_bytes)})."
    ]
    if include_packets:
        packets = "packet" if result.packets == 1 else "packets"
        lines.append(
            f"Deleted {result.packets} saved evidence {packets} ({_size(result.packet_bytes)})."
        )
    return "\n".join(lines)


def _size(count: int) -> str:
    size = float(count)
    for unit in ("bytes", "KB", "MB"):
        if size < 1024:
            return f"{count} bytes" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _serve(settings: Settings, server: Server) -> int:
    if not settings.is_loopback:
        print(NON_LOOPBACK_WARNING.format(host=settings.host), file=sys.stderr)
    print(
        f"PR Evidence Reviewer running at http://{settings.host}:{settings.port}", file=sys.stderr
    )
    server(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
