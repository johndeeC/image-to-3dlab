// Tidy the canvas (Alt/Option + Shift + F): each line of boxes left to right, branches stacked.
// Every box has one line in at most, so the graph is a forest and a tree layout fits. No DOM here.

export function tidy(graph, sizes = {}, { gapX = 60, gapY = 36, left = 40, top = 40 } = {}) {
  const size = (id) => sizes[id] || { w: 210, h: 150 };
  const parent = new Map(graph.edges.map((e) => [e.to, e.from]));
  const kids = new Map(graph.nodes.map((n) => [n.id, []]));
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  for (const e of graph.edges) if (kids.has(e.from) && byId.has(e.to)) kids.get(e.from).push(e.to);
  for (const list of kids.values()) list.sort((a, b) => byId.get(a).y - byId.get(b).y);
  const roots = graph.nodes.filter((n) => !parent.has(n.id) || !byId.has(parent.get(n.id))).sort((a, b) => a.y - b.y || a.x - b.x);
  // column x: the widest box in each column decides where the next one starts
  const depth = new Map();
  const walkDepth = (id, d) => { depth.set(id, d); for (const c of kids.get(id)) walkDepth(c, d + 1); };
  roots.forEach((r) => walkDepth(r.id, 0));
  const widths = [];
  for (const [id, d] of depth) widths[d] = Math.max(widths[d] || 0, size(id).w);
  const colX = [];
  widths.forEach((w, d) => { colX[d] = d ? colX[d - 1] + widths[d - 1] + gapX : left; });
  const place = new Map();
  let nextY = top;
  const lay = (id) => {
    const children = kids.get(id);
    if (!children.length) {
      place.set(id, nextY);
      nextY += size(id).h + gapY;
      return;
    }
    const start = nextY;
    children.forEach(lay);
    place.set(id, Math.max(start, place.get(children[0])));
    nextY = Math.max(nextY, place.get(id) + size(id).h + gapY);
  };
  roots.forEach((r) => lay(r.id));
  return { ...graph, nodes: graph.nodes.map((n) => (place.has(n.id) ? { ...n, x: colX[depth.get(n.id)], y: place.get(n.id) } : n)) };
}
