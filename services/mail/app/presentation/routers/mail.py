import asyncio
import json
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse
from stiamo_common.errors import ValidationFailed

from app.application.accounts import MailService
from app.infrastructure.db.repositories import SqlAccountRepository
from app.presentation.deps import CurrentPrincipal, Mail
from app.presentation.schemas import AccountOut, ConnectIn, EditIn, FolderOut, MarkIn, MessageOut, PageOut

router = APIRouter(prefix="/v1/mail", tags=["mail"])

FolderParam = Annotated[str, Query(max_length=300)]
_KEEPALIVE = 20.0  # seconds of quiet before a comment is sent, so proxies don't close an idle stream


@router.get("/accounts", response_model=list[AccountOut])
async def accounts(me: CurrentPrincipal, mail: Mail, workspace: str | None = None):
    found = await mail.all_for(me.user_id)
    return [AccountOut.of(a) for a in found if workspace in (None, a.workspace)]


@router.post("/accounts", response_model=AccountOut, status_code=201)
async def connect(body: ConnectIn, me: CurrentPrincipal, mail: Mail):
    account = await mail.connect(me.user_id, **body.model_dump(exclude={"label"}), label=body.label)
    return AccountOut.of(account)


@router.patch("/accounts/{account_id}", response_model=AccountOut)
async def edit(account_id: str, body: EditIn, me: CurrentPrincipal, mail: Mail):
    return AccountOut.of(await mail.edit(me.user_id, account_id, **body.model_dump(exclude_none=True)))


@router.delete("/accounts/{account_id}", status_code=204)
async def disconnect(account_id: str, me: CurrentPrincipal, mail: Mail):
    await mail.disconnect(me.user_id, account_id)
    return Response(status_code=204)


@router.post("/accounts/{account_id}/test", status_code=204)
async def test(account_id: str, me: CurrentPrincipal, mail: Mail):
    await mail.test(me.user_id, account_id)
    return Response(status_code=204)


@router.get("/accounts/{account_id}/folders", response_model=list[FolderOut])
async def folders(account_id: str, me: CurrentPrincipal, mail: Mail):
    return [FolderOut.of(f) for f in await mail.folders(me.user_id, account_id)]


@router.get("/accounts/{account_id}/messages", response_model=PageOut)
async def messages(
    account_id: str, request: Request, me: CurrentPrincipal, mail: Mail, folder: FolderParam = "INBOX",
    before: int | None = Query(None, ge=1), q: str = Query("", max_length=200), limit: int | None = Query(None, ge=1, le=100),
):  # fmt: skip
    size = limit or request.app.state.settings.page_size
    return PageOut.of(await mail.page(me.user_id, account_id, folder, before=before, limit=size, query=q))


@router.get("/accounts/{account_id}/messages/{uid}", response_model=MessageOut)
async def message(account_id: str, uid: int, me: CurrentPrincipal, mail: Mail, folder: FolderParam = "INBOX"):
    return MessageOut.of(await mail.message(me.user_id, account_id, folder, uid))


@router.patch("/accounts/{account_id}/messages/{uid}", status_code=204)
async def mark(account_id: str, uid: int, body: MarkIn, me: CurrentPrincipal, mail: Mail, folder: FolderParam = "INBOX"):
    if body.seen is None and body.flagged is None:
        raise ValidationFailed("Say what to change: seen or flagged.")
    await mail.mark(me.user_id, account_id, folder, uid, seen=body.seen, flagged=body.flagged)
    return Response(status_code=204)


@router.get("/accounts/{account_id}/messages/{uid}/attachments/{index}")
async def attachment(account_id: str, uid: int, index: int, me: CurrentPrincipal, mail: Mail, folder: FolderParam = "INBOX"):
    file = await mail.attachment(me.user_id, account_id, folder, uid, index)
    # Always a download, never rendered by the browser, whatever the sender claimed it was.
    return Response(
        file.data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.filename)}", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/accounts/{account_id}/events")
async def events(account_id: str, request: Request, me: CurrentPrincipal, folder: FolderParam = "INBOX"):
    """Server-sent events for one folder: `state` (connecting, live, down, auth) and `changed` (ask for the list again)."""
    s = request.app.state
    # Its own short session: a stream can last hours and must not hold a database connection.
    async with s.session_factory() as session:
        account = await MailService(SqlAccountRepository(session, s.box), s.gateway, s.live)._account(me.user_id, account_id)

    async def stream():
        async with s.live.listen(account, folder) as queue:
            yield "retry: 5000\n\n"
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), _KEEPALIVE)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if event is None:
                    return
                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
