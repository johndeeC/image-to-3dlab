// Copy and paste on the canvas: boxes with their settings and the lines between them. No DOM here.

const clone = (value) => JSON.parse(JSON.stringify(value));

/** What Cmd C keeps: the chosen boxes (without their results) and the lines inside the choice. */
export function copyNodes(graph, ids) {
  const keep = new Set(ids);
  const nodes = graph.nodes.filter((n) => keep.has(n.id)).map(({ id, type, x, y, settings }) => clone({ id, type, x, y, settings }));
  const edges = graph.edges.filter((e) => keep.has(e.from) && keep.has(e.to)).map((e) => ({ ...e }));
  return nodes.length ? { kind: 'assetfurnace-canvas', nodes, edges } : null;
}

/** Cmd V: the boxes again with new ids, shifted by (dx, dy). Returns the graph and the new ids. */
export function pasteNodes(graph, clip, dx = 40, dy = 40) {
  if (!clip?.nodes?.length) return { graph, ids: [] };
  const g = clone(graph);
  let k = g.nodes.reduce((top, n) => Math.max(top, Number(String(n.id).replace(/^n/, '')) || 0), 0);
  const used = new Set(g.nodes.map((n) => n.id));
  const fresh = new Map();
  for (const n of clip.nodes) {
    do { k += 1; } while (used.has(`n${k}`));
    fresh.set(n.id, `n${k}`);
    g.nodes.push({ id: `n${k}`, type: n.type, x: Math.round(n.x + dx), y: Math.round(n.y + dy), settings: clone(n.settings || {}) });
  }
  for (const e of clip.edges || []) if (fresh.has(e.from) && fresh.has(e.to)) g.edges.push({ from: fresh.get(e.from), to: fresh.get(e.to) });
  return { graph: g, ids: [...fresh.values()] };
}
