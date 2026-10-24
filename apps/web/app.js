// ClawVoice local UI.
// Native WebRTC to Pipecat's SmallWebRTC transport (/start + /sessions/:id/api/offer)
// and RTVI messages on the data channel. No secrets ever reach this page: the
// Boson key and OpenClaw Gateway token stay in the Python server.

const $ = (id) => document.getElementById(id);
const MODEL = "higgs-realtime-1.5-preview";
const ACTIVE = new Set(["accepted", "queued", "running", "verifying"]);

const ui = {
  agents: [],
  agent: null,
  pc: null,
  dc: null,
  stream: null,
  audioCtx: null,
  ping: null,
  poll: null,
  connected: false,
  connecting: false,
  botTurn: null,
  userInterim: null,
  userSpeaking: false,
  botSpeaking: false,
  pendingNotices: 0,
  jobs: new Map(),
  noticed: new Set(),
  levelTimer: null,
  remoteAnalyser: null,
  localAnalyser: null,
};

// ------------------------------------------------------------------ helpers
const rid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random()));
const esc = (text) =>
  String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

async function postJSON(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!response.ok) {
    let detail = "";
    try { detail = (await response.json()).detail || ""; } catch { /* ignore */ }
    throw new Error(`Server returned ${response.status}${detail ? `: ${detail}` : ""}`);
  }
  return response.json();
}

function banner(text, kind = "error") {
  const el = $("banner");
  if (!text) { el.hidden = true; return; }
  el.className = `banner ${kind === "info" ? "info" : ""}`;
  el.textContent = text;
  el.hidden = false;
}

function logLine(listId, text) {
  const list = $(listId);
  const item = document.createElement("li");
  item.textContent = `${new Date().toLocaleTimeString()} ${text}`;
  list.prepend(item);
  while (list.children.length > 150) list.lastChild.remove();
}

function hexToSoft(hex, alpha) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

// ------------------------------------------------------------------ agent + state
function shortRole(agent) {
  if (!agent) return "";
  if (agent.id === "tari") return "Personal Assistant";
  if (agent.id === "astra") return "Coding Assistant";
  const raw = String(agent.role || "").split(/[.:]/)[0].trim();
  return raw || "Assistant";
}

function renderAgentSelector() {
  const bar = $("agent-selector");
  if (!bar) return;
  const agents = ui.agents || [];
  if (agents.length <= 1) {
    bar.hidden = true;
    bar.innerHTML = "";
    return;
  }
  bar.hidden = false;
  bar.innerHTML = agents
    .map((a) => {
      const selected = a.id === ui.agent?.id;
      const color = esc(a.color || "#22c55e");
      return `<button type="button" id="select-agent-${esc(a.id)}" class="agent-tab${selected ? " active" : ""}"
        role="tab" aria-selected="${selected}" data-agent="${esc(a.id)}" style="--agent-color: ${color}">
        <span class="agent-dot" aria-hidden="true"></span>
        <span class="agent-tab-text">
          <span class="agent-tab-name">${esc(a.display_name)}</span>
          <span class="agent-tab-role">${esc(shortRole(a))}</span>
        </span>
      </button>`;
    })
    .join("");
}

function resetConversationForAgent(agent) {
  const box = $("transcript");
  if (!box) return;
  const example = agent?.id === "astra"
    ? "create a simple website for a fictional coffee shop."
    : "What's on my planner today?";
  box.innerHTML = `<p class="hint">Press <strong>Connect</strong>, allow the microphone, and say “${esc(example)}” Use headphones to avoid echo.</p>`;
}

async function selectAgent(agentId) {
  const next = (ui.agents || []).find((a) => a.id === agentId);
  if (!next || ui.agent?.id === next.id) return;
  const wasActive = ui.connected || ui.connecting;
  if (wasActive) {
    sendRtvi("disconnect-bot", {});
  }
  const audio = $("bot-audio");
  if (audio?.pause) {
    try { audio.pause(); } catch { /* ignore */ }
  }
  teardown();
  banner(null);
  ui.agent = next;
  ui.jobs = new Map();
  ui.noticed = new Set();
  ui.topicRows = new Map();
  ui.lastMetrics = null;
  ui.lastPoll = 0;
  if (typeof history !== "undefined" && history.replaceState) {
    const url = new URL(location.href);
    url.searchParams.set("agent", next.id);
    history.replaceState(null, "", url.toString());
  }
  renderAgentSelector();
  renderIdentity();
  resetConversationForAgent(next);
  renderJobs();
  renderTopics({ topics: [], metrics: {} });
  await refreshState(true);
  if (wasActive) {
    await connect();
  }
}

async function init() {
  try {
    const data = await (await fetch("/api/agents")).json();
    ui.agents = data.agents || [];
    const wanted = new URLSearchParams(location.search).get("agent");
    ui.agent = ui.agents.find((a) => a.id === wanted) || ui.agents.find((a) => a.id === data.default) || ui.agents[0];
    renderAgentSelector();
    renderIdentity();
    resetConversationForAgent(ui.agent);
    await refreshState(true);
  } catch (error) {
    banner("ClawVoice server is not reachable. Start it with `make dev` in services/voice.");
  }
  ui.poll = setInterval(refreshState, 4000);
  setOrb();
}

function renderIdentity() {
  const a = ui.agent;
  if (!a) return;
  document.title = `ClawVoice · ${a.display_name}`;
  $("agent-name").textContent = `${a.display_name} ${a.emoji || ""}`.trim();
  $("agent-role").textContent = a.role || "";
  document.documentElement.style.setProperty("--accent", a.color);
  document.documentElement.style.setProperty("--accent-soft", hexToSoft(a.color, 0.22));
}

async function refreshState(force = false) {
  if (!ui.agent) return;
  // While connected, live updates arrive over RTVI; poll only as a slow backstop.
  if (!force && ui.connected && Date.now() - (ui.lastPoll || 0) < 15000) return;
  const requestedAgentId = ui.agent.id;
  ui.lastPoll = Date.now();
  try {
    const response = await fetch(`/api/state?agent=${encodeURIComponent(requestedAgentId)}`);
    if (!response.ok || ui.agent?.id !== requestedAgentId) return;
    applyState(await response.json());
  } catch { /* server restarting; next poll will retry */ }
}

function applyState(state) {
  if (!state) return;
  if (state.agent?.id && ui.agent?.id && state.agent.id !== ui.agent.id) return;
  renderGateway(state.gateway);
  ui.jobs = new Map();
  for (const job of state.jobs || []) ui.jobs.set(job.job_id, job);
  renderJobs();
  if (state.cache) {
    ui.topicRows = new Map((state.cache.topics || []).map((row) => [row.topic, row]));
    ui.lastMetrics = state.cache.metrics;
    renderTopics(state.cache);
  }
}

function renderGateway(gateway) {
  const badge = $("gateway-badge");
  if (!gateway) return;
  const mode = gateway.active_transport;
  const ws = gateway.websocket;
  if (gateway.config_error) {
    badge.textContent = "Gateway · configuration error";
    badge.className = "badge bad";
    banner(`OpenClaw Gateway configuration problem: ${gateway.config_error}`);
    return;
  }
  if (mode === "ws") {
    badge.textContent = "Gateway · WebSocket (paired)";
    badge.className = "badge ok";
  } else if (mode === "http") {
    badge.textContent = ws && ws.state === "pairing_required" ? "Gateway · HTTP (pairing pending)" : "Gateway · HTTP";
    badge.className = "badge ok";
    badge.title = gateway.pairing_hint
      ? `Optional: approve the WebSocket device for run IDs and recovery: ${gateway.pairing_hint}`
      : "OpenClaw Gateway reachable over HTTP";
  } else {
    badge.textContent = "Gateway · unavailable";
    badge.className = "badge bad";
  }
}

// ------------------------------------------------------------------ jobs
function renderJobs() {
  const list = $("jobs");
  const jobs = [...ui.jobs.values()].sort((a, b) => (a.created_at < b.created_at ? 1 : -1)).slice(0, 12);
  const active = jobs.filter((j) => ACTIVE.has(j.status)).length;
  $("jobs-count").textContent = jobs.length ? `${active} active` : "";
  setOrb();
  if (!jobs.length) {
    list.innerHTML = '<li class="empty">No tasks yet.</li>';
    return;
  }
  list.innerHTML = jobs
    .map((job) => {
      const verified = job.verification === "verified" ? " · ✓ verified" : job.verification === "mismatch" ? " · ⚠ mismatch" : "";
      const timing = [];
      if (job.timings?.dispatch_ack_ms != null) timing.push(`ack ${job.timings.dispatch_ack_ms} ms`);
      if (job.timings?.run_ms != null) timing.push(`run ${(job.timings.run_ms / 1000).toFixed(1)} s`);
      if (job.timings?.announce_delay_ms != null) timing.push(`announced +${(job.timings.announce_delay_ms / 1000).toFixed(1)} s`);
      const cancel = ACTIVE.has(job.status) && !job.cancel_requested
        ? `<button class="link" data-cancel="${esc(job.job_id)}">Cancel</button>` : "";
      return `<li class="job">
        <div class="job-top"><span class="job-title">${esc(job.title)}</span>
          <span class="chip ${esc(job.status)}">${esc(job.status)}${esc(verified)}</span></div>
        ${job.outcome_text ? `<div class="job-outcome">${esc(job.outcome_text)}</div>` : ""}
        <div class="job-meta"><span>${esc(job.job_id)}</span><span>${esc(job.age)}</span>
          ${job.transport ? `<span>${esc(job.transport)}</span>` : ""}${timing.map((t) => `<span>${esc(t)}</span>`).join("")}
          ${job.announcement && job.announcement !== "none" ? `<span>announce: ${esc(job.announcement)}</span>` : ""}${cancel}</div>
      </li>`;
    })
    .join("");
}

$("jobs").addEventListener("click", async (event) => {
  const id = event.target?.dataset?.cancel;
  if (!id) return;
  event.target.disabled = true;
  if (ui.connected && ui.dc?.readyState === "open") {
    sendRtvi("client-message", { t: "clawvoice.cancel", d: { job_id: id } });
  } else {
    try { await postJSON(`/api/jobs/${encodeURIComponent(id)}/cancel?agent=${encodeURIComponent(ui.agent.id)}`); }
    catch (error) { banner(`Cancel failed: ${error.message}`); }
    refreshState();
  }
});

function upsertJob(job, event) {
  if (!job || (job.agent_id && ui.agent?.id && job.agent_id !== ui.agent.id)) return;
  ui.jobs.set(job.job_id, job);
  renderJobs();
  if (event === "job.finished" && !ui.noticed.has(job.job_id)) {
    ui.noticed.add(job.job_id);
    const icon = job.status === "completed" ? (job.verification === "verified" ? "✓" : "•") : job.status === "cancelled" ? "⨯" : "⚠";
    addTurn("notice", `${icon} ${ui.agent.display_name} task finished (${job.status}${job.verification ? `, ${job.verification}` : ""}): ${job.outcome_text}`);
  }
}

// ------------------------------------------------------------------ topics
function renderTopics(cache) {
  if (!cache) return;
  const list = $("topics");
  const rows = cache.topics || [];
  const m = cache.metrics || {};
  $("cache-metrics").textContent = m.read_ms_p50 != null ? `read p50 ${m.read_ms_p50} ms · ${m.hits_fresh} fresh hits` : "";
  list.innerHTML = rows
    .map((row) => {
      const age = row.age_seconds == null ? "—" : row.age_seconds < 90 ? `${row.age_seconds}s` : `${Math.round(row.age_seconds / 60)}m`;
      const note = row.last_error ? `error: ${row.last_error}` : row.invalidated ? `refreshing after ${row.invalidated}` : row.complete === false ? "incomplete source data" : "";
      return `<li class="topic" title="${esc(row.summary || "")}">
        <span class="topic-key">${esc(row.topic)}</span>
        <span class="topic-state ${esc(row.state)}">${esc(row.state)}${row.refreshing ? " ↻" : ""} · ${esc(age)}</span>
        ${note ? `<span class="topic-note">${esc(note)}</span>` : ""}
      </li>`;
    })
    .join("") || '<li class="empty">No topics configured.</li>';
}

function updateTopic(row) {
  if (!row) return;
  ui.topicRows = ui.topicRows || new Map();
  ui.topicRows.set(row.topic, row);
  renderTopics({ topics: [...ui.topicRows.values()], metrics: ui.lastMetrics || {} });
}

// ------------------------------------------------------------------ transcript
function addTurn(kind, text, { interim = false } = {}) {
  const box = $("transcript");
  box.querySelector(".hint")?.remove();
  const el = document.createElement("div");
  el.className = `turn ${kind}${interim ? " interim" : ""}`;
  const who = kind === "user" ? "You" : kind === "bot" ? ui.agent?.display_name || "Agent" : "ClawVoice";
  el.innerHTML = `<span class="who">${esc(who)}</span><span class="text"></span>`;
  el.querySelector(".text").textContent = text;
  box.append(el);
  box.scrollTop = box.scrollHeight;
  return el;
}

function setTurnText(el, text) {
  el.querySelector(".text").textContent = text;
  $("transcript").scrollTop = $("transcript").scrollHeight;
}

// ------------------------------------------------------------------ orb
function setOrb() {
  const orb = $("orb");
  const working = [...ui.jobs.values()].some((j) => ACTIVE.has(j.status));
  let state = "offline";
  let label = ui.connecting ? "Connecting…" : "Not connected";
  if (ui.connected) {
    state = ui.botSpeaking ? "speaking" : ui.userSpeaking ? "listening" : "idle";
    label = ui.botSpeaking ? "Speaking" : ui.userSpeaking ? "Listening" : "Ready — speak or type";
  }
  if (working) label += " · working in background";
  if (ui.pendingNotices) label += " · update waiting to speak";
  orb.className = `orb state-${state}${working ? " working" : ""}${ui.pendingNotices ? " waiting" : ""}`;
  $("agent-state").textContent = label;
}

function startLevels() {
  stopLevels();
  const buffer = new Uint8Array(256);
  const sample = (analyser) => {
    if (!analyser) return 0;
    analyser.getByteTimeDomainData(buffer);
    let peak = 0;
    for (const v of buffer) peak = Math.max(peak, Math.abs(v - 128));
    return Math.min(1, peak / 64);
  };
  ui.levelTimer = setInterval(() => {
    const level = ui.botSpeaking ? sample(ui.remoteAnalyser) : ui.userSpeaking ? sample(ui.localAnalyser) * 0.6 : 0;
    document.documentElement.style.setProperty("--level", level.toFixed(3));
  }, 60);
}

function stopLevels() {
  clearInterval(ui.levelTimer);
  document.documentElement.style.setProperty("--level", "0");
}

function analyserFor(stream) {
  try {
    const source = ui.audioCtx.createMediaStreamSource(stream);
    const analyser = ui.audioCtx.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);
    return analyser;
  } catch { return null; }
}

// ------------------------------------------------------------------ connection
async function getMicrophone() {
  try {
    return await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false },
      video: false,
    });
  } catch (error) {
    if (error?.name === "NotAllowedError") {
      throw new Error("Microphone permission was denied. Allow microphone access for this page (address bar → site settings), or tick “Connect without microphone”.");
    }
    if (error?.name === "NotFoundError") {
      throw new Error("No microphone was found. Plug one in, or tick “Connect without microphone”.");
    }
    throw new Error(`Microphone unavailable (${error?.name || "error"}).`);
  }
}

function silentStream() {
  const destination = ui.audioCtx.createMediaStreamDestination();
  return destination.stream;
}

function waitForIce(pc, timeoutMs) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => { pc.removeEventListener("icegatheringstatechange", check); resolve(); };
    const check = () => pc.iceGatheringState === "complete" && done();
    pc.addEventListener("icegatheringstatechange", check);
    setTimeout(done, timeoutMs);
  });
}

async function connect() {
  if (ui.connecting || ui.connected) return;
  banner(null);
  ui.connecting = true;
  setButtons();
  setOrb();
  try {
    ui.audioCtx = ui.audioCtx || new AudioContext();
    await ui.audioCtx.resume();
    ui.stream = $("typed-only").checked ? silentStream() : await getMicrophone();
    applyMicGate(false);
    ui.localAnalyser = $("typed-only").checked ? null : analyserFor(ui.stream);
    const start = await postJSON("/start", { transport: "webrtc", body: { agent: ui.agent.id } });
    const pc = new RTCPeerConnection({ iceServers: start.iceConfig?.iceServers || [] });
    ui.pc = pc;
    pc.addTransceiver(ui.stream.getAudioTracks()[0], { direction: "sendrecv", streams: [ui.stream] });
    const dc = pc.createDataChannel("rtvi", { ordered: true });
    ui.dc = dc;
    dc.onopen = onChannelOpen;
    dc.onmessage = (event) => onRtvi(event.data);
    dc.onclose = () => ui.connected && dropped("The voice connection closed.");
    pc.ontrack = (event) => {
      const stream = event.streams[0] || new MediaStream([event.track]);
      const audio = $("bot-audio");
      audio.srcObject = stream;
      audio.play().catch(() => banner("Click anywhere on the page to allow audio playback.", "info"));
      ui.remoteAnalyser = analyserFor(stream);
    };
    pc.onconnectionstatechange = () => {
      if (["failed", "disconnected"].includes(pc.connectionState) && ui.connected) {
        dropped(`WebRTC connection ${pc.connectionState}. Your background tasks keep running; reconnect to hear results.`);
      }
    };
    await pc.setLocalDescription(await pc.createOffer());
    await waitForIce(pc, 2500);
    const answer = await postJSON(`/sessions/${encodeURIComponent(start.sessionId)}/api/offer`, {
      sdp: pc.localDescription.sdp,
      type: "offer",
    });
    await pc.setRemoteDescription(answer);
  } catch (error) {
    ui.connecting = false;
    teardown();
    banner(error.message || String(error));
  }
}

function onChannelOpen() {
  sendRtvi("client-ready", { version: "1.0.0", about: { library: "clawvoice-web", library_version: "0.1.0" } });
  ui.ping = setInterval(() => {
    if (ui.dc?.readyState !== "open") return;
    ui.dc.send("ping"); // SmallWebRTC transport keepalive
    sendRtvi("client-message", { t: "clawvoice.alive", d: {} }); // lets the server know we can hear notices
  }, 2000);
  sendRtvi("client-message", { t: "clawvoice.alive", d: {} });
  ui.connecting = false;
  ui.connected = true;
  setButtons();
  setOrb();
  startLevels();
}

function sendRtvi(type, data) {
  if (ui.dc?.readyState !== "open") return false;
  ui.dc.send(JSON.stringify({ id: rid(), label: "rtvi-ai", type, data }));
  return true;
}

function disconnect() {
  sendRtvi("disconnect-bot", {});
  teardown();
  addTurn("notice", "Disconnected. Background tasks keep running; reconnect to hear their results.");
}

function dropped(message) {
  teardown();
  banner(message, "info");
}

function teardown() {
  applyMicGate(false);
  clearInterval(ui.ping);
  stopLevels();
  try { ui.dc?.close(); } catch { /* ignore */ }
  try { ui.pc?.close(); } catch { /* ignore */ }
  ui.stream?.getTracks().forEach((t) => t.stop());
  $("bot-audio").srcObject = null;
  Object.assign(ui, { dc: null, pc: null, stream: null, connected: false, connecting: false, botTurn: null,
    userInterim: null, botSpeaking: false, userSpeaking: false, pendingNotices: 0 });
  $("model-badge").textContent = `${MODEL} · not connected`;
  $("model-badge").className = "badge";
  setButtons();
  setOrb();
}

function setButtons() {
  $("connect").disabled = ui.connected || ui.connecting;
  $("connect").textContent = ui.connecting ? "Connecting…" : "Connect";
  $("disconnect").disabled = !ui.connected;
  $("mute").disabled = !ui.connected || $("typed-only").checked || $("ptt-mode").checked;
  $("typed-only").disabled = ui.connected || ui.connecting;
  $("push-talk").hidden = !$("ptt-mode").checked;
  $("push-talk").disabled = !ui.connected || $("typed-only").checked;
  $("message").disabled = !ui.connected;
  $("send").disabled = !ui.connected;
}

// ------------------------------------------------------------------ RTVI
function onRtvi(raw) {
  let message;
  try { message = JSON.parse(raw); } catch { return; }
  const data = message.data || {};
  switch (message.type) {
    case "bot-ready":
      logLine("events", "bot ready");
      break;
    case "user-transcription":
      if (!data.text) break;
      if (data.final) {
        if (ui.userInterim) { setTurnText(ui.userInterim, data.text); ui.userInterim.classList.remove("interim"); ui.userInterim = null; }
        else addTurn("user", data.text);
      } else if (ui.userInterim) setTurnText(ui.userInterim, data.text);
      else ui.userInterim = addTurn("user", data.text, { interim: true });
      break;
    case "user-started-speaking":
      ui.userSpeaking = true; setOrb();
      break;
    case "user-stopped-speaking":
      ui.userSpeaking = false; setOrb();
      break;
    case "bot-started-speaking":
      ui.botSpeaking = true; ui.userSpeaking = false; setOrb();
      break;
    case "bot-stopped-speaking":
      ui.botSpeaking = false; setOrb();
      break;
    case "bot-llm-started":
    case "bot-llm-stopped":
      // One bubble per model response (audio pauses between sentences do not split it).
      ui.botTurn = null;
      break;
    case "bot-interrupted": {
      const bubbles = document.querySelectorAll(".turn.bot");
      bubbles[bubbles.length - 1]?.classList.add("interrupted");
      ui.botTurn = null; ui.botSpeaking = false; setOrb();
      logLine("events", "assistant interrupted (speech only; background tasks continue)");
      break;
    }
    case "bot-llm-text": {
      // One message per spoken transcript chunk. (RTVI's bot-output repeats the
      // same text when it is announced and again when spoken, so it is ignored.)
      if (!data.text) break;
      if (!ui.botTurn) ui.botTurn = addTurn("bot", "");
      const current = ui.botTurn.querySelector(".text").textContent;
      setTurnText(ui.botTurn, (current + data.text).replace(/\s+/g, " ").trimStart());
      break;
    }
    case "llm-function-call-in-progress":
      logLine("tools", `→ ${data.function_name} ${JSON.stringify(data.arguments || {})}`);
      break;
    case "error":
      banner(`Voice session error: ${data.error || data.message || "unknown"}`);
      logLine("events", `error ${JSON.stringify(data)}`);
      break;
    case "server-message":
      onServerMessage(data);
      break;
    default:
      break;
  }
}

function onServerMessage(data) {
  switch (data.kind) {
    case "preview-model": {
      const ok = data.acknowledged === MODEL;
      $("model-badge").textContent = ok ? `${MODEL} · verified` : `${data.configured} · awaiting acknowledgement`;
      $("model-badge").className = `badge ${ok ? "ok" : "warn"}`;
      break;
    }
    case "boson.recv":
    case "boson.send":
    case "preview-diagnostic": {
      // Server diagnostics carry the trace entry's own kind (boson.recv / boson.send).
      const event = data.event || {};
      const type = event.type || data.kind;
      if (type === "session.created" || type === "session.updated") {
        const acknowledged = event.session?.model;
        $("model-badge").textContent = acknowledged === MODEL ? `${MODEL} · verified` : `unexpected model ${acknowledged}`;
        $("model-badge").className = `badge ${acknowledged === MODEL ? "ok" : "bad"}`;
      }
      if (type === "conversation.item.truncate") logLine("events", `truncate ${event.item_id || ""} at ${event.audio_end_ms ?? "?"} ms`);
      else if (type && type !== "response.output_audio.delta") logLine("events", type);
      break;
    }
    case "clawvoice.hello":
      applyState(data.state);
      break;
    case "clawvoice.state":
      applyState(data.state);
      break;
    case "clawvoice.job":
      upsertJob(data.job, data.event);
      break;
    case "clawvoice.cache":
      updateTopic(data.row);
      break;
    case "clawvoice.tool": {
      const extra = [data.cache && `cache ${data.cache}`, data.status, data.age_seconds != null && `age ${data.age_seconds}s`, data.read_ms != null && `read ${data.read_ms} ms`, data.job_id]
        .filter(Boolean).join(" · ");
      logLine("tools", `${data.phase === "started" ? "▶" : "■"} ${data.name}${data.ms != null ? ` ${data.ms} ms` : ""}${extra ? ` (${extra})` : ""}`);
      break;
    }
    case "clawvoice.latency":
      $("latency").textContent = `last ${data.origin?.replace("_", " ")} → first audio: ${data.first_audio_ms} ms`;
      logLine("events", `latency ${data.origin} → first audio ${data.first_audio_ms} ms`);
      break;
    case "clawvoice.floor":
      ui.pendingNotices = (data.pending || []).length + (data.delivering || []).length;
      setOrb();
      break;
    case "clawvoice.cancel":
      addTurn("notice", `Cancel ${data.job_id}: ${data.message || data.status}`);
      break;
    default:
      break;
  }
}

// ------------------------------------------------------------------ wiring
$("agent-selector").addEventListener("click", (event) => {
  const btn = event.target?.closest ? event.target.closest("[data-agent]") : event.target;
  const agentId = btn?.dataset?.agent;
  if (agentId) selectAgent(agentId);
});
$("connect").addEventListener("click", connect);
$("disconnect").addEventListener("click", disconnect);
$("typed-only").addEventListener("change", setButtons);
let micMuted = false;
let talkHeld = false;
function applyMicGate(held = talkHeld) {
  talkHeld = held;
  const track = ui.stream?.getAudioTracks()[0];
  if (track) track.enabled = !$("typed-only").checked && !micMuted && (!$("ptt-mode").checked || held);
  $("push-talk").setAttribute("aria-pressed", String(held));
  $("mute").textContent = micMuted ? "Unmute mic" : "Mute mic";
  $("mute").setAttribute("aria-pressed", String(micMuted));
}
$("mute").addEventListener("click", () => { micMuted = !micMuted; applyMicGate(); });
$("ptt-mode").addEventListener("change", () => { micMuted = false; applyMicGate(false); setButtons(); });
$("push-talk").addEventListener("pointerdown", (event) => {
  if (!ui.connected || $("push-talk").disabled) return;
  event.preventDefault();
  $("push-talk").setPointerCapture(event.pointerId);
  applyMicGate(true);
});
for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) {
  $("push-talk").addEventListener(type, () => applyMicGate(false));
}
$("push-talk").addEventListener("keydown", (event) => {
  if (["Space", "Enter"].includes(event.code) && !event.repeat && !$("push-talk").disabled) {
    event.preventDefault(); applyMicGate(true);
  }
});
$("push-talk").addEventListener("keyup", (event) => {
  if (["Space", "Enter"].includes(event.code)) { event.preventDefault(); applyMicGate(false); }
});
$("push-talk").addEventListener("blur", () => applyMicGate(false));
window.addEventListener("keydown", (event) => {
  if (event.code !== "Space" || event.repeat || !ui.connected || !$("ptt-mode").checked ||
      $("typed-only").checked || event.target.closest("input, textarea, select, button, [contenteditable]")) return;
  event.preventDefault(); applyMicGate(true);
});
window.addEventListener("keyup", (event) => { if (event.code === "Space") applyMicGate(false); });
window.addEventListener("blur", () => applyMicGate(false));
document.addEventListener("visibilitychange", () => { if (document.hidden) applyMicGate(false); });
$("composer").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("message").value.trim();
  if (!text || !ui.connected) return;
  if (sendRtvi("send-text", { content: text, options: { audio_response: true, run_immediately: true } })) {
    addTurn("user", text);
    $("message").value = "";
  }
});
window.addEventListener("beforeunload", () => ui.connected && sendRtvi("disconnect-bot", {}));

init();
