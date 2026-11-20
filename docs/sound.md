# Sound: make a sound effect

Describe a sound, get four tries, keep the one you like. Effects only for now (a footstep,
a hit, a door). Background loops, music and voice come later.

## Flow

1. Install the engine once: **Setup → MOSS-SoundEffect v2.0**. It tells you what it fetches
   (about 6.4 GB of model files, plus about 0.5 GB of Python packages) and waits for a yes.
   Nothing else ever downloads it: without it, the Sound sheet says so and points to Setup.
2. Press **Sound**, beside + Create and Canvas.
3. **Describe it.** One thing happening, close up. Say what hits what: *"sword striking a
   wooden shield, one hit, dry"*.
4. **Leave out** what you do not want: music, voices, room echo.
5. Pick a **Length**, from half a second to 30 seconds.
6. Press **Make 4**. Play each try, look at its wave, and press **Keep** on the good one.

Kept sounds show under **Library → Sounds**; click one to hear it. Tries you do not keep
are cleared when you make the next batch.

## The engine

[MOSS-SoundEffect v2.0](https://huggingface.co/OpenMOSS-Team/MOSS-SoundEffect-v2.0), in its
4-bit [MLX](https://huggingface.co/mlx-community/MOSS-SoundEffect-v2.0-4bit) form (MLX is
Apple's own number library, so this is Apple Silicon only). It is run by the
[moss-sfx-mlx](https://github.com/xocialize/moss-soundeffect-mlx) port. All three are
Apache-2.0: the sounds are yours, commercial use included. The model's training data is not
published, and the record beside each sound says so.

What it can do: words in, one channel of sound out, at 48 kHz, up to 30 seconds. It takes a
"leave out" list. It cannot take a sound and change it.

What to expect: it works on a full 30 seconds every time and cuts the result to length, so
a short sound takes as long as a long one. The port's own figure is about 45 seconds a sound
and about 12 GB of memory on a top-end Mac; we have not measured it ourselves yet.

It lives in its own Python environment, `vendor/moss-sfx/.venv`, so its packages never mix
with the studio's.

## Limits refuse, they do not clip

Ask for 45 seconds and you are told the most is 30. You do not get 30 and a surprise. The
same goes for an empty description, more than four tries, or a kind that is not built yet.

## One seed per batch

A seed is the number a try starts from: same words and same seed, same sound. "Make 4"
picks one seed. Try 1 uses it, try 2 uses seed + 1, and so on. Each sound's record keeps
both numbers, so any try can be made again.

## Clean-up

Every sound goes through `image_to_3dlab/sound_post.py` before it is written:

1. mixed down to one channel, at 48 kHz
2. silence cut from both ends
3. turned down if its loudest point is past 0.98 of full scale (never clipped, never turned up)
4. a 5 millisecond fade in and out, so it never clicks

Then it is checked. A try that is silent or broken is dropped, and the sheet says so.

## Where it lives

```
output/sounds/<name>__<date-time>/sound.wav
output/sounds/<name>__<date-time>/sound.provenance.json
output/sounds/.tries/        tries not kept yet
```

The record is shaped for what comes next: a sound is a list of layers (today always one),
with room for variation sets, loops and "made for this move".

```json
{"schema_version": 1, "kind": "sound", "sound_kind": "effect",
 "output": {"path": "sound.wav", "sha256": "...", "sample_rate": 48000, "channels": 1,
            "seconds": 1.2, "classification": "commercial-clear"},
 "license": {"name": "Apache-2.0", "url": "...", "classification": "commercial-clear"},
 "layers": [{"id": "layer-1",
             "source": {"type": "generated", "engine": "moss", "prompt": "...",
                        "leave_out": ["music"], "seed": 8, "batch_seed": 7, "try": 2, "steps": 100},
             "file": "sound.wav", "sha256": "...", "license": {"name": "Apache-2.0"},
             "start_ms": 0, "volume_db": 0.0, "pitch_semitones": 0}],
 "variation": null,
 "post": {"trim": true, "fade_ms": 5.0, "peak": 0.98},
 "loudness": {"target": null, "measured": null, "peak": 0.97},
 "loop": null, "made_for": null}
```

When a sound is one day made from several sources, its licence follows the rule the rest of
the repo already uses for anything built from more than one thing: the names are joined
with " + ", and if the sources are not all in the same class, or one is unknown, the result
is marked unverified.

## From a terminal

```bash
python scripts/bootstrap_sound_effects.py          # says what it wants, then asks
vendor/moss-sfx/.venv/bin/python scripts/sound_generate.py \
    --prompt "sword striking a wooden shield, one hit, dry" --seconds 1.5 --leave-out music
```

The second command never downloads: with the model files missing it stops and says so.

## For agents

`make_sound(description, seconds, leave_out, tries, seed)` starts a batch;
`sound_status(job_id, wait_seconds)` follows it; `sound_status(job_id, keep=2)` keeps a try
and answers its file path; `list_assets(kind="sound")` lists the kept ones. The server
routes are `/api/sound…`; `viewer/sound_api.py` is the code.
