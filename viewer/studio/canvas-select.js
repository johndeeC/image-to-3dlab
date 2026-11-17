// Choosing boxes on the canvas: click, Shift/Cmd-click, a dragged rectangle, Cmd A. No DOM here.

const sizeOf = (sizes, n) => sizes?.[n.id] || { w: 210, h: 150 };

export function nodeRect(node, sizes) {
  const { w, h } = sizeOf(sizes, node);
  return { x: node.x, y: node.y, w, h };
}

/** A rectangle with a positive width and height, whichever way it was dragged. */
export function normalRect({ x, y, w, h }) {
  return { x: w < 0 ? x + w : x, y: h < 0 ? y + h : y, w: Math.abs(w), h: Math.abs(h) };
}

/** The boxes a dragged rectangle touches (board coordinates). */
export function nodesInRect(nodes, sizes, rect) {
  const r = normalRect(rect);
  return nodes.filter((n) => {
    const b = nodeRect(n, sizes);
    return b.x < r.x + r.w && b.x + b.w > r.x && b.y < r.y + r.h && b.y + b.h > r.y;
  }).map((n) => n.id);
}

/** The top box under a board point, or null. */
export function hitNode(nodes, sizes, pt) {
  for (let i = nodes.length - 1; i >= 0; i -= 1) {
    const b = nodeRect(nodes[i], sizes);
    if (pt.x >= b.x && pt.x <= b.x + b.w && pt.y >= b.y && pt.y <= b.y + b.h) return nodes[i].id;
  }
  return null;
}

/** A click: just this box, or with Shift/Cmd, add it to the choice (or take it out). */
export function toggle(selection, id, additive) {
  if (!additive) return [id];
  return selection.includes(id) ? selection.filter((s) => s !== id) : [...selection, id];
}

/** A rectangle drag: these boxes, or with Shift/Cmd, these added to the choice. */
export function addAll(selection, ids, additive) {
  return additive ? [...new Set([...selection, ...ids])] : [...ids];
}

/** The choice with boxes that no longer exist dropped. */
export function keepExisting(selection, graph) {
  const ids = new Set(graph.nodes.map((n) => n.id));
  return selection.filter((id) => ids.has(id));
}
