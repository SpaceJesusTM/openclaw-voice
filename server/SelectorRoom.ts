import WebSocket from "ws";
import { AGENT_NAMES, CALL_AGENT, OVERSEER_PROMPT, PERSONAS, parseCall, type AgentName } from "../src/agent/selectorAgents";
import { LevelGate } from "../src/audio/LevelGate";
import { CHUNK_SAMPLES, concat, msOf, samplesOf } from "../src/audio/pcm";
import { MODEL, SERVER, functionCallOutput, responseCreate, textItem, type ServerEvent } from "../src/realtime/events";
import { costOf, sumUsage } from "../src/realtime/pricing";
import { RealtimeSession, type SessionLog } from "../src/realtime/RealtimeSession";
import { buildSessionConfig } from "../src/realtime/sessionConfig";
import { DEFAULT_LIVE_FEED } from "../src/room/LiveFeed";
import { SelectorVoice, type Reply } from "./SelectorVoice";

/**
 * Higgs 1.5 for the overseer, as for the agents. Higgs 1 decided faster (~0.5 s
 * after you stop) but misheard requests on raw audio ("Coder, quick one" ->
 * jester); routing stays on audio, so it gets the better listener.
 */
const OVERSEER_MODEL = MODEL;
/**
 * Pipecat's example settings, but 700 ms of silence instead of 500: at 500 a
 * pause between sentences ended the turn, and the overseer misjudged the
 * fragment ("Okay, that makes sense." -> planner). turn_detection can't change
 * after session start (probed 2026-10-24), so only the overseer has it.
 */
const VAD = { type: "server_vad" as const, threshold: 0.85, prefix_padding_ms: 300, silence_duration_ms: 700 };
/** Mic audio kept for slicing out each turn. */
const MIC_KEEP_SAMPLES = samplesOf(120_000);
/** Chunks sent ahead of real time when a reply starts, to absorb network jitter. */
const LEAD_CHUNKS = 2;
/**
 * Quiet this long inside your turn starts a held draft reply. Higgs takes
 * 0.55-0.8 s to first audio, so waiting for the VAD's 700 ms before asking put
 * every reply ~1.5 s after you stopped.
 */
const DRAFT_AFTER_MS = 200;

/**
 * The agent selector demo, one per browser tab, after Boson's basic Pipecat
 * example: speech-to-speech Higgs sessions fed by the browser mic.
 *
 *   overseer  text out, never speaks. Hears your mic with server VAD, so it
 *             decides on every turn you take; calls call_agent to switch.
 *   planner / coder / jester  audio out, manual turns. All three hear every
 *             turn (yours and each other's, as real audio); only the active
 *             one is asked to reply. Planner starts.
 *
 * Drafting early: at a 200 ms pause in your turn (a level gate on the mic),
 * the active agent hears your turn so far and starts a reply that is held,
 * not played. If you carry on talking it's cancelled unheard. When the VAD
 * says your turn is over, the draft plays at once if it heard all your
 * speech; otherwise it's dropped and the agent replies fresh. The VAD stays
 * the one authority on when your turn ends.
 *
 * Wire protocol over the WebSocket:
 *   tab -> server  binary: mic PCM16 24 kHz
 *                  JSON {type:"start"} | {type:"say", text} | {type:"pick", name} | {type:"diag", ...}
 *   server -> tab  binary: the active agent's audio, PCM16 24 kHz, paced at real time
 *                  JSON {type:"status"|"ready"|"active"|"speaking"|"hearing"|"user"|"said"|"overseer"|"clear"|"usage"|"error", ...}
 */
export class SelectorRoom {
  private overseer: RealtimeSession | null = null;
  private voices = new Map<AgentName, SelectorVoice>();
  private active: AgentName = "planner";
  /** The reply being played (or about to be): always the active agent's latest. */
  private current: Reply | null = null;
  private queued: Int16Array = new Int16Array(0);
  private mic: { at: number; pcm: Int16Array }[] = [];
  /** Samples of mic audio sent to the overseer: its VAD's timeline. */
  private micSamples = 0;
  /** Per agent: mic audio before this sample has already gone to it. */
  private heardTo = new Map<AgentName, number>();
  private turn: { startMs: number; endMs?: number } | null = null;
  /** Says which mic chunks are speech, for spotting pauses inside a turn. */
  private gate = new LevelGate(DEFAULT_LIVE_FEED);
  private quietMs = 0;
  /** When, and up to which mic sample, you were last heard speaking. */
  private lastSpeechAt = 0;
  private lastSpeechSample = 0;
  /** A held reply, started at a pause in your turn, not played yet. */
  private draft: { reply: Reply; agent: AgentName; at: number } | null = null;
  /** Per reply: when you stopped talking before it, to measure how fast it answered. */
  private timing = new WeakMap<Reply, { stoppedAt: number; drafted: boolean; latencyMs?: number }>();
  /** Every word of yours shown so far, normalized. */
  private heard: string[] = [];
  /** The overseer's decision on this turn has already been retried once. */
  private overseerRetried = false;
  private ticker: NodeJS.Timeout | null = null;
  private startedAt = 0;
  private started = false;
  private closed = false;

  constructor(
    private ws: WebSocket,
    private apiKey: string,
    private log: (line: string) => void = (line) => console.log(`[selector] ${line}`),
  ) {}

  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;
    const slog: SessionLog = (s, m) => this.log(`{${s}} ${m}`);

    // One at a time: the server's voice validation is rate limited.
    for (const p of PERSONAS) {
      this.send({ type: "status", text: `Starting ${p.name}…` });
      const session = await RealtimeSession.connect(
        p.name,
        this.apiKey,
        buildSessionConfig({ instructions: p.prompt, modality: "audio", voice: p.voice, model: MODEL }),
        slog,
        MODEL,
      );
      this.voices.set(
        p.name,
        new SelectorVoice(p, session, { audio: (r, pcm) => this.onAgentAudio(r, pcm), ended: (r) => this.onReplyEnded(r) }, (m) => this.log(m)),
      );
      this.watchClose(session);
      if (this.closed) return void (await this.close());
    }
    this.send({ type: "status", text: "Starting overseer…" });
    this.overseer = await RealtimeSession.connect(
      "overseer",
      this.apiKey,
      buildSessionConfig({ instructions: OVERSEER_PROMPT, modality: "text", model: OVERSEER_MODEL, turnDetection: VAD, transcribe: true, tools: [CALL_AGENT] }),
      slog,
      OVERSEER_MODEL,
    );
    this.watchClose(this.overseer);
    this.overseer.on((e) => this.onOverseer(e));
    if (this.closed) return void (await this.close());

    this.startedAt = Date.now();
    this.ticker = setInterval(() => this.tick(), 100);
    this.send({
      type: "ready",
      agents: PERSONAS.map((p) => ({ name: p.name, voice: p.voice, blurb: p.blurb })),
      model: MODEL,
      overseerModel: OVERSEER_MODEL,
      active: this.active,
    });
    this.overseer.send(textItem(`(Note: ${this.active} is talking with the user now.)`));
    this.reply(this.active, "(Note: the user just joined the call. Greet them in one or two sentences as Planner, and mention Coder and Jester are here too.)");
  }

  /** One chunk of the tab's mic: always to the overseer, kept so each turn can be sliced out for the agents. */
  onMic(data: Buffer): void {
    if (!this.overseer || this.closed || data.length < 2) return;
    const copy = new Uint8Array(data.length - (data.length % 2));
    copy.set(data.subarray(0, copy.length));
    const pcm = new Int16Array(copy.buffer);
    this.mic.push({ at: this.micSamples, pcm });
    this.micSamples += pcm.length;
    while (this.mic.length && this.mic[0].at + this.mic[0].pcm.length < this.micSamples - MIC_KEEP_SAMPLES) this.mic.shift();
    this.overseer.appendAudio(pcm);

    if (this.gate.loud(pcm)) {
      this.quietMs = 0;
      this.lastSpeechAt = Date.now();
      this.lastSpeechSample = this.micSamples;
      if (this.draft) this.dropDraft("you kept talking");
    } else {
      this.quietMs += msOf(pcm.length);
      if (this.turn && !this.draft && this.quietMs >= DRAFT_AFTER_MS && this.lastSpeechSample > samplesOf(this.turn.startMs)) this.startDraft();
    }
  }

  /** You paused: the active agent hears your turn so far and starts a reply, held until your turn is over. */
  private startDraft(): void {
    const agent = this.active;
    const v = this.voices.get(agent);
    if (!v || !this.turn) return;
    const to = this.micSamples;
    void v.hear("(Note: the next audio is the user speaking.)", this.micSlice(this.turnFrom(agent), to)).catch((err) => this.fail(err));
    this.heardTo.set(agent, to);
    const reply = v.respond();
    this.timing.set(reply, { stoppedAt: this.lastSpeechAt, drafted: true });
    this.draft = { reply, agent, at: Date.now() };
    this.log(`draft: ${agent} starts a held reply after ${this.quietMs} ms of quiet`);
  }

  private dropDraft(why: string): void {
    const d = this.draft;
    if (!d) return;
    this.draft = null;
    this.voices.get(d.agent)?.stop(d.reply);
    this.log(`draft dropped: ${why} (${d.reply.audio.length ? "had" : "no"} audio yet, ${Date.now() - d.at} ms old)`);
  }

  /** Where this turn's audio starts for an agent: what it hasn't heard yet, from just before you started. */
  private turnFrom(agent: AgentName): number {
    return Math.max(this.heardTo.get(agent) ?? 0, samplesOf((this.turn?.startMs ?? 0) - 200));
  }

  /** A typed line: a user turn without audio. */
  async say(text: string): Promise<void> {
    const line = text.trim().slice(0, 500);
    if (!line || !this.overseer || this.closed) return;
    this.log(`user typed: ${line}`);
    this.send({ type: "user", text: line, typed: true });
    this.dropDraft("you typed");
    this.cut("you typed");
    const note = `(The user typed: "${line}")`;
    for (const v of this.voices.values()) void v.note(note);
    this.overseer.send(textItem(note));
    this.overseerRetried = false;
    if (!this.overseer.generating) this.overseer.send(responseCreate());
    this.reply(this.active, undefined, { stoppedAt: Date.now(), drafted: false });
  }

  /** You clicked an agent's tile: switch without asking the overseer. */
  pick(name: string): void {
    const agent = AGENT_NAMES.find((n) => n === name);
    if (agent && this.overseer) this.switchTo(agent, "you picked them", "you");
  }

  diag(report: Record<string, unknown>): void {
    this.log(`diag page=${JSON.stringify(report)} server=${JSON.stringify({ active: this.active, playing: !!this.current, queuedMs: msOf(this.queued.length), turn: this.turn, draft: !!this.draft, gate: this.gate.last })}`);
  }

  private onOverseer(e: ServerEvent): void {
    const o = this.overseer!;
    if (e.type === SERVER.speechStarted) {
      this.turn = { startMs: Number(e.audio_start_ms ?? msOf(this.micSamples)) };
      this.send({ type: "hearing", on: true });
      // Barge-in: you talking over the agent stops it, as in Pipecat.
      this.dropDraft("new turn");
      this.cut("you spoke");
    } else if (e.type === SERVER.speechStopped) {
      if (this.turn) this.turn.endMs = Number(e.audio_end_ms ?? msOf(this.micSamples));
      this.send({ type: "hearing", on: false });
    } else if (e.type === SERVER.audioCommitted) {
      this.overseerRetried = false;
      this.onUserTurn();
    } else if (e.type === SERVER.responseDone) {
      // The 1.5 preview fails some responses outright ("upstream_error", status
      // "failed"), which is no decision at all. Ask again once, unless you've
      // started talking again (then the next turn brings a fresh decision).
      const status = (e.response as { status?: string } | undefined)?.status;
      if (status === "failed" && !this.overseerRetried && !this.turn) {
        this.overseerRetried = true;
        this.log("overseer response failed; asking again");
        o.send(responseCreate());
      }
    } else if (e.type === SERVER.transcriptionCompleted) {
      const text = newWords(this.heard, String(e.transcript ?? "").trim());
      this.heard.push(...words(text));
      if (text) {
        this.log(`user said: ${text}`);
        this.send({ type: "user", text });
      }
    } else if (e.type === SERVER.functionCallArgsDone) {
      const call = parseCall(String(e.arguments ?? ""));
      o.send(functionCallOutput(String(e.call_id ?? ""), JSON.stringify({ ok: !!call, talking_now: call?.agent ?? this.active })));
      this.log(`overseer call_agent ${String(e.arguments ?? "")}`);
      if (call) this.switchTo(call.agent, call.reason, "overseer");
    } else if (e.type === SERVER.outputTextDone) {
      const text = String(e.text ?? "").trim();
      if (text) this.send({ type: "overseer", kind: "text", text });
    }
  }

  /**
   * The overseer's VAD ended your turn: every agent hears it, and the active
   * one replies. A held draft plays now: it still exists only if the mic gate
   * heard no speech after it started (speech drops it).
   */
  private onUserTurn(): void {
    const turn = this.turn ?? { startMs: msOf(this.lastSpeechSample) };
    const endMs = turn.endMs ?? msOf(this.micSamples);
    const to = Math.min(this.micSamples, samplesOf(endMs));
    const d = this.draft;
    const keep = d !== null && d.agent === this.active;
    if (d && !keep) this.dropDraft("the active agent changed");
    this.log(`turn end: vad says ${Math.round(endMs)} ms, gate last heard speech at ${msOf(this.lastSpeechSample)} ms, ${Date.now() - this.lastSpeechAt} ms ago`);
    for (const v of this.voices.values()) {
      // The drafting agent already heard your speech; the rest is silence.
      if (!(keep && v.name === this.active)) {
        void v.hear("(Note: the next audio is the user speaking.)", this.micSlice(this.turnFrom(v.name), to)).catch((err) => this.fail(err));
      }
      this.heardTo.set(v.name, Math.max(this.heardTo.get(v.name) ?? 0, to));
    }
    this.turn = null;
    if (keep && d) {
      this.draft = null;
      if (this.current) this.cut("new reply");
      this.current = d.reply;
      this.queued = concat(d.reply.audio);
      this.log(`user turn over: ${this.active}'s draft plays (${msOf(this.queued.length)} ms of audio ready)`);
    } else {
      this.log(`user turn over: ${this.active} replies fresh`);
      this.reply(this.active, undefined, { stoppedAt: this.lastSpeechAt, drafted: false });
    }
  }

  private switchTo(agent: AgentName, reason: string, by: "overseer" | "you"): void {
    if (agent === this.active) {
      this.send({ type: "overseer", kind: "stay", agent, reason, by });
      return;
    }
    const from = this.active;
    this.log(`switch ${from} -> ${agent} (${by}: ${reason})`);
    this.dropDraft(`${agent} takes over`);
    this.cut(`${agent} takes over`);
    this.active = agent;
    this.send({ type: "active", name: agent, from, reason, by });
    if (by === "you") this.overseer?.send(textItem(`(Note: the user switched to ${agent}. ${agent} is talking with the user now.)`));
    this.reply(
      agent,
      // A short opener says who's taking over, so a swap sounds like someone stepping in, not a glitch.
      `(Note: you have the floor now${by === "you" ? ", the user picked you" : ""}; ${from} has gone quiet. ` +
        `You're stepping in, so open with a few words like "I can help with that" or "Ooh, that's my area", in your own style, ` +
        `then reply to the last thing the user said, as ${agent}. Don't mention any overseer.)`,
      by === "overseer" ? { stoppedAt: this.lastSpeechAt, drafted: false } : undefined,
    );
  }

  /** `answers`: when you stopped talking, if this reply answers you (for the latency log). */
  private reply(agent: AgentName, note?: string, answers?: { stoppedAt: number; drafted: boolean }): void {
    const v = this.voices.get(agent);
    if (!v) return;
    if (this.current) this.cut("new reply");
    this.queued = new Int16Array(0);
    this.current = v.respond(note);
    if (answers) this.timing.set(this.current, answers);
  }

  /** Only the current reply's audio is played. */
  private onAgentAudio(r: Reply, pcm: Int16Array): void {
    if (r === this.current) this.queued = concat([this.queued, pcm]);
  }

  private onReplyEnded(r: Reply): void {
    if (r.outcome !== "completed") this.log(`${r.agent} reply ${r.outcome} (${msOf(r.sent)} ms played)`);
  }

  /** Stop the active agent now. What you already heard is kept, and relayed to the others. */
  private cut(why: string): void {
    const r = this.current;
    if (!r) return;
    this.current = null;
    this.voices.get(r.agent)?.stop(r);
    this.queued = new Int16Array(0);
    this.log(`cut ${r.agent} at ${msOf(r.sent)} ms played (${why}; reply was ${r.state})`);
    if (r.sent > 0) this.send({ type: "clear" });
    this.finish(r, true);
  }

  /** 100 ms of the current reply per tick, a little ahead at the start. */
  private tick(): void {
    if (Math.round((Date.now() - this.startedAt) / 100) % 20 === 0) this.send({ type: "usage", ...this.usageReport() });
    const r = this.current;
    if (!r) return;
    const chunks = r.sent === 0 ? LEAD_CHUNKS : 1;
    for (let i = 0; i < chunks && this.queued.length > 0; i++) {
      if (r.sent === 0) {
        const t = this.timing.get(r);
        if (t) t.latencyMs = Date.now() - t.stoppedAt;
        this.log(
          `${r.agent} audible ${Date.now() - r.askedAt} ms after response.create` +
            (t ? `, ${t.latencyMs} ms after you stopped talking (${t.drafted ? "draft" : "fresh"})` : ""),
        );
        this.send({ type: "speaking", name: r.agent });
      }
      const chunk = this.queued.subarray(0, CHUNK_SAMPLES);
      this.queued = this.queued.subarray(chunk.length);
      r.sent += chunk.length;
      if (this.ws.readyState === WebSocket.OPEN) this.ws.send(Buffer.from(chunk.buffer, chunk.byteOffset, chunk.byteLength), { binary: true });
    }
    if (r.state === "done" && this.queued.length === 0) {
      this.current = null;
      this.finish(r, false);
    }
  }

  /** A reply ended, played out or cut: show it, let the other agents hear it, and tell the overseer. */
  private finish(r: Reply, cutOff: boolean): void {
    this.send({ type: "speaking", name: null });
    const playedMs = msOf(r.sent);
    const text = (cutOff ? heardText(r.fragments, playedMs) : r.transcript || r.fragments.map((f) => f.text).join("")).trim();
    if (r.sent === 0 && !text) return;
    this.log(`${r.agent} said${cutOff ? " (cut off)" : ""}: ${text}`);
    this.send({ type: "said", name: r.agent, text: text || "…", cut: cutOff, latencyMs: this.timing.get(r)?.latencyMs });
    const heard = concat(r.audio).subarray(0, r.sent);
    for (const v of this.voices.values()) {
      if (v.name !== r.agent) void v.hear(`(Note: the next audio is ${r.agent} speaking${cutOff ? ", cut off" : ""}.)`, heard).catch((err) => this.fail(err));
    }
    // Context only. The overseer decides on your turns alone: asked after agent
    // replies, it invented requests ("User asked for a joke") and bounced agents.
    this.overseer?.send(textItem(`(Note: ${r.agent} said: "${text}"${cutOff ? " (cut off)" : ""})`));
  }

  private micSlice(from: number, to: number): Int16Array {
    const parts: Int16Array[] = [];
    for (const c of this.mic) {
      const a = Math.max(from, c.at);
      const b = Math.min(to, c.at + c.pcm.length);
      if (b > a) parts.push(c.pcm.subarray(a - c.at, b - c.at));
    }
    return concat(parts);
  }

  usageReport() {
    const sessions = [...[...this.voices.values()].map((v) => v.session), ...(this.overseer ? [this.overseer] : [])];
    const agents = sumUsage(...[...this.voices.values()].map((v) => v.session.usage));
    const overseer = this.overseer?.usage ?? sumUsage();
    return {
      agentsCost: costOf(agents),
      overseerCost: costOf(overseer),
      total: costOf(sumUsage(...sessions.map((s) => s.usage))),
      minutes: this.startedAt ? (Date.now() - this.startedAt) / 60_000 : 0,
    };
  }

  private watchClose(s: RealtimeSession): void {
    s.on((e) => {
      if (e.type === "__closed" && !this.closed) this.send({ type: "error", text: `${s.name} disconnected: ${s.closed?.message ?? "closed"}` });
    });
  }

  private fail(e: unknown): void {
    const text = e instanceof Error ? e.message : String(e);
    this.log(`error: ${text}`);
    this.send({ type: "error", text });
  }

  async close(): Promise<void> {
    if (this.started && !this.closed) {
      const r = this.usageReport();
      this.log(`usage: agents $${r.agentsCost.toFixed(4)} + overseer $${r.overseerCost.toFixed(4)} = $${r.total.toFixed(4)} over ${r.minutes.toFixed(1)} min`);
    }
    this.closed = true;
    if (this.ticker) clearInterval(this.ticker);
    const sessions = [...[...this.voices.values()].map((v) => v.session), ...(this.overseer ? [this.overseer] : [])];
    this.voices.clear();
    this.overseer = null;
    await Promise.all(sessions.map((s) => s.close()));
  }

  send(msg: Record<string, unknown>): void {
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }
}

const words = (s: string) => s.toLowerCase().match(/[a-z0-9']+/g) ?? [];

/**
 * The part of a transcript not shown yet. The overseer's transcripts sometimes
 * replay the conversation from its start (seen 2026-10-24), with earlier
 * pieces re-split. So when one opens like your first words, skip its leading
 * words that follow, in order, what was already shown.
 */
function newWords(shown: string[], transcript: string): string {
  const t = words(transcript);
  const replay = shown.length >= 2 && t.length > 2 && t[0] === shown[0] && t[1] === shown[1];
  if (!replay) return transcript;
  let p = 0;
  let j = 0;
  for (; j < t.length; j++) {
    while (p < shown.length && shown[p] !== t[j]) p++;
    if (p === shown.length) break;
    p++;
  }
  // Map the j skipped words back onto the original text, punctuation and all.
  const raw = transcript.split(/\s+/);
  let seen = 0;
  let k = 0;
  while (k < raw.length && seen < j) seen += words(raw[k++]).length;
  return raw.slice(k).join(" ");
}

/**
 * The words of a reply heard before it was cut at `playedMs`. Fragments are
 * whole clauses (`endMs` is where each ends in the audio, probed 2026-10-24),
 * often 4 s or more, so the one playing at the cut is included in proportion
 * to how much of it was heard.
 */
function heardText(fragments: { text: string; endMs: number }[], playedMs: number): string {
  let out = "";
  let prevEnd = 0;
  for (const f of fragments) {
    if (f.endMs <= playedMs) out += f.text;
    else {
      const share = (playedMs - prevEnd) / Math.max(1, f.endMs - prevEnd);
      const ws = f.text.trim().split(/\s+/);
      const n = Math.floor(ws.length * share);
      if (n > 0) out += `${f.text.startsWith(" ") ? " " : ""}${ws.slice(0, n).join(" ")}…`;
      break;
    }
    prevEnd = f.endMs;
  }
  return out;
}
