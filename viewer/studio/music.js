// Music logic for the studio: what to ask the server for, and how to say what came back.
// No DOM here, so Node tests import this exact file. music-pane.js draws it.
// Music is made from words only: nothing here takes a recording.

/** A length as the page shows it: "30 s", "1 min", "1 min 30 s". */
export function musicLength(seconds) {
  const s = Math.round(Number(seconds));
  if (seconds == null || !Number.isFinite(s)) return '';
  if (s < 60) return `${s} s`;
  return s % 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s / 60} min`;
}

/** The form as it starts: the server's own usual choices. */
export function startForm(defaults) {
  return { prompt: '', seconds: defaults?.limits?.default_seconds ?? 60, singing: !!defaults?.singing?.default,
    lyrics: '', plan: defaults?.plan?.default !== false, loop: !!defaults?.loop?.default };
}

/** The form after a switch is flipped. A loop has no singing, so turning one on turns the other off. */
export function flip(form, key) {
  const next = { ...form, [key]: !form[key] };
  if (key === 'loop' && next.loop) next.singing = false;
  if (key === 'singing' && next.singing) next.loop = false;
  return next;
}

/** What POST /api/music is sent. The words to sing go only when singing is on. The server
 *  checks every limit; this only tidies. */
export function musicRequest(form = {}, defaults = null) {
  const request = { prompt: String(form.prompt || '').trim(), seconds: Number(form.seconds), singing: !!form.singing,
    plan: !!form.plan, loop: !!form.loop, count: defaults?.limits?.tries || 2 };
  if (request.singing) request.lyrics = String(form.lyrics || '').trim();
  return request;
}

/** What the sheet says when the engine is not on this machine. It never offers to fetch it
 *  itself: Setup is where a download is named, sized and agreed to. */
export function musicNotInstalledText(defaults) {
  const size = defaults?.download_size ? ` It is a ${defaults.download_size} download.` : '';
  return `The music engine is not installed yet.${size} Open Setup to add it.`;
}

/** Why Make can't start yet, in words, or null. Limits themselves are the server's to refuse. */
export function musicProblem(request, defaults) {
  if (!defaults?.installed) return musicNotInstalledText(defaults);
  if (!request.prompt) return 'Describe the music first.';
  const limits = defaults.limits || {};
  if (limits.max_prompt && request.prompt.length > limits.max_prompt) return `Keep the description under ${limits.max_prompt} characters.`;
  if (request.singing && !request.lyrics) return 'Write the words to sing, or turn singing off.';
  if (limits.max_lyrics && (request.lyrics || '').length > limits.max_lyrics) return `Keep the words to sing under ${limits.max_lyrics} characters.`;
  return null;
}

/** The Make button and the line under it. */
export function makeWords(defaults, form = {}) {
  const n = defaults?.limits?.tries || 2;
  const wait = Number(form.seconds) > 90 ? 'a minute or two' : 'under a minute';
  return { button: `Make ${n}`,
    hint: `${n} tries, ${wait}. ${form.loop ? 'Each one plays round and round; keep the one you like.' : 'Keep the one you like.'}` };
}

/** What the song planner chose for a try, in plain words: "73 beats a minute · D major". */
export function plannedText(planned) {
  if (!planned) return '';
  return [planned.tempo_bpm ? `${planned.tempo_bpm} beats a minute` : '', planned.key || ''].filter(Boolean).join(' · ');
}

/** The small line on a try: its length, then what was planned for it. */
export function tryWords(made) {
  return [musicLength(made?.seconds), plannedText(made?.planned)].filter(Boolean).join(' · ');
}

/** One line about a music job in progress or how it ended. */
export function musicProgress(job) {
  if (!job) return '';
  const made = job.tries?.length || 0, count = job.count || 2;
  if (job.status === 'error') return job.error || 'That did not work.';
  if (job.status === 'cancelled') return made ? `Stopped. ${made} of ${count} made.` : 'Stopped.';
  if (job.status === 'done') {
    return made === count ? 'Done. Play each one and keep what you like.'
      : `${made} of ${count} came out. The rest were silent or the wrong length, so they were dropped.`;
  }
  if (job.status === 'cancelling') return 'Stopping…';
  const event = job.last_event || {};
  if (job.status === 'queued' || event.phase === 'loading' || (!made && !event.phase)) return 'Loading the music engine…';
  const percent = Number.isFinite(event.percent) ? ` · ${Math.round(event.percent)}%` : '';
  return `Making try ${Math.min(made + 1, count)} of ${count}${percent}`;
}

/** "Make more like this": the same description and switches as the batch a try came from.
 *  No seed is sent, so the server picks a new one and the tries come out different. */
export function moreMusicLikeThis(job, defaults = null) {
  const facts = job?.music || {};
  return musicRequest({ prompt: job?.prompt || '', seconds: facts.seconds ?? job?.seconds, singing: !!facts.singing,
    lyrics: facts.lyrics || '', plan: facts.plan !== false, loop: !!facts.loop }, defaults);
}

/** The form that matches a request, so the page shows what is being made again. */
export function formOf(request) {
  return { prompt: request.prompt, seconds: request.seconds, singing: !!request.singing, lyrics: request.lyrics || '',
    plan: !!request.plan, loop: !!request.loop };
}
