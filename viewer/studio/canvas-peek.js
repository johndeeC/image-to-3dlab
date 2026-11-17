// Quick view on the canvas: click a box's result to see it large, a picture as it is, a 3D model
// you can turn. Buttons hand back to the pane: use this try, or open it in the studio's viewer.
// The framing maths is fitDistance (canvas-view.js, Node-tested); this file is only the drawing.

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { fitDistance } from './canvas-view.js';

const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const PICTURE = /\.(png|jpe?g|webp)$/i;
const FOV = 35;

/**
 * Show `file` over `container`. `url(file)` is where the studio serves it. `actions` are
 * [label, hint, fn] buttons. `ask` adds a line to say what is wrong ({ placeholder, label, hint,
 * send(text) }); `note` is a line of help under it. Returns close(); Esc and the backdrop close it too.
 */
export function openPeek(container, { file, url, title, actions = [], ask = null, note = '', onClose }) {
  const box = document.createElement('div');
  box.className = 'canvas-peek';
  box.innerHTML = `<div class="peek-card" role="dialog" aria-label="${esc(title)}">
      <div class="row"><b>${esc(title)}</b><button type="button" class="ghost small" data-peek-close>Close</button></div>
      <div class="peek-stage">${PICTURE.test(file) ? `<img src="${esc(url(file))}" alt="${esc(title)}">` : '<p class="hint">Loading the 3D model…</p>'}</div>
      ${ask ? `<form class="row peek-ask"><input type="text" maxlength="300" placeholder="${esc(ask.placeholder)}" aria-label="What is wrong with it">
        <button type="submit" class="primary small" title="${esc(ask.hint)}">${esc(ask.label)}</button></form>` : ''}
      ${note ? `<small class="hint">${esc(note)}</small>` : ''}
      <div class="row end">${actions.map(([label, hint], i) => `<button type="button" class="${i === 0 ? 'primary' : 'ghost'} small" data-peek-act="${i}" title="${esc(hint)}">${esc(label)}</button>`).join('')}</div>
    </div>`;
  container.appendChild(box);
  let stop = () => {};

  if (!PICTURE.test(file)) stop = showModel(box.querySelector('.peek-stage'), url(file));

  const close = () => { stop(); box.remove(); onClose?.(); };
  box.addEventListener('click', (event) => {
    if (event.target === box || event.target.closest('[data-peek-close]')) { close(); return; }
    const act = event.target.closest('[data-peek-act]');
    if (act) { close(); actions[Number(act.dataset.peekAct)][2](); }
  });
  box.querySelector('.peek-ask')?.addEventListener('submit', (event) => {
    event.preventDefault();
    const text = event.target.querySelector('input').value.trim();
    if (!text) { event.target.querySelector('input').focus(); return; }
    close();
    ask.send(text);
  });
  box.addEventListener('pointerdown', (event) => event.stopPropagation());
  box.addEventListener('wheel', (event) => event.stopPropagation());
  box.addEventListener('keydown', (event) => { if (event.key === 'Escape') { event.stopPropagation(); close(); } });
  box.querySelector('[data-peek-close]').focus();
  return close;
}

function showModel(stage, href) {
  const width = stage.clientWidth || 480, height = stage.clientHeight || 360;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  renderer.setSize(width, height);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  const camera = new THREE.PerspectiveCamera(FOV, width / height, 0.01, 1000);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 1.2;
  let frame = 0, alive = true, model = null;
  const tick = () => {
    if (!alive) return;
    controls.update();
    renderer.render(scene, camera);
    frame = requestAnimationFrame(tick);
  };
  new GLTFLoader().load(href, (gltf) => {
    if (!alive) return;
    model = gltf.scene;
    const bounds = new THREE.Box3().setFromObject(model);
    const size = bounds.getSize(new THREE.Vector3()), centre = bounds.getCenter(new THREE.Vector3());
    model.position.sub(centre);
    scene.add(model);
    const distance = fitDistance(Math.max(size.x, size.y, size.z), FOV);
    camera.position.set(distance * 0.6, distance * 0.35, distance * 0.75);
    camera.near = distance / 100;
    camera.far = distance * 100;
    camera.updateProjectionMatrix();
    controls.target.set(0, 0, 0);
    stage.replaceChildren(renderer.domElement);
    tick();
  }, undefined, () => { if (alive) stage.innerHTML = '<p class="bad">This model could not be opened.</p>'; });
  controls.addEventListener('start', () => { controls.autoRotate = false; });

  return () => {
    alive = false;
    cancelAnimationFrame(frame);
    controls.dispose();
    model?.traverse((o) => {
      o.geometry?.dispose();
      for (const m of [o.material].flat()) {
        if (!m) continue;
        for (const v of Object.values(m)) if (v?.isTexture) v.dispose();
        m.dispose();
      }
    });
    scene.environment?.dispose();
    pmrem.dispose();
    renderer.dispose();
    renderer.forceContextLoss?.();
  };
}
