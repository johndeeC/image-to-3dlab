// What the canvas offers from your Library: every picture, and every 3D model an asset has (the
// raw model, the finished one, the rigged one, a split's props), each named by what it is, so a
// "Your 3D model" box can tell them apart. No DOM here, so Node tests import this exact file.

import { ENGINE_WORDS } from './canvas-settings.js';

const ENGINE = /__(pixal3d|sf3d|trellis|hunyuan[a-z_-]*?)__/;
const DETAIL = /_(\d+k|hero|high|full)(?:_rigged)?\.glb$/;
const DETAIL_WORDS = { hero: 'hero face', high: 'high detail', full: 'every triangle' };

/** Plain words for which engine made a model, read from its file name; '' when unknown. */
export function engineWords(file) {
  const id = String(file || '').match(ENGINE)?.[1];
  return id ? (ENGINE_WORDS[id]?.[0] || id) : '';
}

/** How much detail a finished model kept, from its name (`_20k`, `_hero`); '' when unknown. */
export function detailWords(file) {
  const d = String(file || '').match(DETAIL)?.[1];
  return d ? (DETAIL_WORDS[d] || d) : '';
}

/**
 * The Library's pictures (`want` 'picture') or 3D models ('model'), newest first, each
 * {file, name, what, picture}. `query` keeps the ones whose name has every word in it.
 */
export function libraryChoices(assets, want, query = '') {
  const words = String(query).toLowerCase().split(/\s+/).filter(Boolean);
  const out = [];
  const seen = new Set();
  const add = (file, name, what, picture) => {
    if (!file || seen.has(file)) return;
    seen.add(file);
    out.push({ file, name, what, picture: picture || null });
  };
  for (const a of assets || []) {
    if (a.hidden || !words.every((w) => String(a.name || '').toLowerCase().includes(w))) continue;
    const name = a.name || 'Untitled';
    if (want === 'picture') {
      add(a.picture, name, 'Picture', a.picture);
      add(a.back, name, 'Back picture', a.back);
      continue;
    }
    if (a.kind === 'prop set') {
      for (const p of a.props || []) add(p.file, `${String(p.name).replace(/[_-]/g, ' ')}`, `Prop from ${name}`, null);
    }
    add(a.finished, name, ['Finished', detailWords(a.finished)].filter(Boolean).join(' · '), a.picture);
    add(a.rigged, name, 'Rigged', a.picture);
    add(a.model, name, ['3D model', engineWords(a.model)].filter(Boolean).join(' · '), a.picture);
  }
  return out;
}

/** The Library assets that hold these files (any of their pictures, models, moves or props), each once. */
export function assetIdsFor(assets, files) {
  const wanted = new Set(files || []);
  const holds = (a) => [a.picture, a.back, a.model, a.finished, a.rigged].some((f) => f && wanted.has(f))
    || (a.clips || []).some((c) => wanted.has(c.file)) || (a.props || []).some((p) => wanted.has(p.file));
  return [...new Set((assets || []).filter(holds).map((a) => a.id))];
}
