// The kit pane: one row of step nodes per member (the character, then its gear), each node with
// its tries and Redo / ×2-×4, and every split prop in one grid. Logic lives in kit.js; this file
// only draws it and hands clicks back to the page (app.js).

import { kitLanes, kitProps, nodeActions, stepMade, TRY_COUNTS } from './kit.js';

const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const STATE_WORDS = { done: 'Done', now: 'Ready', later: 'Waits on the step before' };

/** The file a node's picture comes from: the picture itself, or a model to render. */
function nodeFile(asset, stepId) {
  if (stepId === 'picture') return { picture: asset.picture };
  if (stepId === 'finished' && asset.kind === 'prop set') return { model: asset.props?.[0]?.file };
  if (stepId === 'animated') return {};
  return { model: stepMade(asset, stepId) };
}

function thumbHtml({ picture, model }, alt = '') {
  if (picture) return `<img src="/api/thumb?path=${encodeURIComponent(picture)}" alt="${esc(alt)}" loading="lazy">`;
  if (model) return `<img class="mark" data-kit-file="${esc(model)}" src="./studio/mark.svg" alt="${esc(alt)}">`;
  return '';
}

function nodeHtml(lane, node, index) {
  const asset = lane.asset;
  const file = nodeFile(asset, node.id);
  const shown = node.state === 'done' ? thumbHtml(file, node.label) : '';
  const moves = node.id === 'animated' && asset.clips?.length ? `${asset.clips.length} move${asset.clips.length === 1 ? '' : 's'}` : '';
  const actions = nodeActions(node, asset);
  const buttons = actions.includes('make')
    ? `<button type="button" class="primary small" data-run="${node.id}" data-count="1" data-tip="Run this step now, with the usual settings.">Make</button>`
    : actions.includes('try')
      ? `<span class="tries" role="group" aria-label="Make it again">${TRY_COUNTS.map((n) => `<button type="button" class="ghost small" data-run="${node.id}" data-count="${n}" data-tip="${n === 1 ? 'Make this step again. The current one is kept.' : `Make ${n} more tries, one after another. Each is kept.`}">${n === 1 ? 'Redo' : `×${n}`}</button>`).join('')}</span>`
      : '';
  const chosen = stepMade(asset, node.id);
  const options = node.options.length > 1 ? `<div class="options" role="group" aria-label="Tries">${node.options.map((o, i) => {
    const pressed = stepMade(o, node.id) === chosen;
    return `<button type="button" class="option" data-use="${esc(o.id)}" aria-pressed="${pressed}" data-tip="${pressed ? 'The try this kit uses.' : 'Use this try in the kit.'}">${thumbHtml(nodeFile(o, node.id), `Try ${i + 1}`) || i + 1}</button>`;
  }).join('')}</div>` : '';
  return `<li class="node ${node.state}" data-node="${node.id}">
      ${index ? '<span class="link" aria-hidden="true"></span>' : ''}
      <header><b>${esc(node.label)}</b><span class="state">${node.state === 'done' ? '✓' : esc(STATE_WORDS[node.state])}</span></header>
      ${shown ? `<button type="button" class="shot" data-open="${node.id}" data-tip="Open in the viewer">${shown}</button>` : moves ? `<button type="button" class="shot words" data-open="${node.id}">${esc(moves)}</button>` : ''}
      ${node.options.length > 1 ? `<small class="meta">${node.options.length} tries</small>` : ''}
      ${options}
      ${buttons ? `<div class="acts">${buttons}</div>` : ''}
    </li>`;
}

function laneHtml(lane) {
  const title = lane.missing ? 'Not in the Library any more' : esc(lane.asset.name);
  const remove = lane.role === 'gear' ? `<button type="button" class="ghost small" data-remove="${esc(lane.id)}" data-tip="Take it out of the kit. It stays in the Library.">Take out</button>` : '';
  return `<section class="lane ${lane.role}" data-lane="${esc(lane.id)}">
      <div class="lane-head"><span class="meta">${lane.role === 'character' ? 'Character' : 'Gear'}</span><b>${title}</b>${remove}</div>
      ${lane.missing ? '<p class="hint">It was deleted. Take it out, or leave it as a reminder.</p>' : `<ol class="nodes">${lane.nodes.map((n, i) => nodeHtml(lane, n, i)).join('')}</ol>`}
    </section>`;
}

/**
 * Open the pane over the viewer. `on` holds the page's handlers:
 * open(asset, stepId), prop(asset, index), run(asset, stepId, count), use(laneId, assetId),
 * remove(assetId), add(assetId), rename(name), remove kit: drop(), makeGear(), thumb(img, file).
 * Returns { render(kit, assets, waiting) } for the page to redraw it when the Library changes.
 */
export function openKitPane({ stage, on }) {
  stage.querySelector('.sheet')?.onSheetClose?.();
  stage.querySelector('.sheet')?.remove();
  const el = document.createElement('div');
  el.className = 'sheet kit-sheet';
  stage.appendChild(el);
  let state = null;

  const render = (kit, assets, waiting = 0) => {
    state = { kit, assets };
    const lanes = kitLanes(kit, assets);
    const props = kitProps(kit, assets);
    const inKit = new Set([kit.character, ...kit.gear]);
    const loose = assets.filter((a) => (a.kind === 'prop set' || a.kind === 'prop') && !inKit.has(a.id) && !a.hidden);
    const [character, ...gear] = lanes;
    el.innerHTML = `<div class="sheet-card kit-card">
      <div class="sheet-top"><h2><input class="kit-name" value="${esc(kit.name)}" maxlength="80" aria-label="Kit name"></h2>
        <span class="row"><button type="button" class="primary" data-gear data-tip="Make a sheet of nine props for this character. It joins the kit when it is done.">Make gear</button>
        <button type="button" class="ghost" data-close>Close</button></span></div>
      <p class="hint">A character and the gear made for it, kept together. Each box is one step: click a picture to open it,
        Redo or ×2–×4 to make more tries, and pick the try the kit should use.${waiting ? ` <b>${waiting} ${waiting === 1 ? 'try' : 'tries'} waiting</b>: they run one after another.` : ''}</p>
      <div class="kit-tree">${laneHtml(character)}<div class="gear-lanes">${gear.map(laneHtml).join('') || '<p class="hint">No gear yet. Press Make gear.</p>'}</div></div>
      ${loose.length ? `<label class="field">Add gear from the Library<select data-add><option value="">Pick a prop or prop set…</option>${loose.map((a) => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('')}</select></label>` : ''}
      <h3>All props · ${props.length}</h3>
      ${props.length ? `<div class="kit-props">${props.map((p) => `<button type="button" class="prop" data-prop="${esc(p.setId)}" data-index="${p.index}">
          ${thumbHtml({ model: p.file }, p.name)}<b>${esc(p.name)}</b><small class="meta">${esc(p.set)}</small></button>`).join('')}</div>`
        : '<p class="hint">Split props show here, all at once.</p>'}
      <div class="row end"><button type="button" class="ghost small" data-drop data-tip="Removes the grouping only. Every asset stays in the Library.">Delete kit</button></div>
    </div>`;
    el.querySelectorAll('[data-kit-file]').forEach((img) => on.thumb(img, img.dataset.kitFile));
  };

  const assetOf = (id) => state.assets.find((a) => a.id === id);
  el.addEventListener('click', (event) => {
    const t = event.target.closest('button');
    if (!t || !state) return;
    const lane = t.closest('[data-lane]')?.dataset.lane;
    if (t.dataset.close !== undefined) { el.remove(); return; }
    if (t.dataset.gear !== undefined) on.makeGear();
    else if (t.dataset.open) on.open(assetOf(lane), t.dataset.open);
    else if (t.dataset.prop) on.prop(assetOf(t.dataset.prop), Number(t.dataset.index));
    else if (t.dataset.run) on.run(assetOf(lane), t.dataset.run, Number(t.dataset.count));
    else if (t.dataset.use && t.getAttribute('aria-pressed') !== 'true') on.use(lane, t.dataset.use);
    else if (t.dataset.remove) on.remove(t.dataset.remove);
    else if (t.dataset.drop !== undefined) {
      if (t.dataset.sure) on.drop();
      else { t.dataset.sure = '1'; t.textContent = 'Delete this kit? Click again'; }
    }
  });
  el.addEventListener('change', (event) => {
    if (event.target.matches('[data-add]') && event.target.value) on.add(event.target.value);
    if (event.target.matches('.kit-name')) on.rename(event.target.value);
  });
  el.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') el.remove();
    if (event.key === 'Enter' && event.target.matches('.kit-name')) event.target.blur();
  });
  return { render, element: el };
}
