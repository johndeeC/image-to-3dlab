// The canvas's keyboard shortcuts. The shortcuts panel is drawn from this same table. No DOM here.

const MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
export const MOD = MAC ? '⌘' : 'Ctrl';
export const ALT = MAC ? '⌥' : 'Alt';

// A letter by its place on the keyboard (code), or by the letter itself when a keyboard or a
// tool sends no code. Alt changes the letter on a Mac (Alt+F is "ƒ"), so the place comes first.
const is = (e, letter) => e.code === `Key${letter.toUpperCase()}` || (!e.code && String(e.key).toLowerCase() === letter);

/** [action, what it does, keys shown, test]. `test(e)` reads key, code and the modifier flags. */
export const SHORTCUTS = [
  ['add', 'Add a box', 'Tab · double-click the board', (e) => e.key === 'Tab' && !e.mod && !e.shiftKey],
  ['pointer', 'Pointer: pick and move boxes', 'V', (e) => is(e, 'v') && !e.mod && !e.shiftKey && !e.altKey],
  ['hand', 'Hand: drag the board about', 'H · hold Space', (e) => is(e, 'h') && !e.mod && !e.shiftKey && !e.altKey],
  ['zoomIn', 'Zoom in', `${MOD} + · wheel · pinch`, (e) => e.mod && (e.key === '=' || e.key === '+')],
  ['zoomOut', 'Zoom out', `${MOD} −`, (e) => e.mod && (e.key === '-' || e.key === '_')],
  ['fit', 'Fit everything on screen', `${MOD} 0`, (e) => e.mod && (e.key === '0' || e.code === 'Digit0')],
  ['selectAll', 'Choose every box', `${MOD} A`, (e) => e.mod && is(e, 'a')],
  ['copy', 'Copy', `${MOD} C`, (e) => e.mod && is(e, 'c') && !e.shiftKey],
  ['paste', 'Paste', `${MOD} V`, (e) => e.mod && is(e, 'v') && !e.shiftKey],
  ['delete', 'Remove the chosen boxes', 'Delete', (e) => !e.mod && (e.key === 'Delete' || e.key === 'Backspace')],
  ['redo', 'Redo', `${MOD} ⇧ Z`, (e) => e.mod && ((is(e, 'z') && e.shiftKey) || is(e, 'y'))],
  ['undo', 'Undo', `${MOD} Z`, (e) => e.mod && is(e, 'z') && !e.shiftKey],
  ['connect', 'Join the chosen boxes, left to right', `${MOD} L`, (e) => e.mod && is(e, 'l')],
  ['ungroup', 'Take the frame off', `${MOD} ⇧ G`, (e) => e.mod && is(e, 'g') && e.shiftKey],
  ['group', 'Put the chosen boxes in a frame', `${MOD} G`, (e) => e.mod && is(e, 'g') && !e.shiftKey],
  ['tidy', 'Tidy the layout', `${ALT} ⇧ F`, (e) => e.altKey && e.shiftKey && is(e, 'f')],
  ['lines', 'Hide or show the lines', `${ALT} ⇧ L`, (e) => e.altKey && e.shiftKey && is(e, 'l')],
  ['help', 'Show these shortcuts', '?', (e) => e.key === '?' || (e.code === 'Slash' && e.shiftKey)],
  ['escape', 'Close a menu, or let go of the chosen boxes', 'Esc', (e) => e.key === 'Escape'],
];

/** The action a key press means, or null. While typing in a field only Esc counts. */
export function actionFor(event, { typing = false, mac = MAC } = {}) {
  const e = { key: event.key, code: event.code, shiftKey: !!event.shiftKey, altKey: !!event.altKey,
    mod: mac ? !!event.metaKey : !!event.ctrlKey };
  if (typing) return e.key === 'Escape' ? 'escape' : null;
  const hit = SHORTCUTS.find(([, , , test]) => test(e));
  return hit ? hit[0] : null;
}
