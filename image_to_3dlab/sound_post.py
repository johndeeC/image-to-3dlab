"""Clean-up for a generated sound: the maths between the engine and the WAV on disk.

A raw sound from a model has dead air at both ends, may click where it starts and stops,
and can peak past what a 16-bit file holds. `clean` fixes those in one fixed order:

    one channel -> 48 kHz -> trim the silence -> hold the peak under 0.98 -> 5 ms fades

Inside, a sound is always **mono float32 at 48 kHz** (`RATE`). Each step is its own function
so later work reuses them: several layers are summed and then pass through `limit_peak`
once, as a whole; loudness is measured on that mix, never per layer.

Pure numpy and the standard library's `wave`, on purpose: the main environment pins
numpy below 2 and the sound engine's own environment may not, so nothing here leans on
either version, and no audio package is needed to read or write a file.

Ported from the `postprocess_wav` / `inspect_wav` pair in our older ai-sfx project (trim at
0.3% of the peak, 5 ms fades, peak 0.98, 16-bit, refuse empty or broken audio).
"""

from __future__ import annotations

import hashlib
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
    length = max(1, int(round(data.size * to / rate)))
    spectrum = np.fft.rfft(data.astype(np.float64))
    resized = np.zeros(length // 2 + 1, dtype=np.complex128)
    kept = min(spectrum.size, resized.size)
    resized[:kept] = spectrum[:kept]
    return (np.fft.irfft(resized, n=length) * (length / data.size)).astype(np.float32)


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


def fade(samples: np.ndarray, rate: int = RATE, ms: float = FADE_MS) -> np.ndarray:
    """A short ramp in and out, so the sound never starts or stops on a click."""
    data = np.asarray(samples, dtype=np.float32).copy()
    frames = min(int(rate * max(0.0, ms) / 1000.0), data.size // 2)
    if frames > 0:
        ramp = np.linspace(0.0, 1.0, frames, dtype=np.float32)
        data[:frames] *= ramp
        data[-frames:] *= ramp[::-1]
    return data


def clean(samples: Any, rate: int, *, trim: bool = True, fade_ms: float = FADE_MS,
          peak: float = PEAK, channels_first: bool = False) -> tuple[np.ndarray, dict[str, Any]]:
    """Everything a fresh sound needs, in order. Returns (mono float32 at 48 kHz, what was done).

    The second value is the sidecar's `post` block. Empty or broken audio is refused here,
    before any of the maths can spread a NaN through the whole clip.
    """
    data = to_mono(samples, channels_first=channels_first)
    if not data.size:
        raise SoundProblem("the engine made an empty sound")
    if not np.isfinite(data).all():
        raise SoundProblem("the engine made a broken sound (it has values that are not numbers)")
    data = resample(data, rate, RATE)
    trimmed = False
    if trim:
        data, trimmed = trim_silence(data)
    data = fade(limit_peak(data, peak), RATE, fade_ms)
    return data, {"trim": bool(trim), "trimmed": trimmed, "fade_ms": float(fade_ms), "peak": float(peak)}


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


def write_wav(path: Path, samples: Any, rate: int = RATE) -> Path:
    """A 16-bit PCM WAV: what every game engine reads. Mono, or (frames, channels)."""
    data = np.asarray(samples, dtype=np.float32)
    channels = 1 if data.ndim == 1 else int(data.shape[1])
    pcm = np.round(np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(int(rate))
        out.writeframes(pcm.tobytes())
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
