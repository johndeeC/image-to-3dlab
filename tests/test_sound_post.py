"""Tests for image_to_3dlab/sound_post.py: the clean-up between the engine and the WAV.

All on made-up sounds (a sine, noise, silence), so nothing here needs a model or an audio
package. The functions are imported, never re-implemented.
"""

from __future__ import annotations

import hashlib
import math
from itertools import pairwise

import numpy as np
import pytest

from image_to_3dlab import sound_post as sp


def sine(seconds=0.5, hz=440.0, rate=sp.RATE, level=0.5):
    t = np.arange(int(seconds * rate)) / rate
    return (level * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def padded(sound, before=4800, after=9600):
    return np.concatenate([np.zeros(before, np.float32), sound, np.zeros(after, np.float32)])


def test_dead_air_is_cut_from_both_ends():
    sound = sine()
    trimmed, cut = sp.trim_silence(padded(sound))
    assert cut is True
    # the sine starts and ends near zero, so a few of its own quietest samples go too
    assert sound.size - 40 < trimmed.size <= sound.size


def test_a_sound_with_no_dead_air_is_left_alone():
    sound = sine() + 0.2
    trimmed, cut = sp.trim_silence(sound)
    assert cut is False and trimmed.size == sound.size


def test_silence_all_the_way_through_is_not_trimmed_to_nothing():
    trimmed, cut = sp.trim_silence(np.zeros(1000, np.float32))
    assert cut is False and trimmed.size == 1000


def test_fades_start_and_end_at_zero_and_leave_the_middle():
    sound = np.full(4800, 0.5, np.float32)
    faded = sp.fade(sound, ms=5.0)
    frames = int(sp.RATE * 0.005)
    assert faded[0] == 0.0 and faded[-1] == 0.0
    assert np.all(np.diff(faded[:frames]) > 0)            # a ramp, not a step
    assert np.allclose(faded[frames:-frames], 0.5)
    assert sound[0] == 0.5                                # the caller's array is not changed


def test_a_fade_never_outgrows_a_tiny_sound():
    faded = sp.fade(np.ones(6, np.float32), ms=5.0)
    assert faded.size == 6 and np.isfinite(faded).all()


def test_a_loud_sound_is_turned_down_not_clipped():
    loud = sine(level=1.7)
    limited = sp.limit_peak(loud)
    assert sp.peak_of(limited) == pytest.approx(0.98, abs=1e-4)
    assert np.allclose(limited / sp.peak_of(limited), loud / sp.peak_of(loud), atol=1e-5)   # same shape


def test_a_quiet_sound_is_not_turned_up():
    quiet = sine(level=0.2)
    assert np.array_equal(sp.limit_peak(quiet), quiet)


def test_a_mix_of_layers_passes_the_limiter_once():
    """Layers are summed and limited as a whole: what the next slice leans on."""
    mix = sine(level=0.8) + sine(hz=220.0, level=0.8)
    assert sp.peak_of(mix) > 1.0
    assert sp.peak_of(sp.limit_peak(mix)) == pytest.approx(0.98, abs=1e-4)


@pytest.mark.parametrize("rate", [44_100, 24_000, 96_000])
def test_another_rate_becomes_48k_with_the_same_pitch_and_length(rate):
    out = sp.resample(sine(seconds=0.5, hz=1000.0, rate=rate), rate)
    assert abs(out.size - 24_000) <= 1
    spectrum = np.abs(np.fft.rfft(out))
    assert np.argmax(spectrum) * sp.RATE / out.size == pytest.approx(1000.0, abs=4.0)
    assert sp.peak_of(out) == pytest.approx(0.5, abs=0.03)


def test_48k_is_passed_through_as_a_copy():
    sound = sine()
    out = sp.resample(sound, sp.RATE)
    assert np.array_equal(out, sound) and out is not sound


def test_two_channels_become_one():
    left, right = sine(level=0.4), sine(level=0.8)
    mono = sp.to_mono(np.stack([left, right], axis=1))
    assert mono.shape == left.shape and sp.peak_of(mono) == pytest.approx(0.6, abs=1e-3)
    # an engine that answers (channels, frames), as MOSS does
    assert np.allclose(sp.to_mono(np.stack([left, right]), channels_first=True), mono)


def test_clean_does_all_of_it_and_says_what_it_did():
    raw = np.stack([padded(sine(level=1.5, rate=44_100)), padded(sine(level=1.5, rate=44_100))], axis=1)
    sound, post = sp.clean(raw, 44_100)
    assert sound.ndim == 1 and sound.dtype == np.float32
    assert sp.peak_of(sound) <= 0.98 + 1e-4
    assert sound[0] == 0.0 and sound[-1] == 0.0
    assert 0.45 < sound.size / sp.RATE < 0.55             # the padding is gone
    assert post == {"trim": True, "trimmed": True, "fade_ms": 5.0, "peak": 0.98,
                    "gain_db": pytest.approx(-3.7, abs=0.05), "tail_s": 0.0, "fade_out_ms": 5.0}
    assert sp.inspect(sound, sp.RATE)["channels"] == 1


# ------------------------------------------------------------- a fresh sound: volume and tail

def ring(seconds, half_life=0.2, hz=880.0, level=0.5):
    """A struck thing: a tone that halves every `half_life` seconds."""
    t = np.arange(int(seconds * sp.RATE)) / sp.RATE
    return (level * np.sin(2 * np.pi * hz * t) * 0.5 ** (t / half_life)).astype(np.float32)


def db(level, against):
    return 20 * np.log10(level / against)


def test_a_quiet_fresh_sound_is_turned_up_to_the_standard_peak():
    sound, post = sp.clean(sine(level=0.25), sp.RATE)
    assert sp.peak_of(sound) == pytest.approx(0.98, abs=1e-3)
    assert post["gain_db"] == pytest.approx(db(0.98, 0.25), abs=0.05) and post["gain_db"] > 11


def test_set_peak_goes_up_or_down_and_says_by_how_much():
    up, gain_up = sp.set_peak(sine(level=0.25))
    down, gain_down = sp.set_peak(sine(level=1.7))
    assert sp.peak_of(up) == pytest.approx(0.98, abs=1e-4) and gain_up == pytest.approx(db(0.98, 0.25), abs=0.01)
    assert sp.peak_of(down) == pytest.approx(0.98, abs=1e-4) and gain_down < 0


def test_the_boost_stops_at_24_db_so_a_whisper_is_not_blown_up_into_noise():
    faint = sine(level=0.001)
    louder, gain = sp.set_peak(faint)
    assert gain == pytest.approx(24.0) == sp.MAX_BOOST_DB
    assert sp.peak_of(louder) == pytest.approx(0.001 * 10 ** (24 / 20), rel=1e-3)


def test_near_silence_is_not_boosted_so_the_silence_check_still_refuses_it():
    hiss = sine(level=sp.SILENT_PEAK / 2)
    same, gain = sp.set_peak(hiss)
    assert gain == 0.0 and np.array_equal(same, hiss)
    sound, _ = sp.clean(hiss, sp.RATE)
    with pytest.raises(sp.SoundProblem, match="silent"):
        sp.inspect(sound, sp.RATE)


@pytest.mark.parametrize("asked,most,want", [(1.0, 30.0, 2.0), (0.5, 30.0, 1.5), (4.0, 30.0, 8.0),
                                             (25.0, 30.0, 30.0), (30.0, 30.0, 30.0), (1.5, 30.0, 3.0)])
def test_the_engine_is_asked_for_room_for_a_tail(asked, most, want):
    """Twice as long, at least a second more, never past the engine's most."""
    assert sp.seconds_with_tail(asked, most) == pytest.approx(want, abs=0.051)
    assert asked <= sp.seconds_with_tail(asked, most) <= most


def test_a_sound_still_ringing_at_the_cut_keeps_its_tail_until_it_has_died_away():
    raw = ring(1.5, half_life=0.04)                     # asked for 0.2 s: 30 dB down, not yet 45
    asked = int(0.2 * sp.RATE)
    assert db(np.abs(raw[asked - 480:asked]).max(), 0.5) > -45          # still ringing at the cut
    kept, tail_s, fade_out = sp.keep_tail(raw, 0.2)
    assert kept.size > asked and np.array_equal(kept, raw[:kept.size])   # the same sound, just more of it
    assert 0.05 < tail_s < 0.3 and tail_s == pytest.approx(kept.size / sp.RATE - 0.2, abs=1e-3)
    assert db(np.abs(kept[-480:]).max(), 0.5) < -45                      # it ends once it is quiet
    assert fade_out == sp.TAIL_FADE_MS >= 20


def test_a_long_ring_runs_to_the_end_of_the_room_it_was_given_and_fades():
    raw = ring(1.5, half_life=0.25)                     # 24 dB down at 1.0 s, 36 dB down at 1.5 s
    kept, tail_s, fade_out = sp.keep_tail(raw, 1.0)
    assert kept.size == raw.size and tail_s == pytest.approx(0.5)
    assert fade_out == pytest.approx(200.0)              # still going: a long fade, 40% of the tail
    sound, post = sp.clean(raw, sp.RATE, asked_seconds=1.0)
    assert post["tail_s"] == pytest.approx(0.5) and post["fade_out_ms"] == pytest.approx(200.0)
    assert abs(sound.size - raw.size) <= 2 and sound[-1] == 0.0
    frames = int(200 * sp.RATE / 1000)
    half_way = np.abs(sound[-frames:-frames // 2]).max() / np.abs(sound[-2 * frames:-frames]).max()
    assert 0.3 < half_way < 1.0                          # a slope, not a cliff


def test_the_long_fade_is_never_more_than_300_ms():
    _, tail_s, fade_out = sp.keep_tail(ring(3.0, half_life=0.5), 1.0)       # 2 s of tail, never quiet
    assert tail_s == pytest.approx(2.0) and fade_out == sp.LONG_FADE_MS == 300.0


def test_a_long_fade_out_is_a_smooth_curve():
    faded = sp.fade(np.ones(sp.RATE, np.float32), out_ms=300.0)
    frames = int(0.3 * sp.RATE)
    assert faded[-1] == 0.0 and faded[-frames - 1] == 1.0
    assert faded[-frames // 2] == pytest.approx(0.5, abs=0.01)
    assert 1.0 - faded[-frames + 20] < 1e-4                      # it leaves full volume gently
    assert faded[240] == 1.0 and 0.4 < faded[120] < 0.6          # the fade-in is still the short straight one


def test_a_sound_already_quiet_at_the_cut_ends_as_before():
    raw = np.concatenate([ring(0.5, half_life=0.03), np.zeros(sp.RATE, np.float32)])    # 1.5 s made
    kept, tail_s, fade_out = sp.keep_tail(raw, 1.0)
    assert kept.size == sp.RATE and tail_s == 0.0 and fade_out == sp.FADE_MS
    with_room, post = sp.clean(raw, sp.RATE, asked_seconds=1.0)
    as_before, _ = sp.clean(raw[:sp.RATE], sp.RATE)
    assert np.array_equal(with_room, as_before)
    assert post["tail_s"] == 0.0 and post["fade_out_ms"] == sp.FADE_MS


def test_a_steady_sound_is_not_made_longer_only_faded_out():
    """Rain does not die away, so more of it is not a tail: it ends where asked, on a slope."""
    kept, tail_s, fade_out = sp.keep_tail(sine(seconds=1.5), 1.0)
    assert kept.size == sp.RATE and tail_s == 0.0 and fade_out == sp.LONG_FADE_MS
    short, _, short_fade = sp.keep_tail(sine(seconds=1.0), 0.5)
    assert short.size == sp.RATE // 2 and short_fade == pytest.approx(200.0)     # 40% of a short sound


def test_with_no_room_a_ringing_sound_still_gets_the_long_fade():
    kept, tail_s, fade_out = sp.keep_tail(ring(1.0), 1.0)
    assert kept.size == sp.RATE and tail_s == 0.0 and fade_out == sp.LONG_FADE_MS


def test_the_fade_out_can_be_longer_than_the_fade_in():
    faded = sp.fade(np.ones(4800, np.float32), ms=5.0, out_ms=30.0)
    assert faded[0] == 0.0 and faded[-1] == 0.0
    assert faded[240] == 1.0 and faded[-241] < 1.0 and faded[-1441] == 1.0


@pytest.mark.parametrize("bad,why", [
    (np.array([], np.float32), "empty"),
    (np.array([0.1, np.nan, 0.2], np.float32), "broken"),
    (np.array([0.1, np.inf], np.float32), "broken"),
])
def test_clean_refuses_empty_and_broken_audio(bad, why):
    with pytest.raises(sp.SoundProblem, match=why):
        sp.clean(bad, sp.RATE)


@pytest.mark.parametrize("sound,rate,why", [
    (np.zeros(4800, np.float32), sp.RATE, "silent"),
    (np.array([0.2, np.nan], np.float32), sp.RATE, "broken"),
    (np.array([], np.float32), sp.RATE, "empty"),
    (sine(), 44_100, "44100 Hz"),
    (np.stack([sine(), sine()], axis=1), sp.RATE, "2 channels"),
])
def test_inspect_refuses_what_must_not_be_kept(sound, rate, why):
    with pytest.raises(sp.SoundProblem, match=why):
        sp.inspect(sound, rate)


def test_inspect_reports_length_and_peak():
    facts = sp.inspect(sine(seconds=0.25, level=0.5), sp.RATE)
    assert facts == {"sample_rate": 48_000, "channels": 1, "frames": 12_000, "seconds": 0.25, "peak": 0.5}


def test_a_wav_comes_back_as_it_went_in(tmp_path):
    sound = sp.fade(sine(seconds=0.2))
    path = sp.write_wav(tmp_path / "deep" / "sound.wav", sound)
    back, rate = sp.read_wav(path)
    assert rate == sp.RATE and back.shape == sound.shape
    assert np.allclose(back, sound, atol=1 / 32767 + 1e-6)          # 16 bits of rounding, no more
    facts = sp.inspect_wav(path)
    assert facts["channels"] == 1 and facts["sample_rate"] == 48_000 and facts["seconds"] == 0.2
    assert facts["bytes"] == path.stat().st_size == 44 + 2 * sound.size
    assert facts["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest() == sp.sha256_file(path)


def test_samples_past_full_scale_are_held_not_wrapped(tmp_path):
    path = sp.write_wav(tmp_path / "s.wav", np.array([2.0, -2.0, 0.0], np.float32))
    back, _ = sp.read_wav(path)
    assert back.tolist() == [1.0, -1.0, 0.0]


def test_a_silent_file_is_refused_on_disk_too(tmp_path):
    path = sp.write_wav(tmp_path / "s.wav", np.zeros(480, np.float32))
    with pytest.raises(sp.SoundProblem, match="silent"):
        sp.inspect_wav(path)


def test_the_wave_picture_is_sixty_bars_with_the_loudest_at_one():
    hit = sine(seconds=0.5) * np.linspace(1.0, 0.0, 24_000, dtype=np.float32)   # a hit that dies away
    bars = sp.wave_bars(hit)
    assert len(bars) == 60 and max(bars) == 1.0 and bars[0] > bars[-1]
    assert sp.wave_bars(np.zeros(100, np.float32), 8) == [0.0] * 8


# --------------------------------------------------------------------------- variations

def loudest_hz(sound, rate=sp.RATE):
    return float(np.argmax(np.abs(np.fft.rfft(sound))) * rate / sound.size)


@pytest.mark.parametrize("semitones", [2.0, -3.0, 12.0])
def test_pitch_is_changed_by_resampling_so_the_length_changes_too(semitones):
    sound = sine(seconds=1.0, hz=440.0)
    ratio = 2.0 ** (semitones / 12.0)
    shifted = sp.shift_pitch(sound, semitones)
    assert shifted.dtype == np.float32
    assert shifted.size == round(sound.size / ratio)                 # higher is shorter, lower is longer
    assert loudest_hz(shifted) == pytest.approx(440.0 * ratio, abs=2.0)
    assert sp.peak_of(shifted) == pytest.approx(0.5, abs=0.02)       # pitch alone does not change how loud it is


def test_no_pitch_change_is_a_copy():
    sound = sine()
    same = sp.shift_pitch(sound, 0)
    assert np.array_equal(same, sound) and same is not sound


def test_volume_is_in_decibels():
    sound = sine(level=0.25)
    assert sp.peak_of(sp.change_volume(sound, 6.0)) == pytest.approx(0.25 * 10 ** 0.3, rel=1e-4)
    assert sp.peak_of(sp.change_volume(sound, -20.0)) == pytest.approx(0.025, rel=1e-4)
    assert np.array_equal(sp.change_volume(sound, 0.0), sound)


def test_a_start_can_move_a_few_milliseconds_either_way():
    sound = sine(seconds=0.1)
    later = sp.shift_start(sound, 5.0)
    assert later.size == sound.size + 240 and not later[:240].any() and np.array_equal(later[240:], sound)
    sooner = sp.shift_start(sound, -5.0)
    assert np.array_equal(sooner, sound[240:])
    assert sp.shift_start(sound, -10_000).size == sound.size - sound.size // 2    # never the whole sound
    assert np.array_equal(sp.shift_start(sound, 0), sound)


def test_the_same_seed_makes_the_same_set_and_another_seed_another():
    sound = sine(seconds=0.3)
    first = sp.make_variations(sound, sp.RATE, 6, "some", seed=11)
    again = sp.make_variations(sound, sp.RATE, 6, "some", seed=11)
    other = sp.make_variations(sound, sp.RATE, 6, "some", seed=12)
    facts = lambda takes: [(t["pitch_semitones"], t["volume_db"], t["gain_db"], t["note"]) for t in takes]
    assert facts(first) == facts(again) != facts(other)
    assert all(np.array_equal(a["samples"], b["samples"]) for a, b in zip(first, again, strict=True))
    assert [t["index"] for t in first] == [1, 2, 3, 4, 5, 6]


def test_take_one_is_the_original():
    sound = sine(seconds=0.3, level=0.2)          # quiet enough that nothing needs turning down
    first = sp.make_variations(sound, sp.RATE, 4, "a little", seed=3)[0]
    assert first["note"] == "the original" and np.array_equal(first["samples"], sound)
    assert (first["pitch_semitones"], first["volume_db"], first["gain_db"], first["start_ms"]) == (0.0, 0.0, 0.0, 0.0)


@pytest.mark.parametrize("spread,pitch,volume", [("a little", 1.0, 1.5), ("some", 2.5, 3.0), ("a lot", 4.0, 5.0)])
def test_how_different_sets_how_far_a_take_may_move(spread, pitch, volume):
    assert sp.HOW_DIFFERENT[spread] == {"pitch_semitones": pitch, "volume_db": volume}
    sound = sine(seconds=0.2, level=0.2)
    for seed in range(25):
        changed = sp.make_variations(sound, sp.RATE, 12, spread, seed=seed)[1:]
        pitches = [t["pitch_semitones"] for t in changed]
        volumes = [t["volume_db"] for t in changed]
        assert all(0.15 * pitch - 0.01 <= abs(p) <= pitch for p in pitches)      # moved, but never past the range
        assert all(0.15 * volume - 0.06 <= abs(v) <= volume for v in volumes)
        assert min(pitches) < 0 < max(pitches) and min(volumes) < 0 < max(volumes)   # spread out, both ways
        assert len(set(pitches)) == len(pitches)


def test_only_what_was_ticked_changes():
    sound = sine(seconds=0.2, level=0.2)
    both = sp.make_variations(sound, sp.RATE, 5, "some", seed=4)
    pitch_only = sp.make_variations(sound, sp.RATE, 5, "some", seed=4, change={"pitch"})
    volume_only = sp.make_variations(sound, sp.RATE, 5, "some", seed=4, change=["volume"])
    assert all(t["volume_db"] == 0.0 for t in pitch_only)
    assert [t["pitch_semitones"] for t in pitch_only] == [t["pitch_semitones"] for t in both]   # unticking one keeps the other
    assert all(t["pitch_semitones"] == 0.0 and t["samples"].size == sound.size for t in volume_only)
    assert all(t["start_ms"] == 0.0 for t in both)                    # timing is off unless asked for
    timed = sp.make_variations(sound, sp.RATE, 5, "some", seed=4, change={"start"})[1:]
    assert all(0 < abs(t["start_ms"]) <= sp.START_JITTER_MS or t["start_ms"] == 0 for t in timed)
    assert any(t["samples"].size != sound.size for t in timed)


def test_a_set_that_would_be_too_loud_is_turned_down_together_never_up():
    loud = sine(seconds=0.2, level=0.98)
    takes = sp.make_variations(loud, sp.RATE, 8, "a lot", seed=2, change={"volume"})
    peaks = [sp.peak_of(t["samples"]) for t in takes]
    assert max(peaks) == pytest.approx(sp.PEAK, abs=1e-3)             # the loudest take sits at the limit
    down = takes[0]["gain_db"]
    assert down < 0                                                    # the original came down with the rest
    for take, peak in zip(takes, peaks, strict=True):
        assert take["gain_db"] == pytest.approx(take["volume_db"] + down, abs=0.011)
        assert peak == pytest.approx(0.98 * 10 ** (take["gain_db"] / 20), rel=2e-3)   # differences are kept
    quiet = sp.make_variations(sine(seconds=0.2, level=0.05), sp.RATE, 8, "a lot", seed=2, change={"volume"})
    assert all(t["gain_db"] == t["volume_db"] for t in quiet)          # nothing is turned up to fill the room
    assert max(sp.peak_of(t["samples"]) for t in quiet) < 0.1


@pytest.mark.parametrize("count", [0, 1, 13, 2.5, True, "6"])
def test_a_count_outside_two_to_twelve_is_refused_not_clipped(count):
    with pytest.raises(sp.SoundProblem, match="between 2 and 12"):
        sp.make_variations(sine(), sp.RATE, count, "some", seed=1)


@pytest.mark.parametrize("kwargs,why", [
    ({"spread": "loads"}, "how different"), ({"change": []}, "pick what changes"),
    ({"change": ["pitch", "reverb"]}, "pick what changes")])
def test_unknown_choices_are_refused(kwargs, why):
    with pytest.raises(sp.SoundProblem, match=why):
        sp.make_variations(sine(), sp.RATE, **{"count": 4, "spread": "some", "seed": 1, **kwargs})


def test_silence_has_no_variations():
    with pytest.raises(sp.SoundProblem, match="no sound"):
        sp.make_variations(np.zeros(4800, np.float32), sp.RATE, 4, "some", seed=1)


def test_a_take_in_plain_words():
    assert sp.variation_note(0.5, -2.5) == "a bit higher, softer"
    assert sp.variation_note(-2.0, 0.0) == "lower"
    assert sp.variation_note(3.5, 4.5) == "much higher, much louder"
    assert sp.variation_note(0.0, 1.0) == "a bit louder"
    assert sp.variation_note(0.1, 0.1) == "nearly the same"


@pytest.mark.parametrize("count", [2, 3, 6, 12])
def test_play_like_a_game_never_repeats_a_take_straight_away(count):
    for seed in range(200):
        order = sp.play_order(count, rounds=6, seed=seed)
        assert len(order) == count * 6
        assert all(a != b for a, b in pairwise(order)), (seed, order)
        assert order[-1] != order[0]                                   # nor when the list starts over
        for turn in range(6):                                          # each round plays every take once
            assert sorted(order[turn * count:(turn + 1) * count]) == list(range(count))


def test_the_play_order_is_set_by_its_seed_and_really_shuffles():
    assert sp.play_order(6, seed=9) == sp.play_order(6, seed=9) != sp.play_order(6, seed=10)
    rounds = [tuple(sp.play_order(6, rounds=8, seed=1)[i * 6:(i + 1) * 6]) for i in range(8)]
    assert len(set(rounds)) > 1
    assert sp.play_order(1, rounds=3) == [0, 0, 0]
    with pytest.raises(ValueError):
        sp.play_order(0)


def test_wav_bytes_are_what_write_wav_puts_on_disk(tmp_path):
    sound = sine(seconds=0.05)
    path = sp.write_wav(tmp_path / "deep" / "a.wav", sound)
    assert path.read_bytes() == sp.wav_bytes(sound) and sp.wav_bytes(sound)[:4] == b"RIFF"


# ------------------------------------------------------------------ layers: the mix maths

def _wav(samples, rate=48_000, width=2, channels=1, fmt=1) -> bytes:
    """A WAV file's bytes, written by hand so odd ones (24-bit, float) can be made."""
    import struct
    data = np.asarray(samples, dtype=np.float64)
    if width == 2:
        body = np.round(data * 32767).astype("<i2").tobytes()
    elif width == 3:
        whole = np.round(data * (2**23 - 1)).astype(np.int32)
        body = b"".join(int(v).to_bytes(3, "little", signed=True) for v in whole.reshape(-1))
    elif width == 1:
        body = (np.round(data * 127) + 128).astype(np.uint8).tobytes()
    else:
        body = data.astype("<f4").tobytes()
    head = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(body), b"WAVE", b"fmt ", 16, fmt, channels,
                       rate, rate * channels * width, channels * width, width * 8, b"data", len(body))
    return head + body


def _tone(seconds=0.1, hz=440.0, level=0.5, rate=48_000):
    return (level * np.sin(2 * np.pi * hz * np.arange(int(seconds * rate)) / rate)).astype(np.float32)


def test_a_layer_lands_where_it_starts_and_the_mix_is_as_long_as_its_last_layer():
    first, second = _tone(0.1, level=0.2), _tone(0.2, 880, level=0.2)
    mix, facts = sp.mix_layers([{"samples": first}, {"samples": second, "start_ms": 250}])
    assert mix.dtype == np.float32 and mix.size == 12_000 + 9_600
    assert facts["seconds"] == 0.45
    assert facts["layers"] == [{"start_s": 0.0, "seconds": 0.1}, {"start_s": 0.25, "seconds": 0.2}]
    assert not mix[4_800:12_000].any()                              # the gap between them is silence
    assert np.allclose(mix[1_000:3_000], first[1_000:3_000], atol=1e-6)
    assert np.allclose(mix[12_000 + 1_000:12_000 + 3_000], second[1_000:3_000], atol=1e-6)


def test_layers_that_overlap_are_added_together():
    tone = _tone(0.1, level=0.2)
    mix, facts = sp.mix_layers([{"samples": tone}, {"samples": tone}])
    assert np.allclose(mix[500:4_000], 2 * tone[500:4_000], atol=1e-6)
    assert facts["turned_down_db"] == 0.0 and facts["peak"] == pytest.approx(0.4, abs=1e-3)


def test_the_mix_is_turned_down_once_as_a_whole_and_keeps_its_balance():
    loud, soft = _tone(0.1, level=0.9), _tone(0.1, 880, level=0.9)
    mix, facts = sp.mix_layers([{"samples": loud}, {"samples": soft, "volume_db": -6, "start_ms": 200}])
    assert sp.peak_of(mix) == pytest.approx(0.9, abs=1e-3) and facts["turned_down_db"] == 0.0
    # no overlap and under the peak: nothing moves, and the softer layer stays 6 dB under the other
    assert sp.peak_of(mix[9_600:]) / sp.peak_of(mix[:4_800]) == pytest.approx(10 ** (-6 / 20), abs=5e-3)
    both, facts = sp.mix_layers([{"samples": loud}, {"samples": loud}, {"samples": soft, "volume_db": -6, "start_ms": 200}])
    assert facts["peak_before"] == pytest.approx(1.8, abs=1e-2) and facts["peak"] == pytest.approx(0.98, abs=1e-3)
    assert facts["turned_down_db"] == pytest.approx(20 * math.log10(0.98 / 1.8), abs=0.05)
    assert not (np.abs(both) > 0.98 + 1e-6).any()                   # turned down, not clipped flat
    assert sp.peak_of(both[9_600:]) / sp.peak_of(both[:4_800]) == pytest.approx(10 ** (-6 / 20) / 2, abs=5e-3)


def test_a_quiet_mix_is_never_turned_up():
    quiet = _tone(0.1, level=0.05)
    mix, facts = sp.mix_layers([{"samples": quiet, "volume_db": -6}])
    assert sp.peak_of(mix) == pytest.approx(0.05 * 10 ** (-6 / 20), abs=1e-4)
    assert facts["turned_down_db"] == 0.0 and facts["loudness_db"] < -30


def test_pitch_changes_a_layers_length_and_so_the_mix():
    tone = _tone(0.2)
    up, facts_up = sp.mix_layers([{"samples": tone, "pitch_semitones": 12}])
    down, facts_down = sp.mix_layers([{"samples": tone, "pitch_semitones": -12}])
    assert up.size == tone.size // 2 and down.size == tone.size * 2
    assert facts_up["layers"][0]["seconds"] == 0.1 and facts_down["layers"][0]["seconds"] == 0.4
    spectrum = np.abs(np.fft.rfft(up))
    assert np.argmax(spectrum) * 48_000 / up.size == pytest.approx(880, abs=15)


@pytest.mark.parametrize("layer, said", [
    ({"start_ms": -1}, "Starts at must be between 0 and 10000 ms"),
    ({"start_ms": 10_001}, "Starts at"),
    ({"volume_db": 12.5}, "Volume must be between -40 and 12 dB"),
    ({"volume_db": float("nan")}, "Volume"),
    ({"pitch_semitones": -13}, "Pitch must be between -12 and 12 semitones"),
    ({"pitch_semitones": "high"}, "Pitch must be a number"),
    ({"volume_db": True}, "Volume must be a number"),
])
def test_a_layer_outside_the_limits_is_refused_not_pulled_inside(layer, said):
    with pytest.raises(sp.SoundProblem, match=said):
        sp.mix_layers([{"samples": _tone(), **layer}])


def test_no_layers_too_many_or_an_empty_one_are_refused():
    with pytest.raises(sp.SoundProblem, match="add a layer first"):
        sp.mix_layers([])
    with pytest.raises(sp.SoundProblem, match="up to 8 layers"):
        sp.mix_layers([{"samples": _tone()}] * 9)
    with pytest.raises(sp.SoundProblem, match="no sound in it"):
        sp.mix_layers([{"samples": []}])
    with pytest.raises(sp.SoundProblem, match="silent"):
        sp.mix_layers([{"samples": np.zeros(100, np.float32)}])


def test_loudness_is_measured_on_the_mix():
    square = np.where(np.arange(4_800) % 2, 0.5, -0.5).astype(np.float32)
    assert sp.loudness_db(square) == pytest.approx(-6.02, abs=0.01)
    assert sp.loudness_db(np.zeros(10)) is None


def test_a_16_bit_mono_wav_comes_in_as_it_is():
    tone = _tone(0.1)
    audio, facts = sp.import_wav(_wav(tone))
    assert audio.dtype == np.float32 and audio.size == tone.size
    assert np.allclose(audio, tone, atol=1e-4)
    assert facts == {"sample_rate": 48_000, "channels": 1, "bits": 16, "seconds": 0.1,
                     "resampled": False, "mixed_down": False}


def test_a_24_bit_stereo_wav_at_another_rate_becomes_one_channel_at_48k():
    left, right = _tone(0.1, 440, 0.6, 44_100), _tone(0.1, 440, 0.2, 44_100)
    audio, facts = sp.import_wav(_wav(np.stack([left, right], axis=1), 44_100, width=3, channels=2))
    assert audio.ndim == 1 and audio.size == 4_800
    assert facts["resampled"] and facts["mixed_down"] and (facts["bits"], facts["channels"]) == (24, 2)
    assert np.allclose(audio[500:4_300], _tone(0.1, 440, 0.4)[500:4_300], atol=5e-3)     # the two averaged
    negative, _ = sp.import_wav(_wav(np.full(480, -0.5), width=3))
    assert np.allclose(negative[100:300], -0.5, atol=1e-3)                               # 24-bit sign is kept


@pytest.mark.parametrize("data, said", [
    (b"not a wav at all", "not a WAV this can read"),
    (b"", "not a WAV this can read"),
    (_wav(_tone(0.01), width=4, fmt=3), "not a WAV this can read"),          # 32-bit float
    (_wav(_tone(0.01), width=1), "8-bit"),
    (_wav([]), "empty"),
    (_wav(np.zeros(480)), "silent"),
    (_wav(np.full(8_000 * 31, 0.1), rate=8_000), "up to 30 seconds"),
])
def test_a_file_that_cannot_be_read_is_refused_in_plain_words(data, said):
    with pytest.raises(sp.SoundProblem, match=said):
        sp.import_wav(data)


# ----------------------------------------------------------------------------- voice lines

def speech(seconds=2.0, level=0.1, pause=0.5):
    """Something shaped like talking: bursts of tone with silence between them."""
    word, gap = sine(0.3, 180.0, level=level), np.zeros(int(pause * sp.RATE), np.float32)
    count = max(1, int(seconds / 0.3))
    return np.concatenate([part for _ in range(count) for part in (word, gap)])


def test_a_voice_line_is_measured_while_speaking_not_through_its_pauses():
    talking, spaced = speech(pause=0.0), speech(pause=0.5)
    assert sp.speech_level_db(talking) == pytest.approx(sp.speech_level_db(spaced), abs=0.3)
    # the plain average is dragged down by the pauses; that is why it is not used for speech
    assert sp.loudness_db(spaced) < sp.speech_level_db(spaced) - 3
    assert sp.speech_level_db(sine(0.2)) is None                       # too short to measure
    assert sp.speech_level_db(np.zeros(sp.RATE, np.float32)) is None


@pytest.mark.parametrize("level", [0.02, 0.1, 0.4])
def test_lines_come_out_at_the_same_level_however_loud_the_engine_made_them(level):
    line, facts = sp.level_voice(speech(level=level))
    assert sp.speech_level_db(line) == pytest.approx(sp.VOICE_LEVEL_DB, abs=0.2)
    assert facts["target"] == -16.0 and facts["set_by"] == "level" and not facts["held_by_peak"]
    assert facts["level"] == pytest.approx(-16.0, abs=0.2) and sp.peak_of(line) <= sp.PEAK


def test_a_shout_stays_louder_than_a_plain_line_and_a_whisper_quieter():
    raw = speech()
    levels = {how: sp.speech_level_db(sp.level_voice(raw, how)[0])
              for how in ("whisper", "sad", "calm", None, "angry", "excited", "shout")}
    assert levels["whisper"] < levels["calm"] < levels[None] < levels["angry"] < levels["shout"]
    assert levels["shout"] - levels["whisper"] == pytest.approx(13.0, abs=0.3)
    assert sp.voice_target_db("Shout") == -12.0 and sp.voice_target_db("like a tired old wizard") == -16.0


def test_a_line_that_would_clip_is_held_at_the_peak_instead():
    spiky = speech(level=0.05)
    spiky[100] = 0.9                                     # one sharp sample, far over the rest
    line, facts = sp.level_voice(spiky, "shout")
    assert facts["held_by_peak"] is True and sp.peak_of(line) == pytest.approx(sp.PEAK, abs=1e-3)
    assert sp.speech_level_db(line) < sp.voice_target_db("shout")


def test_a_very_short_line_is_set_by_its_peak_and_silence_is_left_alone():
    line, facts = sp.level_voice(sine(0.2, level=0.05))
    assert facts["set_by"] == "peak" and facts["measured"] is None and facts["level"] is None
    assert sp.peak_of(line) == pytest.approx(10 ** ((-16.0 + 12.0) / 20), rel=0.01)
    quiet, _ = sp.level_voice(sine(0.2, level=0.05), "whisper")
    assert sp.peak_of(quiet) < sp.peak_of(line)
    silent, facts = sp.level_voice(np.zeros(4800, np.float32))
    assert not silent.any() and facts["gain_db"] == 0.0


def test_a_fresh_voice_line_is_cleaned_in_one_go():
    raw = np.concatenate([np.zeros(2400, np.float32), speech(level=0.05)[: 24_000 * 2 // 2 * 2], np.zeros(2400, np.float32)])
    line, post = sp.clean_voice(raw, 24_000, "whisper")
    assert line.dtype == np.float32 and post["trimmed"] is True and post["loudness"]["target"] == -25.0
    assert sp.speech_level_db(line) == pytest.approx(-25.0, abs=0.3)
    assert post["gain_db"] == post["loudness"]["gain_db"] and {"trim", "fade_ms", "peak", "tail_s"} <= set(post)
    assert sp.inspect(line, sp.RATE)["seconds"] > 0.5
    with pytest.raises(sp.SoundProblem, match="empty"):
        sp.clean_voice(np.zeros(0, np.float32), 24_000)
    with pytest.raises(sp.SoundProblem, match="broken"):
        sp.clean_voice(np.array([0.1, np.nan], np.float32), 24_000)


def test_lines_are_joined_with_a_gap_and_keep_their_own_levels():
    loud, quiet = sp.level_voice(speech(1.0), "shout")[0], sp.level_voice(speech(1.0), "whisper")[0]
    joined, facts = sp.join_lines([loud, quiet], gap_ms=250)
    gap = int(0.25 * sp.RATE)
    assert joined.size == loud.size + gap + quiet.size and facts["seconds"] == round(joined.size / sp.RATE, 3)
    assert facts["starts_ms"] == [0.0, round((loud.size + gap) * 1000 / sp.RATE, 1)]
    assert not joined[loud.size:loud.size + gap].any()                  # the gap is silence
    np.testing.assert_array_equal(joined[:loud.size], loud)             # nothing was turned up or down
    np.testing.assert_array_equal(joined[-quiet.size:], quiet)
    with pytest.raises(sp.SoundProblem, match="no line"):
        sp.join_lines([])


def stressed(bang=0.4):
    """Speech-shaped: quiet words, and one short syllable far louder than the rest. Like real
    speech, its loudest sample sits well over its level (about 19 dB here)."""
    word, gap = sine(0.3, 180.0, level=0.05), np.zeros(int(0.1 * sp.RATE), np.float32)
    return np.concatenate([sine(0.03, 180.0, level=bang), gap] + [word, gap] * 20)


def crest_db(sound):
    return 20 * math.log10(sp.peak_of(sound)) - sp.speech_level_db(sound)


def test_pressing_peaks_never_passes_the_ceiling_and_leaves_the_rest_alone():
    quiet, loud = sine(0.5, level=0.3), sine(0.2, level=1.5)
    sound = np.concatenate([quiet, loud, quiet])
    pressed = sp.press_peaks(sound, 0.98)
    assert sp.peak_of(pressed) <= 0.98 + 1e-6 and pressed.size == sound.size
    far = int(0.4 * sp.RATE)                                   # well away from the loud part: untouched
    np.testing.assert_array_equal(pressed[:far], sound[:far])
    np.testing.assert_array_equal(pressed[-far:], sound[-far:])
    assert np.abs(np.diff(pressed)).max() < np.abs(np.diff(loud)).max() * 1.01      # no click where it starts
    untouched = sp.press_peaks(quiet, 0.98)
    np.testing.assert_array_equal(untouched, quiet)


def test_a_peaky_line_still_reaches_its_level_by_pressing_its_peaks():
    """Real speech peaks 12 to 18 dB over its level, so a plain turn-up would stop short."""
    raw = stressed()
    assert 17 < crest_db(raw) < 21                              # a plain line would peak 1 to 5 dB over full scale
    line, facts = sp.level_voice(raw)
    assert sp.speech_level_db(line) == pytest.approx(-16.0, abs=0.3) and facts["level"] == sp.speech_level_db(line)
    assert sp.peak_of(line) <= sp.PEAK + 1e-6 and not facts["held_by_peak"]
    assert 1.0 < facts["pressed_db"] <= sp.VOICE_PRESS_DB
    # the quiet words were only turned up, by the same amount: nothing but the loud syllable was pressed
    tail, was = line[-int(1.0 * sp.RATE):], raw[-int(1.0 * sp.RATE):]
    np.testing.assert_allclose(tail, was * 10 ** (facts["gain_db"] / 20), rtol=1e-3, atol=1e-5)


def test_peaks_are_pressed_by_six_decibels_at_most_and_then_the_line_is_held():
    raw = stressed()
    line, facts = sp.level_voice(raw, "shout")
    assert facts["held_by_peak"] is True and facts["pressed_db"] == pytest.approx(sp.VOICE_PRESS_DB, abs=0.05)
    assert sp.peak_of(line) <= sp.PEAK + 1e-6
    # short of a shout's mark, and still louder than the same line said plainly
    assert sp.speech_level_db(sp.level_voice(raw)[0]) < sp.speech_level_db(line) < sp.voice_target_db("shout")


# ------------------------------------------------------------------------- background loops

def noise(seconds, seed=1, level=0.2):
    return (level * np.random.default_rng(seed).standard_normal(int(seconds * sp.RATE))).astype(np.float32)


def rumble(seconds, seed=2):
    """Noise with most of its power low down, like wind or a storm: each sample close to the last."""
    white = np.random.default_rng(seed).standard_normal(int(seconds * sp.RATE))
    spectrum = np.fft.rfft(white)
    spectrum /= np.maximum(1.0, np.arange(spectrum.size) / 200.0)
    out = np.fft.irfft(spectrum, n=white.size)
    return (0.3 * out / np.abs(out).max()).astype(np.float32)


def block_levels(sound, ms=100.0):
    size = int(sp.RATE * ms / 1000.0)
    count = sound.size // size
    power = (sound[: count * size].astype(np.float64).reshape(count, size) ** 2).mean(axis=1)
    return 10.0 * np.log10(power)


def test_the_blend_is_a_tenth_of_the_loop_within_its_limits():
    assert [sp.loop_blend_ms(s) for s in (1, 4, 8, 10, 30)] == [250.0, 400.0, 800.0, 1000.0, 1000.0]


def test_the_engine_is_asked_for_the_loop_plus_what_the_join_needs():
    assert sp.seconds_for_loop(8.0, 30.0) == 9.5            # 8 + 0.5 left out + 0.8 blend + 0.15 to move, rounded up
    assert sp.seconds_for_loop(4.0, 30.0) == 5.1
    assert sp.seconds_for_loop(10.0, 30.0) == 11.7
    assert sp.seconds_for_loop(30.0, 30.0) == 30.0          # never past the engine's most


@pytest.mark.parametrize("make", [noise, rumble, lambda s: sine(s, 110.0, level=0.4)])
def test_a_loop_has_no_step_where_it_wraps(make):
    source = make(9.5)
    loop, made = sp.make_loop(source, 8.0)
    seam = sp.seam_report(loop)
    assert seam["step_ratio"] < 4.0 and seam["slope_ratio"] < 4.0      # like any two neighbours; a click is 10s
    swings = np.abs(np.diff(block_levels(source)))                     # wind swells on its own: no more than that
    assert abs(seam["level_jump_db"]) <= max(1.0, swings.max())
    assert seam["usual_level_jump_db"] == pytest.approx(np.median(np.abs(np.diff(block_levels(loop)))), abs=0.01)
    assert made["start_sample"] == 0 and made["end_sample"] == loop.size - 1
    assert made["crossfade_ms"] == 800.0 and abs(loop.size / sp.RATE - 8.0) <= 0.15


def test_the_wrap_is_the_engines_own_next_sample():
    """The last sample is followed by the first exactly as the source had them: that is why
    there is no click, whatever the sound."""
    source = rumble(9.5)
    loop, made = sp.make_loop(source, 8.0)
    start = int(made["taken_from_s"] * sp.RATE)
    assert made["taken_from_s"] == 0.5
    assert loop[-1] == source[start + loop.size - 1] and loop[0] == pytest.approx(source[start + loop.size], abs=1e-6)
    blend = int(0.8 * sp.RATE)
    np.testing.assert_array_equal(loop[blend:], source[start + blend:start + loop.size])   # past the blend: untouched


def test_a_plain_cut_does_click_and_the_report_says_so():
    cut = rumble(9.5)[: 8 * sp.RATE]
    assert sp.seam_report(cut)["step_ratio"] > 10.0


@pytest.mark.parametrize("make", [noise, rumble, lambda s: sine(s, 110.0, level=0.4),
                                  lambda s: sine(s, 60.0, level=0.3) + noise(s, level=0.05)])
def test_loudness_stays_even_through_the_blend_and_across_the_wrap(make):
    source = make(9.5)
    loop, _ = sp.make_loop(source, 8.0)
    levels, own = block_levels(np.tile(loop, 2)), block_levels(source)     # twice round: the wrap is in the middle
    # no dip and no bump, for unlike sounds and alike ones: it moves no more than the source does by itself
    assert levels.max() - levels.min() < (own.max() - own.min()) + 1.0
    assert levels.min() > own.min() - 1.0 and levels.max() < own.max() + 1.0


def test_equal_power_keeps_two_unlike_sounds_level_and_a_plain_fade_would_dip():
    source = noise(6.0)
    blend = sp.RATE
    loop = sp.blend_loop(source, 0, 4 * sp.RATE, blend, alike=0.0)
    middle = loop[blend // 2 - 2400:blend // 2 + 2400].astype(np.float64)
    assert 10 * math.log10(np.mean(middle ** 2) / np.mean(source.astype(np.float64) ** 2)) == pytest.approx(0.0, abs=0.5)
    ramp = np.arange(blend) / blend                          # the straight fade, for contrast: 3 dB down midway
    straight = source[:blend] * ramp + source[4 * sp.RATE:5 * sp.RATE] * (1 - ramp)
    dip = straight[blend // 2 - 2400:blend // 2 + 2400].astype(np.float64)
    assert 10 * math.log10(np.mean(dip ** 2) / np.mean(source.astype(np.float64) ** 2)) < -2.0


def test_two_alike_ends_are_not_made_louder_by_the_blend():
    tone = sine(6.0, 100.0, level=0.4)                       # 480 samples a wave: 4 s is a whole number of them
    even = sp.blend_loop(tone, 0, 4 * sp.RATE, sp.RATE, alike=1.0)
    np.testing.assert_allclose(even, tone[: 4 * sp.RATE], atol=1e-4)      # the same wave, untouched
    bump = sp.blend_loop(tone, 0, 4 * sp.RATE, sp.RATE, alike=0.0)
    assert sp.peak_of(bump) > 0.4 * 1.3                      # what equal power alone would do: 3 dB up


def test_a_hum_gets_the_nearest_length_that_lines_its_waves_up():
    tone = sine(6.0, 100.0, level=0.4)                       # one wave is 480 samples
    asked = int(4.003 * sp.RATE)                             # 144 samples past a whole wave
    length, alike = sp.pick_loop_length(tone, 0, asked, int(0.4 * sp.RATE), int(0.15 * sp.RATE))
    assert length % 480 == 0 and abs(length - asked) <= 240 and alike > 0.99
    loop, made = sp.make_loop(tone, 4.003)
    assert loop.size % 480 == 0 and made["alike"] > 0.99


def test_rain_keeps_the_length_asked():
    source = noise(6.0)
    length, alike = sp.pick_loop_length(source, 0, 4 * sp.RATE, int(0.4 * sp.RATE), int(0.15 * sp.RATE))
    assert length == 4 * sp.RATE and abs(alike) < 0.1
    loop, made = sp.make_loop(source, 4.0)
    assert loop.size == 4 * sp.RATE and made["seconds"] == 4.0


def test_dead_air_and_the_swell_at_the_start_stay_out_of_the_loop():
    source = padded(noise(9.5), before=4800, after=4800)
    loop, made = sp.make_loop(source, 8.0)
    assert made["taken_from_s"] == 0.5 and np.abs(loop).min() < 1e-3 < np.abs(loop[:100]).max()
    assert block_levels(loop).min() > -30                    # no silent stretch inside


def test_too_little_sound_makes_a_shorter_loop_and_far_too_little_is_refused():
    loop, made = sp.make_loop(noise(6.0), 8.0)               # the engine stopped early
    assert 5.0 <= made["seconds"] < 6.0 and made["end_sample"] == loop.size - 1
    assert sp.seam_report(loop)["step_ratio"] < 4.0
    with pytest.raises(sp.SoundProblem, match="too soon"):
        sp.make_loop(noise(1.5), 8.0)
    with pytest.raises(sp.SoundProblem, match="too soon"):
        sp.make_loop(padded(noise(1.0), 48_000, 6 * 48_000), 4.0)


def test_a_loop_at_the_engines_limit_comes_out_a_little_shorter():
    _, made = sp.make_loop(noise(30.0), 30.0)
    assert made["seconds"] == pytest.approx(29.0, abs=0.01)  # 30 s made, a second of it blended in


def test_a_background_is_set_by_its_average_level_not_its_loudest_sample():
    for level in (0.9, 0.03):                                # a wall of noise, and a whisper of one
        out, facts = sp.level_background(noise(4.0, level=level / 3))
        assert sp.loudness_db(out) == pytest.approx(-20.0, abs=0.05)
        assert facts["target"] == -20.0 and facts["measured"] == pytest.approx(-20.0, abs=0.05)
        assert facts["held_by_peak"] is False and facts["peak"] == pytest.approx(sp.peak_of(out), abs=1e-4)
    loud, facts = sp.level_background(noise(4.0, level=0.3))
    assert facts["gain_db"] < 0 and sp.peak_of(loud) < 0.6   # an effect would have been pushed to 0.98


def test_a_spiky_background_is_held_under_its_mark_and_never_clipped():
    spiky = noise(4.0, level=0.001)
    spiky[1000] = 0.5                                        # one thunderclap
    out, facts = sp.level_background(spiky)
    assert facts["held_by_peak"] is True and sp.peak_of(out) == pytest.approx(0.98, abs=1e-3)
    assert facts["measured"] < -20.0


def test_silence_is_left_for_the_check_to_refuse_and_hiss_is_not_blown_up():
    out, facts = sp.level_background(np.zeros(4800, np.float32))
    assert facts["gain_db"] == 0.0 and facts["measured"] is None and not out.any()
    _, facts = sp.level_background(noise(1.0, level=1e-4))
    assert facts["gain_db"] == sp.MAX_BOOST_DB


def test_a_loop_is_cleaned_without_fades_or_a_tail():
    stereo = np.stack([rumble(9.5), rumble(9.5, seed=5)])    # (channels, frames), as an engine answers
    loop, post = sp.clean_loop(stereo, sp.RATE, 8.0, channels_first=True)
    assert post["fade_ms"] == 0.0 and post["fade_out_ms"] == 0.0 and post["tail_s"] == 0.0
    assert post["loop"] == {"start_sample": 0, "end_sample": loop.size - 1, "crossfade_ms": 800.0}
    assert post["loudness"]["target"] == -20.0 and sp.loudness_db(loop) == pytest.approx(-20.0, abs=0.05)
    assert post["join"]["step_ratio"] < 4.0 and abs(loop[0]) > 1e-4 and abs(loop[-1]) > 1e-4   # not faded to zero
    sp.inspect(loop, sp.RATE)


def test_a_steady_effect_still_gets_its_fade_and_its_peak():
    """Loops take their own route: the effect path is as it was."""
    sound, post = sp.clean(noise(3.0), sp.RATE, asked_seconds=1.5)
    assert sp.peak_of(sound) == pytest.approx(0.98, abs=1e-3) and post["fade_out_ms"] == sp.LONG_FADE_MS
    assert abs(sound[-1]) < 1e-3 and "loop" not in post and sound.size == int(1.5 * sp.RATE)
    assert sp.voice_target_db() == -16.0 and sp.voice_target_db("shout") == -12.0


def test_broken_or_empty_audio_is_refused_for_a_loop_too():
    with pytest.raises(sp.SoundProblem, match="empty"):
        sp.clean_loop(np.zeros(0, np.float32), sp.RATE, 4.0)
    bad = noise(6.0)
    bad[10] = np.nan
    with pytest.raises(sp.SoundProblem, match="broken"):
        sp.clean_loop(bad, sp.RATE, 4.0)


def test_loop_points_are_written_into_the_wav_and_read_back(tmp_path):
    loop, post = sp.clean_loop(rumble(5.1), sp.RATE, 4.0)
    points = (post["loop"]["start_sample"], post["loop"]["end_sample"])
    wav = sp.write_wav(tmp_path / "loop.wav", loop, loop=points)
    assert sp.read_loop(wav) == {"start_sample": 0, "end_sample": loop.size - 1}
    assert sp.read_loop(wav.read_bytes()) == sp.read_loop(wav)
    raw = wav.read_bytes()
    assert int.from_bytes(raw[4:8], "little") == len(raw) - 8          # the header still tells the truth
    back, rate = sp.read_wav(wav)                                      # and it is still an ordinary WAV
    assert rate == sp.RATE and back.size == loop.size
    assert sp.inspect_wav(wav)["frames"] == loop.size
    assert sp.seam_report(back)["step_ratio"] < 4.0                    # 16 bits do not open the join
    again, facts = sp.import_wav(raw)
    assert again.size == loop.size and facts["bits"] == 16


def test_the_loop_part_of_the_file_is_what_samplers_expect():
    raw = sp.wav_bytes(sine(0.1), loop=(10, 4000))
    at = raw.index(b"smpl")
    assert at > raw.index(b"data") and int.from_bytes(raw[at + 4:at + 8], "little") == 60
    fields = np.frombuffer(raw[at + 8:at + 68], dtype="<u4").tolist()
    assert fields[2] == 20833 and fields[3] == 60 and fields[7] == 1   # 1e9 / 48000 ns a sample; middle C; one loop
    assert fields[9:] == [0, 0, 10, 4000, 0, 0]                        # forward, from 10 to 4000, for ever


def test_a_wav_with_no_loop_says_none_and_a_bad_loop_is_refused(tmp_path):
    assert sp.read_loop(sp.wav_bytes(sine(0.1))) is None
    assert sp.wav_bytes(sine(0.1)) == sp.wav_bytes(sine(0.1), loop=None)
    for bad in ((0, 4800), (-1, 100), (200, 100)):
        with pytest.raises(sp.SoundProblem, match="inside the sound"):
            sp.wav_bytes(sine(0.1), loop=bad)
    with pytest.raises(sp.SoundProblem, match="not a WAV"):
        sp.read_loop(b"nope")


# ------------------------------------------------------------------ music


def tune(seconds=6.0, level=0.3, seed=3):
    """Two channels that differ: a chord on the left, the same with a fifth on the right."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * sp.RATE)) / sp.RATE
    left = np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 277 * t)
    right = np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 330 * t)
    both = np.stack([left, right], axis=1) + 0.05 * rng.standard_normal((t.size, 2))
    return (level * both / np.abs(both).max()).astype(np.float32)


def test_music_keeps_both_channels_and_sits_at_its_average_level():
    raw = tune()
    music, post = sp.clean_music(raw.T, sp.RATE, channels_first=True)
    assert music.shape == raw.shape and post["channels"] == 2
    assert not np.allclose(music[:, 0], music[:, 1])              # still two different channels
    assert post["loudness"]["target"] == sp.MUSIC_LEVEL_DB == -18.0
    assert sp.loudness_db(music) == pytest.approx(-18.0, abs=0.6)   # the fade out costs a little
    assert sp.peak_of(music) <= sp.PEAK + 1e-6
    facts = sp.inspect(music, sp.RATE, want_channels=2)
    assert facts["channels"] == 2 and facts["seconds"] == pytest.approx(6.0, abs=0.01)


def test_music_that_cannot_reach_its_level_without_clipping_is_held_under_it():
    spiky = tune(level=0.05)
    spiky[1000] = [1.0, -1.0]                                      # one loud click in a quiet tune
    music, post = sp.clean_music(spiky, sp.RATE)
    assert post["loudness"]["held_by_peak"] is True
    assert sp.peak_of(music) == pytest.approx(sp.PEAK, abs=1e-3)
    assert sp.loudness_db(music) < -18.0


def test_a_tune_still_playing_where_it_stops_fades_out_and_one_that_ended_does_not():
    cut_off, post = sp.clean_music(tune(8.0), sp.RATE)
    assert post["fade_out_ms"] == sp.MUSIC_FADE_OUT_MS and post["ended"].startswith("faded out")
    last = cut_off[-sp.RATE // 10:]
    assert sp.loudness_db(last) < sp.loudness_db(cut_off) - 20.0     # it dies away
    assert abs(float(cut_off[-1, 0])) < 1e-3 and abs(float(cut_off[0, 0])) < 1e-3

    ending = tune(8.0)
    ending *= np.linspace(1.0, 0.0, ending.shape[0], dtype=np.float32)[:, None] ** 4      # its own ending
    _, post = sp.clean_music(ending, sp.RATE)
    assert post["ended"] == "by itself" and post["fade_out_ms"] == sp.TAIL_FADE_MS


def test_dead_air_round_a_tune_is_cut_with_the_channels_kept_in_step():
    raw = tune(3.0)
    quiet = np.zeros((4800, 2), np.float32)
    left_late = np.concatenate([quiet, raw, quiet])
    left_late[:6000, 0] = 0.0                                      # the left channel starts later than the right
    trimmed, cut = sp.trim_frames(left_late)
    assert cut is True and trimmed.shape[0] == pytest.approx(raw.shape[0], abs=60)
    assert np.all(trimmed[:1000, 0] == 0.0) and np.any(trimmed[:1000, 1] != 0.0)


def test_one_channel_music_stays_one_row_and_broken_music_is_refused():
    music, post = sp.clean_music(sine(3.0), sp.RATE)
    assert music.ndim == 1 and post["channels"] == 1
    with pytest.raises(sp.SoundProblem, match="empty"):
        sp.clean_music(np.zeros((0, 2), np.float32), sp.RATE)
    bad = tune(1.0)
    bad[5, 1] = np.nan
    with pytest.raises(sp.SoundProblem, match="broken"):
        sp.clean_music(bad, sp.RATE)


def test_music_at_another_rate_comes_out_at_48_khz():
    t = np.arange(44_100 * 2) / 44_100
    raw = np.stack([np.sin(2 * np.pi * 440 * t), np.sin(2 * np.pi * 660 * t)], axis=1).astype(np.float32) * 0.3
    music, _ = sp.clean_music(raw, 44_100)
    assert music.shape[1] == 2 and music.shape[0] == pytest.approx(2 * sp.RATE, abs=200)


def test_a_music_loop_joins_with_no_click_in_either_channel():
    asked = 8.0
    raw = tune(sp.seconds_for_music_loop(asked, 600.0))
    loop, post = sp.clean_music_loop(raw.T, sp.RATE, asked, channels_first=True)
    assert loop.ndim == 2 and loop.shape[1] == 2 and post["channels"] == 2
    assert loop.shape[0] / sp.RATE == pytest.approx(asked, abs=sp.MUSIC_LOOP_SEARCH_MS / 1000.0 + 0.01)
    assert post["loop"] == {"start_sample": 0, "end_sample": loop.shape[0] - 1, "crossfade_ms": 800.0}
    assert post["join"]["taken_from_s"] == sp.MUSIC_LOOP_LEAD_S       # the lead-in is left out
    for channel in (0, 1):
        assert sp.seam_report(loop[:, channel])["step_ratio"] < 4.0
    assert post["join"]["step_ratio"] == max(sp.seam_report(loop[:, c])["step_ratio"] for c in (0, 1))
    assert post["fade_ms"] == 0.0 and post["fade_out_ms"] == 0.0      # nothing done to its ends
    assert sp.loudness_db(loop) == pytest.approx(sp.MUSIC_LEVEL_DB, abs=0.1)


def test_a_music_loop_is_written_stereo_with_its_loop_in_the_file(tmp_path):
    loop, post = sp.clean_music_loop(tune(12.0), sp.RATE, 8.0)
    points = (post["loop"]["start_sample"], post["loop"]["end_sample"])
    wav = sp.write_wav(tmp_path / "sound.wav", loop, loop=points)
    assert sp.read_loop(wav) == {"start_sample": points[0], "end_sample": points[1]}
    back, rate = sp.read_wav(wav)
    assert rate == sp.RATE and back.shape == loop.shape
    assert sp.inspect_wav(wav, want_channels=2)["channels"] == 2
    assert len(sp.wave_bars(loop)) == 60


def test_how_much_music_a_loop_asks_the_engine_for():
    assert sp.seconds_for_music_loop(20.0, 600.0) == 28.5            # 20 + 2 lead + 1 blend + 0.5 search + a quarter
    assert sp.seconds_for_music_loop(8.0, 600.0) == 13.3
    assert sp.seconds_for_music_loop(239.0, 240.0) == 240.0
