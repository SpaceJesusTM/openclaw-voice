// Voice Room client. The page is a participant like any other: it streams the
// mic to the server and plays the room. Every decision about who speaks is
// made by the agents' own minds on the server; this page shows them.

import { PRESETS } from "./presets.js";

const RATE = 24000;
const CHUNK = RATE / 10; // 100 ms, the bus tick
const MIN_AGENTS = 2;
const MAX_AGENTS = 4;
const STORE_KEY = "voice-room-setup-v1";
const VOICES = ["chloe", "eleanor", "jake", "marcus", "nora", "oliver"];

// Measured 2026-10-24 (silence to speech, with a private mind): hybrid 1.0–1.2 s;
// all 1.5 up to ~4 s, because 1.5 slows down when minds and voices generate at once.
const MODEL_HINTS = {
  "1.5": "Most natural speech. With a private mind, slow (~4 s) when several agents decide at once.",
  "1": "Fast and reliable. Tends to read out lists and agree quickly.",
  hybrid: "Recommended: 1.5 for what you hear, 1 for the private mind's decisions.",
};
const VOICE_ONLY_HINT = "No private mind: the voice (on {model}) decides for itself, by speaking or staying silent.";
const SHORT_MODEL = { "higgs-realtime": "1", "higgs-realtime-1.5-preview": "1.5" };

const $ = (id) => document.getElementById(id);
const els = {
  status: $("status"),
  setup: $("setup"),
  preset: $("preset"),
  you: $("you"),
  scene: $("scene"),
  cards: $("agent-cards"),
  addAgent: $("add-agent"),
  start: $("start"),
  maxTurns: $("max-turns"),
  setupError: $("setup-error"),
  room: $("room"),
  tiles: $("tiles"),
  mic: $("mic"),
  meter: $("meter-fill"),
  speakerMeter: $("speaker-fill"),
  testSound: $("test-sound"),
  soundNote: $("sound-note"),
  leave: $("leave"),
  sayForm: $("say-form"),
  sayText: $("say-text"),
  micNote: $("mic-note"),
  transcript: $("transcript"),
  showThoughts: $("show-thoughts"),
  download: $("download"),
};

let ws = null;
let ctx = null;
let playback = null;
let micStream = null;
let micOn = true;
let humanName = "the human";
const colors = new Map(); // participant -> CSS color
const entries = []; // everything the room journaled, for the download
// For diagnosing "no sound": inspect window.voiceRoomDebug in the console.
const debug = { audioChunks: 0, audioPeak: 0, micChunks: 0, micLevel: 0, ctxState: "none", testSounds: 0, errors: [] };
window.voiceRoomDebug = debug;
window.addEventListener("error", (e) => debug.errors.push(String(e.message).slice(0, 200)));
window.addEventListener("unhandledrejection", (e) => debug.errors.push(String(e.reason).slice(0, 200)));

/** Every 2 s in the room, report the page's audio state to the server's log. */
function startDiagnostics() {
  let first = true;
  setInterval(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const report = {
      type: "diag",
      ctxState: ctx?.state,
      sampleRate: ctx?.sampleRate,
      outputLatency: ctx?.outputLatency,
      audioChunks: debug.audioChunks,
      audioPeak: Number(debug.audioPeak.toFixed(3)),
      micOn,
      hasMic: Boolean(micStream),
      micTrack: micStream?.getAudioTracks()[0]?.label,
      micChunks: debug.micChunks,
      micLevel: Number(debug.micLevel.toFixed(3)),
      testSounds: debug.testSounds,
      errors: debug.errors.splice(0),
    };
    if (first) Object.assign(report, { userAgent: navigator.userAgent });
    first = false;
    ws.send(JSON.stringify(report));
  }, 2000);
}

// --- setup -------------------------------------------------------------------

for (const p of PRESETS) els.preset.append(new Option(p.label, p.id));
els.preset.append(new Option("Your last setup", "saved"));

function loadSetup(setup) {
  // A preset has no name for you; keep whatever is typed.
  if (setup.you != null) els.you.value = setup.you;
  els.scene.value = setup.scene ?? "";
  if (setup.maxAgentTurns) els.maxTurns.value = setup.maxAgentTurns;
  els.cards.replaceChildren();
  for (const a of setup.agents) addCard(a);
  refreshCards();
}

function addCard(a) {
  const card = $("agent-card").content.firstElementChild.cloneNode(true);
  for (const k of ["name", "voice", "model", "role", "goal", "facts"]) {
    const field = card.querySelector(`[name=${k}]`);
    if (a[k] != null && a[k] !== "") field.value = a[k];
  }
  const mind = card.querySelector("[name=mind]");
  mind.checked = Boolean(a.mind);
  const hint = card.querySelector(".model-hint");
  const model = card.querySelector("[name=model]");
  const showHint = () => {
    const voiceModel = model.value === "1" ? "Higgs 1" : "Higgs 1.5";
    hint.textContent = mind.checked ? MODEL_HINTS[model.value] : VOICE_ONLY_HINT.replace("{model}", voiceModel);
  };
  model.addEventListener("change", showHint);
  mind.addEventListener("change", showHint);
  showHint();
  card.querySelector(".remove").addEventListener("click", () => {
    card.remove();
    refreshCards();
    save();
  });
  els.cards.append(card);
}

function refreshCards() {
  const cards = [...els.cards.children];
  cards.forEach((c, i) => c.style.setProperty("--c", `var(--p${i % 4})`));
  for (const c of cards) c.querySelector(".remove").disabled = cards.length <= MIN_AGENTS;
  els.addAgent.disabled = cards.length >= MAX_AGENTS;
}

els.addAgent.addEventListener("click", () => {
  const used = readSetup().agents.map((a) => a.voice);
  const n = els.cards.children.length + 1;
  addCard({ name: `Agent ${String.fromCharCode(64 + n)}`, voice: VOICES.find((v) => !used.includes(v)) ?? VOICES[0], model: "hybrid" });
  refreshCards();
  save();
});

function readSetup() {
  return {
    you: els.you.value.trim(),
    scene: els.scene.value.trim(),
    maxAgentTurns: Number(els.maxTurns.value) || 4,
    agents: [...els.cards.children].map((card) => {
      const get = (k) => card.querySelector(`[name=${k}]`).value.trim();
      return {
        name: get("name"),
        voice: get("voice"),
        model: get("model"),
        mind: card.querySelector("[name=mind]").checked,
        role: get("role"),
        goal: get("goal"),
        facts: get("facts"),
      };
    }),
  };
}

// The setup is remembered per browser, so a reload doesn't lose your agents.
function save() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(readSetup()));
  } catch {
    /* storage unavailable: fine */
  }
}
function loadSaved() {
  try {
    return JSON.parse(localStorage.getItem(STORE_KEY) ?? "null");
  } catch {
    return null;
  }
}

els.setup.addEventListener("input", (e) => {
  if (e.target !== els.preset) save();
});
els.preset.addEventListener("change", () => {
  const p = els.preset.value === "saved" ? loadSaved() : PRESETS.find((x) => x.id === els.preset.value);
  if (p) loadSetup(p);
  save();
});

// ?preset=<id> (used by demo-thoughts.bat) wins over the remembered setup.
const asked = PRESETS.find((p) => p.id === new URLSearchParams(location.search).get("preset"));
const saved = loadSaved();
if (asked) {
  els.preset.value = asked.id;
  loadSetup({ ...asked, you: saved?.you });
} else if (saved?.agents?.length >= MIN_AGENTS) {
  els.preset.value = "saved";
  loadSetup(saved);
} else {
  loadSetup(PRESETS[0]);
}

function setStatus(text, kind = "") {
  els.status.textContent = text;
  els.status.className = `pill ${kind}`;
}

function showSetupError(text) {
  els.setupError.textContent = text;
  els.setupError.hidden = !text;
}

els.start.addEventListener("click", async () => {
  const setup = readSetup();
  const names = setup.agents.map((a) => a.name.toLowerCase());
  if (setup.agents.some((a) => !a.name) || new Set(names).size !== names.length) {
    return showSetupError("Give each agent a different name.");
  }
  if (setup.you && names.includes(setup.you.toLowerCase())) {
    return showSetupError("Your name matches one of the agents; pick a different one.");
  }
  showSetupError("");
  save();
  els.start.disabled = true;
  try {
    await startAudio(); // must run inside the click: browsers only start audio on a gesture
    connect({ type: "start", ...setup });
  } catch (e) {
    els.start.disabled = false;
    showSetupError(`Could not start audio: ${e.message}`);
  }
});

// --- audio -------------------------------------------------------------------

async function startAudio() {
  // A 24 kHz context: the browser resamples the mic to Higgs's rate for us, and
  // room audio plays sample for sample.
  ctx = new AudioContext({ sampleRate: RATE });
  // Browsers can start a context suspended (autoplay rules); a suspended
  // context plays nothing at all, silently.
  void ctx.resume();
  ctx.onstatechange = () => showSoundState();
  await Promise.all([ctx.audioWorklet.addModule("capture-worklet.js"), ctx.audioWorklet.addModule("playback-worklet.js")]);
  playback = new AudioWorkletNode(ctx, "playback", { outputChannelCount: [1] });
  playback.connect(ctx.destination);
  showSoundState();

  try {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (e) {
    micStream = null;
    els.micNote.textContent = `No microphone (${e.message}). You can still type lines to say them out loud.`;
    els.micNote.hidden = false;
    return;
  }
  const src = ctx.createMediaStreamSource(micStream);
  const capture = new AudioWorkletNode(ctx, "capture");
  src.connect(capture); // not to the speakers

  let pending = new Float32Array(CHUNK);
  let filled = 0;
  capture.port.onmessage = (e) => {
    const frame = e.data;
    for (let i = 0; i < frame.length; i++) {
      pending[filled++] = frame[i];
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
  if (micOn && ws && ws.readyState === WebSocket.OPEN) {
    ws.send(pcm.buffer);
    debug.micChunks++;
  }
}

function playRoom(buf) {
  const pcm = new Int16Array(buf);
  const f32 = new Float32Array(pcm.length);
  let sum = 0;
  for (let i = 0; i < pcm.length; i++) {
    f32[i] = pcm[i] / 0x8000;
    sum += f32[i] * f32[i];
  }
  const level = Math.sqrt(sum / Math.max(1, pcm.length));
  debug.audioChunks++;
  debug.audioPeak = Math.max(debug.audioPeak, level);
  els.speakerMeter.style.width = `${Math.min(100, level * 400)}%`;
  clearTimeout(playRoom.idle);
  playRoom.idle = setTimeout(() => (els.speakerMeter.style.width = "0"), 200);
  if (ctx && ctx.state !== "running") void ctx.resume();
  playback.port.postMessage(f32, [f32.buffer]);
}

/** Tell the user if the page can't make sound, and how to fix it. */
function showSoundState() {
  debug.ctxState = ctx ? ctx.state : "none";
  const blocked = ctx && ctx.state !== "running";
  els.soundNote.hidden = !blocked;
  if (blocked) els.soundNote.textContent = `Sound is ${ctx.state} in this tab. Click anywhere on the page (or Test sound) to turn it on.`;
}

// Any click can unblock a suspended context.
document.addEventListener("click", () => {
  if (ctx && ctx.state === "suspended") void ctx.resume();
});

/** A short tone through the same path the room's audio takes. */
els.testSound.addEventListener("click", () => {
  if (!playback) return;
  void ctx.resume();
  debug.testSounds++;
  const tone = new Float32Array(RATE * 0.4);
  for (let i = 0; i < tone.length; i++) tone[i] = 0.2 * Math.sin((2 * Math.PI * 440 * i) / RATE) * Math.min(1, (tone.length - i) / 2400);
  playback.port.postMessage(tone, [tone.buffer]);
});

// --- connection --------------------------------------------------------------

function connect(startMsg) {
  setStatus("Connecting…");
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/room`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => ws.send(JSON.stringify(startMsg));
  ws.onmessage = (e) => {
    if (e.data instanceof ArrayBuffer) return playRoom(e.data);
    const msg = JSON.parse(e.data);
    if (msg.type === "status") setStatus(msg.text);
    else if (msg.type === "ready") onReady(msg);
    else if (msg.type === "journal") addEntry(msg.entry);
    else if (msg.type === "active") setActive(msg.names);
    else if (msg.type === "usage") showUsage(msg);
    else if (msg.type === "error") onError(msg.text);
  };
  ws.onclose = () => {
    setStatus("Disconnected", "bad");
    els.sayText.disabled = true;
  };
}

function onError(text) {
  if (!els.room.hidden) return addEntry({ at: null, who: "room", kind: "note", text: `error: ${text}` });
  setStatus("Error", "bad");
  showSetupError(text);
  els.start.disabled = false;
  ws?.close();
}

function onReady(msg) {
  humanName = msg.human;
  colors.set(humanName, "var(--human)");
  msg.agents.forEach((a, i) => colors.set(a.name, `var(--p${i % 4})`));
  els.tiles.replaceChildren(...msg.agents.map(agentTile), humanTile());
  els.setup.hidden = true;
  els.room.hidden = false;
  setStatus("Live", "live");
  els.sayText.focus();
  startDiagnostics();
}

function modelBadge(a) {
  const v = SHORT_MODEL[a.voiceModel] ?? a.voiceModel;
  if (!a.mind) return `Higgs ${v} · voice only`;
  const m = SHORT_MODEL[a.mindModel] ?? a.mindModel;
  return v === m ? `Higgs ${v} · with mind` : `${v} voice · ${m} mind`;
}

function agentTile(a) {
  const t = document.createElement("div");
  t.className = "tile";
  t.dataset.name = a.name;
  t.style.setProperty("--c", colors.get(a.name));
  t.innerHTML = `
    <div class="tile-head">
      <div class="avatar"></div>
      <div><div class="who"></div><div class="what"></div></div>
    </div>
    <div class="mind" aria-live="polite">
      <div class="mind-top"><span class="chip">listening</span><span class="mind-context"></span></div>
      <div class="mind-thought"></div>
      <div class="mind-detail"></div>
    </div>`;
  t.querySelector(".mind-thought").textContent = a.mind ? "No thoughts yet." : "Decides by speaking or staying silent.";
  t.querySelector(".avatar").textContent = a.name[0].toUpperCase();
  t.querySelector(".who").textContent = a.name;
  t.querySelector(".what").textContent = `${modelBadge(a)} · ${a.voice}`;
  return t;
}

function humanTile() {
  const t = document.createElement("div");
  t.className = "tile human";
  t.dataset.name = humanName;
  t.style.setProperty("--c", colors.get(humanName));
  t.innerHTML = `<div class="tile-head"><div class="avatar">Y</div><div><div class="who">You</div><div class="what"></div></div></div>`;
  t.querySelector(".what").textContent = micStream ? "your mic" : "typing only";
  return t;
}

const usd = (n) => `$${n < 0.01 ? n.toFixed(4) : n.toFixed(3)}`;

/** Running cost: header total, and speech vs thoughts on each agent's tile. */
function showUsage(u) {
  const cost = $("cost");
  cost.hidden = false;
  const perMin = u.minutes > 0.2 ? ` · ${usd(u.total / u.minutes)}/min` : "";
  cost.textContent = `≈ ${usd(u.total)}${perMin}`;
  cost.title = `Speech (voices) ${usd(u.voiceCost)} + thoughts (minds) ${usd(u.mindCost)}. Estimated from the token usage Higgs reports, at Boson's published rates; your billing dashboard is the source of truth.`;
  for (const a of u.agents) {
    const t = [...els.tiles.children].find((x) => x.dataset.name === a.name);
    if (!t) continue;
    let line = t.querySelector(".cost");
    if (!line) {
      line = document.createElement("div");
      line.className = "cost";
      t.append(line);
    }
    const tokens = (x) => x.inputTokens + x.outputTokens;
    line.textContent =
      `speech ${usd(a.voiceCost)} (${tokens(a.voice).toLocaleString()} tok)` +
      (a.mind.responses ? ` · thoughts ${usd(a.mindCost)} (${tokens(a.mind).toLocaleString()} tok)` : "");
  }
}

function setActive(names) {
  for (const t of els.tiles.children) t.classList.toggle("speaking", names.includes(t.dataset.name));
}

/** The tile's live view of an agent's private mind: its latest thought and decision. */
function showThought(name, data) {
  const t = [...els.tiles.children].find((x) => x.dataset.name === name);
  if (!t || !data) return;
  const chip = t.querySelector(".chip");
  chip.textContent = data.decision;
  chip.className = `chip ${data.decision}`;
  t.querySelector(".mind-context").textContent = data.context ?? "";
  t.querySelector(".mind-thought").textContent = data.thought ? `“${data.thought}”` : "(no thought)";
  t.querySelector(".mind-detail").textContent = data.detail ? `→ ${data.detail}` : "";
  const mind = t.querySelector(".mind");
  mind.classList.remove("fresh");
  void mind.offsetWidth; // restart the highlight animation
  mind.classList.add("fresh");
}

/** The tile's status for what an agent did, not what it thought: spoke, stayed silent, working. */
function showStatus(name, status, text) {
  const t = [...els.tiles.children].find((x) => x.dataset.name === name);
  if (!t || !t.querySelector(".chip")) return;
  const chip = t.querySelector(".chip");
  chip.textContent = status;
  chip.className = `chip ${status.replace(/\s+/g, "-")}`;
  t.querySelector(".mind-context").textContent = "";
  t.querySelector(".mind-thought").textContent = text;
  t.querySelector(".mind-detail").textContent = "";
}

function statusFrom(e) {
  if (e.kind === "said") return showStatus(e.who, "spoke", e.text.replace(/\*\(([^)]*)\)\*/g, "($1)"));
  if (e.kind === "work") return showStatus(e.who, "done", `Finished: ${e.data?.task ?? "its work"}`);
  const s = e.data?.status;
  if (s === "passed") showStatus(e.who, "silent", "Chose to stay silent.");
  else if (s === "working") showStatus(e.who, "working", `Working on: ${e.data.task}`);
}

// --- transcript --------------------------------------------------------------

const label = (who) => (who === humanName ? "You" : who);

function addEntry(e) {
  entries.push(e);
  if (e.kind === "thought") showThought(e.who, e.data);
  else statusFrom(e);

  const li = document.createElement("li");
  li.className = e.kind;
  li.style.setProperty("--c", colors.get(e.who) ?? "var(--muted)");
  const at = e.at == null ? "" : `${(e.at / 1000).toFixed(1)} s`;
  if (e.kind === "said") {
    li.innerHTML = `<span class="who"></span><span class="text"></span><time></time>`;
    li.querySelector(".who").textContent = label(e.who);
    li.querySelector(".text").textContent = e.text.replace(/\*\(([^)]*)\)\*/g, "($1)");
  } else if (e.kind === "work") {
    li.innerHTML = `<div class="work-head"><b></b> finished: <span class="task"></span><time></time></div><div class="work-body"></div>`;
    li.querySelector("b").textContent = label(e.who);
    li.querySelector(".task").textContent = e.data?.task ?? "";
    li.querySelector(".work-body").textContent = e.text;
  } else if (e.kind === "thought" && e.data) {
    li.innerHTML = `<b></b> <span class="ctx"></span> <span class="text"></span> <span class="chip"></span> <span class="detail"></span><time></time>`;
    li.querySelector("b").textContent = label(e.who);
    li.querySelector(".ctx").textContent = `(${e.data.context})`;
    li.querySelector(".text").textContent = e.data.thought ? `“${e.data.thought}”` : "(no thought)";
    li.querySelector(".chip").textContent = e.data.decision;
    li.querySelector(".chip").classList.add(e.data.decision);
    li.querySelector(".detail").textContent = e.data.detail ?? "";
  } else {
    li.innerHTML = `<span class="text"></span><time></time>`;
    li.querySelector(".text").textContent = `${label(e.who)}: ${e.text}`;
  }
  li.querySelector("time").textContent = at;

  // Entries can arrive out of order (a turn is logged when it ends, stamped when it began).
  li.dataset.at = String(e.at ?? Number.MAX_SAFE_INTEGER);
  const later = [...els.transcript.children].find((c) => Number(c.dataset.at) > Number(li.dataset.at));
  const stick = els.transcript.scrollTop + els.transcript.clientHeight >= els.transcript.scrollHeight - 40;
  els.transcript.insertBefore(li, later ?? null);
  if (stick) els.transcript.scrollTop = els.transcript.scrollHeight;
}

els.showThoughts.addEventListener("change", () => {
  els.transcript.classList.toggle("hide-thoughts", !els.showThoughts.checked);
});

els.download.addEventListener("click", () => {
  const lines = ["# Voice Room transcript", ""];
  const scene = els.scene.value.trim();
  if (scene) lines.push(`*Situation: ${scene}*`, "");
  for (const e of [...entries].sort((a, b) => (a.at ?? 0) - (b.at ?? 0))) {
    const at = e.at == null ? "" : ` (${(e.at / 1000).toFixed(1)} s)`;
    if (e.kind === "said") lines.push("", `**${label(e.who)}**${at}: ${e.text}`);
    else if (e.kind === "thought" && e.data)
      lines.push(`> ${label(e.who)}${at} (${e.data.context}): *${e.data.thought || "(no thought)"}* → **${e.data.decision}**${e.data.detail ? `: ${e.data.detail}` : ""}`);
    else if (e.kind === "work") lines.push("", `#### ${label(e.who)}'s work${at}: ${e.data?.task ?? ""}`, "", e.text, "");
    else lines.push(`*${label(e.who)}${at}: ${e.text}*`);
  }
  const blob = new Blob([lines.join("\n") + "\n"], { type: "text/markdown" });
  const a = Object.assign(document.createElement("a"), {
    href: URL.createObjectURL(blob),
    download: `voice-room-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}.md`,
  });
  a.click();
  URL.revokeObjectURL(a.href);
});

// --- controls ----------------------------------------------------------------

els.mic.addEventListener("click", () => {
  micOn = !micOn;
  els.mic.textContent = micOn ? "Mic on" : "Mic off";
  els.mic.classList.toggle("on", micOn);
  els.mic.setAttribute("aria-pressed", String(micOn));
});

els.sayForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = els.sayText.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "say", text }));
  els.sayText.value = "";
});

els.leave.addEventListener("click", () => {
  ws?.close();
  micStream?.getTracks().forEach((t) => t.stop());
  ctx?.close();
  setStatus("Left the room");
  els.leave.disabled = true;
  els.mic.disabled = true;
});
