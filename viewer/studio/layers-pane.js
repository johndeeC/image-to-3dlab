// "Layers": build one sound from a few parts. Each part is made on its own (described, your
// own file, or a sound you kept), set on a timeline with its own start, volume and pitch, then
// played together and saved as one sound. The server does the mixing, for hearing and for
// saving alike. Logic lives in layers.js; this file only draws it and talks to the server.

import { lengthText, tryCards, progressLine } from './sound.js';
import { signed, newLayer, layersFromSplit, splitLine, madeLayer, layerSourceLine, timeline, stackRequest, stackProblem,
  notMadeCount, layerMakeRequest, finishedLicence, licenceChoice, fileProblem, layersTitle } from './layers.js';
import { esc, getJson, post, createPlayer, waveHtml, PLAY, STOP } from './sound-pane.js';

async function sendFile(path, fields) {
  const form = new FormData();
  Object.entries(fields).forEach(([key, value]) => { if (value != null && value !== '') form.append(key, value); });
  const response = await fetch(path, { method: 'POST', body: form });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || 'That did not work.');
  return body;
}

/**
 * Open the Layers screen over the viewer. Give `description` to start from the split helper,
 * or `sound` ({ id, name }) to open a kept sound that was built from layers. Neither: one
 * empty layer. `on.saved()` tells the page a sound joined the Library; `on.back()` goes back to the Sound sheet.
 */
export function openLayersPane({ stage, description = '', sound = null, on = {} }) {
  stage.querySelector('.sheet')?.onSheetClose?.();
  stage.querySelector('.sheet')?.remove();
  const el = document.createElement('div');
  el.className = 'sheet sound-sheet layers-sheet';
  el.innerHTML = '<div class="layers-card"><p class="hint">Loading…</p></div>';
  stage.appendChild(el);

  let options = null, defaults = null, kept = [], rows = [], mix = null, note = '', open = true, timer = null;
  let name = sound?.name || '', saved = null, busy = false, adding = null, making = null;
  const own = { licence: null, link: '' };
  const player = createPlayer(() => { if (open) draw(); });
  const row = (key) => rows.find((item) => item.key === key);
  const changed = () => { mix = null; saved = null; };

  const controlsHtml = (item) => {
    const lim = options.limits;
    return `<div class="layer-knobs">
      <label><span>Starts at</span><span class="with-unit"><input type="number" data-set="start_ms" data-key="${item.key}" min="${lim.start_ms.min}" max="${lim.start_ms.max}" step="10" value="${item.start_ms}"><i>ms</i></span></label>
      <label><span>Volume <output>${esc(signed(item.volume_db))}</output></span>
        <input type="range" data-set="volume_db" data-key="${item.key}" min="-24" max="${lim.volume_db.max}" step="1" value="${item.volume_db}" data-tip="0 is as it was made. Each 6 down is about half as loud."></label>
      <label><span>Pitch <output>${esc(signed(item.pitch_semitones))}</output></span>
        <input type="range" data-set="pitch_semitones" data-key="${item.key}" min="${lim.pitch_semitones.min}" max="${lim.pitch_semitones.max}" step="1" value="${item.pitch_semitones}" data-tip="Steps up or down. Higher is also shorter, lower is longer."></label>
    </div>`;
  };

  const triesHtml = (item) => {
    if (!item.job) return '';
    const cards = tryCards(item.job, item.job.count);
    return `<p class="hint" role="status" aria-live="polite">${esc(progressLine(item.job))}
        ${['done', 'error', 'cancelled'].includes(item.job.status) ? '' : `<button type="button" class="ghost small" data-stop="${item.key}">Stop</button>`}</p>
      <ul class="layer-tries">${cards.map((card) => {
        const ready = card.state === 'ready', playing = ready && player.playing === card.url;
        return `<li class="${card.state}"><button type="button" class="play" data-play="${esc(card.url || '')}" aria-label="${playing ? 'Stop' : 'Play'} try ${card.number}" ${ready ? '' : 'disabled'}>${playing ? STOP : PLAY}</button>
          ${waveHtml(card.bars, 36)}<span class="meta">${esc(ready ? lengthText(card.seconds) : { making: 'Being made…', waiting: 'Waiting', missing: 'Came out silent' }[card.state] || '')}</span>
          <button type="button" class="ghost small" data-use="${item.key}" data-try="${card.number}" ${ready ? '' : 'disabled'}>Use this one</button></li>`;
      }).join('')}</ul>`;
  };

  const rowHtml = (item, block) => {
    const playing = item.url && player.playing === item.url;
    const head = `<div class="layer-who">
        ${item.part ? `<span class="layer-part">${esc(item.part)}</span>` : ''}
        <b>${esc(item.layer ? item.label : item.describe || 'New layer')}</b>
        <small class="meta">${esc(layerSourceLine(item))}${item.layer ? ` · ${esc(lengthText(item.seconds))}` : ''}</small>
        ${item.where ? `<small class="meta">Where it's from: ${esc(item.where)}</small>` : ''}
        <button type="button" class="link" data-remove="${item.key}">Remove</button></div>`;
    if (!item.layer) {
      const blocked = !defaults.installed || !!making;
      return `<li class="layer-row todo" data-row="${item.key}">${head}
        <div class="layer-make"><label class="field"><span>Describe it</span>
            <textarea data-describe="${item.key}" rows="2" maxlength="${defaults.limits.max_prompt}" placeholder="One thing happening. Arrow thudding into wood, one hit, dry">${esc(item.describe)}</textarea></label>
          <div class="row"><label class="tries-pick">Tries <select data-count="${item.key}">${[1, 2, 3, 4].filter((n) => n <= defaults.limits.tries).map((n) => `<option value="${n}" ${n === item.count ? 'selected' : ''}>${n}</option>`).join('')}</select></label>
            <button type="button" class="primary small" data-make="${item.key}" ${blocked ? 'disabled' : ''} data-tip="Makes this layer on its own. About a minute a try.">Make</button>
            ${defaults.installed ? '' : '<a class="link" href="./setup.html">The sound engine is not installed. Open Setup</a>'}</div>
          ${triesHtml(item)}</div></li>`;
    }
    return `<li class="layer-row" data-row="${item.key}">${head}
      <div class="layer-lane"><div class="layer-track"><div class="layer-block${playing ? ' playing' : ''}" style="left:${block.left.toFixed(2)}%;width:${block.width.toFixed(2)}%">
          <button type="button" class="play small" data-play="${esc(item.url)}" aria-label="${playing ? 'Stop' : 'Play'} this layer alone">${playing ? STOP : PLAY}</button>${waveHtml(item.bars, 40)}</div></div>
        ${controlsHtml(item)}</div></li>`;
  };

  const addHtml = () => {
    if (!adding) {
      return `<div class="layer-add"><button type="button" class="ghost" data-add="pick">+ Add a layer</button>
        <span class="hint">Describe it, or use your own file. Starts at sets when each one comes in.</span></div>`;
    }
    const choice = licenceChoice(options, own.licence);
    const singles = kept.filter((item) => item.kind !== 'sound_set');
    return `<div class="layer-add open"><div class="kinds" role="group" aria-label="Add a layer">
        <button type="button" class="kind" data-add="describe" aria-pressed="false">Describe it</button>
        <button type="button" class="kind" data-add="file" aria-pressed="${adding === 'file'}">Use my own file</button>
        <button type="button" class="kind" data-add="kept" aria-pressed="${adding === 'kept'}">From my Library</button></div>
      ${adding === 'file' ? `<div class="layer-own">
        <label class="field"><span>Your file</span><input type="file" data-file accept=".wav,audio/wav">
          <small class="hint">A WAV up to ${options.upload_mb} MB and ${options.max_file_seconds} seconds.</small></label>
        <label class="field"><span>Licence</span><select data-licence>${options.licences.map((item) => `<option value="${esc(item.id)}" ${item.id === choice.id ? 'selected' : ''}>${esc(item.label)}</option>`).join('')}</select>
          <small class="hint" data-licence-hint>${esc(choice.hint)}</small></label>
        <label class="field"><span>Where it's from</span><input type="url" data-link placeholder="https://" value="${esc(own.link)}">
          <small class="hint">The page you got it from, so its licence can be checked later.</small></label>
        <div class="row"><button type="button" class="primary small" data-add-file>Add this file</button><button type="button" class="ghost small" data-add-cancel>Cancel</button></div></div>` : ''}
      ${adding === 'kept' ? `<div class="layer-own">${singles.length ? `<label class="field"><span>A sound you kept</span><select data-kept>${singles.map((item) => `<option value="${esc(item.id)}">${esc(item.name)} · ${esc(lengthText(item.seconds))}</option>`).join('')}</select></label>
        <div class="row"><button type="button" class="primary small" data-add-kept>Add it</button><button type="button" class="ghost small" data-add-cancel>Cancel</button></div>`
        : '<p class="hint">No kept sounds yet.</p><button type="button" class="ghost small" data-add-cancel>Cancel</button>'}</div>` : ''}</div>`;
  };

  function draw() {
    if (!options) return;
    const lanes = timeline(rows), licence = finishedLicence(rows, mix), together = mix && player.playing === mix.url;
    const waiting = notMadeCount(rows), title = layersTitle(name, rows), cut = title.lastIndexOf(' · ');
    el.innerHTML = `<div class="layers-card">
      <div class="sheet-top"><div><button type="button" class="link" data-back>← Sound</button><h2>${esc(title.slice(0, cut))} <span>${esc(title.slice(cut + 1))}</span></h2></div>
        <div class="row"><button type="button" class="ghost game" data-together aria-pressed="${!!together}" ${busy ? 'disabled' : ''}>${together ? STOP : PLAY} ${together ? 'Stop' : 'Play together'}</button>
          <button type="button" class="primary" data-save ${busy || saved ? 'disabled' : ''}>${saved ? 'Saved' : 'Save as one sound'}</button>
          <button type="button" class="ghost" data-close>Close</button></div></div>
      <p class="sound-note" data-note role="status" aria-live="polite">${esc(note)}</p>
      <label class="field layers-name"><span>Name</span><input type="text" data-name maxlength="60" placeholder="Arrow hit" value="${esc(name)}"></label>
      <div class="layer-ruler" aria-hidden="true">${lanes.marks.map((mark) => `<i style="left:${mark.at.toFixed(2)}%">${esc(mark.label)}</i>`).join('')}</div>
      <ul class="layer-rows">${rows.map((item, i) => rowHtml(item, lanes.blocks[i])).join('')}</ul>
      ${rows.length >= options.max ? `<p class="hint">A sound can have up to ${options.max} layers.</p>` : addHtml()}
      <div class="layers-all"><div><h3>All together</h3>
          ${mix ? `${waveHtml(mix.bars, 48)}<small class="meta">${esc(lengthText(mix.seconds))}${mix.turned_down_db < 0 ? ' · turned down a little so it does not distort' : ''}</small>`
          : `<p class="hint">Press Play together to hear the layers as one sound.${waiting ? ` ${waiting} not made yet: ${waiting === 1 ? 'it is' : 'they are'} left out.` : ''}</p>`}</div>
        <div class="layers-licence"><h3>Licence of the finished sound</h3>
          <b class="${licence.checked === false ? 'unchecked' : ''}">${esc(licence.name)}</b><p class="hint">${esc(licence.note)}</p></div></div>
    </div>`;
  }

  function say(text) {
    note = text || '';
    const target = el.querySelector('[data-note]');
    if (target) target.textContent = note;
  }

  async function ask(action) {
    const problem = stackProblem(rows, options);
    if (problem) { say(problem); return null; }
    say('');
    return post(`/api/sound/layers/${action}`, stackRequest(rows, name));
  }

  async function playTogether() {
    if (mix && player.playing === mix.url) { player.stop(); return; }
    if (!mix) {
      const made = await ask('mix');
      if (!made) return;
      mix = made;
    }
    await player.toggle(mix.url);
  }

  async function save() {
    busy = true;
    try {
      const made = await ask('save');
      if (!made) return;
      saved = made;
      const left = notMadeCount(rows);
      say(`Saved. Find it in your Library, under Sounds.${left ? ` ${left} layer${left === 1 ? ' was' : 's were'} not made, so left out.` : ''}`);
      on.saved?.();
    } finally {
      busy = false;
      draw();
    }
  }

  async function poll() {
    if (!open || !making) return;
    const item = row(making);
    if (!item?.job) { making = null; draw(); return; }
    try {
      item.job = await getJson(`/api/sound/${item.job.job_id}/status`);
    } catch (error) {
      say(error.message);
      making = null;
      draw();
      return;
    }
    if (['done', 'error', 'cancelled'].includes(item.job.status)) making = null;
    else timer = setTimeout(poll, 1000);
    // typing in another layer's description must not be interrupted by a redraw
    if (!el.contains(document.activeElement) || !document.activeElement.matches('textarea, input[type="text"], input[type="url"], input[type="number"]')) draw();
  }

  async function make(key) {
    const item = row(key);
    const request = layerMakeRequest(item, item.count, defaults.limits.default_seconds);
    if (!request.prompt) { say('Describe the layer first.'); return; }
    say('');
    player.stop();
    const started = await post('/api/sound', request);
    item.job = { job_id: started.job_id, status: 'queued', count: started.count, tries: [] };
    making = key;
    draw();
    poll();
  }

  function add(staged, at = null) {
    if (at) Object.assign(at, madeLayer(at, staged));
    else rows.push(newLayer(staged));
    adding = null;
    changed();
    draw();
  }

  const shut = () => { open = false; clearTimeout(timer); player.dispose(); };
  const close = () => { shut(); el.remove(); };
  el.onSheetClose = shut;

  el.addEventListener('click', async (event) => {
    const t = event.target.closest('button');
    if (!t || !options) return;
    const d = t.dataset;
    try {
      if (d.close !== undefined) close();
      else if (d.back !== undefined) { close(); on.back?.(); }
      else if (d.together !== undefined) await playTogether();
      else if (d.save !== undefined) await save();
      else if (d.play) await player.toggle(d.play);
      else if (d.make) await make(d.make);
      else if (d.stop) await post(`/api/sound/${row(d.stop).job.job_id}/cancel`);
      else if (d.use) {
        const item = row(d.use);
        add(await post('/api/sound/layers/add', { job_id: item.job.job_id, try: Number(d.try) }), item);
      } else if (d.remove) {
        if (making === d.remove) making = null;
        rows = rows.filter((item) => item.key !== d.remove);
        changed();
        draw();
      } else if (d.add === 'describe') { rows.push(newLayer()); adding = null; draw(); el.querySelector('.layer-row.todo:last-child textarea')?.focus(); }
      else if (d.add) { adding = d.add; draw(); }
      else if (d.addCancel !== undefined) { adding = null; draw(); }
      else if (d.addFile !== undefined) {
        const file = el.querySelector('[data-file]').files[0];
        const problem = fileProblem(file, options);
        if (problem) { say(problem); return; }
        say('');
        add(await sendFile('/api/sound/layers/upload', { licence: licenceChoice(options, own.licence).id, link: own.link.trim(), file }));
        own.link = '';
      } else if (d.addKept !== undefined) add(await post('/api/sound/layers/add', { sound_id: el.querySelector('[data-kept]').value }));
    } catch (error) {
      say(error.message);
    }
  });
  el.addEventListener('input', (event) => {
    const t = event.target;
    if (t.matches('[data-describe]')) row(t.dataset.describe).describe = t.value;
    else if (t.matches('[data-name]')) { name = t.value; saved = null; }
    else if (t.matches('[data-link]')) own.link = t.value;
    else if (t.matches('[data-set]')) {
      const item = row(t.dataset.key);
      item[t.dataset.set] = t.value === '' ? 0 : Number(t.value);
      changed();
      player.stop();
      const out = t.closest('label').querySelector('output');
      if (out) out.textContent = signed(item[t.dataset.set]);
      // move the block as the number changes, without redrawing the field being typed in
      const lanes = timeline(rows);
      lanes.blocks.forEach((block) => {
        const made = el.querySelector(`[data-row="${block.key}"] .layer-block`);
        if (made) { made.style.left = `${block.left.toFixed(2)}%`; made.style.width = `${block.width.toFixed(2)}%`; }
      });
    }
  });
  el.addEventListener('change', (event) => {
    const t = event.target;
    if (t.matches('[data-count]')) row(t.dataset.count).count = Number(t.value);
    else if (t.matches('[data-licence]')) { own.licence = t.value; el.querySelector('[data-licence-hint]').textContent = licenceChoice(options, own.licence).hint; }
    else if (t.matches('[data-set]')) draw();
  });
  el.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });

  (async () => {
    try {
      defaults = await getJson('/api/sound/defaults');
      options = defaults.layers;
      kept = (await getJson('/api/sound/kept')).sounds || [];
      if (sound) {
        const opened = await post(`/api/sounds/${encodeURIComponent(sound.id)}/layers`);
        name = opened.title || name;
        rows = opened.layers.map((item) => newLayer(item));
      } else if (description.trim()) {
        const split = await post('/api/sound/layers/split', { description });
        rows = layersFromSplit(split);
        note = splitLine(split);
      } else rows = [newLayer()];
      if (open) draw();
    } catch (error) {
      el.innerHTML = `<div class="layers-card"><div class="sheet-top"><h2>Layers</h2><button type="button" class="ghost" data-close>Close</button></div><p class="hint">${esc(error.message)}</p></div>`;
      el.querySelector('[data-close]').addEventListener('click', close);
    }
  })();

  return { element: el };
}
