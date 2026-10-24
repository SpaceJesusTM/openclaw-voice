// Agent selector client. Streams the mic to the server and plays whichever
// agent is active. The server's silent overseer picks that agent; clicking a
// tile picks one yourself.

const RATE = 24000;
const CHUNK = RATE / 10; // 100 ms
const COLORS = { planner: "var(--p0)", coder: "var(--p2)", jester: "var(--p1)", you: "var(--human)" };

const $ = (id) => document.getElementById(id);
const els = {
  status: $("status"),
  cost: $("cost"),
  intro: $("intro"),
  start: $("start"),
  startError: $("start-error"),
  room: $("room"),
  tiles: $("tiles"),
  hearing: $("hearing"),
  overseerModel: $("overseer-model"),
  decisions: $("decisions"),
  mic: $("mic"),
  meter: $("meter-fill"),
  micNote: $("mic-note"),
  say: $("say"),
  sayText: $("say-text"),
  transcript: $("transcript"),
};

let ws = null;
let ctx = null;
let playback = null;
let micStream = null;
let active = null;
const debug = { micChunks: 0, audioChunks: 0, micLevel: 0, errors: [] };
window.addEventListener("error", (e) => debug.errors.push(String(e.message)));

function setStatus(text, kind = "") {
  els.status.textContent = text;
  els.status.className = `pill ${kind}`;
}

// --- audio -------------------------------------------------------------------

async function startAudio() {
  // A 24 kHz context: the browser resamples the mic to Higgs's rate, and agent audio plays sample for sample.
  ctx = new AudioContext({ sampleRate: RATE });
  void ctx.resume();
  await Promise.all([ctx.audioWorklet.addModule("capture-worklet.js"), ctx.audioWorklet.addModule("playback-worklet.js")]);
  playback = new AudioWorkletNode(ctx, "playback", { outputChannelCount: [1] });
  playback.connect(ctx.destination);
  try {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (e) {
    micStream = null;
    els.mic.checked = false;
    els.mic.disabled = true;
    els.micNote.textContent = `No microphone (${e.message}). You can still type.`;
    els.micNote.hidden = false;
    return;
  }
  const capture = new AudioWorkletNode(ctx, "capture");
  ctx.createMediaStreamSource(micStream).connect(capture); // not to the speakers
  let pending = new Float32Array(CHUNK);
  let filled = 0;
  capture.port.onmessage = (e) => {
    for (const s of e.data) {
      pending[filled++] = s;
      if (filled === CHUNK) {
        sendMic(pending);
        pending = new Float32Array(CHUNK);
        filled = 0;
      }
    }
  };
}

function sendMic(f32) {
  let sum = 0;
  const pcm = new Int16Array(f32.length);
  for (let i = 0; i < f32.length; i++) {
    const s = Math.max(-1, Math.min(1, f32[i]));
    pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    sum += s * s;
  }
  debug.micLevel = Math.sqrt(sum / f32.length);
  els.meter.style.width = `${Math.min(100, debug.micLevel * 400)}%`;
  // Muted still sends silence, so the overseer's turn detection keeps its timeline.
  if (!els.mic.checked) pcm.fill(0);
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(pcm.buffer);
    debug.micChunks++;
  }
}

function play(buf) {
  const pcm = new Int16Array(buf);
  const f32 = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) f32[i] = pcm[i] / 0x8000;
  debug.audioChunks++;
  if (ctx.state !== "running") void ctx.resume();
  playback.port.postMessage(f32, [f32.buffer]);
}

document.addEventListener("click", () => {
  if (ctx && ctx.state === "suspended") void ctx.resume();
});

// --- connection --------------------------------------------------------------

els.start.addEventListener("click", async () => {
  els.start.disabled = true;
  els.startError.hidden = true;
  try {
    await startAudio(); // inside the click: browsers only start audio on a gesture
  } catch (e) {
    els.start.disabled = false;
    els.startError.textContent = `Could not start audio: ${e.message}`;
    els.startError.hidden = false;
    return;
  }
  connect();
});

function connect() {
  setStatus("Connecting…");
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/selector`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => ws.send(JSON.stringify({ type: "start" }));
  ws.onmessage = (e) => {
    if (e.data instanceof ArrayBuffer) return play(e.data);
    const msg = JSON.parse(e.data);
    if (msg.type === "status") setStatus(msg.text);
    else if (msg.type === "ready") onReady(msg);
    else if (msg.type === "active") onActive(msg);
    else if (msg.type === "speaking") onSpeaking(msg.name);
    else if (msg.type === "hearing") onHearing(msg.on);
    else if (msg.type === "user") addLine("you", msg.text, { typed: msg.typed });
    else if (msg.type === "said") addLine(msg.name, msg.text, { cut: msg.cut, latencyMs: msg.latencyMs });
    else if (msg.type === "overseer") addDecision(msg);
    else if (msg.type === "clear") playback?.port.postMessage("clear");
    else if (msg.type === "usage") showUsage(msg);
    else if (msg.type === "error") onError(msg.text);
  };
  ws.onclose = () => {
    setStatus("Disconnected", "bad");
    els.sayText.disabled = true;
  };
}

function onError(text) {
  if (els.room.hidden) {
    setStatus("Error", "bad");
    els.startError.textContent = text;
    els.startError.hidden = false;
    els.start.disabled = false;
    ws?.close();
    return;
  }
  addNote(`error: ${text}`);
}

function onReady(msg) {
  els.tiles.replaceChildren(...msg.agents.map(agentTile));
  els.overseerModel.textContent = `never speaks · one tool: call_agent · ${msg.overseerModel}`;
  els.intro.hidden = true;
  els.room.hidden = false;
  setStatus("Live", "live");
  setActive(msg.active);
  addDecision({ kind: "start", agent: msg.active });
  startDiagnostics();
}

function agentTile(a) {
  const t = document.createElement("button");
  t.className = "tile";
  t.dataset.name = a.name;
  t.title = `Talk to ${a.name}`;
  t.style.setProperty("--c", COLORS[a.name]);
  t.innerHTML = `
    <div class="tile-head">
      <div class="avatar"></div>
      <div><div class="who"></div><div class="what"></div></div>
    </div>
    <div class="state"><span class="chip">sidelined</span></div>`;
  t.querySelector(".avatar").textContent = a.name[0].toUpperCase();
  t.querySelector(".who").textContent = a.name;
  t.querySelector(".what").textContent = `${a.blurb} · ${a.voice}`;
  t.addEventListener("click", () => {
    if (a.name !== active && ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "pick", name: a.name }));
  });
  return t;
}

function setActive(name) {
  active = name;
  for (const t of els.tiles.children) {
    const on = t.dataset.name === name;
    t.classList.toggle("active", on);
    t.querySelector(".chip").textContent = on ? "talking to you" : "sidelined";
  }
}

function onActive(msg) {
  setActive(msg.name);
  addDecision({ kind: "switch", ...msg });
  addNote(`${msg.by === "you" ? "You" : "Overseer"} handed the conversation to ${msg.name}${msg.reason ? `: ${msg.reason}` : ""}`);
}

function onSpeaking(name) {
  for (const t of els.tiles.children) t.classList.toggle("speaking", t.dataset.name === name);
}

function onHearing(on) {
  els.hearing.textContent = on ? "hearing you" : "listening";
  els.hearing.classList.toggle("on", on);
}

function addDecision(d) {
  const li = document.createElement("li");
  if (d.kind === "start") li.innerHTML = `<b></b> starts the conversation`;
  else if (d.kind === "switch") li.innerHTML = `call_agent(<b></b>) <span class="why"></span>`;
  else if (d.kind === "stay") li.innerHTML = `call_agent(<b></b>), already talking <span class="why"></span>`;
  else {
    li.className = "text";
    li.textContent = `“${d.text}” (dropped: the overseer never speaks)`;
  }
  if (li.querySelector("b")) li.querySelector("b").textContent = d.kind === "start" ? d.agent : d.name ?? d.agent;
  if (li.querySelector(".why")) li.querySelector(".why").textContent = d.reason ? `— ${d.by === "you" ? "you clicked" : d.reason}` : "";
  els.decisions.append(li);
  els.decisions.scrollTop = els.decisions.scrollHeight;
}

function addLine(who, text, { cut = false, typed = false, latencyMs } = {}) {
  const li = document.createElement("li");
  li.className = `said${cut ? " cut" : ""}`;
  li.style.setProperty("--c", COLORS[who] ?? "var(--muted)");
  li.innerHTML = `<span class="who"></span><span class="text"></span><time></time>`;
  li.querySelector(".who").textContent = who === "you" ? (typed ? "you (typed)" : "you") : who;
  li.querySelector(".text").textContent = text;
  // How long after you stopped talking this reply was first heard (server side).
  if (latencyMs != null) {
    li.querySelector("time").textContent = `${(latencyMs / 1000).toFixed(2)} s`;
    li.querySelector("time").title = "From when you stopped talking to this reply's first audio";
  }
  append(li);
}

function addNote(text) {
  const li = document.createElement("li");
  li.className = "note";
  li.textContent = text;
  append(li);
}

function append(li) {
  const t = els.transcript;
  const stick = t.scrollTop + t.clientHeight >= t.scrollHeight - 40;
  t.append(li);
  if (stick) t.scrollTop = t.scrollHeight;
}

const usd = (n) => `$${n < 0.01 ? n.toFixed(4) : n.toFixed(3)}`;

function showUsage(u) {
  els.cost.hidden = false;
  const perMin = u.minutes > 0.1 ? ` · ${usd(u.total / u.minutes)}/min` : "";
  els.cost.textContent = `${usd(u.total)}${perMin}`;
  els.cost.title = `Agents ${usd(u.agentsCost)}, overseer ${usd(u.overseerCost)}. Estimated from reported tokens at Boson's published rates.`;
}

els.say.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = els.sayText.value.trim();
  if (!text || ws?.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "say", text }));
  els.sayText.value = "";
});

/** Every 2 s the page reports its audio state; the server logs it to captures/web-*.log. */
function startDiagnostics() {
  setInterval(() => {
    if (ws?.readyState !== WebSocket.OPEN) return;
    ws.send(
      JSON.stringify({
        type: "diag",
        ctxState: ctx?.state,
        mic: els.mic.checked,
        micTrack: micStream?.getAudioTracks()[0]?.label,
        micChunks: debug.micChunks,
        micLevel: Number(debug.micLevel.toFixed(3)),
        audioChunks: debug.audioChunks,
        errors: debug.errors.splice(0),
      }),
    );
  }, 2000);
}
