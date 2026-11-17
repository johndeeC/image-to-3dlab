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
