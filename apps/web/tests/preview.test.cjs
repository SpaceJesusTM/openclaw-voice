// Live preview pane: when it opens, what it shows, and how it reloads.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { mount, decide, projectName, isCodingAgent } = require(path.join(__dirname, '..', 'preview.js'));

const ASTRA = { id: 'astra', display_name: 'Astra', topics: [{ key: 'projects.active' }, { key: 'project.latest_update' }] };
const TARI = { id: 'tari', display_name: 'Tari', topics: [{ key: 'planner.today' }] };

function fakePage() {
  const nodes = new Map();
  const element = (tag = 'div') => {
    const classes = new Set();
    const el = {
      tag, hidden: false, inert: false, textContent: '', attrs: {}, dataset: {}, handlers: {}, children: [], parent: null,
      classList: {
        add: (...c) => c.forEach((x) => classes.add(x)),
        remove: (...c) => c.forEach((x) => classes.delete(x)),
        toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
        contains: (c) => classes.has(c),
      },
      set className(v) { classes.clear(); String(v).split(/\s+/).filter(Boolean).forEach((c) => classes.add(c)); },
      get className() { return [...classes].join(' '); },
      setAttribute(k, v) { this.attrs[k] = String(v); },
      addEventListener(type, fn) { this.handlers[type] = fn; },
      append(child) { child.parent = this; this.children.push(child); },
      remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); },
      querySelectorAll(selector) {
        // Only ".preview-frame" plus one optional state class is used.
        const [, base, state] = /^\.([\w-]+)(?:\.([\w-]+))?$/.exec(selector);
        return this.children.filter((c) => c.classList.contains(base) && (!state || c.classList.contains(state)));
      },
    };
    return el;
  };
  const doc = {
    hidden: false,
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
    createElement: (tag) => element(tag),
    addEventListener() {},
  };
  const server = { up: false, probes: 0 };
  const fetchImpl = async () => { server.probes += 1; if (!server.up) throw new TypeError('Failed to fetch'); return {}; };
  const timers = [];
  const pane = mount(doc, {
    fetchImpl,
    now: () => 1000,
    setTimer: (fn) => { timers.push(fn); return timers.length; },
    clearTimer: () => {},
    everyMs: () => 1,
    stopEvery: () => {},
  });
  const runTimers = () => { while (timers.length) timers.shift()(); };
  const frames = () => doc.getElementById('preview-stage').children;
  const loadFrames = () => frames().filter((f) => f.classList.contains('pending')).forEach((f) => f.handlers.load());
  return { doc, pane, server, runTimers, frames, loadFrames, el: (id) => doc.getElementById(id) };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));
const running = (id, title = 'Create coffee shop website') => ({ job_id: id, status: 'running', title });
const done = (id) => ({ job_id: id, status: 'completed' });

test('phases follow building and liveness; dismissal hides an open pane', () => {
  assert.deepEqual(decide({ coding: false, building: true, live: true }), { phase: 'hidden', open: false });
  assert.deepEqual(decide({ coding: true, building: true, live: false }), { phase: 'building', open: true });
  assert.deepEqual(decide({ coding: true, building: true, live: true }), { phase: 'updating', open: true });
  assert.deepEqual(decide({ coding: true, building: false, live: true }), { phase: 'live', open: true });
  assert.deepEqual(decide({ coding: true, building: false, live: false }), { phase: 'hidden', open: false });
  assert.deepEqual(decide({ coding: true, building: false, live: true, dismissed: true }), { phase: 'live', open: false });
});

test('only coding agents get a preview; the project name comes from the latest update', () => {
  assert.equal(isCodingAgent(ASTRA), true);
  assert.equal(isCodingAgent(TARI), false);
  assert.equal(projectName('Latest update on Moonlight Coffee (projects/coffee-shop): Created site.'), 'Moonlight Coffee');
  assert.equal(projectName('No completed coding updates or builds have been recorded in the workspace yet.'), '');
});

test('Tari never opens the pane or probes the preview port', async () => {
  const { pane, server, el } = fakePage();
  pane.sync({ agent: TARI, jobs: [running('JT1')] });
  await flush();
  assert.equal(pane.state().open, false);
  assert.equal(server.probes, 0);
  assert.equal(el('layout').classList.contains('with-preview'), false);
});

test('a build opens the pane, the site fades in once live, and a finished job reloads it', async () => {
  const { pane, server, runTimers, frames, loadFrames, el } = fakePage();
  pane.sync({ agent: ASTRA, jobs: [running('JA1')] });
  await flush();
  assert.equal(pane.state().phase, 'building');
  assert.equal(pane.state().open, true);
  assert.equal(el('layout').classList.contains('with-preview'), true);
  assert.equal(el('preview').attrs['aria-hidden'], 'false');
  assert.equal(el('preview-caption').textContent, 'Astra is building · Create coffee shop website');
  assert.equal(frames().length, 0, 'no frame until the server answers');

  server.up = true;
  await pane.probe();
  assert.equal(pane.state().phase, 'updating');
  runTimers();
  assert.equal(frames().length, 1);
  assert.equal(frames()[0].src, 'http://127.0.0.1:18950/?v=1000');
  loadFrames();
  assert.equal(pane.state().loaded, false, 'waits a beat for the page to paint');
  runTimers();
  assert.equal(pane.state().loaded, true);
  assert.equal(el('preview').dataset.loaded, 'true');
  assert.equal(el('preview-toast').classList.contains('show'), false, 'first load is not announced');

  pane.sync({ agent: ASTRA, jobs: [done('JA1')] });
  assert.equal(pane.state().phase, 'live');
  runTimers();
  assert.equal(frames().length, 2, 'new frame loads behind the visible one');
  loadFrames();
  assert.equal(frames().filter((f) => f.classList.contains('shown')).length, 1, 'old site stays fully visible meanwhile');
  runTimers();
  assert.equal(frames().length, 1, 'old frame removed after the cross-fade');
  assert.equal(el('preview-toast').textContent, 'Updated');
});

test('an already running preview opens straight to the live site, and a changed update reloads it', async () => {
  const { pane, server, runTimers, frames, loadFrames, el } = fakePage();
  server.up = true;
  const topic = (summary) => [{ topic: 'project.latest_update', summary }];
  pane.sync({ agent: ASTRA, jobs: [], topics: topic('Latest update on Moonlight Coffee (projects/coffee-shop): Created site.') });
  await flush();
  assert.equal(pane.state().phase, 'live');
  assert.equal(el('preview-title').textContent, 'Moonlight Coffee');
  runTimers();
  loadFrames();

  pane.sync({ agent: ASTRA, jobs: [], topics: topic('Latest update on Moonlight Coffee (projects/coffee-shop): Changed the title.') });
  runTimers();
  assert.equal(frames().filter((f) => f.classList.contains('pending')).length, 1);
});

test('closing hides the pane until the next build; a stopped server closes it', async () => {
  const { pane, server, el } = fakePage();
  server.up = true;
  pane.sync({ agent: ASTRA, jobs: [running('JA1')] });
  await flush();
  el('preview-close').handlers.click();
  assert.equal(pane.state().open, false);
  assert.equal(el('preview-reopen').hidden, false, 'reopen pill offered');

  pane.sync({ agent: ASTRA, jobs: [running('JA1')] });
  assert.equal(pane.state().open, false, 'same build stays dismissed');
  pane.sync({ agent: ASTRA, jobs: [done('JA1'), running('JA2')] });
  assert.equal(pane.state().open, true, 'a new build reopens it');

  pane.sync({ agent: ASTRA, jobs: [done('JA1'), done('JA2')] });
  server.up = false;
  await pane.probe();
  assert.equal(pane.state().open, true, 'one missed probe is tolerated');
  await pane.probe();
  assert.equal(pane.state().open, false);
  assert.equal(el('preview-reopen').hidden, true);
});

test('switching to Tari closes the pane and drops the site', async () => {
  const { pane, server, runTimers, frames, loadFrames, el } = fakePage();
  server.up = true;
  pane.sync({ agent: ASTRA, jobs: [] });
  await flush();
  runTimers();
  loadFrames();
  assert.equal(frames().length, 1);
  pane.sync({ agent: TARI, jobs: [] });
  assert.equal(pane.state().open, false);
  assert.equal(frames().length, 0);
  assert.equal(el('layout').classList.contains('with-preview'), false);
});
