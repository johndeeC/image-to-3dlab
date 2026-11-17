// The canvas: step nodes joined by lines (docs/canvas.md). The server (viewer/graph_api.py) turns
// a graph into ordinary runs; this file only edits the graph and reads a run back.
// No DOM here, so Node tests import this exact file.

/** Every node: its name, its one-line hint, what it takes in, what it makes, and its runner step.
 *  tests/test_studio_canvas.py checks takes/makes/step against graph_api.py. */
export const NODES = {
  picture: { label: 'Picture', hint: 'Draws a picture from your words, or uses one you already made.', takes: [], makes: 'picture', step: 'picture' },
  model: { label: '3D model', hint: 'Builds a 3D model from the picture.', takes: ['picture'], makes: 'model', step: 'model' },
  split: { label: 'Split into props', hint: 'Cuts a sheet of nine into separate game-ready props.', takes: ['model'], makes: 'props', step: 'split' },
  finish: { label: 'Finish', hint: 'Makes the model game-ready: cleaner shape, lighter file.', takes: ['model'], makes: 'finished', step: 'finished' },
  rig: { label: 'Rig', hint: 'Adds a skeleton so it can move. Humanoids work best.', takes: ['model', 'finished', 'props'], makes: 'rigged', step: 'rigged' },
  move: { label: 'Move', hint: 'Plays a move on the rigged model.', takes: ['rigged'], makes: 'animated', step: 'animated' },
  export: { label: 'Export', hint: 'Lists the files to take into your game.', takes: ['model', 'finished', 'rigged', 'animated', 'props'], makes: null, step: null },
};

/** What a Picture is for: it picks the wording added to your words, and Finish's defaults. */
export const GOALS = { character: 'A character', prop: 'One prop', set: 'Sheet of nine', picture: 'Just a picture' };

const THING = { picture: 'a picture', model: 'a 3D model', finished: 'a game-ready model', rigged: 'a rigged model', animated: 'a moving model', props: 'props' };

// A node's box: its width, and how far down its in and out dots sit.
export const NODE_W = 210;
export const PORT_Y = 22;

const clone = (graph) => JSON.parse(JSON.stringify(graph));

export function newGraph(name = 'Untitled canvas') {
  return { schema: 1, id: null, name, nodes: [], edges: [] };
}

/** A new node at (x, y), with the next free id. */
export function addNode(graph, type, x, y, settings = {}) {
  if (!NODES[type]) throw new Error(`unknown node type ${type}`);
  const g = clone(graph);
  const used = new Set(g.nodes.map((n) => n.id));
  let k = g.nodes.reduce((top, n) => Math.max(top, Number(String(n.id).replace(/^n/, '')) || 0), 0) + 1;
  while (used.has(`n${k}`)) k += 1;
  g.nodes.push({ id: `n${k}`, type, x: Math.max(0, Math.round(x)), y: Math.max(0, Math.round(y)), settings: { ...settings } });
  return g;
}

export function removeNode(graph, id) {
  const g = clone(graph);
  g.nodes = g.nodes.filter((n) => n.id !== id);
  g.edges = g.edges.filter((e) => e.from !== id && e.to !== id);
  return g;
}

export function moveNode(graph, id, x, y) {
  const g = clone(graph);
  const node = g.nodes.find((n) => n.id === id);
  if (node) Object.assign(node, { x: Math.max(0, Math.round(x)), y: Math.max(0, Math.round(y)) });
  return g;
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
  const x1 = a.x + NODE_W, y1 = a.y + PORT_Y, x2 = b.x, y2 = b.y + PORT_Y;
  const d = Math.max(40, Math.abs(x2 - x1) / 2);
  return `M ${x1} ${y1} C ${x1 + d} ${y1}, ${x2 - d} ${y2}, ${x2} ${y2}`;
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
  else if (results.length) state = 'skipped';
  else if (run?.status === 'running') state = 'waiting';
  return { state, files, errors };
}

/** The node the running chain is on now, or null. */
export function runningNode(run, graph, chain) {
  if (!run || run.status !== 'running' || !chain?.current) return null;
  const stage = run.stages?.[run.stage];
  const types = new Map(graph.nodes.map((n) => [n.id, n.type]));
  return stage?.nodes.find((id) => NODES[types.get(id)]?.step === chain.current) ?? null;
}
