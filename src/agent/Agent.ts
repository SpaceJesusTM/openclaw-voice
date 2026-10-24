import { decodeB64Pcm } from "../audio/pcm";
import { MODEL, SERVER, functionCallOutput, responseCreate, textItem, truncateItem, type ServerEvent } from "../realtime/events";
import { emptyUsage, type Usage } from "../realtime/pricing";
import { RealtimeSession, type SessionLog } from "../realtime/RealtimeSession";
import { buildSessionConfig } from "../realtime/sessionConfig";
import { listNames } from "../room/Journal";
import { Utterance } from "../room/Utterance";
import { MIND_TOOLS, parseDecision, type Decision } from "./mindTools";
import { VOICE_ONLY_TOOLS } from "./voiceTools";

export interface AgentConfig {
  name: string;
  /** Who they are, as a sentence: "the team's project manager, who owns the schedule." */
  role: string;
  /**
   * What they know for certain. Kept apart from the role so it carries weight:
   * mixed into the role, an agent let a confident colleague talk it out of a fact.
   */
  facts?: string;
  /** What they want out of this conversation; makes them push for it. */
  goal?: string;
  /** A Higgs preset (chloe, eleanor, jake, marcus, nora, oliver) or a custom voice id. */
  voice: string;
  /**
   * Model for each session; both default to MODEL. Measured 2026-10-24: 1.5
   * talks like speech (1 reads out lists), but 1 is faster and does not fail
   * under simultaneous load, which is when minds decide (docs/model-comparison.md).
   */
  voiceModel?: string;
  mindModel?: string;
  /**
   * Give the agent a private mind: a second, text session that decides when
   * it speaks and can cut in mid-sentence, with its thoughts visible. Without
   * one, the voice decides alone: at each pause it speaks or calls
   * stay_silent. Defaults to true; the web app defaults to false.
   */
  mind?: boolean;
}

export interface RoomContext {
  /** Everyone else in the room, by name. */
  others: string[];
  /** The shared situation every participant knows about. */
  scene?: string;
  /** The human's name, if they gave one, so agents can address them. */
  human?: string;
}

/**
 * One agent's Higgs sessions, on the same harness for every agent:
 *
 *   voice  audio out, manual turns. Hears everyone else's real audio and
 *          speaks when its own loop (AgentLoop) decides to. With no mind, it
 *          decides itself: it speaks, or calls stay_silent / start_work.
 *   mind   optional; text out, private. Hears everything (its own voice
 *          included) and decides with speak / interrupt / wait / start_work,
 *          each carrying a thought.
 *
 * These are primitives; the behaviour lives in AgentLoop. Operations on each
 * session are serialized: a commit is rejected while the session is
 * generating, and a decision must see the audio committed before it.
 */
export class Agent {
  private mindChain: Promise<unknown> = Promise.resolve();
  private mindQueued = 0;
  private voiceChain: Promise<unknown> = Promise.resolve();

  private constructor(
    readonly config: AgentConfig,
    readonly voice: RealtimeSession,
    readonly mind: RealtimeSession | null,
    private voicePrompt: string,
    /** Who the agent is, for the prompt it works under after leaving the conversation. */
    private identity: string,
    private log: SessionLog,
  ) {}

  get name(): string {
    return this.config.name;
  }

  get hasMind(): boolean {
    return this.mind !== null;
  }

  /** Tokens spent so far: what it said (voice) and what it thought (mind). */
  get usage(): { voice: Usage; mind: Usage } {
    return { voice: this.voice.usage, mind: this.mind?.usage ?? emptyUsage() };
  }

  static async create(config: AgentConfig, room: RoomContext, apiKey: string, log: SessionLog): Promise<Agent> {
    const company = `${listNames(room.others)} and ${room.human ? `${room.human} (a human)` : "a human"}`;
    const facts = config.facts ? ` You know for certain: ${config.facts}` : "";
    const goal = config.goal ? ` Your goal in this conversation: ${config.goal}` : "";
    const scene = room.scene ? ` The situation: ${room.scene}` : "";
    const who = `${config.name}, ${config.role}${facts}${goal} You are in a live voice meeting with ${company}.${scene}`;
    const withMind = config.mind !== false;
    // Labels come BEFORE the audio they describe ("[Bob:]" then Bob's audio):
    // labels placed after it got attached to the wrong speaker.
    const voicePrompt =
      `You are ${who} ` +
      `Speak naturally, in one to three short sentences, and address people by name. ` +
      (withMind ? "" : "Speak only when you're addressed or have something new for your goal. ") +
      `Messages starting "(Note:" say who is talking in the audio after them; never say them aloud.`;
    const mindPrompt =
      `You are the private inner voice of ${who} ` +
      `You never speak aloud: you decide when ${config.name} speaks. A note like "[Bob:]" says who is talking in the audio after it. ` +
      `If anyone contradicts what you know for certain, interrupt them. Speak when you are addressed or it moves your goal forward; otherwise wait. ` +
      `Each time, reply only with exactly one tool call, no text.`;

    const voiceModel = config.voiceModel ?? MODEL;
    const mindModel = config.mindModel ?? MODEL;
    const [voice, mind] = await Promise.all([
      RealtimeSession.connect(
        `${config.name}:voice`,
        apiKey,
        buildSessionConfig({
          instructions: voicePrompt,
          modality: "audio",
          voice: config.voice,
          model: voiceModel,
          tools: withMind ? [] : VOICE_ONLY_TOOLS,
        }),
        log,
        voiceModel,
      ),
      withMind
        ? RealtimeSession.connect(
            `${config.name}:mind`,
            apiKey,
            buildSessionConfig({ instructions: mindPrompt, modality: "text", tools: MIND_TOOLS, model: mindModel }),
            log,
            mindModel,
          )
        : Promise.resolve(null),
    ]);
    return new Agent(config, voice, mind, voicePrompt, `You are ${who}`, log);
  }

  /** Everyone else's audio. The voice never hears itself. */
  hearVoice(chunk: Int16Array): void {
    this.voice.appendAudio(chunk);
  }

  /** The whole room, own voice included. */
  hearMind(chunk: Int16Array): void {
    this.mind?.appendAudio(chunk);
  }

  /**
   * Commit what the voice has heard, labelled with who was talking ("Bob").
   * A plain sentence, not "[Bob:]": the voice copied that format and said
   * "[Bob:]" aloud. (A system-role item would be ideal, but the server rejects
   * it: "Invalid role: system", 2026-10-23.)
   */
  closeVoiceTurn(speakers: string): Promise<void> {
    return this.withVoice(() => labelAndCommit(this.voice, `(Note: the next audio is ${speakers} speaking.)`));
  }

  /** Commit what the mind has heard, labelled, without asking it to decide. No-op without a mind. */
  noteToMind(label: string): Promise<void> {
    const mind = this.mind;
    return mind ? this.withMind(() => labelAndCommit(mind, label)) : Promise.resolve();
  }

  /** True while a mind operation is running or queued. */
  get mindBusy(): boolean {
    return this.mindQueued > 0;
  }

  /**
   * Commit what the mind has heard under `label` ("[Bob:]"), add `ask` after
   * it if given ("(Bob is still speaking.)"), and get one decision.
   */
  think(label: string, ask?: string): Promise<Decision> {
    const mind = this.mind;
    if (!mind) return Promise.reject(new Error(`${this.name} has no mind`));
    return this.withMind(async () => {
      await labelAndCommit(mind, label);
      if (ask) mind.send(textItem(ask));

      // One retry: the 1.5 preview sometimes fails a response outright
      // ("upstream_error", status "failed"), which is not a decision.
      for (let attempt = 0; attempt < 2; attempt++) {
        let call: ServerEvent | null = null;
        const off = mind.on((e) => {
          if (e.type === SERVER.functionCallArgsDone && !call) call = e;
        });
        const done = mind.waitFor((e) => e.type === SERVER.responseDone, 15_000, "mind decision");
        mind.send(responseCreate());
        let failed = false;
        try {
          const evt = await done;
          failed = (evt.response as { status?: string } | undefined)?.status === "failed";
        } catch (e) {
          this.log(mind.name, `no decision: ${e instanceof Error ? e.message : String(e)}`);
        } finally {
          off();
        }
        if (call) {
          const c = call as ServerEvent;
          // Close the tool loop so the conversation stays valid; no follow-up response.
          mind.send(functionCallOutput(String(c.call_id), JSON.stringify({ ok: true })));
          return parseDecision(String(c.name), String(c.arguments ?? ""));
        }
        if (!failed) break;
        this.log(mind.name, "response failed, retrying once");
      }
      return { kind: "wait", thought: "" } satisfies Decision;
    });
  }

  /**
   * Speak, steered by `steer`. The steer goes into the session prompt via
   * session.update: a per-response `instructions` override makes the model
   * ignore the conversation (and on 1.5, return nothing), and a system-role
   * message is rejected (probed 2026-10-23/24).
   * Returns at once with an Utterance that fills as audio is generated.
   */
  speak(steer: string, cue?: string): Promise<Utterance> {
    return this.withVoice(() => this.startResponse(steer, cue));
  }

  /**
   * `cue` adds a user message first. Needed when nothing new was heard since
   * the voice last spoke (e.g. reporting after its work): the server refuses
   * a response with "No user input".
   */
  private async startResponse(steer: string, cue?: string): Promise<Utterance> {
    await this.voice.commit();
    await this.voice.update({ instructions: `${this.voicePrompt}\n\nRight now: ${steer}` });
    if (cue) this.voice.send(textItem(cue));
    const utt = new Utterance(this.name);
    const off = this.voice.on((e) => {
      if (e.type === SERVER.outputItemAdded && !utt.itemId) utt.itemId = String((e.item as { id?: string })?.id ?? "");
      else if (e.type === SERVER.outputAudioDelta) utt.push(decodeB64Pcm(String(e.delta ?? "")));
      else if (e.type === SERVER.audioTranscriptLength) {
        utt.fragments.push({ text: String(e.delta ?? ""), endMs: Number(e.length_ms ?? 0) });
      } else if (e.type === SERVER.audioTranscriptDone) utt.transcript = String(e.transcript ?? "");
      else if (e.type === SERVER.functionCallArgsDone) {
        const callId = String(e.call_id ?? "");
        if (!utt.calls.some((c) => c.callId === callId)) utt.calls.push({ name: String(e.name ?? ""), args: String(e.arguments ?? ""), callId });
      } else if (e.type === SERVER.responseDone || e.type === "__closed") {
        off();
        // The 1.5 preview fails some responses under load ("upstream_error").
        utt.failed = e.type === "__closed" || (e.response as { status?: string } | undefined)?.status === "failed";
        // Close the tool loop (stay_silent, start_work); the loop acts on them, no follow-up response.
        for (const c of utt.calls) this.voice.send(functionCallOutput(c.callId, JSON.stringify({ ok: true })));
        utt.finish();
      }
    });
    this.voice.send(responseCreate());
    return utt;
  }

  /**
   * Out of the conversation: do the task and return the result as text. The
   * voice session switches to text for this (probed: modality can change
   * mid-session) and back to audio after, so the work stays in its memory.
   */
  doWork(task: string): Promise<string> {
    return this.withVoice(async () => {
      await this.voice.commit();
      await this.voice.update({
        output_modalities: ["text"],
        instructions: `${this.identity} You have left the conversation to do your work. Write the finished result: concrete, concise, no preamble.`,
      });
      this.voice.send(textItem(`(Do this now: ${task})`));
      let text = "";
      const off = this.voice.on((e) => {
        if (e.type === SERVER.outputTextDelta) text += String(e.delta ?? "");
      });
      const done = this.voice.waitFor((e) => e.type === SERVER.responseDone, 90_000, "work");
      this.voice.send(responseCreate());
      try {
        await done;
      } finally {
        off();
        await this.voice.update({ output_modalities: ["audio"], instructions: this.voicePrompt });
      }
      return text.trim();
    });
  }

  /** Make the voice forget everything past what was actually heard. */
  async truncate(utt: Utterance, playedMs: number): Promise<void> {
    await utt.finished;
    if (utt.itemId) this.voice.send(truncateItem(utt.itemId, playedMs));
  }

  async close(): Promise<void> {
    await Promise.all([this.voice.close(), this.mind?.close()]);
  }

  private withMind<T>(fn: () => Promise<T>): Promise<T> {
    this.mindQueued++;
    const run = this.mindChain.then(fn).finally(() => this.mindQueued--);
    this.mindChain = run.catch(() => undefined);
    return run;
  }

  /** Two concurrent commits on one session would race the buffer; queue them. */
  private withVoice<T>(fn: () => Promise<T>): Promise<T> {
    const run = this.voiceChain.then(fn);
    this.voiceChain = run.catch(() => undefined);
    return run;
  }
}

/** If anything was heard: the label, then the audio as a user item after it. */
async function labelAndCommit(session: RealtimeSession, label: string): Promise<void> {
  if (session.pendingSamples === 0) return;
  session.send(textItem(label));
  await session.commit();
}
