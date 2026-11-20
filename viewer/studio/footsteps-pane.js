// Footsteps on a move: one card under the steps while a character's move is on show. It asks
// the studio where the feet land (viewer/footsteps_api.py), draws them on a timeline, plays the
// move with a step sound at each mark, and saves the marks beside the move. The logic is in
// footsteps.js; this file is the drawing and the buttons.

import { plainError } from './jobs.js';
import { soundChoices, soundHint, foundTitle, axisTicks, timelineRows, dueCues, nextStepFile, saveRequest, stepsProblem, savedLine } from './footsteps.js';

const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const NEW = '+new';

async function call(path, body) {
  const response = await fetch(path, body === undefined ? undefined
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `${response.status} ${response.statusText}`);
  return payload;
}

/** The timeline: a row a foot, shaded where it is on the floor, a numbered dot at each step. */
export function timelineHtml(found) {
  const rows = timelineRows(found);
  if (!rows.length) return '';
  return `<div class="steps-line" role="img" aria-label="${esc(found.words || '')}">
    <div class="steps-axis">${axisTicks(found.seconds).map((t) => `<span>${esc(t)}</span>`).join('')}</div>
    ${rows.map((row) => `<div class="steps-row"><span class="steps-foot">${esc(row.name)}</span><div class="steps-track">
      ${row.planted.map((p) => `<i style="left:${p.left};width:${p.width}"></i>`).join('')}
      ${row.steps.map((s) => `<b data-step-n="${s.n}" style="left:${s.left}">${s.n}</b>`).join('')}</div></div>`).join('')}
    <p class="hint">Shaded: foot on the ground. Dots: where a step sound plays.</p></div>`;
}

export function cardHtml(found, sounds, chosen, on, problem) {
  const choices = soundChoices(sounds, chosen);
  const picked = choices.find((c) => c.selected)?.id ?? '';
  const blocked = problem ? ' disabled' : '';
  return `<li class="step footsteps" data-step="footsteps"><header><span class="dot">♪</span>
    <span style="min-width:0"><h3>Footsteps on this move</h3><div class="sum">Marks each step so your game plays a sound there.</div></span>
    <label class="steps-on"><input type="checkbox" data-steps-on${on ? ' checked' : ''}>On</label></header>
    <div class="body">
      <label class="field">Step sound<select data-steps-sound>
        ${choices.map((c) => `<option value="${esc(c.id)}"${c.selected ? ' selected' : ''}>${esc(c.label)}</option>`).join('')}
        <option value=""${chosen === '' || !choices.length ? ' selected' : ''}>No sound, just the marks</option>
        <option value="${NEW}">Make a new one…</option></select></label>
      <p class="hint" data-steps-hint>${esc(soundHint(sounds, chosen === '' ? null : picked))}</p>
      <div><div class="steps-title">${esc(foundTitle(found))}</div><p class="steps-found">${esc(found?.words || problem || '')}</p></div>
      ${found ? timelineHtml(found) : ''}
      <button type="button" class="ghost" data-steps-play${blocked}>Play with sound</button>
      <button type="button" class="primary" data-steps-save${blocked}>Save step marks</button>
      <p class="hint">Saved beside the move as a small list of times, so your game can play the steps itself. No sound is baked in.</p>
      <p class="hint steps-note" data-steps-note></p>
    </div></li>`;
}

let live = null;   // the card on show: stopped before another takes its place

/** Take the card away and stop anything it was playing. */
export function closeFootsteps(list) {
  live?.stop();
  live = null;
  list?.querySelector('[data-step="footsteps"]')?.remove();
}

/**
 * Put the Footsteps card at the end of `list` for the move on show, or take it away when no
 * move is. ctx: { url(rel), clip(): the move on show ({name, file}) or null,
 * viewer: { onFrame(fn), replay() }, newSound() }.
 */
export async function mountFootsteps(list, ctx) {
  closeFootsteps(list);
  const clip = ctx.clip();
  if (!clip) return;
  const mine = { stop: () => {} };
  live = mine;
  let found = null, sounds = [], problem = null;
  try {
    [found, sounds] = await Promise.all([
      call(`/api/footsteps?file=${encodeURIComponent(clip.file)}`),
      call('/api/sound/kept').then((kept) => kept.sounds || []).catch(() => []),
    ]);
  } catch (error) { problem = plainError(error.message); }
  if (live !== mine || ctx.clip()?.file !== clip.file) return;   // the person moved on while this loaded

  let chosen = found?.saved ? (found.saved.sound_id || '') : null;
  let on = found?.saved ? found.saved.on : true;
  let sounding = false, last = null, played = 0;
  const sound = () => sounds.find((s) => s.id === chosen) || null;

  const draw = (note = '') => {
    list.querySelector('[data-step="footsteps"]')?.remove();
    list.insertAdjacentHTML('beforeend', cardHtml(found, sounds, chosen, on, problem ?? stepsProblem(found)));
    const card = list.querySelector('[data-step="footsteps"]');
    const pick = card.querySelector('[data-steps-sound]');
    if (chosen == null) chosen = pick.value;
    card.querySelector('[data-steps-note]').textContent = note;
    card.querySelector('[data-steps-play]').textContent = sounding ? 'Stop the sound' : 'Play with sound';
    if (!on) card.querySelector('[data-steps-play]').disabled = true;
    pick.onchange = () => {
      if (pick.value === NEW) { pick.value = chosen; ctx.newSound(); return; }
      chosen = pick.value;
      played = 0;
      draw();
    };
    card.querySelector('[data-steps-on]').onchange = (event) => { on = event.target.checked; if (!on) quiet(); draw(); };
    card.querySelector('[data-steps-play]').onclick = () => {
      if (sounding) { quiet(); draw(); return; }
      sounding = true;
      last = null;
      ctx.viewer.replay();
      draw();
    };
    card.querySelector('[data-steps-save]').onclick = async (event) => {
      event.target.disabled = true;
      try {
        const saved = await call('/api/footsteps', saveRequest(clip.file, chosen, on));
        found = { ...found, saved: saved.saved };
        draw(on ? savedLine(saved) : `${savedLine(saved)} Switched off: a game will skip them.`);
      } catch (error) { draw(plainError(error.message)); }
    };
  };

  const hear = (cue, index) => {
    const next = nextStepFile(sound(), played);
    played = next.played;
    if (next.file) new Audio(ctx.url(next.file)).play().catch(() => {});
    const dot = list.querySelector(`[data-step="footsteps"] [data-step-n="${index + 1}"]`);
    if (dot) { dot.classList.add('hit'); setTimeout(() => dot.classList.remove('hit'), 160); }
  };
  const quiet = () => { sounding = false; last = null; };
  ctx.viewer.onFrame((time, duration, playing) => {
    if (!sounding || !found) return;
    if (!playing) { last = time; return; }
    const startsAt = Math.max(0, duration - found.seconds);   // three.js keeps the gap before the file's first key
    for (const cue of dueCues(found.cues, last, time, { loop: true, startsAt })) hear(cue, found.cues.indexOf(cue));
    last = time;
  });
  mine.stop = () => { quiet(); ctx.viewer.onFrame(null); };
  draw();
}
