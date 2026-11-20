// "Make variations": from one kept sound, a handful of takes that differ a little, so a
// footstep heard twenty times does not sound copied. No model runs; it takes seconds.
// Logic lives in sound.js; this file only draws it and talks to the server.

import { howDifferentStops, variationsRequest, variationsProblem, sameSet, variationRows, nextInOrder, saveSetHint } from './sound.js';
import { esc, getJson, post, createPlayer, waveHtml, PLAY, STOP } from './sound-pane.js';

const SHUFFLE = '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 3h5v5"/><path d="M4 20 21 3"/><path d="M21 16v5h-5"/><path d="m15 15 6 6"/><path d="M4 4l5 5"/></svg>';

/**
 * Open the sheet over the viewer for the kept sound `sound` ({ id, name }).
 * `on.saved()` tells the page a set joined the Library; `on.back()` runs when it closes.
 */
export function openVariationsPane({ stage, sound, on = {} }) {
  stage.querySelector('.sheet')?.onSheetClose?.();
  stage.querySelector('.sheet')?.remove();
  const el = document.createElement('div');
  el.className = 'sheet sound-sheet variations-sheet';
  el.innerHTML = '<div class="sound-card"><p class="hint">Loading…</p></div>';
  stage.appendChild(el);

  let options = null, preview = null, saved = false, busy = false, note = '', open = true, played = 0;
  const form = { count: 6, howDifferent: null, change: new Set() };
  const player = createPlayer(() => { if (open) drawResults(); });

  const formHtml = () => {
    const stops = howDifferentStops(options, form.howDifferent);
    return `<section class="sound-form" aria-label="Make variations">
      <div class="sheet-top"><button type="button" class="link" data-close>← ${esc(sound.name)}</button><button type="button" class="ghost" data-close>Close</button></div>
      <h2>Make <span>variations</span></h2>
      <p class="hint">Small changes so repeats don't sound copied. Good for footsteps, hits and pickups. Nothing is made from scratch, so it takes seconds.</p>
      <label class="field"><span>How many <output data-count>${form.count}</output></span>
        <input type="range" data-count-in min="${options.min}" max="${options.max}" step="1" value="${form.count}">
        <small class="hint">The first one is the sound as it is.</small></label>
      <label class="field"><span>How different <output data-how>${esc(stops.label)}</output></span>
        <input type="range" data-how-in min="0" max="${stops.max}" step="1" value="${stops.at}" aria-valuetext="${esc(stops.label)}">
        <small class="hint" data-how-hint>${esc(stops.hint)}</small></label>
      <div class="field"><span>What changes</span>
        ${options.change.map((item) => `<label class="check"><input type="checkbox" data-change="${esc(item.id)}" ${form.change.has(item.id) ? 'checked' : ''}><span>${esc(item.label)} <span class="hint">${esc(item.hint)}</span></span></label>`).join('')}</div>
      <div class="sound-go">
        <button type="button" class="primary" data-make>Make ${form.count} variations</button>
        <p class="sound-note" data-note role="status" aria-live="polite"></p>
      </div>
    </section>`;
  };

  const resultsHtml = () => {
    if (!preview) return '<div class="sound-head"><h3>Variations</h3></div><p class="hint">Press Make and your variations show here. Nothing is saved until you say so.</p>';
    const rows = variationRows(preview, player.playing);
    const going = player.playingOn;
    return `<div class="sound-head"><h3>${esc(preview.title)} · ${preview.count} variations</h3>
        <button type="button" class="ghost game" data-game aria-pressed="${going}">${going ? STOP : SHUFFLE} ${going ? 'Stop' : 'Play like a game'}</button></div>
      <p class="hint">Plays them in random order, never the same one twice running, the way your game engine will.</p>
      <ul class="var-rows">${rows.map((row) => `<li class="var-row${row.playing ? ' playing' : ''}">
          <button type="button" class="play" data-play="${esc(row.url)}" aria-label="${row.playing ? 'Stop' : 'Play'} ${esc(row.file)}">${row.playing ? STOP : PLAY}</button>
          <code><span>${esc(row.stem)}</span>${esc(row.tail)}</code>${waveHtml(row.bars, 44)}<span class="meta">${esc(row.note)}</span></li>`).join('')}</ul>
      <div class="var-save"><div><b>Save the set</b><p class="hint">${esc(saveSetHint(preview.count))}</p></div>
        <button type="button" class="primary" data-save ${saved || busy ? 'disabled' : ''}>${saved ? 'Saved' : `Save ${preview.count} sounds`}</button></div>`;
  };

  function drawResults() {
    const target = el.querySelector('.sound-results');
    if (target) target.innerHTML = resultsHtml();
  }

  function draw() {
    el.innerHTML = `<div class="sound-card">${formHtml()}<section class="sound-results" aria-label="Variations"></section></div>`;
    drawResults();
    say(note);
  }

  function say(text) {
    note = text || '';
    const target = el.querySelector('[data-note]');
    if (target) target.textContent = note;
  }

  async function make() {
    const request = variationsRequest({ ...form, howDifferent: howDifferentStops(options, form.howDifferent).id });
    const problem = variationsProblem(request);
    if (problem) { say(problem); return; }
    say('');
    player.stop();
    preview = await post(`/api/sounds/${encodeURIComponent(sound.id)}/variations`, request);
    saved = false;
    played = 0;
    drawResults();
  }

  async function save() {
    busy = true;
    drawResults();
    try {
      await post(`/api/sounds/${encodeURIComponent(sound.id)}/set`, sameSet(preview));
      saved = true;
      say('Saved. Find the set in your Library, under Sounds.');
      on.saved?.();
    } finally {
      busy = false;
      drawResults();
    }
  }

  function playLikeAGame() {
    if (player.playingOn) { player.stop(); return Promise.resolve(); }
    return player.playEach(() => {
      const next = nextInOrder(preview.play_order, played);
      played = next.played;
      return preview.variations[next.index]?.url || null;
    });
  }

  const shut = () => { open = false; player.dispose(); };
  const close = () => { shut(); el.remove(); on.back?.(); };
  el.onSheetClose = shut;

  el.addEventListener('click', async (event) => {
    const t = event.target.closest('button');
    if (!t || !options) return;
    try {
      if (t.dataset.close !== undefined) close();
      else if (t.dataset.make !== undefined) await make();
      else if (t.dataset.save !== undefined) await save();
      else if (t.dataset.game !== undefined) await playLikeAGame();
      else if (t.dataset.play) await player.toggle(t.dataset.play);
    } catch (error) {
      say(error.message);
    }
  });
  el.addEventListener('input', (event) => {
    const t = event.target;
    if (t.matches('[data-count-in]')) {
      form.count = Number(t.value);
      el.querySelector('[data-count]').textContent = form.count;
      el.querySelector('[data-make]').textContent = `Make ${form.count} variations`;
    } else if (t.matches('[data-how-in]')) {
      const stops = howDifferentStops(options, options.how_different[Number(t.value)]?.id);
      form.howDifferent = stops.id;
      el.querySelector('[data-how]').textContent = stops.label;
      el.querySelector('[data-how-hint]').textContent = stops.hint;
      t.setAttribute('aria-valuetext', stops.label);
    } else if (t.matches('[data-change]')) {
      if (t.checked) form.change.add(t.dataset.change); else form.change.delete(t.dataset.change);
    }
  });
  el.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });

  (async () => {
    try {
      options = (await getJson('/api/sound/defaults')).variations;
      form.count = options.default;
      form.howDifferent = options.default_how_different;
      form.change = new Set(options.change.map((item) => item.id));
      if (open) { draw(); el.querySelector('[data-make]')?.focus(); }
    } catch (error) {
      el.innerHTML = `<div class="sound-card"><div class="sheet-top"><h2>Make <span>variations</span></h2><button type="button" class="ghost" data-close>Close</button></div><p class="hint">${esc(error.message)}</p></div>`;
      el.querySelector('[data-close]').addEventListener('click', close);
    }
  })();

  return { element: el };
}
