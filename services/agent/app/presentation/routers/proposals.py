from typing import Annotated, Any

from fastapi import APIRouter, Request

from app.application.proposals import ProposalService
from app.presentation import deps

router = APIRouter(prefix="/v1/agent/proposals", tags=["proposals"])


def _service(request: Request, session: deps.Session) -> ProposalService:
    return deps.build_proposals(request.app, session)


Proposals = Annotated[ProposalService, deps.Depends(_service)]


@router.post("/{proposal_id}/apply", summary="Approve: save what an agent proposed")
async def apply(proposal_id: str, proposals: Proposals, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return await proposals.apply(me, token, proposal_id)


@router.post("/{proposal_id}/drop", summary="Not this one")
async def drop(proposal_id: str, proposals: Proposals, me: deps.Me) -> dict[str, Any]:
    return await proposals.drop(me, proposal_id)
