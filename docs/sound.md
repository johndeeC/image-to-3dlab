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
5. Pick a **Length**, from half a second to 30 seconds. A sound still ringing at the end
   runs a little longer, so it is not cut off; each try shows its real length.
6. Press **Make 4**. Play each try, look at its wave, and press **Keep** on the good one.

Kept sounds show under **Library → Sounds**; click one to hear it. Tries you do not keep
are cleared when you make the next batch.

On a try, **Make more like this** starts four new tries from the same description and
settings. Tries you have not kept are cleared, as with any new batch.

## Variations

A footstep played twenty times in a row sounds like a machine. Games fix that by keeping a
handful of slightly different takes and playing one at random. **Make variations** builds
that handful from a sound you kept. No model runs, so it takes about a second and works
without the engine installed.

1. Press **Make variations** on a kept sound (in **Library → Sounds**, or on a try you kept).
2. **How many**: 2 to 12. The first is always the sound as it is.
3. **How different**:

   | Choice | Pitch, up or down | Volume, up or down |
   |---|---|---|
   | A little | 1 semitone | 1.5 dB |
   | Some | 2.5 semitones | 3 dB |
   | A lot | 4 semitones | 5 dB |

   A semitone is one key on a piano; 12 make an octave. A decibel (dB) is a step of
   loudness; 6 dB is about double. *A little* is the same boot taking another step;
   *a lot* is a different boot.
4. **What changes**: Pitch, Volume, or both.
5. **Make**, then listen. **Play like a game** plays them in random order, never the same
   one twice running, the way a game engine will.
6. **Save the set**: numbered files in one folder, ready to drop into Unity, Godot or Roblox.

Three things worth knowing:

- **Pitch changes the length.** Pitch is changed the way game engines do it, by playing the
  sound faster or slower (resampling). So a higher take is a little shorter and a lower one a
  little longer: 4 semitones down is about a quarter longer.
- **Nothing is turned up to fill the room.** If any take would be too loud for the file, the
  whole set is turned down by the same amount, so the differences you heard are the ones you
  get. The record says by how much (`turned_down_db`).
- **A set can be rebuilt.** Takes are spread evenly across the range, then shuffled by a seed.
  The same kept sound, numbers and seed give the same files. That is also why
  nothing is stored while you listen: Save makes the same takes again.

```
output/sounds/<name>_set__<date-time>/<name>_01.wav … <name>_NN.wav
output/sounds/<name>_set__<date-time>/set.provenance.json
```

One record covers the whole set. Its licence is the kept sound's own, unchanged: a variation
is the same sound, moved a little. If the kept sound has no record, the set is marked
unverified.

```json
{"schema_version": 1, "kind": "sound_set", "name": "boots",
 "source": {"id": "boots__20261120-091306", "path": "sounds/boots__20261120-091306/sound.wav", "sha256": "..."},
 "license": {"name": "Apache-2.0", "classification": "commercial-clear"},
 "variation": {"of": "boots__20261120-091306", "count": 6, "seed": 21, "how_different": "some",
               "change": ["pitch", "volume"], "most": {"pitch_semitones": 2.5, "volume_db": 3.0},
               "turned_down_db": -2.1},
 "files": [{"file": "boots_02.wav", "sha256": "...", "seconds": 1.42, "peak": 0.71,
            "variation": {"of": "boots__20261120-091306", "index": 2, "seed": 21,
                          "pitch_semitones": 1.3, "volume_db": -1.8, "gain_db": -3.9,
                          "start_ms": 0.0, "note": "a bit higher, a bit softer"}}],
 "layers": ["how the kept sound was made: its description, seed and engine"]}
```

`volume_db` is how loud a take is beside the original; `gain_db` is what was really applied
(that, plus the set's turn-down). In the Library a set is one row; each click plays a
different take.

Agents and the server can also ask for `"start"` under what changes: each take starts up to
8 milliseconds earlier or later. It is not on the sheet, to keep the sheet short.

## Footsteps on a move

A walk needs a step sound each time a foot lands. Games do not want that baked into one long
sound file: they want to know *when* each foot lands, and play the sound themselves. So the
studio marks the landings and saves them as a small list of times.

1. Open a character and pick a move (walk, run). Under the steps there is a card:
   **Footsteps on this move**.
2. **Step sound**: a kept sound or a set. A set sounds best: no two steps in a row the same.
3. **Found in the walk** says what it found, such as "2 steps in each 1 s loop: left, right."
   The timeline has a row a foot. Shaded is foot on the ground; a dot is where a sound plays.
4. **Play with sound** plays the move with the steps, in the order a game would.
5. **Save step marks** writes the list beside the move. **On** unticked saves it switched off.

A move where the feet never leave the floor (idle, wave) has no steps, and the card says so.

### How the steps are found

The finished move is played back as numbers and each foot's height is watched. A foot is *down*
near the lowest it ever gets, and *up* once it is well clear; a landing is a step. Because it
reads the file that ships, it works on any skeleton and any move, and a faster move simply has
closer steps. Feet are found by the skeleton's shape, not by bone names. A foot that never
rises 2% of the body's height is treated as standing. Two feet landing within 40 thousandths
of a second make one mark ("left + right"), as in a jump. Marks cannot be dragged yet.

The code is `image_to_3dlab/footsteps.py` (the finding) and `image_to_3dlab/glb_motion.py`
(reading a move out of a GLB). For the four-legged gaits built by `scripts/quadruped_gait.py`
the same answer comes straight from the gait's own timing (`footsteps.gait_steps`); tests hold
the two to each other.

### The file

```
output/animate/<run>/<name>_walk.glb
output/animate/<run>/<name>_walk.cues.json
```

```json
{"schema_version": 1, "kind": "move_cues", "on": true, "move": "walk",
 "model": "knight_walk.glb", "loop": true, "loop_seconds": 1.0, "seconds": 1.0, "speed": 1.0,
 "cues": [{"time_s": 0.45, "foot": "left", "event": "footstep",
           "sound": {"id": "boots_set__20261120-093300", "files": ["../../sounds/boots_set__20261120-093300/boots_01.wav"]}}],
 "sound": {"id": "boots_set__20261120-093300", "kind": "sound_set", "name": "Boots",
           "files": ["../../sounds/boots_set__20261120-093300/boots_01.wav"],
           "play": "Pick one at random for each step, never the same one twice running.",
           "license": {"name": "Apache-2.0", "classification": "commercial-clear"},
           "record": "../../sounds/boots_set__20261120-093300/set.provenance.json"},
 "found": {"how": "Played the move back and watched each foot's height.",
           "feet": {"left": ["bone_40", "bone_41"]}, "planted": {"left": [[0.45, 1.07]]}}}
```

- `time_s` is seconds from the move's first frame, at the speed the move was saved (`speed`).
  Play the move twice as fast and each time halves.
- `planted` is when each foot is on the ground. On a loop, a span that ends past
  `loop_seconds` carries on from the start.
- File paths are from the cue file's own folder.
- The file holds no audio, so it has no licence of its own. `sound.license` repeats the
  sound's licence and `sound.record` points at its record.

### Using it in a game

Each engine has its own name for "call me at this moment of the animation".

| Engine | What to add | How |
|---|---|---|
| Unity | An **Animation Event** at each `time_s` | Select the clip, open the Animation tab of the import settings (or the Animation window), add an event at each time and name a function such as `Footstep`. In that function, play a random clip from the set, not the one just played. |
| Godot | A **Call Method Track** key at each `time_s` | In the AnimationPlayer, add a Call Method Track on your character, and a key at each time calling `footstep()`. An `AudioStreamRandomizer` holding the set picks a take for you. |
| Roblox | A **marker** (animation event) at each `time_s` | In the Animation Editor, add an event named `Footstep` at each time, and listen with `GetMarkerReachedSignal("Footstep")`. |

For Roblox there is a catch, and we will not pretend otherwise: **Export for Roblox leaves the
moves out** (Roblox cannot play this skeleton's moves; the copy comes in standing still). So
the times are only of use there if you animate the character in Roblox yourself and its
steps fall at the same moments. The sound files work in Roblox either way.

One thing to check in your engine: moves made here start one frame (1/30 s) into the file.
Times are counted from that first frame. If your engine counts from zero instead, add 1/30 s.

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
2. the tail is kept, if it is still ringing (below)
3. silence cut from both ends
4. a 5 millisecond fade in and out, so it never clicks
5. brought to the standard volume: its loudest point becomes 0.98 of full scale, up or down

Then it is checked. A try that is silent or broken is dropped, and the sheet says so.

**Volume.** A fresh sound is turned up as well as down, so four tries are as loud as each
other and none comes out too quiet. It is never turned up by more than 24 dB (about 16
times): anything needing more is mostly hiss, and true silence is left alone so the check
still drops it. Never clipped. This is for fresh sounds only. Once volumes mean something
(a variation set, a mix of layers) a sound is only ever turned down.

**Tails.** A coin ding asked for at 1 second is still ringing at 1 second. The engine makes
30 seconds every time anyway, so we ask it for extra: half as long again, at least half a
second more, never past 30 in all. The words it is given still say the length you asked
for, so the sound itself does not change. Then:

- Quiet at the length asked (45 dB under its loudest point): it ends there, as before.
- Still ringing: it runs on until it has been that quiet for 30 milliseconds.
- Not that quiet by the end of the extra, but at least 6 dB down: all the extra is kept.
- Not dying away at all (rain, an engine): it ends at the length asked. More is not a tail.

Whatever was still going where it ends gets a 30 millisecond fade-out, not a hard cut. So
a sound can be a little longer than asked; the sheet and the Library show its real length.
The record says what happened: `gain_db` (how far the volume moved), `tail_s` (seconds kept
past the length asked) and `fade_out_ms`; the length asked is `seconds_asked` on the layer.

## Where it lives

```
output/sounds/<name>__<date-time>/sound.wav
output/sounds/<name>__<date-time>/sound.provenance.json
output/sounds/.tries/        tries not kept yet
```

The record is shaped for what comes next: a sound is a list of layers (today always one),
with room for loops and "made for this move". Variation sets have a record of their own
(see Variations).

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
 "post": {"trim": true, "fade_ms": 5.0, "peak": 0.98, "gain_db": 11.9, "tail_s": 0.31, "fade_out_ms": 30.0},
 "loudness": {"target": null, "measured": null, "peak": 0.98},
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
and answers its file path; `list_assets(kind="sound")` lists the kept ones and the saved
sets. `make_variations(sound_id, count, how_different, change)` saves a set from a kept sound
and answers its folder and files. `footsteps(asset_id, move, sound_id)` finds the steps in a
character's move, saves the cue file and answers the times; `save=False` only looks.

The server routes are `/api/sound…` for making sounds, and for variations
`POST /api/sounds/<id>/variations` (takes to listen to; writes nothing) and
`POST /api/sounds/<id>/set` (saves them). Both take
`{count, how_different, change, seed, name}`. Footsteps: `GET /api/footsteps?file=<move>` finds
the steps and writes nothing; `POST /api/footsteps` with `{file, sound_id, on}` saves them
(`viewer/footsteps_api.py`). `viewer/sound_api.py` is the code for the rest; the maths
is in `image_to_3dlab/sound_post.py`.
