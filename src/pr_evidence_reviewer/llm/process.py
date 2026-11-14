"""Runs a CLI provider as a child process: no shell, a hard timeout, and capped output."""

import asyncio
import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

READ_CHUNK_BYTES = 64 * 1024
MAX_STDERR_BYTES = 64 * 1024


class ProcessTimeoutError(Exception):
    """The process didn't finish in time and was killed."""


class OutputTooLargeError(Exception):
    """The process wrote more than the allowed output and was killed."""


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class ProcessRunner(Protocol):
    async def run(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes,
        cwd: Path,
        timeout: float,
        max_output_bytes: int,
    ) -> ProcessResult: ...


class AsyncProcessRunner:
    """The real runner. Arguments go straight to the OS, never through a shell."""

    async def run(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes,
        cwd: Path,
        timeout: float,
        max_output_bytes: int,
    ) -> ProcessResult:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            await _kill(process)
            raise OSError("The provider process started without its pipes.")
        try:
            async with asyncio.timeout(timeout):
                _, stdout, stderr = await asyncio.gather(
                    _feed(process.stdin, stdin),
                    _read(process.stdout, max_output_bytes, truncate=False),
                    _read(process.stderr, MAX_STDERR_BYTES, truncate=True),
                )
                returncode = await process.wait()
        except TimeoutError as error:
            await _kill(process)
            raise ProcessTimeoutError from error
        except BaseException:
            await _kill(process)
            raise
        return ProcessResult(returncode=returncode, stdout=stdout, stderr=stderr)


async def _feed(stream: asyncio.StreamWriter, data: bytes) -> None:
    try:
        stream.write(data)
        await stream.drain()
    except (BrokenPipeError, ConnectionResetError):
        return  # The process exited before reading everything; its exit code tells the story.
    finally:
        stream.close()


async def _read(stream: asyncio.StreamReader, limit: int, *, truncate: bool) -> bytes:
    buffer = bytearray()
    while chunk := await stream.read(READ_CHUNK_BYTES):
        room = limit - len(buffer)
        if len(chunk) > room and not truncate:
            raise OutputTooLargeError
        buffer.extend(chunk[: max(room, 0)])
    return bytes(buffer)


async def _kill(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
