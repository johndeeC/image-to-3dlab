// The canvas's keyboard shortcuts. The shortcuts panel is drawn from this same table. No DOM here.

const MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
export const MOD = MAC ? '⌘' : 'Ctrl';
export const ALT = MAC ? '⌥' : 'Alt';

/** [action, what it does, keys shown, test]. `test(e)` reads key, code and the modifier flags. */
export const SHORTCUTS = [
  ['add', 'Add a box', 'Tab · double-click the board', (e) => e.key === 'Tab' && !e.mod && !e.shiftKey],
  ['pointer', 'Pointer: pick and move boxes', 'V', (e) => e.code === 'KeyV' && !e.mod && !e.shiftKey && !e.altKey],
  ['hand', 'Hand: drag the board about', 'H · hold Space', (e) => e.code === 'KeyH' && !e.mod && !e.shiftKey && !e.altKey],
  ['zoomIn', 'Zoom in', `${MOD} + · wheel · pinch`, (e) => e.mod && (e.key === '=' || e.key === '+')],
  ['zoomOut', 'Zoom out', `${MOD} −`, (e) => e.mod && (e.key === '-' || e.key === '_')],
  ['fit', 'Fit everything on screen', `${MOD} 0`, (e) => e.mod && (e.key === '0' || e.code === 'Digit0')],
  ['selectAll', 'Choose every box', `${MOD} A`, (e) => e.mod && e.code === 'KeyA'],
  ['copy', 'Copy', `${MOD} C`, (e) => e.mod && e.code === 'KeyC' && !e.shiftKey],
  ['paste', 'Paste', `${MOD} V`, (e) => e.mod && e.code === 'KeyV' && !e.shiftKey],
  ['delete', 'Remove the chosen boxes', 'Delete', (e) => !e.mod && (e.key === 'Delete' || e.key === 'Backspace')],
  ['redo', 'Redo', `${MOD} ⇧ Z`, (e) => e.mod && ((e.code === 'KeyZ' && e.shiftKey) || e.code === 'KeyY')],
  ['undo', 'Undo', `${MOD} Z`, (e) => e.mod && e.code === 'KeyZ' && !e.shiftKey],
  ['connect', 'Join the chosen boxes, left to right', `${MOD} L`, (e) => e.mod && e.code === 'KeyL'],
  ['ungroup', 'Take the frame off', `${MOD} ⇧ G`, (e) => e.mod && e.code === 'KeyG' && e.shiftKey],
  ['group', 'Put the chosen boxes in a frame', `${MOD} G`, (e) => e.mod && e.code === 'KeyG' && !e.shiftKey],
  ['tidy', 'Tidy the layout', `${ALT} ⇧ F`, (e) => e.altKey && e.shiftKey && e.code === 'KeyF'],
  ['lines', 'Hide or show the lines', `${ALT} ⇧ L`, (e) => e.altKey && e.shiftKey && e.code === 'KeyL'],
  ['help', 'Show these shortcuts', '?', (e) => e.key === '?'],
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
