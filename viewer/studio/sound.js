// Sound logic for the studio: what to ask the server for, and how to say what came back.
// No DOM here, so Node tests import this exact file. sound-pane.js draws it.

/** A length as the page shows it: "1.5 s", "30 s". */
export function lengthText(seconds) {
  const s = Number(seconds);
  if (seconds == null || !Number.isFinite(s)) return '';
  return `${+s.toFixed(1)} s`;
}

/** What POST /api/sound is sent. The server checks every limit; this only tidies. */
export function soundRequest({ prompt = '', seconds, leaveOut = [], count = 4, kind = 'effect' } = {}) {
  return { kind, prompt: String(prompt).trim(), seconds: Number(seconds), leave_out: [...leaveOut], count };
}

/** Why Make can't start yet, in words, or null. Limits themselves are the server's to refuse. */
export function requestProblem(request, defaults) {
  if (!defaults?.installed) return notInstalledText(defaults);
  if (!request.prompt) return 'Describe the sound first.';
  const most = defaults.limits?.max_prompt;
  if (most && request.prompt.length > most) return `Keep the description under ${most} characters.`;
  return null;
}

/** What the sheet says when the engine is not on this machine. It never offers to fetch it
 *  itself: Setup is where a download is named, sized and agreed to. */
export function notInstalledText(defaults) {
  const size = defaults?.download_size ? ` It is a ${defaults.download_size} download.` : '';
  return `The sound engine is not installed yet.${size} Open Setup to add it.`;
}

/** The kinds as buttons: only a built one can be pressed; the rest say "later". */
export function kindButtons(defaults, chosen = 'effect') {
  return (defaults?.kinds || []).map((kind) => ({
    id: kind.id,
    label: kind.ready ? kind.label : `${kind.label} · later`,
    hint: kind.hint,
    disabled: !kind.ready,
    pressed: kind.ready && kind.id === chosen,
  }));
}

/** The tries as cards, always `count` of them: made ones can be played, the one being made
 *  says so, the rest wait. A try the engine could not make (silent, say) is marked. */
export function tryCards(job, count = 4) {
  const total = job?.count || count;
  const made = new Map((job?.tries || []).map((t) => [t.number, t]));
  const ended = ['done', 'error', 'cancelled'].includes(job?.status);
  const next = [...Array(total).keys()].map((i) => i + 1).find((n) => !made.has(n) && n > Math.max(0, ...made.keys()));
  return [...Array(total).keys()].map((i) => {
    const number = i + 1, t = made.get(number);
    if (t) return { number, state: 'ready', seconds: t.seconds, bars: t.bars || [], url: t.url, kept: t.kept || null };
    if (!job) return { number, state: 'empty', bars: [] };
    if (ended) return { number, state: 'missing', bars: [] };
    return { number, state: number === next && job.status === 'running' ? 'making' : 'waiting', bars: [] };
  });
}

/** One line about a job in progress or how it ended. */
export function progressLine(job) {
  if (!job) return '';
  const made = job.tries?.length || 0, count = job.count || 4;
  if (job.status === 'error') return job.error || 'That did not work.';
  if (job.status === 'cancelled') return made ? `Stopped. ${made} of ${count} made.` : 'Stopped.';
  if (job.status === 'done') {
    return made === count ? 'Done. Play each one and keep what you like.'
      : `${made} of ${count} came out. The rest were silent or broken, so they were dropped.`;
  }
  if (job.status === 'cancelling') return 'Stopping…';
  const event = job.last_event || {};
  if (job.status === 'queued' || event.phase === 'loading' || (!made && !event.phase)) return 'Loading the sound engine…';
  const percent = Number.isFinite(event.percent) ? ` · ${Math.round(event.percent)}%` : '';
  return `Making try ${Math.min(made + 1, count)} of ${count}${percent}`;
}

/** Bar heights in pixels for a wave picture: never shorter than 3, so silence still shows. */
export function barHeights(bars, tallest = 64) {
  return (bars || []).map((b) => Math.max(3, Math.round(tallest * Math.min(1, Math.max(0, Number(b) || 0)))));
}

const KIND_WORDS = { effect: 'Effect', loop: 'Background loop', music: 'Music', voice: 'Voice' };

/** The small line under a kept sound's name: "Effect · 1.2 s". */
export function keptMeta(sound) {
  const kind = KIND_WORDS[sound?.sound_kind] || 'Sound';
  const length = lengthText(sound?.seconds);
  return length ? `${kind} · ${length}` : kind;
}

/** Kept sounds whose name or description has every word typed in the Library's search. */
export function filterSounds(sounds, query = '') {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return (sounds || []).filter((s) => words.every((w) => `${s.name} ${s.prompt || ''}`.toLowerCase().includes(w)));
}

/** The Library's footer for the Sounds filter. */
export function soundsFoot(sounds) {
  const n = sounds?.length || 0;
  return `${n} sound${n === 1 ? '' : 's'} · saved in output/sounds on this machine`;
}
