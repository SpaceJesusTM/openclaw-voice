// Chorus local UI (the ClawVoice runtime keeps its internal names).
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
  thinking: false,
  // Team mode: independent agents sharing one speaking floor.
  mode: "single",
  team: null,
  teamAgents: new Map(),
  teamFloor: null,
  teamStatus: "",
  replies: new Map(),
};

const TEAM = "team";
const isTeam = () => ui.mode === TEAM;
const teamAgent = (id) => ui.teamAgents.get(id) || (ui.agents || []).find((a) => a.id === id) || { id, display_name: id || "Agent", color: "#64748b", emoji: "" };

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
  const n = parseInt(String(hex || "#d97757").slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

// ------------------------------------------------------------------ stage, activity, preview
// Classic scripts loaded before this module: the orb waveform (stage.js), the
// "Now" list (activity.js) and the coding agent's preview pane (preview.js).
const orbNode = $("orb");
const stage = globalThis.ClawStage && orbNode?.querySelector?.("canvas")?.getContext ? globalThis.ClawStage.mount(orbNode) : null;
const activity = globalThis.ClawActivity ? globalThis.ClawActivity.mount($("activity")) : null;
const preview = globalThis.ClawPreview ? globalThis.ClawPreview.mount(document) : null;
const clip = (text, n = 64) => (text.length > n ? `${text.slice(0, n - 1).trimEnd()}…` : text);

function findCodingAgent() {
  return (ui.agents || []).find((a) => (a?.topics || []).some((t) => t.key === "projects.active"))
    || [...ui.teamAgents.values()].find((a) => (a?.topics || []).some((t) => t.key === "projects.active"));
}

function syncPreview() {
  if (!preview) return;
  if (isTeam()) {
    const codingAgent = findCodingAgent();
    if (!codingAgent) {
      preview.sync({ agent: null, jobs: [], topics: [] });
      return;
    }
    const jobs = [...ui.jobs.values()].filter((j) => j.agent_id === codingAgent.id);
    const topics = [...(ui.topicRows?.values() || [])].filter((r) => r.agent === codingAgent.id);
    preview.sync({ agent: codingAgent, jobs, topics });
    return;
  }
  preview.sync({ agent: ui.agent, jobs: [...ui.jobs.values()], topics: [...(ui.topicRows?.values() || [])] });
}

// ------------------------------------------------------------------ agent + state
function shortRole(agent) {
  if (!agent) return "";
  if (agent.tagline) return agent.tagline;
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
  const teamTab = ui.team?.available
    ? `<button type="button" id="select-agent-team" class="agent-tab team-tab${isTeam() ? " active" : ""}"
        role="tab" aria-selected="${isTeam()}" data-agent="${TEAM}" style="--agent-color: #d97757">
        <span class="agent-dot" aria-hidden="true"></span>
        <span class="agent-tab-text">
          <span class="agent-tab-name">Team</span>
          <span class="agent-tab-role">${esc([...ui.teamAgents.values()].map((a) => a.display_name).join(" + "))} · ${ui.team?.router_mode === "names" ? "by name" : "auto routing"}</span>
        </span>
      </button>`
    : "";
  bar.innerHTML = teamTab + agents
    .map((a) => {
      const selected = !isTeam() && a.id === ui.agent?.id;
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
  ui.replies = new Map();
  if (isTeam()) {
    box.innerHTML = ui.team?.router_mode === "names"
      ? `<p class="hint">Press <strong>Connect</strong> once and address an agent by name. Requests without a name go to nobody. Use headphones to avoid echo.</p>`
      : `<p class="hint">Press <strong>Connect</strong> and talk naturally: “What's on my planner today?”, “What's the latest website update?” or “Tell me a joke.” You can also address an agent by name. Use headphones to avoid echo.</p>`;
    return;
  }
  const example = agent?.voice_only
    ? "Tell me something funny."
    : agent?.id === "astra"
      ? "create a simple website for a fictional coffee shop."
      : "What's on my planner today?";
  box.innerHTML = `<p class="hint">Press <strong>Connect</strong>, allow the microphone, and say “${esc(example)}” Use headphones to avoid echo.</p>`;
}

async function selectAgent(agentId) {
  const team = agentId === TEAM;
  if (team && !ui.team?.available) return;
  const next = team ? ui.agent : (ui.agents || []).find((a) => a.id === agentId);
  if (!next) return;
  if (team ? isTeam() : !isTeam() && ui.agent?.id === next.id) return;
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
  ui.mode = team ? TEAM : "single";
  ui.agent = next;
  ui.jobs = new Map();
  ui.noticed = new Set();
  ui.topicRows = new Map();
  ui.lastMetrics = null;
  ui.lastPoll = 0;
  activity?.reset();
  if (typeof history !== "undefined" && history.replaceState) {
    const url = new URL(location.href);
    url.searchParams.set("agent", team ? TEAM : next.id);
    history.replaceState(null, "", url.toString());
  }
  renderAgentSelector();
  renderIdentity();
  resetConversationForAgent(next);
  renderJobs();
  renderTopics({ topics: [], metrics: {} });
  renderSeats();
  setButtons();
  await refreshState(true);
  if (wasActive) {
    await connect();
  }
}

async function init() {
  try {
    const data = await (await fetch("/api/agents")).json();
    ui.agents = data.agents || [];
    ui.team = data.team || null;
    ui.teamAgents = new Map(ui.agents.filter((a) => ui.team?.agents?.includes(a.id)).map((a) => [a.id, a]));
    const wanted = new URLSearchParams(location.search).get("agent");
    if (wanted === TEAM && ui.team?.available) ui.mode = TEAM;
    ui.agent = ui.agents.find((a) => a.id === wanted) || ui.agents.find((a) => a.id === data.default) || ui.agents[0];
    renderAgentSelector();
    renderIdentity();
    resetConversationForAgent(ui.agent);
    renderSeats();
    await refreshState(true);
  } catch (error) {
    banner("Chorus server is not reachable. Start it with `make dev` in services/voice.");
  }
  ui.poll = setInterval(refreshState, 2500);
  setOrb();
  setButtons();
  // Long enough for the first preview probe; later openings animate.
  globalThis.setTimeout?.(() => document.body?.classList?.remove("booting"), 1500);
}

function renderIdentity() {
  const a = ui.agent;
  if (!a) return;
  const team = isTeam();
  document.body?.setAttribute?.("data-mode", team ? TEAM : "single");
  $("orb").hidden = false;
  $("agent-state").hidden = false;
  $("team-seats").hidden = !team;
  $("team-queue").hidden = !team;
  if (team) {
    const names = [...ui.teamAgents.values()].map((x) => x.display_name);
    document.title = "Chorus · Team";
    $("agent-name").textContent = "Team";
    $("agent-role").textContent = `${names.join(" and ")} in one room. Start with a name: “Hey ${names[0]}…”, “Okay ${names[1] || names[0]}…”.`;
    document.documentElement.style.setProperty("--accent", a.color);
    document.documentElement.style.setProperty("--accent-soft", hexToSoft(a.color, 0.22));
    return;
  }
  document.title = `Chorus · ${a.display_name}`;
  $("agent-name").textContent = a.display_name;
  $("agent-role").textContent = a.role || "";
  document.documentElement.style.setProperty("--accent", a.color);
  document.documentElement.style.setProperty("--accent-soft", hexToSoft(a.color, 0.22));
}

async function refreshState(force = false) {
  if (!ui.agent) return;
  // While a task runs, poll often so its tool steps reach "Now". Otherwise live
  // updates arrive over RTVI and polling is a slow backstop.
  const busy = [...ui.jobs.values()].some((j) => ACTIVE.has(j.status));
  const gap = busy ? 2000 : ui.connected ? 15000 : 3500;
  if (!force && Date.now() - (ui.lastPoll || 0) < gap) return;
  if (isTeam()) {
    ui.lastPoll = Date.now();
    try {
      const ids = ui.team?.agents || [];
      const states = await Promise.all(ids.map(async (id) => {
        const response = await fetch(`/api/state?agent=${encodeURIComponent(id)}`);
        return response.ok ? [id, await response.json()] : null;
      }));
      if (isTeam()) applyTeamStates(Object.fromEntries(states.filter(Boolean)));
    } catch { /* server restarting; next poll will retry */ }
    return;
  }
  const requestedAgentId = ui.agent.id;
  ui.lastPoll = Date.now();
  try {
    const response = await fetch(`/api/state?agent=${encodeURIComponent(requestedAgentId)}`);
    if (!response.ok || ui.agent?.id !== requestedAgentId || isTeam()) return;
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

function applyTeamStates(states) {
  if (!states || !isTeam()) return;
  ui.jobs = new Map();
  ui.topicRows = new Map();
  const gateways = [];
  for (const [id, state] of Object.entries(states)) {
    for (const job of state.jobs || []) ui.jobs.set(`${id}:${job.job_id}`, { ...job, agent_id: job.agent_id || id });
    for (const row of state.cache?.topics || []) ui.topicRows.set(`${id}:${row.topic}`, { ...row, agent: id });
    gateways.push([id, state.gateway]);
  }
  renderTeamGateways(gateways);
  renderJobs();
  renderTopics({ topics: [...ui.topicRows.values()], metrics: {} });
}

function renderTeamGateways(gateways) {
  const badge = $("gateway-badge");
  // A voice-only agent has no gateway: shown as such, never as a failure.
  const parts = gateways.map(([id, g]) => `${teamAgent(id).display_name} ${g?.voice_only ? "voice only" : g?.active_transport === "ws" ? "WebSocket" : g?.active_transport === "http" ? "HTTP" : "unavailable"}`);
  const ok = gateways.every(([, g]) => g && (g.voice_only || g.active_transport === "ws" || g.active_transport === "http"));
  badge.textContent = `Gateways · ${parts.join(" · ")}`;
  badge.className = `status-line badge ${ok ? "ok" : "bad"}`;
  const problem = gateways.find(([, g]) => g?.config_error);
  if (problem) {
    const [id, g] = problem;
    banner(`${teamAgent(id).display_name} isn't connected to its OpenClaw gateway on this machine. ${g.config_error}`);
  }
}

function renderGateway(gateway) {
  const badge = $("gateway-badge");
  if (!gateway) return;
  const mode = gateway.active_transport;
  const ws = gateway.websocket;
  if (gateway.voice_only) {
    badge.textContent = "Voice only · no gateway needed";
    badge.className = "status-line badge ok";
    badge.title = gateway.note || "This agent talks through Higgs only: no OpenClaw gateway, tools or background jobs.";
    return;
  }
  if (gateway.config_error) {
    badge.textContent = "Gateway · configuration error";
    badge.className = "status-line badge bad";
    banner(`${ui.agent?.display_name || "This agent"} isn't connected to its OpenClaw gateway on this machine. ${gateway.config_error}`);
    return;
  }
  if (mode === "ws") {
    badge.textContent = "Gateway · WebSocket (paired)";
    badge.className = "status-line badge ok";
  } else if (mode === "http") {
    badge.textContent = ws && ws.state === "pairing_required" ? "Gateway · HTTP (pairing pending)" : "Gateway · HTTP";
    badge.className = "status-line badge ok";
    badge.title = gateway.pairing_hint
      ? `Optional: approve the WebSocket device for run IDs and recovery: ${gateway.pairing_hint}`
      : "OpenClaw Gateway reachable over HTTP";
  } else {
    badge.textContent = "Gateway · unavailable";
    badge.className = "status-line badge bad";
  }
}

// ------------------------------------------------------------------ jobs
function renderJobs() {
  const list = $("jobs");
  const jobs = [...ui.jobs.values()].sort((a, b) => (a.created_at < b.created_at ? 1 : -1)).slice(0, 12);
  const active = jobs.filter((j) => ACTIVE.has(j.status)).length;
  $("jobs-count").textContent = active ? `${active} running` : "";
  setOrb();
  syncPreview();
  const team = isTeam();
  activity?.jobTools(jobs, team ? (j) => teamAgent(j.agent_id).display_name : null);
  if (!jobs.length) {
    list.innerHTML = '<li class="empty">No tasks yet.</li>';
    return;
  }
  list.innerHTML = jobs
    .map((job) => {
      const running = ACTIVE.has(job.status);
      const verified = job.verification === "verified" ? " · verified" : job.verification === "mismatch" ? " · mismatch" : "";
      const status = running ? `${job.status} · ${job.age}` : `${job.status}${verified}`;
      // Ids, transport and timings stay available on hover rather than as visible clutter.
      const details = [job.job_id, job.age, job.transport];
      if (job.timings?.dispatch_ack_ms != null) details.push(`ack ${job.timings.dispatch_ack_ms} ms`);
      if (job.timings?.run_ms != null) details.push(`run ${(job.timings.run_ms / 1000).toFixed(1)} s`);
      if (job.timings?.announce_delay_ms != null) details.push(`announced +${(job.timings.announce_delay_ms / 1000).toFixed(1)} s`);
      if (job.announcement && job.announcement !== "none") details.push(`announce: ${job.announcement}`);
      const owner = team ? teamAgent(job.agent_id) : null;
      const ownerChip = owner ? `<span class="owner" style="--owner-color: ${esc(owner.color)}">${esc(owner.display_name)}</span>` : "";
      const cancel = running && !job.cancel_requested
        ? `<button class="link" data-cancel="${esc(job.job_id)}" data-agent="${esc(job.agent_id || ui.agent?.id || "")}">Cancel</button>` : "";
      const meta = ownerChip || cancel ? `<div class="job-meta">${ownerChip}${cancel}</div>` : "";
      return `<li class="job" title="${esc(details.filter(Boolean).join(" · "))}">
        <div class="job-top"><span class="job-title">${esc(job.title)}</span>
          <span class="chip ${esc(job.status)}">${esc(status)}</span></div>
        ${running ? '<div class="job-run" aria-hidden="true"></div>' : ""}
        ${!running && job.outcome_text ? `<div class="job-outcome" title="${esc(job.outcome_text)}">${esc(job.outcome_text)}</div>` : ""}
        ${meta}
      </li>`;
    })
    .join("");
}

$("jobs").addEventListener("click", async (event) => {
  const id = event.target?.dataset?.cancel;
  if (!id) return;
  const agent = event.target.dataset.agent || ui.agent?.id;
  event.target.disabled = true;
  if (ui.connected && ui.dc?.readyState === "open") {
    sendRtvi("client-message", { t: "clawvoice.cancel", d: { job_id: id, agent } });
  } else {
    try { await postJSON(`/api/jobs/${encodeURIComponent(id)}/cancel?agent=${encodeURIComponent(agent)}`); }
    catch (error) { banner(`Cancel failed: ${error.message}`); }
    refreshState(true);
  }
});

function upsertJob(job, event) {
  if (!job) return;
  if (isTeam()) {
    if (job.agent_id && !ui.teamAgents.has(job.agent_id)) return;
  } else if (job.agent_id && ui.agent?.id && job.agent_id !== ui.agent.id) return;
  const key = isTeam() ? `${job.agent_id}:${job.job_id}` : job.job_id;
  ui.jobs.set(key, job);
  renderJobs();
  const ownerName = isTeam() ? teamAgent(job.agent_id).display_name : "";
  activity?.job(job, event, ownerName);
  if (event === "job.finished" && !ui.noticed.has(key)) {
    ui.noticed.add(key);
    const icon = job.status === "completed" ? (job.verification === "verified" ? "✓" : "•") : job.status === "cancelled" ? "⨯" : "⚠";
    const name = isTeam() ? teamAgent(job.agent_id).display_name : ui.agent.display_name;
    addTurn("notice", `${icon} ${name} task finished (${job.status}${job.verification ? `, ${job.verification}` : ""}): ${job.outcome_text}`);
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
      const key = row.agent ? `${teamAgent(row.agent).display_name} · ${row.topic}` : row.topic;
      return `<li class="topic" title="${esc(row.summary || "")}">
        <span class="topic-key">${esc(key)}</span>
        <span class="topic-state ${esc(row.state)}">${esc(row.state)}${row.refreshing ? " ↻" : ""} · ${esc(age)}</span>
        ${note ? `<span class="topic-note">${esc(note)}</span>` : ""}
      </li>`;
    })
    .join("") || (!isTeam() && ui.agent?.voice_only
      ? '<li class="empty">Voice-only agent: no fast context or background tasks.</li>'
      : '<li class="empty">No topics configured.</li>');
  syncPreview();
}

function updateTopic(row, agent) {
  if (!row) return;
  ui.topicRows = ui.topicRows || new Map();
  if (isTeam() && agent) ui.topicRows.set(`${agent}:${row.topic}`, { ...row, agent });
  else if (!isTeam()) ui.topicRows.set(row.topic, row);
  renderTopics({ topics: [...ui.topicRows.values()], metrics: ui.lastMetrics || {} });
}

// ------------------------------------------------------------------ transcript
function addTurn(kind, text, { interim = false, speaker = null } = {}) {
  const box = $("transcript");
  box.querySelector(".hint")?.remove();
  const el = document.createElement("div");
  el.className = `turn ${kind}${interim ? " interim" : ""}`;
  if (speaker?.color) el.style.setProperty("--who-color", speaker.color);
  const who = kind === "user" ? "You" : kind === "bot" ? speaker?.display_name || ui.agent?.display_name || "Agent" : "Chorus";
  const whoHtml = kind === "user" ? `<span class="who-label">${esc(who)}</span>` : esc(who);
  el.innerHTML = `<span class="who">${whoHtml}</span><span class="text"></span>`;
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
  if (isTeam()) { renderSeats(); return; }
  const orb = $("orb");
  const working = [...ui.jobs.values()].some((j) => ACTIVE.has(j.status));
  const thinking = ui.connected && ui.thinking && !ui.botSpeaking && !ui.userSpeaking;
  let state = "offline";
  let label = ui.connecting ? "Connecting…" : "Not connected";
  if (ui.connected) {
    state = ui.botSpeaking ? "speaking" : ui.userSpeaking ? "listening" : "idle";
    label = ui.botSpeaking ? "Speaking" : ui.userSpeaking ? "Listening" : thinking ? "Thinking" : "Ready — speak or type";
  }
  if (working) label += " · working in the background";
  if (ui.pendingNotices) label += " · has an update for you";
  orb.className = `orb state-${state}${thinking ? " thinking" : ""}${working ? " working" : ""}${ui.pendingNotices ? " waiting" : ""}`;
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
    stage?.setAnalysers({ local: ui.localAnalyser });
    const start = await postJSON("/start", { transport: "webrtc", body: isTeam() ? { mode: TEAM } : { agent: ui.agent.id } });
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
      stage?.setAnalysers({ remote: ui.remoteAnalyser });
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
    userInterim: null, botSpeaking: false, userSpeaking: false, pendingNotices: 0, thinking: false,
    teamFloor: null, teamStatus: "", localAnalyser: null, remoteAnalyser: null });
  stage?.setAnalysers({ local: null, remote: null });
  $("model-badge").textContent = `${MODEL} · not connected`;
  $("model-badge").className = "status-line badge";
  setButtons();
  setOrb();
}

function setButtons() {
  // The stylesheet shows one main call button at a time from these attributes.
  document.body?.setAttribute?.("data-call", ui.connected ? "live" : ui.connecting ? "connecting" : "idle");
  document.body?.setAttribute?.("data-ptt", $("ptt-mode").checked ? "on" : "off");
  $("connect").disabled = ui.connected || ui.connecting;
  $("connect").setAttribute("aria-label", ui.connecting ? "Connecting" : "Connect");
  $("connect").setAttribute("data-busy", String(ui.connecting));
  $("keyboard")?.setAttribute("aria-pressed", String($("typed-only").checked));
  $("keyboard")?.setAttribute("title", ui.connected ? "Type a message" : $("typed-only").checked
    ? "Type only: no microphone on the next connect" : "Type instead of talking");
  const targetName = isTeam()
    ? [...ui.teamAgents.values()].map((a) => a.display_name).join(" or ") || "Tari or Astra"
    : ui.agent?.display_name || "the agent";
  $("message").placeholder = ui.connected
    ? isTeam() ? `Message ${targetName}${ui.team?.router_mode === "names" ? " by name" : ""}…` : `Message ${targetName}`
    : "Connect to type a message";
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
        activity?.newTurn(`Heard “${clip(data.text)}”`);
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
      ui.botSpeaking = true; ui.userSpeaking = false; ui.thinking = false; setOrb();
      activity?.speaking();
      break;
    case "bot-stopped-speaking":
      ui.botSpeaking = false; setOrb();
      activity?.settle();
      break;
    case "bot-llm-started":
      // One bubble per model response (audio pauses between sentences do not split it).
      ui.botTurn = null; ui.thinking = true; setOrb();
      if (!ui.botSpeaking) activity?.thinking();
      break;
    case "bot-llm-stopped":
      ui.botTurn = null; ui.thinking = false; setOrb();
      break;
    case "bot-interrupted": {
      if (isTeam()) { ui.botSpeaking = false; break; }
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
      $("model-badge").className = `status-line badge ${ok ? "ok" : "warn"}`;
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
        $("model-badge").className = `status-line badge ${acknowledged === MODEL ? "ok" : "bad"}`;
      }
      if (type === "conversation.item.truncate") logLine("events", `truncate ${event.item_id || ""} at ${event.audio_end_ms ?? "?"} ms`);
      else if (type && type !== "response.output_audio.delta") logLine("events", type);
      break;
    }
    case "clawvoice.hello":
      if (data.team) applyTeamStates(data.states);
      else applyState(data.state);
      break;
    case "clawvoice.state":
      applyState(data.state);
      break;
    case "clawvoice.team.ready":
      ui.teamStatus = "";
      if (ui.team && data.router_mode) ui.team.router_mode = data.router_mode;
      for (const a of data.agents || []) ui.teamAgents.set(a.id, { ...teamAgent(a.id), ...a });
      logLine("events", `team ready: ${(data.agents || []).map((a) => `${a.display_name} (${a.voice})`).join(", ")}`);
      renderSeats();
      syncPreview();
      break;
    case "clawvoice.team.floor":
      ui.teamFloor = data;
      if (ui.team && data.router_mode) ui.team.router_mode = data.router_mode;
      ui.botSpeaking = Boolean(data.speaking);
      renderSeats();
      break;
    case "clawvoice.team.reply":
      onTeamReply(data);
      break;
    case "clawvoice.team.user":
      onTeamUser(data);
      break;
    case "clawvoice.team.hearing":
      ui.userSpeaking = !!data.on;
      if (data.on) ui.teamStatus = "";
      renderSeats();
      break;
    case "clawvoice.team.status":
      if (data.text) logLine("events", data.text);
      ui.teamStatus = data.text || "";
      renderSeats();
      break;
    case "clawvoice.team.error":
      banner(data.message || "Team room problem", data.fatal ? "error" : "info");
      logLine("events", `team error: ${data.message}`);
      break;
    case "clawvoice.stop":
      activity?.settle();
      logLine("events", `stop speaking${data.agent ? ` (${teamAgent(data.agent).display_name})` : ""}; background tasks keep running`);
      break;
    case "clawvoice.job":
      upsertJob(data.job, data.event);
      break;
    case "clawvoice.cache":
      updateTopic(data.row, data.agent);
      break;
    case "clawvoice.tool": {
      const who = data.agent ? teamAgent(data.agent).display_name : "";
      activity?.tool(data, who);
      const extra = [data.cache && `cache ${data.cache}`, data.status, data.age_seconds != null && `age ${data.age_seconds}s`, data.read_ms != null && `read ${data.read_ms} ms`, data.job_id]
        .filter(Boolean).join(" · ");
      const owner = who ? `${who}: ` : "";
      logLine("tools", `${data.phase === "started" ? "▶" : "■"} ${owner}${data.name}${data.ms != null ? ` ${data.ms} ms` : ""}${extra ? ` (${extra})` : ""}`);
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

// ------------------------------------------------------------------ team mode
const SEAT_LABEL = {
  speaking: "Speaking",
  waiting: "Waiting to speak",
  thinking: "Thinking…",
  listening: "Listening…",
  idle: "Ready",
};

function ensureSeats() {
  const box = $("team-seats");
  const ids = ui.team?.agents || [];
  if (box.dataset.agents === ids.join(",")) return;
  box.dataset.agents = ids.join(",");
  box.innerHTML = ids.map((id) => {
    const a = teamAgent(id);
    return `<div class="seat state-offline" data-agent="${esc(id)}" style="--seat-color: ${esc(a.color)}; --seat-soft: ${hexToSoft(a.color, 0.22)}">
      <span class="seat-dot" aria-hidden="true"></span>
      <div class="orb state-offline" aria-hidden="true"><div class="orb-core"></div><div class="orb-ring"></div><div class="orb-work"></div></div>
      <div class="seat-name">${esc(a.display_name)}</div>
      <div class="seat-state"></div>
      <div class="seat-chips"></div>
    </div>`;
  }).join("");
}

function renderSeats() {
  if (!isTeam()) return;
  ensureSeats();
  const floor = ui.teamFloor || {};
  const states = floor.agents || {};
  let anyWorking = false;
  let anyWaiting = false;
  const thinkingNames = [];
  const listeningNames = [];

  for (const seat of $("team-seats").querySelectorAll(".seat")) {
    const id = seat.dataset.agent;
    const s = states[id] || {};
    const jobsActive = [...ui.jobs.values()].filter((j) => j.agent_id === id && ACTIVE.has(j.status)).length;
    const working = Math.max(jobsActive, s.working || 0);
    if (working > 0) anyWorking = true;
    const state = ui.connected ? (s.state || "idle") : "offline";
    if (state === "waiting" || s.notices) anyWaiting = true;
    if (state === "thinking") thinkingNames.push(teamAgent(id).display_name);
    if (state === "listening") listeningNames.push(teamAgent(id).display_name);

    const orbState = state === "waiting" ? "idle" : state;
    seat.className = `seat state-${state}${s.addressed && ui.connected ? " addressed" : ""}${ui.connected && s.online === false ? " offline" : ""}`;
    seat.querySelector(".orb").className =
      `orb state-${orbState}${working ? " working" : ""}${state === "waiting" || s.notices ? " waiting" : ""}`;
    seat.querySelector(".seat-state").textContent = ui.connected
      ? SEAT_LABEL[state] || state
      : ui.connecting ? "Connecting…" : "Not connected";
    const chips = [];
    if (s.addressed && ui.connected) chips.push('<span class="chip">active conversation</span>');
    if (working) chips.push(`<span class="chip running">${working} task${working > 1 ? "s" : ""} running</span>`);
    if (s.notices) chips.push('<span class="chip queued">update waiting</span>');
    if (s.silenced) chips.push('<span class="chip">quiet after stop</span>');
    if (ui.connected && s.online === false) chips.push('<span class="chip failed">voice offline</span>');
    seat.querySelector(".seat-chips").innerHTML = chips.join("");
  }

  // Update the central waveform orb and stage status for the shared Team floor.
  const orb = $("orb");
  const speakingAgent = floor.speaking ? teamAgent(floor.speaking) : null;
  const userTalking = Boolean(floor.user_speaking || ui.userSpeaking);
  const teamThinking = ui.connected && !speakingAgent && !userTalking && thinkingNames.length > 0;
  let orbState = "offline";
  let stageLabel = ui.connecting ? "Connecting…" : "Not connected";
  if (ui.connected) {
    if (speakingAgent) {
      orbState = "speaking";
      stageLabel = `${speakingAgent.display_name} · Speaking`;
    } else if (userTalking) {
      orbState = "listening";
      stageLabel = "Listening";
    } else if (teamThinking) {
      orbState = "idle";
      stageLabel = `${thinkingNames.join(" & ")} · Thinking`;
    } else if (listeningNames.length) {
      orbState = "idle";
      stageLabel = `${listeningNames[0]} · Listening for follow-up`;
    } else {
      orbState = "idle";
      const names = [...ui.teamAgents.values()].map((a) => a.display_name).join(" or ") || "Tari or Astra";
      stageLabel = ui.team?.router_mode === "names" ? `Ready — address ${names} by name`
        : `${floor.addressed ? teamAgent(floor.addressed).display_name + " · " : ""}Ready`;
    }
  }
  orb.className = `orb state-${orbState}${teamThinking ? " thinking" : ""}${anyWorking ? " working" : ""}${anyWaiting ? " waiting" : ""}`;
  $("agent-state").textContent = stageLabel;

  const queue = (floor.queue || []).map((q) => `${teamAgent(q.agent).display_name}'s ${q.kind === "notice" ? "update" : "reply"}`);
  const line = !ui.connected ? "" : queue.length ? `Waiting for the floor: ${queue.join(", ")}` : ui.teamStatus;
  $("team-queue").textContent = line;
  const workingTotal = [...ui.jobs.values()].filter((j) => ACTIVE.has(j.status)).length;
  $("jobs-count").textContent = workingTotal ? `${workingTotal} running` : "";
}

function onTeamReply(d) {
  const speaker = teamAgent(d.agent);
  let el = ui.replies.get(d.reply);
  if (d.phase === "thinking") {
    activity?.thinking(speaker.display_name);
    return;
  }
  if (d.phase === "speaking") {
    activity?.speaking(speaker.display_name);
    if (!el) {
      el = addTurn("bot", d.text || "…", { speaker });
      if (d.reply_kind === "notice") el.classList.add("task-update");
      ui.replies.set(d.reply, el);
    } else if (d.text) setTurnText(el, d.text);
  } else if (d.phase === "done" || d.phase === "cut") {
    activity?.settle();
    if (!el && d.text) {
      el = addTurn("bot", d.text, { speaker });
      if (d.reply_kind === "notice") el.classList.add("task-update");
      ui.replies.set(d.reply, el);
    } else if (el && d.text) setTurnText(el, d.text);
    if (el && d.phase === "cut") el.classList.add("interrupted");
  } else if (d.phase === "dropped") {
    activity?.settle();
    if (el) el.classList.add("dropped");
    const why = d.outcome === "handed_over" ? "you turned to another agent; its work continues" : d.outcome;
    logLine("events", `${speaker.display_name}'s ${d.reply_kind} not played (${why})`);
  }
}

function onTeamUser(d) {
  if (d.status === "unclear") { logLine("events", "didn't catch that"); return; }
  if (!d.text) return;
  const el = addTurn("user", d.text);
  const agent = d.agent ? teamAgent(d.agent) : null;
  const route = document.createElement("span");
  route.className = "route";
  if (d.status === "routed" || d.status === "listening") {
    ui.teamStatus = "";
    route.classList.add("to");
    route.style.setProperty("--route-color", agent.color);
    route.textContent = d.how === "overseer" && d.reason ? `Routed to ${agent.display_name} · ${d.reason}`
      : d.status === "listening" ? `→ ${agent.display_name} (listening…)`
      : `→ ${agent.display_name}${d.how === "continuation" ? " (continued)" : d.how === "follow-up" ? " (follow-up)" : ""}${d.typed ? " · typed" : ""}`;
    activity?.newTurn(`Heard “${clip(d.text)}” → ${agent.display_name}`, { append: true });
  } else if (d.status === "stop") {
    route.textContent = agent ? `stop ${agent.display_name}` : "stop (everyone)";
    activity?.newTurn(`Heard “${clip(d.text)}”`, { append: true });
  } else {
    el.classList.add("unaddressed");
    const names = [...ui.teamAgents.values()].map((a) => a.display_name).join(" or ");
    route.textContent = d.status === "routing_failed" ? `routing unavailable — repeat or address ${names} by name`
      : d.status === "superseded" ? "superseded by your next turn"
      : `not addressed — start with ${names}`;
  }
  el.querySelector(".who").append(route);
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
  $("mute").setAttribute("aria-label", micMuted ? "Unmute microphone" : "Mute microphone");
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
  if (isTeam()) {
    // Routed by name on the server, like speech; the routed turn comes back as clawvoice.team.user.
    if (sendRtvi("client-message", { t: "clawvoice.say", d: { text } })) $("message").value = "";
    return;
  }
  if (sendRtvi("send-text", { content: text, options: { audio_response: true, run_immediately: true } })) {
    addTurn("user", text);
    activity?.newTurn(`Read “${clip(text)}”`);
    $("message").value = "";
  }
});
$("keyboard")?.addEventListener("click", () => {
  if (ui.connected) { $("message").focus?.(); return; }
  $("typed-only").checked = !$("typed-only").checked;
  setButtons();
});

// Settings and details: model, gateway, voice options, fast context, diagnostics.
function setSettings(open) {
  $("settings").hidden = !open;
  $("settings-toggle")?.setAttribute("aria-expanded", String(open));
}
$("settings-toggle")?.addEventListener("click", (event) => {
  event.stopPropagation?.();
  setSettings($("settings").hidden);
});
document.addEventListener("click", (event) => {
  if (!$("settings").hidden && !event.target?.closest?.("#settings, #settings-toggle")) setSettings(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("settings").hidden) setSettings(false);
});
window.addEventListener("beforeunload", () => ui.connected && sendRtvi("disconnect-bot", {}));

init();
