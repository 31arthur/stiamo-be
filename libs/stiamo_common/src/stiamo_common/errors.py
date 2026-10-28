"""Application error model shared by every service, rendered as one consistent JSON shape."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


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
