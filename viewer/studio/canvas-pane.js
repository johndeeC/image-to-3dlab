// The canvas pane: boxes on a board you can zoom and pan, joined by lines; Run all, or run one
// box. Everything that decides something lives in the canvas*.js modules (Node-tested); this file
// turns events into those calls and patches the boxes by id. The runs happen on the server
// (/api/graphs, docs/canvas.md). Boxes reuse the kit pane's node look.

import { NODES, NODE_W, PORT_Y, newGraph, addNode, removeNodes, moveNodes, setSetting, connect, canConnect, removeEdge,
  edgePath, starter, nodeState, runningNode, takersOf, addConnected, connectSelected, group, ungroup, renameGroup,
  groupBounds, pickResult, downstreamOf } from './canvas.js';
import { identity, screenToBoard, zoomAt, zoomTo, zoomStep, panBy, wheelFactor, wheelIntent, fitAll, zoomPercent, rememberView, recallView } from './canvas-view.js';
import { nodesInRect, toggle, addAll, keepExisting } from './canvas-select.js';
import { createHistory, record, undo, redo } from './canvas-history.js';
import { copyNodes, pasteNodes } from './canvas-clip.js';
import { tidy } from './canvas-layout.js';
import { SHORTCUTS, actionFor } from './canvas-keys.js';
import { FIELDS, defaultsFor, readField, toggleValue } from './canvas-settings.js';

const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const STATE_WORDS = { done: '✓', error: 'Stopped', skipped: 'Skipped', cancelled: 'Cancelled', waiting: 'Waiting' };
const PICTURE = /\.(png|jpe?g|webp)$/i;
const STARTERS = [['character', 'Moving character', 'Picture, 3D model, Finish, Rig and a move.'],
  ['sheet', 'Nine props', 'A sheet of nine, cut into nine game-ready props.'],
  ['parts', 'Parts by name', 'One picture of named parts, each its own 3D model.'],
  ['compare', 'Two engines', 'One picture, built by two engines side by side.']];
let clipboard = null;   // kept while the studio is open, so a copy survives closing the canvas

async function json(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || `the server answered ${response.status}`);
    error.stale = !!payload.stale;
    throw error;
  }
  return payload;
}
const post = (fetchImpl, url, body) => fetchImpl(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) });

/**
 * Open the canvas over the viewer. `tools` holds the ready 3D engines, the moves and the picture
 * engines; `on` holds the page's handlers: open(file), thumb(img, file), ran() once a run ends.
 */
export function openCanvasPane({ stage, tools, on, fetchImpl = fetch, interval = 1500 }) {
  stage.querySelector('.sheet')?.onSheetClose?.();
  stage.querySelector('.sheet')?.remove();
  const el = document.createElement('div');
  el.className = 'sheet canvas-sheet';
  stage.appendChild(el);
  // on a narrow window the pane covers everything under the top bar (studio.css)
  const bar = document.querySelector('.top, header');
  if (bar) el.style.setProperty('--canvas-top', `${Math.max(0, Math.round(bar.getBoundingClientRect().bottom))}px`);

  let graph = newGraph(), saved = [], view = identity(), selection = [], history = createHistory();
  let run = null, chain = null, timer = null, saveTimer = null, saving = Promise.resolve();
  let tool = 'pointer', spaceHeld = false, drag = null, menu = null, pastes = 0;
  const cards = new Map();   // box id -> its element
  const sizes = {};          // box id -> {w, h}, measured

  el.innerHTML = `<div class="canvas-top">
      <input class="kit-name canvas-name" maxlength="80" aria-label="Canvas name">
      <select data-pick aria-label="Open a saved canvas"></select>
      <span class="row">
        <button type="button" class="ghost small" data-check data-tip="Shows how this canvas will run, without running it.">Check</button>
        <button type="button" class="primary small" data-run data-tip="Runs every box, one step at a time. Closing the studio does not stop it.">Run all</button>
        <button type="button" class="ghost small" data-stop hidden>Stop</button>
        <button type="button" class="ghost small" data-close>Close</button>
      </span>
    </div>
    <div class="canvas-tools" role="toolbar" aria-label="Canvas tools">
      <button type="button" class="ghost small" data-add-menu data-tip="Add a box, or a ready-made line of boxes. Also Tab, or double-click the board.">+ Add</button>
      <span class="seg"><button type="button" class="ghost small" data-tool="pointer" data-tip="Pick and move boxes (V)">Pointer</button><button type="button" class="ghost small" data-tool="hand" data-tip="Drag the board about (H, or hold Space)">Hand</button></span>
      <span class="seg"><button type="button" class="ghost small" data-zoom="-1" aria-label="Zoom out">−</button><button type="button" class="ghost small zoom" data-zoom="0" data-tip="Back to life size">100%</button><button type="button" class="ghost small" data-zoom="1" aria-label="Zoom in">+</button></span>
      <button type="button" class="ghost small" data-fit data-tip="Fit everything on screen">Fit</button>
      <button type="button" class="ghost small" data-tidy data-tip="Line the boxes up, left to right">Tidy</button>
      <button type="button" class="ghost small" data-lines aria-pressed="true" data-tip="Hide or show the lines">Lines</button>
      <span class="seg"><button type="button" class="ghost small" data-undo>Undo</button><button type="button" class="ghost small" data-redo>Redo</button></span>
      <button type="button" class="ghost small" data-help aria-label="Keyboard shortcuts">?</button>
    </div>
    <p class="hint canvas-say" aria-live="polite"></p>
    <div class="canvas-board" tabindex="0" aria-label="Canvas board">
      <div class="canvas-space"><div class="canvas-frames"></div><svg class="canvas-lines" aria-hidden="true"></svg><div class="canvas-nodes"></div></div>
      <p class="hint canvas-empty">An empty board. Double-click it, press Tab or use + Add.</p>
      <div class="canvas-marquee" hidden></div>
      <div class="canvas-menu" role="menu" hidden></div>
      <div class="canvas-keys" hidden></div>
    </div>
    <div class="canvas-picker" hidden></div>
    <div class="row end"><button type="button" class="ghost small" data-drop data-tip="Removes this board only. What it made stays in the Library.">Delete canvas</button></div>`;
  const $ = (sel) => el.querySelector(sel);
  const board = $('.canvas-board');
  const say = (text, bad = false) => { $('.canvas-say').textContent = text; $('.canvas-say').classList.toggle('bad', bad); };
  say('Drag from a box\'s right-hand dot to join it to another. Drop a line on empty board to add the next box.');

  // ---------------------------------------------------------------- changing the graph
  function apply(next, { key = null } = {}) {
    history = record(history, graph, key);
    graph = next;
    selection = keepExisting(selection, graph);
    render();
    saveSoon();
  }

  function boardPoint(event) {
    const r = board.getBoundingClientRect();
    return screenToBoard(view, event.clientX - r.left, event.clientY - r.top);
  }

  // ---------------------------------------------------------------- drawing
  function fieldHtml(node, field) {
    const [key, label, hint, kind, choices] = field;
    const tip = `data-tip="${esc(hint)}"`;
    if (kind === 'area') return `<textarea data-key="${key}" rows="3" placeholder="${esc(hint)}" aria-label="${esc(label)}"></textarea>`;
    if (kind === 'file') return `<div class="cfile" data-file-field="${key}"></div>`;
    if (kind === 'toggle') return `<label class="ccheck" ${tip}><input type="checkbox" data-key="${key}"> ${esc(label)}</label>`;
    let control;
    if (kind === 'select') {
      const options = choices(tools);
      if (!options.length) return `<small class="hint">${esc(label)}: nothing installed yet. Setup adds it.</small>`;
      control = `<select data-key="${key}">${options.map(([v, words]) => `<option value="${esc(v)}">${esc(words)}</option>`).join('')}</select>`;
    } else if (kind === 'number') {
      control = `<input type="number" data-key="${key}" step="0.1" min="${choices[0]}" max="${choices[1]}">`;
    } else {
      control = `<input type="text" data-key="${key}" placeholder="${esc(hint)}">`;
    }
    return `<label class="cfield" ${tip}><span>${esc(label)}</span>${control}</label>`;
  }

  function cardHtml(node) {
    const def = NODES[node.type];
    const fields = FIELDS[node.type] || [];
    const main = fields.filter((f) => !f[5]), more = fields.filter((f) => f[5]);
    const runs = def.step || def.takes.length === 0;
    return `${def.takes.length ? '<span class="port in" aria-hidden="true"></span>' : ''}
      ${def.makes ? '<span class="port out" data-port title="Drag to another box to join them, or to empty board to add one"></span>' : ''}
      <header data-grip><b data-tip="${esc(def.hint)}">${esc(def.label)}</b><span class="state"></span>
        ${runs ? '<button type="button" class="x" data-box-run aria-label="Run this box" data-tip="Run this box">▶</button>' : ''}
        <button type="button" class="x" data-remove aria-label="Remove ${esc(def.label)}">×</button></header>
      ${main.map((f) => fieldHtml(node, f)).join('')}
      ${!fields.length ? `<small class="hint">${esc(def.hint)}</small>` : ''}
      ${more.length ? `<details class="cmore"><summary>More settings</summary>${more.map((f) => fieldHtml(node, f)).join('')}</details>` : ''}
      <div class="cresults"></div>`;
  }

  function syncFields(card, node) {
    for (const input of card.querySelectorAll('[data-key]')) {
      if (input === document.activeElement) continue;
      const value = node.settings[input.dataset.key];
      if (input.type === 'checkbox') input.checked = toggleValue(input.dataset.key, node.settings);
      else input.value = value == null ? '' : String(value);
    }
    for (const box of card.querySelectorAll('[data-file-field]')) {
      const file = node.settings[box.dataset.fileField];
      const html = file
        ? `<span class="hint" title="${esc(file)}">${esc(file.split('/').pop())}</span><button type="button" class="x" data-clear-file aria-label="Stop using this file">×</button>`
        : `<button type="button" class="ghost small" data-library>From Library</button>${node.type === 'picture' ? '<button type="button" class="ghost small" data-upload>Upload</button>' : ''}`;
      if (box.dataset.shown !== html) { box.innerHTML = html; box.dataset.shown = html; }
    }
  }

  const merged = () => ({ status: run?.status, results: { ...(graph.last_run?.results || {}), ...(run?.results || {}) } });

  function resultsHtml(node) {
    const { state, files, errors } = nodeState(merged(), node.id);
    const picking = node.type === 'picture' && files.length > 1;
    const shown = files.slice(0, 9).map((f) => {
      const img = PICTURE.test(f.file)
        ? `<img src="/api/thumb?path=${encodeURIComponent(f.file)}" alt="" loading="lazy">`
        : `<img class="mark" data-kit-file="${esc(f.file)}" src="./studio/mark.svg" alt="">`;
      const tip = picking ? (node.picked === f.file ? 'The picture that carries on' : 'Pick this one') : (f.label || 'Open in the viewer');
      return `<button type="button" class="option" data-file="${esc(f.file)}" ${picking ? `data-pick-file aria-pressed="${node.picked === f.file}"` : ''} data-tip="${esc(tip)}">${img}</button>`;
    }).join('');
    const more = files.length > 9 ? `<small class="hint">+${files.length - 9} more</small>` : '';
    const carry = picking && node.picked && downstreamOf(graph, node.id).length
      ? '<button type="button" class="primary small" data-carry>Carry on with this one</button>' : '';
    return { state, partly: state === 'error' && files.length > 0,
      html: `${shown || more ? `<div class="options">${shown}${more}</div>` : ''}${carry}${errors.length ? `<small class="bad">${esc(errors[0])}</small>` : ''}` };
  }

  function renderNodes() {
    const box = $('.canvas-nodes');
    const live = runningNode(run, graph, chain);
    const seen = new Set();
    for (const n of graph.nodes) {
      seen.add(n.id);
      let card = cards.get(n.id);
      if (!card || card.dataset.type !== n.type) {
        card?.remove();
        card = document.createElement('div');
        card.dataset.node = n.id;
        card.dataset.type = n.type;
        card.style.width = `${NODE_W}px`;
        card.innerHTML = cardHtml(n);
        box.appendChild(card);
        cards.set(n.id, card);
      }
      card.style.transform = `translate(${n.x}px, ${n.y}px)`;
      syncFields(card, n);
      const { state, partly, html } = resultsHtml(n);
      const shown = n.id === live ? 'running' : state;
      card.className = `node cnode ${shown || ''}${selection.includes(n.id) ? ' chosen' : ''}`;
      card.querySelector('.state').textContent = n.id === live
        ? (chain?.percent != null ? `${Math.round(chain.percent)}%` : 'Running') : partly ? 'Some stopped'
          : state === 'waiting' && run?.status !== 'running' ? 'Waits for a pick' : STATE_WORDS[state] || '';
      const results = card.querySelector('.cresults');
      const next = html + (n.id === live && chain?.message ? `<small class="hint">${esc(chain.message)}</small>` : '');
      if (results.dataset.shown !== next) {
        results.innerHTML = next;
        results.dataset.shown = next;
        results.querySelectorAll('[data-kit-file]').forEach((img) => on.thumb?.(img, img.dataset.kitFile));
      }
    }
    for (const [id, card] of cards) if (!seen.has(id)) { card.remove(); cards.delete(id); }
    for (const [id, card] of cards) sizes[id] = { w: card.offsetWidth || NODE_W, h: card.offsetHeight || 150 };
    $('.canvas-empty').hidden = graph.nodes.length > 0;
  }

  function renderLines(extra = null) {
    const at = new Map(graph.nodes.map((n) => [n.id, n]));
    $('.canvas-lines').innerHTML = graph.edges.map((e) => {
      const a = at.get(e.from), b = at.get(e.to);
      if (!a || !b) return '';
      const d = edgePath(a, b);
      return `<g class="edge" data-from="${esc(e.from)}" data-to="${esc(e.to)}"><path class="hit" d="${d}"><title>Click to remove this line</title></path><path class="wire" d="${d}"/></g>`;
    }).join('') + (extra ? `<path class="wire draft" d="${extra}"/>` : '');
  }

  function renderFrames() {
    $('.canvas-frames').innerHTML = (graph.groups || []).map((gr) => {
      const b = groupBounds(gr, graph.nodes, sizes);
      if (!b) return '';
      return `<div class="frame" data-group="${esc(gr.id)}" style="transform:translate(${b.x}px,${b.y}px);width:${b.w}px;height:${b.h}px">
        <div class="frame-bar" data-frame-grip><input class="frame-label" value="${esc(gr.label)}" maxlength="60" aria-label="Frame name"></div></div>`;
    }).join('');
  }

  function renderView() {
    $('.canvas-space').style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.k})`;
    $('.zoom').textContent = zoomPercent(view);
    board.style.backgroundSize = `${22 * view.k}px ${22 * view.k}px`;
    board.style.backgroundPosition = `${view.x}px ${view.y}px`;
  }

  function renderTools() {
    el.querySelectorAll('[data-tool]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.tool === tool)));
    board.classList.toggle('hand', tool === 'hand' || spaceHeld);
    $('[data-undo]').disabled = !history.past.length;
    $('[data-redo]').disabled = !history.future.length;
    const going = run?.status === 'running';
    $('[data-run]').hidden = going;
    $('[data-stop]').hidden = !going;
  }

  function render() {
    renderNodes();
    renderFrames();
    renderLines();
    renderView();
    renderTools();
  }

  function renderPicker() {
    $('.canvas-name').value = graph.name;
    $('[data-pick]').innerHTML = `<option value="">New canvas</option>${saved.map((g) => `<option value="${esc(g.id)}" ${g.id === graph.id ? 'selected' : ''}>${esc(g.name)}</option>`).join('')}`;
  }

  let storage = null;
  try { storage = window.localStorage; } catch { /* storage blocked: the view is just not kept */ }
  const saveView = () => rememberView(storage, graph.id, view);   // this browser's, never the shared file
  function setView(next) {
    view = next;
    renderView();
    saveView();
  }
  function fit() {
    renderNodes();
    setView(fitAll(graph.nodes, sizes, board.clientWidth, board.clientHeight));
  }

  function openGraph(next) {
    graph = { groups: [], ...next };
    history = createHistory();
    selection = [];
    for (const card of cards.values()) card.remove();
    cards.clear();
    renderPicker();
    render();
    const kept = recallView(storage, graph.id);
    if (kept) { view = kept; renderView(); } else if (graph.nodes.length) fit();
  }

  // ---------------------------------------------------------------- saving
  function saveNow() {
    clearTimeout(saveTimer);
    saveTimer = null;
    saving = saving.catch(() => {}).then(async () => {
      const { last_run: _kept, view: _mine, ...body } = graph;   // the server keeps the last run; the view is this browser's
      let stored;
      try {
        stored = await json(await post(fetchImpl, '/api/graphs', body));
      } catch (error) {
        if (!error.stale || !graph.id) throw error;
        // someone else (another tab, an agent) saved it since: show theirs
        openGraph(await json(await fetchImpl(`/api/graphs/${graph.id}`)));
        say('This canvas was changed somewhere else, so it now shows those changes.', true);
        return graph;
      }
      const fresh = graph.id !== stored.id;
      graph = { ...graph, id: stored.id, updated: stored.updated };
      if (fresh) rememberView(storage, stored.id, view);
      // the list must follow every save: opening a board from an old copy of it was refused as stale
      saved = [stored, ...saved.filter((g) => g.id !== stored.id)];
      renderPicker();
      return graph;
    });
    return saving;
  }
  const saveSoon = () => { clearTimeout(saveTimer); saveTimer = setTimeout(() => saveNow().catch((e) => say(e.message, true)), 600); };

  async function loadList() {
    try { saved = (await json(await fetchImpl('/api/graphs'))).graphs || []; } catch { saved = []; }
  }

  // ---------------------------------------------------------------- running
  async function follow(id) {
    clearTimeout(timer);
    try {
      run = await json(await fetchImpl(`/api/graph-runs/${id}`));
      chain = run.current_chain ? await fetchImpl(`/api/chains/${run.current_chain}`).then((r) => (r.ok ? r.json() : null)).catch(() => null) : null;
    } catch (error) {
      say(error.message, true);
      run = null;
      render();
      return;
    }
    render();
    if (run.status === 'running') {
      const line = run.plan[run.stage] ? run.plan[run.stage].replace(/^Run \d+[^:]*: /, '') : '';
      say(`Running ${line}… You can close this; it keeps going.`);
      timer = setTimeout(() => follow(id), interval);
      return;
    }
    const waiting = run.waiting_for_pick?.length;
    say(run.status === 'done'
      ? (waiting ? 'Pick a picture in the Picture box, then press Carry on with this one.' : 'Done. Everything it made is in your Library.')
      : run.status === 'cancelled' ? 'Stopped. Everything finished before that is kept.'
        : `It stopped: ${run.error || 'see the boxes marked Stopped.'}`, run.status === 'error');
    graph = { ...graph, last_run: { status: run.status, results: merged().results } };
    run = null;
    chain = null;
    render();
    on.ran?.();
  }

  async function start(body) {
    try {
      await saveNow();
      const started = await json(await post(fetchImpl, `/api/graphs/${graph.id}/run`, body));
      say(started.plan.join(' · '));
      follow(started.id);
    } catch (error) { say(error.message, true); }
  }

  // ---------------------------------------------------------------- menus
  function closeMenu() { menu = null; $('.canvas-menu').hidden = true; }

  /** A menu at a screen point inside the board. `items` are [label, hint, action]. */
  function openMenu(sx, sy, title, items) {
    const box = $('.canvas-menu');
    menu = { items };
    box.innerHTML = `<p class="hint">${esc(title)}</p>${items.map(([label, hint], i) => `<button type="button" role="menuitem" data-item="${i}" title="${esc(hint)}"><b>${esc(label)}</b><small class="hint">${esc(hint)}</small></button>`).join('')}`;
    box.hidden = false;
    box.style.left = `${Math.max(4, Math.min(sx, board.clientWidth - box.offsetWidth - 4))}px`;
    box.style.top = `${Math.max(4, Math.min(sy, board.clientHeight - box.offsetHeight - 4))}px`;
    box.querySelector('button')?.focus();
  }

  function addMenu(sx, sy) {
    const at = screenToBoard(view, sx, sy);
    const boxes = Object.entries(NODES).map(([type, def]) => [def.label, def.hint, () => {
      apply(addNode(graph, type, at.x, at.y, defaultsFor(type, tools)));
      selection = [graph.nodes.at(-1).id];
      render();
    }]);
    const starters = STARTERS.map(([kind, label, hint]) => [`Start: ${label}`, hint, () => {
      const made = starter(kind, { engines: (tools.engines || []).map((e) => e.id), move: (tools.presets || []).find((p) => !p.take)?.id });
      const empty = !graph.nodes.length;
      const { graph: next, ids } = pasteNodes(graph, made, at.x - 40, at.y - 60);
      apply(empty ? { ...next, name: made.name } : next);
      selection = ids;
      if (empty) renderPicker();
      fit();
      say('Describe what you want in the Picture box, then press Run all.');
    }]);
    openMenu(sx, sy, 'Add a box', [...boxes, ...starters]);
  }

  function takersMenu(from, sx, sy) {
    const at = screenToBoard(view, sx, sy);
    const type = graph.nodes.find((n) => n.id === from)?.type;
    const items = takersOf(type).map((t) => [NODES[t].label, NODES[t].hint, () => {
      apply(addConnected(graph, from, t, at.x, at.y - PORT_Y, defaultsFor(t, tools)));
      selection = [graph.nodes.at(-1).id];
      render();
    }]);
    if (!items.length) { say(`Nothing takes what ${NODES[type].label} makes.`, true); return; }
    openMenu(sx, sy, 'Add the next box', items);
  }

  function runMenu(id, sx, sy) {
    const below = downstreamOf(graph, id).length;
    openMenu(sx, sy, 'Run', [
      ['Run this box', 'Runs just this step, from what the boxes before it made last time.', () => start({ node: id, mode: 'only' })],
      ...(below ? [['Run from here', 'Runs this step and every box after it.', () => start({ node: id, mode: 'from' })]] : []),
    ]);
  }

  // ---------------------------------------------------------------- the Library picker
  async function pickFromLibrary(id) {
    const node = graph.nodes.find((n) => n.id === id);
    const box = $('.canvas-picker');
    box.hidden = false;
    box.dataset.for = id;
    box.innerHTML = '<p class="hint">Loading your Library…</p>';
    let assets = [];
    try { assets = (await json(await fetchImpl('/api/assets'))).assets || []; } catch (error) { box.innerHTML = `<p class="bad">${esc(error.message)}</p>`; return; }
    const want = node.type === 'picture'
      ? assets.filter((a) => a.picture).map((a) => ({ name: a.name, file: a.picture, thumb: a.picture }))
      : assets.filter((a) => a.finished || a.model).map((a) => ({ name: a.name, file: a.finished || a.model, thumb: a.picture }));
    box.innerHTML = `<div class="row"><b>${node.type === 'picture' ? 'Pick a picture' : 'Pick a 3D model'}</b><button type="button" class="ghost small" data-picker-close>Close</button></div>
      ${want.length ? `<div class="kit-props">${want.slice(0, 120).map((a) => `<button type="button" class="prop" data-use-file="${esc(a.file)}">
        ${a.thumb ? `<img src="/api/thumb?path=${encodeURIComponent(a.thumb)}" alt="" loading="lazy">` : ''}<b>${esc(a.name)}</b></button>`).join('')}</div>`
        : '<p class="hint">Nothing in your Library yet.</p>'}`;
  }

  function upload(id) {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/png,image/jpeg,image/webp';
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      const form = new FormData();
      form.append('file', file);
      try {
        const { file: kept } = await json(await fetchImpl('/api/graphs/upload', { method: 'POST', body: form }));
        apply(setSetting(graph, id, 'file', kept));
      } catch (error) { say(error.message, true); }
    };
    input.click();
  }

  // ---------------------------------------------------------------- actions (buttons and keys)
  const keepRun = (next) => ({ ...next, last_run: graph.last_run, updated: graph.updated });
  const actions = {
    add: () => addMenu(board.clientWidth / 2 - 110, board.clientHeight / 4),
    pointer: () => { tool = 'pointer'; renderTools(); },
    hand: () => { tool = 'hand'; renderTools(); },
    zoomIn: () => setView(zoomStep(view, 1, board.clientWidth, board.clientHeight)),
    zoomOut: () => setView(zoomStep(view, -1, board.clientWidth, board.clientHeight)),
    fit,
    selectAll: () => { selection = graph.nodes.map((n) => n.id); render(); },
    copy: () => {
      clipboard = copyNodes(graph, selection);
      pastes = 0;
      if (clipboard) say(`Copied ${clipboard.nodes.length} box${clipboard.nodes.length === 1 ? '' : 'es'}.`);
    },
    paste: () => {
      if (!clipboard) return;
      pastes += 1;
      const { graph: next, ids } = pasteNodes(graph, clipboard, 40 * pastes, 40 * pastes);
      apply(next);
      selection = ids;
      render();
    },
    delete: () => { if (selection.length) apply(removeNodes(graph, selection)); },
    undo: () => {
      const u = undo(history, graph);
      if (!u) return;
      history = u.history;
      graph = keepRun(u.graph);
      selection = keepExisting(selection, graph);
      render();
      saveSoon();
    },
    redo: () => {
      const r = redo(history, graph);
      if (!r) return;
      history = r.history;
      graph = keepRun(r.graph);
      selection = keepExisting(selection, graph);
      render();
      saveSoon();
    },
    connect: () => {
      const { graph: next, made } = connectSelected(graph, selection);
      if (made) apply(next);
      say(made ? `Joined ${made} pair${made === 1 ? '' : 's'}.` : 'Choose two or more boxes that fit together, left to right.', !made);
    },
    group: () => { if (selection.length) { apply(group(graph, selection, 'Group')); say('Framed. Click the frame\'s name to rename it.'); } },
    ungroup: () => apply(ungroup(graph, selection)),
    tidy: () => { apply(tidy(graph, sizes)); fit(); },
    lines: () => { const off = board.classList.toggle('no-lines'); $('[data-lines]').setAttribute('aria-pressed', String(!off)); },
    help: () => {
      const panel = $('.canvas-keys');
      panel.hidden = !panel.hidden;
      panel.innerHTML = `<div class="row"><b>Keyboard shortcuts</b><button type="button" class="ghost small" data-keys-close>Close</button></div>
        <dl>${SHORTCUTS.map(([, what, keys]) => `<dt>${esc(keys)}</dt><dd>${esc(what)}</dd>`).join('')}</dl>`;
    },
    escape: () => {
      if (menu) closeMenu();
      else if (!$('.canvas-keys').hidden) $('.canvas-keys').hidden = true;
      else if (!$('.canvas-picker').hidden) $('.canvas-picker').hidden = true;
      else if (selection.length) { selection = []; render(); }
    },
  };

  // ---------------------------------------------------------------- pointer
  board.addEventListener('pointerdown', (event) => {
    if (event.target.closest('.canvas-menu, .canvas-keys')) return;
    if (menu) closeMenu();
    const card = event.target.closest('.cnode');
    const additive = event.shiftKey || event.metaKey || event.ctrlKey;
    const pt = boardPoint(event);
    if (event.button === 1 || ((tool === 'hand' || spaceHeld) && event.button === 0)) {
      drag = { kind: 'pan', x: event.clientX, y: event.clientY };
    } else if (card && event.target.closest('[data-port]')) {
      drag = { kind: 'line', from: card.dataset.node };
    } else if (card && event.target.closest('[data-grip]') && !event.target.closest('button')) {
      const id = card.dataset.node;
      if (additive) selection = toggle(selection, id, true);
      else if (!selection.includes(id)) selection = [id];
      drag = { kind: 'move', ids: [...selection], start: pt, key: `move:${Date.now()}`, base: graph, moved: false };
      render();
    } else if (event.target.closest('[data-frame-grip]') && !event.target.matches('input')) {
      const gr = graph.groups.find((g) => g.id === event.target.closest('[data-group]').dataset.group);
      drag = { kind: 'move', ids: [...gr.nodes], start: pt, key: `move:${Date.now()}`, base: graph, moved: false };
    } else if (card) {
      const id = card.dataset.node;
      selection = additive ? toggle(selection, id, true) : selection.includes(id) ? selection : [id];
      for (const [cid, c] of cards) c.classList.toggle('chosen', selection.includes(cid));
      return;   // a field inside the box: leave the click to it
    } else if (!event.target.closest('.edge, .frame-label')) {
      drag = { kind: 'marquee', start: pt, sx: event.clientX, sy: event.clientY, additive, before: additive ? selection : [] };
    } else return;
    event.preventDefault();
    board.focus({ preventScroll: true });
    board.setPointerCapture?.(event.pointerId);
  });

  board.addEventListener('pointermove', (event) => {
    if (!drag) return;
    if (drag.kind === 'pan') {
      setView(panBy(view, event.clientX - drag.x, event.clientY - drag.y));
      drag.x = event.clientX;
      drag.y = event.clientY;
    } else if (drag.kind === 'move') {
      const pt = boardPoint(event);
      graph = keepRun(moveNodes(drag.base, drag.ids, pt.x - drag.start.x, pt.y - drag.start.y));
      drag.moved = true;
      for (const id of drag.ids) {
        const n = graph.nodes.find((x) => x.id === id);
        cards.get(id).style.transform = `translate(${n.x}px, ${n.y}px)`;
      }
      renderLines();
      renderFrames();
    } else if (drag.kind === 'line') {
      const a = graph.nodes.find((n) => n.id === drag.from);
      const pt = boardPoint(event);
      renderLines(edgePath(a, { x: pt.x, y: pt.y - PORT_Y }));
    } else if (drag.kind === 'marquee') {
      const r = board.getBoundingClientRect();
      const box = $('.canvas-marquee');
      Object.assign(box.style, { left: `${Math.min(drag.sx, event.clientX) - r.left}px`, top: `${Math.min(drag.sy, event.clientY) - r.top}px`,
        width: `${Math.abs(event.clientX - drag.sx)}px`, height: `${Math.abs(event.clientY - drag.sy)}px` });
      box.hidden = false;
      const pt = boardPoint(event);
      selection = addAll(drag.before, nodesInRect(graph.nodes, sizes, { x: drag.start.x, y: drag.start.y, w: pt.x - drag.start.x, h: pt.y - drag.start.y }), drag.additive);
      for (const [id, card] of cards) card.classList.toggle('chosen', selection.includes(id));
    }
  });

  board.addEventListener('pointerup', (event) => {
    if (!drag) return;
    const was = drag;
    drag = null;
    $('.canvas-marquee').hidden = true;
    if (was.kind === 'move' && was.moved) {
      const moved = graph;
      graph = keepRun(was.base);
      apply(keepRun(moved), { key: was.key });
    } else if (was.kind === 'marquee') {
      if (Math.abs(event.clientX - was.sx) < 3 && Math.abs(event.clientY - was.sy) < 3 && !was.additive) selection = [];
      render();
    } else if (was.kind === 'line') {
      const target = document.elementFromPoint(event.clientX, event.clientY)?.closest('.cnode');
      renderLines();
      if (!target) {
        const r = board.getBoundingClientRect();
        takersMenu(was.from, event.clientX - r.left, event.clientY - r.top);
        return;
      }
      if (target.dataset.node === was.from) return;
      const ok = canConnect(graph, was.from, target.dataset.node);
      if (ok !== true) { say(ok, true); return; }
      apply(connect(graph, was.from, target.dataset.node));
      say('Joined. Press Check to see how it will run.');
    }
  });

  board.addEventListener('dblclick', (event) => {
    if (event.target.closest('.cnode, .canvas-menu, .canvas-keys, .frame-bar')) return;
    const r = board.getBoundingClientRect();
    addMenu(event.clientX - r.left, event.clientY - r.top);
  });

  board.addEventListener('wheel', (event) => {
    if (event.target.closest('.canvas-menu, .canvas-keys, textarea')) return;
    event.preventDefault();
    const r = board.getBoundingClientRect();
    if (wheelIntent(event) === 'zoom') setView(zoomAt(view, wheelFactor(event.deltaY, event.deltaMode), event.clientX - r.left, event.clientY - r.top));
    else setView(panBy(view, -event.deltaX, -event.deltaY));
  }, { passive: false });

  // ---------------------------------------------------------------- clicks
  const BUTTONS = { fit: 'fit', tidy: 'tidy', lines: 'lines', undo: 'undo', redo: 'redo', help: 'help' };
  el.addEventListener('click', async (event) => {
    const item = event.target.closest('[data-item]');
    if (item && menu) { const chosen = menu.items[Number(item.dataset.item)][2]; closeMenu(); chosen(); return; }
    const t = event.target.closest('button, .edge');
    if (!t) return;
    const id = t.closest('.cnode')?.dataset.node;
    const near = () => { const r = board.getBoundingClientRect(), b = t.getBoundingClientRect(); return [Math.max(0, b.left - r.left), Math.max(0, b.bottom - r.top)]; };
    if (t.classList.contains('edge')) { apply(removeEdge(graph, t.dataset.from, t.dataset.to)); return; }
    if (t.dataset.close !== undefined) { close(); return; }
    if (t.dataset.addMenu !== undefined) { addMenu(near()[0], 4); return; }
    if (t.dataset.tool) { actions[t.dataset.tool](); return; }
    if (t.dataset.zoom) {
      const d = Number(t.dataset.zoom);
      setView(d ? zoomStep(view, d, board.clientWidth, board.clientHeight) : zoomTo(view, 1, board.clientWidth / 2, board.clientHeight / 2));
      return;
    }
    const named = Object.keys(BUTTONS).find((k) => t.dataset[k] !== undefined);
    if (named) { actions[BUTTONS[named]](); return; }
    if (t.dataset.keysClose !== undefined) { $('.canvas-keys').hidden = true; return; }
    if (t.dataset.pickerClose !== undefined) { $('.canvas-picker').hidden = true; return; }
    if (t.dataset.useFile) { apply(setSetting(graph, $('.canvas-picker').dataset.for, 'file', t.dataset.useFile)); $('.canvas-picker').hidden = true; return; }
    if (t.dataset.library !== undefined) { pickFromLibrary(id); return; }
    if (t.dataset.upload !== undefined) { upload(id); return; }
    if (t.dataset.clearFile !== undefined) { apply(setSetting(graph, id, 'file', '')); return; }
    if (t.dataset.remove !== undefined) { apply(removeNodes(graph, [id])); return; }
    if (t.dataset.boxRun !== undefined) { runMenu(id, ...near()); return; }
    if (t.dataset.pickFile !== undefined) { apply(pickResult(graph, id, t.dataset.file)); return; }
    if (t.dataset.carry !== undefined) { start({ node: id, mode: 'after' }); return; }
    if (t.dataset.file) {
      on.open?.(t.dataset.file);
      if (getComputedStyle(el).position === 'fixed') say('Opened in the viewer. Close the canvas to look at it; the canvas keeps everything.');
      return;
    }
    if (t.dataset.check !== undefined) {
      try { say((await json(await post(fetchImpl, '/api/graphs/check', graph))).plan.join(' · ')); } catch (error) { say(error.message, true); }
    } else if (t.dataset.run !== undefined) {
      t.disabled = true;
      await start({});
      t.disabled = false;
    } else if (t.dataset.stop !== undefined && run) {
      fetchImpl(`/api/graph-runs/${run.id}/cancel`, { method: 'POST' }).catch(() => {});
      say('Stopping after the step that is running…');
    } else if (t.dataset.drop !== undefined) {
      if (!graph.id) { openGraph(newGraph()); return; }
      if (!t.dataset.sure) { t.dataset.sure = '1'; t.textContent = 'Delete this canvas? Click again'; return; }
      await fetchImpl(`/api/graphs/${graph.id}/delete`, { method: 'POST' }).catch(() => {});
      delete t.dataset.sure;
      t.textContent = 'Delete canvas';
      await loadList();
      openGraph(saved[0] || newGraph());
    }
  });

  // ---------------------------------------------------------------- fields
  const fieldOf = (input) => {
    const id = input.closest('.cnode').dataset.node;
    return [id, FIELDS[graph.nodes.find((n) => n.id === id).type].find((f) => f[0] === input.dataset.key)];
  };
  el.addEventListener('input', (event) => {
    const input = event.target.closest('[data-key]');
    if (input && input.type !== 'checkbox' && input.tagName !== 'SELECT') {
      const [id, field] = fieldOf(input);
      apply(setSetting(graph, id, field[0], readField(field, input.value)), { key: `type:${id}:${field[0]}` });
    }
    if (event.target.matches('.canvas-name')) { graph = { ...graph, name: event.target.value.trim() || 'Untitled canvas' }; saveSoon(); }
  });
  el.addEventListener('change', async (event) => {
    const input = event.target.closest('[data-key]');
    if (input && (input.type === 'checkbox' || input.tagName === 'SELECT')) {
      const [id, field] = fieldOf(input);
      apply(setSetting(graph, id, field[0], readField(field, input.type === 'checkbox' ? input.checked : input.value)));
      return;
    }
    if (event.target.matches('.frame-label')) {
      apply(renameGroup(graph, event.target.closest('[data-group]').dataset.group, event.target.value));
      return;
    }
    if (!event.target.matches('[data-pick]')) return;
    if (saveTimer) await saveNow().catch(() => {});
    const id = event.target.value;
    let next = newGraph();
    if (id) {
      try { next = await json(await fetchImpl(`/api/graphs/${id}`)); } catch { next = saved.find((g) => g.id === id) || next; }
    }
    openGraph(next);
  });

  // ---------------------------------------------------------------- keys
  el.addEventListener('keydown', (event) => {
    const typing = !!event.target.closest('input, textarea, select');
    if (event.key === ' ' && !typing && !event.target.closest('button')) { spaceHeld = true; renderTools(); event.preventDefault(); return; }
    const action = actionFor(event, { typing });
    if (!action || !actions[action]) return;
    if (action === 'add' && event.target.closest('.canvas-menu')) return;   // Tab moves through the menu
    event.preventDefault();
    actions[action]();
  });
  el.addEventListener('keyup', (event) => { if (event.key === ' ' && spaceHeld) { spaceHeld = false; renderTools(); } });

  function close() {
    clearTimeout(timer);
    if (saveTimer) saveNow().catch(() => {});
    el.remove();
  }
  el.onSheetClose = () => { clearTimeout(timer); if (saveTimer) saveNow().catch(() => {}); };

  // a run that is already going (the tab was reopened) is picked back up
  (async () => {
    await loadList();
    let running = null;
    try { ({ running } = await json(await fetchImpl('/api/graph-runs'))); } catch { /* an older server */ }
    openGraph((running && saved.find((g) => g.id === running.graph)) || saved[0] || newGraph());
    if (running) follow(running.id);
    board.focus({ preventScroll: true });
  })();
  return { element: el };
}
