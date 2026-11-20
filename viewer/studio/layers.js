// Layers logic for the studio: a sound built from a few parts, each made on its own, then
// stacked. What to ask the server for, and how to say what came back. No DOM here, so Node
// tests import this exact file. layers-pane.js draws it; viewer/sound_layers_api.py answers it.

import { soundRequest, isSet } from './sound.js';

const MINUS = '−';

/** A number with its sign, as the page shows volume and pitch: "+1", "−2", "0". */
export function signed(value) {
  const n = Math.round((Number(value) || 0) * 10) / 10;
  return n > 0 ? `+${n}` : n < 0 ? `${MINUS}${Math.abs(n)}` : '0';
}

let layerKeys = 0;

/** One row on the Layers screen. With no `layer` id it is not made yet: only a description. */
export function newLayer(from = {}) {
  layerKeys += 1;
  return {
    key: `l${layerKeys}`, part: from.part || from.label_part || '', describe: from.describe || '',
    layer: from.layer || null, label: from.label || '', from: from.from || null,
    seconds: from.seconds ?? null, bars: from.bars || [], licence: from.licence || null,
    checked: from.checked ?? null, where: from.where || null, url: from.url || null,
    start_ms: Number(from.start_ms) || 0, volume_db: Number(from.volume_db) || 0,
    pitch_semitones: Number(from.pitch_semitones) || 0,
    count: from.count || 1, job: null,
  };
}

/** The split helper's answer as rows to make: each with its description, ready to edit. */
export function layersFromSplit(split) {
  return (split?.layers || []).map((item) => newLayer({ part: item.label, describe: item.describe, start_ms: item.start_ms }));
}

/** What the split did, in one line. It says so when it had no rule and changed nothing. */
export function splitLine(split) {
  const n = split?.layers?.length || 0;
  if (!n) return '';
  if (!split.rule) return 'No split for this one, so it is one layer as you typed it. Add more yourself: one thing happening in each.';
  return `Split into ${n} layers. Change any description, then make each one.`;
}

/** A staged layer the server answered, laid over the row it was made for. */
export function madeLayer(row, staged) {
  return { ...row, layer: staged.layer, label: staged.label || row.describe, from: staged.from, seconds: staged.seconds,
    bars: staged.bars || [], licence: staged.licence, checked: staged.checked, where: staged.where || null, url: staged.url, job: null };
}

/** Where a layer came from and its licence: "Described · Apache-2.0", "Your file · CC0". */
export function layerSourceLine(row) {
  if (!row?.layer) return 'Not made yet';
  const from = { generated: 'Described', file: 'Your file', kept: 'From your Library' }[row.from] || 'Layer';
  return row.licence ? `${from} · ${row.licence}` : from;
}

/** How long a layer plays once its pitch is set: higher is shorter, lower is longer. */
export function layerSeconds(row) {
  const s = Number(row?.seconds);
  return Number.isFinite(s) && s > 0 ? s / 2 ** ((Number(row.pitch_semitones) || 0) / 12) : 0;
}

/** The timeline: how long it is, where its marks go, and each made layer's block as a share of it. */
export function timeline(rows) {
  const ends = (rows || []).map((row) => (Number(row.start_ms) || 0) / 1000 + layerSeconds(row));
  const longest = Math.max(1, ...ends);
  const step = longest <= 2 ? 0.25 : longest <= 5 ? 0.5 : longest <= 12 ? 1 : 5;
  const span = Math.ceil(longest / step - 1e-9) * step;
  const marks = [];
  for (let i = 0; i * step <= span + 1e-9; i += 1) marks.push({ at: (100 * i * step) / span, label: `${+(i * step).toFixed(2)}${i === 0 || Number.isInteger(i * step) ? ' s' : ''}` });
  return {
    span, marks,
    blocks: (rows || []).map((row) => ({
      key: row.key, made: !!row.layer,
      left: (100 * ((Number(row.start_ms) || 0) / 1000)) / span,
      width: row.layer ? Math.max(1.5, (100 * layerSeconds(row)) / span) : 0,
    })),
  };
}

/** What POST /api/sound/layers/mix and /save are sent: the made layers, in order. */
export function stackRequest(rows, name = '') {
  const layers = (rows || []).filter((row) => row.layer).map((row) => ({
    layer: row.layer, start_ms: Number(row.start_ms), volume_db: Number(row.volume_db), pitch_semitones: Number(row.pitch_semitones) }));
  const tidy = String(name || '').trim();
  return tidy ? { layers, name: tidy } : { layers };
}

const LAYER_WORDS = { start_ms: 'Starts at', volume_db: 'Volume', pitch_semitones: 'Pitch' };

/** Why the stack can't be played or saved yet, in words, or null. The server checks again. */
export function stackProblem(rows, options) {
  const made = (rows || []).filter((row) => row.layer);
  if (!made.length) return (rows || []).length ? 'Make a layer first: press Make on one.' : 'Add a layer first.';
  if (options?.max && made.length > options.max) return `A sound can have up to ${options.max} layers.`;
  for (const row of made) {
    for (const [key, words] of Object.entries(LAYER_WORDS)) {
      const limit = options?.limits?.[key], value = Number(row[key]);
      if (!Number.isFinite(value)) return `${words} must be a number.`;
      if (limit && (value < limit.min || value > limit.max)) return `${words} must be between ${limit.min} and ${limit.max}.`;
    }
  }
  return null;
}

/** How many rows are still only a description. Saving leaves them out, and says so. */
export function notMadeCount(rows) {
  return (rows || []).filter((row) => !row.layer).length;
}

/** What POST /api/sound is sent to make one layer: its description, `count` tries. */
export function layerMakeRequest(row, count = 1, seconds) {
  return soundRequest({ prompt: row?.describe || '', seconds, count: Number(count) || 1 });
}

/** The line under "Licence of the finished sound". */
export function finishedLicence(rows, mix = null) {
  const made = (rows || []).filter((row) => row.layer);
  if (!made.length) return { name: '', checked: null, note: 'Add a layer and its licence shows here.' };
  const names = mix?.licence ? [mix.licence] : [...new Set(made.map((row) => row.licence || 'Not checked'))];
  const checked = mix ? !!mix.checked : made.every((row) => row.checked) && names.length === 1;
  return {
    name: names.join(' + '), checked,
    note: checked ? 'Every layer is listed in its record. If one layer says Not checked, so does the finished sound.'
      : mix || made.some((row) => !row.checked)
        ? 'Not checked: one layer has no licence anyone can check, or the layers have different kinds of licence. Check before you ship it.'
        : 'Press Play together to see the licence of the finished sound.',
  };
}

/** The licence choice for your own file: its hint, and whether it needs "Where it's from". */
export function licenceChoice(options, id) {
  const list = options?.licences || [];
  const found = list.find((item) => item.id === (id ?? options?.default_licence)) || list[0] || {};
  return { id: found.id, label: found.label || '', hint: found.hint || '', needsLink: !!found.needs_link };
}

/** Why your own file can't be added yet, in words, or null. */
export function fileProblem(file, options) {
  if (!file) return 'Pick a file first.';
  if (!/\.wav$/i.test(file.name || '')) return 'Bring a WAV file (its name ends in .wav).';
  const most = options?.upload_mb;
  if (most && file.size > most * 1024 * 1024) return `That file is larger than ${most} MB.`;
  return null;
}

/** A kept sound that was built from layers can be opened again. */
export function canEditLayers(sound) {
  return !isSet(sound) && (sound?.layers || 0) > 0;
}

/** The screen's heading: "Arrow hit · 2 layers". */
export function layersTitle(name, rows) {
  const n = (rows || []).length;
  return `${name || 'New sound'} · ${n} layer${n === 1 ? '' : 's'}`;
}
