"""Text for voice lines: what happens to the words before a voice engine reads them.

Four small, pure jobs, with no model and no package behind them:

- `split_sentences`: a paragraph as separate lines to say, so each can be made again on its
  own. A full stop inside "Mr. Smith", "3.5" or "J. R. R. Tolkien" does not end a sentence,
  and a very short one ("Halt!") stays with its neighbour.
- `spoken`: the line as it should be *said*: "Dr." becomes "Doctor", "3.5" becomes "three
  point five", "&" becomes "and". English only; other languages are left to the engine. The
  typed text is never changed: the record keeps both.
- `most_seconds` / `speech_tokens`: the longest a line may reasonably take to say. An engine
  that writes speech piece by piece can get stuck repeating itself; with this limit a short
  line can never run for a minute.
- `guess_phonemes`: how to say a word that is in no dictionary (a made-up name), from the
  usual sounds of its letters. Rough on purpose. It stands in for espeak-ng, the GPL-3
  program most Kokoro set-ups call for this, so that nothing GPL is needed.

The ideas (not the code) for the first three came from the user's own audiobook project.
"""

from __future__ import annotations

import math
import re

MAX_LINE = 300          # characters in one line to say: longer and an engine may cut it short
MAX_LINES = 8           # lines in one go; also the most layers "Save as one" can join
SHORT_SENTENCE = 12     # a sentence with fewer characters than this joins its neighbour

# A full stop after one of these does not end a sentence.
ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "capt", "col", "gen", "sgt", "lt",
    "vs", "etc", "no", "e.g", "i.e",
})
_ENDS = ".!?…。！？"
_CLOSERS = "\"'”’)]»"


def _ends_sentence(line: str, at: int) -> bool:
    """Whether the mark at `line[at]` really ends a sentence."""
    after = line[at + 1:at + 2]
    following = line[at + 1:].lstrip(_ENDS + _CLOSERS + " \t")
    if following and following[0].islower():
        return False                                  # '"Stop!" she said', "approx. ten"
    if line[at] != ".":
        return True
    if after and not after.isspace() and after not in _CLOSERS and after not in _ENDS:
        return False                                  # 3.5, e.g., a.m.
    word = re.search(r"([^\s(\[\"'“‘]+)$", line[:at])
    before = word.group(1).lower() if word else ""
    if before in ABBREVIATIONS:
        return False
    return not (len(before) == 1 and before.isalpha() and word.group(1).isupper())   # J. R. R. Tolkien


def _split_line(line: str) -> list[str]:
    parts, start, at = [], 0, 0
    while at < len(line):
        if line[at] in _ENDS and _ends_sentence(line, at):
            end = at + 1
            while end < len(line) and (line[end] in _ENDS or line[end] in _CLOSERS):
                end += 1
            parts.append(line[start:end].strip())
            start = at = end
        else:
            at += 1
    parts.append(line[start:].strip())
    return [part for part in parts if part]


def split_sentences(text: str, short: int = SHORT_SENTENCE) -> list[str]:
    """`text` as lines to say, one sentence each. A new line in the text always starts a new
    line to say. A sentence shorter than `short` characters joins the one after it (or the
    one before, when it is last), so "Halt! Who goes there?" stays one line."""
    lines: list[str] = []
    for row in str(text or "").splitlines():
        parts = _split_line(" ".join(row.split()))
        merged: list[str] = []
        carry = ""
        for part in parts:
            part = f"{carry} {part}".strip()
            carry = ""
            if len(part) < short:
                carry = part
            else:
                merged.append(part)
        if carry:
            if merged:
                merged[-1] = f"{merged[-1]} {carry}"
            else:
                merged.append(carry)
        lines += merged
    return lines


# ----------------------------------------------------------------------------- spoken text

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SCALES = ((10**12, "trillion"), (10**9, "billion"), (10**6, "million"), (1000, "thousand"))
_ORDINALS = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
             "nine": "ninth", "twelve": "twelfth"}
MOST_NUMBER = 10**15        # from here up a number is read digit by digit


def number_words(number: int) -> str:
    """A whole number in English words: 1200 is "one thousand two hundred"."""
    number = int(number)
    if number < 0:
        return "minus " + number_words(-number)
    if number >= MOST_NUMBER:
        return " ".join(_ONES[int(digit)] for digit in str(number))
    if number < 20:
        return _ONES[number]
    if number < 100:
        tens, ones = divmod(number, 10)
        return _TENS[tens - 2] + (f"-{_ONES[ones]}" if ones else "")
    if number < 1000:
        hundreds, rest = divmod(number, 100)
        return f"{_ONES[hundreds]} hundred" + (f" {number_words(rest)}" if rest else "")
    for size, name in _SCALES:
        if number >= size:
            many, rest = divmod(number, size)
            return f"{number_words(many)} {name}" + (f" {number_words(rest)}" if rest else "")
    raise AssertionError("unreachable")


def ordinal_words(number: int) -> str:
    """A place in English words: 21 is "twenty-first"."""
    words = number_words(number)
    cut = max(words.rfind(" "), words.rfind("-")) + 1
    head, last = words[:cut], words[cut:]
    if last in _ORDINALS:
        last = _ORDINALS[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return head + last


def _plain_number(digits: str) -> str:
    """Digits as they are usually said. 1100 to 1999 and 2010 to 2099, written without a
    comma, are said in pairs ("nineteen ninety-nine", "fifteen hundred"): right for a year
    and still natural for a price or a count."""
    number = int(digits)
    if len(digits) == 4 and (1100 <= number <= 1999 or 2010 <= number <= 2099):
        first, last = divmod(number, 100)
        if last == 0:
            return f"{number_words(first)} hundred"
        return f"{number_words(first)} {'oh ' if last < 10 else ''}{number_words(last)}"
    if len(digits) > 1 and digits.startswith("0"):
        return " ".join(_ONES[int(digit)] for digit in digits)      # a code, not an amount
    return number_words(number)


def _say_number(match: re.Match) -> str:
    whole, fraction = match.group("whole").replace(",", ""), match.group("fraction")
    words = _plain_number(whole) if "," not in match.group("whole") else number_words(int(whole))
    if fraction:
        words += " point " + " ".join(_ONES[int(digit)] for digit in fraction)
    return words


_NUMBER = r"(?P<whole>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<fraction>\d+))?"
_TITLES = {"mr": "Mister", "mrs": "Missus", "ms": "Miz", "dr": "Doctor", "prof": "Professor",
           "capt": "Captain", "col": "Colonel", "gen": "General", "sgt": "Sergeant",
           "lt": "Lieutenant", "sr": "Senior", "jr": "Junior", "vs": "versus"}
_MONEY = {"$": ("dollar", "dollars"), "£": ("pound", "pounds"), "€": ("euro", "euros")}
_SYMBOLS = (("&", "and"), ("%", "percent"), ("@", "at"), ("=", "equals"), ("+", "plus"),
            ("°", "degrees"))
ENGLISH = frozenset({"english", "en", "en-us", "en-gb", "a", "b"})


def spoken(text: str, language: str = "english") -> str:
    """`text` as it should be said. Spaces and quote marks are tidied for every language;
    titles, symbols and numbers are written out for English only."""
    line = " ".join(str(text or "").split())
    line = line.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"}))
    if str(language or "").lower() not in ENGLISH:
        return line
    line = re.sub(r"\b(" + "|".join(_TITLES) + r")\.(?=\s|$)",
                  lambda m: _TITLES[m.group(1).lower()], line, flags=re.IGNORECASE)
    line = re.sub(r"\bSt\.(?=\s+[A-Z])", "Saint", line)
    line = re.sub(r"\b[Nn]o\.\s*(?=\d)", "number ", line)
    line = re.sub(r"#(?=\d)", "number ", line)
    for sign, (one, many) in _MONEY.items():
        line = re.sub(re.escape(sign) + r"\s*" + _NUMBER,
                      lambda m, one=one, many=many: f"{_say_number(m)} "
                      f"{one if m.group('whole') == '1' and not m.group('fraction') else many}", line)
    line = re.sub(r"\b(\d+)(st|nd|rd|th)\b", lambda m: ordinal_words(int(m.group(1))), line)
    line = re.sub(r"(?<=\d)\s*[x×]\s*(?=\d)", " times ", line)
    line = re.sub(r"(?<![\w.])-(?=\d)", "minus ", line)
    line = re.sub(_NUMBER, _say_number, line)
    for sign, word in _SYMBOLS:
        line = line.replace(sign, f" {word} ")
    return " ".join(line.split())


# ----------------------------------------------------------------------------- length guard

SECONDS_FLOOR = 3.0         # every line may take this long...
SECONDS_PER_WORD = 1.0      # ...plus this for each word: about twice a normal reading
SECONDS_PER_SIGN = 0.5      # ...or this for each character of Chinese, Japanese or Korean
MOST_SECONDS = 90.0
_SIGNS = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


def most_seconds(text: str) -> float:
    """The longest `text` may reasonably take to say: generous for a slow, feeling reading,
    far short of an engine stuck in a loop."""
    line = str(text or "")
    signs = len(_SIGNS.findall(line))
    words = len(re.findall(r"[^\W\d_]+(?:['’-][^\W\d_]+)*|\d+", _SIGNS.sub(" ", line)))
    return min(MOST_SECONDS, SECONDS_FLOOR + SECONDS_PER_WORD * words + SECONDS_PER_SIGN * signs)


def speech_tokens(text: str, per_second: float) -> int:
    """`most_seconds` as a count of the pieces an engine writes speech in (Qwen3-TTS writes
    12.5 a second). The engine is stopped there, and a line that reaches it is dropped."""
    return int(math.ceil(most_seconds(text) * per_second))


# ----------------------------------------------------------------------------- unknown words

# Letters to Kokoro's own sound signs (misaki's alphabet: A is the vowel of "day", I "eye",
# O "go", W "now", Y "boy"). Longest match first. A guess, not a dictionary.
_SOUNDS = (
    ("tion", "ʃən"), ("sion", "ʒən"), ("ough", "O"), ("igh", "I"), ("eau", "O"), ("tch", "ʧ"),
    ("sch", "sk"), ("dge", "ʤ"),
    ("sh", "ʃ"), ("ch", "ʧ"), ("th", "θ"), ("ph", "f"), ("ck", "k"), ("qu", "kw"), ("ng", "ŋ"),
    ("wh", "w"), ("kn", "n"), ("wr", "ɹ"), ("gh", "ɡ"), ("zh", "ʒ"),
    ("ee", "i"), ("ea", "i"), ("oo", "u"), ("ou", "W"), ("ow", "O"), ("ai", "A"), ("ay", "A"),
    ("ei", "A"), ("ey", "A"), ("oi", "Y"), ("oy", "Y"), ("au", "ɔ"), ("aw", "ɔ"), ("ie", "i"),
    ("oa", "O"), ("ue", "u"), ("ae", "A"),
    ("ar", "ɑɹ"), ("er", "əɹ"), ("ir", "ɜɹ"), ("ur", "ɜɹ"), ("or", "ɔɹ"),
    ("a", "æ"), ("e", "ɛ"), ("i", "ɪ"), ("o", "ɑ"), ("u", "ʌ"), ("y", "ɪ"),
    ("b", "b"), ("c", "k"), ("d", "d"), ("f", "f"), ("g", "ɡ"), ("h", "h"), ("j", "ʤ"),
    ("k", "k"), ("l", "l"), ("m", "m"), ("n", "n"), ("p", "p"), ("q", "k"), ("r", "ɹ"),
    ("s", "s"), ("t", "t"), ("v", "v"), ("w", "w"), ("x", "ks"), ("z", "z"),
)
_VOWEL_SOUNDS = "AIOWYæɑɛɪiuʊʌəɜɔ"
_FOLD = str.maketrans("àáâãäåèéêëìíîïòóôõöùúûüýÿñç", "aaaaaaeeeeiiiiooooouuuuyync")


def guess_phonemes(word: str) -> str:
    """How to say `word` from the usual sounds of its letters, in Kokoro's sound signs, with
    the stress on its first vowel (its second-to-last when there are three or more). Empty
    when the word has no letters. Rough: "Eldoria" comes out near "EL-dor-ee-uh"."""
    letters = re.sub(r"[^a-z]", "", str(word or "").lower().translate(_FOLD))
    letters = re.sub(r"([b-df-hj-np-tv-z])\1", r"\1", letters)           # a doubled consonant is said once
    if not letters:
        return ""
    sounds: list[str] = []
    at = 0
    while at < len(letters):
        rest = letters[at:]
        last = at == len(letters) - 1
        if rest[0] == "c" and rest[1:2] in ("e", "i", "y") and rest[1:2]:
            sounds.append("s")
            at += 1
        elif rest[0] == "y" and at == 0 and len(letters) > 1:
            sounds.append("j")
            at += 1
        elif last and rest == "e" and len(sounds) > 1:
            at += 1                                                        # a silent final e
        elif last and rest in ("a", "y", "i", "o") and sounds:
            sounds.append({"a": "ə", "y": "i", "i": "i", "o": "O"}[rest])
            at += 1
        elif rest[0] == "i" and rest[1:2] in ("a", "o"):
            sounds.append("i")                                             # "ia", "io": two sounds
            at += 1
        else:
            for spelt, said in _SOUNDS:
                if rest.startswith(spelt):
                    sounds.append(said)
                    at += len(spelt)
                    break
            else:
                at += 1
    vowels = [index for index, said in enumerate(sounds) if said[0] in _VOWEL_SOUNDS]
    if len(vowels) > 1 and vowels[-1] - vowels[-2] == 1:
        vowels.pop()                                                       # "-ia": one beat, for the stress
    if vowels:
        stressed = vowels[-2] if len(vowels) >= 3 else vowels[0]
        sounds.insert(stressed, "ˈ")
    return "".join(sounds)
