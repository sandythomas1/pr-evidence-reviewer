"""The `prev` command."""

import argparse
import sys
from collections.abc import Callable, Sequence

import uvicorn

from pr_evidence_reviewer import __version__
from pr_evidence_reviewer.api.app import create_app
from pr_evidence_reviewer.config import DEFAULT_HOST, DEFAULT_PORT, Settings

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
    return parser


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def main(argv: Sequence[str] | None = None, server: Server = _run_uvicorn) -> int:
    args = _build_parser().parse_args(argv)
    settings = Settings(host=args.host, port=args.port)
    if not settings.is_loopback:
        print(NON_LOOPBACK_WARNING.format(host=settings.host), file=sys.stderr)
    print(
        f"PR Evidence Reviewer running at http://{settings.host}:{settings.port}", file=sys.stderr
    )
    server(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
