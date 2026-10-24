import { mkdir, writeFile } from 'node:fs/promises';
import { loadLocalEnv, readConfig, mintSecret, root } from '../server/config';
import { closeMessage, debugEvent, object, parseEvent, type WireEvent } from '../src/protocol';
import { Session, type ToolTrace } from '../src/session';
import { sessionConfig } from '../src/sessionConfig';
import planner from '../fixtures/planner.json';

const args = process.argv.slice(2);
if (args.includes('--help') || !args.includes('--live')) {
  console.log(`Higgs 1.5 probe (no API requests without --live)
  npm run probe -- --live
  npm run probe -- --live --text "Say hello" --voice default
  npm run probe -- --live --search "Search for the latest Boson AI news"
  Optional: --timeout 60000 (milliseconds per turn, default 45000)
Runs a text/audio turn, then a real planner function turn. Search adds a native
server-side search turn. Saves sanitized events and a PASS/FAIL report in captures/.
Microphone hardware, audible playback, and barge-in remain browser/manual checks.`);
} else {
  await main().catch(error => { console.error(`PROBE BLOCKED/FAILED: ${error instanceof Error ? error.message : String(error)}`); process.exitCode = 1; });
}

async function main(): Promise<void> {
  const known = new Set(['--live', '--text', '--voice', '--search', '--timeout']);
  for (let i = 0; i < args.length; i++) {
    if (!known.has(args[i])) throw new Error(`Unknown option: ${args[i]}`);
    if (args[i] !== '--live') {
      if (!args[i + 1] || args[i + 1].startsWith('--')) throw new Error(`Missing value for ${args[i]}`);
      i++;
    }
  }
  const option = (name: string, fallback: string) => {
    const i = args.indexOf(name); return i < 0 ? fallback : args[i + 1];
  };
  const timeout = Number(option('--timeout', '45000'));
  if (!Number.isInteger(timeout) || timeout < 1000 || timeout > 180000) throw new Error('--timeout must be 1000–180000 ms');
  loadLocalEnv();
  const config = readConfig();
  // No direct-key WebSocket authentication: exercise the same secret flow as the UI.
  const secret = await mintSecret(config);
  const events: unknown[] = [], tools: ToolTrace[] = [], checks: unknown[] = [];
  const started = Date.now();
  let model = '(unacknowledged)', audioBytes = 0, audioChunks = 0;
  let failure: Error | null = null;
  let intentionalClose = false;
  let readyResolve!: () => void, readyReject!: (error: Error) => void;
  const ready = new Promise<void>((resolve, reject) => { readyResolve = resolve; readyReject = reject; });
  let turn: { kind: string; transcripts: Map<string, string>; searchCalls: Set<string>; searchResults: Set<string>;
    resolve: () => void; reject: (error: Error) => void } | null = null;
  const ws = new WebSocket(config.wsUrl, ['realtime', `bai-client-secret.${secret.value}`]);
  const fail = (message: string) => {
    failure ??= new Error(message); readyReject(failure); turn?.reject(failure);
  };
  const send = (event: WireEvent) => {
    if (ws.readyState !== WebSocket.OPEN) throw new Error('Socket is not open');
    events.push({ direction: 'send', event: debugEvent(event) });
    ws.send(JSON.stringify(event));
  };
  const session = new Session({ send,
    ready: (resolved, value) => { model = resolved; console.log(`VERIFIED model: ${model}\nResolved session: ${JSON.stringify(debugEvent({ type: 'session.created', session: value }).session)}`); readyResolve(); },
    error: fail,
    tool: trace => { tools.push(trace); console.log(`TOOL ${JSON.stringify(trace)}`); },
    event: event => {
      if (event.type === 'response.done' && object(event.response).status !== 'completed') {
        fail(`Unexpected response completion: ${JSON.stringify(event.response)}`);
      }
      if (event.type === 'response.output_audio.delta' && typeof event.delta === 'string') {
        const length = Buffer.from(event.delta, 'base64').length;
        if (!length || length % 2) { fail('Invalid PCM16 audio chunk'); return; }
        audioChunks++; audioBytes += length;
      }
      if (!turn) return;
      const id = String(event.item_id ?? 'unknown');
      if (event.type === 'response.output_audio_transcript.delta') turn.transcripts.set(id, (turn.transcripts.get(id) ?? '') + String(event.delta ?? ''));
      if (event.type === 'response.output_audio_transcript.done') turn.transcripts.set(id, String(event.transcript ?? ''));
      const item = object(event.item);
      if (event.type === 'response.output_item.added' && item.executor === 'server' && item.name === 'web_search') turn.searchCalls.add(String(item.call_id));
      if (event.type === 'conversation.item.added' && item.type === 'function_call_output' && turn.searchCalls.has(String(item.call_id))) turn.searchResults.add(String(item.call_id));
    },
    idle: () => {
      if (turn && (turn.kind !== 'search' || turn.searchResults.size > 0)) turn.resolve();
    },
  });
  const ackTimer = setTimeout(() => fail('No verified session acknowledgement within 20 seconds'), 20000);
  ws.addEventListener('open', () => send(sessionConfig({ model: config.model,
    voice: option('--voice', config.voice), webSearch: args.includes('--search') })));
  ws.addEventListener('message', ({ data }) => {
    try {
      const event = parseEvent(String(data));
      const safe = debugEvent(event); events.push({ direction: 'recv', elapsedMs: Date.now() - started, event: safe });
      if (event.type !== 'response.output_audio.delta') console.log(`RECV ${JSON.stringify(safe)}`);
      session.receive(event);
    } catch (error) { fail(`Protocol error: ${String(error)}`); }
  });
  ws.addEventListener('error', () => fail('WebSocket transport error; inspect close code and network/authentication'));
  ws.addEventListener('close', event => {
    console.log(closeMessage(event.code, event.reason));
    if (!intentionalClose) fail(`Unexpected ${closeMessage(event.code, event.reason)}`);
  });
  try {
    console.log(`Connecting to ${config.wsUrl} (short-lived secret minted; credential omitted)`);
    await ready; clearTimeout(ackTimer);
    await runTurn('text', option('--text', 'Say: Higgs preview connection verified.'));
    await runTurn('planner', "What's on my planner today?");
    if (args.includes('--search')) await runTurn('search', option('--search', ''));
    if (failure) throw failure;
    console.log(`PASS: ${checks.length} live turns, exact preview model, ${audioChunks} audio chunks.`);
  } catch (error) {
    failure = error instanceof Error ? error : new Error(String(error));
    console.error(`FAIL: ${failure.message}`); process.exitCode = 1;
  } finally {
    clearTimeout(ackTimer); intentionalClose = true; session.dispose(); ws.close(1000, 'probe complete');
    const report = { status: failure ? 'FAIL' : 'PASS', model, configuredModel: config.model,
      websocketUrl: config.wsUrl, error: failure?.message, checks, audioChunks, audioSeconds: audioBytes / 48000,
      limitations: ['No physical microphone, audible speaker, or interruption verification'], tools, events };
    await mkdir(`${root}captures`, { recursive: true });
    const path = `${root}captures/probe-${new Date().toISOString().replaceAll(':', '-')}.json`;
    await writeFile(path, JSON.stringify(report, null, 2)); console.log(`Report: ${path}`);
  }

  async function runTurn(kind: string, prompt: string): Promise<void> {
    const initialBytes = audioBytes, initialTools = tools.length;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const completed = new Promise<void>((resolve, reject) => {
      turn = { kind, transcripts: new Map(), searchCalls: new Set(), searchResults: new Set(), resolve, reject };
      timer = setTimeout(() => reject(new Error(`${kind} turn timed out after ${timeout} ms`)), timeout);
    });
    // Capture state before sending so a fast response cannot race the waiter.
    const current = turn!;
    try { session.sendText(prompt); await completed; }
    finally { clearTimeout(timer); turn = null; }
    if (failure) throw failure;
    const spoken = [...current.transcripts.values()].join(' ').trim();
    if (!spoken || audioBytes <= initialBytes) throw new Error(`${kind}: missing spoken transcript or generated audio`);
    if (kind === 'planner') {
      const local = tools.slice(initialTools).filter(trace => trace.name === 'get_demo_planner' && trace.status === 'completed' && trace.result && !trace.result.error);
      if (!local.length) throw new Error('Planner prompt did not execute get_demo_planner successfully');
      const normalize = (text: string) => text.toLowerCase().replace(/[^a-z0-9]/g, '');
      if (!normalize(spoken).includes(normalize(planner.entries[0].title))) throw new Error('Planner answer did not include the distinctive first fixture title');
    }
    if (kind === 'search' && (!current.searchCalls.size || !current.searchResults.size)) throw new Error('Native server web_search call/result not observed');
    checks.push({ kind, status: 'PASS', prompt, transcript: spoken, generatedAudioBytes: audioBytes - initialBytes });
    console.log(`PASS ${kind}: ${spoken}`);
  }
}
