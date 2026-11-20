// The Voice sheet: type a line on the left, hear its takes on the right, keep what you like.
// Several sentences are several lines, each with its own takes and "Make this line again";
// "Save as one" joins the take picked for each. Logic lives in voice.js; this file only
// draws it and talks to the server. With an engine missing it says so and links to Setup.
// It never fetches anything itself, and it has no way to send a recording of a person.

import { esc, PLAY, STOP, SPEAKER, getJson, post, createPlayer, waveHtml } from './sound-pane.js';
import { keptMeta, kindButtons } from './sound.js';
import { engineOf, engineButtons, voiceNotInstalledText, voiceOf, languageOf, voiceRequest, voiceProblem, makeWords, speedText,
  newBlocks, addTakes, chooseTake, blockCards, jobsOnPage, joinRequest, joinProblem, voiceProgress } from './voice.js';

function takeHtml(card, line, several) {
  return `<li class="try-card ready${card.kept ? ' kept' : ''}${card.chosen ? ' chosen' : ''}">
      <div class="try-top">
        <button type="button" class="play" data-play="${esc(card.url)}" aria-label="${card.playing ? 'Stop' : 'Play'} ${esc(card.title)}">${card.playing ? STOP : PLAY}</button>
        <b>${esc(card.title)}</b><span class="meta">${esc(card.words)}</span>
      </div>
      ${waveHtml(card.bars, 48)}
      <div class="row">
        ${several ? `<button type="button" class="ghost small" data-choose="${esc(card.key)}" data-line="${line}" aria-pressed="${card.chosen}"
          data-tip="Use this take when the lines are saved as one.">${card.chosen ? 'Picked' : 'Pick this one'}</button>` : ''}
        <button type="button" class="ghost small keep" data-keep="${card.number}" data-job="${esc(card.job_id)}" ${card.kept ? 'disabled' : ''}
          data-tip="${card.kept ? 'It is in your Library.' : 'Save this line on its own to your Library.'}">${card.kept ? 'Kept' : 'Keep'}</button>
      </div>
    </li>`;
}

function keptHtml(sounds, base, playing) {
  if (!sounds.length) return '<p class="hint">Lines you keep show here.</p>';
  return sounds.slice(0, 8).map((sound) => {
    const source = base + sound.file;
    return `<button type="button" class="kept-sound" data-play="${esc(source)}" data-tip="${esc(sound.prompt || sound.name)}" aria-label="${playing === source ? 'Stop' : 'Play'} ${esc(sound.name)}">
        <span class="icon">${playing === source ? STOP : SPEAKER}</span><b>${esc(sound.name)}</b><small class="meta">${esc(keptMeta(sound))}</small></button>`;
  }).join('');
}

/**
 * Open the sheet over the viewer. `on.kept()` tells the page a sound joined the Library;
 * `on.back()` asks it to open the Sound sheet (another kind was picked).
 * Returns { element } so the page can tell whether it is still open.
 */
export function openVoicePane({ stage, on = {} }) {
  stage.querySelector('.sheet')?.onSheetClose?.();
  stage.querySelector('.sheet')?.remove();
  const el = document.createElement('div');
  el.className = 'sheet sound-sheet voice-sheet';
  el.innerHTML = '<div class="sound-card"><p class="hint">Loading…</p></div>';
  stage.appendChild(el);

  let defaults = null, kinds = [], kept = [], base = '/output/', job = null, lineMap = {}, blocks = [], note = '', timer = null, open = true;
  const form = { engine: null, voice: null, direction: '', ownWords: '', language: null, speed: 1, text: '' };
  const player = createPlayer(() => { if (open) drawResults(); });
  const running = () => job && !['done', 'error', 'cancelled'].includes(job.status);
  const engine = () => engineOf(defaults, form.engine);

  const formHtml = () => {
    const chosen = engine();
    const voice = voiceOf(chosen, form.voice);
    const blocked = !chosen?.installed;
    const words = makeWords(chosen);
    return `<section class="sound-form" aria-label="Say a line">
      <div class="sheet-top"><h2>Say a <span>line</span></h2><button type="button" class="ghost" data-close>Close</button></div>
      ${kinds.length ? `<div class="field"><span>What kind?</span>
        <div class="kinds" role="group" aria-label="What kind?">${kinds.map((k) => `<button type="button" class="kind" data-kind="${esc(k.id)}" aria-pressed="${k.id === 'voice'}" ${k.disabled ? 'disabled' : ''}>${esc(k.label)}</button>`).join('')}</div></div>` : ''}
      <div class="field"><span>How should it sound?</span>
        <div class="kinds" role="group" aria-label="How should it sound?">${engineButtons(defaults, form.engine).map((e) => `<button type="button" class="kind" data-engine="${esc(e.id)}" aria-pressed="${e.pressed}">${esc(e.label)}</button>`).join('')}</div>
        <small class="hint">${esc(chosen?.hint || '')}</small></div>
      ${blocked ? `<div class="sound-setup" role="status"><p>${esc(voiceNotInstalledText(chosen))}</p><a class="primary" href="./setup.html">Open Setup</a></div>` : ''}
      <label class="field"><span>Voice</span>
        <select data-voice>${(chosen?.voices || []).map((v) => `<option value="${esc(v.id)}" ${v.id === voice?.id ? 'selected' : ''}>${esc(v.label)}</option>`).join('')}</select>
        <small class="hint" data-voice-hint>${esc(voice?.hint || '')}</small></label>
      ${chosen?.directions ? `<div class="field"><span>How it's said</span>
        <div class="quick" role="group" aria-label="How it's said">${(defaults.directions || []).map((d) => `<button type="button" class="qs${form.direction === d.id ? ' on' : ''}" data-direction="${esc(d.id)}" aria-pressed="${form.direction === d.id}">${esc(d.label)}</button>`).join('')}</div>
        <input type="text" data-own maxlength="${defaults.limits.max_direction}" value="${esc(form.ownWords)}" placeholder="Or in your own words: like a tired old wizard">
        <small class="hint">Leave it empty for a plain reading. A shout comes out louder than a whisper.</small></div>` : ''}
      ${chosen?.languages?.length ? `<label class="field"><span>Language</span>
        <select data-language>${chosen.languages.map((l) => `<option value="${esc(l.id)}" ${l.id === (form.language || languageOf(chosen, form.voice)) ? 'selected' : ''}>${esc(l.label)}</option>`).join('')}</select>
        <small class="hint">The language the line is written in. Each voice sounds most at home in its own.</small></label>` : ''}
      ${chosen?.speed ? `<label class="field"><span>Speed <output data-speed-text>${esc(speedText(form.speed))}</output></span>
        <input type="range" data-speed min="${chosen.speed.min}" max="${chosen.speed.max}" step="0.05" value="${form.speed}"></label>` : ''}
      <label class="field"><span>What to say</span>
        <textarea data-text rows="4" maxlength="${defaults.limits.max_text}" placeholder="Halt! Who goes there?">${esc(form.text)}</textarea>
        <small class="hint">Several sentences are made one by one, so you can make any of them again.</small></label>
      <div class="sound-go">
        <button type="button" class="primary" data-make ${blocked ? 'disabled' : ''}>${esc(words.button)}</button>
        <small class="hint">${esc(words.hint)}</small>
        <p class="sound-note" data-note role="status" aria-live="polite"></p>
      </div>
    </section>`;
  };

  const resultsHtml = () => {
    const several = blocks.length > 1;
    const lines = blocks.map((block, index) => `<section class="voice-line" aria-label="Line ${index + 1}">
        <div class="sound-head"><h3>${several ? `<span class="meta">Line ${index + 1}</span> ` : ''}${esc(block.text)}</h3>
          <button type="button" class="ghost small" data-again="${index}" ${running() ? 'disabled' : ''}
            data-tip="New takes of just this line. The takes you already have stay.">Make this line again</button></div>
        <ul class="sound-tries">${blockCards(block, player.playing, several).map((card) => takeHtml(card, index, several)).join('')
          || `<li class="try-card ${block.making ? 'making' : 'missing'}"><div class="try-top"><b>${block.making ? 'Being made…' : 'Nothing came out'}</b></div>${waveHtml([], 48)}</li>`}</ul>
      </section>`).join('');
    return `<div class="sound-head"><h3>${blocks.length ? `${blocks.length} line${blocks.length === 1 ? '' : 's'}` : 'Your lines'}</h3><span class="meta">Made on this computer · free to use in games</span></div>
      <p class="hint" data-progress role="status" aria-live="polite">${esc(voiceProgress(job, blocks.length) || 'Type a line and press Make. Your takes show here.')}
        ${running() ? '<button type="button" class="ghost small" data-stop>Stop</button>' : ''}</p>
      ${lines}
      ${several ? `<div class="voice-join"><button type="button" class="primary" data-join ${running() ? 'disabled' : ''}>Save as one</button>
        <small class="hint">Joins the take picked for each line, in order, with a short gap. One sound in your Library.</small></div>` : ''}
      <div class="kept"><h3>In your Library</h3><div class="kept-strip">${keptHtml(kept, base, player.playing)}</div></div>`;
  };

  function drawResults() {
    const target = el.querySelector('.sound-results');
    if (target) target.innerHTML = resultsHtml();
    const make = el.querySelector('[data-make]');
    if (make) make.disabled = !engine()?.installed || !!running();
  }

  function draw() {
    el.innerHTML = `<div class="sound-card">${formHtml()}<section class="sound-results" aria-label="Lines"></section></div>`;
    drawResults();
    say(note);
  }

  function say(text) {
    note = text || '';
    const target = el.querySelector('[data-note]');
    if (target) target.textContent = note;
  }

  async function loadKept() {
    const answer = await getJson('/api/sound/kept');
    kept = answer.sounds || [];
    base = answer.base || base;
  }

  async function poll() {
    if (!open || !job) return;
    try {
      job = await getJson(`/api/sound/${job.job_id}/status`);
    } catch (error) {
      say(error.message);
      return;
    }
    blocks = addTakes(blocks, job, lineMap);
    drawResults();
    if (running()) timer = setTimeout(poll, 700);
  }

  /** Make the whole text (`again` null) or one line again (`again` is its place on the page). */
  async function make(again = null) {
    const chosen = engine();
    const request = again == null ? voiceRequest(form, chosen)
      : voiceRequest(form, chosen, { lines: [blocks[again].text], using: jobsOnPage(blocks) });
    const problem = voiceProblem(request, chosen, defaults.limits);
    if (problem) { say(problem); return; }
    say('');
    player.stop();
    try {
      const started = await post('/api/voice', request);
      job = { job_id: started.job_id, status: 'queued', count: started.count, tries: [] };
      if (again == null) {
        blocks = newBlocks(started.lines);
        lineMap = Object.fromEntries(started.lines.map((_, index) => [index + 1, index]));
      } else {
        lineMap = { 1: again };
        blocks = blocks.map((block, index) => (index === again ? { ...block, making: true } : block));
      }
      drawResults();
      poll();
    } catch (error) {
      say(error.message);
    }
  }

  const close = () => {
    open = false;
    clearTimeout(timer);
    player.dispose();
    el.remove();
  };
  el.onSheetClose = () => { open = false; clearTimeout(timer); player.dispose(); };

  el.addEventListener('click', async (event) => {
    const t = event.target.closest('button');
    if (!t || !defaults) return;
    try {
      if (t.dataset.close !== undefined) close();
      else if (t.dataset.make !== undefined) await make();
      else if (t.dataset.again !== undefined) await make(Number(t.dataset.again));
      else if (t.dataset.stop !== undefined) await post(`/api/sound/${job.job_id}/cancel`);
      else if (t.dataset.kind) { if (t.dataset.kind !== 'voice') on.back?.(); }
      else if (t.dataset.engine) { form.engine = t.dataset.engine; form.voice = null; form.language = null; draw(); }
      else if (t.dataset.direction) { form.direction = form.direction === t.dataset.direction ? '' : t.dataset.direction; draw(); }
      else if (t.dataset.choose) { blocks = chooseTake(blocks, Number(t.dataset.line), t.dataset.choose); drawResults(); }
      else if (t.dataset.play) await player.toggle(t.dataset.play);
      else if (t.dataset.keep) {
        t.disabled = true;
        // named after its own line, not the whole text it came with
        const line = blocks.find((block) => block.takes.some((take) => take.job_id === t.dataset.job && take.number === Number(t.dataset.keep)));
        const made = await post(`/api/sound/${t.dataset.job}/keep`, { try: Number(t.dataset.keep), name: line?.text || '' });
        blocks = blocks.map((block) => ({ ...block, takes: block.takes.map((take) => (
          take.job_id === t.dataset.job && take.number === Number(t.dataset.keep) ? { ...take, kept: made.file } : take)) }));
        await loadKept();
        drawResults();
        on.kept?.();
      } else if (t.dataset.join !== undefined) {
        const problem = joinProblem(blocks);
        if (problem) { say(problem); return; }
        t.disabled = true;
        const saved = await post('/api/voice/join', joinRequest(blocks, { gapMs: defaults.join?.gap_ms }));
        await loadKept();
        drawResults();
        say(`Saved as one sound, ${saved.seconds} s. It is in your Library.`);
        on.kept?.();
      }
    } catch (error) {
      say(error.message);
    }
  });
  el.addEventListener('input', (event) => {
    if (event.target.matches('[data-text]')) form.text = event.target.value;
    if (event.target.matches('[data-own]')) form.ownWords = event.target.value;
    if (event.target.matches('[data-speed]')) {
      form.speed = Number(event.target.value);
      el.querySelector('[data-speed-text]').textContent = speedText(form.speed);
    }
  });
  el.addEventListener('change', (event) => {
    if (event.target.matches('[data-voice]')) {
      form.voice = event.target.value;
      form.language = null;                      // the new voice's own language, until one is picked
      draw();
    }
    if (event.target.matches('[data-language]')) form.language = event.target.value;
  });
  el.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });

  (async () => {
    try {
      defaults = await getJson('/api/voice/defaults');
      // the engine that is here, if only one of them is
      form.engine = (defaults.engines.find((e) => e.id === defaults.default_engine && e.installed)
        || defaults.engines.find((e) => e.installed) || engineOf(defaults, defaults.default_engine))?.id;
      kinds = kindButtons(await getJson('/api/sound/defaults').catch(() => null), 'voice');
      await loadKept();
      if (open) { draw(); el.querySelector('[data-text]')?.focus(); }
    } catch (error) {
      el.innerHTML = `<div class="sound-card"><div class="sheet-top"><h2>Say a <span>line</span></h2><button type="button" class="ghost" data-close>Close</button></div><p class="hint">${esc(error.message)}</p></div>`;
      el.querySelector('[data-close]').addEventListener('click', close);
    }
  })();

  return { element: el };
}
