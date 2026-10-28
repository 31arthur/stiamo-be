from typing import Annotated

from fastapi import APIRouter, Depends

from app.application.use_cases.directory import ClientUseCases
from app.domain.directory import Client, Counsel, Project
from app.presentation import deps
from app.presentation.crud_router import crud_router, dump

counsel = crud_router(prefix="/v1/counsel", tag="counsel", model=Counsel, service_dep=deps.counsel_service)
clients = crud_router(prefix="/v1/clients", tag="clients", model=Client, service_dep=deps.client_service)
projects = crud_router(prefix="/v1/projects", tag="projects", model=Project, service_dep=deps.project_service)

extra = APIRouter(prefix="/v1/clients", tags=["clients"])


@extra.get("/{client_id}/projects", summary="Projects of a client")
async def projects_of(client_id: str, principal: deps.CurrentPrincipal,
                      svc: Annotated[ClientUseCases, Depends(deps.client_service)]):
    return {"items": [dump(p) for p in await svc.projects_of(principal.user_id, client_id)]}
