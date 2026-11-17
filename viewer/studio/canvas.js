// The canvas: step nodes joined by lines (docs/canvas.md). The server (viewer/graph_api.py) turns
// a graph into ordinary runs; this file only edits the graph and reads a run back.
// No DOM here, so Node tests import this exact file.

/** Every node: its name, its one-line hint, what it takes in, what it makes, and its runner step.
 *  tests/test_studio_canvas.py checks takes/makes/step against graph_api.py. */
export const NODES = {
  picture: { tone: 'draw', label: 'Picture', hint: 'Draws a picture from your words, or uses one you already made.', takes: [], makes: 'picture', step: 'picture' },
  asset: { tone: 'source', label: 'Your 3D model', hint: 'Starts from a 3D model already in your Library.', takes: [], makes: 'model', step: null },
  back: { tone: 'draw', label: 'Front + back', hint: 'Draws the back too, so the 3D model is right all round. Best detail engine only.', takes: ['picture'], makes: 'pair', step: 'back' },
  parts: { tone: 'draw', label: 'Pick out parts', hint: 'Redraws each part you name (head, hair, hands) on its own, so each can become its own 3D model.', takes: ['picture'], makes: 'picture', step: 'part' },
  views: { tone: 'draw', label: 'Four views', hint: 'Draws its left, back and right too, so the 3D model is built from all four sides. Needs the Fast and clean engine.', takes: ['picture'], makes: 'views', step: 'views' },
  model: { tone: 'shape', label: '3D model', hint: 'Builds a 3D model from the picture.', takes: ['picture', 'pair', 'views'], makes: 'model', step: 'model' },
  split: { tone: 'shape', label: 'Split into props', hint: 'Cuts a sheet of nine into separate game-ready props.', takes: ['model'], makes: 'props', step: 'split' },
  repaint: { tone: 'finish', label: 'Repaint', hint: 'Paints the model again from a picture: a new look on the same shape.', takes: ['model', 'finished', 'props'], makes: 'model', step: 'repaint' },
  cutout: { tone: 'draw', label: 'Cut out background', hint: 'Takes the background off a picture, leaving just the subject.', takes: ['picture'], makes: 'picture', step: 'cutout' },
  remesh: { tone: 'shape', label: 'Quad remesh', hint: 'Rebuilds the surface as tidy four-sided faces, keeping its look. Smooth subjects fare best.', takes: ['model', 'finished', 'props'], makes: 'model', step: 'remesh' },
  unwrap: { tone: 'shape', label: 'UV unwrap', hint: 'Lays the texture out in large clean pieces, easier to paint over. Shape untouched.', takes: ['model', 'finished', 'props'], makes: 'model', step: 'unwrap' },
  combine: { tone: 'shape', label: 'Put together', hint: 'Puts every model coming in into one file, side by side, to arrange in Blender.', takes: ['model', 'finished', 'props'], makes: 'model', step: 'combine' },
  saveas: { tone: 'source', label: 'Save as', hint: 'Saves the model as FBX, OBJ or USDZ for other tools.', takes: ['model', 'finished', 'rigged', 'animated', 'props'], makes: null, step: 'export' },
  finish: { tone: 'finish', label: 'Finish', hint: 'Makes the model game-ready: cleaner shape, lighter file.', takes: ['model'], makes: 'finished', step: 'finished' },
  rig: { tone: 'move', label: 'Rig', hint: 'Adds a skeleton so it can move. Humanoids work best.', takes: ['model', 'finished', 'props'], makes: 'rigged', step: 'rigged' },
  move: { tone: 'move', label: 'Move', hint: 'Plays a move on the rigged model.', takes: ['rigged'], makes: 'animated', step: 'animated' },
  export: { tone: 'source', label: 'Export', hint: 'Lists the files to take into your game.', takes: ['model', 'finished', 'rigged', 'animated', 'props'], makes: null, step: null },
};

/** What a Picture is for: it picks the wording added to your words, and Finish's defaults. */
export const GOALS = { character: 'A character', prop: 'One prop', set: 'Sheet of nine', picture: 'Just a picture' };

const THING = { views: 'four views', picture: 'a picture', pair: 'a front and back picture', model: 'a 3D model', finished: 'a game-ready model', rigged: 'a rigged model', animated: 'a moving model', props: 'props' };

// A box is a square preview tile: its width (and height), and how far down its in and out dots
// sit (the middle, so lines meet tiles side-on).
export const NODE_W = 160;
export const PORT_Y = 80;

const clone = (graph) => JSON.parse(JSON.stringify(graph));

export function newGraph(name = 'Untitled canvas') {
  return { schema: 2, id: null, name, nodes: [], edges: [], groups: [] };
}

/** A new node at (x, y), with the next free id. */
export function addNode(graph, type, x, y, settings = {}) {
  if (!NODES[type]) throw new Error(`unknown node type ${type}`);
  const g = clone(graph);
  const used = new Set(g.nodes.map((n) => n.id));
  let k = g.nodes.reduce((top, n) => Math.max(top, Number(String(n.id).replace(/^n/, '')) || 0), 0) + 1;
  while (used.has(`n${k}`)) k += 1;
  g.nodes.push({ id: `n${k}`, type, x: Math.round(x), y: Math.round(y), settings: { ...settings } });
  return g;
}

export function removeNode(graph, id) {
  return removeNodes(graph, [id]);
}

/** Remove boxes, their lines, and their place in any group (an emptied group goes too). */
export function removeNodes(graph, ids) {
  const gone = new Set(ids);
  const g = clone(graph);
  g.nodes = g.nodes.filter((n) => !gone.has(n.id));
  g.edges = g.edges.filter((e) => !gone.has(e.from) && !gone.has(e.to));
  g.groups = (g.groups || []).map((gr) => ({ ...gr, nodes: gr.nodes.filter((id) => !gone.has(id)) })).filter((gr) => gr.nodes.length);
  return g;
}

export function moveNode(graph, id, x, y) {
  const g = clone(graph);
  const node = g.nodes.find((n) => n.id === id);
  if (node) Object.assign(node, { x: Math.round(x), y: Math.round(y) });
  return g;
}

/** Move several boxes by the same amount. */
export function moveNodes(graph, ids, dx, dy) {
  const moving = new Set(ids);
  const g = clone(graph);
  for (const n of g.nodes) if (moving.has(n.id)) Object.assign(n, { x: Math.round(n.x + dx), y: Math.round(n.y + dy) });
  return g;
}

/** The box types that can take what this type makes, for the menu a dropped line opens. */
export function takersOf(type) {
  const makes = NODES[type]?.makes;
  return makes ? Object.keys(NODES).filter((t) => NODES[t].takes.includes(makes)) : [];
}

/** A new box of `type` at (x, y), already joined to `from`. Its id is the last box's. */
export function addConnected(graph, from, type, x, y, settings = {}) {
  const g = addNode(graph, type, x, y, settings);
  return connect(g, from, g.nodes.at(-1).id);
}

/** Join the chosen boxes left to right, each to the next where it fits. Returns the graph and how many lines it drew. */
export function connectSelected(graph, ids) {
  const order = graph.nodes.filter((n) => ids.includes(n.id)).sort((a, b) => a.x - b.x || a.y - b.y);
  let g = graph, made = 0;
  for (let i = 1; i < order.length; i += 1) {
    if (canConnect(g, order[i - 1].id, order[i].id) === true) { g = connect(g, order[i - 1].id, order[i].id); made += 1; }
  }
  return { graph: g, made };
}

/** Put boxes in a labelled frame. A box is in one frame at most, so it leaves any other. */
export function group(graph, ids, label = 'Group') {
  const g = clone(graph);
  const inside = new Set(ids.filter((id) => g.nodes.some((n) => n.id === id)));
  if (!inside.size) return g;
  g.groups = (g.groups || []).map((gr) => ({ ...gr, nodes: gr.nodes.filter((id) => !inside.has(id)) })).filter((gr) => gr.nodes.length);
  let k = g.groups.reduce((top, gr) => Math.max(top, Number(String(gr.id).replace(/^g/, '')) || 0), 0) + 1;
  while (g.groups.some((gr) => gr.id === `g${k}`)) k += 1;
  g.groups.push({ id: `g${k}`, label, nodes: [...inside] });
  return g;
}

/** Take the frames off these boxes (or these frames). The boxes stay. */
export function ungroup(graph, ids) {
  const g = clone(graph);
  const hit = new Set(ids);
  g.groups = (g.groups || []).filter((gr) => !hit.has(gr.id) && !gr.nodes.some((id) => hit.has(id)));
  return g;
}

export function renameGroup(graph, groupId, label) {
  const g = clone(graph);
  const found = (g.groups || []).find((gr) => gr.id === groupId);
  if (found && String(label).trim()) found.label = String(label).trim().slice(0, 60);
  return g;
}

/** The rectangle around a frame's boxes, with room for its label. `sizes` maps id -> {w, h}. */
export function groupBounds(gr, nodes, sizes = {}, pad = 18) {
  const inside = nodes.filter((n) => gr.nodes.includes(n.id));
  if (!inside.length) return null;
  const size = (n) => sizes[n.id] || { w: NODE_W, h: 150 };
  const x = Math.min(...inside.map((n) => n.x)) - pad, y = Math.min(...inside.map((n) => n.y)) - pad - 20;
  const right = Math.max(...inside.map((n) => n.x + size(n).w)) + pad, bottom = Math.max(...inside.map((n) => n.y + size(n).h)) + pad;
  return { x, y, w: right - x, h: bottom - y };
}

/** Which of a box's results flows on (a Picture asked for several options). */
export function pickResult(graph, id, file) {
  const g = clone(graph);
  const node = g.nodes.find((n) => n.id === id);
  if (node) { if (file) node.picked = file; else delete node.picked; }
  return g;
}

/** Every box above this one, nearest first. */
export function upstreamOf(graph, id) {
  const parent = new Map(graph.edges.map((e) => [e.to, e.from]));
  const out = [];
  for (let at = parent.get(id); at && !out.includes(at); at = parent.get(at)) out.push(at);
  return out;
}

/** Every box below this one. */
export function downstreamOf(graph, id) {
  const out = [];
  const queue = [id];
  while (queue.length) {
    const at = queue.shift();
    for (const e of graph.edges) if (e.from === at && !out.includes(e.to) && e.to !== id) { out.push(e.to); queue.push(e.to); }
  }
  return out;
}

/** Change one setting; an empty value removes it. */
export function setSetting(graph, id, key, value) {
  const g = clone(graph);
  const node = g.nodes.find((n) => n.id === id);
  if (!node) return g;
  if (value === '' || value == null) delete node.settings[key];
  else node.settings[key] = value;
  return g;
}

/** true when a line from `from` to `to` may be drawn; otherwise the reason, in words. */
export function canConnect(graph, from, to) {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const a = byId.get(from), b = byId.get(to);
  if (!a || !b) return 'That node is gone.';
  if (from === to) return 'A node cannot feed itself.';
  if (graph.edges.some((e) => e.from === from && e.to === to)) return 'These two are already joined.';
  const into = NODES[b.type], out = NODES[a.type];
  if (!into.takes.length) return `${into.label} takes no line in: it starts a run.`;
  if (graph.edges.some((e) => e.to === to)) return `${into.label} already has a line in. Each node takes one.`;
  if (!into.takes.includes(out.makes)) {
    return `${into.label} takes ${into.takes.map((t) => THING[t]).join(' or ')}, and ${out.label} makes ${THING[out.makes] || 'nothing'}.`;
  }
  // walking up from `from` must never reach `to`
  const parent = new Map(graph.edges.map((e) => [e.to, e.from]));
  for (let at = from, seen = 0; at && seen <= graph.nodes.length; at = parent.get(at), seen += 1) {
    if (at === to) return 'The lines would go round in a loop.';
  }
  return true;
}

export function connect(graph, from, to) {
  const ok = canConnect(graph, from, to);
  if (ok !== true) throw new Error(ok);
  const g = clone(graph);
  g.edges.push({ from, to });
  return g;
}

export function removeEdge(graph, from, to) {
  const g = clone(graph);
  g.edges = g.edges.filter((e) => !(e.from === from && e.to === to));
  return g;
}

/** The SVG path of a line: from the right-hand dot of one box to the left-hand dot of the next. */
export function edgePath(a, b) {
  return curve(a.x + NODE_W, a.y + PORT_Y, b.x, b.y + PORT_Y);
}

/** The same curve between any two points (a line out of an option tile). */
export function curve(x1, y1, x2, y2) {
  const d = Math.max(40, Math.abs(x2 - x1) / 2);
  return `M ${x1} ${y1} C ${x1 + d} ${y1}, ${x2 - d} ${y2}, ${x2} ${y2}`;
}

// A box with several tries (a Picture asked for 4, or Make another) lays each out as a tile of its
// own in a column to its right, so you can carry on from any one: its size, gap, and distance.
export const OPTION_W = 96;
export const OPTION_GAP = 12;
export const OPTION_OFFSET = 40;

/** The option tiles of a box with tries: [{file, label, x, y, w, h, port: {x, y}}], or none. */
export function optionTiles(graph, node, files) {
  // a box's tries, and each part Pick out parts made: every one its own tile to carry on from
  if (!files || files.length < 2 || !(isChoice(graph, node.id) || node.type === 'parts')) return [];
  const x = node.x + NODE_W + OPTION_OFFSET;
  return files.map((f, i) => {
    const y = node.y + i * (OPTION_W + OPTION_GAP);
    return { file: f.file, label: f.label || `Try ${i + 1}`, x, y, w: OPTION_W, h: OPTION_W,
      port: { x: x + OPTION_W, y: y + OPTION_W / 2 } };
  });
}

/** Move everything after a box to the right, if needed, so `width` past its tile is free. */
export function makeRoom(graph, id, width) {
  const node = graph.nodes.find((n) => n.id === id);
  const below = downstreamOf(graph, id);
  if (!node || !below.length) return graph;
  const nearest = Math.min(...graph.nodes.filter((n) => below.includes(n.id)).map((n) => n.x));
  const dx = node.x + NODE_W + width - nearest;
  return dx > 0 ? moveNodes(graph, below, dx, 0) : graph;
}

/** Ready-made boards to start from. `engines` are the ready 3D engines, best first. */
export function starter(kind, { engines = [], move = null } = {}) {
  const row = (types, goal) => {
    let g = newGraph();
    types.forEach(([type, settings], i) => { g = addNode(g, type, 40 + i * 250, 60, settings); });
    for (let i = 1; i < g.nodes.length; i += 1) g = connect(g, g.nodes[i - 1].id, g.nodes[i].id);
    g.nodes[0].settings.goal = goal;
    return g;
  };
  const engine = engines[0] ? { engine: engines[0] } : {};
  if (kind === 'sheet') {
    const g = row([['picture', {}], ['model', engine], ['split', {}], ['export', {}]], 'set');
    return { ...g, name: 'Nine props' };
  }
  if (kind === 'parts') {
    const g = row([['picture', {}], ['parts', { names: 'head, hair, body, hands' }], ['model', engine]], 'character');
    return { ...g, name: 'Pick out parts' };
  }
  if (kind === 'compare') {
    let g = row([['picture', {}], ['model', engine]], 'prop');
    g = addNode(g, 'model', 290, 260, engines[1] ? { engine: engines[1] } : engine);
    return { ...connect(g, 'n1', 'n3'), name: 'Two engines, one picture' };
  }
  const g = row([['picture', {}], ['model', engine], ['finish', {}], ['rig', {}], ['move', move ? { move } : {}]], 'character');
  return { ...g, name: 'Moving character' };
}

/** What a node shows after (or during) a run: done / error / skipped / cancelled / waiting, and its files. */
export function nodeState(run, id) {
  const results = run?.results?.[id] || [];
  const files = results.filter((r) => r.status === 'done' && r.file).map((r) => ({ file: r.file, label: r.label ?? null }));
  const errors = results.filter((r) => r.status === 'error').map((r) => (r.label ? `${r.label}: ${r.error}` : r.error || 'It stopped.'));
  let state = null;
  if (errors.length) state = 'error';
  else if (results.some((r) => r.status === 'cancelled')) state = 'cancelled';
  else if (files.length || results.some((r) => r.status === 'done')) state = 'done';
  else if (results.length && results.every((r) => r.status === 'waiting')) state = 'waiting';
  else if (results.length) state = 'skipped';
  // only a box this run will reach waits for it; the others are left as they are
  else if (run?.status === 'running' && (run.stages || []).some((s) => s.nodes?.includes(id))) state = 'waiting';
  return { state, files, errors };
}

/** The node the running chain is on now, or null. */
export function runningNode(run, graph, chain) {
  if (!run || run.status !== 'running' || !chain?.current) return null;
  const stage = run.stages?.[run.stage];
  const types = new Map(graph.nodes.map((n) => [n.id, n.type]));
  // a Four views box runs three steps (left, back, right)
  const step = (id) => (types.get(id) === 'views' && ['left', 'back', 'right'].includes(chain.current) ? chain.current : NODES[types.get(id)]?.step);
  return stage?.nodes.find((id) => step(id) === chain.current) ?? null;
}

/** Whether a box's results are tries to choose between (a Picture's options, a box made again),
 *  not a set that all carries on (a Split's props, and every box below a Split). */
export function isChoice(graph, id) {
  const types = new Map(graph.nodes.map((n) => [n.id, n.type]));
  if (!types.has(id) || ['split', 'parts', 'views', 'export'].includes(types.get(id))) return false;
  return !upstreamOf(graph, id).some((up) => FANS.includes(types.get(up)));
}

/** Boxes whose results all carry on, each as its own run below: a Split's props, picked-out parts. */
export const FANS = ['split', 'parts'];

/** The try a line carries, or null. */
export function edgeFile(graph, from, to) {
  return graph.edges.find((e) => e.from === from && e.to === to)?.file ?? null;
}

/** A try of `from` dragged onto box `to`: join them if they are not, and the line carries that try.
 *  Throws the reason, in words, when `to` cannot take it. */
export function useResult(graph, from, file, to) {
  let g = graph;
  if (!g.edges.some((e) => e.from === from && e.to === to)) g = connect(g, from, to);
  g = clone(g);
  g.edges.find((e) => e.from === from && e.to === to).file = file;
  return g;
}

/** A try of `from` dragged onto empty board, made into a new box of `type` that starts from it. */
export function addFromResult(graph, from, file, type, x, y, settings = {}) {
  const g = addConnected(graph, from, type, x, y, settings);
  return useResult(g, from, file, g.nodes.at(-1).id);
}

/**
 * What a box's tile shows: one big preview, a mosaic of everything it made (a split's props, a
 * box below a split), or, before it has made anything, its own words. `state` is nodeState's.
 *   { kind: 'one' | 'mosaic' | 'empty', file, files, count, tryIndex, text }
 */
export function tileFace(graph, node, state) {
  const files = state?.files || [];
  const given = ['picture', 'asset'].includes(node.type) ? node.settings?.file : null;
  if (!files.length) {
    if (given) return { kind: 'one', file: given, files: [], count: 0, tryIndex: -1, text: '' };
    const words = node.type === 'picture' ? (node.settings?.prompt || 'Describe it in the side panel') : NODES[node.type].hint;
    return { kind: 'empty', file: null, files: [], count: 0, tryIndex: -1, text: words };
  }
  if (node.type === 'saveas' && files.length) {   // a file to download, not a picture or a model to show
    return { kind: 'empty', file: null, files, count: files.length, tryIndex: -1,
      text: `Saved: ${files.map((f) => String(f.file).split('/').pop()).join(', ')}` };
  }
  if (files.length > 1 && node.type === 'parts') {   // the parts sit beside it as tiles
    return { kind: 'empty', file: null, files, count: files.length, tryIndex: -1, text: `${files.length} parts: carry on from any one, or all` };
  }
  if (files.length > 1 && isChoice(graph, node.id)) {
    // tries sit beside the box as option tiles: the box shows the picked one, or its own words
    const picked = files.findIndex((f) => f.file === node.picked);
    if (picked >= 0) return { kind: 'one', file: files[picked].file, files, count: files.length, tryIndex: picked, text: '' };
    const words = node.type === 'picture' && node.settings?.prompt ? node.settings.prompt : `${files.length} tries: carry on from any one`;
    return { kind: 'empty', file: null, files, count: files.length, tryIndex: -1, text: words };
  }
  if (files.length > 1) {   // a split's props, and every box below a split: they all carry on
    return { kind: 'mosaic', file: null, files: files.slice(0, 9), count: files.length, tryIndex: -1, text: '' };
  }
  const picked = files.findIndex((f) => f.file === node.picked);
  const index = picked >= 0 ? picked : files.length - 1;   // the pick, or the newest try
  return { kind: 'one', file: files[index].file, files, count: files.length, tryIndex: index, text: '' };
}

/** The coloured tag above a tile: the box's name, a Picture's first words, and how many tries. */
export function tagText(node, count = 0) {
  const prompt = node.type === 'picture' && !node.settings?.file ? String(node.settings?.prompt || '').trim() : '';
  const name = prompt ? (prompt.length > 26 ? `${prompt.slice(0, 25).trimEnd()}…` : prompt) : NODES[node.type].label;
  return count > 1 ? `${name} (${count})` : name;
}

/** Boxes whose tries come out different each time: one can simply be made again (graph_api.REMAKE). */
export const REMAKE = ['picture', 'parts', 'model', 'repaint'];

/** What can be done over for one try of box `id`: `note` (say what is wrong: pictures only, a 3D
 *  engine reads no words) and `again` (the same box, a new seed). A file you gave takes neither. */
export function redoChoices(graph, id, file) {
  const node = graph.nodes.find((n) => n.id === id);
  if (!node || node.settings?.file) return { note: false, again: false };
  return { note: /\.(png|jpe?g|webp)$/i.test(String(file)), again: REMAKE.includes(node.type) };
}

/** A box's tries as the canvas shows them: the last run's, with this run's on top. A box made
 *  again keeps its old tries beside the new; a try done over (`run.redo`) is swapped in its place
 *  once it is made, keeping its label and what it replaced. A failed one leaves the old try. */
export function mergeRun(before = {}, run = null) {
  const now = run?.results || {};
  if (run?.redo) {
    const { node, file } = run.redo;
    const fresh = (now[node] || []).find((r) => r.status === 'done' && r.file);
    if (!fresh) return { ...before };
    const { label: _drop, ...made } = fresh;
    return { ...before, [node]: (before[node] || []).map((r) => (r.file === file
      ? { ...made, ...(r.label ? { label: r.label } : {}), was: [...(r.was || []), file] } : r)) };
  }
  const joined = run?.again
    ? Object.fromEntries(Object.entries(now).map(([k, v]) => [k, [...(before[k] || []).filter((r) => r.status === 'done'), ...v]]))
    : now;
  return { ...before, ...joined };
}

/** The graph once `run` ends: its results kept, and a try done over carried on by the lines and
 *  the pick that carried the old one (as the server keeps it, graph_api._swap_try). */
export function finishRun(graph, run) {
  const results = mergeRun(graph.last_run?.results || {}, run);
  const g = { ...clone(graph), last_run: { status: run.status, results } };
  const { node, file } = run.redo || {};
  const fresh = node && run.status === 'done' && (run.results?.[node] || []).find((r) => r.status === 'done' && r.file);
  if (fresh) {
    for (const e of g.edges) if (e.from === node && e.file === file) e.file = fresh.file;
    for (const n of g.nodes) if (n.id === node && n.picked === file) n.picked = fresh.file;
  }
  return g;
}

/** What a run is on now, in the plan's words: "Run 2, once for each part: 3D model" reads
 *  "3D model, once for each part" (graph_api.doing). */
function doingNow(run) {
  const line = run.plan?.[Math.min(run.stage, run.plan.length - 1)] || 'starting';
  const hit = /^Run \d+(?:, (.*?))?: (.*)$/.exec(line);
  return hit ? (hit[1] ? `${hit[2]}, ${hit[1]}` : hit[2]) : line;
}

/** What is running and where, for the line above the board: `here` when the run shown is this
 *  canvas's (its boxes show it too), else which canvas it is on; `side` is a run of tool boxes
 *  going beside it. Null when nothing runs. */
export function runBanner(run, graphId, queued = 0, side = null) {
  const live = (r) => r && r.status === 'running';
  const first = live(run) ? run : live(side) ? side : null;
  if (!first) return null;
  const secs = Math.round(first.elapsed || 0);
  const time = secs < 60 ? `${secs} s` : `${Math.floor(secs / 60)} min ${secs % 60} s`;
  const here = first.graph === graphId;
  const where = here ? 'Running' : first.name ? `Running on “${first.name}”` : 'Running on another canvas';
  const beside = first !== side && live(side)
    ? ` · beside it${side.graph === graphId ? '' : side.name ? ` on “${side.name}”` : ' on another canvas'}: ${doingNow(side)}` : '';
  return { here, text: `${where}: ${doingNow(first)} · ${time}${queued ? ` · ${queued} queued` : ''}${beside}` };
}

/** The boxes of canvas `graphId` that queued runs will run, each with its best place in line:
 *  Run all queues every box; from a box, it and every box below; after, the boxes below. */
export function queuedBoxes(queued, graph, graphId) {
  const out = new Map();
  for (const q of queued || []) {
    if (q.graph !== graphId) continue;
    const ids = !q.queued_for ? graph.nodes.map((n) => n.id)
      : q.mode === 'from' ? [q.queued_for, ...downstreamOf(graph, q.queued_for)]
        : q.mode === 'after' ? downstreamOf(graph, q.queued_for) : [q.queued_for];
    for (const id of ids) out.set(id, Math.min(out.get(id) ?? Infinity, q.position));
  }
  return out;
}

/** Where a line's wires start: from the try it carries; from every tile of a box whose results
 *  all go on (a Split's props, picked-out parts, the sides of Four views), so they visibly
 *  gather into the next box; else from the box. `tiles` are the box's option tiles ({file, port}). */
export function lineStarts(graph, edge, tiles, boxPort) {
  const carried = edge.file ? tiles.find((t) => t.file === edge.file) : null;
  if (carried) return [carried.port];
  const node = graph.nodes.find((n) => n.id === edge.from);
  if ([...FANS, 'views'].includes(node?.type) && tiles.length) return tiles.map((t) => t.port);
  return [boxPort(node)];
}
