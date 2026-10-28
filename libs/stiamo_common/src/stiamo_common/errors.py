"""Application error model shared by every service, rendered as one consistent JSON shape."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.__class__.__doc__ or self.code
        super().__init__(self.message)


class ValidationFailed(AppError):
    """The request was well-formed but not acceptable."""

    status_code = 422
    code = "validation_failed"


class Unauthorized(AppError):
    """Authentication is required or the credentials are not valid."""

    status_code = 401
    code = "unauthorized"


class Forbidden(AppError):
    """You are not allowed to do that."""

    status_code = 403
    code = "forbidden"


class NotFound(AppError):
    """The requested resource does not exist."""

    status_code = 404
    code = "not_found"


class Conflict(AppError):
    """The request conflicts with the current state."""

    status_code = 409
    code = "conflict"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, Unauthorized) else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _request_invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p not in ("body", "query", "path"))
        message = f"{where}: {first.get('msg', 'invalid value')}" if where else first.get("msg", "invalid request")
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "validation_failed", "message": message}},
        )

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, __: IntegrityError) -> JSONResponse:
        # A constraint the application didn't check first (unique key, missing parent row, …).
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "conflict", "message": "The change conflicts with existing data."}},
        )

    @app.exception_handler(ValidationError)
    async def _model_invalid(_: Request, exc: ValidationError) -> JSONResponse:
        # A domain model rejected a value the use case built or loaded.
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()))
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "validation_failed", "message": f"{where}: {first.get('msg', 'invalid value')}"}},
        )
