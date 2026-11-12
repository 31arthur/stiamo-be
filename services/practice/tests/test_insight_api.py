from datetime import date, timedelta

from app.domain.common import Outcome
from app.domain.insight import Evidence, confidence, lens_view, profile_of, track_record, weight
from app.domain.work import Attempt, FollowUp

from tests.test_stories_api import story

TODAY = "2026-11-12"


def ev(label, lens="under_pressure", days=0, **kw):
    d = (date.fromisoformat(TODAY) - timedelta(days=days)).isoformat()
    return Evidence(id=f"e{label}{days}", user_id="u", created_at="x", updated_at="x", character_id="c", lens=lens, label=label, date=d, **kw)


def test_evidence_counts_by_what_it_is_and_fades_with_time():
    assert weight(ev("goes_quiet"), TODAY) == 1.0
    assert weight(ev("goes_quiet", strength="weak"), TODAY) == 0.5
    assert weight(ev("goes_quiet", source="answer", method="quick_tap", specific=True), TODAY) == 0.9
    assert weight(ev("goes_quiet", source="answer", method="sheet"), TODAY) == 0.6
    assert weight(ev("goes_quiet", source="answer", method="check"), TODAY) == 1.0
    assert weight(ev("goes_quiet", source="prediction", method="what_would"), TODAY) == 0.0
    assert abs(weight(ev("goes_quiet", days=182), TODAY) - 0.5) < 0.01  # half a year: half as much


def test_confidence_and_conflicts_are_arithmetic():
    assert confidence(3, 0.7) == "high" and confidence(1.5, 0.55) == "medium" and confidence(1.4, 0.9) == "low"
    four = [ev("goes_quiet", days=i) for i in range(4)]
    v = lens_view(four, TODAY)
    assert v["label"] == "goes_quiet" and v["confidence"] == "high" and v["evidence"] == 4 and "conflict" not in v
    mixed = lens_view([*four[:2], ev("confronts", days=1), ev("confronts", days=2), ev("solves", strength="weak")], TODAY)
    assert set(mixed["conflict"]) == {"goes_quiet", "confronts"} and mixed["confidence"] == "low"  # shown, not averaged away
    wants = lens_view([ev("money", "wants"), ev("money", "wants", days=1), ev("face", "wants")], TODAY, multi=True)
    assert wants["label"] == "money" and wants["also"] == "face"


def test_the_track_record_is_counted_from_the_call_log():
    def call(fid, attempts, done=None):
        return FollowUp(id=fid, user_id="u", created_at="x", updated_at="x", title="Call", channel="call", character_id="c",
                        attempts=attempts, outcome=Outcome(status="done", recorded_at=f"{done}T10:00:00.000Z") if done else Outcome())
    calls = [
        call("f1", [Attempt(date="2026-11-01", minute=600, result="no_answer"), Attempt(date="2026-11-01", minute=1110, result="reached", promised_for="2026-11-05"),
                    Attempt(date="2026-11-08", minute=1120, result="reached")]),
        call("f2", [Attempt(date="2026-11-03", minute=1100, result="reached", channel="message", promised_for="2026-11-04")], done="2026-11-04"),
    ]
    tr = track_record(calls)
    assert tr["tries"] == 4 and tr["answerRate"] == {"call": 0.67, "message": 1.0}
    assert tr["bestHours"] == ["18:00–19:00"]
    assert tr["promises"] == {"kept": 1, "of": 2, "avgDaysLate": 3.0}
    assert track_record([]) is None


def test_a_profile_is_labels_and_counts_never_quotes():
    p = profile_of([ev("goes_quiet", quote="He went silent for a week"), ev("goes_quiet", days=3),
                    ev("money", "wants", source="answer", method="sheet"),
                    ev("goes_quiet", source="prediction", method="what_would", predicted_by="you", verdict="right"),
                    ev("confronts", source="prediction", method="what_would", predicted_by="model", verdict="wrong")], [], [], TODAY)
    assert p["lenses"]["under_pressure"]["label"] == "goes_quiet" and p["lenses"]["wants"]["label"] == "money"
    assert p["reads"] == {"you": {"right": 1, "of": 1}, "model": {"right": 0, "of": 1}}
    assert "He went silent" not in str(p)


async def test_evidence_takes_its_storys_workspace_and_refreshes_the_profile(client, alice):
    await story(client, alice, workspace="sb")
    await client.post("/v1/characters/join", json={"id": "rk", "name": "Ravi", "storyId": "s1"}, headers=alice)
    r = await client.put("/v1/evidence/e1", headers=alice, json={
        "characterId": "rk", "storyId": "s1", "workspace": "personal", "lens": "under_pressure", "label": "Goes Quiet", "date": TODAY,
        "quote": "He didn't pick up for a week"})
    assert r.status_code == 200, r.text
    assert r.json()["workspace"] == "sb" and r.json()["label"] == "goes_quiet"
    c = (await client.get("/v1/characters/rk", headers=alice)).json()
    assert c["profile"]["lenses"]["under_pressure"]["label"] == "goes_quiet"
    assert "pick up" not in str(c["profile"])
    general = (await client.put("/v1/evidence/e2", json={"characterId": "rk", "lens": "wants", "label": "money", "date": TODAY,
                                                          "source": "answer", "method": "sheet"}, headers=alice)).json()
    assert general["workspace"] == "sb"  # no story: the person's home (first met in an S&B story)
    for bad in ({"lens": "astrology", "label": "leo"}, {"lens": "wants", "label": "lots of money!"}):
        assert (await client.put("/v1/evidence/e3", json={"characterId": "rk", "date": TODAY, **bad}, headers=alice)).status_code == 422
    await client.delete("/v1/evidence/e1", headers=alice)
    assert "under_pressure" not in (await client.get("/v1/characters/rk", headers=alice)).json()["profile"]["lenses"]


async def test_predictions_are_settled_and_reads_are_kept(client, alice):
    await client.put("/v1/characters/rk", json={"name": "Ravi"}, headers=alice)
    p = (await client.put("/v1/evidence/p1", json={"characterId": "rk", "lens": "under_pressure", "label": "goes_quiet", "date": TODAY,
                                                     "source": "prediction", "method": "what_would", "predictedBy": "you", "about": "the loan is late"}, headers=alice)).json()
    assert p["verdict"] == "pending"
    r = await client.post("/v1/evidence/p1/settle", json={"verdict": "right"}, headers=alice)
    assert r.status_code == 200 and r.json()["verdict"] == "right"
    assert (await client.get("/v1/characters/rk/profile", headers=alice)).json()["reads"] == {"you": {"right": 1, "of": 1}}
    await client.put("/v1/evidence/x1", json={"characterId": "rk", "lens": "wants", "label": "money", "date": TODAY}, headers=alice)
    assert (await client.post("/v1/evidence/x1/settle", json={"verdict": "right"}, headers=alice)).status_code == 422


async def test_custom_lenses_and_merges_carry_the_evidence(client, alice):
    await client.put("/v1/characters/a", json={"name": "A"}, headers=alice)
    await client.put("/v1/characters/b", json={"name": "B"}, headers=alice)
    lens = (await client.put("/v1/lenses/l1", json={"name": "Promises", "poleA": "overpromises", "poleB": "underpromises"}, headers=alice)).json()
    await client.put("/v1/evidence/c1", json={"characterId": "b", "lens": f"custom:{lens['id']}", "label": "a", "date": TODAY, "source": "answer", "method": "compare"}, headers=alice)
    await client.post("/v1/characters/b/merge", json={"into": "a"}, headers=alice)
    prof = (await client.get("/v1/characters/a/profile", headers=alice)).json()
    assert prof["custom"][0]["name"] == "Promises" and prof["custom"][0]["label"] == "a"
    await client.delete("/v1/lenses/l1", headers=alice)
    assert "custom" not in (await client.get("/v1/characters/a/profile", headers=alice)).json()
