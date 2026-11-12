"""The codebooks: what each lens asks and what each of its labels means, with what it sounds like and what it is not. Pure data.

The lens coders see exactly this and nothing else besides the scene, which is what lets a small model label consistently. Change a
definition and bump CODEBOOK_VERSION, so test results are compared like with like. Labels are plain words a person reads the same way.
"""

from __future__ import annotations

CODEBOOK_VERSION = "1"

CODEBOOK: dict[str, dict] = {
    "talks": {
        "asks": "How does this person put things when they speak or write?",
        "built_on": "communication-style models (directness)",
        "labels": {
            "straight": {"means": "Says the thing plainly, good news or bad, without softening or delay.",
                         "sounds_like": ["He told me straight out the file was rejected.", "She said no, and why, in one line."],
                         "not_this": "Being rude or angry is not the same as being straight; label that under pressure, not here.",
                         "phrase": "says things plainly"},
            "diplomatic": {"means": "Softens or wraps the message: cushions bad news, hints rather than says.",
                           "sounds_like": ["He said there might be a small issue with the papers.", "She hinted they were rethinking the budget."],
                           "not_this": "Hiding the news altogether is evasive, not diplomatic.",
                           "phrase": "softens what they say"},
            "evasive": {"means": "Avoids giving a straight answer: changes the subject, stays vague, won't commit to facts.",
                        "sounds_like": ["Every time I asked for the date he talked about something else.", "She kept saying 'we'll see'."],
                        "not_this": "Not knowing the answer and saying so is straight, not evasive.",
                        "phrase": "stays vague when pressed"},
        },
    },
    "decides": {
        "asks": "What part does this person play in how the decision gets made?",
        "built_on": "stakeholder mapping and buying-centre roles",
        "labels": {
            "decider": {"means": "Can say the final yes or no themselves.", "sounds_like": ["The MD signed off himself.", "She said she'd approve it."],
                        "not_this": "Someone who promises to 'push it upstairs' is not the decider.", "phrase": "can say yes themselves"},
            "influencer": {"means": "Doesn't decide, but the one who decides listens to them.", "sounds_like": ["Her word carries weight with the board.", "He'll recommend it to the director."],
                           "not_this": "Just passing papers along is a gatekeeper, not an influencer.", "phrase": "has the decider's ear"},
            "gatekeeper": {"means": "Controls access or the flow of papers to the decider, without deciding.", "sounds_like": ["Everything goes through his desk first.", "The clerk holds the file until it's complete."],
                           "not_this": "", "phrase": "controls what reaches the decider"},
            "executor": {"means": "Carries out what was decided; has no say in the decision.", "sounds_like": ["He just processes what the bank approves.", "She'll book it once they say go."],
                         "not_this": "", "phrase": "carries out what others decide"},
            "champion": {"means": "Actively pushes for us inside their side.", "sounds_like": ["She argued our case in the meeting.", "He's pushing his boss to sign."],
                         "not_this": "Being friendly is not championing; label that under trust.", "phrase": "pushes for us on their side"},
            "blocker": {"means": "Actively works to stop or slow it.", "sounds_like": ["He keeps raising new objections.", "She told them not to sign yet."],
                        "not_this": "Being slow without intent is not blocking.", "phrase": "works against it moving"},
            "bystander": {"means": "Involved, but with no real effect on the outcome.", "sounds_like": ["He was copied on the mail but has no say.", "She just attends the meetings."],
                          "not_this": "", "phrase": "has little say"},
        },
    },
    "wants": {
        "asks": "What does this person want or care about most here (their interest, not what they say they want)?",
        "built_on": "interest-based negotiation: interests, not positions",
        "labels": {
            "money": {"means": "Getting or keeping money: a fee, a commission, a lower cost.", "sounds_like": ["He only moved when the commission came up.", "She keeps asking about the price."],
                      "not_this": "", "phrase": "is moved by money"},
            "speed": {"means": "Getting it done fast; time matters more than terms.", "sounds_like": ["He wants it closed this week.", "She'd accept less to finish sooner."],
                      "not_this": "", "phrase": "wants it done fast"},
            "certainty": {"means": "Safety and predictability: no surprises, no risk.", "sounds_like": ["He wanted it all in writing first.", "She won't move without a guarantee."],
                          "not_this": "", "phrase": "wants certainty"},
            "face": {"means": "Reputation and how they look to others; not being embarrassed.", "sounds_like": ["He didn't want his boss to hear about the delay.", "She cares how it looks to the client."],
                     "not_this": "", "phrase": "minds how it looks"},
            "relationship": {"means": "Keeping the relationship good, now and later.", "sounds_like": ["He said he values working with us.", "She did it as a favour to keep things good."],
                             "not_this": "", "phrase": "values the relationship"},
            "control": {"means": "Being in charge of how and when it happens.", "sounds_like": ["He insisted on doing it his way.", "She wants to approve every step."],
                        "not_this": "", "phrase": "wants to be in control"},
            "fairness": {"means": "Being treated fairly; a principle more than a gain.", "sounds_like": ["He said it was the principle of the thing.", "She objected that it wasn't fair to the workers."],
                         "not_this": "", "phrase": "cares about fairness"},
            "avoiding_blame": {"means": "Not being the one blamed if it goes wrong.", "sounds_like": ["He blamed the bank before anyone asked.", "She wants it on record it wasn't her call."],
                               "not_this": "", "phrase": "avoids the blame"},
        },
    },
    "under_pressure": {
        "asks": "What does this person do when the news is bad or things get tense?",
        "built_on": "conflict modes and stress responses",
        "labels": {
            "confronts": {"means": "Pushes back hard: escalates, argues, demands, raises their voice.", "sounds_like": ["He shouted that we'd wasted his time.", "She threatened to go to the director."],
                          "not_this": "Disagreeing calmly and proposing a trade is bargaining.", "phrase": "pushes back hard"},
            "bargains": {"means": "Looks for a trade: offers something to get something.", "sounds_like": ["He said he'd waive the fee if we moved the date.", "She offered half now, half later."],
                         "not_this": "", "phrase": "looks for a trade"},
            "solves": {"means": "Works the problem with you: looks for a fix, takes a step.", "sounds_like": ["He called the bank himself to sort it.", "She suggested another way to get the papers."],
                       "not_this": "", "phrase": "works the problem"},
            "gives_in": {"means": "Accepts the bad news or the other side's terms without much fight.", "sounds_like": ["He just said OK, do what you need.", "She agreed to the new terms straight away."],
                         "not_this": "", "phrase": "gives way"},
            "goes_quiet": {"means": "Withdraws: stops answering, postpones, waits for it to pass.", "sounds_like": ["He didn't pick up for a week after the bank said no.", "She said she'd look into it and went silent."],
                           "not_this": "Being away or travelling is a circumstance, not a reaction: not shown.", "phrase": "goes quiet under bad news"},
            "agrees_no_delivery": {"means": "Agrees to whatever is asked, then doesn't do it.", "sounds_like": ["He said yes, yes, tomorrow, and nothing came.", "She promised the papers twice and sent nothing."],
                                   "not_this": "A promise kept late is a track-record matter, not this.", "phrase": "agrees, then doesn't deliver"},
        },
    },
    "trust": {
        "asks": "Whose side does this person act on, as far as we're concerned?",
        "built_on": "the trust equation (credibility, reliability, closeness, self-interest)",
        "labels": {
            "ally": {"means": "Acts in our interest, even at some cost to themselves.", "sounds_like": ["He warned us before the hearing.", "She went out of her way to get us the file."],
                     "not_this": "", "phrase": "is on our side"},
            "neutral": {"means": "Neither for nor against; does their job.", "sounds_like": ["He processed it like any other file.", "She was polite and correct, nothing more."],
                        "not_this": "", "phrase": "is neutral"},
            "wary": {"means": "Careful or suspicious of us; holds back.", "sounds_like": ["He wanted everything checked twice before trusting us.", "She wouldn't share the details over the phone."],
                     "not_this": "", "phrase": "is wary of us"},
            "against": {"means": "Acts against our interest.", "sounds_like": ["He tipped off the other side.", "She is working to get the case dismissed."],
                        "not_this": "Being on the other side of a case is a part, not this: label what they do.", "phrase": "works against us"},
        },
    },
    "moved_by": {
        "asks": "What changed this person's mind, or got them to act?",
        "built_on": "the principles of influence",
        "labels": {
            "authority": {"means": "A rule, an order, an official or an expert.", "sounds_like": ["He moved once we showed him the court's order.", "She agreed when the bank manager said so."],
                          "not_this": "", "phrase": "moves for an authority"},
            "others_did_it": {"means": "Seeing that others like them did the same.", "sounds_like": ["He agreed when I said the other builders had.", "She signed after hearing the rest had."],
                              "not_this": "", "phrase": "follows what others do"},
            "favour_returned": {"means": "Returning a favour we did them.", "sounds_like": ["He sped it up because we'd helped him last year.", "She did it since we'd waited for her."],
                                "not_this": "", "phrase": "returns favours"},
            "own_word": {"means": "Being held to what they themselves said or promised.", "sounds_like": ["He did it once I reminded him he'd promised Friday.", "She acted when I quoted her own email."],
                         "not_this": "", "phrase": "keeps to their word when reminded"},
            "rapport": {"means": "Liking us, a personal connection.", "sounds_like": ["After we chatted about cricket he helped.", "She does it because she likes working with us."],
                        "not_this": "", "phrase": "moves for people they like"},
            "deadline": {"means": "A date, a last chance, something running out.", "sounds_like": ["He only acted when the offer was about to lapse.", "She moved when the hearing was two days off."],
                         "not_this": "", "phrase": "moves at a deadline"},
            "facts": {"means": "Evidence, numbers, a clear argument.", "sounds_like": ["He changed his mind when he saw the figures.", "She agreed after reading the report."],
                      "not_this": "", "phrase": "moves for facts"},
            "unmoved": {"means": "Nothing tried has moved them.", "sounds_like": ["We tried everything and he wouldn't budge.", "She said no to every option."],
                        "not_this": "", "phrase": "is hard to move"},
        },
    },
}

LENSES: tuple[str, ...] = tuple(CODEBOOK)
#: The reactions "how will they take it?" chooses from: how people act under pressure, and taking it well.
REACTIONS: tuple[str, ...] = (*CODEBOOK["under_pressure"]["labels"], "takes_it_well")
REACTION_WORDS = {**{k: v["phrase"] for k, v in CODEBOOK["under_pressure"]["labels"].items()}, "takes_it_well": "takes it well"}


def label_names(lens: str) -> tuple[str, ...]:
    return tuple(CODEBOOK[lens]["labels"]) if lens in CODEBOOK else ("a", "b", "middle")


def phrase(lens: str, label: str) -> str:
    return CODEBOOK.get(lens, {}).get("labels", {}).get(label, {}).get("phrase", label.replace("_", " "))


def codebook_text(lens: str) -> str:
    """The codebook for one lens, as the coder reads it."""
    book = CODEBOOK[lens]
    lines = [f"Lens: {lens}. {book['asks']}", ""]
    for name, d in book["labels"].items():
        lines.append(f"- {name}: {d['means']}")
        lines.append("  Sounds like: " + " / ".join(f"“{x}”" for x in d["sounds_like"]))
        if d.get("not_this"):
            lines.append(f"  Not this: {d['not_this']}")
    lines.append("- not_shown: the scene says nothing about this lens. Most scenes say nothing about most lenses: this is often the right answer.")
    return "\n".join(lines)


# --- the approach playbook: from what is known to how to break news ---------------------------------------------------------

#: Moves for each expected reaction. Code picks them; a model only words them.
PLAYBOOK: dict[str, list[str]] = {
    "confronts": ["Tell them in private, not in front of others", "Lead with what was decided and by whom, not with your view", "Leave them a way to save face"],
    "bargains": ["Know in advance what you can give and what you can't", "Name the next step and what each side does"],
    "solves": ["Bring the problem with the facts and ask for their idea", "Agree one next step each"],
    "gives_in": ["Check they really agree: ask what they'll do next", "Don't take more than is fair: it comes back later"],
    "goes_quiet": ["Send the one-line fact first, then call at their best hour", "Ask for one small, dated next step", "Follow up on that date without waiting to hear"],
    "agrees_no_delivery": ["Get their next step in writing, with a date", "Ask what could stop them doing it", "Check in a day before the date"],
    "takes_it_well": ["Tell them plainly and early", "Thank them and say what happens next"],
}
#: Extra moves by what they want and what moves them.
WANT_MOVES = {
    "money": "Say what it means for their money", "speed": "Say how soon it can be settled", "certainty": "Put what you can guarantee in writing",
    "face": "Say how it will look, and keep it between you", "relationship": "Say you want to keep working together",
    "control": "Let them choose between two options", "fairness": "Explain why it is fair to everyone", "avoiding_blame": "Make clear it isn't their fault",
}
LEVER_MOVES = {
    "authority": "Point to the rule, order or official behind it", "others_did_it": "Mention that others in their place did the same",
    "favour_returned": "Remind them of how you've helped", "own_word": "Quote what they said or promised", "rapport": "Keep it personal and warm",
    "deadline": "Give a clear date", "facts": "Bring the figures", "unmoved": "Don't argue: say the decision and the next step",
}


def moves_for(reaction: str, wants: str | None, lever: str | None) -> list[str]:
    moves = list(PLAYBOOK.get(reaction, PLAYBOOK["takes_it_well"]))
    if wants and wants in WANT_MOVES:
        moves.append(WANT_MOVES[wants])
    if lever and lever in LEVER_MOVES:
        moves.append(LEVER_MOVES[lever])
    return moves[:5]
