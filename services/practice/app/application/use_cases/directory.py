"""Counsel, clients and projects."""

from stiamo_common.errors import ValidationFailed

from app.application.crud import Crud
from app.application.ports import Env
from app.domain.directory import Client, Counsel, Project
from app.domain.rules import required, unique, valid_email


def normalize_counsel(c: Counsel) -> Counsel:
    return c.model_copy(update={
        "name": required(c.name, "Name"), "email": valid_email(c.email), "expertise": unique(c.expertise),
    })


def normalize_client(c: Client) -> Client:
    return c.model_copy(update={"name": required(c.name, "Name"), "email": valid_email(c.email)})


def normalize_project(p: Project) -> Project:
    if p.start_date and p.due_date and p.due_date < p.start_date:
        raise ValidationFailed("The due date can’t be before the start date")
    return p.model_copy(update={"title": required(p.title, "Title"), "client_id": required(p.client_id, "Client")})


def counsel_crud(env: Env) -> Crud[Counsel]:
    return Crud(env, "counsel", Counsel, env.stores.counsel, normalize=normalize_counsel)


class ProjectUseCases(Crud[Project]):
    def __init__(self, env: Env) -> None:
        super().__init__(env, "project", Project, env.stores.project, normalize=normalize_project)

    async def save(self, user_id, id, draft):
        if await self.env.stores.client.get(user_id, draft.client_id) is None:
            raise ValidationFailed("That client no longer exists")
        return await super().save(user_id, id, draft)


def project_crud(env: Env) -> Crud[Project]:
    return ProjectUseCases(env)


class ClientUseCases(Crud[Client]):
    """Deleting a client deletes its projects. Meetings, tasks and documents that pointed at them stay, untied."""

    def __init__(self, env: Env) -> None:
        super().__init__(env, "client", Client, env.stores.client, normalize=normalize_client)
        self._projects = project_crud(env)

    async def projects_of(self, user_id: str, client_id: str) -> list[Project]:
        return [p for p in await self.env.stores.project.list(user_id) if p.client_id == client_id]

    async def remove(self, user_id: str, id: str) -> None:
        for project in await self.projects_of(user_id, id):
            await self._projects.remove(user_id, project.id)
        await super().remove(user_id, id)
