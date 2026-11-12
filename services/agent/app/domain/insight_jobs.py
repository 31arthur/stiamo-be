"""The small AI jobs of gauging a person. Each reads one thing and answers one question, so it works on a cheap model:

- lens_router: which of the six lenses a scene says anything about (often none).
- lens_<lens> (six coders): one scene, one lens, one label from that lens's codebook, or not_shown, quoting the words relied on.
- reaction: how a person will most likely take some news, as one of a fixed set (asked three times; agreement is the confidence).
- wording: two or three sentences that follow moves code chose from the playbook.
- people_in_text: the people named in a news item or a mail, with only numbers and addresses that are really in the text.
- construct: the person's own dimension from comparing two people, in their own words.

Code adds the labels up (practice service, domain/insight.py). The model never says what someone is like.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.aijobs import Checked, Job, Problem, find_quote, items, one_of, short
from app.domain.lenses import CODEBOOK, LENSES, REACTION_WORDS, REACTIONS, codebook_text, label_names
from app.domain.story_jobs import same_name

# --- which lenses ----------------------------------------------------------------------------------------------------

ROUTER_SYSTEM = """You read one short scene about a person and say which lenses it gives evidence on. Most scenes speak to one or two lenses, and many to none.

The lenses:
""" + "\n".join(f"- {k}: {v['asks']}" for k, v in CODEBOOK.items()) + """

Only list a lens when the scene shows something the person did or said that answers its question. When in doubt, leave it out.

Reply with one JSON object and nothing else: {"lenses": ["...", ...]}"""


def scene_text(i: dict[str, Any]) -> str:
    return " ".join(x for x in (i.get("scene"), i.get("quote")) if x)


def _render_scene(i: dict[str, Any]) -> str:
    return f"Person: {i['name']}\nStory: {i.get('story') or '(none)'}\nThe scene:\n<<<\n{scene_text(i)}\n>>>"


def _check_router(out: dict[str, Any], i: dict[str, Any]) -> Checked:
    raw = out.get("lenses")
    if not isinstance(raw, list):
        raise Problem('Give "lenses" as a list (it can be empty).')
    kept = [x for x in raw if x in LENSES]
    return Checked({"lenses": list(dict.fromkeys(kept))[:4]}, [f"“{x}” isn't a lens" for x in raw if x not in LENSES])


LENS_ROUTER = Job(
    name="lens_router",
    version="1",
    purpose="Says which lenses a scene about a person gives evidence on.",
    system=ROUTER_SYSTEM,
    examples=(
        ({"name": "The agent", "story": "Home loan", "scene": "called late again with more paperwork, apologising", "quote": "The agent called at seven, sorry as always"},
         {"lenses": ["talks", "under_pressure"]}),
        ({"name": "The MD", "story": "Gulbarga SMPL", "scene": "agreed to settle once we showed him the High Court's order", "quote": "He agreed the moment he saw the order"},
         {"lenses": ["moved_by", "decides"]}),
        ({"name": "Priya", "story": "Client website", "scene": "sent the logo", "quote": "Priya sent the logo"}, {"lenses": []}),
    ),
    render=_render_scene,
    check=_check_router,
    fallback=lambda i: {"lenses": []},
)


# --- one lens --------------------------------------------------------------------------------------------------------


def _coder_system(lens: str) -> str:
    return f"""You label one scene about a person on one lens, using the codebook below and nothing else.

{codebook_text(lens)}

Rules:
1. First copy "quote": the exact words from the scene you rely on, character for character. Then give "label".
2. Choose "not_shown" when the scene doesn't show this lens. Never guess from the person's job or the situation alone.
3. "strength": "clear" when the behaviour is described, "weak" when it is only implied.

Reply with one JSON object and nothing else: {{"quote": "...", "label": "...", "strength": "clear"}}"""


def _check_coder(lens: str):
    names = label_names(lens)

    def check(out: dict[str, Any], i: dict[str, Any]) -> Checked:
        label = one_of(out.get("label"), (*names, "not_shown"))
        if label is None:
            raise Problem(f"The label must be one of: {', '.join(names)}, or not_shown.")
        if label == "not_shown":
            return Checked({"label": None, "quote": None, "strength": None})
        q = find_quote(out.get("quote"), scene_text(i), min_chars=3)
        if q is None:
            raise Problem("Your quote isn't in the scene word for word. Copy the exact words, or answer not_shown.")
        return Checked({"label": label, "quote": q, "strength": one_of(out.get("strength"), ("clear", "weak"), "clear")})

    return check


_CODER_EXAMPLES: dict[str, tuple] = {
    "talks": (({"name": "The agent", "scene": "kept changing the subject when asked for the sanction date", "quote": "Every time I asked for the date he talked about something else"},
               {"quote": "Every time I asked for the date he talked about something else", "label": "evasive", "strength": "clear"}),),
    "decides": (({"name": "The clerk", "scene": "won't send the file to the judge until it's complete", "quote": "The clerk holds the file until it's complete"},
                 {"quote": "holds the file until it's complete", "label": "gatekeeper", "strength": "clear"}),),
    "wants": (({"name": "The builder", "scene": "only moved after hearing his payment was due", "quote": "He only moved when I said the next payment was due"},
               {"quote": "He only moved when I said the next payment was due", "label": "money", "strength": "weak"}),),
    "under_pressure": (({"name": "The agent", "scene": "didn't pick up after the bank said no", "quote": "He didn't pick up for a week after the bank said no"},
                        {"quote": "He didn't pick up for a week after the bank said no", "label": "goes_quiet", "strength": "clear"}),
                       ({"name": "HR", "scene": "away on leave", "quote": "HR was on leave all week"}, {"quote": "", "label": "not_shown", "strength": "clear"})),
    "trust": (({"name": "Counsel", "scene": "warned us before the hearing", "quote": "She warned us the bench was in a bad mood"},
               {"quote": "She warned us the bench was in a bad mood", "label": "ally", "strength": "clear"}),),
    "moved_by": (({"name": "The MD", "scene": "agreed once he saw the order", "quote": "He agreed the moment he saw the High Court's order"},
                  {"quote": "He agreed the moment he saw the High Court's order", "label": "authority", "strength": "clear"}),),
}


def lens_coder(lens: str) -> Job:
    return Job(
        name=f"lens_{lens}", version="1", purpose=f"Labels one scene on the “{lens}” lens: {CODEBOOK[lens]['asks']}",
        system=_coder_system(lens), examples=_CODER_EXAMPLES[lens], render=_render_scene, check=_check_coder(lens),
        fallback=lambda i: {"label": None, "quote": None, "strength": None},
    )


LENS_CODERS: dict[str, Job] = {lens: lens_coder(lens) for lens in LENSES}


# --- how will they take it -----------------------------------------------------------------------------------------

REACTION_SYSTEM = """You predict how one person will most likely react to some news, from what is recorded about them. Choose one reaction:

""" + "\n".join(f"- {k}: {REACTION_WORDS[k]}" + (f". {CODEBOOK['under_pressure']['labels'][k]['means']}" if k in CODEBOOK["under_pressure"]["labels"] else ". Accepts it calmly and carries on.")
                for k in REACTIONS) + """

Base it on the profile and the scenes given, not on what people in their role usually do. If the record shows nothing about how they take bad news, choose from what they want and how they talk.

Reply with one JSON object and nothing else: {"reaction": "..."}"""


def _render_reaction(i: dict[str, Any]) -> str:
    scenes = "\n".join(f"- {s}" for s in i.get("scenes") or []) or "(none)"
    return f"Person: {i['name']}\nWhat is recorded about them:\n{i.get('profile') or '(little yet)'}\nScenes:\n{scenes}\n\nThe news:\n<<<\n{i['news']}\n>>>"


def _check_reaction(out: dict[str, Any], i: dict[str, Any]) -> Checked:
    r = one_of(out.get("reaction"), REACTIONS)
    if r is None:
        raise Problem(f"The reaction must be one of: {', '.join(REACTIONS)}.")
    return Checked({"reaction": r})


REACTION = Job(
    name="reaction", version="1", purpose="Predicts how someone will most likely take some news, from what's recorded about them.",
    system=REACTION_SYSTEM,
    examples=(({"name": "The agent", "profile": "Under pressure: goes quiet under bad news (high, 4 scenes). Wants: money.",
                "scenes": ["didn't pick up for a week after the bank said no"], "news": "The bank wants one more document."}, {"reaction": "goes_quiet"}),),
    render=_render_reaction, check=_check_reaction, fallback=lambda i: {"reaction": None},
)


# --- the words to say it --------------------------------------------------------------------------------------------

WORDING_SYSTEM = """You write the message a person will send (or say) to break some news to someone. Follow the moves given, in order. Two or three short sentences, plain and warm, in the person's own voice (first person). No greeting line, no sign-off. Use only facts in the news: never add a date, an amount, a name or a promise that isn't there.

Reply with one JSON object and nothing else: {"message": "..."}"""


def _render_wording(i: dict[str, Any]) -> str:
    moves = "\n".join(f"- {m}" for m in i["moves"])
    return f"To: {i['name']}\nBy: {i.get('channel') or 'call'}\nThe moves:\n{moves}\n\nThe news:\n<<<\n{i['news']}\n>>>"


def _check_wording(out: dict[str, Any], i: dict[str, Any]) -> Checked:
    msg = short(out.get("message"), 600)
    if not msg:
        raise Problem("Give the message.")
    allowed = set(re.findall(r"\d+", i["news"]))
    invented = [n for n in re.findall(r"\d+", msg) if n not in allowed]
    if invented:
        raise Problem(f"The message has numbers that aren't in the news ({', '.join(invented)}). Use only the facts given.")
    return Checked({"message": msg})


WORDING = Job(
    name="wording", version="1", purpose="Words a message to break news, following the moves the playbook chose.",
    system=WORDING_SYSTEM,
    examples=(({"name": "the agent", "channel": "message", "news": "The bank wants one more document, the 2023 ITR.",
                "moves": ["Send the one-line fact first, then call at their best hour", "Ask for one small, dated next step"]},
               {"message": "The bank wants one more document: the 2023 ITR. Could you tell me by when you can get it to them? I'll call you this evening."}),),
    render=_render_wording, check=_check_wording, fallback=lambda i: {"message": None},
)


# --- people in a news item or a mail --------------------------------------------------------------------------------

PEOPLE_SYSTEM = """You list the people named in a text (a news article or an email) with what the text says about them: their title, their organisation, and any phone numbers and email addresses given for them.

Rules:
1. Only people named in the text, written as the text writes them. Not the publication, not "the court", not organisations.
2. Copy phone numbers and email addresses exactly as they appear. If none is given for a person, leave the list empty. Never make one up.
3. "quote": the words from the text that name them, copied exactly.

Reply with one JSON object and nothing else: {"people": [{"name": "...", "title": null, "organisation": null, "phones": [], "emails": [], "quote": "..."}]}"""


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _check_people(out: dict[str, Any], i: dict[str, Any]) -> Checked:
    text = i["text"]
    digits = _digits(text)
    dropped: list[str] = []
    people = []
    for p in items(out.get("people"), 10):
        name = short(p.get("name"), 120)
        if not name or find_quote(name, text, min_chars=3) is None:
            dropped.append(f"“{name}” isn't named in the text")
            continue
        phones = [x for x in (p.get("phones") or []) if isinstance(x, str) and len(_digits(x)) >= 6 and _digits(x) in digits]
        emails = [x for x in (p.get("emails") or []) if isinstance(x, str) and x.lower() in text.lower()]
        dropped += [f"a number for {name} that isn't in the text" for x in (p.get("phones") or []) if x not in phones]
        if not any(same_name(name, q["name"]) for q in people):
            people.append({"name": name, "title": short(p.get("title"), 120), "organisation": short(p.get("organisation"), 160), "phones": phones, "emails": emails})
    return Checked({"people": people}, dropped)


PEOPLE_IN_TEXT = Job(
    name="people_in_text", version="1", purpose="Finds the people named in a news item or a mail, with only the numbers really in it.",
    system=PEOPLE_SYSTEM,
    examples=(({"text": "Kalaburagi: Assistant Labour Commissioner R. Nayak said the inspection would resume on Monday. Contact: 08472-263401."},
               {"people": [{"name": "R. Nayak", "title": "Assistant Labour Commissioner", "organisation": None, "phones": ["08472-263401"], "emails": [], "quote": "Assistant Labour Commissioner R. Nayak"}]}),),
    render=lambda i: f"The text:\n<<<\n{i['text'][:6000]}\n>>>", check=_check_people, fallback=lambda i: {"people": []},
)


# --- comparing two people --------------------------------------------------------------------------------------------

CONSTRUCT_SYSTEM = """A person compared two people they know. From their answer, take the one way the two differ, as two opposite ends in the person's own words, and say where each of the two sits.

Rules:
1. "pole_a" and "pole_b" are short (1 to 4 words) and use the person's own words from the answer.
2. "name": a short name for this way of seeing people (2 to 4 words).
3. Each person sits at "a", "b" or "middle".

Reply with one JSON object and nothing else: {"name": "...", "pole_a": "...", "pole_b": "...", "first_sits": "a", "second_sits": "b"}"""


def _check_construct(out: dict[str, Any], i: dict[str, Any]) -> Checked:
    a, b = short(out.get("pole_a"), 60), short(out.get("pole_b"), 60)
    if not a or not b:
        raise Problem("Give both ends, pole_a and pole_b.")
    words = set(re.findall(r"[a-z]+", i["answer"].lower()))
    if not (set(re.findall(r"[a-z]+", a.lower())) & words) or not (set(re.findall(r"[a-z]+", b.lower())) & words):
        raise Problem("Use the person's own words from the answer for both ends.")
    return Checked({"name": short(out.get("name"), 60) or f"{a} or {b}", "pole_a": a, "pole_b": b,
                    "first_sits": one_of(out.get("first_sits"), ("a", "b", "middle"), "middle"),
                    "second_sits": one_of(out.get("second_sits"), ("a", "b", "middle"), "middle")})


CONSTRUCT = Job(
    name="construct", version="1", purpose="Turns your comparison of two people into a lens of your own.",
    system=CONSTRUCT_SYSTEM,
    examples=(({"first": "the loan agent", "second": "my counsel", "answer": "The agent promises everything and delivers late; she promises little and always delivers early."},
               {"name": "Promises", "pole_a": "promises everything", "pole_b": "promises little", "first_sits": "a", "second_sits": "b"}),),
    render=lambda i: f"First: {i['first']}\nSecond: {i['second']}\nTheir answer:\n<<<\n{i['answer']}\n>>>",
    check=_check_construct, fallback=lambda i: {},
)

INSIGHT_JOBS: dict[str, Job] = {j.name: j for j in (LENS_ROUTER, *LENS_CODERS.values(), REACTION, WORDING, PEOPLE_IN_TEXT, CONSTRUCT)}
