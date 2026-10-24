import WebSocket from "ws";
import {
  AUDIO_RATE,
  MODEL,
  SERVER,
  describeClose,
  type ClientEvent,
  type CloseInfo,
  type ServerEvent,
} from "../../src/realtime/events";
import { b64Samples, concat, decodeB64Pcm } from "../../src/audio/pcm";

export { b64Samples, concat, decodeB64Pcm };

const wsUrl = (model: string) => `wss://api.boson.ai/v1/realtime?model=${encodeURIComponent(model)}`;

/** One clock for every session, so timings compare across sessions. */
const RUN_START = performance.now();
export const now = () => performance.now() - RUN_START;

export interface Timed {
  at: number;
  evt: ServerEvent;
}

interface Waiter {
  pred: (e: ServerEvent) => boolean;
  resolve: (t: Timed) => void;
  reject: (e: Error) => void;
  timer: NodeJS.Timeout;
}

export interface ResponseSummary {
  createdAt: number;
  doneAt: number;
  firstAudioAt: number | null;
  firstTranscriptAt: number | null;
  firstTextAt: number | null;
  transcript: string;
  text: string;
  audio: Int16Array;
  calls: { at: number; name: string; args: string; callId: string }[];
  errors: ServerEvent[];
}

const str = (v: unknown) => (typeof v === "string" ? v : "");

/**
 * One raw Realtime socket, server-side auth, with every event timestamped and
 * kept. `waitFor` scans already-received events from an index first, so a
 * caller can `mark()`, send, and then wait without racing the reply.
 */
export class ProbeSession {
  readonly events: Timed[] = [];
  resolved: Record<string, any> | null = null;
  closed: CloseInfo | null = null;
  private waiters = new Set<Waiter>();
  private closedPromise: Promise<void>;
  private loggedAudioFor = new Set<string>();

  private constructor(
    readonly name: string,
    private ws: WebSocket,
    private verbose: boolean,
  ) {
    let done!: () => void;
    this.closedPromise = new Promise((r) => (done = r));
    ws.on("message", (data) => this.onMessage(JSON.parse(data.toString()) as ServerEvent));
    ws.on("close", (code, reason) => {
      this.onClose(code, reason.toString());
      done();
    });
    ws.on("error", (e) => this.log(`socket error: ${e.message}`));
  }

  static async open(
    name: string,
    apiKey: string,
    config: ClientEvent,
    verbose = true,
    model = MODEL,
    url = wsUrl(model),
  ): Promise<ProbeSession> {
    const ws = new WebSocket(url, { headers: { Authorization: `Bearer ${apiKey}` } });
    const s = new ProbeSession(name, ws, verbose);
    ws.on("open", () => s.send(config));
    try {
      const t = await s.waitFor(
        (e) => e.type === SERVER.sessionCreated || e.type === SERVER.error,
        15_000,
        "session.created",
      );
      if (t.evt.type === SERVER.error) {
        throw new Error(`${name}: session.update rejected: ${JSON.stringify(t.evt.error ?? t.evt)}`);
      }
      s.resolved = (t.evt.session as Record<string, any>) ?? null;
      return s;
    } catch (e) {
      // Each open session is billed; never leave a failed one dangling.
      await s.close();
      throw e;
    }
  }

  send(evt: ClientEvent): void {
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(evt));
  }

  mark(): number {
    return this.events.length;
  }

  since(from: number, type?: string): Timed[] {
    const out = this.events.slice(from);
    return type ? out.filter((t) => t.evt.type === type) : out;
  }

  waitFor(pred: (e: ServerEvent) => boolean, timeoutMs: number, label: string, from?: number): Promise<Timed> {
    if (from !== undefined) {
      const hit = this.events.slice(from).find((t) => pred(t.evt));
      if (hit) return Promise.resolve(hit);
    }
    if (this.closed) return Promise.reject(new Error(`${this.name} closed before ${label}: ${this.closed.message}`));
    return new Promise((resolve, reject) => {
      const w: Waiter = {
        pred,
        resolve,
        reject,
        timer: setTimeout(() => {
          this.waiters.delete(w);
          reject(new Error(`${this.name}: timed out after ${timeoutMs} ms waiting for ${label}`));
        }, timeoutMs),
      };
      this.waiters.add(w);
    });
  }

  waitType(type: string, timeoutMs: number, from?: number): Promise<Timed> {
    return this.waitFor((e) => e.type === type, timeoutMs, type, from);
  }

  /**
   * Wait for the response started after `from` to finish, and gather what it
   * produced. Pass `from` = mark() taken just before sending response.create.
   */
  async collectResponse(from: number, timeoutMs = 45_000): Promise<ResponseSummary> {
    const created = await this.waitType(SERVER.responseCreated, timeoutMs, from);
    const done = await this.waitType(SERVER.responseDone, timeoutMs, from);
    const evs = this.events.slice(from, this.events.indexOf(done) + 1);
    const first = (type: string) => evs.find((t) => t.evt.type === type)?.at ?? null;

    const chunks: Int16Array[] = [];
    let transcript = "";
    let text = "";
    const calls: ResponseSummary["calls"] = [];
    const seen = new Set<string>();
    for (const { at, evt } of evs) {
      if (evt.type === SERVER.outputAudioDelta) chunks.push(decodeB64Pcm(str(evt.delta)));
      if (evt.type === SERVER.audioTranscriptDelta) transcript += str(evt.delta);
      if (evt.type === SERVER.outputTextDelta) text += str(evt.delta);
      if (evt.type === SERVER.functionCallArgsDone && !seen.has(str(evt.call_id))) {
        seen.add(str(evt.call_id));
        calls.push({ at, name: str(evt.name), args: str(evt.arguments), callId: str(evt.call_id) });
      }
    }
    return {
      createdAt: created.at,
      doneAt: done.at,
      firstAudioAt: first(SERVER.outputAudioDelta),
      firstTranscriptAt: first(SERVER.audioTranscriptDelta),
      firstTextAt: first(SERVER.outputTextDelta),
      transcript,
      text,
      audio: concat(chunks),
      calls,
      errors: evs.filter((t) => t.evt.type === SERVER.error).map((t) => t.evt),
    };
  }

  async close(): Promise<void> {
    if (!this.closed) this.ws.close(1000, "probe complete");
    await Promise.race([this.closedPromise, new Promise((r) => setTimeout(r, 3000))]);
  }

  /** Resolves when the socket closes (for whatever reason). */
  whenClosed(): Promise<void> {
    return this.closedPromise;
  }

  log(msg: string): void {
    if (this.verbose) console.log(`[${now().toFixed(0).padStart(7)}ms] ${this.name.padEnd(8)} ${msg}`);
  }

  private onMessage(evt: ServerEvent): void {
    const t = { at: now(), evt };
    this.events.push(t);
    const quietAudio = evt.type === SERVER.outputAudioDelta && this.loggedAudioFor.has(str(evt.response_id));
    if (evt.type === SERVER.outputAudioDelta) this.loggedAudioFor.add(str(evt.response_id));
    if (!quietAudio) this.log(summarize(evt));
    for (const w of [...this.waiters]) {
      if (w.pred(evt)) {
        clearTimeout(w.timer);
        this.waiters.delete(w);
        w.resolve(t);
      }
    }
  }

  private onClose(code: number, reason: string): void {
    this.closed = describeClose(code, reason);
    this.log(`closed: ${this.closed.message}`);
    for (const w of this.waiters) {
      clearTimeout(w.timer);
      w.reject(new Error(`${this.name} closed: ${this.closed.message}`));
    }
    this.waiters.clear();
  }
}

function summarize(evt: ServerEvent): string {
  const type = evt.type;
  if (type === SERVER.outputAudioDelta) {
    const n = b64Samples(str(evt.delta));
    return `${type}  ≈${((n / AUDIO_RATE) * 1000).toFixed(0)}ms (further chunks not logged)`;
  }
  if (type === SERVER.functionCallArgsDone) return `${type}  ${str(evt.name)}(${str(evt.arguments)})`;
  if (type === SERVER.error) return `${type}  ${JSON.stringify(evt.error ?? evt)}`;
  if (type === SERVER.audioTranscriptLength) return `${type}  ${JSON.stringify(stripBig(evt))}`;
  if (typeof evt.delta === "string") return `${type}  ${JSON.stringify(evt.delta)}`;
  if (typeof evt.transcript === "string") return `${type}  ${JSON.stringify(evt.transcript)}`;
  return type;
}

export function stripBig(evt: ServerEvent): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(evt)) {
    out[k] = typeof v === "string" && v.length > 200 ? `<${v.length} chars>` : v;
  }
  return out;
}
