"""One error envelope for every API failure: {"error": {"code": ..., "message": ...}}."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pr_evidence_reviewer.errors import ReviewerError

logger = logging.getLogger(__name__)


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


def _envelope(status: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status, content=body.model_dump())


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ReviewerError)
    async def reviewer_error(_: Request, error: ReviewerError) -> JSONResponse:
        return _envelope(error.http_status, error.code, error.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        fields = sorted(
            {".".join(str(part) for part in item["loc"][1:]) for item in error.errors()}
        )
        return _envelope(422, "invalid_request", f"Check these fields: {', '.join(fields)}.")

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, error: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=error)
        return _envelope(500, "internal_error", "Something went wrong in the reviewer. Try again.")
