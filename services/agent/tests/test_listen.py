import asyncio

from app.application.listeners import ListenerManager

from .conftest import headers
from .test_mail_filter import BASE, answer, config, make
from .test_triage import make_triage


async def until(check, tries=200):
    for _ in range(tries):
        if check():
            return True
        await asyncio.sleep(0.02)
    return False


async def test_it_runs_once_to_catch_up_then_once_for_each_burst_of_mail():
    runs = []
    gate = asyncio.Queue()

    async def feed(account, folder, token):
        yield "live"
        while True:
            yield await gate.get()

    async def trigger():
        runs.append(1)

    m = ListenerManager(feed, debounce=0.02)
    m.ensure("w", ["a1"], "INBOX", lambda: "tok", trigger)
    assert await until(lambda: len(runs) == 1)  # on connecting: whatever came while nobody was listening
    for _ in range(5):  # a burst of mail
        gate.put_nowait("changed")
    assert await until(lambda: len(runs) == 2)
    await asyncio.sleep(0.1)
    assert len(runs) == 2  # five messages, one run
    s = m.status("w")
    assert s["listening"] and s["state"] == "listening" and s["runs"] == 2 and s["lastEventAt"] and s["lastRunAt"]
    m.stop("w")
    assert m.status("w")["listening"] is False
    await m.close()


async def test_a_dropped_feed_is_reconnected_and_a_catch_up_run_follows(monkeypatch):
    import app.application.listeners as module

    attempts = []
    drop = asyncio.Event()

    async def feed(account, folder, token):
        attempts.append(token)
        yield "live"
        if len(attempts) == 1:
            await drop.wait()
            raise ConnectionError("the mail service restarted")
        await asyncio.Event().wait()

    runs = []

    async def trigger():
        runs.append(1)

    real_sleep = asyncio.sleep

    async def quick(seconds):
        await real_sleep(0.01 if seconds >= 1 else seconds)

    monkeypatch.setattr(module.asyncio, "sleep", quick)
    m = ListenerManager(feed, debounce=0.01)
    m.ensure("w", ["a1"], "INBOX", lambda: f"tok{len(attempts)}", trigger)
    assert await until(lambda: len(runs) == 1)
    drop.set()  # the feed drops: it reconnects, and runs again for whatever came meanwhile
    assert await until(lambda: len(attempts) == 2 and len(runs) == 2)
    assert m.status("w")["state"] == "listening" and m.status("w")["error"] is None
    await m.close()


async def test_listening_follows_the_agents_setup(rig):
    rig.app.state.listeners.debounce = 0.01
    rig.script += [answer("keep"), answer("spam")]
    wid = await make(rig, listen=True)
    assert await until(lambda: rig.events.opened == [("acc1", "INBOX")])
    assert (await rig.client.get(f"{BASE}/{wid}/listen", headers=headers())).json()["listening"] is True
    # the first catch-up ran before a model was chosen and said so; the next new mail starts it again, by itself
    rig.events.poke()
    assert await until(lambda: rig.data.trashed == [3])
    runs = []
    for _ in range(100):  # the move happens just before the run is written down
        runs = (await rig.client.get(f"{BASE}/{wid}/executions", headers=headers())).json()["items"]
        if runs:
            break
        await asyncio.sleep(0.02)
    assert len(runs) >= 1

    rig.events.poke()  # more new mail
    assert await until(lambda: rig.app.state.listeners.status(wid)["runs"] >= 3)

    off = config(listen=False)
    await rig.client.put(f"{BASE}/{wid}", json={"name": "Spam filter", "description": "", "config": off}, headers=headers())
    assert (await rig.client.get(f"{BASE}/{wid}/listen", headers=headers())).json()["listening"] is False


async def test_an_agent_that_goes_on_from_another_listens_to_that_ones_mailbox(rig):
    rig.app.state.listeners.debounce = 0.01
    cls = await make(rig)
    rig.script += [answer("keep"), answer("spam"), answer("fyi")]
    tri = await make_triage(rig, cls, listen=True)
    assert await until(lambda: ("acc1", "INBOX") in rig.events.opened)
    assert (await rig.client.get(f"{BASE}/{tri}/listen", headers=headers())).json()["mailboxes"] == 1
    await rig.client.delete(f"{BASE}/{tri}", headers=headers())
    assert rig.app.state.listeners.status(tri)["listening"] is False


async def test_only_agents_that_read_a_mailbox_can_listen_and_only_their_owner_sees_it(rig):
    manual = (await rig.client.post(BASE, json={"name": "m", "description": "", "config": {"source": {"kind": "manual"}, "listen": True, "prompt": "x", "labels": [{"name": "a", "description": "A"}, {"name": "b", "description": "B"}]}}, headers=headers())).json()["id"]
    assert (await rig.client.get(f"{BASE}/{manual}/listen", headers=headers())).json()["listening"] is False
    assert (await rig.client.get(f"{BASE}/{manual}/listen", headers=headers("b@example.com", "u2"))).status_code == 404
