"""Tests for image_to_3dlab/sound_post.py: the clean-up between the engine and the WAV.

All on made-up sounds (a sine, noise, silence), so nothing here needs a model or an audio
package. The functions are imported, never re-implemented.
"""

from __future__ import annotations

import hashlib

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
    assert post == {"trim": True, "trimmed": True, "fade_ms": 5.0, "peak": 0.98}
    assert sp.inspect(sound, sp.RATE)["channels"] == 1


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
