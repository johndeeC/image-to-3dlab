// Undo and redo for the canvas. Graphs are never changed in place (canvas.js), so the
// history keeps whole snapshots. A run of changes with the same key close together (one drag,
// one word typed) is one step. No DOM here.

export const LIMIT = 100;
const TOGETHER_MS = 1200;

export function createHistory() {
  return { past: [], future: [], key: null, at: 0 };
}

/** Remember `before` (the graph as it was) ahead of a change. */
export function record(history, before, key = null, now = Date.now()) {
  const same = key && key === history.key && now - history.at < TOGETHER_MS;
  const past = same ? history.past : [...history.past, before].slice(-LIMIT);
  return { past, future: [], key, at: now };
}

/** {history, graph} with the last change undone, or null when there is nothing to undo. */
export function undo(history, current) {
  if (!history.past.length) return null;
  return { graph: history.past.at(-1),
    history: { past: history.past.slice(0, -1), future: [current, ...history.future], key: null, at: 0 } };
}

export function redo(history, current) {
  if (!history.future.length) return null;
  return { graph: history.future[0],
    history: { past: [...history.past, current], future: history.future.slice(1), key: null, at: 0 } };
}
