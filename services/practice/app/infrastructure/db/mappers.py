"""Translation between domain entities and table rows.

Entities whose fields line up one-to-one with their columns use `flat_mapper`; the rest (nested value objects
flattened into columns) have hand-written mappers.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.domain.cases import Engagement, Hearing, HearingAction, LegalCase
from app.domain.common import EntityRef, Outcome, TimeSlot, to_dt, to_iso
from app.domain.goals import CheckIn, Goal, KeyResult
from app.domain.history import Activity, FieldChange, Link
from app.domain.plans import ActionPlan
from app.domain.work import AgendaItem, Attempt, DueDate, FollowUp, Meeting, Task
from app.infrastructure.db.models import (
    ActivityModel,
    FollowUpModel,
    GoalModel,
    HearingModel,
    LegalCaseModel,
    LinkModel,
    MeetingModel,
    PlanModel,
    TaskModel,
)


@dataclass(frozen=True)
class Mapper:
    model: type
    to_row: Callable[[Any], dict[str, Any]]
    from_row: Callable[[Any], Any]


def base_row(e) -> dict[str, Any]:
    return {"user_id": e.user_id, "id": e.id, "created_at": to_dt(e.created_at), "updated_at": to_dt(e.updated_at)}


def base_fields(m) -> dict[str, Any]:
    return {"user_id": m.user_id, "id": m.id, "created_at": to_iso(m.created_at), "updated_at": to_iso(m.updated_at)}


def flat_mapper(model: type, entity: type) -> Mapper:
    columns = [c.key for c in model.__table__.columns if c.key not in ("created_at", "updated_at")]

    def to_row(e) -> dict[str, Any]:
        return {**{c: getattr(e, c) for c in columns}, **base_row(e)}

    def from_row(m) -> Any:
        return entity.model_validate({**{c: getattr(m, c) for c in columns}, **base_fields(m)})

    return Mapper(model, to_row, from_row)


# --- reusable column groups -----------------------------------------------------------------------------


def ref_to_cols(ref: EntityRef | None, prefix: str) -> dict[str, Any]:
    return {f"{prefix}_kind": ref.kind if ref else None, f"{prefix}_id": ref.id if ref else None}


def ref_from_cols(m, prefix: str) -> EntityRef | None:
    kind = getattr(m, f"{prefix}_kind")
    return EntityRef(kind=kind, id=getattr(m, f"{prefix}_id")) if kind else None


def outcome_to_cols(o: Outcome) -> dict[str, Any]:
    return {
        "outcome_status": o.status, "outcome_comment": o.comment, "outcome_next_action": o.next_action,
        "outcome_recorded_at": to_dt(o.recorded_at) if o.recorded_at else None,
    }


def outcome_from_cols(m) -> Outcome:
    return Outcome(
        status=m.outcome_status, comment=m.outcome_comment, next_action=m.outcome_next_action,
        recorded_at=to_iso(m.outcome_recorded_at) if m.outcome_recorded_at else None,
    )


def slot_to_cols(s: TimeSlot | None, prefix: str = "slot") -> dict[str, Any]:
    return {
        f"{prefix}_date": s.date if s else None,
        f"{prefix}_start": s.start if s else None,
        f"{prefix}_duration": s.duration if s else None,
    }


def slot_from_cols(m, prefix: str = "slot") -> TimeSlot | None:
    date = getattr(m, f"{prefix}_date")
    if date is None:
        return None
    return TimeSlot(date=date, start=getattr(m, f"{prefix}_start"), duration=getattr(m, f"{prefix}_duration"))


# --- history ------------------------------------------------------------------------------------------


def _activity_to_row(a: Activity) -> dict[str, Any]:
    return {
        **base_row(a), "at": to_dt(a.at), **ref_to_cols(a.ref, "ref"), "title": a.title, "action": a.action,
        "summary": a.summary, "related_kind": a.related.kind if a.related else None,
        "related_id": a.related.id if a.related else None,
        "changes": [c.model_dump(by_alias=True) for c in a.changes] if a.changes else None,
    }


def _activity_from_row(m: ActivityModel) -> Activity:
    return Activity(
        **base_fields(m), at=to_iso(m.at), ref=ref_from_cols(m, "ref"), title=m.title, action=m.action,
        summary=m.summary, related=ref_from_cols(m, "related"),
        changes=[FieldChange.model_validate(c) for c in m.changes] if m.changes else None,
    )


def _link_to_row(link: Link) -> dict[str, Any]:
    return {**base_row(link), **ref_to_cols(link.a, "a"), **ref_to_cols(link.b, "b"), "note": link.note}


def _link_from_row(m: LinkModel) -> Link:
    return Link(**base_fields(m), a=ref_from_cols(m, "a"), b=ref_from_cols(m, "b"), note=m.note)


def _dump_list(items):
    if items is None:
        return None
    return [i.model_dump(by_alias=True, exclude_none=True, mode="json") for i in items]


def _task_to_row(t: Task) -> dict[str, Any]:
    return {
        **base_row(t), "title": t.title, "notes": t.notes, "workspace": t.workspace, "priority": t.priority,
        "due_date": t.due_date, **slot_to_cols(t.slot), "estimate": t.estimate, "plan_id": t.plan_id,
        "step_order": t.order, **ref_to_cols(t.related, "related"), **outcome_to_cols(t.outcome),
    }


def _task_from_row(m: TaskModel) -> Task:
    return Task(
        **base_fields(m), title=m.title, notes=m.notes, workspace=m.workspace, priority=m.priority,
        due_date=m.due_date, slot=slot_from_cols(m), estimate=m.estimate, plan_id=m.plan_id, order=m.step_order,
        related=ref_from_cols(m, "related"), outcome=outcome_from_cols(m),
    )


def _follow_up_to_row(f: FollowUp) -> dict[str, Any]:
    return {
        **base_row(f), "title": f.title, "channel": f.channel, "workspace": f.workspace, "contact": f.contact,
        "due_date": f.due.date if f.due else None, "due_time": f.due.time if f.due else None,
        "duration": f.duration, **ref_to_cols(f.subject, "subject"), "trigger": f.trigger, "notes": f.notes,
        "attempts": _dump_list(f.attempts), "plan_id": f.plan_id, "step_order": f.order,
        **outcome_to_cols(f.outcome),
    }


def _follow_up_from_row(m: FollowUpModel) -> FollowUp:
    return FollowUp(
        **base_fields(m), title=m.title, channel=m.channel, workspace=m.workspace, contact=m.contact,
        due=DueDate(date=m.due_date, time=m.due_time) if m.due_date else None, duration=m.duration,
        subject=ref_from_cols(m, "subject"), trigger=m.trigger, notes=m.notes,
        attempts=[Attempt.model_validate(a) for a in m.attempts] if m.attempts else None,
        plan_id=m.plan_id, order=m.step_order, outcome=outcome_from_cols(m),
    )


def _meeting_to_row(x: Meeting) -> dict[str, Any]:
    return {
        **base_row(x), "kind": x.kind, "title": x.title, "workspace": x.workspace, **slot_to_cols(x.slot),
        "mode": x.mode, "location": x.location, "link": x.link, "counterpart": x.counterpart,
        "contact": x.contact, "attendees": x.attendees, "purpose": x.purpose, "agenda": _dump_list(x.agenda),
        "confirmations": x.confirmations, "prep_plan_id": x.prep_plan_id, "plan_id": x.plan_id,
        "step_order": x.order, **ref_to_cols(x.related, "related"), "minutes": x.minutes,
        **outcome_to_cols(x.outcome),
    }


def _meeting_from_row(m: MeetingModel) -> Meeting:
    return Meeting(
        **base_fields(m), kind=m.kind, title=m.title, workspace=m.workspace, slot=slot_from_cols(m), mode=m.mode,
        location=m.location, link=m.link, counterpart=m.counterpart, contact=m.contact, attendees=m.attendees,
        purpose=m.purpose, agenda=[AgendaItem.model_validate(a) for a in m.agenda], confirmations=m.confirmations,
        prep_plan_id=m.prep_plan_id, plan_id=m.plan_id, order=m.step_order, related=ref_from_cols(m, "related"),
        minutes=m.minutes, outcome=outcome_from_cols(m),
    )


def _plan_to_row(p: ActionPlan) -> dict[str, Any]:
    return {
        **base_row(p), "title": p.title, "objective": p.objective, "workspace": p.workspace, "status": p.status,
        "due_date": p.due_date, **ref_to_cols(p.origin, "origin"), "task_id": p.task_id, "doc": p.doc,
    }


def _plan_from_row(m: PlanModel) -> ActionPlan:
    return ActionPlan(
        **base_fields(m), title=m.title, objective=m.objective, workspace=m.workspace, status=m.status,
        due_date=m.due_date, origin=ref_from_cols(m, "origin"), task_id=m.task_id, doc=m.doc,
    )


def _goal_to_row(g: Goal) -> dict[str, Any]:
    return {
        **base_row(g), "title": g.title, "description": g.description, "workspace": g.workspace,
        "horizon": g.horizon, "start_date": g.start_date, "target_date": g.target_date, "status": g.status,
        "key_results": _dump_list(g.key_results), "check_ins": _dump_list(g.check_ins),
    }


def _goal_from_row(m: GoalModel) -> Goal:
    return Goal(
        **base_fields(m), title=m.title, description=m.description, workspace=m.workspace, horizon=m.horizon,
        start_date=m.start_date, target_date=m.target_date, status=m.status,
        key_results=[KeyResult.model_validate(k) for k in m.key_results],
        check_ins=[CheckIn.model_validate(c) for c in m.check_ins],
    )


PLAN = Mapper(PlanModel, _plan_to_row, _plan_from_row)
GOAL = Mapper(GoalModel, _goal_to_row, _goal_from_row)
def _case_to_row(c: LegalCase) -> dict[str, Any]:
    e = c.engagement
    return {
        **base_row(c), "reference": c.reference, "organization": c.organization, "type": c.type, "title": c.title,
        "client": c.client, "court_number": c.court_number, "court": c.court, "opposing_party": c.opposing_party,
        "practice_area": c.practice_area, "stage": c.stage, "status": c.status, "summary": c.summary,
        "filed_on": c.filed_on, "engagement_counsel_id": e.counsel_id if e else None,
        "engagement_engaged_on": e.engaged_on if e else None, "engagement_scope": e.scope if e else None,
        "engagement_pro_bono": e.pro_bono if e else None, "engagement_fee": e.fee if e else None,
        "engagement_notes": e.notes if e else None,
    }


def _case_from_row(m: LegalCaseModel) -> LegalCase:
    engagement = (
        Engagement(counsel_id=m.engagement_counsel_id, engaged_on=m.engagement_engaged_on, scope=m.engagement_scope,
                   pro_bono=bool(m.engagement_pro_bono), fee=m.engagement_fee, notes=m.engagement_notes)
        if m.engagement_counsel_id else None
    )
    return LegalCase(
        **base_fields(m), reference=m.reference, organization=m.organization, type=m.type, title=m.title,
        client=m.client, court_number=m.court_number, court=m.court, engagement=engagement,
        opposing_party=m.opposing_party, practice_area=m.practice_area, stage=m.stage, status=m.status,
        summary=m.summary, filed_on=m.filed_on,
    )


def _hearing_to_row(h: Hearing) -> dict[str, Any]:
    return {
        **base_row(h), "case_id": h.case_id, "date": h.date, "time": h.time, "duration": h.duration,
        "purpose": h.purpose, "forum": h.forum, "summary": h.summary, "bench": h.bench, "order_text": h.order,
        "submissions": h.submissions, "authorities": h.authorities, "evidence": h.evidence,
        "reflections": h.reflections, "actions": _dump_list(h.actions), "next_hearing_id": h.next_hearing_id,
        **outcome_to_cols(h.outcome),
    }


def _hearing_from_row(m: HearingModel) -> Hearing:
    return Hearing(
        **base_fields(m), case_id=m.case_id, date=m.date, time=m.time, duration=m.duration, purpose=m.purpose,
        forum=m.forum, summary=m.summary, bench=m.bench, order=m.order_text, submissions=m.submissions,
        authorities=m.authorities, evidence=m.evidence, reflections=m.reflections,
        actions=[HearingAction.model_validate(a) for a in m.actions] if m.actions else None,
        next_hearing_id=m.next_hearing_id, outcome=outcome_from_cols(m),
    )


CASE = Mapper(LegalCaseModel, _case_to_row, _case_from_row)
HEARING = Mapper(HearingModel, _hearing_to_row, _hearing_from_row)
TASK = Mapper(TaskModel, _task_to_row, _task_from_row)
FOLLOW_UP = Mapper(FollowUpModel, _follow_up_to_row, _follow_up_from_row)
MEETING = Mapper(MeetingModel, _meeting_to_row, _meeting_from_row)
ACTIVITY = Mapper(ActivityModel, _activity_to_row, _activity_from_row)
LINK = Mapper(LinkModel, _link_to_row, _link_from_row)
