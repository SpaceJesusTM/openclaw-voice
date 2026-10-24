import type { AgentName, Persona } from "../src/agent/selectorAgents";
import { CHUNK_SAMPLES, decodeB64Pcm, msOf } from "../src/audio/pcm";
import { SERVER, responseCancel, responseCreate, textItem, truncateItem, type ServerEvent } from "../src/realtime/events";
import type { RealtimeSession } from "../src/realtime/RealtimeSession";

/**
 * Higgs never sends response.done for a response cancelled before its first
 * audio, though the session is free (probed 2026-10-24,
 * tools/probe/cancelEarly.ts). So a reply stopped that early is cancelled when
 * its first audio arrives, which Higgs ends cleanly. This is only the fallback
 * for a stopped response that never produces audio at all.
 */
const CANCEL_GRACE_MS = 4000;

/**
 * One reply, through its whole life:
 *
 *   queued     waiting behind this agent's earlier operations
 *   requested  response.create sent, response.created not seen yet
 *   live       bound to a response id; audio arriving
 *   done       ended: completed, cancelled, failed, or skipped before it was requested
 *
 * Generated faster than real time; the room plays it and counts `sent`.
 */
export interface Reply {
  readonly agent: AgentName;
  state: "queued" | "requested" | "live" | "done";
  /** How it ended. */
  outcome: "" | "completed" | "cancelled" | "failed" | "skipped" | "abandoned";
  responseId: string;
  itemId: string;
  audio: Int16Array[];
  fragments: { text: string; endMs: number }[];
  transcript: string;
  cancelled: boolean;
  /** response.cancel has been sent for it. */
  cancelSent: boolean;
  /** Re-requested once after the server failed it. */
  retried: boolean;
  askedAt: number;
  /** Samples the room has played of it. */
  sent: number;
  /** Set when stopped at a point still ahead of playback: the agent's memory is truncated there, not at `sent`. */
  keepSamples: number | null;
  /** Resolves when the reply is done. */
  readonly ended: Promise<void>;
}

export interface VoiceSink {
  /** Audio of a reply that hasn't been cancelled. */
  audio(r: Reply, pcm: Int16Array): void;
  /** The reply is done (any outcome). */
  ended(r: Reply): void;
}

/**
 * One agent's voice session, with manual turns. All operations on it are
 * queued, because a commit is rejected while it's generating.
 *
 * One listener for the session's lifetime routes every response event to the
 * reply it belongs to, by response id. Events from any other response (late
 * ones from a reply already given up on) are ignored, and a response nobody
 * asked for is cancelled. So a cut reply can never touch the next one.
 */
export class SelectorVoice {
  private chain: Promise<unknown> = Promise.resolve();
  /** The reply that owns the session's response: requested or live. At most one. */
  private inflight: Reply | null = null;
  private finishers = new Map<Reply, () => void>();
  private graceTimers = new Map<Reply, NodeJS.Timeout>();

  constructor(
    readonly persona: Persona,
    readonly session: RealtimeSession,
    private sink: VoiceSink,
    private log: (msg: string) => void,
  ) {
    session.on((e) => this.onEvent(e));
  }

  get name(): AgentName {
    return this.persona.name;
  }

  /** Commit audio as a user item, labelled with who was talking. */
  hear(label: string, samples: Int16Array): Promise<void> {
    return this.queue(async () => {
      if (samples.length === 0) return;
      this.session.send(textItem(label));
      for (let i = 0; i < samples.length; i += CHUNK_SAMPLES) this.session.appendAudio(samples.subarray(i, i + CHUNK_SAMPLES));
      await this.session.commit();
    });
  }

  /** A text message in the conversation, in order with the audio. */
  note(text: string): Promise<void> {
    return this.queue(async () => this.session.send(textItem(text)));
  }

  /** Ask for a reply, after an optional stage direction. Returns at once; audio goes to the sink. */
  respond(note?: string): Reply {
    let finish!: () => void;
    const r: Reply = {
      agent: this.name,
      state: "queued",
      outcome: "",
      responseId: "",
      itemId: "",
      audio: [],
      fragments: [],
      transcript: "",
      cancelled: false,
      cancelSent: false,
      retried: false,
      askedAt: 0,
      sent: 0,
      keepSamples: null,
      ended: new Promise<void>((res) => (finish = res)),
    };
    this.finishers.set(r, finish);
    void this.queue(async () => {
      if (r.state === "done") return; // stopped while queued
      if (note) this.session.send(textItem(note));
      r.state = "requested";
      r.askedAt = Date.now();
      this.inflight = r;
      this.session.send(responseCreate());
      await r.ended;
    });
    return r;
  }

  /**
   * Stop a reply at `sent` samples played, or at `keepSamples` when the room
   * will play on to that point (a graceful handoff). Queued: it's skipped.
   * Requested or live: its response is cancelled. Either way the agent's
   * memory is truncated to what will have been heard.
   */
  stop(r: Reply, keepSamples?: number): void {
    if (r.cancelled) return;
    r.cancelled = true;
    if (keepSamples !== undefined) r.keepSamples = keepSamples;
    if (r.state === "queued") return this.end(r, "skipped");
    if (r.state === "done") return this.truncate(r);
    // Requested, or live with no audio yet: cancelled when its first audio arrives (see onEvent).
    if (r.state === "live" && r.audio.length > 0) this.sendCancel(r);
    this.graceTimers.set(
      r,
      setTimeout(() => {
        if (r.state === "done") return;
        this.log(`${this.name}: no response.done ${CANCEL_GRACE_MS} ms after cancel (${r.state}); treating the session as idle`);
        this.session.markIdle();
        this.end(r, "abandoned");
      }, CANCEL_GRACE_MS),
    );
  }

  private onEvent(e: ServerEvent): void {
    const r = this.inflight;
    if (e.type === "__closed") {
      if (r) this.end(r, "failed");
      return;
    }
    if (e.type === SERVER.responseCreated) {
      const id = String((e.response as { id?: string } | undefined)?.id ?? "");
      if (r && r.state === "requested") {
        r.responseId = id;
        r.state = "live";
      } else {
        this.log(`${this.name}: unrequested response ${id}; cancelling it`);
        this.session.send(responseCancel());
      }
      return;
    }
    const id = String(e.response_id ?? (e.response as { id?: string } | undefined)?.id ?? "");
    if (!r || r.state !== "live" || !id || id !== r.responseId) return;
    if (e.type === SERVER.outputItemAdded && !r.itemId) r.itemId = String((e.item as { id?: string })?.id ?? "");
    else if (e.type === SERVER.outputAudioDelta) {
      const pcm = decodeB64Pcm(String(e.delta ?? ""));
      r.audio.push(pcm);
      if (!r.cancelled) this.sink.audio(r, pcm);
      else if (!r.cancelSent) this.sendCancel(r);
    } else if (e.type === SERVER.audioTranscriptLength) r.fragments.push({ text: String(e.delta ?? ""), endMs: Number(e.length_ms ?? 0) });
    else if (e.type === SERVER.audioTranscriptDone) r.transcript = String(e.transcript ?? "");
    else if (e.type === SERVER.responseDone) {
      const status = String((e.response as { status?: string } | undefined)?.status ?? "");
      // The 1.5 preview fails some responses outright ("upstream_error"). One that
      // produced no audio yet is asked for again, once, so you're never left in silence.
      if (status === "failed" && !r.cancelled && !r.retried && r.audio.length === 0) {
        this.log(`${this.name}: reply failed before any audio; asking again`);
        r.retried = true;
        r.state = "requested";
        r.responseId = "";
        this.session.send(responseCreate());
        return;
      }
      this.end(r, status === "failed" ? "failed" : r.cancelled ? "cancelled" : "completed");
    }
  }

  private sendCancel(r: Reply): void {
    r.cancelSent = true;
    this.session.send(responseCancel());
  }

  private end(r: Reply, outcome: Exclude<Reply["outcome"], "">): void {
    if (r.state === "done") return;
    r.state = "done";
    r.outcome = outcome;
    if (this.inflight === r) this.inflight = null;
    clearTimeout(this.graceTimers.get(r));
    this.graceTimers.delete(r);
    if (r.cancelled) this.truncate(r);
    this.finishers.get(r)?.();
    this.finishers.delete(r);
    this.sink.ended(r);
  }

  /** Forget what was never heard, so the agent doesn't think it said it. */
  private truncate(r: Reply): void {
    if (r.itemId) this.session.send(truncateItem(r.itemId, msOf(r.keepSamples ?? r.sent)));
  }

  private queue(fn: () => Promise<void>): Promise<void> {
    const run = this.chain.then(fn);
    this.chain = run.catch(() => undefined);
    return run;
  }
}
