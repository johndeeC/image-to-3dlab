// The Music sheet: describe a tune on the left, its tries on the right, keep the one you like.
// Logic lives in music.js; this file only draws it and talks to the server.
// With the engine missing it says so and links to Setup. It never fetches anything itself,
// and it has no way to send a recording: music is made from words only.

import { esc, PLAY, STOP, SPEAKER, getJson, post, createPlayer, waveHtml } from './sound-pane.js';
import { keptMeta, kindButtons, tryCards, isLoop } from './sound.js';
import { musicLength, startForm, flip, musicRequest, musicNotInstalledText, musicProblem, makeWords, tryWords,
  musicProgress, moreMusicLikeThis, formOf } from './music.js';

function tryHtml(card, made, player, busy, loop) {
  const ready = card.state === 'ready';
  const words = { making: 'Being made…', waiting: 'Waiting', missing: 'Did not come out', empty: '' }[card.state] ?? tryWords(made);
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
        ${loop && ready ? `<button type="button" class="ghost small more" data-join="${esc(card.url)}" aria-pressed="${joining}" data-tip="Plays the end running into the start, two seconds of each, so you can hear how it comes round.">${joining ? 'Stop' : 'Hear the join'}</button>` : ''}
        ${ready ? `<button type="button" class="ghost small more" data-more ${busy ? 'disabled' : ''} data-tip="New tries from the same description. Tries you have not kept are cleared.">Make more like this</button>` : ''}</div>
    </li>`;
}

function keptHtml(sounds, base, playing) {
  if (!sounds.length) return '<p class="hint">Music you keep shows here.</p>';
  return sounds.slice(0, 8).map((sound) => {
    const source = base + sound.file;
    return `<button type="button" class="kept-sound" data-play="${esc(source)}" ${isLoop(sound) ? 'data-loop' : ''} data-tip="${esc(sound.prompt || sound.name)}" aria-label="${playing === source ? 'Stop' : 'Play'} ${esc(sound.name)}">
        <span class="icon">${playing === source ? STOP : SPEAKER}</span><b>${esc(sound.name)}</b><small class="meta">${esc(keptMeta(sound))}</small></button>`;
  }).join('');
}

const switchHtml = (key, told, on, off = false) => `<div class="field music-switch">
    <button type="button" class="qs${on ? ' on' : ''}" data-switch="${key}" role="switch" aria-checked="${on}" ${off ? 'disabled' : ''}>${esc(told?.label || key)}</button>
    <small class="hint">${esc(told?.hint || '')}</small></div>`;

/**
 * Open the sheet over the viewer. `on.kept()` tells the page a sound joined the Library;
 * `on.back()` asks it to open the Sound sheet and `on.voice()` the Voice sheet (another kind
 * was picked). Returns { element } so the page can tell whether it is still open.
 */
export function openMusicPane({ stage, on = {} }) {
  stage.querySelector('.sheet')?.onSheetClose?.();
  stage.querySelector('.sheet')?.remove();
  const el = document.createElement('div');
  el.className = 'sheet sound-sheet music-sheet';
  el.innerHTML = '<div class="sound-card"><p class="hint">Loading…</p></div>';
  stage.appendChild(el);

  let defaults = null, kinds = [], kept = [], base = '/output/', job = null, note = '', timer = null, open = true;
  let form = startForm(null);
  const player = createPlayer(() => { if (open) drawResults(); });
  const running = () => job && !['done', 'error', 'cancelled'].includes(job.status);

  const formHtml = () => {
    const limits = defaults.limits, blocked = !defaults.installed, words = makeWords(defaults, form);
    return `<section class="sound-form" aria-label="Make music">
      <div class="sheet-top"><h2>Make <span>music</span></h2><button type="button" class="ghost" data-close>Close</button></div>
      ${kinds.length ? `<div class="field"><span>What kind?</span>
        <div class="kinds" role="group" aria-label="What kind?">${kinds.map((k) => `<button type="button" class="kind" data-kind="${esc(k.id)}" aria-pressed="${k.id === 'music'}" ${k.disabled ? 'disabled' : ''}>${esc(k.label)}</button>`).join('')}</div></div>` : ''}
      ${blocked ? `<div class="sound-setup" role="status"><p>${esc(musicNotInstalledText(defaults))}</p><a class="primary" href="./setup.html">Open Setup</a></div>` : ''}
      <label class="field"><span>Describe the music</span>
        <textarea data-prompt rows="3" maxlength="${limits.max_prompt}" placeholder="Calm fantasy village theme, harp, flute, soft strings">${esc(form.prompt)}</textarea>
        <small class="hint">Style, mood, instruments. It is made from your words only.</small></label>
      ${switchHtml('singing', defaults.singing, form.singing)}
      ${form.singing ? `<label class="field"><span>Words to sing</span>
        <textarea data-lyrics rows="5" maxlength="${limits.max_lyrics}" placeholder="One line of the song on each line">${esc(form.lyrics)}</textarea>
        <small class="hint">Write your own words. A longer song needs a longer length.</small></label>` : ''}
      <label class="field"><span>Length <output data-length>${esc(musicLength(form.seconds))}</output></span>
        <input type="range" data-seconds min="${limits.min_seconds}" max="${limits.max_seconds}" step="5" value="${form.seconds}">
        <small class="hint">A tune comes to its own ending, often a few seconds early. A loop is the length you pick.</small></label>
      ${switchHtml('plan', defaults.plan, form.plan)}
      ${switchHtml('loop', defaults.loop, form.loop)}
      <div class="sound-go">
        <button type="button" class="primary" data-make ${blocked ? 'disabled' : ''}>${esc(words.button)}</button>
        <small class="hint">${esc(words.hint)}</small>
        <p class="sound-note" data-note role="status" aria-live="polite"></p>
      </div>
    </section>`;
  };

  const resultsHtml = () => {
    const count = defaults.limits.tries, loop = !!job?.music?.loop;
    const made = new Map((job?.tries || []).map((t) => [t.number, t]));
    return `<div class="sound-head"><h3>${job?.count || count} tries</h3><span class="meta">Made on this computer · free to use in games · stereo</span></div>
      <p class="hint" data-progress role="status" aria-live="polite">${esc(musicProgress(job) || 'Describe a tune and press Make. Your tries show here.')}
        ${running() ? '<button type="button" class="ghost small" data-stop>Stop</button>' : ''}</p>
      <ul class="sound-tries">${tryCards(job, count).map((card) => tryHtml(card, made.get(card.number), player, !!running(), loop)).join('')}</ul>
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
    const request = again || musicRequest(form, defaults);
    const problem = musicProblem(request, defaults);
    if (problem) { say(problem); return; }
    say('');
    player.stop();
    try {
      const started = await post('/api/music', request);
      job = { job_id: started.job_id, status: 'queued', count: started.count, tries: [], kind: 'music', music: { loop: started.loop } };
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
      else if (t.dataset.stop !== undefined) await post(`/api/sound/${job.job_id}/cancel`);
      else if (t.dataset.switch) { form = flip(form, t.dataset.switch); draw(); }
      else if (t.dataset.more !== undefined) {
        // the same description and switches, shown in the form so it is plain what is being made
        const again = moreMusicLikeThis(job, defaults);
        form = formOf(again);
        draw();
        await make(again);
      } else if (t.dataset.kind) {
        if (t.dataset.kind === 'voice') on.voice?.();
        else if (t.dataset.kind !== 'music') on.back?.();
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
    if (event.target.matches('[data-lyrics]')) form.lyrics = event.target.value;
    if (event.target.matches('[data-seconds]')) {
      form.seconds = Number(event.target.value);
      el.querySelector('[data-length]').textContent = musicLength(form.seconds);
    }
  });
  el.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });

  (async () => {
    try {
      defaults = await getJson('/api/music/defaults');
      form = startForm(defaults);
      kinds = kindButtons(await getJson('/api/sound/defaults').catch(() => null), 'music');
      await loadKept();
      if (open) { draw(); el.querySelector('[data-prompt]')?.focus(); }
    } catch (error) {
      el.innerHTML = `<div class="sound-card"><div class="sheet-top"><h2>Make <span>music</span></h2><button type="button" class="ghost" data-close>Close</button></div><p class="hint">${esc(error.message)}</p></div>`;
      el.querySelector('[data-close]').addEventListener('click', close);
    }
  })();

  return { element: el };
}
