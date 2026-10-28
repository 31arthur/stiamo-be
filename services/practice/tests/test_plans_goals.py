from app.domain.common import Outcome
from app.domain.goals import Goal, KeyResult, goal_progress, key_result_progress
from app.domain.plans import ActionPlan, StepPool, plan_progress, plan_scope, plan_steps
from app.domain.work import FollowUp, Meeting, Task


def task(id, plan="p", order=None, status="open", created="2026-01-01T00:00:00.000Z", **kw):
    return Task(id=id, user_id="u", created_at=created, updated_at=created, title=id, plan_id=plan, order=order,
                outcome=Outcome(status=status), **kw)


def test_steps_are_ordered_across_kinds_and_progress_counts_all_of_them():
    f = FollowUp(id="f1", user_id="u", created_at="2026-01-01T00:00:00.000Z", updated_at="", title="call", plan_id="p",
                 order=1, outcome=Outcome(status="partial"))
    m = Meeting(id="m1", user_id="u", created_at="2026-01-01T00:00:00.000Z", updated_at="", title="appt", plan_id="p",
                order=0, slot={"date": "2026-02-01", "start": 600, "duration": 30}, outcome=Outcome(status="done"))
    pool = StepPool(tasks=[task("t1", order=2), task("other", plan="elsewhere")], follow_ups=[f], meetings=[m])
    assert [s.id for s in plan_steps("p", pool)] == ["m1", "f1", "t1"]
    p = plan_progress("p", pool)
    assert (p.total, p.closed, p.done) == (3, 2, 1)
    assert p.progress == (1 + 0.5 + 0) / 3


def test_unordered_steps_follow_creation_time():
    pool = StepPool(tasks=[task("late", created="2026-01-02T00:00:00.000Z"), task("early", created="2026-01-01T00:00:00.000Z"),
                           task("first", order=0)])
    assert [s.id for s in plan_steps("p", pool)] == ["first", "early", "late"]


def test_plan_scope():
    def plan(**kw):
        return ActionPlan(id="p", user_id="u", created_at="", updated_at="", title="t", **kw)

    assert plan_scope(plan()) == "general"
    assert plan_scope(plan(case_ids=["c"])) == "case"
    assert plan_scope(plan(case_ids=["c"], hearing_ids=["h"])) == "hearing"
    assert plan_scope(plan(task_id="t")) == "task"


def goal(**kw):
    base = dict(id="g", user_id="u", created_at="", updated_at="", title="G", start_date="2026-01-01",
                target_date="2026-01-31")
    return Goal(**{**base, **kw})


def test_key_result_progress_is_clamped_and_handles_equal_bounds():
    kr = KeyResult(id="k", label="x", start=10, target=20, current=15)
    assert key_result_progress(kr) == 0.5
    assert key_result_progress(kr.model_copy(update={"current": 99})) == 1
    assert key_result_progress(kr.model_copy(update={"current": -5})) == 0
    assert key_result_progress(KeyResult(id="k", label="x", start=5, target=5, current=5)) == 1
    assert key_result_progress(KeyResult(id="k", label="x", start=20, target=10, current=15)) == 0.5  # decreasing target


def test_goal_health_compares_progress_with_time():
    krs = lambda cur: [KeyResult(id="k", label="x", start=0, target=100, current=cur)]  # noqa: E731
    pool = StepPool()
    assert goal_progress(goal(key_results=krs(50)), pool, "2026-01-16").health == "on_track"
    assert goal_progress(goal(key_results=krs(30)), pool, "2026-01-16").health == "at_risk"
    assert goal_progress(goal(key_results=krs(0)), pool, "2026-01-26").health == "off_track"
    assert goal_progress(goal(), pool, "2026-01-02").health == "not_started"
    assert goal_progress(goal(status="achieved"), pool, "2026-01-26").health == "achieved"
    p = goal_progress(goal(key_results=krs(50)), pool, "2026-01-16")
    assert p.days_left == 15 and round(p.elapsed, 2) == 0.5 and p.plans is None


def test_goal_blends_plans_and_key_results_equally():
    pool = StepPool(tasks=[task("a", status="done"), task("b", status="not_done")])  # plan p = 0.5
    g = goal(plan_ids=["p"], key_results=[KeyResult(id="k", label="x", start=0, target=10, current=10)])  # kr = 1
    assert goal_progress(g, pool, "2026-01-10").progress == 0.75
