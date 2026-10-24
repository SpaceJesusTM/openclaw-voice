import type { AudioBus, BusListener, Stream, Tick } from "../room/AudioBus";
import { listNames, type Journal } from "../room/Journal";
import type { Utterance } from "../room/Utterance";
import type { Agent } from "./Agent";
import type { Decision } from "./mindTools";
import { taskOf } from "./voiceTools";

export interface LoopOptions {
  /**
   * Silence after others' speech at which this agent starts getting ready: its
   * mind decides and its voice drafts a reply, in parallel. Thrown away if the
   * speaker carries on.
   */
  prepareAtMs: number;
  /** Silence after others' speech that counts as the end of their turn. */
  endOfTurnMs: number;
  /** While someone else talks, consider cutting in after every this much of their speech. */
  checkEveryMs: number;
  /** Agent turns (heard or own) since the human last spoke, after which this agent stays quiet. */
  maxAgentTurns: number;
  /** Reaction-time spread before starting to talk, so two agents rarely start on the same tick. */
  reactionJitterMs: number;
  humanName: string;
  journal: Journal;
}

/**
 * Silence-to-speech, measured 2026-10-24 on hybrid agents (1.5 voice, 1 mind):
 *   sequential (wait 700, then mind ~900, then voice ~650)   2.4–2.6 s
 *   decide + draft side by side at a 300 ms pause            1.1–1.4 s
 *   the same at a 200 ms pause                               1.0–1.2 s
 * What remains is the pause itself plus the voice's time to first audio
 * (~650–800 ms with conversation history); the mind finishes about then too.
 */
export const DEFAULT_LOOP_TIMING = { prepareAtMs: 200, endOfTurnMs: 500, checkEveryMs: 1500, reactionJitterMs: 100 } as const;

/** A draft with no intention yet comes from role, goal and the conversation alone. */
const DRAFT_STEER = "Respond to what was just said, in one to three short sentences.";

const speakSteer = (intent: string) => `Say this, in your own words: ${intent}`;

/**
 * With no mind, the voice is offered the turn and decides: speak, stay_silent,
 * or start_work. start_work is named here as well as in its description: with
 * only the description, an agent said "I'll go write the plan now" and stayed.
 */
const VOICE_DECIDES_STEER =
  "It's your turn if you want it: reply in one to three short sentences, or call stay_silent. " +
  "If you now have what you need to do your job, say a short goodbye and call start_work.";

/** speak: my turn. interrupt: talking over someone on purpose. handoff: a quick "go ahead" after a collision. */
type Mode = "speak" | "interrupt" | "handoff";

const MAX_HOLD_BACKS = 2;

/** A reply being readied at a pause: the voice's draft, and the mind's last look, in flight together. */
interface Prep {
  marks: Marks;
  /** What the mind had already decided while the others were talking, if anything. */
  intention: Decision | null;
  /** The mind's last look at the pause: a veto if it says wait. */
  decision: Promise<Decision>;
  draft: Promise<Utterance | null>;
}

/** Someone else's stretch of talk, as this agent experiences it. */
interface Stretch {
  speakers: Set<string>;
  talkMs: number;
  nextCheckMs: number;
  /** The mind's latest decision about what to do when they finish. */
  intention: Decision | null;
}

/** performance.now() at each stage of a reply, from the moment the room went quiet. */
interface Marks {
  silence: number;
  /** Getting ready began (the pause, or the end of turn on the slow path). */
  detected: number;
  committed?: number;
  decided?: number;
  /** The end of turn was confirmed. */
  confirmed?: number;
  jittered?: number;
  requested?: number;
  firstAudio?: number;
  firstPlayed?: number;
}

/**
 * "1150 ms from silence to speech: prepare +300, end of turn +500, first audio
 * +940, decided +1120, playing +1150". Offsets from silence, in order; stages
 * overlap when a reply is drafted at the pause.
 */
function latencyBreakdown(m: Marks): string {
  const points: [string, number | undefined][] = [
    ["prepare", m.detected],
    ["voice committed", m.committed],
    ["end of turn", m.confirmed],
    ["decided", m.decided],
    ["requested", m.requested],
    ["first audio", m.firstAudio],
    ["playing", m.firstPlayed],
  ];
  const parts = points
    .filter((p): p is [string, number] => p[1] !== undefined)
    .sort((a, b) => a[1] - b[1])
    .map(([n, t]) => `${n} +${Math.round(t - m.silence)}`);
  return `${Math.round((m.firstPlayed ?? m.silence) - m.silence)} ms from silence to speech: ${parts.join(", ")}`;
}

interface Attempt {
  mode: Mode;
  /** Set if someone else started talking before this agent's audio was ready. */
  abandoned: boolean;
  /** A normal turn that won't yield, because this agent has held back too often. */
  insistent: boolean;
  /** The mind's last look said wait after the voice had already gone ahead. */
  vetoed: boolean;
  utt: Utterance | null;
  stream: Stream | null;
  /** People this agent already chose to keep talking over during this attempt. */
  talkedOverBy: Set<string>;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * "I'll get the week's plan written up" → "get the week's plan written up": a
 * spoken commitment to go do something now. Not "I'll keep …" (an ongoing
 * promise) and not a question.
 */
const COMMITMENT =
  /\b(?:I'll|I will|let me|I'm going to|I am going to|I'm off to|I'll go|I'm heading (?:off|out) to|heading (?:off|out) to)\s+((?:go\s+)?(?:get|write|draft|put|start|work on|do|send|make|prepare|pull|set up|build|draw up|map out|plan|book|schedule)\b[^.?!]*)[.!]/i;

function commitmentIn(text: string): string | null {
  const m = COMMITMENT.exec(text);
  return m ? m[1].trim() : null;
}

/**
 * One agent's behaviour, run entirely by that agent. It hears the room through
 * the bus and makes every call itself. With no mind, the voice decides: at each
 * pause it is offered the turn and speaks, stays silent, or leaves to work;
 * when talked over it yields to the human. With a mind:
 *
 *  - while someone talks: what to do when they finish, decided ahead of time
 *    (and whether to cut in, if it's an agent), every couple of seconds
 *  - at their pause: the voice goes first on that decision; the mind's last
 *    look can still stop it
 *  - when someone talks over it: stop, keep going, or stop and settle the order
 *    out loud ("Bob, go ahead")
 *
 * Its only manners are mechanical: it won't START a normal turn over someone
 * who is already talking, and it goes quiet after too many agent-only turns.
 */
export class AgentLoop implements BusListener {
  private attempt: Attempt | null = null;
  private othersNow: string[] = [];
  private stretch: Stretch | null = null;
  private silenceMs = 0;
  /** Who each session has heard since its last commit, for the label before that audio. */
  private heardByVoice = new Set<string>();
  private heardByMind = new Set<string>();
  private agentTurns = 0;
  /**
   * Normal turns abandoned in a row because someone else got their audio out
   * first. A slower model (1.5 under load) lost every race to a Higgs 1 agent,
   * so after MAX_HOLD_BACKS this agent stops yielding and jumps in.
   */
  private heldBack = 0;
  private prep: Prep | null = null;
  /** The task this agent left the conversation to do, while it is doing it. */
  private working: string | null = null;
  /** Finished work, to report out loud at the next quiet moment. */
  private pendingReport: { task: string; result: string; tries: number } | null = null;
  /** Consecutive silence in the whole room, own voice included. */
  private quietMs = 0;
  /** Date.now() when this agent last finished a piece of work. */
  private lastWorkAt = 0;

  constructor(
    private agent: Agent,
    private bus: AudioBus,
    private opts: LoopOptions,
  ) {}

  get name(): string {
    return this.agent.name;
  }

  onTick(t: Tick): void {
    const others = t.active.filter((n) => n !== this.name);
    // Silence is not sent: it would be billed and tells the model nothing.
    if (others.length) this.agent.hearVoice(t.others);
    if (t.active.length) this.agent.hearMind(t.all);
    for (const n of t.active) this.heardByMind.add(n === this.name ? "you" : n);
    const started = others.filter((n) => !this.othersNow.includes(n));
    this.othersNow = others;
    this.quietMs = t.active.length ? 0 : this.quietMs + 100;
    if (this.pendingReport && !this.attempt && !this.working && this.quietMs >= this.opts.endOfTurnMs) void this.report();

    if (others.length) {
      this.silenceMs = 0;
      // They carried on after a pause: whatever was readied is moot.
      if (this.prep) this.discard(this.prep);
      for (const n of others) this.heardByVoice.add(n);
      this.stretch ??= { speakers: new Set(), talkMs: 0, nextCheckMs: this.opts.checkEveryMs, intention: null };
      for (const n of others) this.stretch.speakers.add(n);
      this.stretch.talkMs += 100;
      if (started.length) this.onOthersStarted(started);
      // Every few seconds of their speech, the mind updates what it will do
      // when they finish (and may cut in on an agent). Skipped while a
      // decision is in flight: a late one is useless.
      if (this.agent.hasMind && !this.attempt && !this.working && this.stretch.talkMs >= this.stretch.nextCheckMs) {
        this.stretch.nextCheckMs += this.opts.checkEveryMs;
        if (!this.agent.mindBusy) void this.thinkWhileListening(others, this.stretch);
      }
    } else if (this.stretch) {
      this.silenceMs += 100;
      const free = !this.attempt && !this.working && this.agentTurns < this.opts.maxAgentTurns;
      if (this.silenceMs === this.opts.prepareAtMs && free) {
        this.prep = this.prepare([...this.stretch.speakers], this.stretch.intention);
      }
      if (this.silenceMs >= this.opts.endOfTurnMs) {
        const speakers = [...this.stretch.speakers];
        this.stretch = null;
        const prep = this.prep;
        this.prep = null;
        const now = performance.now();
        const marks = prep?.marks ?? { silence: now - this.silenceMs, detected: now };
        marks.confirmed = now;
        void this.onOthersFinished(speakers, marks, prep);
      }
    }
  }

  /**
   * At a pause: the voice drafts a reply (steered by the intention the mind
   * already formed, if any) while the mind takes a last look. Nothing is
   * played yet. Even a "wait" intention gets a draft: the last look often
   * changes it, and a draft in hand keeps that case at ~1.3 s.
   */
  private prepare(speakers: string[], intention: Decision | null): Prep {
    const now = performance.now();
    const marks: Marks = { silence: now - this.silenceMs, detected: now };
    const who = listNames(speakers);
    const committed = this.commitVoice().then(() => void (marks.committed = performance.now()));
    if (!this.agent.hasMind) return this.prepareVoiceOnly(marks, committed);
    const decision = this.agent.think(this.mindLabel(), `(${who} paused, and may have finished.)`).then((d) => {
      marks.decided = performance.now();
      return d;
    });
    const steer = intention?.kind === "speak" ? speakSteer(intention.intent) : DRAFT_STEER;
    const draft = committed
      .then(() => this.agent.speak(steer))
      .then((u) => {
        marks.requested = performance.now();
        return u;
      })
      .catch((e) => {
        this.fail(e);
        return null;
      });
    return { marks, intention, decision, draft };
  }

  /**
   * No mind: the voice is offered the turn, and its response is the decision.
   * Audio means speak; stay_silent means wait; start_work (with no goodbye
   * spoken) means leave to work. One model call instead of two.
   */
  private prepareVoiceOnly(marks: Marks, committed: Promise<void>): Prep {
    const draft = committed
      .then(() => this.agent.speak(VOICE_DECIDES_STEER))
      .then((u) => {
        marks.requested = performance.now();
        return u;
      })
      .catch((e) => {
        this.fail(e);
        return null;
      });
    const decision = draft.then(async (u): Promise<Decision> => {
      if (!u) return { kind: "wait", thought: "" };
      await u.firstAudio; // resolves at the first audio, or when a silent response ends
      marks.decided = performance.now();
      if (u.totalSamples > 0) return { kind: "speak", thought: "", intent: "" };
      const work = u.calls.find((c) => c.name === "start_work");
      return work ? { kind: "start_work", thought: "", task: taskOf(work.args) } : { kind: "wait", thought: "" };
    });
    return { marks, intention: null, decision, draft };
  }

  /** Throw a prepared reply away. Nobody heard the draft, so the voice must not remember it. */
  private discard(p: Prep): void {
    if (this.prep === p) this.prep = null;
    void p.draft.then((u) => (u ? this.agent.truncate(u, 0) : undefined)).catch(() => undefined);
    void p.decision.catch(() => undefined);
  }

  private onOthersStarted(names: string[]): void {
    const a = this.attempt;
    if (!a) return;
    if (!a.stream) {
      // Still preparing: a normal turn politely holds back (unless it has done so too often).
      if (a.mode === "speak" && !a.insistent) a.abandoned = true;
      return;
    }
    // A quick "go ahead" is meant to overlap; and once this agent has chosen
    // to keep talking over someone, it does not reconsider every time they
    // restart (that turned collisions into a cut-each-other-off loop).
    const fresh = names.filter((n) => !a.talkedOverBy.has(n));
    if (a.mode !== "handoff" && fresh.length) void this.onTalkedOver(fresh, a);
  }

  private async onOthersFinished(speakers: string[], marks: Marks, prep: Prep | null): Promise<void> {
    if (speakers.includes(this.opts.humanName)) this.agentTurns = 0;
    else this.agentTurns += speakers.length;
    if (this.working) {
      if (prep) this.discard(prep);
      return;
    }
    if (this.attempt || this.agentTurns >= this.opts.maxAgentTurns) {
      if (prep) this.discard(prep);
      if (!this.attempt) {
        await this.commitVoice().catch((e) => this.fail(e));
        await this.agent.noteToMind(this.mindLabel()).catch((e) => this.fail(e));
        this.opts.journal.add(this.name, "note", `staying quiet: ${this.agentTurns} agent turns since the human spoke`);
      }
      return;
    }
    const who = listNames(speakers);
    try {
      if (!this.agent.hasMind) {
        // The voice decides by answering (or not); there was no time to prepare, so ask now.
        const p = prep ?? this.prepareVoiceOnly(marks, this.commitVoice());
        const d = await p.decision;
        if (d.kind === "speak") await this.say("speak", VOICE_DECIDES_STEER, p.marks, p.draft);
        else if (d.kind === "start_work") void this.beginWork(d.task);
        else this.opts.journal.add(this.name, "note", "stayed silent", undefined, { status: "passed" });
        return;
      }

      // Voice first: the mind already chose to speak while they were talking,
      // so the draft plays as soon as it has audio. The mind's last look still
      // runs, and can stop it. (Rare in practice: in 64 checks, minds never
      // committed to speaking while someone was still talking.)
      if (prep?.intention?.kind === "speak") {
        const intent = prep.intention.intent;
        const veto = prep.decision.then((d) => {
          this.noteThought(d, `last look after ${who}`);
          return d.kind === "wait";
        });
        await this.say("speak", speakSteer(intent), marks, prep.draft, veto);
        return;
      }

      let d: Decision;
      if (prep) {
        d = await prep.decision;
      } else {
        // No pause long enough to prepare in (or it was skipped): the slow path.
        await this.commitVoice();
        marks.committed = performance.now();
        d = await this.agent.think(this.mindLabel(), `(${who} finished speaking.)`);
        marks.decided = performance.now();
      }
      this.noteThought(d, `after ${who}`);
      if (d.kind === "wait") {
        if (prep) this.discard(prep);
        return;
      }
      if (d.kind === "start_work") {
        if (prep) this.discard(prep);
        await this.say("speak", `Say a short goodbye: you're leaving the conversation to do this: ${d.task}`, marks);
        void this.beginWork(d.task);
        return;
      }
      await this.say("speak", speakSteer(d.kind === "speak" ? d.intent : d.reason), marks, prep?.draft);
    } catch (e) {
      this.fail(e);
    }
  }

  /**
   * While someone else talks: decide now what to do when they finish (the
   * stretch's intention), or cut in on an agent who just said something wrong.
   * The human is never cut off.
   */
  private async thinkWhileListening(others: string[], stretch: Stretch): Promise<void> {
    const who = listNames(others);
    const cuttable = others.some((n) => n !== this.opts.humanName);
    try {
      // Asked as a plan for AFTER they finish: asked "speak or wait?" mid-speech,
      // minds answered wait meaning "until they're done" (2026-10-24).
      const d = await this.agent.think(
        this.mindLabel(),
        `(${who} is still speaking, so you can't talk yet. ` +
          (cuttable ? "Interrupt only if they just said something wrong. Otherwise plan ahead" : "Never interrupt the human. Plan ahead") +
          `: call speak with what you'll say the moment they finish, or wait only if you'll have nothing to add even after they finish.)`,
      );
      this.noteThought(d, `while ${who} talks`);
      if (d.kind !== "interrupt" || !cuttable) {
        // Only if they are still talking: an intention about a finished stretch is stale.
        if (this.stretch === stretch) {
          stretch.intention = d.kind === "interrupt" ? { kind: "speak", thought: d.thought, intent: d.reason } : d;
        }
        return;
      }
      if (this.attempt || this.othersNow.length === 0) return;
      const target = this.othersNow[0];
      await this.say(
        "interrupt",
        `You are cutting in on ${listNames(this.othersNow)} because: ${d.reason}. Start with a quick cut-in like "Sorry ${target}," and make the correction briefly.`,
      );
    } catch (e) {
      this.fail(e);
    }
  }

  private async onTalkedOver(names: string[], a: Attempt): Promise<void> {
    const who = listNames(names);
    if (!this.agent.hasMind) {
      // No mind to weigh it: always yield to the human; keep going over another agent.
      if (names.includes(this.opts.humanName) && a.stream) {
        a.stream.stop();
        this.opts.journal.add(this.name, "note", `stopped: ${who} started talking`);
      } else {
        for (const n of names) a.talkedOverBy.add(n);
      }
      return;
    }
    try {
      const d = await this.agent.think(
        this.mindLabel(),
        `(${who} started talking while you were speaking. Choose: speak = stop and let them go first, saying a few words like ` +
          `"Sorry, go ahead, ${names[0]}"; interrupt = keep talking, because what you are saying matters more right now; wait = just stop.)`,
      );
      if (this.attempt !== a || !a.stream) return;
      this.noteThought(d, `talked over by ${who}`);
      if (d.kind === "interrupt") {
        for (const n of names) a.talkedOverBy.add(n);
        return;
      }
      a.stream.stop();
      if (d.kind === "speak") {
        await a.stream.ended;
        // The handoff only settles the order; the point waits for the next turn.
        await this.say("handoff", `You and ${who} started talking at once. Say only a few words to let ${names[0]} go first, like "Sorry, go ahead, ${names[0]}."`);
      }
    } catch (e) {
      this.fail(e);
    }
  }

  /**
   * Take the floor: get a reply (the one drafted at the pause, if given), then
   * play it into the room unless someone beat us to it. If `veto` resolves
   * true, the mind has decided to wait after all: the draft is dropped, or
   * the voice stops itself if it has already started.
   */
  private async say(
    mode: Mode,
    steer: string,
    marks?: Marks,
    draft?: Promise<Utterance | null>,
    veto?: Promise<boolean>,
    /** A user message to add before speaking, when nothing new has been heard (see Agent.speak). */
    cue?: string,
  ): Promise<boolean> {
    if (this.attempt) {
      if (draft) void draft.then((u) => (u ? this.agent.truncate(u, 0) : undefined)).catch(() => undefined);
      return false;
    }
    let played = false;
    const insistent = mode === "speak" && this.heldBack >= MAX_HOLD_BACKS;
    const a: Attempt = { mode, abandoned: false, insistent, vetoed: false, utt: null, stream: null, talkedOverBy: new Set() };
    this.attempt = a;
    const yields = () => mode === "speak" && !insistent && this.othersNow.length > 0;
    void veto
      ?.then((v) => {
        if (!v || this.attempt !== a) return;
        a.vetoed = true;
        if (a.stream) a.stream.stop();
        else a.abandoned = true;
      })
      .catch(() => undefined);
    try {
      // A draft already varies in readiness; only fresh replies need jitter.
      if (mode === "speak" && !draft) await sleep(Math.random() * this.opts.reactionJitterMs);
      if (yields()) a.abandoned = true;
      if (marks) marks.jittered = performance.now();
      if (!a.abandoned) {
        const jumpIn = insistent && this.othersNow.length > 0;
        if (jumpIn) {
          steer = `You have been trying to get a word in. Start with a quick "Sorry, can I jump in?", then: ${steer}`;
          this.opts.journal.add(this.name, "note", `held back ${this.heldBack} times; jumping in`);
        }
        a.utt = draft ? await draft : null;
        if (a.utt && jumpIn) {
          // The draft has no "can I jump in?"; regenerate with it.
          await this.agent.truncate(a.utt, 0);
          a.utt = null;
        }
        if (!a.utt) {
          await this.commitVoice();
          a.utt = await this.agent.speak(steer, cue);
          if (marks) marks.requested = performance.now();
        }
        await a.utt.firstAudio;
        if (marks) marks.firstAudio = a.utt.firstAudioAt ?? performance.now();
        // A failed reply is not a choice to stay quiet: try once more.
        if (a.utt.failed && a.utt.totalSamples === 0 && !yields()) {
          this.opts.journal.add(this.name, "note", "voice reply failed on the server; retrying once");
          a.utt = await this.agent.speak(steer, cue);
          await a.utt.firstAudio;
        }
        if (yields()) a.abandoned = true;
      }
      if (a.abandoned || !a.utt || a.utt.totalSamples === 0) {
        if (a.vetoed) {
          this.opts.journal.add(this.name, "note", "dropped its draft: decided to wait after all");
        } else if (a.abandoned) {
          this.heldBack++;
          this.opts.journal.add(this.name, "note", "held back: someone else started talking first");
        } else if (a.utt) {
          const why = a.utt.failed ? "voice reply failed on the server" : "stayed silent";
          this.opts.journal.add(this.name, "note", why, undefined, a.utt.failed ? undefined : { status: "passed" });
        }
        // Nobody heard it, so the voice must not remember saying it.
        if (a.utt) await this.agent.truncate(a.utt, 0);
        return false;
      }

      const startedAt = this.bus.elapsedMs();
      const s = (a.stream = this.bus.play(this.name, a.utt));
      await s.ended;
      played = s.played > 0;
      this.attempt = null; // synchronously, so a handoff can take the floor next
      this.agentTurns++;
      this.heldBack = 0;
      const text = a.utt.textUpTo(s.playedMs) || a.utt.transcript;
      this.opts.journal.add(this.name, "said", s.stopped ? `${text} *(stopped at ${(s.playedMs / 1000).toFixed(1)} s)*` : text, startedAt);
      if (marks && s.firstPlayedAt) {
        marks.firstPlayed = s.firstPlayedAt;
        this.opts.journal.add(this.name, "note", `latency ${latencyBreakdown(marks)}`, startedAt + 1);
      }
      if (a.vetoed) this.opts.journal.add(this.name, "note", "stopped itself: decided to wait after all", startedAt + 2);
      if (s.stopped) await this.agent.truncate(a.utt, s.playedMs);
      await this.agent.noteToMind(this.mindLabel());
      // A voice that said its goodbye and called start_work leaves now. Higgs
      // doesn't always make the call while speaking (an agent said "I'll get
      // the week's plan written up" twice and stayed), so a clear spoken
      // commitment counts too, unless it just came back from working.
      const work = a.utt.calls.find((c) => c.name === "start_work");
      const committed = !this.agent.hasMind && !work && mode === "speak" && Date.now() - this.lastWorkAt > 30_000 ? commitmentIn(text) : null;
      if (!s.stopped && (work || committed)) {
        if (committed) this.opts.journal.add(this.name, "note", `said it would do it ("${committed}"); leaving to work`);
        void this.beginWork(work ? taskOf(work.args) : committed!);
      }
    } catch (e) {
      this.fail(e);
    } finally {
      if (this.attempt === a) this.attempt = null;
    }
    return played;
  }

  private async commitVoice(): Promise<void> {
    if (this.heardByVoice.size === 0) return;
    const who = listNames([...this.heardByVoice]);
    this.heardByVoice.clear();
    await this.agent.closeVoiceTurn(who);
  }

  /** "[Bob:]", "[Bob and you, at the same time:]": who is in the mind's uncommitted audio. Resets. */
  private mindLabel(): string {
    const names = [...this.heardByMind];
    this.heardByMind.clear();
    return `[${listNames(names) || "silence"}${names.length > 1 ? ", at the same time" : ""}:]`;
  }

  /**
   * Leave the conversation and do the task: the agent keeps hearing the room
   * but takes no turns, writes its deliverable, then comes back and reports.
   */
  private async beginWork(task: string): Promise<void> {
    if (this.working) return;
    this.working = task;
    if (this.prep) this.discard(this.prep);
    this.opts.journal.add(this.name, "note", `left the conversation to work on: ${task}`, undefined, { status: "working", task });
    try {
      const result = await this.agent.doWork(task);
      this.opts.journal.add(this.name, "work", result || "(no result)", undefined, { task });
      if (result) this.pendingReport = { task, result, tries: 0 };
    } catch (e) {
      this.fail(e);
    } finally {
      this.working = null;
      this.lastWorkAt = Date.now();
      this.opts.journal.add(this.name, "note", "finished the work; back in the conversation", undefined, { status: "back" });
    }
  }

  /** Back from working: tell the room the result, once it is quiet. */
  private async report(): Promise<void> {
    const r = this.pendingReport;
    if (!r) return;
    this.pendingReport = null;
    // "Out loud, no tools": a voice-only agent offered this turn chose stay_silent.
    // "Already finished, past tense": it once reported "I'll get that sent out".
    const played = await this.say(
      "speak",
      `You already finished this: ${r.task}. Say out loud now, in the past tense and one or two sentences, what you did and the key result; don't call any tool. Your result: ${r.result.slice(0, 600)}`,
      undefined,
      undefined,
      undefined,
      "(You're back in the conversation with your finished work.)",
    );
    // Lost to a server failure or someone else talking: try again at the next quiet moment.
    if (!played && r.tries < 2 && !this.pendingReport) this.pendingReport = { ...r, tries: r.tries + 1 };
  }

  private noteThought(d: Decision, context: string): void {
    const detail = d.kind === "speak" ? d.intent : d.kind === "interrupt" ? d.reason : d.kind === "start_work" ? d.task : "";
    this.opts.journal.add(
      this.name,
      "thought",
      `(${context}) thinks ${JSON.stringify(d.thought)} → ${d.kind}${detail ? `: ${detail}` : ""}`,
      undefined,
      { decision: d.kind, thought: d.thought, detail, context },
    );
  }

  private fail(e: unknown): void {
    this.opts.journal.add(this.name, "note", `error: ${e instanceof Error ? e.message : String(e)}`);
  }
}
