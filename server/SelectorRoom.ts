import WebSocket from "ws";
import { AGENT_NAMES, AGENT_TOOLS, OVERSEER_PROMPT, OVERSEER_TOOLS, PERSONAS, parseAnswer, parseCall, parseStartTask, type AgentName } from "../src/agent/selectorAgents";
import { DEFAULT_LEVEL_GATE, LevelGate } from "../src/audio/LevelGate";
import { CHUNK_SAMPLES, concat, msOf, samplesOf } from "../src/audio/pcm";
import { MODEL, SERVER, functionCallOutput, responseCreate, textItem, type ServerEvent } from "../src/realtime/events";
import { costOf, sumUsage } from "../src/realtime/pricing";
import { RealtimeSession, type SessionLog } from "../src/realtime/RealtimeSession";
import { buildSessionConfig } from "../src/realtime/sessionConfig";
import { SelectorVoice, type Reply } from "./SelectorVoice";
import { TaskBoard, type Task, type TaskAlert } from "./TaskBoard";

/**
 * Higgs 1.5 for the overseer, as for the agents. Higgs 1 decided faster (~0.5 s
 * after you stop) but misheard requests on raw audio (one naming an agent
 * went to another); routing stays on audio, so it gets the better listener.
 */
const OVERSEER_MODEL = MODEL;
/**
 * Pipecat's example settings, but 700 ms of silence instead of 500: at 500 a
 * pause between sentences ended the turn, and the overseer misjudged the
 * fragment ("Okay, that makes sense." went to another agent). turn_detection can't change
 * after session start (probed 2026-10-24), so only the overseer has it.
 */
const VAD = { type: "server_vad" as const, threshold: 0.85, prefix_padding_ms: 300, silence_duration_ms: 700 };
/** Mic audio kept for slicing out each turn. */
const MIC_KEEP_SAMPLES = samplesOf(120_000);
/** Chunks sent ahead of real time when a reply starts, to absorb network jitter. */
const LEAD_CHUNKS = 2;
/**
 * On a swap the old agent wraps up: it plays on to the end of its current
 * clause if that's this close, else this long, then says a handoff line.
 * Roughly the time its handoff line takes to generate.
 */
const WRAP_UP_MAX_CLAUSE_MS = 1500;
const WRAP_UP_DEFAULT_MS = 900;
/**
 * Quiet this long inside your turn starts a held draft reply. Higgs takes
 * 0.55-0.8 s to first audio, so waiting for the VAD's 700 ms before asking put
 * every reply ~1.5 s after you stopped.
 */
const DRAFT_AFTER_MS = 200;
/** A natural gap: nobody has spoken for this long. Pending alerts are announced then. */
const ALERT_GAP_MS = 1200;

/**
 * The agent selector demo, one per browser tab, after Boson's basic Pipecat
 * example: speech-to-speech Higgs sessions fed by the browser mic.
 *
 *   overseer  text out, never speaks. Hears your mic with server VAD, so it
 *             decides on every turn you take; calls call_agent to switch.
 *   agents    one per persona in PERSONAS; audio out, manual turns. All of
 *             them hear every turn (yours and each other's, as real audio);
 *             only the active one is asked to reply. The first persona starts.
 *
 * Drafting early: at a 200 ms pause in your turn (a level gate on the mic),
 * the active agent hears your turn so far and starts a reply that is held,
 * not played. If you carry on talking it's cancelled unheard. When the VAD
 * says your turn is over, the draft plays at once if it heard all your
 * speech; otherwise it's dropped and the agent replies fresh. The VAD stays
 * the one authority on when your turn ends.
 *
 * Background tasks: any agent can start one (start_task) and pass on your
 * answer to any task's approval request (answer_approval). When a task
 * finishes or needs approval, the alert goes on one room-level queue, and
 * whoever you're talking to at the next natural gap tells you about it.
 *
 * Wire protocol over the WebSocket:
 *   tab -> server  binary: mic PCM16 24 kHz
 *                  JSON {type:"start"} | {type:"say", text} | {type:"pick", name} | {type:"approve", id, approve} | {type:"diag", ...}
 *   server -> tab  binary: the active agent's audio, PCM16 24 kHz, paced at real time
 *                  JSON {type:"status"|"ready"|"active"|"speaking"|"hearing"|"user"|"said"|"overseer"|"tasks"|"announce"|"clear"|"usage"|"error", ...}
 */
export class SelectorRoom {
  private overseer: RealtimeSession | null = null;
  private voices = new Map<AgentName, SelectorVoice>();
  private active: AgentName = PERSONAS[0].name;
  /** The reply being played (or about to be). */
  private current: Reply | null = null;
  /**
   * Replies that play back to back after `current`, with no gap if their audio
   * is ready: on a swap, the old agent's handoff line, then the new agent.
   * They generate while the one before them plays.
   */
  private lineup: Reply[] = [];
  /** Where a wrapping-up reply stops playing (samples). */
  private stopAt = new WeakMap<Reply, number>();
  /** Whether the last tick sent audio, so a reply that follows on gets no extra lead. */
  private sentLastTick = false;
  private queued: Int16Array = new Int16Array(0);
  private mic: { at: number; pcm: Int16Array }[] = [];
  /** Samples of mic audio sent to the overseer: its VAD's timeline. */
  private micSamples = 0;
  /** Per agent: mic audio before this sample has already gone to it. */
  private heardTo = new Map<AgentName, number>();
  private turn: { startMs: number; endMs?: number } | null = null;
  /** Says which mic chunks are speech, for spotting pauses inside a turn. */
  private gate = new LevelGate(DEFAULT_LEVEL_GATE);
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
  /**
   * The answer to an approval request belongs to the agent who asked it. Owners
   * of approval requests just heard (`asked`), and of the ones your current turn
   * is answering (`answering`): the overseer can't swap to them for that turn.
   * Its tool description said so, and it still swapped to the task's owner on "Yes, approve it".
   */
  private approvals = { asked: new Set<AgentName>(), answering: new Set<AgentName>(), replied: false };
  private board = new TaskBoard(
    (a) => this.onTaskAlert(a),
    (tasks) => this.send({ type: "tasks", tasks }),
  );
  /** The common alert queue: task news waiting for a natural gap. */
  private alerts: TaskAlert[] = [];
  /** The reply announcing alerts right now, and which ones. */
  private announcing: { reply: Reply; alerts: TaskAlert[] } | null = null;
  /** When agent audio last went out, for spotting a natural gap. */
  private lastPlayedAt = 0;
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
        buildSessionConfig({ instructions: p.prompt, modality: "audio", voice: p.voice, model: MODEL, tools: AGENT_TOOLS }),
        slog,
        MODEL,
      );
      this.voices.set(
        p.name,
        new SelectorVoice(
          p,
          session,
          { audio: (r, pcm) => this.onAgentAudio(r, pcm), ended: (r) => this.onReplyEnded(r), toolCall: (r) => this.onToolCall(r) },
          (m) => this.log(m),
        ),
      );
      this.watchClose(session);
      if (this.closed) return void (await this.close());
    }
    this.send({ type: "status", text: "Starting overseer…" });
    this.overseer = await RealtimeSession.connect(
      "overseer",
      this.apiKey,
      buildSessionConfig({ instructions: OVERSEER_PROMPT, modality: "text", model: OVERSEER_MODEL, turnDetection: VAD, transcribe: true, tools: OVERSEER_TOOLS }),
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
    const others = PERSONAS.filter((p) => p.name !== this.active).map((p) => cap(p.name));
    this.reply(
      this.active,
      `(Note: the user just joined the call. Greet them in one or two sentences as ${cap(this.active)}, and mention ${others.join(" and ")} ${others.length === 1 ? "is" : "are"} here too.)`,
    );
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
    this.newUserTurn();
    if (!this.overseer.generating) this.overseer.send(responseCreate());
    this.reply(this.active, undefined, { stoppedAt: Date.now(), drafted: false });
  }

  /** You clicked an agent's tile: switch without asking the overseer. */
  pick(name: string): void {
    const agent = AGENT_NAMES.find((n) => n === name);
    if (agent && this.overseer) this.switchTo(agent, "you picked them", "you");
  }

  diag(report: Record<string, unknown>): void {
    this.log(`diag page=${JSON.stringify(report)} server=${JSON.stringify({ active: this.active, playing: !!this.current, queuedMs: msOf(this.queued.length), turn: this.turn, draft: !!this.draft, alerts: this.alerts.length, gate: this.gate.last })}`);
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
      this.newUserTurn();
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
    } else if (e.type === SERVER.functionCallArgsDone && e.name === "answer_approval") {
      this.log(`overseer answer_approval ${String(e.arguments ?? "")}`);
      const ok = this.answerFromOverseer(String(e.arguments ?? ""));
      o.send(functionCallOutput(String(e.call_id ?? ""), JSON.stringify({ ok })));
    } else if (e.type === SERVER.functionCallArgsDone) {
      const call = parseCall(String(e.arguments ?? ""));
      o.send(functionCallOutput(String(e.call_id ?? ""), JSON.stringify({ ok: !!call, talking_now: call?.agent ?? this.active })));
      this.log(`overseer call_agent ${String(e.arguments ?? "")}`);
      if (call && this.approvals.answering.has(call.agent)) {
        this.log(`overseer's switch to ${call.agent} ignored: you're answering an approval request ${this.active} asked`);
        this.send({ type: "overseer", kind: "stay", agent: this.active, reason: `you were answering ${this.active}'s question`, by: "overseer" });
      } else if (call) this.switchTo(call.agent, call.reason, "overseer");
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
      this.play(d.reply);
      this.log(`user turn over: ${this.active}'s draft plays (${msOf(this.queued.length)} ms of audio ready)`);
    } else {
      this.log(`user turn over: ${this.active} replies fresh`);
      this.reply(this.active, undefined, { stoppedAt: this.lastSpeechAt, drafted: false });
    }
  }

  /**
   * Hand the conversation to another agent, gracefully. If the old agent is
   * being heard, it plays on to a near clause end, then says a short handoff
   * line; the new agent's reply generates meanwhile and plays right after.
   * If the old agent hasn't been heard yet, its reply is dropped unheard.
   */
  private switchTo(agent: AgentName, reason: string, by: "overseer" | "you"): void {
    if (agent === this.active) {
      this.send({ type: "overseer", kind: "stay", agent, reason, by });
      return;
    }
    const from = this.active;
    const old = this.voices.get(from);
    const next = this.voices.get(agent);
    if (!old || !next) return;
    this.log(`switch ${from} -> ${agent} (${by}: ${reason})`);
    this.dropDraft(`${agent} takes over`);
    for (const q of this.lineup.splice(0)) this.voices.get(q.agent)?.stop(q); // an earlier handoff, superseded
    this.active = agent;
    this.send({ type: "active", name: agent, from, reason, by });
    if (by === "you") this.overseer?.send(textItem(`(Note: the user switched to ${agent}. ${agent} is talking with the user now.)`));

    const r = this.current;
    if (r && r.agent === from && r.sent > 0) {
      const end = this.wrapUpPoint(r);
      this.stopAt.set(r, end);
      old.stop(r, end); // its memory ends where you'll stop hearing it
      this.queued = concat(r.audio).subarray(r.sent, end);
      const name = agent[0].toUpperCase() + agent.slice(1);
      this.lineup.push(
        old.respond(
          `(Note: what the user wants is ${name}'s area, and ${name} is taking over. Wrap up now in one short sentence, ` +
            `like "Actually, ${name}'s better with this one", then stop. Don't start answering.)`,
        ),
      );
      this.log(`${from} wraps up: plays ${msOf(end - r.sent)} ms more, then a handoff line, then ${agent}`);
    } else if (r) {
      this.cut(`${agent} takes over`);
    }
    const reply = next.respond(
      // A short opener says who's taking over, so a swap sounds like someone stepping in, not a glitch.
      `(Note: you have the floor now${by === "you" ? ", the user picked you" : ""}; ${from} is handing over to you. ` +
        `You're stepping in, so open with a few words like "I can help with that" or "Ooh, that's my area", in your own style, ` +
        `then reply to the last thing the user said, as ${agent}. Don't mention any overseer.)`,
    );
    if (by === "overseer") this.timing.set(reply, { stoppedAt: this.lastSpeechAt, drafted: false });
    if (this.current) this.lineup.push(reply);
    else this.play(reply);
  }

  /** Where the old agent stops on a swap: its current clause's end if that's near, else a little further on. */
  private wrapUpPoint(r: Reply): number {
    const playedMs = msOf(r.sent);
    const total = r.audio.reduce((n, c) => n + c.length, 0);
    const clauseEnd = r.fragments.find((f) => f.endMs > playedMs)?.endMs;
    const endMs = clauseEnd !== undefined && clauseEnd - playedMs <= WRAP_UP_MAX_CLAUSE_MS ? clauseEnd : playedMs + WRAP_UP_DEFAULT_MS;
    const end = samplesOf(endMs);
    return r.state === "done" ? Math.min(end, total) : end;
  }

  /** Make `r` the reply being played, with whatever audio it already has. Its tool calls take effect now. */
  private play(r: Reply): void {
    this.current = r;
    this.queued = concat(r.audio);
    this.handleCalls(r);
  }

  /** You clicked Approve or Deny on the Tasks panel. */
  approve(id: number, approve: boolean): void {
    this.answerTask(id, approve, "with a button");
  }

  /**
   * A reply's tool calls act only once it's played: a held draft that's
   * dropped (you kept talking) must not start a task you never heard agreed to.
   */
  private onToolCall(r: Reply): void {
    if (r === this.current) this.handleCalls(r);
  }

  private handleCalls(r: Reply): void {
    for (const c of r.calls) {
      if (c.handled) continue;
      c.handled = true;
      this.log(`${r.agent} called ${c.name} ${c.args}`);
      if (c.name === "start_task") {
        const { title, owner } = parseStartTask(c.args, r.agent);
        const { task, created } = this.board.start(owner, title);
        if (created) c.did = owner === r.agent ? `you started working on "${task.title}" in the background` : `you got ${cap(owner)} started on "${task.title}" in the background`;
        if (created) this.notifyAll(`(Note: background task #${task.id} "${task.title}" started; ${owner} is doing it.)`);
        else this.log(`task #${task.id} "${task.title}" is already under way; not starting it twice`);
      }
    }
  }

  /** The overseer heard you answer an approval request. */
  private answerFromOverseer(args: string): boolean {
    const a = parseAnswer(args);
    const pending = this.board.pending();
    // No usable number but only one task waiting: that's the one.
    const id = a && this.board.get(a.taskId)?.status === "needs_approval" ? a.taskId : pending.length === 1 ? pending[0].id : null;
    if (!a || id === null) {
      this.log(`answer_approval ignored: no matching task waiting (${args})`);
      return false;
    }
    return this.answerTask(id, a.approve, `to ${this.active}`);
  }

  /** Answer a task's approval request; false if it wasn't waiting for one. */
  private answerTask(id: number, approve: boolean, how: string): boolean {
    const task = this.board.get(id);
    if (!task || !this.board.answer(id, approve)) {
      this.log(`task #${id}: nothing to answer`);
      return false;
    }
    this.alerts = this.alerts.filter((a) => !(a.task.id === id && a.kind === "approval"));
    this.log(`task #${id} ${approve ? "approved" : "denied"} ${how}`);
    this.notifyAll(`(Note: the user ${approve ? "approved" : "said no to"} background task #${id} "${task.title}" (${how}).)`);
    return true;
  }

  /**
   * A user turn ended. If an approval was just asked, this turn starts the answer.
   * The answer may span several turns (a pause splits a sentence), so it lasts
   * until your first turn after the asking agent has replied to it: that reply
   * can end before the overseer's decision on your answer arrives.
   */
  private newUserTurn(): void {
    if (this.approvals.asked.size) {
      this.approvals.answering = this.approvals.asked;
      this.approvals.asked = new Set();
      this.approvals.replied = false;
    } else if (this.approvals.replied) {
      this.approvals.answering = new Set();
      this.approvals.replied = false;
    }
  }

  /** Context for every agent and the overseer, in order with what they've heard. */
  private notifyAll(note: string): void {
    for (const v of this.voices.values()) void v.note(note);
    this.overseer?.send(textItem(note));
  }

  /** A task needs attention. It waits on the queue for the next natural gap; the other agents and the overseer get a note now. */
  private onTaskAlert(a: TaskAlert): void {
    this.log(`task #${a.task.id} ${a.kind}: ${a.task.title} (${a.task.owner}'s)`);
    if (a.kind === "denied") return; // you said no yourself; nothing to tell you
    this.alerts.push(a);
    const note = `(Note: background task #${a.task.id} "${a.task.title}" (${a.task.owner}'s) ${a.kind === "done" ? "is done" : `needs the user's approval: ${a.task.question}`})`;
    for (const v of this.voices.values()) if (v.name !== this.active) void v.note(note);
    this.overseer?.send(textItem(note));
  }

  /**
   * At a natural gap (nobody speaking, nothing playing, lined up or drafted,
   * for ALERT_GAP_MS), whoever you're talking to announces every pending alert
   * in one short reply.
   */
  private maybeAnnounce(): void {
    if (!this.alerts.length || this.announcing || this.current || this.lineup.length || this.draft || this.turn) return;
    const now = Date.now();
    if (now - this.lastSpeechAt < ALERT_GAP_MS || now - this.lastPlayedAt < ALERT_GAP_MS) return;
    // Drop news that's out of date: an approval already answered with a button.
    const alerts = this.alerts.filter((a) => a.kind !== "approval" || this.board.get(a.task.id)?.status === "needs_approval");
    this.alerts = [];
    if (!alerts.length) return;
    // The agent you're talking to announces everything. Its own tasks it talks
    // about as "I" ("I've got the signup form ready"), never by its own name.
    const lines = alerts.map((a) => {
      const own = a.task.owner === this.active;
      const owner = cap(a.task.owner);
      if (a.kind === "done") {
        return own
          ? `You finished your own background task #${a.task.id} "${a.task.title}". Say it in first person, like "By the way, I've finished the ${a.task.title}."`
          : `${owner}'s background task #${a.task.id} "${a.task.title}" is done. Say it like "By the way, ${owner} finished the ${a.task.title}."`;
      }
      return own
        ? `Your own background task #${a.task.id} "${a.task.title}" is ready to apply its changes and needs the user's OK. Ask in first person, like "By the way, I've got the ${a.task.title} ready. OK if I apply it?" ` +
            `When they answer, just say you will (or won't) go ahead, like "Great, I'll apply it now."`
        : `${owner}'s background task #${a.task.id} "${a.task.title}" needs the user's OK: ${a.task.question} Ask them, like "By the way, ${owner} has the ${a.task.title} ready and wants your OK to apply it." ` +
            `When they answer, just say you'll let ${owner} know, like "Great, I'll tell ${owner} to go ahead." It's not your work, so never say you'll apply it.`;
    });
    this.log(`alert: ${this.active} announces ${alerts.map((a) => `#${a.task.id} ${a.kind}`).join(", ")}`);
    this.send({ type: "announce", name: this.active, alerts: alerts.map((a) => ({ id: a.task.id, kind: a.kind, owner: a.task.owner, title: a.task.title })) });
    this.reply(this.active, `(Note: news for the user now, briefly, in your own words. ${lines.join(" ")} Only this news; don't repeat earlier news.)`);
    if (this.current) this.announcing = { reply: this.current, alerts };
  }

  /** `answers`: when you stopped talking, if this reply answers you (for the latency log). */
  private reply(agent: AgentName, note?: string, answers?: { stoppedAt: number; drafted: boolean }): void {
    const v = this.voices.get(agent);
    if (!v) return;
    if (this.current) this.cut("new reply");
    this.play(v.respond(note));
    if (answers && this.current) this.timing.set(this.current, answers);
  }

  /** Only the current reply's audio is played. */
  private onAgentAudio(r: Reply, pcm: Int16Array): void {
    if (r === this.current) this.queued = concat([this.queued, pcm]);
  }

  private onReplyEnded(r: Reply): void {
    if (r.outcome !== "completed") this.log(`${r.agent} reply ${r.outcome} (${msOf(r.sent)} ms played)`);
  }

  /** Stop whoever is talking now, and drop what was lined up. What you already heard is kept, and relayed to the others. */
  private cut(why: string): void {
    for (const q of this.lineup.splice(0)) this.voices.get(q.agent)?.stop(q);
    const r = this.current;
    if (!r) return;
    this.current = null;
    this.voices.get(r.agent)?.stop(r);
    this.queued = new Int16Array(0);
    this.log(`cut ${r.agent} at ${msOf(r.sent)} ms played (${why}; reply was ${r.state})`);
    if (r.sent > 0) this.send({ type: "clear" });
    this.finish(r, true);
  }

  /**
   * 100 ms of the current reply per tick, a little ahead when starting from
   * silence. When it ends (played out, or at its wrap-up point), the next one
   * in the lineup starts on the same tick: no gap if its audio is ready.
   */
  private tick(): void {
    if (Math.round((Date.now() - this.startedAt) / 100) % 20 === 0) this.send({ type: "usage", ...this.usageReport() });
    let sent = false;
    while (this.current) {
      const r = this.current;
      const limit = this.stopAt.get(r) ?? Infinity;
      // One chunk per tick; a lead when starting from silence; none if this tick already sent.
      let budget = sent ? 0 : r.sent === 0 && !this.sentLastTick ? LEAD_CHUNKS : 1;
      for (; budget > 0 && this.queued.length > 0 && r.sent < limit; budget--) {
        if (r.sent === 0) {
          const t = this.timing.get(r);
          if (t) t.latencyMs = Date.now() - t.stoppedAt;
          this.log(
            `${r.agent} audible ${Date.now() - r.askedAt} ms after response.create` +
              (t ? `, ${t.latencyMs} ms after you stopped talking (${t.drafted ? "draft" : "fresh"})` : ""),
          );
          this.send({ type: "speaking", name: r.agent });
        }
        const chunk = this.queued.subarray(0, Math.min(CHUNK_SAMPLES, limit - r.sent));
        this.queued = this.queued.subarray(chunk.length);
        r.sent += chunk.length;
        if (this.ws.readyState === WebSocket.OPEN) this.ws.send(Buffer.from(chunk.buffer, chunk.byteOffset, chunk.byteLength), { binary: true });
        sent = true;
        this.lastPlayedAt = Date.now();
      }
      const over = r.sent >= limit || (this.queued.length === 0 && (r.state === "done" || r.cancelled));
      if (!over) break;
      this.current = null;
      const total = r.audio.reduce((n, c) => n + c.length, 0);
      this.finish(r, r.sent < total);
      const next = this.lineup.shift();
      if (!next) break;
      // The next reply starts now if nothing was sent this tick, else on the next tick: no gap either way.
      this.play(next);
    }
    this.sentLastTick = sent;
    this.maybeAnnounce();
  }

  /** A reply ended, played out or cut: show it, let the other agents hear it, and tell the overseer. */
  private finish(r: Reply, cutOff: boolean): void {
    this.send({ type: "speaking", name: null });
    if (this.announcing?.reply === r) {
      // Cut off (you spoke over it): the news goes back on the queue for the next gap.
      // Delivered only if it was heard out. Cut off (you spoke over it), or a reply
      // that said nothing (it only called a tool): back on the queue for the next gap.
      const heard = !cutOff && r.sent > 0;
      if (!heard) this.alerts.unshift(...this.announcing.alerts);
      else {
        // Your next turn answers it, and that answer is for the agent who asked.
        for (const a of this.announcing.alerts) {
          if (a.kind !== "approval") continue;
          this.approvals.asked.add(a.task.owner);
          this.overseer?.send(
            textItem(
              `(Note: ${r.agent} just asked the user to approve ${a.task.owner}'s task #${a.task.id} "${a.task.title}". ` +
                `When the user answers, call answer_approval with task_id ${a.task.id}. Their answer is for ${r.agent}; don't switch for it.)`,
            ),
          );
        }
      }
      this.log(`alert ${heard ? "delivered" : cutOff ? "cut off, re-queued" : "not spoken, re-queued"}: ${this.announcing.alerts.map((a) => `#${a.task.id} ${a.kind}`).join(", ")}`);
      this.announcing = null;
    } else if (!cutOff && r.sent > 0 && this.approvals.answering.size) {
      // The agent replied to your answer: from your next turn, swaps are allowed again.
      this.approvals.replied = true;
    }
    // A reply that only called a tool said nothing: the same agent says what happened, in first person.
    const did = r.calls.flatMap((c) => (c.did ? [c.did] : []));
    if (!cutOff && r.sent === 0 && did.length) {
      const v = this.voices.get(r.agent);
      if (v) this.lineup.unshift(v.respond(`(Note: tell the user in one short sentence, in first person: ${did.join("; ")}.)`));
    }
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
    this.board.close();
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

const cap = (name: string) => name[0].toUpperCase() + name.slice(1);
