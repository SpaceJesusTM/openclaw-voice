// Agent selector and clean session/state isolation when switching between Tari and Astra.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function setupEnv({ team = false } = {}) {
  const nodes = new Map();
  const styles = new Map();
  const element = () => ({
    checked: false,
    disabled: false,
    hidden: false,
    textContent: '',
    innerHTML: '',
    className: '',
    handlers: {},
    children: [],
    dataset: {},
    style: { setProperty() {} },
    classList: { added: [], add(c) { this.added.push(c); }, remove() {} },
    addEventListener(type, fn) { this.handlers[type] = fn; },
    setAttribute() {},
    setPointerCapture() {},
    pause() { this.paused = true; },
    querySelector() { return Object.assign(element(), { remove() {} }); },
    querySelectorAll() { return []; },
    append(child) { this.children.push(child); },
    prepend(child) { this.children.unshift(child); },
  });
  const window = element();
  const document = Object.assign(element(), {
    title: 'ClawVoice',
    documentElement: {
      style: {
        setProperty(k, v) { styles.set(k, v); },
      },
    },
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, element());
      return nodes.get(id);
    },
    createElement() { return element(); },
    querySelectorAll() { return []; },
  });

  const stateByAgent = {
    tari: {
      agent: { id: 'tari', display_name: 'Tari' },
      gateway: { active_transport: 'ws' },
      jobs: [{ job_id: 'JT1', agent_id: 'tari', title: 'Add Dentist event', status: 'running', created_at: '2026-10-24T10:00:00Z', age: '2s' }],
      cache: {
        topics: [{ topic: 'planner.today', state: 'fresh', age_seconds: 5, summary: 'Today schedule' }],
        metrics: { read_ms_p50: 4, hits_fresh: 2 },
      },
    },
    astra: {
      agent: { id: 'astra', display_name: 'Astra' },
      gateway: { active_transport: 'http' },
      jobs: [{ job_id: 'JA1', agent_id: 'astra', title: 'Create coffee shop website', status: 'completed', verification: 'verified', created_at: '2026-10-24T10:01:00Z', age: '5s' }],
      cache: {
        topics: [
          { topic: 'projects.active', state: 'fresh', age_seconds: 3, summary: 'Moonlight Coffee' },
          { topic: 'project.latest_update', state: 'fresh', age_seconds: 3, summary: 'Updated title' },
        ],
        metrics: { read_ms_p50: 6, hits_fresh: 4 },
      },
    },
  };

  let currentUrl = 'http://127.0.0.1:7860/';
  const context = vm.createContext({
    window,
    document,
    URL,
    URLSearchParams,
    location: { search: '', href: currentUrl },
    history: { replaceState(_s, _t, url) { currentUrl = url; } },
    crypto: { randomUUID: () => 'uuid-1' },
    fetch: async (url) => {
      if (url === '/api/agents') {
        return {
          ok: true,
          json: async () => ({
            default: 'tari',
            model: 'higgs-realtime-1.5-preview',
            agents: [
              { id: 'tari', display_name: 'Tari', emoji: '🟢', color: '#22c55e', role: 'Dependable personal coordinator.' },
              { id: 'astra', display_name: 'Astra', emoji: '🔵', color: '#3b82f6', role: 'Practical coding assistant.' },
            ],
            ...(team ? { team: { available: true, agents: ['tari', 'astra'] } } : {}),
          }),
        };
      }
      if (url.startsWith('/api/state?agent=')) {
        const id = decodeURIComponent(url.split('=')[1]);
        return { ok: true, json: async () => stateByAgent[id] };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    },
    navigator: { mediaDevices: { async getUserMedia() { return {}; } } },
    setInterval: () => 1,
    clearInterval: () => {},
  });

  const source = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8').replace(/init\(\);\s*$/, '');
  vm.runInContext(source, context);
  return { nodes, styles, getUrl: () => currentUrl, run: (code) => vm.runInContext(code, context) };
}

test('Agent selector renders Tari and Astra and switching isolates session, transcript, jobs, and cache topics', async () => {
  const env = setupEnv();
  await env.run('init()');

  // Verify initial Tari state and selector rendering
  assert.equal(env.run('ui.agent.id'), 'tari');
  assert.equal(env.styles.get('--accent'), '#22c55e');
  const selectorHtml = env.nodes.get('agent-selector').innerHTML;
  assert.match(selectorHtml, /Tari/);
  assert.match(selectorHtml, /Personal Assistant/);
  assert.match(selectorHtml, /Astra/);
  assert.match(selectorHtml, /Coding Assistant/);
  assert.match(env.nodes.get('topics').innerHTML, /planner\.today/);
  assert.match(env.nodes.get('jobs').innerHTML, /Add Dentist event/);

  // Simulate an active Tari voice session with audio track and transcript turn
  env.run(`
    let trackStopped = false;
    let disconnectSent = false;
    const mockTrack = { enabled: true, stop() { trackStopped = true; } };
    ui.stream = { getAudioTracks: () => [mockTrack], getTracks: () => [mockTrack] };
    ui.dc = { readyState: 'open', send(msg) { if (msg.includes('disconnect-bot')) disconnectSent = true; }, close() {} };
    ui.pc = { close() {} };
    ui.connected = true;
    addTurn('user', 'Schedule dentist tomorrow');
  `);

  // Switch to Astra via selector click handler (with connect stubbed so we test state isolation deterministically)
  env.run('connect = async () => { ui.reconnectedTo = ui.agent.id; };');
  await env.run('selectAgent("astra")');

  assert.equal(env.run('trackStopped'), true, 'previous microphone stream track must be stopped');
  assert.equal(env.run('disconnectSent'), true, 'previous voice session must receive disconnect-bot');
  assert.equal(env.run('ui.agent.id'), 'astra');
  assert.equal(env.run('ui.reconnectedTo'), 'astra');
  assert.equal(env.styles.get('--accent'), '#3b82f6');
  assert.match(env.getUrl(), /\?agent=astra/);

  // Verify Tari's jobs, topics, and transcript do NOT leak into Astra's view
  assert.doesNotMatch(env.nodes.get('jobs').innerHTML, /Add Dentist event/);
  assert.match(env.nodes.get('jobs').innerHTML, /Create coffee shop website/);
  assert.doesNotMatch(env.nodes.get('topics').innerHTML, /planner\.today/);
  assert.match(env.nodes.get('topics').innerHTML, /projects\.active/);
  assert.doesNotMatch(env.nodes.get('transcript').innerHTML, /Schedule dentist tomorrow/);
  assert.match(env.nodes.get('transcript').innerHTML, /fictional coffee shop/);

  // Verify a late job event from Tari is ignored while Astra is selected
  env.run('upsertJob({ job_id: "JT2", agent_id: "tari", title: "Late Tari job", status: "completed" }, "job.finished")');
  assert.equal(env.run('ui.jobs.has("JT2")'), false);
});

test('Team mode shows both agents, labels speakers, owners and routing, and connects once with mode team', async () => {
  const env = setupEnv({ team: true });
  await env.run('init()');
  assert.match(env.nodes.get('agent-selector').innerHTML, /data-agent="team"/);

  env.run('connect = async () => { ui.connectedMode = isTeam() ? "team" : ui.agent.id; };');
  await env.run('selectAgent("team")');
  assert.equal(env.run('ui.mode'), 'team');
  assert.match(env.getUrl(), /\?agent=team/);
  assert.equal(env.nodes.get('team-seats').hidden, false);
  assert.equal(env.nodes.get('orb').hidden, false);
  assert.match(env.nodes.get('team-seats').innerHTML, /Tari/);
  assert.match(env.nodes.get('team-seats').innerHTML, /Astra/);
  // Both agents' jobs and cache topics, each labelled with its owner
  const jobs = env.nodes.get('jobs').innerHTML;
  assert.match(jobs, /Add Dentist event/);
  assert.match(jobs, /Create coffee shop website/);
  assert.match(jobs, /class="owner"[^>]*>Tari</);
  assert.match(jobs, /class="owner"[^>]*>Astra</);
  assert.match(env.nodes.get('topics').innerHTML, /Tari · planner\.today/);
  assert.match(env.nodes.get('topics').innerHTML, /Astra · projects\.active/);
  assert.match(env.nodes.get('transcript').innerHTML, /address an agent by name/);

  // Replies are labelled with their speaker; a cut reply is marked
  env.run(`
    ui.connected = true;
    onServerMessage({ kind: 'clawvoice.team.user', text: 'Hey Tari, what is on my planner today?', agent: 'tari', status: 'routed', how: 'name' });
    onServerMessage({ kind: 'clawvoice.team.reply', reply: 2, agent: 'tari', reply_kind: 'answer', phase: 'speaking', text: 'You have three events.' });
    onServerMessage({ kind: 'clawvoice.team.reply', reply: 3, agent: 'astra', reply_kind: 'answer', phase: 'speaking', text: 'The site title changed.' });
    onServerMessage({ kind: 'clawvoice.team.reply', reply: 3, agent: 'astra', reply_kind: 'answer', phase: 'cut', text: 'The site…' });
    onServerMessage({ kind: 'clawvoice.team.user', text: 'What is the weather?', agent: null, status: 'unaddressed' });
  `);
  const turns = env.nodes.get('transcript').children;
  const bots = turns.filter((t) => t.className.startsWith('turn bot'));
  assert.equal(bots.length, 2);
  assert.match(bots[0].innerHTML, /Tari/);
  assert.match(bots[1].innerHTML, /Astra/);
  assert.ok(bots[1].classList.added.includes('interrupted'));
  const unaddressed = turns.filter((t) => t.classList.added.includes('unaddressed'));
  assert.equal(unaddressed.length, 1);

  // A job event from either team agent is kept, keyed by its owner
  env.run('upsertJob({ job_id: "JT2", agent_id: "tari", title: "Late Tari job", status: "completed" }, "job.finished")');
  assert.equal(env.run('ui.jobs.has("tari:JT2")'), true);
});
