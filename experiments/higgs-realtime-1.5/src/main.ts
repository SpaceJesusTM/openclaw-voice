import './style.css';
import { AudioEngine } from './audio';
import { Interruptions } from './interruptions';
import { AUDIO_RATE, closeMessage, debugEvent, object, parseEvent, requirePreview, type WireEvent } from './protocol';
import { Session, type ToolTrace } from './session';
import { sessionConfig } from './sessionConfig';
import { encodePcm } from './pcm';

const el = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const button = (id: string) => el<HTMLButtonElement>(id);
const input = (id: string) => el<HTMLInputElement>(id);
const status = el('status');
let config: { model: string; wsUrl: string; voice: string; keyAvailable: boolean } | null = null;
let connection: { ws: WebSocket | null; session: Session; audio: AudioEngine; abort: AbortController } | null = null;
let chunks = 0, seconds = 0, results = 0;
let currentResponse = '';
const rows = new Map<string, { root: HTMLElement; body: HTMLElement; generated: HTMLElement; text: string; interrupted: boolean }>();
const toolRows = new Map<string, HTMLDetailsElement>();

function notice(message: string, error = false): void {
  el('notice').textContent = message; el('notice').dataset.error = String(error);
}
function log(direction: string, event: WireEvent): void {
  const node = document.createElement('details');
  node.dataset.direction = direction;
  const summary = document.createElement('summary');
  summary.textContent = `${new Date().toLocaleTimeString()} ${direction} · ${event.type}`;
  const pre = document.createElement('pre'); pre.textContent = JSON.stringify(debugEvent(event), null, 2);
  node.append(summary, pre); el('log').append(node);
  while (el('log').childElementCount > 300) el('log').firstChild?.remove();
  el('log').scrollTop = el('log').scrollHeight;
}
function updateControls(): void {
  const verified = Boolean(connection?.session.ready);
  button('connect').disabled = Boolean(connection) || !config;
  button('disconnect').disabled = !connection;
  input('voice').disabled = Boolean(connection); input('web-search').disabled = Boolean(connection);
  button('mic').disabled = !verified;
  button('mic').textContent = connection?.audio.micActive ? 'Stop microphone' : 'Start microphone';
  button('send').disabled = button('planner').disabled = input('message').disabled = !verified || Boolean(connection?.session.busy);
  button('stop').disabled = !verified;
}
function turn(id: string, role: string) {
  let row = rows.get(id);
  if (!row) {
    el('empty')?.remove();
    const root = document.createElement('article'); root.className = `turn ${role}`;
    const label = document.createElement('div'); label.className = 'role'; label.textContent = role === 'user' ? 'You' : 'Higgs';
    const body = document.createElement('div'); body.className = 'body';
    const details = document.createElement('details'); details.hidden = true;
    const summary = document.createElement('summary'); summary.textContent = 'Generated transcript · may include unheard words';
    const generated = document.createElement('pre'); details.append(summary, generated);
    root.append(label, body, details); el('transcript').append(root);
    row = { root, body, generated, text: '', interrupted: false }; rows.set(id, row);
  }
  return row;
}
function transcript(id: string, role: string, text: string, append = false): void {
  const row = turn(id, role); row.text = append ? row.text + text : text;
  if (row.interrupted) row.generated.textContent = row.text;
  else row.body.textContent = row.text;
  el('transcript').scrollTop = el('transcript').scrollHeight;
}
function tool(trace: ToolTrace): void {
  log('tool', { type: trace.status === 'server' ? 'server.web_search' : `local.${trace.status}`, ...trace });
  if (trace.status === 'duplicate') return;
  let row = toolRows.get(trace.call_id);
  if (!row) { row = document.createElement('details'); row.className = 'tool'; el('tools').querySelector('.muted')?.remove(); el('tools').append(row); toolRows.set(trace.call_id, row); }
  row.dataset.state = trace.status;
  row.replaceChildren();
  const summary = document.createElement('summary');
  summary.textContent = `${trace.status === 'server' ? 'SERVER' : 'LOCAL'} ${trace.name} · ${trace.status}${trace.ms !== undefined ? ` · ${trace.ms} ms` : ''}`;
  const pre = document.createElement('pre'); pre.textContent = JSON.stringify(trace, null, 2);
  row.append(summary, pre);
  if (trace.result) results++;
  metrics();
}
function metrics(): void { el('metrics').textContent = `${chunks} audio chunks · ${seconds.toFixed(2)} s generated · ${results} local results`; }

async function disconnect(message = 'Disconnected', failed = false): Promise<void> {
  const previous = connection; connection = null;
  previous?.abort.abort(); previous?.session.dispose();
  previous?.ws?.close(1000, failed ? 'validation stopped' : 'client disconnect');
  status.textContent = failed ? 'Connection failed' : 'Disconnected'; status.dataset.state = failed ? 'error' : 'closed';
  notice(message, failed); updateControls();
  await previous?.audio.close();
}

async function connect(): Promise<void> {
  if (connection || !config) return;
  const audio = new AudioEngine();
  const abort = new AbortController();
  let acknowledgementTimer: ReturnType<typeof setTimeout>;
  const ctx = { ws: null as WebSocket | null, session: null as unknown as Session, audio, abort };
  const send = (event: WireEvent) => {
    if (connection !== ctx || ctx.ws?.readyState !== WebSocket.OPEN) throw new Error('Socket is not open');
    ctx.ws.send(JSON.stringify(event));
    // Audio frames are counted by the level meter; avoid flooding the event log.
    if (event.type !== 'input_audio_buffer.append') log('send', event);
  };
  const interrupt = new Interruptions(send, item => {
    const row = turn(item.id, 'assistant'); row.interrupted = true; row.root.classList.add('interrupted');
    row.body.textContent = `Interrupted after approximately ${Math.floor(item.heardMs)} ms of playback.`;
    row.generated.textContent = row.text; row.root.querySelector('details')!.hidden = false;
    log('info', { type: 'playback.interrupted', ...item });
  });
  const stopPlayback = () => interrupt.stop(audio.interrupt());
  ctx.session = new Session({ send, tool,
    ready: (model, resolved) => {
      clearTimeout(acknowledgementTimer);
      status.textContent = 'Preview verified'; status.dataset.state = 'verified';
      const voice = object(object(object(resolved).audio).output).voice;
      notice(`Verified ${model}. Resolved voice: ${String(voice ?? '(missing)')}. Type a message to start.`);
      updateControls();
    },
    error: message => {
      clearTimeout(acknowledgementTimer); log('error', { type: 'validation.error', message });
      void disconnect(message, true);
    },
    idle: updateControls,
    event: event => {
      const item = object(event.item);
      const itemId = typeof event.item_id === 'string' ? event.item_id : String(item.id ?? '');
      const responseId = String(event.response_id ?? currentResponse);
      if (event.type === 'response.created') {
        currentResponse = String(object(event.response).id ?? 'current'); interrupt.responseStarted(currentResponse);
        el('activity').textContent = 'Thinking';
      }
      if (event.type === 'response.output_item.added' && item.type === 'message' && itemId) interrupt.observe(itemId, responseId);
      if (event.type === 'response.output_audio.delta' && itemId && typeof event.delta === 'string') {
        const index = Number(event.content_index ?? 0);
        chunks++; seconds += atob(event.delta).length / 2 / AUDIO_RATE; metrics();
        if (interrupt.observe(itemId, responseId, index)) audio.enqueue(itemId, index, event.delta);
        el('activity').textContent = 'Audio streaming';
      }
      if (event.type === 'response.output_audio.done') { audio.markDone(itemId, Number(event.content_index ?? 0)); interrupt.audioDone(itemId); }
      if (event.type === 'response.output_audio_transcript.delta' && typeof event.delta === 'string') transcript(itemId, 'assistant', event.delta, true);
      if (event.type === 'response.output_audio_transcript.done' && typeof event.transcript === 'string') transcript(itemId, 'assistant', event.transcript);
      if (event.type === 'conversation.item.input_audio_transcription.completed') transcript(itemId, 'user', String(event.transcript ?? ''));
      if (event.type === 'conversation.item.input_audio_transcription.failed') transcript(itemId, 'user', `[Transcription failed: ${JSON.stringify(event.error)}]`);
      if (event.type === 'conversation.item.added' && item.type === 'message' && item.role === 'user' && Array.isArray(item.content)) {
        const text = item.content.map(c => String(object(c).text ?? '')).join('');
        if (text) transcript(itemId, 'user', text);
      }
      if (event.type === 'input_audio_buffer.speech_started') { stopPlayback(); el('activity').textContent = 'Listening'; }
      if (event.type === 'input_audio_buffer.speech_stopped') el('activity').textContent = 'Thinking';
      if (event.type === 'response.done') {
        interrupt.responseDone(String(object(event.response).id ?? responseId));
        el('activity').textContent = audio.queuedMs > 0 ? 'Playback queued' : 'Ready';
      }
      if (event.type === 'session.idle_timeout' || event.type === 'session.max_duration_reached') notice(event.type);
      updateControls();
    },
  });
  connection = ctx;
  status.textContent = 'Connecting'; status.dataset.state = 'connecting';
  el('acknowledged').textContent = 'Awaiting acknowledgement'; el('resolved').textContent = 'Awaiting server acknowledgement.';
  updateControls();
  button('stop').onclick = stopPlayback;
  audio.onLevel = rms => { el<HTMLMeterElement>('mic-level').value = rms; };
  audio.onChunk = base64 => { if (connection === ctx && ctx.session.ready) send({ type: 'input_audio_buffer.append', audio: base64 }); };
  audio.onMicEnded = () => { void toggleMic(); };
  try {
    // Resume inside the user gesture, before fetching credentials.
    await audio.resume();
    const response = await fetch('/api/token', { method: 'POST', signal: abort.signal });
    const data = await response.json() as { value?: string; error?: string };
    if (!response.ok || !data.value) throw new Error(data.error ?? 'Invalid client-secret response');
    if (connection !== ctx) return;
    requirePreview(config.model);
    const ws = new WebSocket(config.wsUrl, ['realtime', `bai-client-secret.${data.value}`]); ctx.ws = ws;
    acknowledgementTimer = setTimeout(() => { if (connection === ctx) void disconnect('No resolved session acknowledgement within 15 seconds.', true); }, 15_000);
    ws.onopen = () => { if (connection === ctx) send(sessionConfig({ model: config!.model, voice: input('voice').value.trim() || 'default', webSearch: input('web-search').checked })); };
    ws.onmessage = ({ data: raw }) => {
      if (connection !== ctx) return;
      try {
        const event = parseEvent(String(raw)); log('recv', event);
        if (event.type === 'session.created' || event.type === 'session.updated') {
          el('acknowledged').textContent = String(object(event.session).model ?? '(missing)');
          el('resolved').textContent = JSON.stringify(debugEvent(event).session, null, 2);
        }
        ctx.session.receive(event);
      } catch (error) { void disconnect(`Protocol/audio error: ${String(error)}`, true); }
    };
    ws.onerror = () => { log('error', { type: 'socket.error', message: 'WebSocket connection failed; see close code.' }); };
    ws.onclose = event => {
      clearTimeout(acknowledgementTimer);
      if (connection === ctx) void disconnect(closeMessage(event.code, event.reason), event.code !== 1000);
    };
  } catch (error) {
    if (connection === ctx) void disconnect(error instanceof Error ? error.message : String(error), true);
  }
}

let micChanging = false;
async function toggleMic(): Promise<void> {
  const ctx = connection;
  if (!ctx?.session.ready || micChanging) return;
  micChanging = true; button('mic').disabled = true;
  try {
    if (ctx.audio.micActive) {
      await ctx.audio.stopMic();
      // Let server VAD close a final utterance even if capture stops mid-word.
      if (connection === ctx) {
        for (let i = 0; i < 7; i++) {
          ctx.ws?.send(JSON.stringify({ type: 'input_audio_buffer.append', audio: encodePcm(new Float32Array(2400)) }));
          await new Promise(resolve => setTimeout(resolve, 100));
          if (connection !== ctx) break;
        }
      }
      notice('Microphone stopped. Final audio sent to server VAD.');
    } else {
      await ctx.audio.startMic();
      if (connection !== ctx) await ctx.audio.stopMic();
      else notice('Microphone active. Speak naturally; speaking over a reply stops playback.');
    }
  } catch (error) { notice(`Microphone error: ${String(error)}`, true); }
  finally { micChanging = false; updateControls(); }
}
function sendMessage(text: string): void {
  if (!text.trim()) return;
  try {
    const id = `user_${crypto.randomUUID().replaceAll('-', '')}`;
    connection?.session.sendText(text.trim(), id); transcript(id, 'user', text.trim()); input('message').value = '';
    el('activity').textContent = 'Thinking'; updateControls();
  } catch (error) { notice(String(error), true); }
}

button('connect').onclick = () => { void connect(); };
button('disconnect').onclick = () => { void disconnect(); };
button('mic').onclick = () => { void toggleMic(); };
el<HTMLFormElement>('message-form').onsubmit = event => { event.preventDefault(); sendMessage(input('message').value); };
button('planner').onclick = () => sendMessage("What's on my planner today?");
button('clear-log').onclick = () => el('log').replaceChildren();
button('speaker').onclick = async () => {
  try {
    const ctx = new AudioContext(); await ctx.resume();
    const tone = ctx.createOscillator(), gain = ctx.createGain();
    tone.frequency.value = 440; gain.gain.setValueAtTime(0.08, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
    tone.connect(gain).connect(ctx.destination); tone.start(); tone.stop(ctx.currentTime + 0.3);
    tone.onended = () => { void ctx.close(); }; notice('Speaker test: a short 440 Hz tone played.');
  } catch (error) { notice(`Speaker error: ${String(error)}`, true); }
};
window.addEventListener('pagehide', () => { void disconnect(); });
void fetch('/api/config').then(async response => {
  if (!response.ok) throw new Error(`Configuration endpoint returned HTTP ${response.status}`);
  config = await response.json(); requirePreview(config!.model);
  el('configured').textContent = config!.model; input('voice').value = config!.voice;
  status.textContent = 'Disconnected';
  notice(config!.keyAvailable ? 'Local key available. Connect to verify the resolved preview model.' : 'Offline mode: create .env from .env.example, add BOSON_API_KEY, then restart. Speaker test works without a key.');
  updateControls();
}).catch(error => { config = null; notice(`Configuration error: ${String(error)}`, true); });
