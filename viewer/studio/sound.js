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

/** The small line under a kept sound's name: "Effect · 1.2 s". A set says how many it holds. */
export function keptMeta(sound) {
  if (isSet(sound)) return `Set · ${sound.count || sound.files?.length || 0} sounds`;
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

// ------------------------------------------------------------------ variation sets

/** A saved set of numbered takes, not a single sound. */
export function isSet(sound) {
  return sound?.kind === 'sound_set';
}

/** A kept sound's id from its file ("sounds/<id>/sound.wav"), or null. */
export function soundIdOf(file) {
  return /^sounds\/([A-Za-z0-9_-]+)\/sound\.wav$/.exec(String(file || ''))?.[1] || null;
}

/** "Make more like this": the same description and settings as the batch a try came from.
 *  No seed is sent, so the server picks a new one and the tries come out different. */
export function moreLikeThis(job, count = 4) {
  return soundRequest({ prompt: job?.prompt || '', seconds: job?.seconds, leaveOut: job?.leave_out || [], count });
}

/** The "How different" slider: one stop per choice the server offers. */
export function howDifferentStops(options, chosen) {
  const list = options?.how_different || [];
  const at = Math.max(0, list.findIndex((item) => item.id === (chosen ?? options?.default_how_different)));
  return { max: Math.max(0, list.length - 1), at, id: list[at]?.id, label: list[at]?.label || '', hint: list[at]?.hint || '' };
}

/** What POST /api/sounds/<id>/variations is sent. The server checks every limit. */
export function variationsRequest({ count = 6, howDifferent = 'a little', change = [], seed = null } = {}) {
  const request = { count: Number(count), how_different: howDifferent, change: [...change] };
  return seed == null ? request : { ...request, seed };
}

/** Why Make can't start yet, in words, or null. */
export function variationsProblem(request) {
  return request.change.length ? null : 'Tick at least one thing to change.';
}

/** The numbers that make a heard set again, exactly: what Save sends back. */
export function sameSet(preview) {
  return variationsRequest({ count: preview.count, howDifferent: preview.how_different, change: preview.change, seed: preview.seed });
}

/** The takes as rows: file name, wave, a plain note, and whether it is the one playing. */
export function variationRows(preview, playing = null) {
  return (preview?.variations || []).map((take) => ({
    index: take.index, file: take.file, url: take.url, bars: take.bars || [],
    // the number is the part that matters, so a long name is cut before it, never through it
    stem: fileParts(take.file)[0], tail: fileParts(take.file)[1],
    note: [take.note, lengthText(take.seconds)].filter(Boolean).join(' · '),
    playing: playing === take.url,
  }));
}

function fileParts(file) {
  const found = /^(.*?)(_\d+\.\w+)$/.exec(String(file || ''));
  return found ? [found[1], found[2]] : [String(file || ''), ''];
}

/** "Play like a game": the next take in the order the server planned (random, never the same
 *  one twice running, and safe to go round again). Returns the take's place and the new count. */
export function nextInOrder(order, played = 0) {
  if (!order?.length) return { index: 0, played: played + 1 };
  return { index: order[played % order.length], played: played + 1 };
}

/** What saving a set of `count` does, under "Save the set". */
export function saveSetHint(count) {
  return `${count} numbered files in one folder, ready to drop into Unity, Godot or Roblox. One licence record covers them all.`;
}
