import WebSocket from "ws";
import { Agent, type AgentConfig } from "../src/agent/Agent";
import { AgentLoop, DEFAULT_LOOP_TIMING } from "../src/agent/AgentLoop";
import { synthesize } from "../src/audio/tts";
import { AudioBus, type BusListener, type Tick } from "../src/room/AudioBus";
import { Journal } from "../src/room/Journal";
import { DEFAULT_LIVE_FEED, LiveFeed } from "../src/room/LiveFeed";
import { Utterance } from "../src/room/Utterance";

export const HUMAN = "the human";
/** Typed lines are spoken in the built-in voice, which agents can't pick, so you never sound like one of them. */
const HUMAN_VOICE = "default";
const VOICES = ["chloe", "eleanor", "jake", "marcus", "nora", "oliver"];
const MAX_AGENTS = 4;

const V1 = "higgs-realtime";
const V15 = "higgs-realtime-1.5-preview";
/** What the page's model picker means. "hybrid": 1.5 for what you hear, 1 for fast, reliable decisions. */
export const MODEL_CHOICES: Record<string, { voiceModel: string; mindModel: string; label: string }> = {
  "1.5": { voiceModel: V15, mindModel: V15, label: "Higgs 1.5" },
  "1": { voiceModel: V1, mindModel: V1, label: "Higgs 1" },
  hybrid: { voiceModel: V15, mindModel: V1, label: "1.5 voice · 1 mind" },
};

export interface AgentRequest extends Partial<AgentConfig> {
  /** A key of MODEL_CHOICES. */
  model?: string;
}

export interface RoomRequest {
  /** The human's name, so agents can address them. */
  you?: string;
  /** The shared situation every agent is told about. */
  scene?: string;
  agents: AgentRequest[];
  maxAgentTurns?: number;
}

/**
 * One browser tab's room. The tab is the human: it joins the bus like anyone
 * else, its mic is a LiveFeed source, and each tick it is sent the room mix
 * minus its own voice. This class makes no conversational decisions.
 *
 * Wire protocol over the WebSocket:
 *   tab -> server  binary: mic PCM16 24 kHz, 100 ms chunks
 *                  JSON {type:"start", you, scene, agents, maxAgentTurns} | {type:"say", text}
 *   server -> tab  binary: room audio PCM16 24 kHz, 100 ms per tick (when anyone else is audible)
 *                  JSON {type:"status"|"ready"|"journal"|"active"|"error", ...}
 */
export class BrowserRoom implements BusListener {
  /** The human on the bus: their name if they gave one, so agents hear "[Jefferson:]". */
  name = HUMAN;
  private bus = new AudioBus();
  private agents: Agent[] = [];
  private feed: LiveFeed | null = null;
  private journal: Journal | null = null;
  private lastActive = "";
  private started = false;
  private closed = false;

  constructor(
    private ws: WebSocket,
    private apiKey: string,
    private log: (line: string) => void = (line) => console.log(`[room] ${line}`),
  ) {}

  /** The page's periodic audio report, logged with the mic gate's view of the same moment. */
  diag(report: Record<string, unknown>): void {
    const gate = this.feed ? { gateSpeaking: this.feed.isSpeaking, ...this.feed.last } : {};
    this.log(`diag page=${JSON.stringify(report)} server=${JSON.stringify({ active: this.lastActive, ...gate })}`);
  }

  async start(req: RoomRequest): Promise<void> {
    if (this.started) return;
    const configs = sanitize(req.agents); // throws on a bad request, before anything starts
    const scene = req.scene ? String(req.scene).trim().slice(0, 600) || undefined : undefined;
    const maxAgentTurns = clamp(Number(req.maxAgentTurns ?? 4), 1, 20);
    const you = text(req.you, 24);
    if (you && !configs.some((c) => c.name.toLowerCase() === you.toLowerCase())) this.name = you;
    const human = this.name;
    this.started = true;
    const log = this.log;
    const journal = (this.journal = new Journal(() => this.bus.elapsedMs(), log, (entry) => this.send({ type: "journal", entry })));

    // One at a time: the server's voice validation is rate limited.
    for (const c of configs) {
      this.send({ type: "status", text: `Starting ${c.name}…` });
      const others = configs.filter((o) => o !== c).map((o) => o.name);
      const agent = await Agent.create(c, { others, scene, human: human === HUMAN ? undefined : human }, this.apiKey, (s, m) => log(`{${s}} ${m}`));
      this.agents.push(agent);
      if (this.closed) return void (await this.close());
    }

    for (const agent of this.agents) {
      this.bus.join(new AgentLoop(agent, this.bus, { ...DEFAULT_LOOP_TIMING, maxAgentTurns, humanName: human, journal }));
    }
    this.bus.join(this);
    this.feed = new LiveFeed(DEFAULT_LIVE_FEED, (ms) => journal.add(human, "said", `(spoke for ${(ms / 1000).toFixed(1)} s)`, this.bus.elapsedMs() - ms));
    this.bus.play(human, this.feed);
    this.bus.start();
    this.send({
      type: "ready",
      agents: configs.map((c) => ({ name: c.name, voice: c.voice, voiceModel: c.voiceModel, mindModel: c.mindModel, mind: c.mind })),
      human,
    });
  }

  onTick(t: Tick): void {
    if (t.active.some((n) => n !== this.name) && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(Buffer.from(t.others.buffer, t.others.byteOffset, t.others.byteLength), { binary: true });
    }
    const key = t.active.join("|");
    if (key !== this.lastActive) {
      this.lastActive = key;
      this.send({ type: "active", names: t.active });
    }
  }

  /** One chunk of the tab's mic. */
  mic(data: Buffer): void {
    if (!this.feed || data.length < 2) return;
    const copy = new Uint8Array(data.length - (data.length % 2));
    copy.set(data.subarray(0, copy.length));
    this.feed.push(new Int16Array(copy.buffer));
  }

  /** A typed line, spoken into the room in the human's voice. */
  async say(text: string): Promise<void> {
    const line = text.trim().slice(0, 500);
    if (!line || !this.journal || this.closed) return;
    const samples = await synthesize(this.apiKey, line, HUMAN_VOICE);
    this.journal.add(this.name, "said", line);
    this.bus.play(this.name, Utterance.fromSamples(this.name, samples, line));
  }

  async close(): Promise<void> {
    if (this.closed && this.agents.length === 0) return;
    this.closed = true;
    await this.bus.stop();
    const agents = this.agents.splice(0);
    await Promise.all(agents.map((a) => a.close()));
  }

  send(msg: Record<string, unknown>): void {
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }
}

function clamp(n: number, lo: number, hi: number): number {
  return Number.isFinite(n) ? Math.max(lo, Math.min(hi, Math.round(n))) : lo;
}

const text = (v: unknown, max: number) => (v ? String(v).trim().slice(0, max) || undefined : undefined);

/** Two to four agents with distinct names, preset voices and a known model choice; text fields capped. */
function sanitize(input: AgentRequest[] | undefined): AgentConfig[] {
  const out: AgentConfig[] = [];
  for (const a of (input ?? []).slice(0, MAX_AGENTS)) {
    const name = String(a.name ?? "").trim().slice(0, 24);
    if (!name || out.some((o) => o.name.toLowerCase() === name.toLowerCase()) || name.toLowerCase() === HUMAN) continue;
    const models = MODEL_CHOICES[String(a.model)] ?? MODEL_CHOICES.hybrid;
    out.push({
      name,
      role: text(a.role, 400) ?? "a participant.",
      goal: text(a.goal, 400),
      facts: text(a.facts, 400),
      voice: VOICES.includes(String(a.voice)) ? String(a.voice) : VOICES[out.length % VOICES.length],
      voiceModel: models.voiceModel,
      mindModel: models.mindModel,
      // Off unless asked for: the voice decides alone (one session per agent).
      mind: a.mind === true,
    });
  }
  if (out.length < 2) throw new Error("A room needs at least two agents with different names.");
  return out;
}
