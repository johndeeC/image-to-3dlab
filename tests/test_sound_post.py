"""Tests for image_to_3dlab/sound_post.py: the clean-up between the engine and the WAV.

All on made-up sounds (a sine, noise, silence), so nothing here needs a model or an audio
package. The functions are imported, never re-implemented.
"""

from __future__ import annotations

import hashlib
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


@pytest.mark.parametrize("asked,most,want", [(1.0, 30.0, 1.5), (0.5, 30.0, 1.0), (4.0, 30.0, 6.0),
                                             (25.0, 30.0, 30.0), (30.0, 30.0, 30.0), (1.5, 30.0, 2.25)])
def test_the_engine_is_asked_for_room_for_a_tail(asked, most, want):
    """Half as long again, at least half a second more, never past the engine's most."""
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
    assert kept.size == raw.size and tail_s == pytest.approx(0.5) and fade_out == sp.TAIL_FADE_MS
    sound, post = sp.clean(raw, sp.RATE, asked_seconds=1.0)
    assert post["tail_s"] == pytest.approx(0.5) and post["fade_out_ms"] == sp.TAIL_FADE_MS
    assert abs(sound.size - raw.size) <= 2 and sound[-1] == 0.0
    frames = int(sp.TAIL_FADE_MS * sp.RATE / 1000)
    half_way = np.abs(sound[-frames:-frames // 2]).max() / np.abs(sound[-2 * frames:-frames]).max()
    assert 0.3 < half_way < 1.0                          # a slope, not a cliff


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
    assert kept.size == sp.RATE and tail_s == 0.0 and fade_out == sp.TAIL_FADE_MS


def test_with_no_room_a_ringing_sound_still_gets_the_longer_fade():
    kept, tail_s, fade_out = sp.keep_tail(ring(1.0), 1.0)
    assert kept.size == sp.RATE and tail_s == 0.0 and fade_out == sp.TAIL_FADE_MS


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
