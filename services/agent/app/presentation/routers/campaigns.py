import html
from typing import Annotated, Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.application.campaigns import CampaignService
from app.presentation import deps

router = APIRouter(prefix="/v1/agent/campaigns", tags=["campaigns"])
public = APIRouter(tags=["campaigns"])

CRAWLERS = ("whatsapp", "facebookexternalhit", "facebot", "twitterbot", "telegrambot", "linkedinbot", "slackbot", "discordbot", "googlebot", "bingbot", "skypeuripreview", "preview", "bot/", "crawler", "spider")


def _service(request: Request, session: deps.Session) -> CampaignService:
    return deps.build_campaigns(request.app, session)


Campaigns = Annotated[CampaignService, deps.Depends(_service)]


class CampaignBody(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    #: The words. {name} is each person's first name; {link} is where the link goes (else at the end).
    message: Annotated[str, Field(max_length=2000)] = ""
    url: Annotated[str, Field(max_length=2000)] = ""
    image_url: Annotated[str, Field(max_length=2000)] = ""
    track_clicks: bool = True
    status: Annotated[str | None, Field(pattern="^(active|closed)$")] = None


class PreviewBody(BaseModel):
    url: Annotated[str, Field(min_length=1, max_length=2000)]


class AddBody(BaseModel):
    contacts: Annotated[list[dict[str, Any]], Field(max_length=500)]


class MarkBody(BaseModel):
    state: Annotated[str, Field(pattern="^(pending|sent|replied|declined|skipped)$")]
    note: Annotated[str | None, Field(max_length=1000)] = None


@router.get("", summary="The campaigns, newest first, each with how many are sent, answered and opened")
async def campaigns(svc: Campaigns, me: deps.Me) -> dict[str, Any]:
    return {"items": await svc.list(me)}


@router.post("/preview", summary="How a link will look when it is shared on WhatsApp: its title, description and picture")
async def preview(body: PreviewBody, svc: Campaigns, me: deps.Me) -> dict[str, Any]:
    return await svc.preview(body.url)


@router.post("", summary="Start a campaign: words, a link or a picture")
async def create(body: CampaignBody, svc: Campaigns, me: deps.Me) -> dict[str, Any]:
    return await svc.save(me, None, name=body.name, message=body.message, url=body.url, image_url=body.image_url, track_clicks=body.track_clicks, status=body.status)


@router.get("/{campaign_id}", summary="One campaign with every person in it, their message, their WhatsApp link and how it went")
async def detail(campaign_id: str, svc: Campaigns, me: deps.Me) -> dict[str, Any]:
    return await svc.detail(me, campaign_id)


@router.put("/{campaign_id}", summary="Change a campaign's words, link, picture or whether it is open")
async def change(campaign_id: str, body: CampaignBody, svc: Campaigns, me: deps.Me) -> dict[str, Any]:
    return await svc.save(me, campaign_id, name=body.name, message=body.message, url=body.url, image_url=body.image_url, track_clicks=body.track_clicks, status=body.status)


@router.delete("/{campaign_id}", status_code=204, summary="Delete a campaign and its list")
async def delete(campaign_id: str, svc: Campaigns, me: deps.Me) -> Response:
    await svc.delete(me, campaign_id)
    return Response(status_code=204)


@router.post("/{campaign_id}/contacts", summary="Put contacts in the campaign (one already in it stays as it is)")
async def add(campaign_id: str, body: AddBody, svc: Campaigns, me: deps.Me) -> dict[str, Any]:
    return await svc.add_contacts(me, campaign_id, body.contacts)


@router.post("/members/{member_id}", summary="Mark one person: sent, replied, declined, skipped or back to pending")
async def mark(member_id: str, body: MarkBody, svc: Campaigns, me: deps.Me) -> dict[str, Any]:
    return await svc.mark(me, member_id, body.state, body.note)


@router.delete("/members/{member_id}", summary="Take one person out of the campaign")
async def remove(member_id: str, svc: Campaigns, me: deps.Me) -> dict[str, Any]:
    return await svc.remove(me, member_id)


@public.get("/c/{token}", include_in_schema=False)
async def opened(token: str, request: Request, svc: Campaigns):
    """A person's own link. WhatsApp (and other crawlers) get the page's preview so the message looks right; a person is counted and sent on to the page."""
    agent = request.headers.get("user-agent", "").lower()
    crawler = any(k in agent for k in CRAWLERS)
    found = await svc.opened(token, crawler=crawler)
    if found is None or not found["url"]:
        return HTMLResponse("<!doctype html><title>Not found</title><p>This link is no longer active.</p>", status_code=404)
    if not crawler:
        return RedirectResponse(found["url"], status_code=302)
    p = found["preview"] or {}
    e = lambda v: html.escape(str(v or ""), quote=True)  # noqa: E731
    tags = [("og:type", "website"), ("og:title", p.get("title") or found["name"]), ("og:description", p.get("description")), ("og:image", p.get("image")), ("og:site_name", p.get("site")), ("og:url", p.get("url") or found["url"])]
    meta = "".join(f'<meta property="{k}" content="{e(v)}">' for k, v in tags if v)
    return HTMLResponse(f'<!doctype html><html><head><meta charset="utf-8"><title>{e(p.get("title") or found["name"])}</title>{meta}</head><body><a href="{e(found["url"])}">{e(found["url"])}</a></body></html>')
