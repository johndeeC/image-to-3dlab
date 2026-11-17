// What each canvas box lets you choose: the same choices Create and the Steps panel offer, in
// the studio's words. viewer/graph_api.py SETTINGS keeps the same keys (a test checks), and drops
// any value that does not fit. No DOM here.

import { GOALS } from './canvas.js';

/** Engines named by what they do, not where they came from. Unknown ids keep the catalog's label. */
export const ENGINE_WORDS = {
  pixal3d: ['Best detail', 'Our best results, in one pass. The only one that uses a back picture.'],
  trellis: ['High detail, slower', 'Very close to the original demo. Takes longer.'],
  hunyuan_xiong: ['Fast and clean', 'Clean results, quick to run.'],
  'hunyuan-cuda': ['Shape and paint (NVIDIA)', 'Builds the shape, then paints it, in one run.'],
  'hunyuan-rocm': ['Shape and paint (AMD)', 'Builds the shape, then paints it, in one run.'],
  'hunyuan-mlx': ['Shape and paint, older pairing', 'The earlier shape and paint pair.'],
  sf3d: ['Fastest, rough', 'A quick look in seconds. Lighting is baked into the colours.'],
};
// keyed by the ids /api/catalog's picture_engines use (the ones a run's picture_engine takes)
export const PICTURE_ENGINE_WORDS = {
  flux: ['Quick pictures', 'Fast, and the pictures are yours for any use.'],
  'flux-light': ['Quick pictures, less memory', 'The same pictures for Macs with 16 GB or less.'],
  qwen: ['Follows long descriptions', 'Slower, but sticks closely to what you wrote.'],
};

/** An engine's own name: its catalog label without the bracketed setup notes ("Pixal3D (C++/GGML)" -> "Pixal3D"). */
export function modelName(label) {
  return String(label || '').replace(/\s*\([^)]*\)\s*$/, '').trim();
}

/** A menu entry: what the engine does first, then which engine it is ("Best detail · Pixal3D"). */
const words = (table, id, label) => {
  const plain = table[id]?.[0], name = modelName(label);
  return plain ? (name ? `${plain} · ${name}` : plain) : name || id;
};

/** Boxes that redraw a picture they are given (a part, a side, a fix), rather than draw a new one. */
export const REDRAWS = ['parts', 'back', 'views'];

/** The picture engine a new picture gets when none is chosen: the suggested one if it is set up,
 *  else the first set up (image_api.pick_engine). */
function firstReady(tools) {
  const ready = (tools?.pictureEngines || []).filter((e) => e.installed !== false);
  return ready.find((e) => e.suggested) || ready[0] || null;
}

/** A Drawn by menu. One that redraws lists only the engines that can, and by default keeps the
 *  engine that drew the picture, so the pictures of one model match. */
function pictureChoices(tools, redraw) {
  const ready = (tools?.pictureEngines || []).filter((e) => e.installed !== false && (!redraw || e.can_draw_backs !== false));
  const first = firstReady(tools);
  const usual = redraw ? 'Pick for me: the one that drew the picture'
    : first ? `Pick for me: ${words(PICTURE_ENGINE_WORDS, first.id, first.label)}` : 'Pick for me';
  return [['', usual], ...ready.map((e) => [e.id, words(PICTURE_ENGINE_WORDS, e.id, e.label)])];
}

/** The one line under an engine menu: what the chosen engine is good for, and when to pick it.
 *  With none chosen, which one runs. `type` is the box's; `tools` has the picture engines. */
export function engineNote(key, id, type = 'picture', tools = null) {
  if (!id && key === 'picture_engine') {
    if (REDRAWS.includes(type)) return 'Uses the engine that drew the picture, so they match.';
    const first = firstReady(tools);
    return first ? `Uses ${modelName(first.label)}: the first one set up, as Create does.` : 'The engine Create would use.';
  }
  if (!id) return '';
  return (key === 'picture_engine' ? PICTURE_ENGINE_WORDS : ENGINE_WORDS)[id]?.[1] || '';
}

const REDRAWN_BY = ['picture_engine', 'Drawn by', 'Which picture engine redraws it.', 'select', (t) => pictureChoices(t, true), true];

/**
 * Each box's fields: [key, label, hint, kind, choices(tools) or bounds, folded].
 * kind is text, area, select, toggle or number. Folded ones sit under "More settings".
 */
export const FIELDS = {
  picture: [
    ['prompt', 'Describe it', 'What to draw. The usual wording for its goal is added.', 'area'],
    ['goal', 'For', 'Picks the wording added and how Finish treats it.', 'select', () => Object.entries(GOALS)],
    ['file', 'Or use a picture', 'A picture you already have, instead of drawing one.', 'file'],
    ['count', 'Options', 'Draw a few and pick the one to carry on with.', 'select', () => [[1, '1'], [2, '2'], [3, '3'], [4, '4']], true],
    ['shape', 'Shape', 'Square suits most things; tall for characters, wide for vehicles.', 'select',
      () => [['square', 'Square'], ['tall', 'Tall'], ['wide', 'Wide']], true],
    ['picture_engine', 'Drawn by', 'Which picture engine draws it.', 'select', (t) => pictureChoices(t, false), true],
  ],
  asset: [
    ['file', '3D model', 'A model from your Library.', 'file'],
    ['goal', 'It is', 'Finish stands a character 1.8 m tall and a prop upright.', 'select', () => [['character', 'A character'], ['prop', 'A prop']]],
  ],
  back: [REDRAWN_BY],
  views: [REDRAWN_BY],
  parts: [
    ['names', 'Parts', 'One per line or with commas: head, hair, left hand. Each is redrawn on its own.', 'area'],
    REDRAWN_BY,
  ],
  repaint: [
    ['file', 'Paint from', 'A picture of the new look. Leave it empty to paint from the picture the model was made from.', 'file'],
  ],
  cutout: [],
  remesh: [
    ['faces', 'Faces', 'How many four-sided faces to rebuild it with (500 to 200,000).', 'number', [500, 200000]],
    ['size', 'Texture size', 'Bigger is sharper and a bigger file. 512 suits a phone.', 'select', () => [['', '2048 (usual)'], [512, '512'], [1024, '1024'], [4096, '4096']], true],
  ],
  unwrap: [
    ['size', 'Texture size', 'Bigger is sharper and a bigger file. 512 suits a phone.', 'select', () => [['', '2048 (usual)'], [512, '512'], [1024, '1024'], [4096, '4096']]],
  ],
  combine: [],
  saveas: [
    ['format', 'File type', 'FBX keeps a rig and its moves; OBJ is shape and texture; USDZ is for Apple devices.', 'select',
      () => [['', 'FBX (usual)'], ['obj', 'OBJ'], ['usdz', 'USDZ']]],
  ],
  model: [
    ['engine', 'Engine', 'Which engine builds the 3D model.', 'select', (t) => (t.engines || []).map((e) => [e.id, words(ENGINE_WORDS, e.id, e.label)])],
  ],
  split: [
    ['names', 'Names', 'One per prop, in reading order: head, left hand, helmet.', 'text'],
  ],
  finish: [
    ['detail', 'Detail', 'How many triangles: fewer is lighter for games.', 'select', () => [['', 'Game (20k)'], ['light', 'Light (10k)'], ['more', 'More (40k)'],
      ['high', 'High (50k, sharper shape)'], ['hero', 'Hero face (sharper eyes)'], ['full', 'Full (keep every triangle)']]],
    ['texture_size', 'Texture size', 'Bigger is sharper and a bigger file. 512 suits a phone.', 'select',
      () => [['', '2048 (usual)'], [512, '512'], [1024, '1024'], [4096, '4096']], true],
    ['surface_maps', 'Surface maps', 'Bumps and shine. Drop them for a flat, matte look and smaller files, good for phones.', 'toggle', null, true],
    ['pixel_match', 'Pixel Match', 'Paints your picture back onto the model for sharper colours.', 'toggle', null, true],
    ['cleanup', 'Clean-up', 'Rebuild seals thin parts so they don\'t tear; Fast keeps the shape as it is.', 'select',
      () => [['', 'Pick for me'], ['fast', 'Fast'], ['rebuild', 'Rebuild']], true],
    ['height_m', 'Height (m)', 'Its real height. Leave blank for 1.8 m for characters.', 'number', [0.01, 100], true],
    ['upright', 'Stand upright', 'Straightens a prop that leans. No if it is meant to lean.', 'select',
      () => [['', 'Props yes, characters no'], ['true', 'Yes'], ['false', 'No']], true],
  ],
  rig: [
    ['bone_names', 'Bone names', 'Unity names let Unity set up the character on its own.', 'select', () => [['', 'Numbered'], ['unity', 'Unity Humanoid']], true],
  ],
  move: [
    ['move', 'Move', 'What it does.', 'select', (t) => (t.presets || []).filter((p) => !p.take).map((p) => [p.id, p.label])],
    ['speed', 'Speed', 'Faster or slower than the move\'s own pace (0.5 to 2).', 'number', [0.5, 2], true],
    ['in_place', 'Where', 'On the spot plays a walk or run without travelling.', 'select',
      () => [['', "The move's own"], ['true', 'On the spot'], ['false', 'Travels']], true],
  ],
  export: [],
};

/** The setting keys of each box type: the server keeps exactly these. */
export function settingKeys() {
  return Object.fromEntries(Object.entries(FIELDS).map(([type, fields]) => [type, fields.map((f) => f[0])]));
}

/** The engine that builds from four views (Hunyuan3D-2mv), the studio's "Fast and clean". */
export const FOUR_VIEW_ENGINE = 'hunyuan_xiong';

/** A new box's settings: the first ready engine (the four-view one after Four views), the first
 *  move, a character picture. `after` is the type of the box it is joined to, if any. */
export function defaultsFor(type, tools = {}, after = null) {
  if (type === 'model' && after === 'views') return { engine: FOUR_VIEW_ENGINE };
  if (type === 'model' && tools.engines?.[0]) return { engine: tools.engines[0].id };
  if (type === 'move') {
    const move = (tools.presets || []).find((p) => !p.take);
    return move ? { move: move.id } : {};
  }
  if (type === 'picture') return { goal: 'character' };
  if (type === 'parts') return { names: 'head, hair, body, hands' };
  return {};
}

/** A form value as the box keeps it: '' clears it, numbers and on/off become themselves. */
export function readField(field, raw) {
  const [, , , kind, choices] = field;
  if (kind === 'toggle') return raw === true || raw === 'true' || raw === 'on';
  if (raw === '' || raw == null) return '';
  if (kind === 'number') {
    const n = Number(raw);
    const [lo, hi] = choices;
    return Number.isFinite(n) && n >= lo && n <= hi ? n : '';
  }
  if (kind === 'select' && /^\d+$/.test(String(raw))) return Number(raw);
  return String(raw);
}

/** What a toggle shows: on unless it was turned off (Surface maps, Pixel Match). */
export function toggleValue(key, settings) {
  return key in settings ? settings[key] === true || settings[key] === 'true' : true;
}
