"""Chittu: the owner's personal assistant. His name is Chidambaram, and he answers to Chittu. Pure: text and numbers only.

He is not a day planner any more. He keeps the whole of the owner's work: every task, call, meeting, hearing, event and mail that needs them is an action item
(see domain/actionitems.py), and he is the one who talks to the owner about them. His day has a shape the owner set: it opens with a good morning at 6 and
closes with a good night at 10 in the evening, and in between he brings things to the owner one at a time.
"""

from __future__ import annotations

from datetime import date

from app.domain.dayplan import DAY_END, DAY_START, clock

NAME = "Chidambaram"
NICKNAME = "Chittu"
OPENS = "06:00"
CLOSES = "22:00"
#: How many things Chittu brings in one sitting before he asks whether to go on.
SITTING = 10


def saved_name(planner: dict | None) -> str:
    """The name the owner set for what Chittu calls them (the planner settings' chittu.name), or "" when none was set."""
    return " ".join(str(((planner or {}).get("chittu") or {}).get("name") or "").split())[:40]


def saved_full_name(planner: dict | None) -> str:
    """The owner's full name, as they set it, or "" when none was set."""
    return " ".join(str(((planner or {}).get("chittu") or {}).get("fullName") or "").split())[:80]


def good_morning(owner: str, day: str, count: int, first: str | None = None, carried: int = 0) -> str:
    """The first message of the day. Short; it says how many things want the owner (and how many are still on the list from before), and asks whether to start."""
    when = date.fromisoformat(day).strftime("%A %-d %B")
    hello = f"☀️ Good morning, {owner}. It's {when}."
    if count <= 0:
        return f"{hello} Nothing needs you today. I'll keep an eye on things."
    many = "one thing" if count == 1 else f"{count} things"
    kept = f" ({carried} still on the list from before)" if carried else ""
    return f"{hello}\nThere {'is' if count == 1 else 'are'} {many} I need your attention on today{kept}" + (f", starting with {first}." if first else ".") + " Shall we start?"


def good_night(owner: str, done: int, moved: int, left: int, tomorrow_first: str | None = None, kept: int = 0) -> str:
    """The last message of the day: what got done, what moved, what is still open, and what tomorrow opens with."""
    parts = [f"🌙 Good night, {owner}."]
    bits = []
    if done:
        bits.append(f"{done} done")
    if moved:
        bits.append(f"{moved} moved")
    if left:
        bits.append(f"{left} still open")
    parts.append("Today: " + ", ".join(bits) + "." if bits else "A quiet day.")
    if kept:
        parts.append(f"Your list is still here with {kept} left: I'll pick it up where we stopped.")
    if tomorrow_first:
        parts.append(f"Tomorrow opens with {tomorrow_first}.")
    parts.append("I'll be here at six. Rest well.")
    return "\n".join(parts)


def day_shape() -> str:
    return f"{clock(DAY_START)}–{clock(DAY_END)}"


PERSONA_NAME_LINE = f"You are {NAME}, whom {{{{user}}}} calls {NICKNAME}: {{{{user}}}}'s personal assistant inside Stiamo."
