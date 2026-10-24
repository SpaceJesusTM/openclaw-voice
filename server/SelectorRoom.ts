import WebSocket from "ws";
import { AGENT_NAMES, CALL_AGENT, OVERSEER_PROMPT, PERSONAS, parseCall, type AgentName } from "../src/agent/selectorAgents";
import { CHUNK_SAMPLES, concat, msOf, samplesOf } from "../src/audio/pcm";
import { MODEL, SERVER, functionCallOutput, responseCreate, textItem, type ServerEvent } from "../src/realtime/events";
import { costOf, sumUsage } from "../src/realtime/pricing";
import { RealtimeSession, type SessionLog } from "../src/realtime/RealtimeSession";
import { buildSessionConfig } from "../src/realtime/sessionConfig";
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
 * The agent selector demo, one per browser tab, after Boson's basic Pipecat
 * example: speech-to-speech Higgs sessions fed by the browser mic.
 *
 *   overseer  text out, never speaks. Hears your mic with server VAD, so it
 *             decides on every turn you take; calls call_agent to switch.
 *   planner / coder / jester  audio out, manual turns. All three hear every
 *             turn (yours and each other's, as real audio); only the active
 *             one is asked to reply. Planner starts.
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
  /** Mic audio before this sample has already gone to the agents. */
  private micSentTo = 0;
  private turn: { startMs: number; endMs?: number } | null = null;
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
  }

  /** A typed line: a user turn without audio. */
  async say(text: string): Promise<void> {
    const line = text.trim().slice(0, 500);
    if (!line || !this.overseer || this.closed) return;
    this.log(`user typed: ${line}`);
    this.send({ type: "user", text: line, typed: true });
    this.cut("you typed");
    const note = `(The user typed: "${line}")`;
    for (const v of this.voices.values()) void v.note(note);
    this.overseer.send(textItem(note));
    this.overseerRetried = false;
    if (!this.overseer.generating) this.overseer.send(responseCreate());
    this.reply(this.active);
  }

  /** You clicked an agent's tile: switch without asking the overseer. */
  pick(name: string): void {
    const agent = AGENT_NAMES.find((n) => n === name);
    if (agent && this.overseer) this.switchTo(agent, "you picked them", "you");
  }

  diag(report: Record<string, unknown>): void {
    this.log(`diag page=${JSON.stringify(report)} server=${JSON.stringify({ active: this.active, playing: !!this.current, queuedMs: msOf(this.queued.length), turn: this.turn })}`);
  }

  private onOverseer(e: ServerEvent): void {
    const o = this.overseer!;
    if (e.type === SERVER.speechStarted) {
      this.turn = { startMs: Number(e.audio_start_ms ?? msOf(this.micSamples)) };
      this.send({ type: "hearing", on: true });
      // Barge-in: you talking over the agent stops it, as in Pipecat.
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

  /** The overseer's VAD ended your turn: every agent hears it, the active one replies. */
  private onUserTurn(): void {
    const turn = this.turn ?? { startMs: msOf(this.micSentTo) };
    this.turn = null;
    const from = Math.max(this.micSentTo, samplesOf(turn.startMs - 200));
    const to = Math.min(this.micSamples, samplesOf(turn.endMs ?? msOf(this.micSamples)));
    const samples = this.micSlice(from, to);
    this.micSentTo = Math.max(this.micSentTo, to);
    this.log(`user turn ${msOf(samples.length)} ms -> agents; ${this.active} replies`);
    for (const v of this.voices.values()) void v.hear("(Note: the next audio is the user speaking.)", samples).catch((err) => this.fail(err));
    this.reply(this.active);
  }

  private switchTo(agent: AgentName, reason: string, by: "overseer" | "you"): void {
    if (agent === this.active) {
      this.send({ type: "overseer", kind: "stay", agent, reason, by });
      return;
    }
    const from = this.active;
    this.log(`switch ${from} -> ${agent} (${by}: ${reason})`);
    this.cut(`${agent} takes over`);
    this.active = agent;
    this.send({ type: "active", name: agent, from, reason, by });
    if (by === "you") this.overseer?.send(textItem(`(Note: the user switched to ${agent}. ${agent} is talking with the user now.)`));
    this.reply(
      agent,
      `(Note: you have the floor now${by === "you" ? ", the user picked you" : ""}; ${from} has gone quiet. ` +
        `Reply to the last thing said in the conversation, as ${agent}. Don't mention any overseer.)`,
    );
  }

  private reply(agent: AgentName, note?: string): void {
    const v = this.voices.get(agent);
    if (!v) return;
    if (this.current) this.cut("new reply");
    this.queued = new Int16Array(0);
    this.current = v.respond(note);
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
        this.log(`${r.agent} audible ${Date.now() - r.askedAt} ms after response.create`);
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
    const text = (cutOff ? r.fragments.filter((f) => f.endMs <= playedMs).map((f) => f.text).join("") : r.transcript || r.fragments.map((f) => f.text).join("")).trim();
    if (r.sent === 0 && !text) return;
    this.log(`${r.agent} said${cutOff ? " (cut off)" : ""}: ${text}`);
    this.send({ type: "said", name: r.agent, text: text || "…", cut: cutOff });
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
