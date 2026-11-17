// Zoom and pan for the canvas: where the board sits on screen. A view is {x, y, k}: a point on
// the board at (bx, by) shows on screen at (bx * k + x, by * k + y). No DOM here.

export const MIN_ZOOM = 0.2;
export const MAX_ZOOM = 2.5;
const STEP = 1.2;

export const identity = () => ({ x: 0, y: 0, k: 1 });
const clampK = (k) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, k));

export function screenToBoard(view, sx, sy) {
  return { x: (sx - view.x) / view.k, y: (sy - view.y) / view.k };
}

export function boardToScreen(view, bx, by) {
  return { x: bx * view.k + view.x, y: by * view.k + view.y };
}

/** Zoom by `factor`, keeping the board point under (sx, sy) where it is. */
export function zoomAt(view, factor, sx, sy) {
  const k = clampK(view.k * factor);
  const at = screenToBoard(view, sx, sy);
  return { x: sx - at.x * k, y: sy - at.y * k, k };
}

/** Zoom to an exact scale around a screen point. */
export function zoomTo(view, k, sx, sy) {
  return zoomAt(view, clampK(k) / view.k, sx, sy);
}

/** Cmd + / Cmd −: one step in or out, around the middle of a w × h screen. */
export function zoomStep(view, direction, w, h) {
  return zoomAt(view, direction > 0 ? STEP : 1 / STEP, w / 2, h / 2);
}

export function panBy(view, dx, dy) {
  return { ...view, x: view.x + dx, y: view.y + dy };
}

/** How much one wheel event zooms. Pinch on a trackpad arrives as a wheel event with ctrlKey. */
export function wheelFactor(deltaY, deltaMode = 0) {
  const pixels = deltaMode === 1 ? deltaY * 16 : deltaMode === 2 ? deltaY * 400 : deltaY;
  return Math.exp(-Math.max(-200, Math.min(200, pixels)) * 0.0025);
}

/** The view that shows every box on a w × h screen, never larger than life size. */
export function fitAll(nodes, sizes, w, h, pad = 40) {
  if (!nodes.length || w <= 0 || h <= 0) return identity();
  const size = (n) => sizes?.[n.id] || { w: 210, h: 150 };
  const left = Math.min(...nodes.map((n) => n.x)), top = Math.min(...nodes.map((n) => n.y));
  const right = Math.max(...nodes.map((n) => n.x + size(n).w)), bottom = Math.max(...nodes.map((n) => n.y + size(n).h));
  const k = clampK(Math.min(1, (w - pad * 2) / Math.max(1, right - left), (h - pad * 2) / Math.max(1, bottom - top)));
  return { k, x: (w - (right - left) * k) / 2 - left * k, y: (h - (bottom - top) * k) / 2 - top * k };
}

export function zoomPercent(view) {
  return `${Math.round(view.k * 100)}%`;
}

/** What a wheel event means: 'zoom' (pinch, Cmd/Ctrl + wheel, or a mouse wheel's whole notches)
 *  or 'pan' (a two-finger scroll on a trackpad). */
export function wheelIntent({ deltaX = 0, deltaY = 0, deltaMode = 0, ctrlKey = false, metaKey = false }) {
  if (ctrlKey || metaKey) return 'zoom';
  if (deltaMode === 1 || deltaMode === 2) return 'zoom';
  return deltaX === 0 && Number.isInteger(deltaY) && Math.abs(deltaY) >= 50 ? 'zoom' : 'pan';
}

// Where you left a board is yours, not the canvas's: two tabs (or a tab and an agent) on one board
// must not refuse each other's saves over a pan. Kept in this browser; storage can be missing.
const VIEW_KEY = (id) => `assetfurnace.canvas.view.${id}`;

export function rememberView(storage, id, view) {
  if (!id) return;
  try { storage?.setItem(VIEW_KEY(id), JSON.stringify({ x: view.x, y: view.y, k: view.k })); } catch { /* private window */ }
}

/** The view this browser kept for a board, or null. */
export function recallView(storage, id) {
  try {
    const v = JSON.parse(storage?.getItem(VIEW_KEY(id)) || 'null');
    return v && [v.x, v.y, v.k].every(Number.isFinite) ? { x: v.x, y: v.y, k: Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.k)) } : null;
  } catch {
    return null;
  }
}

/** How far back a camera with a `fovDeg` vertical view stands to see a model `size` across
 *  (its longest side), with a little room round it. */
export function fitDistance(size, fovDeg = 35, room = 1.35) {
  const half = Math.max(1e-6, Number(size) || 0) / 2;
  return (half / Math.tan(((fovDeg * Math.PI) / 180) / 2)) * room;
}
