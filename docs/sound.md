# Sound: make a sound effect

Describe a sound, get four tries, keep the one you like: a footstep, a hit, a door. Or type
a line and have it said out loud: see [Voice](#voice-a-line-said-out-loud). Or make rain,
wind or an engine that plays round and round: see [Background loops](#background-loops).
Or describe a tune: see [Music](#music-a-tune-from-a-description).

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

## Background loops

Rain, wind, a crowd, an engine: a sound that sits under the game and never ends. A game
plays a few seconds of it round and round, so the end must run into the start with no click
and no bump. **What kind? → Background loop** makes one.

1. **Describe it**: one sound that goes on and on. *"Rain on a tent"*, *"spaceship engine
   hum"*. Leave out things that happen once, like a door or a shout.
2. Pick a **Length**, 4 to 30 seconds (8 to start). Longer hides the repeat better.
3. Press **Make 4**. Each try plays **round and round** until you stop it, so you hear
   it as a game would play it. **Hear the join** plays just the end running into the start,
   two seconds of each.
4. **Keep** the good one. In the Library it says "Background loop" and plays round too.

**What is different from an effect**

- **The join.** The engine makes a little more sound than the loop needs. That extra is
  blended into the loop's first part, over a tenth of the loop (a quarter second to a
  second). So the loop's last sample is followed by its first exactly as the engine made
  them one after the other: nothing to click. The two are mixed with "equal power" curves,
  so rain stays as loud through the blend as either side of it.
- **The length may move a touch**, up to 0.15 seconds either way. A hum has waves; if the
  loop's length cuts one in half you hear a wobble each time round. So for a hum the
  nearest length that lines the waves up is taken. Rain has none, and keeps the length asked.
- **No fades, no tail.** An effect gets a fade at each end; on a loop you would hear that
  every time round.
- **Volume is set by the average, not the loudest point.** An effect's loudest sample is
  brought to 0.98. Do that to rain, which has no loud sample to speak of, and you get a wall
  of noise (our first storm averaged -2.5 dB: louder than any effect). A loop's **average
  level** is set to -20 dB below full scale, so it sits under the effects and voices.
  Nothing is clipped: a loop with one very loud moment is held a little under that mark.
- **The words.** Unless your description already says so, "continuous, steady, no single
  events" is added for the engine. Your own words come first and are never changed. If you
  wrote "steady", "constant", "looping" or the like, nothing is added. If you asked for
  things to happen ("occasional thunder", "an owl now and then"), only "continuous" is
  added. The record keeps both: what you typed and what was sent.
- **The loop is written into the WAV** (a `smpl` chunk with one forward loop over the whole
  file), the way samplers and audio tools mark a loop. Not every game engine reads it, and
  we have not tested any yet. It does not matter much: the whole file is the loop, so
  ticking the sound's own "loop" setting in the game does the same thing.

A loop at the engine's 30 second limit comes out about a second shorter, since there is no
extra to blend. A sound that stops before the loop is long enough is made into a shorter
loop; under two seconds of sound it is dropped and the sheet says so.

**What a loop cannot do, yet.** *No variations*: changing a loop's pitch or volume is the
game's job (one setting there), and our varied takes would not join up. The button is not
shown and the server refuses. *Layers*: a kept loop can be a layer in a stack, but the
saved stack is an effect with faded ends. It does not loop. A loop built from layers is not
there yet.

The record fills two blocks that are empty for an effect:

```json
"sound_kind": "loop",
"loop": {"start_sample": 0, "end_sample": 383999, "crossfade_ms": 800.0},
"loudness": {"target": -20.0, "measured": -20.0, "peak": 0.33},
"post": {"...": "...", "join": {"step_ratio": 0.12, "slope_ratio": 1.3, "level_jump_db": -0.4,
                               "usual_level_jump_db": 1.3, "alike": 0.03, "taken_from_s": 0.5}}
```

`start_sample` and `end_sample` are the first and last sample played, counted from 0.
`crossfade_ms` is how long the blend is. `join` is the measure of the join
(`sound_post.seam_report`): `step_ratio` is the jump from the last sample to the first
against the usual jump between two samples (about 1 or less is "like any other pair"; a
click is tens or hundreds), `slope_ratio` the same for the change of direction, and
`level_jump_db` how much louder the first tenth of a second is than the last, beside what
is usual inside the loop. `alike` is how well the two blended ends matched (near 1 for a hum).

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

## Layers

Some sounds are two or three things at once: an arrow *whooshes past* and then *thuds* into
a target. Asked for in one go, the engine tends to blur them, or play the hit several times.
In our listen test the one-description arrow had 14 separate hits in it; made as two layers
it has the whoosh and one thud. So: make each part on its own, then stack them.

1. On the Sound sheet, describe the whole sound and press **Build it from layers**. A small
   rule table splits it into two or three descriptions (a lead-in, the hit, a tail), each to
   edit. With no rule for what you typed, you get one layer as typed and add the rest yourself.
2. Press **Make** on each layer (1 to 4 tries, about a minute a try), play the tries and press
   **Use this one**.
3. **+ Add a layer** for more, up to 8: **Describe it**, **Use my own file**, or **From my
   Library** (a sound you kept).
4. Set each layer's **Starts at** (0 to 10 seconds, in ms), **Volume** (0 is as made; each 6
   down is about half as loud) and **Pitch** (steps up or down, 12 each way). Its block moves
   on the timeline as you type. Blocks are not dragged.
5. **Play together** to hear the mix. **Save as one sound** keeps it. It is then a normal
   kept sound: it plays in the Library, and Make variations and Footsteps take it.

**Edit layers** on a kept sound that was built this way opens its layers again, each where
it was. Saving makes a new sound; the old one stays.

### Your own file

A WAV, 16-bit or 24-bit, one or two channels, any sample rate, up to 20 MB and 30 seconds.
It is brought to one channel at 48 kHz. Anything else (MP3, 8-bit, 32-bit float, silence) is
refused with the reason. The record keeps the file's name, the hash of the file as you gave
it, what it was, and what you said about it:

| Licence | Means | In the record |
|---|---|---|
| **Not checked** (default) | You are not sure | unverified |
| **Mine** | You made it | "Your own work", commercial-clear |
| **CC0** | Given away freely | commercial-clear, **if** you say where it's from |
| **CC-BY (credit needed)** | Free with credit | commercial-conditional, **if** you say where it's from |

**Where it's from** is a link to the page you got it from. A Creative Commons choice with
no link cannot be checked by anyone, so it is recorded as not checked. We record what you
state; we do not check it for you.

### The licence of the finished sound

The same rule as anything else here built from several sources: the layers' licence names
are joined with " + " (the same licence twice is named once), and the result is unverified
unless every layer is in the same class. So:

- engine + engine: `Apache-2.0`
- engine + CC0 (with a link) or Mine: `Apache-2.0 + CC0`, commercial-clear
- engine + CC-BY: `Apache-2.0 + CC-BY`, **unverified** (two classes: read both)
- anything + Not checked: **unverified**. The screen says "Not checked".

### The mix

Every layer is one channel at 48 kHz. Each is shifted in pitch (by resampling, so **higher is
also shorter**), given 5 ms fades so its ends never click, and set to its volume. Then they
are **summed**, and the sum is turned down **once** if it passes a peak of 0.98. It is never
turned up, and no layer is levelled on its own, so the balance you set is the balance you
get. Loudness is measured on the mix (average power, in dB below full scale) and written to
the record. Out-of-range numbers are refused, not pulled inside. Play together and Save run
the same code (`sound_post.mix_layers`); the browser only plays what the server mixed.

### What is saved

```
output/sounds/<name>__<date-time>/sound.wav
output/sounds/<name>__<date-time>/sound.provenance.json
output/sounds/<name>__<date-time>/layers/layer-1.wav ...   each layer on its own, to mix again
output/sounds/.layers/                                      layers being worked on; cleared after a week
```

The record is the one every kept sound has, with one entry per layer:
`{id, label, source, file, sha256, license, start_ms, volume_db, pitch_semitones, seconds}`.
`source.type` is `generated` (with the description, seed and settings), `file` (name, hash,
link, what it was) or `kept` (the kept sound's id and its own layers). `post` says how the
mix was made and how far it was turned down.

### The split rules

`image_to_3dlab/sound_split.py`, first match wins: a thrown thing hitting (arrow, spear,
knife), a door slamming, a footstep with an echo, a blade striking, an explosion, glass
breaking, a gunshot, a punch or kick, a spell, a splash, "... with an echo", and "A, then
B". They are starting points written by hand, not tuned by ear: only the arrow has been
made and measured. Edit what they give you.

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

## Voice: a line said out loud

Press **Sound**, then **Voice** under "What kind?".

1. **How should it sound?** Two choices, each its own engine and its own card on Setup:
   - **Clear and quick** reads a line plainly in about a second. English, 28 voices,
     American and British. Pick it for narration and plain talk.
   - **With feeling** can shout, whisper or sound sad. Nine voices, ten languages. Pick it
     for characters. A few seconds a line.
2. **Voice.** Each has one line on who it sounds like.
3. **How it's said** (With feeling only): Angry, Sad, Whisper, Shout, Calm, Excited, or your
   own few words ("like a tired old wizard"). Empty is a plain reading.
4. **Language** (With feeling only) or **Speed** (Clear and quick only).
5. **What to say**, then **Make**. With feeling makes four takes of each line; Clear and
   quick makes one, because it says a line the same way every time.

Several sentences are several lines. Each line has its own takes, its own **Keep**, and
**Make this line again**, which adds new takes of just that line and leaves the rest alone.
Pick a take of each and **Save as one** joins them in order, with a quarter-second gap, as
one sound in your Library. A very short sentence ("Halt!") stays with its neighbour.

### No copying of people

A voice is always one of the voices that come with the engine. There is no box to upload
or record a voice, no route on the server that takes one, and a request that tries is
refused: "AssetFurnace does not copy a person's voice." The record beside every line names
the voice that said it and says `"cloned": false`.

### How loud a line is

Lines sit level with each other: about -16 dB, measured **while someone is speaking**
(pauses are left out, or a line with long pauses would come out too loud). How it's said
moves that mark, so a shout stays louder than a whisper:

| How it's said | Level |
|---------------|-------|
| Whisper | -25 dB |
| Sad, Calm | -19 dB |
| plain, or your own words | -16 dB |
| Angry, Excited | -14 dB |
| Shout | -12 dB |

Speech has sharp peaks, so reaching the mark often means pressing the loudest syllables
down a little: by 6 dB at most, smoothly, and never by clipping. A line that would need
more is left a little under its mark, and its record says so (`held_by_peak`). A line too
short to measure (under 0.4 s of speech) is set by its loudest point instead. This is our
own simple measure, not the broadcast one (LUFS); the two land close for speech.

### The words, before they are said

- **Numbers and signs** are written out for English: "Dr." is said "Doctor", "3.5" is "three
  point five", "$5" is "five dollars", "1999" is "nineteen ninety-nine". Other languages are
  left to the engine. Your text is never changed: the record keeps what you typed and what
  was said.
- **Made-up names** (Clear and quick only). A word in no dictionary is said from its
  letters, and the take says "Guessed how to say: Eldoria". If the guess is wrong, spell it
  the way it sounds, or use With feeling, which reads names itself.
- **A longest length.** With feeling can get stuck repeating a line. Every line has a
  longest reasonable length (3 seconds, plus one a word); a take that reaches it is dropped,
  so a short line can never run for a minute.

### The two engines

| | Clear and quick | With feeling |
|---|---|---|
| Model | [Kokoro 82M](https://huggingface.co/hexgrad/Kokoro-82M), [MLX form](https://huggingface.co/mlx-community/Kokoro-82M-bf16) | [Qwen3-TTS 1.7B CustomVoice](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice), [8-bit MLX form](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit) |
| Size | 0.34 GB | 3.08 GB |
| Licence | Apache-2.0 | Apache-2.0, no region limits |
| Voices | 28, English | 9 |
| Languages | English | English, Chinese, Japanese, Korean, German, French, Spanish, Italian, Portuguese, Russian |
| Told how to say it | no | yes |
| Speed | 0.7 to 1.4 | no setting (say "speak slowly") |
| Same line twice | identical | different each take |

Both are run by [mlx-audio](https://github.com/Blaizzy/mlx-audio) 0.6.7 (MIT), from one
Python environment of their own, `vendor/voice/.venv` (about 0.5 GB, shared). Apple Silicon
only. The lines are yours, commercial use included. Qwen3-TTS's training data is not
published; Kokoro's maker says it used only audio that is free to use, which we have not
checked. Both are said on the record beside each line.

**No GPL.** Kokoro needs words turned into sounds first. That is done by misaki
(Apache-2.0), with spaCy and its small English model (MIT, 13 MB) and num2words (LGPL-2.1,
used as it comes). The usual helper for unknown words, espeak-ng, is GPL-3: it is **not**
installed, and neither is phonemizer. Our own letter rules stand in
(`image_to_3dlab/voice_text.py`). That is also why Kokoro speaks English only here: its
Spanish, French, Hindi, Italian and Portuguese need espeak-ng, and its Japanese and Chinese
need large dictionaries.

Measured on the machine this was built on: With feeling loads in about 5 seconds, then
about a third of a second for a short line, using about 5 GB of memory. Clear and quick
makes a short line in under a tenth of a second.

### From a terminal, and for agents

```bash
python scripts/bootstrap_voice.py --engine qwen       # or kokoro: says what it wants, then asks
python scripts/bootstrap_voice.py --engine qwen --verify   # the model files are the ones we tested
vendor/voice/.venv/bin/python scripts/voice_generate.py --engine qwen --voice ryan \
    --text "Halt! Who goes there?" --direction shout
```

The last command never downloads: with anything missing it stops and says what.

Agents: `list_voices()` lists the engines and every voice; `make_voice(text, engine, voice,
direction, language, speed, tries, seed)` starts the lines; `sound_status(job_id)` follows
them (each try says which line it is) and `keep=<try>` keeps one;
`join_voice_lines(job_id, tries, gap_ms, name)` saves one try per line as one sound.

Routes (`viewer/voice_api.py`): `GET /api/voice/defaults`; `POST /api/voice` `{text | lines,
engine, voice, direction, language, speed, count, seed, using}`; `POST /api/voice/join`
`{parts: [{job_id, try}], gap_ms, name}`. A voice job is a sound job, so status, keep,
cancel and the try WAVs are the `/api/sound/<job>/…` routes.

The record is the same shape as any sound's, with `"sound_kind": "voice"` and a `voice`
block: `text`, `spoken_text`, `engine`, `model`, `revision`, `voice`, `direction`,
`direction_words`, `language`, `speed`, `seed`, `line`, `take`, `cloned: false`, and
`guessed_words` when there were any. `loudness` says the mark, what was measured, the level
it ended at and how far the peaks were pressed.

## Music: a tune from a description

Press **Sound**, then **Music** under "What kind?".

1. Install the engine once: **Setup → ACE-Step 1.5**. It tells you what it fetches (10.1 GB
   of model files in four parts, plus about 0.7 GB of Python packages) and waits for a yes.
2. **Describe the music**: style, mood, instruments. *"Calm fantasy village theme, harp,
   flute, soft strings"*.
3. **With singing** is off: the music is instrumental, which is what most games want. Turn
   it on and a box opens for the **words to sing**, one line of the song on each line.
4. Pick a **Length**, from 10 seconds to 4 minutes. A tune comes to its own ending, often a
   few seconds before the length you picked (ours ran 24 to 29 seconds of 30); each try
   shows its real length. A loop is the length you picked.
5. **Plan the song first** is on. A second model, the song planner, reads your description,
   writes a fuller one, picks a tempo and a key, and sketches the tune before the music model
   plays it. It adds a few seconds. Turn it off for a quicker, looser try.
6. **Loops** makes a tune that plays round and round with no click, for a level or a menu.
   See below. A loop has no singing.
7. Press **Make 2**. Play each try, look at its wave, and press **Keep** on the good one.

A try made with the planner says what was planned for it: "24 s · 91 beats a minute · C
major".

Kept music shows under **Library → Sounds** with the rest. Music has no variations: a tune
at another pitch is another tune. Make more tries instead.

### Stereo, and how loud

Music keeps its two channels: it is heard as it is, not placed in the game's world, so the
WAV is stereo at 48 kHz (effects, loops and voice lines are one channel). The record says
`"channels": 2` and `"stereo": true`.

A tune is set by its **average** level, to -18 dB below full scale, and its loudest sample
is never past 0.98. Music has sharp peaks, so a tune sometimes cannot reach the mark without
clipping; it is then held just under, and the record says `held_by_peak`. Our four test
tunes came out between -18.9 and -18.0 dB. (We first aimed at -16 dB; every tune was held
back by its peaks and they landed up to 2.5 dB apart, so the mark moved to what real tunes
reach.)

Dead air is cut from both ends. A tune still playing where it stops fades out over two
seconds, so it never ends on a cut; one that came to its own ending is left as it is. The
record's `post.ended` says which.

### Music that loops

With **Loops** on, the engine is asked for a quarter more than the loop, plus three and a
half seconds. The first two seconds are left out (tunes start with a lead-in), the tune's
own ending is left out too, and what follows the loop is blended into its start over up to a
second: the same way a background loop is joined, on both channels alike. The WAV has the
loop written inside, and tries play round and round in the studio, with **Hear the join**.

What to expect, honestly:

- **No click.** Measured on two 20 second battle loops: the jump across the join was 0.1
  and 0.7 times the usual jump between two samples, and the level changed by under 1 dB.
- **It is a blend, not a cut on the bar line.** Where the tune comes round, a second of
  what came after its end is heard under its start. Nothing forces the tune to a whole
  number of bars, and in both test loops the two sides did not line up beat for beat. A
  drummer would notice; under gameplay it mostly passes. Nobody has listened to these yet.
- **Leave "Plan the song first" on for loops.** Unplanned, our battle tune was loud for
  ten seconds and then dropped by 15 dB for the rest. Planned, it held one level all the way
  round. The words "seamless loop, steady, no intro, no ending" are added to the description
  either way (the record's `prompt_sent`).

### From words only

The engine can also take a recording and restyle it. That is never used: there is no box to
upload one, no route on the server that takes one, and a request that names one is refused:
"AssetFurnace makes music from words only." The record says `"made_from": "words only"`.
Its makers warn that a tune can come out sounding like an existing one by chance, so listen
before you ship, and do not ask for a named artist's sound.

### The engine

[ACE-Step 1.5](https://huggingface.co/ACE-Step/Ace-Step1.5), run by its makers' own
[ace-step](https://github.com/ace-step/ACE-Step-1.5) package at one pinned commit. Both are
MIT: the music is yours, commercial use included. The model card says it was trained on
licensed music, royalty-free and public-domain music, and audio made from MIDI. We have not
checked that, and the record beside each tune says so in those words.

Four parts, one download:

| Part | Size | What it does |
|------|------|--------------|
| music model | 4.79 GB | Plays the tune |
| song planner | 3.76 GB | Plans it first: tempo, key, structure. Used when "Plan the song first" is on |
| text reader | 1.21 GB | Reads your description and lyrics |
| sound decoder | 0.34 GB | Turns the music model's output into sound |

On a Mac the music model and the sound decoder run on MLX (Apple's own number library), the
planner on mlx-lm, and the text reader on PyTorch. So this is Apple Silicon only for now.
It lives in its own Python environment, `vendor/music/.venv`: 50 packages, each pinned. The
engine's own install list is much longer (a web page, training tools, other download
sites); none of that is installed, because making music does not use it.

The engine downloads any model it cannot find. Ours never gets the chance: the runner
checks every file first, pins itself offline, and stops if one is missing. The engine also
writes its own code files into its model folder, so it is given `vendor/music/checkpoints/`,
a folder of links to the weights, and never the download itself.

Measured on the machine this was built on (M5 Max, 64 GB):

| What was made | Whole run | Memory at the peak | Came out |
|---------------|-----------|--------------------|----------|
| 30 s village theme, planner off | 15 s | 30 GB | 25.9 s, -18.8 dB |
| the same, planner on | 28 s | 34 GB | 24.1 s, -18.9 dB, planned at 91 beats a minute in C major |
| 20 s battle loop, planner off | 16 s | 30 GB | 20.0 s, -18.0 dB |
| the same, planner on | 30 s | 32 GB | 20.0 s, -18.0 dB |

A whole run includes about 10 seconds of loading; the planner adds about 13 (most of it
loading its word list) and 3 to 4 GB. In the studio, two 30 second tries with the planner
took 39 seconds from pressing Make.

Smaller Macs are untested. Most of that memory is the engine keeping a second copy of the
music model for PyTorch beside the one MLX plays from.

### From a terminal, and for agents

```bash
python scripts/bootstrap_music.py             # says what it wants, then asks
python scripts/bootstrap_music.py --verify    # the model files are the ones we tested
vendor/music/.venv/bin/python scripts/music_generate.py \
    --description "calm fantasy village theme, harp, flute, soft strings" --seconds 30
vendor/music/.venv/bin/python scripts/music_generate.py \
    --description "tense battle music, drums, low brass" --seconds 20 --loop
```

The last two never download: with anything missing they stop and say what. `--no-plan`
skips the song planner; `--lyrics "..."` makes it sing.

The same seed makes the same tune again, planner and all: two runs gave the same file,
byte for byte.

Agents: `make_music(description, seconds, lyrics, language, plan_first, loop, tries, seed)`
starts a batch; `sound_status(job_id)` follows it (each try says what was planned for it)
and `keep=<try>` keeps one.

Routes (`viewer/music_api.py`): `GET /api/music/defaults`; `POST /api/music` `{prompt,
seconds, singing, lyrics, language, plan, loop, count, seed, name}`. A music job is a sound
job, so status, keep, cancel and the try WAVs are the `/api/sound/<job>/…` routes.

The record is the same shape as any sound's, with `"sound_kind": "music"` and a `music`
block: `description`, `instrumental`, `lyrics`, `language`, `seconds_asked`, `seed`,
`planner_used`, `planned` (the planner's tempo, key and fuller description), `parts_used`,
`model`, `revision`, `stereo`, `loops` and `made_from`. `loudness` says the mark, what was
measured and whether the peak held it back; a loop also fills `loop` and `post.join`.

## The sound-effect engine

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
same goes for an empty description, more than four tries, a loop under 4 seconds, or music
past 4 minutes.

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

A background loop skips steps 2, 4 and 5 and takes its own route (`clean_loop`): see
[Background loops](#background-loops).

**Volume.** A fresh sound is turned up as well as down, so four tries are as loud as each
other and none comes out too quiet. It is never turned up by more than 24 dB (about 16
times): anything needing more is mostly hiss, and true silence is left alone so the check
still drops it. Never clipped. This is for fresh sounds only. Once volumes mean something
(a variation set, a mix of layers) a sound is only ever turned down.

**Tails.** A coin ding asked for at 1 second is still ringing at 1 second. The engine makes
30 seconds every time anyway, so we ask it for extra at no cost: as much again, at least a
second more, never past 30 in all. The words it is given still say the length you asked
for, so the sound itself does not change. Then:

- Quiet at the length asked (45 dB under its loudest point): it ends there, as before.
- Still ringing: it runs on until it has been that quiet for 30 milliseconds.
- Not that quiet by the end of the extra, but at least 6 dB down: all the extra is kept.
- Not dying away at all (rain, an engine): it ends at the length asked. More is not a tail.

A tail that died away ends on a 30 millisecond fade-out. A sound cut while it is still
going (the last two) gets a long, smooth one so it does not sound chopped: up to 300
milliseconds, and no more than 40% of the tail kept or, for a steady sound, of the sound. So
a sound can be a little longer than asked; the sheet and the Library show its real length.
The record says what happened: `gain_db` (how far the volume moved), `tail_s` (seconds kept
past the length asked) and `fade_out_ms`; the length asked is `seconds_asked` on the layer.

## Where it lives

```
output/sounds/<name>__<date-time>/sound.wav
output/sounds/<name>__<date-time>/sound.provenance.json
output/sounds/.tries/        tries not kept yet
```

A sound is a list of layers: one for a sound kept from a try, several for one built
from layers (see Layers). A background loop fills `loop` and `loudness` (see Background
loops). There is room for "made for this move". Variation sets have a record of their own
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

When a sound is made from several sources (Layers), its licence follows the rule the rest of
the repo already uses for anything built from more than one thing: the names are joined
with " + ", and if the sources are not all in the same class, or one is unknown, the result
is marked unverified.

## From a terminal

```bash
python scripts/bootstrap_sound_effects.py          # says what it wants, then asks
vendor/moss-sfx/.venv/bin/python scripts/sound_generate.py \
    --prompt "sword striking a wooden shield, one hit, dry" --seconds 1.5 --leave-out music
vendor/moss-sfx/.venv/bin/python scripts/sound_generate.py \
    --kind loop --prompt "rain on a tent" --seconds 10
```

The second command never downloads: with the model files missing it stops and says so.

## For agents

`make_sound(description, seconds, leave_out, tries, seed, kind)` starts a batch
(`kind="loop"` for a background loop, 4 to 30 seconds; a kept loop is listed with
`loop: true` and has no variations);
`sound_status(job_id, wait_seconds)` follows it; `sound_status(job_id, keep=2)` keeps a try
and answers its file path; `list_assets(kind="sound")` lists the kept ones and the saved
sets. `make_variations(sound_id, count, how_different, change)` saves a set from a kept sound
and answers its folder and files. `footsteps(asset_id, move, sound_id)` finds the steps in a
character's move, saves the cue file and answers the times; `save=False` only looks.

`split_sound(description)` answers layers to make, and
`make_layered_sound(layers, name)` builds and saves a stack. Each layer is `{describe}`,
`{sound_id}` or `{job_id, try}`, plus `start_ms`, `volume_db`, `pitch_semitones`. Described
layers are made one per call: it answers `status: "making"` and the layers to send back
until all exist, then `status: "saved"`. Agents cannot bring a file: stating a file's
licence is for the person, in the studio.

Layer routes (`viewer/sound_layers_api.py`), all behind the same door as the rest:
`POST /api/sound/layers/split` `{description}`; `/add` `{job_id, try}` or `{sound_id}`;
`/upload` (a form: `file`, `licence`, `link`); `/mix` and `/save` `{layers: [{layer,
start_ms, volume_db, pitch_semitones}], name}`; `POST /api/sounds/<id>/layers` opens a kept
stack; `GET /api/sound/layers/<id>.wav` and `/api/sound/layers/mix/<key>.wav` play.

The server routes are `/api/sound…` for making sounds, and for variations
`POST /api/sounds/<id>/variations` (takes to listen to; writes nothing) and
`POST /api/sounds/<id>/set` (saves them). Both take
`{count, how_different, change, seed, name}`. Footsteps: `GET /api/footsteps?file=<move>` finds
the steps and writes nothing; `POST /api/footsteps` with `{file, sound_id, on}` saves them
(`viewer/footsteps_api.py`). `viewer/sound_api.py` is the code for the rest; the maths
is in `image_to_3dlab/sound_post.py`.
