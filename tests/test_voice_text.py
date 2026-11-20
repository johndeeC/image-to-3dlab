"""Tests for image_to_3dlab/voice_text.py: the words before a voice engine reads them.

Pure text in, text out. The functions are imported, never re-implemented.
"""

from __future__ import annotations

import pytest

from image_to_3dlab import voice_text as vt


def test_a_paragraph_becomes_one_line_per_sentence():
    text = "Welcome, traveller! Take a look at my wares. The potions are fresh today."
    assert vt.split_sentences(text) == ["Welcome, traveller!", "Take a look at my wares.",
                                        "The potions are fresh today."]


@pytest.mark.parametrize("text", [
    "Mr. Smith went to see Dr. Jones about the map.",
    "The potion costs 3.5 gold and weighs 0.25 pounds.",
    "It was written by J. R. R. Tolkien long ago.",
    "Bring rope, torches, etc. and meet me at St. Mary's gate.",
    "Wait... is that a dragon over there?",
])
def test_a_full_stop_inside_a_title_a_number_or_initials_does_not_split(text):
    assert vt.split_sentences(text) == [text]


def test_a_very_short_sentence_stays_with_its_neighbour():
    assert vt.split_sentences("Halt! Who goes there?") == ["Halt! Who goes there?"]
    assert vt.split_sentences("We ride at dawn, all of us. Go!") == ["We ride at dawn, all of us. Go!"]
    assert vt.split_sentences("No.") == ["No."]


def test_a_new_line_always_starts_a_new_line_to_say_and_quotes_stay_attached():
    assert vt.split_sentences('Run!\n\n"Stay where you are!" she said.  Nobody moved an inch.') == [
        "Run!", '"Stay where you are!" she said.', "Nobody moved an inch."]
    assert vt.split_sentences("  \n ") == []
    assert vt.split_sentences("这是第一句话，说得很长很长很长。这是第二句话，也说得很长很长。") == [
        "这是第一句话，说得很长很长很长。", "这是第二句话，也说得很长很长。"]


@pytest.mark.parametrize("number,words", [
    (0, "zero"), (7, "seven"), (13, "thirteen"), (40, "forty"), (99, "ninety-nine"),
    (100, "one hundred"), (110, "one hundred ten"), (1200, "one thousand two hundred"),
    (1_000_000, "one million"), (2_000_045, "two million forty-five"), (-3, "minus three"),
    (10**15, "one zero zero zero zero zero zero zero zero zero zero zero zero zero zero zero"),
])
def test_whole_numbers_in_words(number, words):
    assert vt.number_words(number) == words


@pytest.mark.parametrize("number,words", [
    (1, "first"), (2, "second"), (3, "third"), (4, "fourth"), (5, "fifth"), (12, "twelfth"),
    (20, "twentieth"), (21, "twenty-first"), (100, "one hundredth"), (103, "one hundred third"),
])
def test_places_in_words(number, words):
    assert vt.ordinal_words(number) == words


@pytest.mark.parametrize("typed,said", [
    ("Mr. Smith & Dr. Jones", "Mister Smith and Doctor Jones"),
    ("St. Mary's is on Mill St.", "Saint Mary's is on Mill St."),
    ("It costs 3.5 gold", "It costs three point five gold"),
    ("You owe me $5", "You owe me five dollars"),
    ("Just $1, friend", "Just one dollar, friend"),
    ("That is £1,200.50!", "That is one thousand two hundred point five zero pounds!"),
    ("50% off for the 1st buyer", "fifty percent off for the first buyer"),
    ("Room no. 7, door #12", "Room number seven, door number twelve"),
    ("Back in 1999 it was 1500 gold", "Back in nineteen ninety-nine it was fifteen hundred gold"),
    ("The year 2026, the year 1905", "The year twenty twenty-six, the year nineteen oh five"),
    ("2 x 3 = 6", "two times three equals six"),
    ("It is -5° out", "It is minus five degrees out"),
    ("Code 007", "Code zero zero seven"),
    ("  “Halt!”   he  said ", '"Halt!" he said'),
    ("I have 5.", "I have five."),
])
def test_text_as_it_is_said(typed, said):
    assert vt.spoken(typed) == said


def test_other_languages_keep_their_numbers_for_the_engine():
    assert vt.spoken("Das kostet 3 Gold  & mehr", "german") == "Das kostet 3 Gold & mehr"
    assert vt.spoken("It costs 3 gold", "en-gb") == "It costs three gold"


def test_a_short_line_can_never_run_for_a_minute():
    short, long = "Halt! Who goes there?", " ".join(["word"] * 40)
    assert vt.most_seconds(short) == 7.0                       # 3 s and a second a word
    assert vt.most_seconds(long) == 43.0
    assert vt.most_seconds(" ".join(["word"] * 500)) == vt.MOST_SECONDS
    assert vt.most_seconds("你好，世界") == 5.0                   # four signs at half a second
    assert vt.speech_tokens(short, 12.5) == 88
    assert vt.speech_tokens(short, 12.5) < 60 * 12.5


@pytest.mark.parametrize("word,sounds", [
    ("Eldoria", "ɛldˈɔɹiə"), ("Zog", "zˈɑɡ"), ("Thrain", "θɹˈAn"), ("Cyra", "sˈɪɹə"),
    ("Grommash", "ɡɹˈɑmæʃ"), ("Yennefer", "jɛnˈɛfəɹ"), ("Kael'thas", "kˈAlθæs"),
])
def test_a_made_up_name_is_said_from_its_letters(word, sounds):
    assert vt.guess_phonemes(word) == sounds


def test_a_guess_uses_only_signs_and_nothing_for_no_letters():
    assert vt.guess_phonemes("123") == "" and vt.guess_phonemes("") == ""
    assert "ˈ" in vt.guess_phonemes("Bjørnstjerne")
