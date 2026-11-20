"""Split a sound into layers: a small rule table, no model.

A sound made of two or three things at once ("an arrow whooshes past and thuds into a
target") tends to come out of the engine as a blur: the parts land on top of each other, or
several times over. Made one part at a time and stacked, each part is clean.

`split` turns one description into two or three, each naming **one thing happening**, with
a start time that puts them in order. The parts follow how a sound effect is usually built:

- **lead-in**: what comes before the hit (a whoosh, a swing, a charge-up)
- **hit**: the moment itself, one strike, dry
- **tail**: what is left afterwards (a ring, an echo, bits falling)

It is a table of rules, read top to bottom, first match wins. It is honest about what it
does not know: **with no rule for a description, it comes back as one layer, unchanged.**
Every description and start time it gives is a starting point to edit, not a verdict.
"""

from __future__ import annotations

import re
from typing import Any

ROLES = {"lead-in": "Lead-in", "hit": "Hit", "tail": "Tail", "whole": "Whole sound"}

# What a thing is made of, if the description says: the word found -> the word used.
MATERIALS = (
    (r"wood(en)?|timber|oak|plank|tree|log", "wood"),
    (r"metal|steel|iron|armou?r|shield|plate", "metal"),
    (r"stone|rock|brick|concrete|wall", "stone"),
    (r"flesh|body|meat|skin", "a body"),
    (r"glass|window", "glass"),
    (r"dirt|earth|ground|mud|sand|grass", "soft ground"),
)
BIG = r"giant|huge|massive|heavy|enormous|big|large|troll|ogre|dragon|monster"
ROOMS = (
    (r"cave|cavern", "a cave"), (r"hall|cathedral|church", "a big hall"),
    (r"tunnel|sewer|corridor", "a tunnel"), (r"canyon|valley|mountain", "a canyon"),
)


def _material(text: str, default: str) -> str:
    return next((name for pattern, name in MATERIALS if re.search(rf"\b({pattern})\b", text)), default)


def _room(text: str, default: str = "a big empty room") -> str:
    return next((name for pattern, name in ROOMS if re.search(rf"\b({pattern})\b", text)), default)


def _arrow(text: str) -> list[tuple[str, str, int]]:
    thing = re.search(r"\b(arrow|bolt|spear|dart|javelin|knife|dagger)\b", text)[1]
    return [("lead-in", f"{thing} whoosh past, fast air, dry", 0),
            ("hit", f"{thing} thudding into {_material(text, 'wood')}, one hit, dry", 250)]


def _door(text: str) -> list[tuple[str, str, int]]:
    made_of = {"wood": "wooden", "metal": "metal", "stone": "stone"}.get(_material(text, "wood"), "wooden")
    return [("hit", f"heavy {made_of} door slam, one hit", 0),
            ("tail", "metal latch click", 180)]


def _footstep(text: str) -> list[tuple[str, str, int]]:
    size = "giant " if re.search(rf"\b({BIG})\b", text) else ""
    ground = _material(text, "stone")
    return [("hit", f"{size}footstep on {ground}, one heavy step, dry", 0),
            ("tail", f"low echo dying away in {_room(text)}", 120)]


def _blade(text: str) -> list[tuple[str, str, int]]:
    thing = re.search(r"\b(sword|blade|axe|hammer|mace|club)\b", text)[1]
    return [("lead-in", f"{thing} swing whoosh, fast air", 0),
            ("hit", f"{thing} striking {_material(text, 'metal')}, one hit, dry", 200),
            ("tail", "metal ring dying away", 220)]


def _explosion(text: str) -> list[tuple[str, str, int]]:
    return [("hit", "deep explosion boom, one hit", 0),
            ("tail", "low rumble dying away", 100),
            ("tail", "debris and rubble falling, scattered", 350)]


def _glass(text: str) -> list[tuple[str, str, int]]:
    return [("hit", "glass smash, one hit, dry", 0),
            ("tail", "glass shards scattering on a hard floor", 150)]


def _gun(text: str) -> list[tuple[str, str, int]]:
    return [("hit", "gunshot crack, one shot, dry", 0),
            ("tail", "gunshot echo rolling away outdoors", 80)]


def _punch(text: str) -> list[tuple[str, str, int]]:
    thing = "kick" if re.search(r"\bkick", text) else "punch"
    return [("lead-in", "fast cloth whoosh, one swing", 0),
            ("hit", f"{thing} landing on a body, one dull hit, dry", 150)]


def _spell(text: str) -> list[tuple[str, str, int]]:
    return [("lead-in", "magic charge-up shimmer, rising", 0),
            ("hit", "magic burst, one hit", 600),
            ("tail", "sparkles fading away", 700)]


def _splash(text: str) -> list[tuple[str, str, int]]:
    return [("hit", "heavy object plunging into water, one splash", 0),
            ("tail", "water droplets and ripples settling", 300)]


def _echo(text: str) -> list[tuple[str, str, int]]:
    dry = re.split(r"\b(?:with|and|,)?\s*(?:an?\s+|a\s+long\s+)?(?:echo(?:es|ing)?|reverb|echoing)\b", text)[0].strip(" ,.")
    dry = re.sub(r"\s+in\s+(?:a|an|the)\s+[\w ]+$", "", dry) or text
    return [("hit", f"{dry}, dry", 0), ("tail", f"echo dying away in {_room(text)}", 120)]


def _then(text: str) -> list[tuple[str, str, int]]:
    parts = [part.strip(" ,.") for part in re.split(r",?\s*\b(?:and then|then|followed by)\b\s*", text)]
    parts = [part for part in parts if part][:3]
    return [("hit" if i else "lead-in", part, i * 400) for i, part in enumerate(parts)]


# id, what must be in the description, how it is split. First match wins.
RULES = (
    ("thrown-hit", r"\b(arrow|bolt|spear|dart|javelin|knife|dagger)\b.*\b(hit|hits|hitting|thud|thuds|strik\w+|into|target|land\w*|stick\w*)\b", _arrow),
    ("door", r"\bdoor\b.*\b(slam\w*|shut\w*|clos\w+|bang\w*)\b|\b(slam\w*|shut\w*|clos\w+)\b.*\bdoor\b", _door),
    ("footstep-echo", r"\b(footsteps?|steps?|stomps?)\b.*\b(echo\w*|cave|cavern|hall|tunnel|reverb)\b", _footstep),
    ("blade-hit", r"\b(sword|blade|axe|hammer|mace|club)\b.*\b(hit\w*|strik\w+|clash\w*|clang\w*|block\w*|smash\w*)\b", _blade),
    ("explosion", r"\b(explosion|explodes?|exploding|blast|detonat\w+)\b", _explosion),
    ("glass", r"\bglass\b.*\b(break\w*|smash\w*|shatter\w*)\b|\b(break\w*|smash\w*|shatter\w*)\b.*\b(glass|window|bottle)\b", _glass),
    ("gunshot", r"\b(gunshots?|gun|pistol|rifle|shotgun)\b", _gun),
    ("punch", r"\b(punch\w*|kick\w*)\b", _punch),
    ("spell", r"\b(spell|magic\w*|fireball|cast\w*)\b", _spell),
    ("splash", r"\b(splash\w*)\b|\binto (the |a )?(water|lake|river|pond|sea)\b", _splash),
    ("echo", r"\b(echo(es|ing)?|reverb)\b", _echo),
    ("then", r"\b(and then|then|followed by)\b", _then),
)
MAX_DESCRIPTION = 500


def split(description: Any) -> dict[str, Any]:
    """`description` as layers to make one at a time: `{rule, layers: [{role, label,
    describe, start_ms}]}`. `rule` is the id of the rule that matched, or None, in which case
    there is exactly one layer and it is the description as typed."""
    typed = " ".join(str(description or "").split())
    if not typed:
        raise ValueError("describe the sound first")
    if len(typed) > MAX_DESCRIPTION:
        raise ValueError(f"the description is longer than {MAX_DESCRIPTION} characters")
    text = typed.lower()
    for rule, pattern, build in RULES:
        if re.search(pattern, text):
            parts = build(text)
            if len(parts) > 1:
                return {"rule": rule, "layers": [{"role": role, "label": ROLES[role], "describe": words,
                                                  "start_ms": start} for role, words, start in parts]}
    return {"rule": None, "layers": [{"role": "whole", "label": ROLES["whole"], "describe": typed, "start_ms": 0}]}
