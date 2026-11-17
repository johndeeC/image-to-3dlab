# Canvas: draw the steps, then run them

The **Canvas** is a board where you place step boxes (we call them *nodes*), join them
with lines, and press **Run**. It is Create with the steps laid out in the open, so a
plan can branch: one picture into two 3D engines, or one sheet of nine into nine props
that each get rigged.

Nothing new makes anything. Every node is a step the studio already runs, and a graph
runs as ordinary runs through the same runner Create uses (`viewer/activity_api.py`).
Each one lands in the Library and the activity history like any other run.

## The nodes

| Node | Takes | Makes | Runner step |
|------|-------|-------|-------------|
| Picture | nothing (a description) or a picture already made | a picture | `picture` (skipped for a picture already made) |
| 3D model | a picture | a 3D model | `model`, with the node's engine |
| Split into props | a 3D model of a sheet of nine | several finished props | `split` |
| Finish | a 3D model | a game-ready model | `finished` |
| Rig | a 3D model, a game-ready model, or props | a rigged model | `rigged` |
| Move | a rigged model | a moving model | `animated`, with the node's move |
| Export | anything with a 3D model | nothing new: lists the files to take | none |

A Picture node says what it is for: a **character**, **one prop**, or a **sheet of nine**.
That picks the wording added to the description (the same wording Create adds,
`image_to_3dlab/prompt_recipes.json`) and Finish's defaults (a character is 1.8 m tall; a
prop stands upright). A Split node only takes a sheet of nine.

## The graph format

Saved in `output/.graphs.json`, beside the run history (`.activity.jsonl`) and the kits
(`.kits.json`). It only names steps and files, never copies them.

```json
{"graphs": [{
  "schema": 1, "id": "graph-1a2b3c4d", "name": "Knight and gear", "updated": 1763190000,
  "nodes": [
    {"id": "n1", "type": "picture", "x": 40,  "y": 80, "settings": {"prompt": "a rune knight", "goal": "character"}},
    {"id": "n2", "type": "model",   "x": 260, "y": 80, "settings": {"engine": "pixal3d"}},
    {"id": "n3", "type": "finish",  "x": 480, "y": 80, "settings": {}},
    {"id": "n4", "type": "rig",     "x": 700, "y": 80, "settings": {}},
    {"id": "n5", "type": "move",    "x": 920, "y": 80, "settings": {"move": "walk"}}
  ],
  "edges": [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"}, {"from": "n3", "to": "n4"}, {"from": "n4", "to": "n5"}],
  "last_run": {"status": "done", "results": {"n2": [{"file": "pixal3d/.../model.glb", "status": "done"}]}}
}]}
```

- `x`, `y` are where the box sits on the board; nothing else reads them.
- Every node has at most one line in. A Picture has none.
- A node may have any number of lines out. That is the branching.
- `last_run` is what the last run made, per node, so a reopened canvas shows it.
- A half-drawn graph saves fine. It is only checked in full when you press Run.

## From a graph to runs

`viewer/graph_api.py` turns a graph into **stages**: one stage is one ordinary run, a
`Chain` with a list of steps. The rules, as `plan_graph()` applies them:

1. Start at each Picture node and walk down the lines.
2. While a node has exactly one line out to a step node, the next node joins the same
   stage. Picture → 3D model → Finish → Rig → Move is **one** run, exactly what Create
   would send for "A moving character".
3. A node with two or more lines out ends its stage. Each line starts a new stage that
   begins with what the first one made (the runner's `made`: picture, model, finished...),
   so the shared part runs once, not once per branch.
4. A Split node also ends its stage. The stages after it are **for each prop** (below).
5. Export nodes are not steps. They hang off a node and list that node's files.

Stages run one after another, a parent before its children (the studio makes one thing
at a time). If a stage fails, the stages after it are skipped; branches that do not
depend on it still run. Cancel stops the running stage and skips the rest.

The run lives on the server (`GraphRunner`), like a Create run: closing the tab does
not stop it. Each step lands in `output/.activity.jsonl` as usual.

## Fan-out: one sheet, many props

```
Picture (sheet of nine) → 3D model → Split into props ─┬→ Rig
                                                       └→ Export
```

Runs as:

- **Run 1**: Picture → 3D model → Split into props. The split makes up to nine finished
  props under `output/props/<run>/finished/`. The runner now records that folder as
  `made["split"]`.
- **Run 2, once per prop**: Rig, starting from that prop as its game-ready model. Nine
  props, nine short runs.
- Export lists every prop and every rig.

Two lines out of a 3D model work the same way without the "per prop": one model, then a
Finish run and a Rig run of the raw model, side by side.

## Kits

A kit (`docs/kits.md`) is a character plus the gear made for it, and its pane draws each
member as a row of step nodes. The canvas is the free-form version of the same picture,
with the same node look. Filing a canvas's results into a kit is the natural next step;
it is not in this first cut.

## Not in this first cut

- Starting from a 3D model file, front-and-back pictures, and Finish's detail choices:
  each node uses the usual defaults. The node settings are where they go next.
- A Picture node can use a picture already made (`"file"` in its settings), and runs that
  way, but the canvas has no picker for one yet.
- Agents cannot draw or run a canvas yet; they have `create` and `continue_asset`.
  A `run_graph` tool would take the same JSON.
- Results are not filed into a kit.
- Nodes are not run one at a time from the canvas; Run runs the whole graph.

## Next: closing the gaps with Tripo's canvas

Advice taken before round two (a second opinion, read from the code only). The decisions:

**Keep the hand-rolled board, no library.** A canvas holds up to 60 boxes and each box
has one line in, so the graph is a forest. Zoom and pan are one CSS `transform`; tidying
is a short tree layout. Boxes hold real form controls, which libraries that draw on a
`<canvas>` (LiteGraph) cannot. The others need a framework or a bundler (React Flow, Rete),
or own their own data model (Drawflow). The studio has no build step.

**Pure modules, thin page.** Each is a plain ES module that Node tests import:

| File | What it holds |
|------|---------------|
| `canvas.js` | the graph: add, connect, remove, `takersOf`, `addConnected`, groups, picks |
| `canvas-view.js` | zoom and pan: `zoomAt`, `panBy`, `fitAll`, `screenToBoard` |
| `canvas-select.js` | `nodesInRect`, selection toggles |
| `canvas-history.js` | undo and redo; a drag or a typed word is one step |
| `canvas-clip.js` | copy and paste with new ids, inner lines kept |
| `canvas-layout.js` | `tidy`: a left-to-right tree layout |
| `canvas-keys.js` | the shortcuts table; the shortcuts panel is drawn from it |
| `canvas-settings.js` | each box's settings, one table checked against the server's list |

`canvas-pane.js` only turns events into actions and patches boxes by id, not rebuilding
them all on each change.

**Server.** A box's **Run** and **Run from here** are `plan_from(graph, node, mode)`. It
starts from what the box's parents made last time (their picked file), so the runner is
unchanged. A Picture asked for 2–4 options ends its stage like a Split. The boxes after it
wait until you pick one, and picking runs from there. The 3D model and the split get their
own settings, passed to the routes that already check them. The format goes to
`schema: 2`. Schema 1 still reads; the new parts are only additions: `groups`, `picked`,
`view`, and typed settings.

**Order.** One small commit each, test first:

1. patch boxes by id; fix the fonts and the narrow width
2. zoom and pan
3. selection
4. undo and redo
5. add a box: double-click, Tab, the + menu, a line dropped on empty board
6. copy and paste, connect selected, tidy, hide lines
7. per-box run on the server
8. per-box Run buttons
9. box settings
10. picture options
11. agent tools
12. groups and the shortcuts panel
13. new box types

**New boxes, and what backs them:**

| Box | Backed by |
|-----|-----------|
| From Library (picture or model) | a Picture's `file`; a model root seeds `made["model"]` |
| Front + back | the `back` step (`/api/image/back`); only Pixal3D uses the back |
| Parts by name | Split into props with names (`props_api.normalise_names`) |
| Cut out background | `image_to_3dlab.matte.cut_out`; needs a small job route first |
| New look | skins (`/api/skins`); not one-shot, it asks you to approve pictures |

No local engine yet, so these are designs only and nothing is downloaded. The user picks
the backend, the route and the size first. The licences and sizes below have not been
checked against each project's own page yet; check them before choosing.

| Box | Candidates |
|-----|------------|
| Quad remesh | Blender's QuadriFlow (ships with Blender), Instant Meshes (BSD-3, a few MB) |
| UV unwrap | `scripts/blender_reunwrap_bake.py` and xatlas, both already here |
| Re-texture | Hunyuan3D-Paint 2.1 (several GB; **not licensed in the EU, UK or South Korea**), MV-Adapter (Apache-2.0, about 7 GB) |
| Split a model into parts | Blender's loose parts (no weights), PartField (**non-commercial**), Hunyuan3D-Part (**same region limits**) |

"Remove lighting" is not something any engine here says it does, so it stays off the
3D model box until one does.

**Agents.** `list_canvases`, `get_canvas`, `save_canvas`, `edit_canvas` (a list of add,
remove, connect, set and pick operations), `check_canvas`, `run_canvas`, `canvas_status`,
`cancel_canvas_run` and `box_types`. A save checks the canvas has not changed since it was
read, so the studio tab and an agent don't overwrite each other.

**In-studio chat: a proposal, not built.** A side panel sends your words and the canvas to
a model and gets `edit_canvas` operations back. The model is yours: a local server or a key
you paste; the studio ships none. "Ask first" shows the changes to accept; "Go ahead" applies
and runs them. An "Instructions" box is the same call for one box. Same operations as the
agent tools, so one code path and one set of tests.
