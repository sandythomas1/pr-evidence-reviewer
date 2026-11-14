"""A scripted LLM provider for tests. It validates replies exactly like a real adapter does."""

from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from pr_evidence_reviewer.llm.base import (
    ProviderStatus,
    StructuredRequest,
    StructuredResult,
    Usage,
    validate_output,
)
from pr_evidence_reviewer.llm.process import ProcessResult


class FakeProvider:
    id = "fake"
    name = "Fake model"

    def __init__(self, *replies: object, available: bool = True) -> None:
        self._replies = list(replies)
        self._available = available
        self.requests: list[StructuredRequest[BaseModel]] = []

    def status(self) -> ProviderStatus:
        detail = "Ready." if self._available else "Not installed."
        return ProviderStatus(id=self.id, name=self.name, available=self._available, detail=detail)

    async def complete_structured[T: BaseModel](
        self, request: StructuredRequest[T]
    ) -> StructuredResult[T]:
        self.requests.append(request)  # type: ignore[arg-type]
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        output = validate_output(reply, request.output_type, self.name)
        return StructuredResult(output=output, provider=self.id, model="fake-1", usage=Usage())


class StubRunner:
    """Stands in for the subprocess. Records each call and what the working directory held."""

    def __init__(self, result: ProcessResult | Exception) -> None:
        self._result = result
        self.argv: list[str] = []
        self.stdin = b""
        self.cwd: Path | None = None
        self.cwd_contents: list[str] = []
        self.timeout = 0.0
        self.max_output_bytes = 0

    async def run(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes,
        cwd: Path,
        timeout: float,
        max_output_bytes: int,
    ) -> ProcessResult:
        self.argv = list(argv)
        self.stdin = stdin
        self.cwd = cwd
        self.cwd_contents = [entry.name for entry in cwd.iterdir()]
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes
        if isinstance(self._result, Exception):
            raise self._result
        return self._result
