// The Sound sheet: describe a sound on the left, four tries on the right, keep the one you
// like. Logic lives in sound.js; this file only draws it and talks to the server.
// With the engine missing it says so and links to Setup. It never fetches anything itself.

import { lengthText, soundRequest, requestProblem, notInstalledText, kindButtons, tryCards, progressLine, barHeights, keptMeta, moreLikeThis, soundIdOf, kindLimits, formWords, isLoop, joinWindow } from './sound.js';

export const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const PLAY = '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16"><path d="M4 2.5v11l9-5.5z" fill="currentColor"/></svg>';
export const STOP = '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16"><rect x="3.5" y="3.5" width="9" height="9" rx="1.5" fill="currentColor"/></svg>';
export const SPEAKER = '<svg aria-hidden="true" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16.5 8.5a5 5 0 0 1 0 7"/></svg>';

export async function getJson(path) {
  const response = await fetch(path);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || 'That did not work.');
  return body;
}

export async function post(path, payload = {}) {
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || 'That did not work.');
  return body;
}

const GAP_MS = 180;     // the breath between sounds when a set plays on: about a walking step

/** Plays one sound at a time. The file is fetched whole first, so it plays in every browser
 *  (some refuse to stream audio from a server that cannot send it in pieces).
 *  `playEach(next)` keeps going: when a sound ends it asks `next()` for another, until stopped.
 *  `toggle(source, { loop: true })` plays it round and round until stopped, joined sample to
 *  sample (an audio element leaves a small gap, which would sound like a fault in the loop).
 *  `{ join: true }` plays only its last two seconds running into its first two, then stops. */
export function createPlayer(onChange = () => {}) {
  let audio = null, playing = null, follow = null, wait = null, mode = null, round = null, context = null, asked = 0;
  const urls = new Map(), decoded = new Map();
  const stop = () => {
    clearTimeout(wait);
    asked += 1;
    audio?.pause();
    audio = null;
    if (round) { round.onended = null; try { round.stop(); } catch { /* never started */ } round.disconnect(); }
    round = null;
    playing = null;
    mode = null;
    follow = null;
    onChange(null);
  };
  const startRound = async (source, join) => {
    const mine = asked;
    if (!context) {
      const Make = window.AudioContext || window.webkitAudioContext;
      try { context = new Make({ sampleRate: 48000 }); } catch { context = new Make(); }   // our files' own rate: nothing is re-made
    }
    if (!decoded.has(source)) {
      const response = await fetch(source);
      if (!response.ok) throw new Error('That sound could not be played.');
      decoded.set(source, await context.decodeAudioData(await response.arrayBuffer()));
    }
    if (asked !== mine) return;           // stopped, or something else started, while it was fetched
    await context.resume();
    const node = context.createBufferSource();
    node.buffer = decoded.get(source);
    node.loop = true;
    node.connect(context.destination);
    round = node;
    playing = source;
    mode = join ? 'join' : 'loop';
    if (join) {
      const part = joinWindow(node.buffer.duration);
      node.onended = () => { if (round === node) stop(); };
      node.start(0, part.offset, part.duration);
    } else node.start();
    onChange(source);
  };
  const start = async (source) => {
    const mine = follow;
    if (!urls.has(source)) {
      const response = await fetch(source);
      if (!response.ok) throw new Error('That sound could not be played.');
      urls.set(source, URL.createObjectURL(await response.blob()));
    }
    if (follow !== mine) return;          // stopped, or something else started, while it was fetched
    const made = new Audio(urls.get(source));
    audio = made;
    playing = source;
    made.addEventListener('ended', () => {
      if (audio !== made) return;
      if (!follow) { stop(); return; }
      const going = follow;
      wait = setTimeout(() => {
        const next = follow === going ? going() : null;
        if (next) start(next).catch(stop); else if (follow === going) stop();
      }, GAP_MS);
    });
    onChange(source);
    await made.play();
  };
  return {
    get playing() { return playing; },
    get playingOn() { return !!follow; },
    /** 'loop' while a sound plays round, 'join' while its join is heard, else null. */
    get mode() { return mode; },
    stop,
    async toggle(source, { loop = false, join = false } = {}) {
      const wanted = join ? 'join' : loop ? 'loop' : null;
      if (playing === source && !follow && mode === wanted) { stop(); return; }
      stop();
      if (wanted) await startRound(source, join); else await start(source);
    },
    async playEach(next) {
      stop();
      follow = next;
      const first = next();
      if (first) await start(first); else stop();
    },
    dispose() {
      stop();
      urls.forEach((made) => URL.revokeObjectURL(made));
      urls.clear();
      decoded.clear();
      context?.close?.().catch(() => {});
      context = null;
    },
  };
}

export function waveHtml(bars, tallest = 64) {
  const heights = barHeights(bars, tallest);
  return `<div class="wave" aria-hidden="true">${(heights.length ? heights : Array(60).fill(3)).map((h) => `<i style="height:${h}px"></i>`).join('')}</div>`;
}

function tryHtml(card, player, busy = false, loop = false) {
  const ready = card.state === 'ready';
  const words = { making: 'Being made…', waiting: 'Waiting', missing: 'Came out silent', empty: '' }[card.state] ?? lengthText(card.seconds);
  const here = ready && player.playing === card.url;
  const on = here && player.mode !== 'join', joining = here && player.mode === 'join';
  return `<li class="try-card ${card.state}${card.kept ? ' kept' : ''}">
      <div class="try-top">
        <button type="button" class="play" data-play="${esc(card.url || '')}" ${loop ? 'data-loop data-tip="Plays round and round until you stop it."' : ''} aria-label="${on ? 'Stop' : 'Play'} try ${card.number}" ${ready ? '' : 'disabled'}>${on ? STOP : PLAY}</button>
        <b>Try ${card.number}</b><span class="meta">${esc(on && loop ? 'Playing round and round' : words)}</span>
      </div>
      ${waveHtml(card.bars)}
      <div class="row"><button type="button" class="ghost small keep" data-keep="${card.number}" ${ready && !card.kept ? '' : 'disabled'}
        data-tip="${card.kept ? 'It is in your Library.' : 'Save this one to your Library. The others are cleared when you make more.'}">${card.kept ? 'Kept' : 'Keep'}</button>
        ${loop && ready ? `<button type="button" class="ghost small more" data-join="${esc(card.url)}" aria-pressed="${joining}" data-tip="Plays the end running into the start, two seconds of each, so you can check there is no click.">${joining ? 'Stop' : 'Hear the join'}</button>` : ''}
        ${!loop && soundIdOf(card.kept) ? `<button type="button" class="ghost small keep" data-variations="${esc(soundIdOf(card.kept))}" data-tip="Small changes of this sound, so repeats don't sound copied. Takes seconds.">Make variations</button>` : ''}
        ${ready ? `<button type="button" class="ghost small more" data-more ${busy ? 'disabled' : ''} data-tip="New tries from the same description. Tries you have not kept are cleared.">Make more like this</button>` : ''}</div>
    </li>`;
}

function keptHtml(sounds, base, playing) {
  if (!sounds.length) return '<p class="hint">Sounds you keep show here.</p>';
  return sounds.slice(0, 8).map((sound) => {
    const source = base + sound.file;
    return `<button type="button" class="kept-sound" data-play="${esc(source)}" ${isLoop(sound) ? 'data-loop' : ''} data-tip="${esc(sound.prompt || sound.name)}" aria-label="${playing === source ? 'Stop' : 'Play'} ${esc(sound.name)}">
        <span class="icon">${playing === source ? STOP : SPEAKER}</span><b>${esc(sound.name)}</b><small class="meta">${esc(keptMeta(sound))}</small></button>`;
  }).join('');
}

/**
 * Open the sheet over the viewer. `on.kept()` tells the page a sound joined the Library;
 * `on.variations({ id, name })` asks it to open "Make variations" for a kept sound;
 * `on.layers({ description })` asks it to open Layers, split from what is described.
 * `on.voice()` asks it to open the Voice sheet (the Voice kind was picked).
 * Returns { element } so the page can tell whether it is still open.
 */
export function openSoundPane({ stage, on = {} }) {
  stage.querySelector('.sheet')?.onSheetClose?.();
  stage.querySelector('.sheet')?.remove();
  const el = document.createElement('div');
  el.className = 'sheet sound-sheet';
  el.innerHTML = '<div class="sound-card"><p class="hint">Loading…</p></div>';
  stage.appendChild(el);

  let defaults = null, kept = [], base = '/output/', job = null, note = '', timer = null, open = true;
  const form = { prompt: '', seconds: 1.5, leaveOut: new Set(), kind: 'effect' };
  const player = createPlayer(() => { if (open) drawResults(); });
  const running = () => job && !['done', 'error', 'cancelled'].includes(job.status);

  const formHtml = () => {
    const limits = defaults.limits;
    const lengths = kindLimits(defaults, form.kind), words = formWords(form.kind);
    const kinds = kindButtons(defaults, form.kind);
    const chosen = kinds.find((k) => k.pressed);
    const blocked = !defaults.installed;
    return `<section class="sound-form" aria-label="Make a sound">
      <div class="sheet-top"><h2>Make a <span>sound</span></h2><button type="button" class="ghost" data-close>Close</button></div>
      ${blocked ? `<div class="sound-setup" role="status"><p>${esc(notInstalledText(defaults))}</p><a class="primary" href="./setup.html">Open Setup</a></div>` : ''}
      <div class="field"><span>What kind?</span>
        <div class="kinds" role="group" aria-label="What kind?">${kinds.map((k) => `<button type="button" class="kind" data-kind="${esc(k.id)}" aria-pressed="${k.pressed}" ${k.disabled ? 'disabled' : ''}>${esc(k.label)}</button>`).join('')}</div>
        <small class="hint">${esc(chosen?.hint || '')}</small></div>
      <label class="field"><span>Describe it</span>
        <textarea data-prompt rows="3" maxlength="${limits.max_prompt}" placeholder="${esc(words.placeholder)}">${esc(form.prompt)}</textarea>
        <small class="hint">${esc(words.describe)}</small></label>
      ${defaults.leave_out?.length ? `<div class="field"><span>Leave out</span>
        <div class="quick" role="group" aria-label="Leave out">${defaults.leave_out.map((word) => `<button type="button" class="qs${form.leaveOut.has(word) ? ' on' : ''}" data-leave="${esc(word)}" aria-pressed="${form.leaveOut.has(word)}">${esc(word)}</button>`).join('')}</div></div>` : ''}
      <label class="field"><span>Length <output data-length>${esc(lengthText(form.seconds))}</output></span>
        <input type="range" data-seconds min="${lengths.min}" max="${lengths.max}" step="0.5" value="${form.seconds}">
        <small class="hint">${esc(words.length)}</small></label>
      <div class="sound-go">
        <button type="button" class="primary" data-make ${blocked ? 'disabled' : ''}>Make ${limits.tries}</button>
        <small class="hint">${limits.tries} tries, ${esc(words.make)}</small>
        ${form.kind === 'loop' ? '' : '<button type="button" class="ghost" data-layers data-tip="For a sound made of two or three things, like a whoosh then a thud. Each part is made on its own, then stacked.">Build it from layers</button>'}
        <p class="sound-note" data-note role="status" aria-live="polite"></p>
      </div>
    </section>`;
  };

  const resultsHtml = () => {
    const count = defaults.limits.tries;
    return `<div class="sound-head"><h3>${job?.count || count} tries</h3><span class="meta">Made on this computer · free to use in games</span></div>
      <p class="hint" data-progress role="status" aria-live="polite">${esc(progressLine(job) || formWords(form.kind).empty)}
        ${running() ? '<button type="button" class="ghost small" data-stop>Stop</button>' : ''}</p>
      <ul class="sound-tries">${tryCards(job, count).map((card) => tryHtml(card, player, !!running(), isLoop(job))).join('')}</ul>
      <div class="kept"><h3>In your Library</h3><div class="kept-strip">${keptHtml(kept, base, player.playing)}</div></div>`;
  };

  function drawResults() {
    const target = el.querySelector('.sound-results');
    if (target) target.innerHTML = resultsHtml();
    const make = el.querySelector('[data-make]');
    if (make) make.disabled = !defaults.installed || !!running();
  }

  function draw() {
    el.innerHTML = `<div class="sound-card">${formHtml()}<section class="sound-results" aria-label="Tries"></section></div>`;
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
    drawResults();
    if (running()) timer = setTimeout(poll, 1000);
  }

  async function make(again = null) {
    const request = again || soundRequest({ ...form, count: defaults.limits.tries });
    const problem = requestProblem(request, defaults);
    if (problem) { say(problem); return; }
    say('');
    player.stop();
    try {
      const started = await post('/api/sound', request);
      job = { job_id: started.job_id, status: 'queued', count: started.count, tries: [], kind: request.kind };
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
      else if (t.dataset.layers !== undefined) on.layers?.({ description: form.prompt });
      else if (t.dataset.stop !== undefined) { await post(`/api/sound/${job.job_id}/cancel`); }
      else if (t.dataset.more !== undefined) {
        // the same description and settings, shown in the form so it is plain what is being made
        const again = moreLikeThis(job, defaults.limits.tries);
        Object.assign(form, { prompt: again.prompt, seconds: again.seconds, leaveOut: new Set(again.leave_out), kind: again.kind });
        draw();
        await make(again);
      } else if (t.dataset.variations) {
        const sound = kept.find((item) => item.id === t.dataset.variations);
        on.variations?.({ id: t.dataset.variations, name: sound?.name || job?.prompt || 'Sound' });
      }
      else if (t.dataset.kind) {
        // Voice is made from a line to say, on a sheet of its own
        if (kindButtons(defaults).find((k) => k.id === t.dataset.kind)?.sheet === 'voice') on.voice?.();
        else if (form.kind !== t.dataset.kind) {
          // each kind has its own lengths: a loop starts at its usual 8 s, an effect at 1.5 s
          form.kind = t.dataset.kind;
          form.seconds = kindLimits(defaults, form.kind).usual;
          draw();
        }
      }
      else if (t.dataset.leave) {
        const word = t.dataset.leave;
        if (form.leaveOut.has(word)) form.leaveOut.delete(word); else form.leaveOut.add(word);
        t.classList.toggle('on', form.leaveOut.has(word));
        t.setAttribute('aria-pressed', String(form.leaveOut.has(word)));
      } else if (t.dataset.join) await player.toggle(t.dataset.join, { join: true });
      else if (t.dataset.play) await player.toggle(t.dataset.play, { loop: t.dataset.loop !== undefined });
      else if (t.dataset.keep) {
        t.disabled = true;
        await post(`/api/sound/${job.job_id}/keep`, { try: Number(t.dataset.keep) });
        job = await getJson(`/api/sound/${job.job_id}/status`);
        await loadKept();
        drawResults();
        on.kept?.();
      }
    } catch (error) {
      say(error.message);
    }
  });
  el.addEventListener('input', (event) => {
    if (event.target.matches('[data-prompt]')) form.prompt = event.target.value;
    if (event.target.matches('[data-seconds]')) {
      form.seconds = Number(event.target.value);
      el.querySelector('[data-length]').textContent = lengthText(form.seconds);
    }
  });
  el.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });

  (async () => {
    try {
      defaults = await getJson('/api/sound/defaults');
      form.seconds = defaults.limits.default_seconds;
      await loadKept();
      if (open) { draw(); el.querySelector('[data-prompt]')?.focus(); }
    } catch (error) {
      el.innerHTML = `<div class="sound-card"><div class="sheet-top"><h2>Make a <span>sound</span></h2><button type="button" class="ghost" data-close>Close</button></div><p class="hint">${esc(error.message)}</p></div>`;
      el.querySelector('[data-close]').addEventListener('click', close);
    }
  })();

  return { element: el };
}
