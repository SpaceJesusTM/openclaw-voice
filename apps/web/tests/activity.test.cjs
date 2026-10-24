// "Now": built only from real events, newest last, cleared per request.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { mount, describeCall, describeResult } = require(path.join(__dirname, '..', 'activity.js'));

const list = () => ({ innerHTML: '' });

test('a spoken request: heard, thinking, lookup resolved with cache and timing, speaking', () => {
  const el = list();
  const now = mount(el);
  now.newTurn('Heard “what project are you on?”');
  now.thinking();
  now.tool({ phase: 'started', name: 'get_context', call_id: 'c1', arguments: { topic: 'projects.active' } });
  assert.equal(now.items().at(-1).text, 'Looking up the active project');
  assert.equal(now.items().at(-1).live, true);
  now.tool({ phase: 'result', name: 'get_context', call_id: 'c1', status: 'ok', cache: 'fresh', read_ms: 3.6, topic: 'projects.active' });
  now.speaking();
  assert.deepEqual(now.items().map((i) => i.text), [
    'Heard “what project are you on?”',
    'Thinking',
    'Checked the active project · fresh · 4 ms',
    'Speaking',
  ]);
  assert.deepEqual(now.items().map((i) => i.live), [false, false, false, true], 'only the current step is highlighted');
  now.settle();
  assert.ok(now.items().every((i) => !i.live));
  assert.match(el.innerHTML, /Checked the active project/);
});

test('a handoff and the coding agent tool steps appear while the task runs', () => {
  const now = mount(list());
  now.newTurn('Heard “build a coffee site”');
  now.tool({ phase: 'started', name: 'delegate_task', call_id: 'c2', arguments: { request: 'Build a coffee site', title: 'Moonlight Coffee' } });
  now.tool({ phase: 'result', name: 'delegate_task', call_id: 'c2', status: 'accepted', job_id: 'J7' });
  now.job({ job_id: 'J7', title: 'Moonlight Coffee', status: 'accepted' }, 'job.accepted');
  now.jobTools([{ job_id: 'J7', status: 'running', tools_seen: ['write'] }]);
  now.jobTools([{ job_id: 'J7', status: 'running', tools_seen: ['write', 'exec: ./bin/astra-project check moonlight-coffee'] }]);
  now.jobTools([{ job_id: 'J7', status: 'running', tools_seen: ['write', 'exec: ./bin/astra-project check moonlight-coffee'] }]);
  now.job({ job_id: 'J7', title: 'Moonlight Coffee', status: 'completed' }, 'job.finished');
  assert.deepEqual(now.items().map((i) => i.text), [
    'Heard “build a coffee site”',
    'Handed off to the agent · J7',
    'Started: Moonlight Coffee',
    'write',
    'exec: ./bin/astra-project check moonlight-coffee',
    'Finished: Moonlight Coffee',
  ]);
  assert.equal(now.items()[4].mono, true, 'agent tool labels render as code');
});

test('tasks that already ended add no history; a new request clears the list; it never grows past seven', () => {
  const now = mount(list());
  now.jobTools([{ job_id: 'OLD', status: 'completed', tools_seen: ['write', 'exec: ls'] }]);
  assert.equal(now.items().length, 0);
  for (let i = 0; i < 12; i++) now.tool({ phase: 'started', name: 'get_context', call_id: `c${i}`, arguments: { topic: `t${i}` } });
  assert.equal(now.items().length, 7);
  now.newTurn('Read “hi”');
  assert.deepEqual(now.items().map((i) => i.text), ['Read “hi”']);
});

test('failures read honestly', () => {
  assert.equal(describeResult('get_context', { status: 'error', topic: 'calendar.today' }).text, "Couldn't check today's calendar");
  assert.equal(describeResult('delegate_task', { status: 'error' }).text, "Couldn't hand off the task");
  assert.equal(describeCall('check_tasks').text, 'Checking your tasks');
});
