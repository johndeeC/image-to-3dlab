"""Clean-up for a generated sound: the maths between the engine and the WAV on disk.

A raw sound from a model has dead air at both ends, may click where it starts and stops,
can peak past what a 16-bit file holds or come out far too quiet, and may still be ringing
where it was cut. `clean` fixes those in one fixed order:

    one channel -> 48 kHz -> keep the tail -> trim the silence -> fades -> peak to 0.98

Two rules for volume. A **fresh** sound is brought to the standard peak, up or down
(`set_peak`), so four tries are as loud as each other. Once volumes mean something (a
variation set, a mix of layers) a sound is only ever turned down (`limit_peak`).

Inside, a sound is always **mono float32 at 48 kHz** (`RATE`). Each step is its own function
so later work reuses them: several layers are summed and then pass through `limit_peak`
once, as a whole; loudness is measured on that mix, never per layer.

Pure numpy and the standard library's `wave`, on purpose: the main environment pins
numpy below 2 and the sound engine's own environment may not, so nothing here leans on
either version, and no audio package is needed to read or write a file.

Variation sets live here too (`make_variations`): small, seeded changes of pitch and volume
to one sound, so a footstep heard twenty times does not sound copied. Pitch is changed by
resampling, the way a game engine does it, so **a higher take is also shorter and a lower one
longer**. And `play_order` is the shuffle a game uses: never the same take twice running.

Layers live here too (`mix_layers`): each layer is placed at its start time with its own volume
and pitch, they are summed, and the sum passes through `limit_peak` once. A layer is never
brought back up to full volume after its volume was chosen. `import_wav` reads a file someone
brings (16- or 24-bit WAV) into the same one channel at 48 kHz.

Voice lines have their own volume rule (`level_voice`, `clean_voice`): a line is set by how
loud it is **while someone is speaking**, about -16 dB, so lines said one after another sit
level. How a line is said moves that mark (`VOICE_DIRECTION_DB`): a whisper sits 9 dB under a
plain line and a shout 4 dB over, so a shout stays louder than a whisper. Nothing is ever
clipped. Speech has sharp peaks, so reaching the mark often means pressing the loudest
syllables down a little (`press_peaks`, by at most 6 dB); a line that would need more than
that is held under its mark instead, and its record says so.

Background loops take their own route (`clean_loop`), because a loop must not be faded at
its ends or have a tail kept: either would be heard every time it comes round. The engine is
asked for a little more than the loop's length; that extra is blended into the loop's first
part (`make_loop`), so its last sample runs straight into its first as the engine made them.
A loop is set by its **average** level, about -20 dB (`level_background`), not its loudest
sample: it sits under the game, it does not sit on top of it. And its WAV says where the loop
is (`wav_bytes(..., loop=...)`, read back by `read_loop`), which game engines pick up.

Ported from the `postprocess_wav` / `inspect_wav` pair in our older ai-sfx project (trim at
0.3% of the peak, 5 ms fades, peak 0.98, 16-bit, refuse empty or broken audio).
"""

from __future__ import annotations

import hashlib
import io
import math
import random
import struct
import wave
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

RATE = 48_000            # every sound, inside and on disk
CHANNELS = 1             # effects are mono: a game places them in the world itself
PEAK = 0.98              # just under full scale, so nothing clips when it is written
FADE_MS = 5.0            # long enough to stop a click, too short to hear
TRIM_FRACTION = 0.003    # quieter than 0.3% of the loudest sample counts as silence
SILENCE_FLOOR = 1e-5     # ...and so does anything this small, however quiet the sound
# Below this peak a "sound" is the model returning nothing: about -80 dB.
SILENT_PEAK = 1e-4
# The most a fresh sound is turned up: 24 dB is about 16 times. Anything that needs more is
# mostly hiss, so it stays quiet rather than being blown up into noise.
MAX_BOOST_DB = 24.0

# A tail: what a struck thing does after the length asked for. The engine is asked for
# extra audio (as much again, at least a second: it makes 30 seconds anyway, so this is free),
# and a sound still ringing at the cut keeps going until it has died away.
TAIL_SHARE = 1.0         # extra room: this share of the length asked...
TAIL_MIN_S = 1.0         # ...and never less than this
TAIL_QUIET_DB = -45.0    # "died away": this far under the sound's loudest point...
TAIL_QUIET_MS = 30.0     # ...and staying there this long
TAIL_FADE_MS = 30.0      # the fade-out for a tail that did die away
LONG_FADE_MS = 300.0     # the most for a sound cut while still going: a slow, smooth fade...
LONG_FADE_SHARE = 0.4    # ...over at most this share of the tail (or of a steady sound)
STEADY_DB = 6.0          # a ring is at least this much quieter by the end of its extra room

# "How different" for a variation set: the most a take may move, up or down. A little keeps
# it the same thing ("same boot, different steps"); a lot makes it a different one. These
# sit around what game audio tools use for random pitch and volume on repeats.
HOW_DIFFERENT = {
    "a little": {"pitch_semitones": 1.0, "volume_db": 1.5},
    "some": {"pitch_semitones": 2.5, "volume_db": 3.0},
    "a lot": {"pitch_semitones": 4.0, "volume_db": 5.0},
}
CHANGES = ("pitch", "volume", "start")   # what may change; "start" is a few ms of timing
MIN_VARIATIONS, MAX_VARIATIONS, DEFAULT_VARIATIONS = 2, 12, 6
START_JITTER_MS = 8.0      # the most a take's start moves, earlier or later
NEAR_ZERO = 0.15           # a changed take moves at least this share of the range


# Layers: a sound built from a few parts, each made cleanly on its own, then stacked.
MAX_LAYERS = 8
LAYER_LIMITS = {               # outside these a layer is refused, never pulled inside
    "start_ms": (0.0, 10_000.0),
    "volume_db": (-40.0, 12.0),
    "pitch_semitones": (-12.0, 12.0),
}
LAYER_WORDS = {"start_ms": "Starts at", "volume_db": "Volume", "pitch_semitones": "Pitch"}
LAYER_UNITS = {"start_ms": "ms", "volume_db": "dB", "pitch_semitones": "semitones"}
MAX_IMPORT_SECONDS = 30.0      # a file brought in as a layer: an effect, not a song
IMPORT_BITS = (16, 24)


# Voice lines. Level is the average power of the parts where someone is speaking, in dB
# below full scale: pauses between words would otherwise drag the number down.
VOICE_LEVEL_DB = -16.0         # where a plainly said line sits
VOICE_DIRECTION_DB = {         # how far "How it's said" moves that, so a shout beats a whisper
    "whisper": -9.0, "calm": -3.0, "sad": -3.0, "angry": 2.0, "excited": 2.0, "shout": 4.0,
}
VOICE_BLOCK_MS = 50.0          # the line is measured in pieces this long...
VOICE_GATE_DB = 30.0           # ...and pieces this far under the loudest one are pauses
VOICE_SHORT_S = 0.4            # less speech than this is too little to measure: set by peak
VOICE_CREST_DB = 12.0          # speech's loudest sample sits about this far over its level
VOICE_PRESS_DB = 6.0           # the most the loudest syllables are pressed down to reach the mark
VOICE_PRESS_MS = 5.0           # ...in pieces this long, each looking one piece either side


# Background loops. The join: the engine makes more than the loop's length, and that extra
# is blended into the loop's first part, so the end runs into the start with no click.
LOOP_LEVEL_DB = -20.0          # a background's average level, dB below full scale
LOOP_BLEND_SHARE = 0.10        # the blend is this share of the loop...
LOOP_BLEND_MIN_MS = 250.0      # ...never shorter than this (a short blend can be heard as a blip)
LOOP_BLEND_MAX_MS = 1000.0     # ...or longer than this
LOOP_LEAD_S = 0.5              # the engine's first half second is left out: sounds often swell in
LOOP_SEARCH_MS = 150.0         # the loop may come out this much longer or shorter, to join better
LOOP_MATCH = 0.3               # how alike the two ends must be (0 to 1) before the length moves
LOOP_SHORTEST_S = 2.0          # less sound than this cannot be made into a loop
SEAM_LEVEL_MS = 100.0          # the level either side of the join is measured over this long


class SoundProblem(ValueError):
    """A sound that must not be kept: empty, broken, silent, or the wrong shape."""


def to_mono(samples: Any, *, channels_first: bool = False) -> np.ndarray:
    """One channel, float32. Takes a plain run of samples or (frames, channels).

    `channels_first` is for engines that answer (channels, frames). Channels are averaged.
    """
    data = np.asarray(samples, dtype=np.float32)
    if data.ndim == 1:
        return data.copy()
    if data.ndim != 2:
        raise SoundProblem(f"a sound is one row of samples per channel, not {data.ndim} dimensions")
    if channels_first:
        data = data.T
    return data.mean(axis=1, dtype=np.float32) if data.shape[1] else data.reshape(-1)


def resample(samples: np.ndarray, rate: int, to: int = RATE) -> np.ndarray:
    """The same sound at another sample rate, by cutting or padding its spectrum.

    Exact for anything below both rates' limits, and it needs no filter design or extra
    package. A whole sound at once: fine for clips of seconds, not for streams.
    """
    data = np.asarray(samples, dtype=np.float32)
    if rate <= 0 or to <= 0:
        raise SoundProblem("a sample rate must be above zero")
    if rate == to or not data.size:
        return data.copy()
    return stretch_to(data, max(1, int(round(data.size * to / rate))))


def stretch_to(samples: np.ndarray, length: int) -> np.ndarray:
    """The same wave squeezed or stretched to `length` samples, by cutting or padding its
    spectrum. Squeezing drops what the shorter sound cannot hold, so nothing folds back."""
    data = np.asarray(samples, dtype=np.float32)
    if length < 1:
        raise SoundProblem("a sound needs at least one sample")
    if length == data.size or not data.size:
        return data.copy()
    spectrum = np.fft.rfft(data.astype(np.float64))
    resized = np.zeros(length // 2 + 1, dtype=np.complex128)
    kept = min(spectrum.size, resized.size)
    resized[:kept] = spectrum[:kept]
    return (np.fft.irfft(resized, n=length) * (length / data.size)).astype(np.float32)


def shift_pitch(samples: np.ndarray, semitones: float) -> np.ndarray:
    """Higher or lower by `semitones`, by resampling: played back at the same rate, a
    squeezed sound is higher. **Its length changes too**: up 12 is twice the pitch and half
    as long. That is what a game engine's pitch knob does, and why it sounds natural."""
    data = np.asarray(samples, dtype=np.float32)
    if not semitones or not data.size:
        return data.copy()
    return stretch_to(data, max(1, int(round(data.size / 2.0 ** (float(semitones) / 12.0)))))


def change_volume(samples: np.ndarray, db: float) -> np.ndarray:
    """Louder (above 0) or softer (below 0) by `db` decibels. 6 dB is about double."""
    return (np.asarray(samples, dtype=np.float32) * np.float32(10.0 ** (float(db) / 20.0))).astype(np.float32)


def shift_start(samples: np.ndarray, ms: float, rate: int = RATE) -> np.ndarray:
    """Start a touch later (above 0: that much silence first) or earlier (below 0: that much
    cut off the front). Never cuts more than half the sound."""
    data = np.asarray(samples, dtype=np.float32)
    frames = int(round(abs(float(ms)) * rate / 1000.0))
    if not frames or not data.size:
        return data.copy()
    if ms > 0:
        return np.concatenate([np.zeros(frames, np.float32), data])
    return data[min(frames, data.size // 2):].copy()


def variation_note(pitch_semitones: float = 0.0, volume_db: float = 0.0) -> str:
    """A take in plain words: "a bit higher, softer". What the studio shows beside it."""
    def word(value: float, small: float, big: float, up: str, down: str) -> str | None:
        size = abs(value)
        if size < small / 4:
            return None
        return ("a bit " if size < small else "much " if size >= big else "") + (up if value > 0 else down)

    said = [w for w in (word(pitch_semitones, 1.5, 3.0, "higher", "lower"),
                        word(volume_db, 2.0, 4.0, "louder", "softer")) if w]
    return ", ".join(said) or "nearly the same"


def _spread_out(rng: random.Random, count: int, most: float, digits: int) -> list[float]:
    """`count` numbers between -most and +most, one from each equal slice of that range, so
    they never bunch up; none closer to zero than NEAR_ZERO of the range; in shuffled order."""
    values = []
    for i in range(count):
        share = -1.0 + (i + rng.random()) * 2.0 / count           # -1 to 1, one per slice
        size = NEAR_ZERO + (1.0 - NEAR_ZERO) * abs(share)         # ...squeezed clear of zero
        values.append(round(math.copysign(size * most, share), digits))
    rng.shuffle(values)
    return values


def make_variations(audio: Any, rate: int, count: int = DEFAULT_VARIATIONS, spread: str = "some",
                    seed: int = 0, change: Any = ("pitch", "volume")) -> list[dict[str, Any]]:
    """`count` takes of one sound. Take 1 is the original; the rest move in pitch, volume
    or start time by up to what `spread` ("a little", "some", "a lot") allows.

    The same sound, numbers and `seed` always give the same takes, so a set can be rebuilt
    from its record. Each take is `{index, samples, pitch_semitones, volume_db, start_ms,
    gain_db, note}`. `volume_db` is how loud it is beside the original; `gain_db` is what was
    really applied, because if any take would pass the peak the **whole set** is turned down
    by the same amount. That keeps the differences, and nothing is ever turned up to fill.
    Counts outside 2 to 12 are refused, not pulled inside.
    """
    if isinstance(count, bool) or not isinstance(count, int) or not MIN_VARIATIONS <= count <= MAX_VARIATIONS:
        raise SoundProblem(f"make between {MIN_VARIATIONS} and {MAX_VARIATIONS} variations")
    if spread not in HOW_DIFFERENT:
        raise SoundProblem("how different is one of: " + ", ".join(HOW_DIFFERENT))
    change = {change} if isinstance(change, str) else set(change or ())
    if not change or change - set(CHANGES):
        raise SoundProblem("pick what changes: " + ", ".join(CHANGES))
    original = resample(to_mono(audio), rate, RATE)
    if not original.size or not np.isfinite(original).all() or peak_of(original) < SILENT_PEAK:
        raise SoundProblem("there is no sound to make variations of")
    limits, rng, extra = HOW_DIFFERENT[spread], random.Random(int(seed)), count - 1
    # every list is drawn whether or not it is used, so ticking Volume never moves the pitches
    pitches = _spread_out(rng, extra, limits["pitch_semitones"], 2)
    volumes = _spread_out(rng, extra, limits["volume_db"], 1)
    starts = [round(rng.uniform(-START_JITTER_MS, START_JITTER_MS), 1) for _ in range(extra)]
    takes = [{"index": 1, "samples": original, "pitch_semitones": 0.0, "volume_db": 0.0, "start_ms": 0.0}]
    for i in range(extra):
        pitch = pitches[i] if "pitch" in change else 0.0
        volume = volumes[i] if "volume" in change else 0.0
        start = starts[i] if "start" in change else 0.0
        samples = shift_start(shift_pitch(original, pitch), start)
        if pitch or start:
            samples = fade(samples)       # new ends: keep them click-free
        takes.append({"index": i + 2, "samples": change_volume(samples, volume),
                      "pitch_semitones": pitch, "volume_db": volume, "start_ms": start})
    loudest = max(peak_of(take["samples"]) for take in takes)
    down = PEAK / loudest if loudest > PEAK else 1.0
    for take in takes:
        if down != 1.0:
            take["samples"] = (take["samples"] * np.float32(down)).astype(np.float32)
        take["gain_db"] = round(take["volume_db"] + 20.0 * math.log10(down), 2)
        take["note"] = ("the original" if take["index"] == 1
                        else variation_note(take["pitch_semitones"], take["volume_db"]))
    return takes


def play_order(count: int, rounds: int = 8, seed: int | None = None) -> list[int]:
    """The order a game plays a set in: every take once in random order, then shuffled again,
    and **never the same take twice running**, not even where one shuffle meets the next or
    where the list ends and starts over. Takes are numbered from 0. Same seed, same order.
    """
    if count < 1 or rounds < 1:
        raise ValueError("a play order needs at least one sound and one round")
    if count == 1:
        return [0] * rounds
    rng, order = random.Random(seed), []
    for turn in range(rounds):
        bag = list(range(count))
        rng.shuffle(bag)
        if order and bag[0] == order[-1]:
            other = rng.randrange(1, count)
            bag[0], bag[other] = bag[other], bag[0]
        if turn == rounds - 1 and count > 2 and bag[-1] == (order or bag)[0]:
            other = rng.randrange(1, count - 1)       # not the first: that one is already safe
            bag[-1], bag[other] = bag[other], bag[-1]
        order += bag
    return order


def trim_silence(samples: np.ndarray, fraction: float = TRIM_FRACTION,
                 floor: float = SILENCE_FLOOR) -> tuple[np.ndarray, bool]:
    """Cut the dead air off both ends. Returns the sound and whether anything was cut.

    A sound that is silent all the way through comes back untouched: `inspect` refuses it.
    """
    data = np.asarray(samples, dtype=np.float32)
    if not data.size:
        return data, False
    level = np.abs(data)
    threshold = max(floor, float(level.max()) * fraction)
    active = np.flatnonzero(level > threshold)
    if not active.size:
        return data, False
    start, end = int(active[0]), int(active[-1]) + 1
    return data[start:end].copy(), start > 0 or end < data.size


def peak_of(samples: np.ndarray) -> float:
    data = np.asarray(samples)
    return float(np.max(np.abs(data))) if data.size else 0.0


def limit_peak(samples: np.ndarray, peak: float = PEAK) -> np.ndarray:
    """Turn the whole sound down until its loudest sample is `peak`. Never turns it up,
    and never clips: the shape of the sound is kept. A mix of layers calls this once."""
    data = np.asarray(samples, dtype=np.float32)
    loudest = peak_of(data)
    if loudest <= peak or loudest == 0.0:
        return data.copy()
    return (data * (peak / loudest)).astype(np.float32)


def set_peak(samples: np.ndarray, peak: float = PEAK,
             max_boost_db: float = MAX_BOOST_DB) -> tuple[np.ndarray, float]:
    """Bring a fresh sound's loudest sample to `peak`, **up or down**. Returns the sound and
    the decibels it moved. For a sound straight from the engine only: once volumes have been
    chosen, use `limit_peak`.

    It is never turned up by more than `max_boost_db`, and silence (under SILENT_PEAK) is
    left alone, so `inspect` still refuses it instead of keeping amplified hiss.
    """
    data = np.asarray(samples, dtype=np.float32)
    loudest = peak_of(data)
    if loudest < SILENT_PEAK or loudest == peak:
        return data.copy(), 0.0
    gain_db = min(20.0 * math.log10(peak / loudest), float(max_boost_db))
    return change_volume(data, gain_db), round(gain_db, 2)


def fade(samples: np.ndarray, rate: int = RATE, ms: float = FADE_MS,
         out_ms: float | None = None) -> np.ndarray:
    """A short ramp in and out, so the sound never starts or stops on a click. `out_ms` is
    for a fade-out of its own length; that one is a smooth curve (half a cosine), which is
    what a long fade needs to sound like dying away. Left out, both ends are `ms`, straight."""
    data = np.asarray(samples, dtype=np.float32).copy()
    for length, at_end in ((ms, False), (ms if out_ms is None else out_ms, True)):
        frames = min(int(rate * max(0.0, length) / 1000.0), data.size // 2)
        if frames > 0:
            ramp = np.linspace(0.0, 1.0, frames, dtype=np.float32)
            if at_end and out_ms is not None:
                ramp = (0.5 - 0.5 * np.cos(np.pi * ramp)).astype(np.float32)
            if at_end:
                data[-frames:] *= ramp[::-1]
            else:
                data[:frames] *= ramp
    return data


def seconds_with_tail(asked: float, most: float) -> float:
    """How long to ask the engine for, so there is room for a tail: twice as long, at
    least a second more, never past `most`. To a tenth of a second, as engines take it."""
    return round(min(float(most), float(asked) + max(TAIL_MIN_S, TAIL_SHARE * float(asked))), 1)


def keep_tail(samples: np.ndarray, asked_seconds: float, rate: int = RATE) -> tuple[np.ndarray, float, float]:
    """End a sound that was made longer than asked. Returns (the sound, seconds kept past
    the length asked, the fade-out in ms it should get).

    - Quiet at the length asked (45 dB under its loudest point): cut there, as always.
    - Still ringing: it runs on until it has been that quiet for 30 ms, then ends.
    - Never gets that quiet, but is at least 6 dB down by the end: all of it is kept.
    - Not dying away at all (rain, an engine): cut at the length asked. More is not a tail.

    A tail that died away gets a 30 ms fade-out. A sound cut while still going (the last two)
    gets a long one, never a hard cut: up to 300 ms, and no more than 40% of the tail kept
    or, for a steady sound, of the sound. Nothing is changed here but where the sound stops.
    """
    data = np.asarray(samples, dtype=np.float32)
    asked = min(int(round(float(asked_seconds) * rate)), data.size)
    if asked <= 0:
        return data.copy(), 0.0, FADE_MS
    head, level = data[:asked], np.abs(data)
    window = max(1, int(rate * TAIL_QUIET_MS / 1000.0))
    quiet = max(SILENCE_FLOOR, peak_of(head) * 10.0 ** (TAIL_QUIET_DB / 20.0))
    start = max(0, asked - window)
    at_cut = float(level[start:asked].max())
    if at_cut <= quiet:
        return head.copy(), 0.0, FADE_MS
    loud = np.flatnonzero(level[start:] > quiet) + start          # never empty: at_cut is in it
    gaps = np.diff(np.append(loud, data.size + window))           # the end counts if there is room
    ends = np.flatnonzero(gaps > window)
    if ends.size and int(loud[ends[0]]) + 1 + window <= data.size:
        end = int(loud[ends[0]]) + 1 + window
        return data[:end].copy(), round((end - asked) / rate, 3), TAIL_FADE_MS
    def long_fade(seconds: float) -> float:
        return round(max(TAIL_FADE_MS, min(LONG_FADE_MS, LONG_FADE_SHARE * seconds * 1000.0)), 1)

    dying = float(level[-window:].max()) <= at_cut * 10.0 ** (-STEADY_DB / 20.0)
    if data.size > asked and dying:
        tail = (data.size - asked) / rate
        return data.copy(), round(tail, 3), long_fade(tail)
    return head.copy(), 0.0, long_fade(asked / rate)


def clean(samples: Any, rate: int, *, trim: bool = True, fade_ms: float = FADE_MS,
          peak: float = PEAK, channels_first: bool = False,
          asked_seconds: float | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    """Everything a fresh sound needs, in order. Returns (mono float32 at 48 kHz, what was done).

    The second value is the sidecar's `post` block. Empty or broken audio is refused here,
    before any of the maths can spread a NaN through the whole clip.

    `asked_seconds` is for a sound made longer than asked, to leave room for a tail
    (`seconds_with_tail`): `keep_tail` then decides where it ends. `gain_db` in the answer is
    how far the volume moved, `tail_s` how much was kept past the length asked.
    """
    data = to_mono(samples, channels_first=channels_first)
    if not data.size:
        raise SoundProblem("the engine made an empty sound")
    if not np.isfinite(data).all():
        raise SoundProblem("the engine made a broken sound (it has values that are not numbers)")
    data = resample(data, rate, RATE)
    tail_s, fade_out_ms = 0.0, float(fade_ms)
    if asked_seconds is not None:
        data, tail_s, tail_fade = keep_tail(data, asked_seconds)
        fade_out_ms = max(fade_out_ms, tail_fade)
    trimmed = False
    if trim:
        data, trimmed = trim_silence(data)
    # fades first: a hit that starts on its loudest sample would lose some of it to the fade-in
    data = fade(data, RATE, fade_ms, fade_out_ms if fade_out_ms != fade_ms else None)
    data, gain_db = set_peak(data, peak)
    return data, {"trim": bool(trim), "trimmed": trimmed, "fade_ms": float(fade_ms), "peak": float(peak),
                  "gain_db": gain_db, "tail_s": tail_s, "fade_out_ms": fade_out_ms}


def inspect(samples: Any, rate: int, *, want_rate: int = RATE, want_channels: int = CHANNELS) -> dict[str, Any]:
    """Facts about a sound, or `SoundProblem` saying why it must not be kept.

    Checked: it has samples, every one is a real number, it is not silence, and it has the
    sample rate and channel count asked for.
    """
    data = np.asarray(samples, dtype=np.float32)
    channels = 1 if data.ndim == 1 else int(data.shape[1])
    frames = int(data.shape[0]) if data.ndim else 0
    if not data.size:
        raise SoundProblem("the sound is empty")
    if not np.isfinite(data).all():
        raise SoundProblem("the sound is broken (it has values that are not numbers)")
    peak = peak_of(data)
    if peak < SILENT_PEAK:
        raise SoundProblem("the sound is silent")
    if int(rate) != want_rate:
        raise SoundProblem(f"the sound is at {rate} Hz, not {want_rate}")
    if channels != want_channels:
        raise SoundProblem(f"the sound has {channels} channel{'s' if channels != 1 else ''}, not {want_channels}")
    return {"sample_rate": int(rate), "channels": channels, "frames": frames,
            "seconds": round(frames / rate, 3), "peak": round(peak, 4)}


def loop_blend_ms(seconds: float) -> float:
    """How long a loop's blend is: a tenth of the loop, from a quarter second to a second."""
    return min(LOOP_BLEND_MAX_MS, max(LOOP_BLEND_MIN_MS, LOOP_BLEND_SHARE * float(seconds) * 1000.0))


def seconds_for_loop(asked: float, most: float) -> float:
    """How long to ask the engine for, to make a loop `asked` seconds long: the loop, the part
    blended in, the start that is left out and room to move the join. Never past `most`; a
    loop near the engine's limit then comes out a little shorter. To a tenth of a second."""
    extra = LOOP_LEAD_S + (loop_blend_ms(asked) + LOOP_SEARCH_MS) / 1000.0
    return min(float(most), math.ceil(round((float(asked) + extra) * 10.0, 6)) / 10.0)


def pick_loop_length(samples: np.ndarray, start: int, asked: int, blend: int,
                     search: int) -> tuple[int, float]:
    """The loop's length in samples, near `asked`, and how alike its two ends are there (-1 to 1).

    A loop starting at `start` blends the `blend` samples that follow it into its own first
    `blend`. Those two pieces are compared at every length within `search` of the one asked.
    For a hum or a drone some lengths line the waves up; the closest such length is taken.
    For rain or wind none does (alike stays under `LOOP_MATCH`), and the length asked is kept.
    """
    data = np.asarray(samples, dtype=np.float64)
    low = max(2 * blend, asked - search)
    high = min(asked + search, data.size - start - blend)
    if high < low or not low <= asked <= high:
        raise SoundProblem("the sound is too short for a loop that long")
    head = data[start:start + blend]
    window = data[start + low:start + high + blend]
    size = 1 << (window.size + blend - 1).bit_length()
    across = np.fft.irfft(np.fft.rfft(window, size) * np.conj(np.fft.rfft(head, size)), size)[: high - low + 1]
    total = np.concatenate([[0.0], np.cumsum(window * window)])
    power = (total[blend:] - total[:-blend]) * float(head @ head)
    alike = across / np.sqrt(np.maximum(power, 1e-20))
    best = float(alike.max())
    if best < LOOP_MATCH:
        return asked, float(alike[asked - low])
    good = np.flatnonzero(alike >= best - 0.01)
    at = int(good[np.argmin(np.abs(good + low - asked))])
    while at + 1 < alike.size and alike[at + 1] > alike[at]:       # ...then up to the top of that rise
        at += 1
    while at > 0 and alike[at - 1] > alike[at]:
        at -= 1
    return at + low, float(alike[at])


def blend_loop(samples: np.ndarray, start: int, length: int, blend: int, alike: float = 0.0) -> np.ndarray:
    """`length` samples from `start` that play round with no click: the `blend` samples after
    them are faded out over the loop's own first `blend`, which fade in.

    So the loop's last sample is followed by its first exactly as the engine made them, and
    the change of material happens slowly, inside the loop. The two fades are a quarter of a
    sine and a cosine wave ("equal power"): two unlike sounds, like two stretches of rain,
    then keep their loudness through the blend. Two alike ones (a hum whose waves line up,
    `alike` near 1) would come out louder that way, so the sum is scaled back by how alike
    they are, and the level stays even for both.
    """
    data = np.asarray(samples, dtype=np.float64)
    if blend < 1 or length < 2 * blend or start < 0 or start + length + blend > data.size:
        raise SoundProblem("the sound is too short for a loop that long")
    loop = data[start:start + length].copy()
    turn = (np.pi / 2.0) * np.arange(blend) / blend
    up, down = np.sin(turn), np.cos(turn)
    both = loop[:blend] * up + data[start + length:start + length + blend] * down
    loop[:blend] = both / np.sqrt(1.0 + 2.0 * min(1.0, max(0.0, float(alike))) * up * down)
    return loop.astype(np.float32)


def make_loop(samples: Any, asked_seconds: float, rate: int = RATE) -> tuple[np.ndarray, dict[str, Any]]:
    """A loop about `asked_seconds` long from a sound made a little longer (`seconds_for_loop`).
    Returns (the loop, where it came from).

    Dead air is cut from both ends first, and the first half second is left out when there
    is room. With too little sound for the length asked the loop comes out shorter; with
    under two seconds it is refused. `start_sample` and `end_sample` are the loop's first and
    last sample in the finished file: all of it.
    """
    data, _ = trim_silence(np.asarray(samples, dtype=np.float32))
    asked = int(round(float(asked_seconds) * rate))
    blend = int(round(loop_blend_ms(asked_seconds) * rate / 1000.0))
    search = int(round(LOOP_SEARCH_MS * rate / 1000.0))
    if data.size < asked + blend:                       # not enough sound: a shorter loop
        asked = data.size - blend
        blend = min(blend, asked // 2)
        if asked < LOOP_SHORTEST_S * rate:
            raise SoundProblem("the sound stops too soon to make a loop of it")
    spare = data.size - asked - blend
    start = min(int(LOOP_LEAD_S * rate), max(0, spare - search))
    length, alike = pick_loop_length(data, start, asked, blend, search)
    loop = blend_loop(data, start, length, blend, alike)
    return loop, {"start_sample": 0, "end_sample": length - 1,
                  "crossfade_ms": round(blend * 1000.0 / rate, 1),
                  "seconds": round(length / rate, 3), "alike": round(alike, 3),
                  "taken_from_s": round(start / rate, 3)}


def seam_report(samples: Any, rate: int = RATE) -> dict[str, Any]:
    """How a loop's join measures, going from its last sample round to its first.

    - `step_ratio`: the jump across the join, against the usual jump between two samples
      next to each other. About 1 is "like any other pair"; a click is tens or hundreds.
    - `slope_ratio`: the same for the change of direction there.
    - `level_jump_db`: how much louder the first tenth of a second is than the last, and
      `usual_level_jump_db`: the same between tenths next to each other anywhere in the loop
      (the middle one of them all). Wind swells and drops on its own; this says by how much.
    """
    data = np.asarray(samples, dtype=np.float64).reshape(-1)
    if data.size < 8:
        raise SoundProblem("that is too short to be a loop")
    steps = np.diff(data)
    usual = float(np.sqrt(np.mean(steps * steps)))
    bends = np.diff(steps)
    usual_bend = float(np.sqrt(np.mean(bends * bends)))
    step = abs(float(data[0] - data[-1]))
    bend = max(abs(float(data[1] - 2.0 * data[0] + data[-1])), abs(float(data[0] - 2.0 * data[-1] + data[-2])))
    window = max(1, min(data.size // 2, int(rate * SEAM_LEVEL_MS / 1000.0)))
    before, after = (float(np.sqrt(np.mean(part * part))) for part in (data[-window:], data[:window]))
    count = data.size // window
    power = np.maximum((data[: count * window].reshape(count, window) ** 2).mean(axis=1), 1e-20)
    usual_jump = float(np.median(np.abs(np.diff(10.0 * np.log10(power))))) if count > 1 else 0.0
    return {"step": round(step, 6), "usual_step": round(usual, 6),
            "step_ratio": round(step / usual, 2) if usual > 0 else 0.0,
            "slope_ratio": round(bend / usual_bend, 2) if usual_bend > 0 else 0.0,
            "level_jump_db": round(20.0 * math.log10(after / before), 2) if before > 0 and after > 0 else 0.0,
            "usual_level_jump_db": round(usual_jump, 2)}


def level_background(samples: Any, target: float = LOOP_LEVEL_DB,
                     peak: float = PEAK) -> tuple[np.ndarray, dict[str, Any]]:
    """A background at its standard volume: its **average** level (`loudness_db`) brought to
    `target`, up or down. Returns (the sound, what was done).

    An effect is set by its loudest sample; a background set that way comes out as a wall of
    noise, since rain has no loud sample to speak of. Nothing is clipped: a background whose
    loudest sample would pass `peak` is held under its mark instead (`held_by_peak`). Never
    turned up by more than `MAX_BOOST_DB`. The whole sound moves as one, so a loop still joins.
    """
    data = np.asarray(samples, dtype=np.float32)
    before, loudest = loudness_db(data), peak_of(data)
    facts: dict[str, Any] = {"target": float(target), "measured": before, "peak": round(loudest, 4),
                             "gain_db": 0.0, "held_by_peak": False,
                             "measure": "average power over the whole sound, dB below full scale"}
    if before is None or loudest < SILENT_PEAK:
        return data.copy(), facts
    gain_db = min(float(target) - before, MAX_BOOST_DB)
    room = 20.0 * math.log10(peak / loudest)
    if gain_db > room:
        gain_db, facts["held_by_peak"] = room, True
    out = change_volume(data, gain_db)
    facts.update(measured=loudness_db(out), peak=round(peak_of(out), 4), gain_db=round(gain_db, 2))
    return out, facts


def clean_loop(samples: Any, rate: int, asked_seconds: float, *, channels_first: bool = False,
               target: float = LOOP_LEVEL_DB) -> tuple[np.ndarray, dict[str, Any]]:
    """Everything a fresh background loop needs, in order: one channel, 48 kHz, the loop made
    (`make_loop`), then its volume (`level_background`). Returns (mono float32 at 48 kHz, the
    record's `post` block, with `loop`, `loudness` and how the join measures under `join`).

    No fades and no tail: a loop's ends are its join, and anything done to them is heard
    every time round.
    """
    data = to_mono(samples, channels_first=channels_first)
    if not data.size:
        raise SoundProblem("the engine made an empty sound")
    if not np.isfinite(data).all():
        raise SoundProblem("the engine made a broken sound (it has values that are not numbers)")
    loop, made = make_loop(resample(data, rate, RATE), asked_seconds)
    loop, loudness = level_background(loop, target)
    return loop, {"trim": True, "fade_ms": 0.0, "peak": float(PEAK), "gain_db": loudness["gain_db"],
                  "tail_s": 0.0, "fade_out_ms": 0.0,
                  "loop": {key: made[key] for key in ("start_sample", "end_sample", "crossfade_ms")},
                  "loudness": loudness,
                  "join": {**seam_report(loop), "alike": made["alike"], "taken_from_s": made["taken_from_s"]}}


def _loop_chunk(start: int, end: int, rate: int) -> bytes:
    """A WAV's `smpl` chunk holding one loop: play forward from `start` to `end` (the last
    sample played), for ever. Samplers and game engines read it."""
    body = struct.pack("<9I", 0, 0, int(round(1e9 / rate)), 60, 0, 0, 0, 1, 0)     # ..., one loop, no extra data
    body += struct.pack("<6I", 0, 0, int(start), int(end), 0, 0)               # id, forward, start, end, -, for ever
    return b"smpl" + struct.pack("<I", len(body)) + body


def wav_bytes(samples: Any, rate: int = RATE, loop: tuple[int, int] | None = None) -> bytes:
    """A sound as the bytes of a 16-bit PCM WAV: what every game engine reads. `loop` is
    (first sample, last sample) of the part that plays round, written into the file itself."""
    data = np.asarray(samples, dtype=np.float32)
    channels = 1 if data.ndim == 1 else int(data.shape[1])
    pcm = np.round(np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")
    held = io.BytesIO()
    with wave.open(held, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(int(rate))
        out.writeframes(pcm.tobytes())
    made = held.getvalue()
    if loop is None:
        return made
    start, end = int(loop[0]), int(loop[1])
    if not 0 <= start < end < data.shape[0]:
        raise SoundProblem("the loop must start and end inside the sound")
    made += _loop_chunk(start, end, int(rate))
    return made[:4] + struct.pack("<I", len(made) - 8) + made[8:]       # the file's own size, with the new part


def write_wav(path: Path, samples: Any, rate: int = RATE, loop: tuple[int, int] | None = None) -> Path:
    """`wav_bytes` on disk. Mono, or (frames, channels)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_bytes(samples, rate, loop))
    return path


def read_loop(source: Path | bytes) -> dict[str, int] | None:
    """Where a WAV says its loop is: `{start_sample, end_sample}` (both played), or None when
    it names none. Takes a file or its bytes. Reads the first loop of the `smpl` chunk."""
    data = bytes(source) if isinstance(source, bytes | bytearray) else Path(source).read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise SoundProblem("that is not a WAV file")
    at = 12
    while at + 8 <= len(data):
        name, size = data[at:at + 4], struct.unpack_from("<I", data, at + 4)[0]
        if name == b"smpl" and size >= 60 and at + 68 <= len(data) and struct.unpack_from("<I", data, at + 36)[0]:
            start, end = struct.unpack_from("<2I", data, at + 52)
            return {"start_sample": int(start), "end_sample": int(end)}
        at += 8 + size + (size & 1)
    return None


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """A 16-bit WAV back as (float32 samples, rate): one row for mono, else (frames, channels)."""
    with wave.open(str(path), "rb") as source:
        if source.getsampwidth() != 2:
            raise SoundProblem(f"{Path(path).name} is not a 16-bit WAV")
        channels, rate = source.getnchannels(), source.getframerate()
        raw = source.readframes(source.getnframes())
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767.0
    return (data if channels == 1 else data.reshape(-1, channels)), int(rate)


def import_wav(data: bytes) -> tuple[np.ndarray, dict[str, Any]]:
    """A WAV someone brought, as (one channel float32 at 48 kHz, what it was).

    Reads 16- and 24-bit PCM, one or two channels (or more: they are averaged), at any sample
    rate. Anything else is refused in plain words: not a WAV at all, compressed or 32-bit float
    audio, 8-bit, empty, silent, or longer than 30 seconds.
    """
    try:
        with wave.open(io.BytesIO(bytes(data)), "rb") as source:
            width, channels, rate = source.getsampwidth(), source.getnchannels(), source.getframerate()
            frames = source.getnframes()
            raw = source.readframes(frames)
    except (wave.Error, EOFError, struct.error) as exc:
        raise SoundProblem("that file is not a WAV this can read. Save it as a plain 16-bit or 24-bit "
                           "WAV and try again") from exc
    bits = width * 8
    if bits not in IMPORT_BITS:
        raise SoundProblem(f"that WAV is {bits}-bit. Save it as 16-bit or 24-bit and try again")
    if channels < 1 or rate <= 0:
        raise SoundProblem("that WAV does not say how many channels or how fast it plays")
    count = len(raw) // (width * channels)
    if not count:
        raise SoundProblem("that WAV is empty")
    if count / rate > MAX_IMPORT_SECONDS:
        raise SoundProblem(f"that WAV is {count / rate:.0f} seconds long. A layer can be up to "
                           f"{MAX_IMPORT_SECONDS:g} seconds")
    raw = raw[: count * width * channels]
    if width == 2:
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    else:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        whole = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        samples = (np.where(whole >= 1 << 23, whole - (1 << 24), whole) / float(1 << 23)).astype(np.float32)
    mono = to_mono(samples.reshape(-1, channels))
    if peak_of(mono) < SILENT_PEAK:
        raise SoundProblem("that WAV is silent")
    out = resample(mono, rate, RATE)
    return out, {"sample_rate": int(rate), "channels": int(channels), "bits": bits,
                 "seconds": round(count / rate, 3), "resampled": rate != RATE, "mixed_down": channels > 1}


def loudness_db(samples: np.ndarray) -> float | None:
    """How loud a sound is over its whole length, in dB below full scale (its average power).
    Measured on a finished mix, never per layer. None for silence."""
    data = np.asarray(samples, dtype=np.float64)
    rms = float(np.sqrt(np.mean(data * data))) if data.size else 0.0
    return round(20.0 * math.log10(rms), 2) if rms > 0 else None


def speech_level_db(samples: Any, rate: int = RATE) -> float | None:
    """How loud a voice line is while someone is speaking, in dB below full scale: the average
    power of its 50 ms pieces, leaving out the pauses (pieces 30 dB under the loudest one).
    None when there is under 0.4 s of speech, or none at all: too little to measure."""
    data = np.asarray(samples, dtype=np.float64).reshape(-1)
    size = max(1, int(rate * VOICE_BLOCK_MS / 1000.0))
    count = data.size // size
    if not count:
        return None
    power = (data[: count * size].reshape(count, size) ** 2).mean(axis=1)
    loudest = float(power.max())
    if loudest <= SILENT_PEAK ** 2:
        return None
    spoken = power[power >= loudest * 10.0 ** (-VOICE_GATE_DB / 10.0)]
    if spoken.size * size < VOICE_SHORT_S * rate:
        return None
    return round(10.0 * math.log10(float(spoken.mean())), 2)


def voice_target_db(direction: str | None = None) -> float:
    """Where a line said this way sits. Words that are not one of ours (free text) sit with a
    plainly said line."""
    return VOICE_LEVEL_DB + VOICE_DIRECTION_DB.get(str(direction or "").strip().lower(), 0.0)


def press_peaks(samples: Any, ceiling: float = PEAK, rate: int = RATE) -> np.ndarray:
    """Turn down only the parts of a sound that pass `ceiling`, smoothly, leaving the rest as
    it is: a peak limiter. The sound is read in 5 ms pieces; each piece that would pass the
    ceiling gets the gain that brings it under, every piece takes the lowest gain of itself
    and its two neighbours (so the turn-down starts before the peak and ends after it), and
    the gain slides from one piece to the next. No sample comes out over the ceiling."""
    data = np.asarray(samples, dtype=np.float32).reshape(-1)
    if not data.size or peak_of(data) <= ceiling:
        return data.copy()
    size = max(1, int(rate * VOICE_PRESS_MS / 1000.0))
    count = -(-data.size // size)
    padded = np.zeros(count * size, dtype=np.float32)
    padded[: data.size] = np.abs(data)
    need = np.minimum(1.0, ceiling / np.maximum(padded.reshape(count, size).max(axis=1), 1e-12))
    wide = np.pad(need, 1, mode="edge")
    held = np.minimum(np.minimum(wide[:-2], wide[1:-1]), wide[2:])
    gain = np.interp(np.arange(data.size), (np.arange(count) + 0.5) * size, held)
    return (data * gain).astype(np.float32)


def level_voice(samples: Any, direction: str | None = None, *, rate: int = RATE,
                peak: float = PEAK) -> tuple[np.ndarray, dict[str, Any]]:
    """A voice line at its standard volume, up or down. Returns (the line, what was done).

    The mark is `voice_target_db(direction)`. A line too short to measure is set by its
    loudest sample instead, 12 dB over that mark (about where speech peaks). It is never
    turned up by more than `MAX_BOOST_DB`.

    Nothing is clipped. Where the mark puts the loudest syllables past `peak`, they are
    pressed down to it (`press_peaks`), by at most `VOICE_PRESS_DB`: `pressed_db` says how
    far. A line that would need more than that is turned down as a whole until it does not,
    so it ends under its mark (`held_by_peak`).
    """
    data = np.asarray(samples, dtype=np.float32)
    target = voice_target_db(direction)
    measured, loudest = speech_level_db(data, rate), peak_of(data)
    facts: dict[str, Any] = {"target": target, "measured": measured, "level": measured, "peak": round(loudest, 4),
                             "gain_db": 0.0, "pressed_db": 0.0,
                             "measure": "average power while speaking, dB below full scale",
                             "set_by": "level", "held_by_peak": False}
    if loudest < SILENT_PEAK:
        return data.copy(), facts
    if measured is None:
        facts["set_by"] = "peak"
        gain_db = min(target + VOICE_CREST_DB - 20.0 * math.log10(loudest), MAX_BOOST_DB,
                      20.0 * math.log10(peak / loudest))
        out = change_volume(data, gain_db)
        facts.update(gain_db=round(gain_db, 2), peak=round(peak_of(out), 4), level=None)
        return out, facts
    out, gain_db, pressed = data, 0.0, 0.0
    for _ in range(6):                      # pressing peaks costs a little level: go round again for it
        level = speech_level_db(out, rate)
        if level is None:
            break
        step = min(target - level, MAX_BOOST_DB - gain_db)
        over = 20.0 * math.log10(peak_of(out) / peak) + step        # how far the peaks would pass
        if over > VOICE_PRESS_DB - pressed:
            step -= over - (VOICE_PRESS_DB - pressed)
            facts["held_by_peak"] = True
        if abs(step) < 0.05:
            break
        out = change_volume(out, step)
        gain_db += step
        before = peak_of(out)
        if before > peak:
            out = press_peaks(out, peak, rate)
            pressed += 20.0 * math.log10(before / peak)
        if facts["held_by_peak"]:
            break
    facts.update(gain_db=round(gain_db, 2), pressed_db=round(pressed, 2), peak=round(peak_of(out), 4),
                 level=speech_level_db(out, rate))
    return out, facts


def clean_voice(samples: Any, rate: int, direction: str | None = None, *,
                channels_first: bool = False, fade_ms: float = FADE_MS) -> tuple[np.ndarray, dict[str, Any]]:
    """Everything a fresh voice line needs, in order: one channel, 48 kHz, the silence cut
    from both ends, short fades, then its volume (`level_voice`). Returns (mono float32 at
    48 kHz, the record's `post` block, with the volume facts under `loudness`)."""
    data = to_mono(samples, channels_first=channels_first)
    if not data.size:
        raise SoundProblem("the engine made an empty line")
    if not np.isfinite(data).all():
        raise SoundProblem("the engine made a broken line (it has values that are not numbers)")
    data, trimmed = trim_silence(resample(data, rate, RATE))
    data, loudness = level_voice(fade(data, RATE, fade_ms), direction)
    return data, {"trim": True, "trimmed": trimmed, "fade_ms": float(fade_ms), "peak": float(PEAK),
                  "gain_db": loudness["gain_db"], "tail_s": 0.0, "fade_out_ms": float(fade_ms),
                  "loudness": loudness}


def join_lines(lines: Any, gap_ms: float = 250.0, rate: int = RATE) -> tuple[np.ndarray, dict[str, Any]]:
    """Voice lines one after another, with `gap_ms` of silence between them. Each is mono at
    48 kHz and already at its own level; none is made louder or quieter here, so the balance
    between a shout and a whisper is kept. Returns (the joined sound, its length and where
    each line starts)."""
    parts = [np.asarray(line, dtype=np.float32).reshape(-1) for line in lines or []]
    if not parts or any(not part.size for part in parts):
        raise SoundProblem("there is no line to join")
    gap = np.zeros(int(round(max(0.0, float(gap_ms)) * rate / 1000.0)), dtype=np.float32)
    out, starts, at = [], [], 0
    for index, part in enumerate(parts):
        if index:
            out.append(gap)
            at += gap.size
        starts.append(round(at * 1000.0 / rate, 1))
        out.append(part)
        at += part.size
    joined = limit_peak(np.concatenate(out))
    return joined, {"seconds": round(joined.size / rate, 3), "starts_ms": starts, "gap_ms": float(gap_ms)}


def check_layer_numbers(layer: dict[str, Any]) -> dict[str, float]:
    """A layer's start, volume and pitch as numbers, or `SoundProblem` naming the limit.
    Missing ones are 0. Out of range is refused, never pulled inside."""
    numbers = {}
    for key, (low, high) in LAYER_LIMITS.items():
        value = layer.get(key)
        if value is None or value == "":
            value = 0.0
        if isinstance(value, bool):
            raise SoundProblem(f"{LAYER_WORDS[key]} must be a number")
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise SoundProblem(f"{LAYER_WORDS[key]} must be a number") from None
        if not low <= value <= high:              # also refuses not-a-number
            raise SoundProblem(f"{LAYER_WORDS[key]} must be between {low:g} and {high:g} {LAYER_UNITS[key]}")
        numbers[key] = value
    return numbers


def place_layer(samples: Any, *, start_ms: float = 0.0, volume_db: float = 0.0,
                pitch_semitones: float = 0.0) -> tuple[int, np.ndarray]:
    """One layer ready to be summed: (the sample it starts on, its samples). Pitch first (by
    resampling, so **its length changes**), then short fades so its ends never click, then its
    volume. Its volume is exactly what was asked: nothing here brings it back up or down."""
    data = fade(shift_pitch(to_mono(samples), pitch_semitones))
    return int(round(float(start_ms) * RATE / 1000.0)), change_volume(data, volume_db)


def mix_layers(layers: Any) -> tuple[np.ndarray, dict[str, Any]]:
    """Several layers as one sound. Each is `{samples, start_ms, volume_db, pitch_semitones}`,
    with samples already mono at 48 kHz (`import_wav`, `read_wav`).

    They are placed, **summed**, and the sum is turned down **once** if it passes the peak
    (`limit_peak`). It is never turned up, and no layer is levelled on its own, so the balance
    you set is the balance you get. Returns (the mix, facts): its length, its peak before and
    after, how far it was turned down, its loudness, and where each layer landed.
    """
    layers = list(layers or [])
    if not layers:
        raise SoundProblem("add a layer first")
    if len(layers) > MAX_LAYERS:
        raise SoundProblem(f"a sound can have up to {MAX_LAYERS} layers")
    placed = []
    for layer in layers:
        numbers = check_layer_numbers(layer)
        data = np.asarray(layer.get("samples"), dtype=np.float32)
        if data.ndim != 1 or not data.size or not np.isfinite(data).all():
            raise SoundProblem("a layer has no sound in it")
        placed.append(place_layer(data, **numbers))
    total = max(start + data.size for start, data in placed)
    mix = np.zeros(total, dtype=np.float32)
    for start, data in placed:
        mix[start:start + data.size] += data
    before = peak_of(mix)
    if before < SILENT_PEAK:
        raise SoundProblem("the layers together are silent. Turn a layer up")
    mix = limit_peak(mix)
    after = peak_of(mix)
    return mix, {
        "seconds": round(total / RATE, 3),
        "peak_before": round(before, 4), "peak": round(after, 4),
        "turned_down_db": round(20.0 * math.log10(after / before), 2) if after < before else 0.0,
        "loudness_db": loudness_db(mix),
        "layers": [{"start_s": round(start / RATE, 3), "seconds": round(data.size / RATE, 3)}
                   for start, data in placed],
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_wav(path: Path, *, want_rate: int = RATE, want_channels: int = CHANNELS) -> dict[str, Any]:
    """`inspect` for a file on disk, plus its size and hash: the check a kept sound must pass."""
    path = Path(path)
    data, rate = read_wav(path)
    facts = inspect(data, rate, want_rate=want_rate, want_channels=want_channels)
    return {**facts, "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def wave_bars(samples: Any, count: int = 60) -> list[float]:
    """The sound as `count` bar heights from 0 to 1, loudest bar 1: its wave picture."""
    data = np.abs(to_mono(samples))
    if not data.size or count < 1:
        return [0.0] * max(0, count)
    edges = np.linspace(0, data.size, count + 1).astype(int)
    bars = [float(data[a:b].max()) if b > a else 0.0 for a, b in pairwise(edges)]
    top = max(bars)
    return [round(bar / top, 3) if top else 0.0 for bar in bars]
