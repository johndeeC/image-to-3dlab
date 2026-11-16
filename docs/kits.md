# Kits: a character and the gear made for it

A **kit** groups one character with the prop sets (and single props) made for it: the
knight, then their sword, shield and helmet, kept together so they can be used together
in a game. Today the Library is flat; a kit is the folder it was missing.

## Flow

1. Make a character as usual (**+ Create → A moving character**).
2. In its Steps panel, press **Make gear**. Create opens on **Nine props** with a prompt
   already written from the character's own: *"a 3x3 grid of nine separate items for
   <character>: …"*. Name the nine things you want, then start.
3. When the sheet is made and split, the prop set joins the character's kit (the kit is
   made on the first **Make gear**).
4. Open the kit (**Library → Kits**, or **Open kit** on the character): a pane with one
   row of steps per member, and every split prop in one grid.

## Where it lives

`output/.kits.json`, beside the Library's own `.assets-meta.json`. No schema bump, no
files moved: a kit only lists asset ids.

```json
{"kits": [{"id": "kit-1a2b3c4d", "name": "Knight kit",
           "character": "<asset id>", "gear": ["<asset id>", "..."], "created": 1763190000}]}
```

Deleting a kit removes the grouping only. An asset that was deleted shows as *missing*
in the kit instead of breaking it. `viewer/kits_api.py` reads and writes the file; the
server routes are `/api/kits…`.

## The kit pane: a node for each step

Each member is a row of nodes, one per step: Picture → 3D model → Finish → Rig → Animate
for the character, Picture → 3D model → Split for a prop set. The gear rows hang off the
character's row. A node shows its state (done, ready to run, waiting) and:

- **Make** on a node that is ready: runs that step with the usual defaults.
- **Redo** and **×2 / ×3 / ×4** on a done node: make that step again 1–4 more times.
  Each try is a new version; nothing is replaced. Runs go one after another (the studio
  runs one thing at a time), queued in the open page.
- **Options**: every try of that step so far, as small thumbnails. Pick one and the kit
  uses that version from then on (its later steps follow it).

What counts as a try: for Picture, every picture drawn from the same prompt; for the
other steps, every version in the same family that shares this one's earlier steps.

Below the rows, **All props** shows every split prop of every prop set in the kit at
once. Click one to look at it in 3D.

## Agents

`list_kits`, `make_kit(character_id, name)` and `add_to_kit(kit_id, asset_id)` in the MCP
server. After a `create(want="set", …)` run is done, `add_to_kit` files its result.

## Not in this first cut

- The ×N queue lives in the open page: close the tab and only the running try finishes.
- Gear is not fitted to the character (sockets, scale to the hand): props keep their own
  size.
- A kit holds one character. Two characters sharing gear means two kits.
