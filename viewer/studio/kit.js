// Kits: a character and the gear made for it (viewer/kits_api.py keeps the list of ids).
// What the kit pane shows and what its buttons run. No DOM here, so Node tests import this exact file.

import { STEPS, stepCount, stepState, finishDetailSettings } from './library.js';
import { chainPlan } from './steps.js';
import { pictureSettings } from './create.js';

/** Redo makes one more try of a step; ×2 to ×4 make that many. */
export const TRY_COUNTS = [1, 2, 3, 4];

const GEAR = 'a sword, a shield, a helmet, a bow, a quiver of arrows, a dagger, a belt pouch, a potion, a lantern';

/** The description "Make gear" opens Create with: the character's own words, without the wording
 *  the studio adds to every character (pose, background), and nine things to fill the sheet. */
export function gearPrompt(character, recipes) {
  let subject = String(character?.prompt || character?.name || 'this character');
  const wording = recipes?.goals?.character?.wording;
  if (wording) subject = subject.replace(wording, '');
  subject = subject.replace(/[\s,.;:]+$/, '').replace(/\s+/g, ' ').trim() || 'this character';
  if (subject.length > 160) subject = subject.slice(0, 160).replace(/\s+\S*$/, '');
  if (/^[A-Z](?![A-Z])/.test(subject)) subject = subject[0].toLowerCase() + subject.slice(1);
  return `Nine pieces of gear for ${subject}, in the same art style: ${GEAR}`;
}

/** The steps one kit row shows: five for a character, three for a prop set or prop. */
export function laneSteps(asset) {
  const set = asset.kind === 'prop set';
  return STEPS.slice(0, asset.kind === 'prop' ? 3 : stepCount(asset))
    .map((step) => ({ ...step, label: set && step.id === 'finished' ? 'Split' : step.label }));
}

/** What a step made, as one comparable value: two versions with the same value share that step. */
export function stepMade(asset, stepId) {
  if (stepId === 'finished' && asset.kind === 'prop set') return asset.props?.length ? asset.props.map((p) => p.file).join('|') : null;
  if (stepId === 'animated') return null;   // moves are added to one rig, not tried side by side
  return asset[stepId] || null;
}

const EARLIER = { picture: [], model: ['picture'], finished: ['picture', 'model'], rigged: ['picture', 'model', 'finished'] };
const reach = (a) => ['picture', 'model', 'finished', 'rigged', 'animated'].filter((s) => stepMade(a, s) || (s === 'animated' && a.clips?.length)).length;

/**
 * Every try of one step, one entry per different result, the given asset's own first.
 * Picture: every picture drawn from the same prompt. The rest: every version in the same family
 * that shares this one's earlier steps. Each entry is the version to switch the kit to.
 */
export function stepOptions(asset, assets, stepId) {
  if (!asset || !(stepId in EARLIER)) return [];
  const pool = stepId === 'picture'
    ? assets.filter((a) => a.picture && (a === asset || a.id === asset.id || (asset.prompt && a.prompt === asset.prompt)))
    : assets.filter((a) => (a.family || a.id) === (asset.family || asset.id)
      && EARLIER[stepId].every((k) => (a[k] || null) === (asset[k] || null)));
  const groups = new Map();
  for (const a of pool) {
    const made = stepMade(a, stepId);
    if (!made) continue;
    const best = groups.get(made);
    // stand for a result with the asset itself, else the version that went furthest, else the newest
    if (!best || a.id === asset.id || (best.id !== asset.id && (reach(a) > reach(best)
      || (reach(a) === reach(best) && (a.updated || 0) > (best.updated || 0))))) groups.set(made, a);
  }
  const own = stepMade(asset, stepId);
  return [...groups.entries()].sort(([a], [b]) => (a === own ? -1 : b === own ? 1 : 0)).map(([, a]) => a);
}

/** The kit as rows of step nodes: the character first, then each piece of gear. */
export function kitLanes(kit, assets) {
  const byId = new Map(assets.map((a) => [a.id, a]));
  const lane = (id, role) => {
    const asset = byId.get(id) ?? null;
    return {
      id, role, asset, missing: !asset,
      nodes: asset ? laneSteps(asset).map((step) => ({
        id: step.id, label: step.label,
        state: stepState(asset, step.id),
        options: stepOptions(asset, assets, step.id),
      })) : [],
    };
  };
  return [lane(kit.character, 'character'), ...kit.gear.map((id) => lane(id, 'gear'))];
}

/** Every finished prop in the kit, for the grid that shows them all at once. */
export function kitProps(kit, assets) {
  const byId = new Map(assets.map((a) => [a.id, a]));
  return kit.gear.flatMap((id) => {
    const a = byId.get(id);
    if (a?.kind === 'prop set') return (a.props || []).map((p, index) => ({ setId: a.id, set: a.name, index, name: p.name.replace(/[_-]/g, ' '), file: p.file }));
    if (a?.kind === 'prop' && a.finished) return [{ setId: a.id, set: a.name, index: -1, name: a.name, file: a.finished }];
    return [];
  });
}

/** The kits an asset is in, as the character or as gear. */
export function kitsWith(kits, assetId) {
  return (kits || []).filter((k) => k.character === assetId || k.gear.includes(assetId));
}

/** What a node can run: "make" when the step is ready, "try" when it is done and can be made again. */
export function nodeActions(node, asset) {
  if (node.id === 'animated') return node.state === 'now' ? ['make'] : [];
  if (node.state === 'later') return [];
  if (node.id === 'picture' && !asset.prompt) return [];   // an uploaded picture has nothing to draw again from
  return node.state === 'done' ? ['try'] : ['make'];
}

/**
 * The runs behind Make, Redo and ×2-×4: `count` one-step plans for this step, each a fresh try
 * (a new seed), with the Steps panel's own defaults. `tools` gives the first ready 3D engine and move.
 * Every try is a new version: nothing is replaced. An empty list means the step cannot run from here.
 */
export function tryPlans(stepId, asset, count, tools = {}, random = Math.random) {
  const n = Math.max(1, Math.min(TRY_COUNTS.at(-1), Math.round(Number(count)) || 1));
  const want = asset.kind === 'prop set' ? 'set' : asset.kind === 'prop' ? 'prop' : 'character';
  const one = () => {
    if (stepId === 'picture') {
      if (!asset.prompt) return null;
      const before = asset.picture_settings || {};
      return { description: asset.name || 'Asset', want, steps: ['picture'], prompt: asset.prompt,
        pictureSettings: pictureSettings({ size: before.width, steps: before.steps }, random) };
    }
    const settings = {
      backend: tools.engine, rigSource: 'finished', preset: tools.move,
      detail: finishDetailSettings('20000'), photo: true, edgeFade: true, nudge: true, texture: 2048, surface: 'keep', cleanup: 'auto',
      height: asset.kind === 'character' ? 1.8 : null, upright: asset.kind === 'prop' ? true : undefined,
      speed: 1, spread: 0, inPlace: true,
    };
    if (stepId === 'model' && !settings.backend) return null;
    return chainPlan(stepId, asset, settings);
  };
  const plans = [];
  for (let i = 0; i < n; i += 1) {
    const plan = one();
    if (!plan) return [];
    plans.push(plan);
  }
  return plans;
}
