"""The real subprocess runner, exercised with small Python scripts we control."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

from pr_evidence_reviewer.llm.process import (
    MAX_STDERR_BYTES,
    AsyncProcessRunner,
    OutputTooLargeError,
    ProcessResult,
    ProcessTimeoutError,
)

pytestmark = pytest.mark.anyio


def python(code: str) -> list[str]:
    return [sys.executable, "-I", "-c", code]


async def run(
    code: str, tmp_path: Path, *, stdin: bytes = b"", timeout: float = 10, cap: int = 10_000
) -> ProcessResult:
    return await AsyncProcessRunner().run(
        python(code), stdin=stdin, cwd=tmp_path, timeout=timeout, max_output_bytes=cap
    )


async def test_passes_stdin_and_returns_stdout(tmp_path: Path) -> None:
    result = await run(
        "import sys; sys.stdout.write(sys.stdin.read().upper())", tmp_path, stdin=b"hello"
    )

    assert result.returncode == 0
    assert result.stdout == b"HELLO"


async def test_runs_in_the_given_directory(tmp_path: Path) -> None:
    result = await run("import os; print(os.getcwd(), end='')", tmp_path)

    assert Path(result.stdout.decode()).resolve() == tmp_path.resolve()


async def test_inherits_the_environment_by_default(tmp_path: Path) -> None:
    result = await run("import os; print('PATH' in os.environ, end='')", tmp_path)

    assert result.stdout == b"True"


async def test_passes_the_given_environment_instead(tmp_path: Path) -> None:
    env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "PREV_MARKER": "set"}
    result = await AsyncProcessRunner().run(
        python("import os; print(os.environ.get('PREV_MARKER'), 'PATH' in os.environ, end='')"),
        stdin=b"",
        cwd=tmp_path,
        timeout=10,
        max_output_bytes=10_000,
        env=env,
    )

    assert result.stdout == b"set False"


async def test_reports_the_exit_code_and_stderr(tmp_path: Path) -> None:
    result = await run("import sys; sys.stderr.write('bad'); sys.exit(3)", tmp_path)

    assert result.returncode == 3
    assert result.stderr == b"bad"


async def test_kills_a_process_that_runs_too_long(tmp_path: Path) -> None:
    with pytest.raises(ProcessTimeoutError):
        await run("import time; time.sleep(30)", tmp_path, timeout=0.5)


GRANDCHILD = """
import sys, time
for _ in range(400):
    with open(sys.argv[1], "a") as log:
        log.write("x")
    time.sleep(0.05)
"""
CHILD_THAT_RELAUNCHES = f"""
import subprocess, sys, time
subprocess.Popen(
    [sys.executable, "-I", "-c", {GRANDCHILD!r}, sys.argv[1]],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
time.sleep(30)
"""


async def test_a_timeout_also_kills_what_the_process_started(tmp_path: Path) -> None:
    log = tmp_path / "grandchild.log"
    argv = [*python(CHILD_THAT_RELAUNCHES), str(log)]

    with pytest.raises(ProcessTimeoutError):
        await AsyncProcessRunner().run(
            argv, stdin=b"", cwd=tmp_path, timeout=5, max_output_bytes=1000
        )

    assert log.exists()
    size_after_kill = log.stat().st_size
    await asyncio.sleep(1)
    assert log.stat().st_size == size_after_kill


async def test_kills_a_process_that_writes_too_much(tmp_path: Path) -> None:
    with pytest.raises(OutputTooLargeError):
        await run("import sys; sys.stdout.write('x' * 50_000)", tmp_path, cap=1000)


async def test_truncates_stderr_instead_of_failing(tmp_path: Path) -> None:
    result = await run(f"import sys; sys.stderr.write('e' * {MAX_STDERR_BYTES * 2})", tmp_path)

    assert result.returncode == 0
    assert len(result.stderr) == MAX_STDERR_BYTES


async def test_survives_a_process_that_ignores_stdin(tmp_path: Path) -> None:
    result = await run("pass", tmp_path, stdin=b"x" * 2_000_000)

    assert result.returncode == 0


async def test_arguments_are_not_interpreted_by_a_shell(tmp_path: Path) -> None:
    hostile = "$(echo pwned) ; echo pwned & echo pwned | `echo pwned`"
    result = await AsyncProcessRunner().run(
        [*python("import sys; print(sys.argv[1], end='')"), hostile],
        stdin=b"",
        cwd=tmp_path,
        timeout=10,
        max_output_bytes=10_000,
    )

    assert result.stdout.decode() == hostile
