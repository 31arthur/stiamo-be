"""Evidence about people, the person's own lenses, and a character's profile."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.application.use_cases.insight import EvidenceUseCases, ProfileUseCases
from app.domain.common import DomainModel
from app.domain.insight import Evidence, Lens, Verdict
from app.presentation import deps
from app.presentation.crud_router import crud_router, dump

evidence = crud_router(prefix="/v1/evidence", tag="insight", model=Evidence, service_dep=deps.evidence_service)
lenses = crud_router(prefix="/v1/lenses", tag="insight", model=Lens, service_dep=deps.lens_service)
extra = APIRouter(tags=["insight"])

Evidences = Annotated[EvidenceUseCases, Depends(deps.evidence_service)]
Profiles = Annotated[ProfileUseCases, Depends(deps.profile_service)]


class SettleBody(DomainModel):
    verdict: Verdict


@extra.post("/v1/evidence/{evidence_id}/settle", summary="Say how a prediction turned out (right or wrong)")
async def settle(evidence_id: str, body: SettleBody, principal: deps.CurrentPrincipal, svc: Evidences) -> dict[str, Any]:
    return dump(await svc.settle(principal.user_id, evidence_id, body.verdict))


@extra.get("/v1/characters/{character_id}/profile", summary="A character's profile, worked out now from every piece of evidence and their call log")
async def profile(character_id: str, principal: deps.CurrentPrincipal, svc: Profiles) -> dict[str, Any]:
    return await svc.refresh(principal.user_id, character_id)
