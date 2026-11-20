// Voice logic for the studio: what to ask the server for, and how to say what came back.
// No DOM here, so Node tests import this exact file. voice-pane.js draws it.
// A voice is always one of the engine's own preset voices: nothing here takes a recording.

import { lengthText } from './sound.js';

/** The engine with this id, or the first one. */
export function engineOf(defaults, id) {
  const engines = defaults?.engines || [];
  return engines.find((engine) => engine.id === id) || engines[0] || null;
}

/** The engines as buttons, named by what they do. One not installed can still be picked:
 *  the sheet then says so and points to Setup. */
export function engineButtons(defaults, chosen) {
  const picked = engineOf(defaults, chosen)?.id;
  return (defaults?.engines || []).map((engine) => ({
    id: engine.id, label: engine.label, hint: engine.hint, pressed: engine.id === picked, installed: !!engine.installed,
  }));
}

/** What the sheet says when the chosen engine is not on this machine. It never offers to
 *  fetch it itself: Setup is where a download is named, sized and agreed to. */
export function voiceNotInstalledText(engine) {
  const size = engine?.download_size ? ` It is a ${engine.download_size} download.` : '';
  return `"${engine?.label || 'This voice'}" is not installed yet.${size} Open Setup to add it.`;
}

/** The voice picked, or the engine's usual one when the pick belongs to the other engine. */
export function voiceOf(engine, id) {
  const voices = engine?.voices || [];
  return voices.find((voice) => voice.id === id) || voices.find((voice) => voice.id === engine?.default_voice) || voices[0] || null;
}

/** The language a line is taken to be in until one is picked: the voice's own. */
export function languageOf(engine, voiceId) {
  const own = voiceOf(engine, voiceId)?.language;
  return (engine?.languages || []).some((item) => item.id === own) ? own : engine?.default_language;
}

/** How the line is said: the user's own words when typed, else the chip, else nothing. */
export function directionOf({ direction = '', ownWords = '' } = {}) {
  return String(ownWords).trim() || direction || '';
}

/** What POST /api/voice is sent. Only what the engine takes is sent; the server checks every
 *  limit. `lines` (one line made again) is sent as given instead of the whole text. */
export function voiceRequest(form = {}, engine, { lines = null, using = [] } = {}) {
  const request = { engine: engine?.id, voice: voiceOf(engine, form.voice)?.id, count: engine?.tries || 1 };
  if (lines) request.lines = [...lines]; else request.text = String(form.text || '').trim();
  if (engine?.directions && directionOf(form)) request.direction = directionOf(form);
  if (engine?.languages?.length) request.language = form.language || languageOf(engine, form.voice);
  if (engine?.speed && Number.isFinite(Number(form.speed))) request.speed = Number(form.speed);
  if (using.length) request.using = [...using];
  return request;
}

/** Why Make can't start yet, in words, or null. Limits themselves are the server's to refuse. */
export function voiceProblem(request, engine, limits = {}) {
  if (!engine?.installed) return voiceNotInstalledText(engine);
  const text = request.lines ? request.lines.join(' ') : request.text;
  if (!text) return 'Type the line to say first.';
  if (limits.max_text && text.length > limits.max_text) return `Keep it under ${limits.max_text} characters, or say it in parts.`;
  return null;
}

/** The Make button and the line under it. */
export function makeWords(engine) {
  const n = engine?.tries || 1;
  return {
    button: `Make ${n}`,
    hint: n > 1 ? `${n} tries of each line, a few seconds each. Keep the one you like.`
      : 'One try of each line: this voice says a line the same way every time.',
  };
}

/** The speed as the page shows it: "1×", "1.2×". */
export function speedText(speed) {
  return `${+Number(speed || 1).toFixed(2)}×`;
}

// ------------------------------------------------------------------ lines and their takes

/** The page's lines, empty, from what the server said it will say. */
export function newBlocks(lines) {
  return (lines || []).map((text) => ({ text, takes: [], chosen: null, making: true }));
}

const takeKey = (take) => `${take.job_id}:${take.number}`;

/** A job's tries, filed under the lines they say. `lineMap[n]` is the block that the job's
 *  line n belongs to (a whole text: 0, 1, 2...; one line made again: just that block).
 *  A line's first take is picked for it until the user picks another. */
export function addTakes(blocks, job, lineMap) {
  const ended = ['done', 'error', 'cancelled'].includes(job?.status);
  const touched = new Set(Object.values(lineMap || {}));
  return (blocks || []).map((block, index) => {
    if (!touched.has(index)) return block;
    const known = new Map(block.takes.map((take) => [takeKey(take), take]));
    for (const made of job?.tries || []) {
      if (lineMap[made.line] !== index) continue;
      const take = { job_id: job.job_id, number: made.number, url: made.url, seconds: made.seconds,
        bars: made.bars || [], kept: made.kept || null, guessed: made.guessed || [] };
      known.set(takeKey(take), take);
    }
    const takes = [...known.values()];
    const chosen = takes.some((take) => takeKey(take) === block.chosen) ? block.chosen : (takes[0] ? takeKey(takes[0]) : null);
    return { ...block, takes, chosen, making: !ended };
  });
}

/** The same blocks with one take picked for its line. */
export function chooseTake(blocks, index, key) {
  return blocks.map((block, at) => (at === index && block.takes.some((take) => takeKey(take) === key) ? { ...block, chosen: key } : block));
}

/** A line as the page draws it: its takes as cards, numbered in the order they were made. */
export function blockCards(block, playing = null, several = false) {
  return block.takes.map((take, at) => ({
    key: takeKey(take), job_id: take.job_id, number: take.number, url: take.url, bars: take.bars,
    title: `Take ${at + 1}`,
    words: [lengthText(take.seconds), guessedText(take.guessed)].filter(Boolean).join(' · '),
    chosen: several && takeKey(take) === block.chosen, kept: take.kept, playing: playing === take.url,
  }));
}

/** Said when a word was in no dictionary and had to be sounded out from its letters. */
export function guessedText(words) {
  return words?.length ? `Guessed how to say: ${words.join(', ')}` : '';
}

/** Every job whose takes are still on the page, so the server does not clear them. */
export function jobsOnPage(blocks) {
  return [...new Set((blocks || []).flatMap((block) => block.takes.map((take) => take.job_id)))];
}

/** What POST /api/voice/join is sent: the picked take of each line, in order. */
export function joinRequest(blocks, { gapMs = null, name = '' } = {}) {
  const parts = (blocks || []).map((block) => block.takes.find((take) => takeKey(take) === block.chosen))
    .map((take) => (take ? { job_id: take.job_id, try: take.number } : null));
  const request = { parts };
  if (gapMs != null) request.gap_ms = Number(gapMs);
  if (name.trim()) request.name = name.trim();
  return request;
}

/** Why "Save as one" can't run yet, in words, or null. */
export function joinProblem(blocks) {
  if ((blocks || []).length < 2) return 'There is only one line. Keep it instead.';
  const missing = blocks.findIndex((block) => !block.takes.length);
  return missing >= 0 ? `Line ${missing + 1} has no take yet. Make it again first.` : null;
}

/** One line about a voice job in progress or how it ended. */
export function voiceProgress(job, lineCount = 1) {
  if (!job) return '';
  const made = job.tries?.length || 0, count = job.count || 1;
  if (job.status === 'error') return job.error || 'That did not work.';
  if (job.status === 'cancelled') return made ? `Stopped. ${made} of ${count} made.` : 'Stopped.';
  if (job.status === 'done') {
    if (made < count) return `${made} of ${count} came out. The rest ran on too long or were silent, so they were dropped.`;
    return lineCount > 1 ? 'Done. Pick a take of each line, then save them as one or keep them one by one.'
      : 'Done. Play each one and keep what you like.';
  }
  if (job.status === 'cancelling') return 'Stopping…';
  if (job.status === 'queued' || !made) return 'Loading the voice…';
  return `Saying it: ${made} of ${count} made`;
}
