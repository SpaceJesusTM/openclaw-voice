import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Session, type ToolTrace } from '../src/session';
import { dispatchTool, type ToolHandler, type ToolResult } from '../src/tools';
import { PREVIEW_MODEL, type WireEvent } from '../src/protocol';

const tick = () => new Promise(resolve => setImmediate(resolve));
const call = (id = 'call1', overrides: Record<string, unknown> = {}): WireEvent => ({
  type: 'response.function_call_arguments.done', response_id: 'r1', call_id: id,
  name: 'get_demo_planner', arguments: '{"day":"today"}', ...overrides,
});
const done = (id = 'r1', output: unknown[] = []): WireEvent => ({ type: 'response.done', response: { id, status: 'completed', output } });
function harness(handler?: ToolHandler) {
  const sent: WireEvent[] = [], traces: ToolTrace[] = [], errors: string[] = [];
  const session = new Session({ send: e => sent.push(e), tool: e => traces.push(e), error: e => errors.push(e) }, handler);
  session.receive({ type: 'session.created', session: { model: PREVIEW_MODEL } });
  session.receive({ type: 'response.created', response: { id: 'r1' } });
  return { session, sent, traces, errors };
}

test('real demo handler validates arguments and returns distinctive editable data', async () => {
  const result = await dispatchTool('get_demo_planner', { day: 'today' });
  assert.match(result.summary, /Copper Otter rehearsal/); assert.match(result.summary, /NEBULA-742/);
  for (const args of [null, [], {}, { day: 'tomorrow' }, { day: 'today', extra: true }]) {
    assert.equal((await dispatchTool('get_demo_planner', args)).error, 'invalid_arguments');
  }
  assert.equal((await dispatchTool('other', {})).error, 'unknown_tool');
});

test('duplicate args and response.done fallback execute one handler and send one result', async () => {
  let runs = 0;
  const h = harness(async (name, args) => { runs++; return dispatchTool(name, args); });
  h.session.receive(call()); h.session.receive(call());
  h.session.receive(done('r1', [{ ...call(), type: 'function_call' }]));
  await tick();
  assert.equal(runs, 1);
  assert.deepEqual(h.sent.map(e => e.type), ['conversation.item.create', 'response.create']);
  const item = h.sent[0].item as Record<string, unknown>;
  assert.equal(item.call_id, 'call1'); assert.equal(typeof item.output, 'string');
  assert.match(String(item.output), /NEBULA-742/);
  assert.equal(h.traces.filter(t => t.status === 'completed').length, 1);
  assert.deepEqual(h.errors, []);
});

test('multiple parallel calls produce all outputs followed by exactly one continuation', async () => {
  let resolve!: (result: ToolResult) => void;
  const slow = new Promise<ToolResult>(r => { resolve = r; });
  let runs = 0;
  const h = harness(async () => ++runs === 1 ? slow : { summary: 'second' });
  h.session.receive(call('one')); h.session.receive(call('two')); h.session.receive(done());
  await tick(); assert.equal(h.sent.length, 0);
  resolve({ summary: 'first' }); await tick();
  assert.deepEqual(h.sent.map(e => e.type), ['conversation.item.create', 'conversation.item.create', 'response.create']);
});

test('response.done alone can announce a local function', async () => {
  const h = harness(); h.session.receive(done('r1', [{ ...call(), type: 'function_call' }]));
  await tick(); assert.equal(h.sent.length, 2);
});

test('malformed JSON, non-object args, unknown functions, and thrown handlers return errors', async () => {
  for (const args of ['{', '[]', 'null', '"hello"']) {
    let runs = 0;
    const h = harness(async () => { runs++; return { summary: 'unexpected' }; });
    h.session.receive(call('bad', { arguments: args })); h.session.receive(done()); await tick();
    assert.equal(runs, 0); assert.equal(h.traces.at(-1)?.status, 'error'); assert.equal(h.sent.length, 2);
  }
  const unknown = harness(); unknown.session.receive(call('u', { name: 'other' })); unknown.session.receive(done()); await tick();
  assert.equal(unknown.traces.at(-1)?.result?.error, 'unknown_tool');
  const throws = harness(async () => { throw new Error('demo failure'); });
  throws.session.receive(call()); throws.session.receive(done()); await tick();
  assert.equal(throws.traces.at(-1)?.result?.error, 'demo failure'); assert.equal(throws.sent.length, 2);
});

test('server-side web search never invokes local handler or requests continuation', async () => {
  let runs = 0;
  const h = harness(async () => { runs++; return { summary: 'wrong' }; });
  h.session.receive({ type: 'response.output_item.added', item: { call_id: 's', name: 'web_search', executor: 'server' } });
  h.session.receive(call('s', { name: 'web_search' }));
  h.session.receive(done('r1', [{ ...call('s'), name: 'web_search', executor: 'server', type: 'function_call' }]));
  await tick(); assert.equal(runs, 0); assert.deepEqual(h.sent, []);
});

test('barge-in does not cancel a slow tool; delivered output waits for user turn', async () => {
  let finish!: (result: ToolResult) => void;
  const h = harness(() => new Promise(resolve => { finish = resolve; }));
  h.session.receive(call()); h.session.receive(done());
  h.session.receive({ type: 'input_audio_buffer.speech_started' });
  finish({ summary: 'background task completed' }); await tick();
  assert.deepEqual(h.sent.map(e => e.type), ['conversation.item.create']);
  h.session.receive({ type: 'input_audio_buffer.speech_stopped' });
  h.session.receive({ type: 'response.created', response: { id: 'r2' } });
  h.session.receive(done('r2')); await tick();
  assert.equal(h.sent.length, 1); assert.equal(h.session.busy, false);
});

test('tool finishing during a new response delivers result then continues after that response', async () => {
  let finish!: (result: ToolResult) => void;
  const h = harness(() => new Promise(resolve => { finish = resolve; }));
  h.session.receive(call()); h.session.receive(done());
  h.session.receive({ type: 'response.created', response: { id: 'r2' } });
  finish({ summary: 'finished' }); await tick();
  assert.equal(h.sent.length, 1);
  h.session.receive(done('r2')); assert.equal(h.sent.at(-1)?.type, 'response.create');
});

test('disconnect lets handler finish but suppresses stale socket outputs', async () => {
  let finish!: (result: ToolResult) => void;
  const h = harness(() => new Promise(resolve => { finish = resolve; }));
  h.session.receive(call()); h.session.receive(done()); h.session.dispose();
  finish({ summary: 'completed independently' }); await tick();
  assert.equal(h.traces.at(-1)?.status, 'completed'); assert.deepEqual(h.sent, []);
});

test('unverified models and API errors fail visibly and block typed requests', () => {
  for (const event of [
    { type: 'session.created', session: { model: 'higgs-realtime' } },
    { type: 'session.updated', session: {} },
    { type: 'error', error: { code: 'invalid_model' } },
    { type: 'response.done', response: { id: 'r1', status: 'failed', status_details: { error: 'bad request' } } },
  ]) {
    const h = harness(); h.session.receive(event);
    assert.equal(h.session.ready, false); assert.equal(h.errors.length, 1);
    assert.throws(() => h.session.sendText('hello'), /verified/);
  }
});

test('typed messages wait for verification and create exactly one response', () => {
  const sent: WireEvent[] = [];
  const session = new Session({ send: e => sent.push(e), error: () => {} });
  assert.throws(() => session.sendText('hello'), /verified/);
  session.receive({ type: 'session.created', session: { model: PREVIEW_MODEL } });
  session.sendText('hello', 'user1');
  assert.deepEqual(sent.map(e => e.type), ['conversation.item.create', 'response.create']);
  assert.throws(() => session.sendText('overlap'), /current turn/);
});
