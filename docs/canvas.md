# Canvas: draw the steps, then run them

The **Canvas** (beside **+ Create**) is a board where you place step boxes (*nodes* in
code), join them with lines, and run them. It is Create with the steps laid out in the
open, so a plan can branch: one picture into two 3D engines, or one sheet of nine into nine
props that each get rigged.

Nothing new makes anything. Every box is a step the studio already runs, and a canvas
runs as ordinary runs through the same runner Create uses (`viewer/activity_api.py`).
Each run lands in the Library and the activity history like any other.

## The boxes

| Box | Takes | Makes | Runner step |
|-----|-------|-------|-------------|
| Picture | nothing: a description, or a picture you upload or pick from the Library | a picture | `picture` (skipped for one you gave) |
| Your 3D model | nothing: a model picked from the Library | a 3D model | none; it starts the run with that file |
| Front + back | a picture | a front and back picture | `back`; only the Best detail engine uses the back |
| 3D model | a picture, or a front and back | a 3D model | `model`, with the box's engine |
| Split into props | a 3D model of a sheet | finished props, named if you name them | `split` |
| Finish | a 3D model | a game-ready model | `finished`, with the box's choices |
| Rig | a 3D model, a game-ready model, or props | a rigged model | `rigged` |
| Move | a rigged model | a moving model | `animated`, with the box's move |
| Export | anything with a 3D model | nothing new: lists the files to take | none |

Each box keeps the choices Create and the Steps panel offer, the main one on show and the
rest under **More settings** (`viewer/studio/canvas-settings.js`):

- **Picture**: what to draw, what it is for (a character, one prop, a sheet of nine, just a
  picture), how many options to draw (1–4), its shape, and which picture engine draws it.
  What it is for picks the wording added (`image_to_3dlab/prompt_recipes.json`) and
  Finish's defaults: a character is 1.8 m tall, and a prop stands upright.
- **3D model**: the engine, named by what it does ("Best detail", "Fastest, rough").
- **Finish**: detail (triangles), texture size, surface maps, Pixel Match, clean-up, height
  and standing upright. Create puts triangle count and texture size on Finish, not on the
  3D model, so the canvas does too.
- **Split into props**: the props' names, one each, in reading order.
- **Rig**: bone names (numbered, or Unity Humanoid). **Move**: the move, speed, on the spot.

The server keeps the same list (`graph_api.SETTINGS`) and drops any value that does not
fit. A test checks the two lists agree, key for key and choice for choice.

## Getting about and editing

- **Zoom**: wheel, pinch, Cmd + / Cmd −, or the − 100% + buttons. **Fit** (Cmd 0) shows
  everything. **Pan**: hold Space and drag, drag with the middle button, scroll with two
  fingers, or pick the **Hand** (H). **Pointer** (V) picks and moves boxes.
- **Add a box**: double-click the board, press Tab, or **+ Add**. The same menu adds a
  ready-made line of boxes (Moving character, Nine props, Parts by name, Two engines).
- **Drop a line on empty board** to get a menu of only the boxes that take what that box
  makes. The one you pick arrives already joined.
- **Choose**: click, Shift/Cmd-click, or drag a rectangle; Cmd A for all. Delete removes;
  Cmd C / Cmd V copy and paste with the lines between them; Cmd Z / Cmd Shift Z undo and
  redo (one drag, or one word typed, is one step).
- **Cmd L** joins the chosen boxes left to right where they fit. **Cmd G** puts them in a
  named frame, Cmd Shift G takes it off; drag a frame by its name bar to move it all.
- **Alt Shift F** tidies the layout. **Lines** hides or shows the lines. **?** lists every
  shortcut (`viewer/studio/canvas-keys.js`; the panel is drawn from that table).
- Below 1150 px wide the studio stacks its columns, so the canvas takes the window under the
  top bar.

## The format

Saved in `output/.graphs.json`, beside the run history (`.activity.jsonl`) and the kits
(`.kits.json`). It only names steps and files, never copies them.

```json
{"graphs": [{
  "schema": 2, "id": "graph-1a2b3c4d", "name": "Knight and gear", "updated": 1763190000,
  "nodes": [
    {"id": "n1", "type": "picture", "x": 40, "y": 80, "settings": {"prompt": "a rune knight", "goal": "character", "count": 3},
     "picked": "images/rune-knight__b2.png"},
    {"id": "n2", "type": "model",   "x": 300, "y": 80, "settings": {"engine": "pixal3d"}},
    {"id": "n3", "type": "finish",  "x": 560, "y": 80, "settings": {"detail": "hero", "texture_size": 1024}}
  ],
  "edges": [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"}],
  "groups": [{"id": "g1", "label": "Body", "nodes": ["n2", "n3"]}],
  "last_run": {"status": "done", "results": {"n2": [{"file": "pixal3d/.../model.glb", "status": "done"}]}}
}]}
```

- Every box has at most one line in; a Picture or Your 3D model has none. A box may have any
  number of lines out: that is the branching.
- `picked` is the result that carries on, for a Picture asked for several options.
- `groups` are the frames. Where you left the board (zoom and pan) is kept in each browser, not
  here: two tabs on one board would otherwise refuse each other's saves over a pan. `view`
  is still read if present, and ignored.
- `last_run` is what each box made last time. Running one box replaces only that box's part.
- A half-drawn canvas saves fine; it is checked in full when it runs.
- `updated` is the version. A save that brings an older one is refused (409), so the studio
  tab and an agent never overwrite each other's change: the tab then shows the newer one.
- Schema 1 (round one) still reads. Schema 2 only added `groups`, `picked`, `view` and typed
  settings.

## From a canvas to runs

`viewer/graph_api.py` turns a canvas into **stages**: one stage is one ordinary run, a
`Chain` with a list of steps. `plan_graph()` applies these rules:

1. Start at each Picture or Your 3D model box and walk down the lines.
2. While a box has exactly one line out to a step box, the next box joins the same stage.
   Picture → 3D model → Finish → Rig → Move is **one** run, as Create would send it.
3. A box with two or more lines out ends its stage. Each line starts a stage that begins
   from what the first one made (the runner's `made`), so the shared part runs once.
4. A Split ends its stage, and the stages after it run **once per prop**.
5. A Picture asked for 2–4 options ends its stage too, and draws that many. The stages after
   it **wait for a pick**.
6. Export boxes are not steps. They hang off a box and list its files.

Stages run one after another, a parent before its children (the studio makes one thing at
a time). A failed stage skips what hangs off it; other branches still run. Stop ends the
running step and skips the rest. The run lives on the server (`GraphRunner`): closing the
tab does not stop it.

### One box at a time

Each box has a **▶** with **Run this box** and **Run from here**. `plan_from(graph, box,
mode)` starts from what the boxes above it made last time: their only result, or the one
picked. Those boxes don't run again, so their own settings aren't checked. Below a Split it
runs once for each prop that got that far. Picking a Picture option shows **Carry on with
this one**, which runs everything below it (mode `after`).

### Fan-out: one sheet, many props

```
Picture (sheet of nine) → 3D model → Split into props ─┬→ Rig
                                                       └→ Export
```

Run 1 is Picture → 3D model → Split; the split's props land under
`output/props/<run>/finished/`, and the runner records that folder as `made["split"]`.
Run 2 rigs each prop, once per prop. Export lists every prop.

**Parts by name** is the same line with names on the Split: describe one sheet of the parts
("the parts of a knight: head, hair, body, left hand, right hand"), and the split names each
part and makes it a 3D model of its own. Tripo cuts named parts out of a picture of the
whole thing; here each part is drawn on its own, which is what the prop sheet already does
well.

## Agents

`box_types`, `list_canvases`, `get_canvas`, `edit_canvas`, `save_canvas`, `check_canvas`,
`run_canvas`, `canvas_status` and `cancel_canvas_run` (`image_to_3dlab/mcp_server.py`).
`edit_canvas` takes a list of operations (`add` with `after` to join it, `remove`,
`connect`, `disconnect`, `set`, `pick`, `rename`), saves them with the version check, and
answers with how the canvas would run, or why it can't yet. `canvas_status` names the
Picture boxes waiting for a pick, so an agent can show the user the options and carry on.
Box types and their settings come from the studio (`/api/graphs/types`), not a copy.

## Not built yet

**Boxes the studio can back, later:**

| Box | Backed by | Why not yet |
|-----|-----------|-------------|
| Cut out background | `image_to_3dlab.matte.cut_out` (BiRefNet-lite, already installable) | No job route of its own; every 3D engine cuts the picture out anyway |
| New look | skins (`/api/skins`, `make_skin`) | Not one-shot: you approve a front and a back picture first |

**Boxes with no engine here yet.** These are designs only, and nothing has been
downloaded. You pick the engine, how it is set up, and its size first (`CLAUDE.md`,
"Weights are never downloaded without an explicit choice"). The licences and sizes below
are from memory and not yet checked against each project's own page; check them before
choosing.

| Box | Takes → makes | Candidates |
|-----|---------------|------------|
| Quad remesh | 3D model → 3D model with four-sided faces | Blender's QuadriFlow (ships with Blender, no download), Instant Meshes (BSD-3, a few MB) |
| UV unwrap | 3D model → 3D model laid out for painting | `scripts/blender_reunwrap_bake.py` and xatlas, both already here, no download |
| Re-texture | 3D model + words → newly painted model | Hunyuan3D-Paint 2.1 (several GB; **not licensed in the EU, UK or South Korea**), MV-Adapter (Apache-2.0, about 7 GB) |
| Split a model into parts | 3D model → parts | Blender's loose parts (no download), PartField (**non-commercial**), Hunyuan3D-Part (**same region limits as Hunyuan**) |

"Remove lighting" is not something any engine here says it does, so the 3D model box
leaves it out until one does.

**In-studio chat: a proposal.** A side panel sends your words and the canvas to a model,
and gets `edit_canvas` operations back. The model is yours, a local server or a key you
paste; the studio ships none (as `scripts/blender_agent.py` already works). **Ask first**
shows the changes to accept; **Go ahead** applies and runs them, pausing at a pick. An
**Instructions** box is the same call scoped to one box ("put these parts together as one
character"). It uses the same operations as the agent tools: one code path, one set of tests.

**Also left:** filing a canvas's results into a kit (`docs/kits.md`); a server restart in
the middle of a run leaves the last run marked as it was; Cmd + / Cmd − may zoom the page
instead in a browser that keeps those keys for itself.

## How the code is split

Advice taken before round two (a second opinion, read from the code only): keep the
hand-rolled board rather than a graph library. A canvas holds up to 60 boxes with one line
in each; zoom and pan are one CSS `transform`; boxes hold real form controls, which
libraries that draw on a `<canvas>` (LiteGraph) cannot; and the others need a framework or
a bundler (React Flow, Rete) or own their own data model (Drawflow). The studio has no
build step.

| File | What it holds (no page code; Node tests import each) |
|------|---------------|
| `canvas.js` | the graph: boxes, lines, which boxes take which, frames, picks, what a box shows |
| `canvas-view.js` | zoom and pan, fit, wheel or pinch |
| `canvas-select.js` | choosing boxes |
| `canvas-history.js` | undo and redo |
| `canvas-clip.js` | copy and paste |
| `canvas-layout.js` | tidy |
| `canvas-keys.js` | the shortcuts |
| `canvas-settings.js` | each box's settings |
| `canvas-pane.js` | the page: events in, boxes patched by id |
