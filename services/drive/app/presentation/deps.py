from typing import Annotated

from fastapi import Depends, Request
from stiamo_common.security import Principal, make_principal_dependency

from app.application.use_cases import DriveSearch
from app.config import get_settings

current_principal = make_principal_dependency(lambda: get_settings().token_settings())
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def drive_search(request: Request) -> DriveSearch:
    s = request.app.state
    return DriveSearch(s.gateway, s.settings.allowed, s.settings.folder_id)


Search = Annotated[DriveSearch, Depends(drive_search)]
