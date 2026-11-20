// Footsteps on a move: what the card shows and when a step sound is due. No DOM here, so Node
// tests import this exact file. footsteps-pane.js draws it; viewer/footsteps_api.py finds the steps.

import { isSet, nextInOrder } from './sound.js';

/** The Step sound picker: sets first (they sound best), then single sounds. */
export function soundChoices(sounds, chosen = null) {
  const list = [...(sounds || [])].sort((a, b) => Number(isSet(b)) - Number(isSet(a)));
  const pick = list.some((s) => s.id === chosen) ? chosen : list[0]?.id ?? null;
  return list.map((s) => ({
    id: s.id,
    label: isSet(s) ? `${s.name} · ${s.count || s.files?.length || 0} variations` : `${s.name} · one sound`,
    selected: s.id === pick,
  }));
}

/** The line under the picker: why a set, or what to do when nothing is kept yet. */
export function soundHint(sounds, chosen) {
  if (!sounds?.length) return 'No kept sounds yet. Make one, then come back: the marks can be saved without a sound too.';
  const sound = sounds.find((s) => s.id === chosen);
  if (sound && !isSet(sound)) return 'One sound, the same every step. Make variations of it and steps stop sounding copied.';
  return 'A set with variations sounds best: no two steps in a row the same.';
}

/** "Found in the walk". */
export function foundTitle(found) {
  return `Found in the ${String(found?.label || found?.move || 'move').replace(/_/g, ' ').toLowerCase()}`;
}

/** Five evenly spaced labels along the timeline: "0 s", "0.3", "0.6", "0.9", "1.2 s". */
export function axisTicks(seconds, count = 5) {
  const total = Number(seconds) || 0;
  return [...Array(count).keys()].map((i) => {
    const value = +((total * i) / (count - 1)).toFixed(2);
    return i === 0 || i === count - 1 ? `${value} s` : String(value);
  });
}

const percent = (value) => `${+(Math.max(0, Math.min(1, value)) * 100).toFixed(2)}%`;

/** One timeline row per foot: shaded spans where it is on the floor, and a numbered dot at
 *  each step. A span that runs past the end of a loop carries on from the start. */
export function timelineRows(found) {
  const total = Number(found?.seconds) || 0;
  if (!total) return [];
  return (found.feet || []).map((foot) => {
    const planted = [];
    for (const [from, to] of foot.planted || []) {
      const end = Math.min(to, total);
      if (end > from) planted.push({ left: percent(from / total), width: percent((end - from) / total) });
      if (to > total && found.loop) planted.push({ left: '0%', width: percent(Math.min(to - total, from) / total) });
    }
    return {
      name: footName(foot.foot),
      planted,
      steps: (foot.steps || []).map((step) => ({ left: percent(step.time_s / total), n: step.n })),
    };
  });
}

/** "left" -> "Left foot"; "front left" -> "Front left"; anything else is shown as it is. */
export function footName(foot) {
  const text = String(foot || '');
  const named = text.charAt(0).toUpperCase() + text.slice(1);
  return /^(left|right)$/.test(text) ? `${named} foot` : named;
}

/** The cues whose moment passed between two looks at the move's clock. `from` is the time at
 *  the last look (null at the very start, so a step at 0 is heard); on a loop the clock wraps.
 *  `startsAt` is where the move begins on that clock (three.js keeps the file's first-key gap). */
export function dueCues(cues, from, to, { loop = true, startsAt = 0 } = {}) {
  const at = (cue) => cue.time_s + startsAt;
  if (from == null) return (cues || []).filter((cue) => at(cue) <= to);
  if (to >= from) return (cues || []).filter((cue) => at(cue) > from && at(cue) <= to);
  if (!loop) return [];
  return (cues || []).filter((cue) => at(cue) > from || at(cue) <= to);
}

/** The file to play for the next step: a set goes round in a game's order (never the same
 *  one twice running), a single sound is always itself. Returns { file, played }. */
export function nextStepFile(sound, played = 0) {
  if (!sound) return { file: null, played };
  if (!isSet(sound)) return { file: sound.file, played: played + 1 };
  const next = nextInOrder(sound.play_order, played);
  return { file: sound.files?.[next.index] || sound.file, played: next.played };
}

/** What POST /api/footsteps is sent. */
export function saveRequest(file, soundId, on = true) {
  const request = { file, on: !!on };
  return soundId ? { ...request, sound_id: soundId } : request;
}

/** Why Play or Save can't go ahead, in words, or null. */
export function stepsProblem(found) {
  if (!found) return 'Looking for the steps…';
  return found.cues?.length ? null : 'There is nothing to mark on this move.';
}

/** The line after a save. */
export function savedLine(saved) {
  const name = String(saved?.cues_file || '').split('/').pop();
  return name ? `Saved ${name}, beside the move.` : '';
}
