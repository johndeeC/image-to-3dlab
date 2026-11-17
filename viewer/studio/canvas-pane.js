// The canvas pane: drag step boxes about, join them with lines, press Run. The graph logic is in
// canvas.js; the runs happen on the server (/api/graphs, docs/canvas.md). Boxes reuse the kit
// pane's node look.

import { NODES, GOALS, NODE_W, PORT_Y, newGraph, addNode, removeNode, moveNode, setSetting, connect, canConnect,
  removeEdge, edgePath, starter, nodeState, runningNode } from './canvas.js';

const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const STATE_WORDS = { done: '✓', error: 'Stopped', skipped: 'Skipped', cancelled: 'Cancelled', waiting: 'Waiting', running: 'Running' };
const PICTURE = /\.(png|jpe?g|webp)$/i;
const SVG = 'http://www.w3.org/2000/svg';

async function json(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `the server answered ${response.status}`);
  return payload;
}

/**
 * Open the canvas over the viewer. `tools` holds the ready 3D engines and the moves;
 * `on` holds the page's handlers: open(file), thumb(img, file), ran() once a run ends.
 */
export function openCanvasPane({ stage, tools, on, fetchImpl = fetch, interval = 1500 }) {
  stage.querySelector('.sheet')?.onSheetClose?.();
  stage.querySelector('.sheet')?.remove();
  const el = document.createElement('div');
  el.className = 'sheet canvas-sheet';
  stage.appendChild(el);

  let graph = newGraph();
  let saved = [];
  let run = null, chain = null, timer = null, saveTimer = null;
  const engines = tools.engines || [];
  const moves = (tools.presets || []).filter((p) => !p.take);
  const defaults = { model: engines[0] ? { engine: engines[0].id } : {}, move: moves[0] ? { move: moves[0].id } : {} };

  el.innerHTML = `<div class="canvas-top">
      <input class="kit-name canvas-name" maxlength="80" aria-label="Canvas name">
      <select data-pick aria-label="Open a saved canvas"></select>
      <span class="row">
        <button type="button" class="ghost" data-check data-tip="Shows how this canvas will run, without running it.">Check</button>
        <button type="button" class="primary" data-run data-tip="Runs every box, one step at a time. Closing the studio does not stop it.">Run</button>
        <button type="button" class="ghost" data-stop hidden>Stop</button>
        <button type="button" class="ghost" data-close>Close</button>
      </span>
    </div>
    <div class="canvas-tools">
      <span class="meta">Add</span>${Object.entries(NODES).map(([type, n]) => `<button type="button" class="ghost small" data-add="${type}" data-tip="${esc(n.hint)}">${esc(n.label)}</button>`).join('')}
      <span class="meta">Start from</span>
      <button type="button" class="ghost small" data-starter="character" data-tip="Picture, 3D model, Finish, Rig and a move: one moving character.">Moving character</button>
      <button type="button" class="ghost small" data-starter="sheet" data-tip="A sheet of nine, cut into nine game-ready props.">Nine props</button>
      <button type="button" class="ghost small" data-starter="compare" data-tip="One picture, built by two 3D engines side by side.">Two engines</button>
    </div>
    <p class="hint canvas-say" aria-live="polite">Drag from a box's right-hand dot to another box to join them. Click a line to remove it.</p>
    <div class="canvas-board"><div class="canvas-space"><svg class="canvas-lines"></svg><div class="canvas-nodes"></div></div></div>
    <div class="row end"><button type="button" class="ghost small" data-drop data-tip="Removes this board only. What it made stays in the Library.">Delete canvas</button></div>`;
  const $ = (sel) => el.querySelector(sel);
  const say = (text, bad = false) => { $('.canvas-say').textContent = text; $('.canvas-say').classList.toggle('bad', bad); };

  // ---------------------------------------------------------------- drawing
  function settingsHtml(node) {
    const s = node.settings;
    if (node.type === 'picture') {
      return `<textarea data-set="prompt" rows="3" placeholder="Describe it: a rune knight in blue armour" aria-label="Describe the picture">${esc(s.prompt || '')}</textarea>
        <select data-set="goal" aria-label="What the picture is for">${Object.entries(GOALS).map(([k, v]) => `<option value="${k}" ${(s.goal || 'character') === k ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select>
        ${s.file ? `<small class="meta">Uses ${esc(s.file.split('/').pop())}</small>` : ''}`;
    }
    if (node.type === 'model') {
      return engines.length
        ? `<select data-set="engine" aria-label="3D engine">${engines.map((e) => `<option value="${esc(e.id)}" ${s.engine === e.id ? 'selected' : ''}>${esc(e.label)}</option>`).join('')}</select>`
        : '<small class="meta">No 3D engine is installed yet. Setup adds one.</small>';
    }
    if (node.type === 'move') {
      return moves.length
        ? `<select data-set="move" aria-label="Move">${moves.map((p) => `<option value="${esc(p.id)}" ${s.move === p.id ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}</select>`
        : '<small class="meta">Moves are not installed yet. Setup adds them.</small>';
    }
    return `<small class="meta">${esc(NODES[node.type].hint)}</small>`;
  }

  function resultsHtml(id) {
    const { state, files, errors } = nodeState(run || graph.last_run, id);
    const shown = files.slice(0, 9).map((f) => {
      const img = PICTURE.test(f.file)
        ? `<img src="/api/thumb?path=${encodeURIComponent(f.file)}" alt="" loading="lazy">`
        : `<img class="mark" data-kit-file="${esc(f.file)}" src="./studio/mark.svg" alt="">`;
      return `<button type="button" class="option" data-file="${esc(f.file)}" data-tip="${esc(f.label || 'Open in the viewer')}">${img}</button>`;
    }).join('');
    const more = files.length > 9 ? `<small class="meta">+${files.length - 9} more</small>` : '';
    return { state, html: `${shown || more ? `<div class="options">${shown}${more}</div>` : ''}${errors.length ? `<small class="bad">${esc(errors[0])}</small>` : ''}` };
  }

  function renderNodes() {
    const box = $('.canvas-nodes');
    box.innerHTML = graph.nodes.map((n) => {
      const def = NODES[n.type];
      return `<div class="node cnode" data-node="${esc(n.id)}" style="left:${n.x}px;top:${n.y}px;width:${NODE_W}px">
        ${def.takes.length ? '<span class="port in" aria-hidden="true"></span>' : ''}
        ${def.makes ? '<span class="port out" data-port title="Drag to another box to join them"></span>' : ''}
        <header data-grip><b>${esc(def.label)}</b><span class="state"></span><button type="button" class="x" data-remove aria-label="Remove ${esc(def.label)}">×</button></header>
        ${settingsHtml(n)}
        <div class="cresults"></div>
      </div>`;
    }).join('') || '<p class="hint canvas-empty">An empty board. Add a Picture, or start from one of the boards above.</p>';
    const far = graph.nodes.reduce((m, n) => ({ x: Math.max(m.x, n.x + NODE_W + 160), y: Math.max(m.y, n.y + 320) }), { x: 0, y: 0 });
    $('.canvas-space').style.width = `${far.x}px`;
    $('.canvas-space').style.height = `${far.y}px`;
    renderLines();
    renderRun();
  }

  function renderLines(extra = null) {
    const svg = $('.canvas-lines');
    const at = new Map(graph.nodes.map((n) => [n.id, n]));
    svg.innerHTML = graph.edges.map((e) => {
      const a = at.get(e.from), b = at.get(e.to);
      if (!a || !b) return '';
      const d = edgePath(a, b);
      return `<g class="edge" data-from="${esc(e.from)}" data-to="${esc(e.to)}"><path class="hit" d="${d}"><title>Click to remove this line</title></path><path class="wire" d="${d}"/></g>`;
    }).join('') + (extra ? `<path class="wire draft" d="${extra}"/>` : '');
  }

  function renderRun() {
    const live = runningNode(run, graph, chain);
    for (const n of graph.nodes) {
      const card = el.querySelector(`[data-node="${CSS.escape(n.id)}"]`);
      if (!card) continue;
      const { state, html } = resultsHtml(n.id);
      const shown = n.id === live ? 'running' : state;
      card.className = `node cnode ${shown || ''}`;
      card.querySelector('.state').textContent = n.id === live
        ? `${chain.percent != null ? `${Math.round(chain.percent)}%` : 'Running'}`
        : state === 'error' && nodeState(run || graph.last_run, n.id).files.length ? 'Some stopped' : STATE_WORDS[state] || '';
      card.querySelector('.cresults').innerHTML = html + (n.id === live && chain.message ? `<small class="meta">${esc(chain.message)}</small>` : '');
      card.querySelectorAll('[data-kit-file]').forEach((img) => on.thumb?.(img, img.dataset.kitFile));
    }
    const going = run?.status === 'running';
    $('[data-run]').hidden = going;
    $('[data-stop]').hidden = !going;
  }

  function renderPicker() {
    $('.canvas-name').value = graph.name;
    $('[data-pick]').innerHTML = `<option value="">New canvas</option>${saved.map((g) => `<option value="${esc(g.id)}" ${g.id === graph.id ? 'selected' : ''}>${esc(g.name)}</option>`).join('')}`;
  }

  // ---------------------------------------------------------------- saving
  async function saveNow() {
    clearTimeout(saveTimer);
    saveTimer = null;
    const { last_run: _skip, ...body } = graph;   // the server keeps the last run itself
    const stored = await json(await fetchImpl('/api/graphs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }));
    const fresh = graph.id !== stored.id;
    graph = { ...graph, id: stored.id, updated: stored.updated };
    if (fresh) { saved = [stored, ...saved.filter((g) => g.id !== stored.id)]; renderPicker(); }
    return graph;
  }
  const saveSoon = () => { clearTimeout(saveTimer); saveTimer = setTimeout(() => saveNow().catch((e) => say(e.message, true)), 600); };
  const change = (next, { redraw = true } = {}) => { graph = next; if (redraw) renderNodes(); saveSoon(); };

  async function loadList(pickId = null) {
    try { saved = (await json(await fetchImpl('/api/graphs'))).graphs || []; } catch { saved = []; }
    const pick = saved.find((g) => g.id === pickId) || null;
    if (pick) graph = pick;
    renderPicker();
    renderNodes();
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
      renderRun();
      return;
    }
    renderRun();
    if (run.status === 'running') {
      say(`Running ${run.plan[run.stage] ? run.plan[run.stage].replace(/^Run \d+[^:]*: /, '') : ''}… You can close this; it keeps going.`);
      timer = setTimeout(() => follow(id), interval);
      return;
    }
    say(run.status === 'done' ? 'Done. Everything it made is in your Library.' : run.status === 'cancelled'
      ? 'Stopped. Everything finished before that is kept.' : `It stopped: ${run.error || 'see the boxes marked Stopped.'}`, run.status === 'error');
    graph = { ...graph, last_run: { status: run.status, results: run.results } };
    run = null;
    chain = null;
    renderRun();
    on.ran?.();
  }

  // ---------------------------------------------------------------- input
  let drag = null;
  el.addEventListener('pointerdown', (event) => {
    const card = event.target.closest('.cnode');
    if (!card) return;
    const id = card.dataset.node;
    const node = graph.nodes.find((n) => n.id === id);
    const space = $('.canvas-space').getBoundingClientRect();
    if (event.target.closest('[data-port]')) {
      drag = { kind: 'line', from: id, space };
    } else if (event.target.closest('[data-grip]') && !event.target.closest('button')) {
      drag = { kind: 'move', id, card, dx: event.clientX - node.x, dy: event.clientY - node.y, x: node.x, y: node.y };
    } else return;
    event.preventDefault();
    el.setPointerCapture?.(event.pointerId);
  });
  el.addEventListener('pointermove', (event) => {
    if (!drag) return;
    if (drag.kind === 'move') {
      drag.x = Math.max(0, event.clientX - drag.dx);
      drag.y = Math.max(0, event.clientY - drag.dy);
      drag.card.style.left = `${drag.x}px`;
      drag.card.style.top = `${drag.y}px`;
      graph.nodes.find((n) => n.id === drag.id).x = drag.x;   // the lines follow while dragging
      graph.nodes.find((n) => n.id === drag.id).y = drag.y;
      renderLines();
    } else {
      const a = graph.nodes.find((n) => n.id === drag.from);
      const tip = { x: event.clientX - drag.space.left - 0, y: event.clientY - drag.space.top - PORT_Y };
      renderLines(edgePath(a, tip));
    }
  });
  el.addEventListener('pointerup', (event) => {
    if (!drag) return;
    const was = drag;
    drag = null;
    if (was.kind === 'move') { change(moveNode(graph, was.id, was.x, was.y)); return; }
    const target = document.elementFromPoint(event.clientX, event.clientY)?.closest('.cnode');
    if (!target || target.dataset.node === was.from) { renderLines(); return; }
    const ok = canConnect(graph, was.from, target.dataset.node);
    if (ok !== true) { say(ok, true); renderLines(); return; }
    change(connect(graph, was.from, target.dataset.node));
    say('Joined. Press Check to see how it will run.');
  });

  el.addEventListener('click', async (event) => {
    const t = event.target.closest('button, .edge');
    if (!t) return;
    if (t.classList.contains('edge')) { change(removeEdge(graph, t.dataset.from, t.dataset.to)); return; }
    if (t.dataset.close !== undefined) { close(); return; }
    if (t.dataset.add) {
      // below everything already on the board, so a new box never lands on top of one
      const board = $('.canvas-board');
      const place = { x: board.scrollLeft + 40, y: graph.nodes.length ? Math.max(...graph.nodes.map((n) => n.y)) + 230 : 40 };
      change(addNode(graph, t.dataset.add, place.x, place.y, defaults[t.dataset.add] || {}));
      el.querySelector('.cnode:last-of-type')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    } else if (t.dataset.starter) {
      if (graph.nodes.length && !t.dataset.sure) { t.dataset.sure = '1'; say('This replaces the board. Click the same button again to go ahead.'); return; }
      delete t.dataset.sure;
      const next = starter(t.dataset.starter, { engines: engines.map((e) => e.id), move: moves[0]?.id });
      change({ ...next, id: graph.id, name: graph.nodes.length ? graph.name : next.name });
      renderPicker();
      say('Describe what you want in the Picture box, then press Run.');
    } else if (t.dataset.remove !== undefined) {
      change(removeNode(graph, t.closest('.cnode').dataset.node));
    } else if (t.dataset.file) {
      on.open?.(t.dataset.file);
    } else if (t.dataset.check !== undefined) {
      try {
        const { plan } = await json(await fetchImpl('/api/graphs/check', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(graph) }));
        say(plan.join(' · '));
      } catch (error) { say(error.message, true); }
    } else if (t.dataset.run !== undefined) {
      t.disabled = true;
      try {
        await saveNow();
        const started = await json(await fetchImpl(`/api/graphs/${graph.id}/run`, { method: 'POST' }));
        say(started.plan.join(' · '));
        follow(started.id);
      } catch (error) { say(error.message, true); } finally { t.disabled = false; }
    } else if (t.dataset.stop !== undefined && run) {
      fetchImpl(`/api/graph-runs/${run.id}/cancel`, { method: 'POST' }).catch(() => {});
      say('Stopping after the step that is running…');
    } else if (t.dataset.drop !== undefined) {
      if (!graph.id) { graph = newGraph(); renderPicker(); renderNodes(); return; }
      if (!t.dataset.sure) { t.dataset.sure = '1'; t.textContent = 'Delete this canvas? Click again'; return; }
      await fetchImpl(`/api/graphs/${graph.id}/delete`, { method: 'POST' }).catch(() => {});
      graph = newGraph();
      await loadList();
    }
  });

  el.addEventListener('input', (event) => {
    const field = event.target.closest('[data-set]');
    if (field) change(setSetting(graph, field.closest('.cnode').dataset.node, field.dataset.set, field.value.trim()), { redraw: false });
    if (event.target.matches('.canvas-name')) change({ ...graph, name: event.target.value.trim() || 'Untitled canvas' }, { redraw: false });
  });
  el.addEventListener('change', async (event) => {
    if (!event.target.matches('[data-pick]')) return;
    if (saveTimer) await saveNow().catch(() => {});
    const id = event.target.value;
    graph = id ? saved.find((g) => g.id === id) : newGraph();
    renderPicker();
    renderNodes();
  });
  el.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !event.target.matches('textarea, input')) close();
  });

  function close() {
    clearTimeout(timer);
    if (saveTimer) saveNow().catch(() => {});
    el.remove();
  }
  el.onSheetClose = () => { clearTimeout(timer); if (saveTimer) saveNow().catch(() => {}); };

  // a run that is already going (the tab was reopened) is picked back up
  (async () => {
    await loadList();
    try {
      const { running } = await json(await fetchImpl('/api/graph-runs'));
      if (running) {
        graph = saved.find((g) => g.id === running.graph) || graph;
        renderPicker();
        renderNodes();
        follow(running.id);
      } else if (saved[0]) {
        graph = saved[0];
        renderPicker();
        renderNodes();
      }
    } catch { /* an older server: nothing running */ }
  })();
  return { element: el };
}
