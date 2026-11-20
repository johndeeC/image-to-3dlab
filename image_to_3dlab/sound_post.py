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

Ported from the `postprocess_wav` / `inspect_wav` pair in our older ai-sfx project (trim at
0.3% of the peak, 5 ms fades, peak 0.98, 16-bit, refuse empty or broken audio).
"""

from __future__ import annotations

import hashlib
import io
import math
import random
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
# extra audio (half as long again, at least half a second), and a sound still ringing at the
# cut keeps going until it has died away.
TAIL_SHARE = 0.5         # extra room: this share of the length asked...
TAIL_MIN_S = 0.5         # ...and never less than this
TAIL_QUIET_DB = -45.0    # "died away": this far under the sound's loudest point...
TAIL_QUIET_MS = 30.0     # ...and staying there this long
TAIL_FADE_MS = 30.0      # the fade-out for a sound that was still going when it ended
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
    for a fade-out longer than the fade-in; left out, both are `ms`."""
    data = np.asarray(samples, dtype=np.float32).copy()
    for length, at_end in ((ms, False), (ms if out_ms is None else out_ms, True)):
        frames = min(int(rate * max(0.0, length) / 1000.0), data.size // 2)
        if frames > 0:
            ramp = np.linspace(0.0, 1.0, frames, dtype=np.float32)
            if at_end:
                data[-frames:] *= ramp[::-1]
            else:
                data[:frames] *= ramp
    return data


def seconds_with_tail(asked: float, most: float) -> float:
    """How long to ask the engine for, so there is room for a tail: half as long again, at
    least half a second more, never past `most`. To a tenth of a second, as engines take it."""
    return round(min(float(most), float(asked) + max(TAIL_MIN_S, TAIL_SHARE * float(asked))), 1)


def keep_tail(samples: np.ndarray, asked_seconds: float, rate: int = RATE) -> tuple[np.ndarray, float, float]:
    """End a sound that was made longer than asked. Returns (the sound, seconds kept past
    the length asked, the fade-out in ms it should get).

    - Quiet at the length asked (45 dB under its loudest point): cut there, as always.
    - Still ringing: it runs on until it has been that quiet for 30 ms, then ends.
    - Never gets that quiet, but is at least 6 dB down by the end: all of it is kept.
    - Not dying away at all (rain, an engine): cut at the length asked. More is not a tail.

    Anything still going where it ends gets the longer fade-out, never a hard cut. Nothing is
    changed here but where the sound stops.
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
    dying = float(level[-window:].max()) <= at_cut * 10.0 ** (-STEADY_DB / 20.0)
    if data.size > asked and dying:
        return data.copy(), round((data.size - asked) / rate, 3), TAIL_FADE_MS
    return head.copy(), 0.0, TAIL_FADE_MS


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
    data, gain_db = set_peak(fade(data, RATE, fade_ms, fade_out_ms), peak)
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


def wav_bytes(samples: Any, rate: int = RATE) -> bytes:
    """A sound as the bytes of a 16-bit PCM WAV: what every game engine reads."""
    data = np.asarray(samples, dtype=np.float32)
    channels = 1 if data.ndim == 1 else int(data.shape[1])
    pcm = np.round(np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")
    held = io.BytesIO()
    with wave.open(held, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(int(rate))
        out.writeframes(pcm.tobytes())
    return held.getvalue()


def write_wav(path: Path, samples: Any, rate: int = RATE) -> Path:
    """`wav_bytes` on disk. Mono, or (frames, channels)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_bytes(samples, rate))
    return path


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """A 16-bit WAV back as (float32 samples, rate): one row for mono, else (frames, channels)."""
    with wave.open(str(path), "rb") as source:
        if source.getsampwidth() != 2:
            raise SoundProblem(f"{Path(path).name} is not a 16-bit WAV")
        channels, rate = source.getnchannels(), source.getframerate()
        raw = source.readframes(source.getnframes())
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767.0
    return (data if channels == 1 else data.reshape(-1, channels)), int(rate)


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
