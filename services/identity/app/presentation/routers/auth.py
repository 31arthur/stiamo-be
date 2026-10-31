from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from stiamo_common.errors import Unauthorized

from app.application.dto import AuthResult
from app.application.throttle import LoginThrottle
from app.application.use_cases.auth import GetProfile, LoginUser, LogoutSession, RefreshSession, RegisterUser
from app.presentation import deps
from app.presentation.schemas import (
    AuthOut,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenOut,
    UserOut,
)

router = APIRouter(prefix="/v1", tags=["auth"])


def _auth_out(r: AuthResult) -> AuthOut:
    return AuthOut(
        user=UserOut(id=r.user.id, name=r.user.name, email=r.user.email, created_at=r.user.created_at),
        tokens=TokenOut(access_token=r.tokens.access_token, refresh_token=r.tokens.refresh_token,
                        expires_in=r.tokens.expires_in),
    )


@router.post("/auth/register", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, uc: Annotated[RegisterUser, Depends(deps.register_uc)]):
    return _auth_out(await uc(name=body.name, email=body.email, password=body.password))


@router.post("/auth/login", response_model=AuthOut)
async def login(body: LoginRequest, request: Request, uc: Annotated[LoginUser, Depends(deps.login_uc)]):
    throttle: LoginThrottle = request.app.state.login_throttle
    # Behind the reverse proxy the real address is the last one it appended; a client can't forge that one.
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
    key = f"{body.email.strip().lower()}|{forwarded or (request.client.host if request.client else '')}"
    throttle.check(key)
    try:
        result = await uc(email=body.email, password=body.password)
    except Unauthorized:
        throttle.failed(key)
        raise
    throttle.succeeded(key)
    return _auth_out(result)


@router.post("/auth/refresh", response_model=AuthOut)
async def refresh(body: RefreshRequest, uc: Annotated[RefreshSession, Depends(deps.refresh_uc)]):
    return _auth_out(await uc(refresh_token=body.refresh_token))


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, uc: Annotated[LogoutSession, Depends(deps.logout_uc)]):
    await uc(refresh_token=body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/users/me", response_model=UserOut)
async def me(principal: deps.CurrentPrincipal, uc: Annotated[GetProfile, Depends(deps.profile_uc)]):
    user = await uc(user_id=principal.user_id)
    return UserOut(id=user.id, name=user.name, email=user.email, created_at=user.created_at)
