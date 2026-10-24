import WebSocket from "ws";
import {
  MODEL,
  SERVER,
  describeClose,
  inputAudioAppend,
  inputAudioCommit,
  type ClientEvent,
  type CloseInfo,
  type ServerEvent,
} from "./events";
import { AUDIO_RATE } from "./events";
import { addUsage, emptyUsage, type Usage } from "./pricing";

const wsUrl = (model: string) => `wss://api.boson.ai/v1/realtime?model=${encodeURIComponent(model)}`;
const MIN_COMMIT_SAMPLES = AUDIO_RATE / 10; // pad tiny buffers to 100 ms before committing

export interface SessionLog {
  (session: string, msg: string): void;
}

/**
 * One server-side Higgs Realtime socket. Unlike the probe's session it keeps no
 * event history: callers subscribe, or wait for the next matching event.
 */
export class RealtimeSession {
  resolved: Record<string, any> | null = null;
  closed: CloseInfo | null = null;
  /** Samples appended since the last commit. */
  pendingSamples = 0;
  /** True between response.created and response.done. */
  generating = false;
  /** Token usage reported by every response.done on this session. */
  readonly usage: Usage = emptyUsage();
  private listeners = new Set<(e: ServerEvent) => void>();

  private constructor(
    readonly name: string,
    private ws: WebSocket,
    private log: SessionLog,
  ) {
    ws.on("message", (data) => {
      const evt = JSON.parse(data.toString()) as ServerEvent;
      if (evt.type === SERVER.responseCreated) this.generating = true;
      if (evt.type === SERVER.responseDone) {
        this.generating = false;
        addUsage(this.usage, (evt.response as { usage?: unknown } | undefined)?.usage);
      }
      if (evt.type === SERVER.error) this.log(name, `error ${JSON.stringify(evt.error ?? evt)}`);
      for (const fn of [...this.listeners]) fn(evt);
    });
    ws.on("close", (code, reason) => {
      this.closed = describeClose(code, reason.toString());
      this.log(name, `closed: ${this.closed.message}`);
      for (const fn of [...this.listeners]) fn({ type: "__closed" });
    });
    ws.on("error", (e) => this.log(name, `socket error: ${e.message}`));
  }

  /**
   * Connect and send the first session.update. Retries when the server's voice
   * validation is rate limited ("voices API returned HTTP 429"), which happens
   * on most first attempts with a preset voice.
   */
  static async connect(
    name: string,
    apiKey: string,
    config: ClientEvent,
    log: SessionLog,
    model = MODEL,
  ): Promise<RealtimeSession> {
    let lastError: unknown;
    for (let attempt = 0; attempt < 4; attempt++) {
      if (attempt) await new Promise((r) => setTimeout(r, 2000 * attempt));
      const ws = new WebSocket(wsUrl(model), { headers: { Authorization: `Bearer ${apiKey}` } });
      const s = new RealtimeSession(name, ws, log);
      const ready = s.waitFor((e) => e.type === SERVER.sessionCreated || e.type === SERVER.error, 15_000, "session.created");
      ws.on("open", () => s.send(config));
      try {
        const evt = await ready;
        if (evt.type === SERVER.error) throw new Error(JSON.stringify(evt.error ?? evt));
        s.resolved = (evt.session as Record<string, any>) ?? null;
        return s;
      } catch (e) {
        lastError = e;
        await s.close();
        if (!String(e).includes("429")) break;
        log(name, `voice validation rate limited, retrying`);
      }
    }
    throw new Error(`${name}: could not start session: ${lastError instanceof Error ? lastError.message : String(lastError)}`);
  }

  send(evt: ClientEvent): void {
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(evt));
  }

  on(fn: (e: ServerEvent) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /** The next event matching `pred`. Rejects on timeout or close. */
  waitFor(pred: (e: ServerEvent) => boolean, timeoutMs: number, label: string): Promise<ServerEvent> {
    return new Promise((resolve, reject) => {
      if (this.closed) return reject(new Error(`${this.name} closed before ${label}`));
      const timer = setTimeout(() => {
        off();
        reject(new Error(`${this.name}: timed out waiting for ${label}`));
      }, timeoutMs);
      const off = this.on((e) => {
        if (e.type === "__closed") {
          clearTimeout(timer);
          off();
          reject(new Error(`${this.name} closed while waiting for ${label}`));
        } else if (pred(e)) {
          clearTimeout(timer);
          off();
          resolve(e);
        }
      });
    });
  }

  appendAudio(chunk: Int16Array): void {
    const bytes = Buffer.from(chunk.buffer, chunk.byteOffset, chunk.length * 2);
    this.send(inputAudioAppend(bytes.toString("base64")));
    this.pendingSamples += chunk.length;
  }

  /**
   * Commit buffered audio as a user item; resolves with its item id, or null if
   * nothing was buffered. A commit is rejected while this session is
   * generating (voice_output_task_ongoing), so wait that out first.
   */
  async commit(): Promise<string | null> {
    if (this.pendingSamples === 0) return null;
    if (this.pendingSamples < MIN_COMMIT_SAMPLES) this.appendAudio(new Int16Array(MIN_COMMIT_SAMPLES - this.pendingSamples));
    for (let attempt = 0; attempt < 3; attempt++) {
      if (this.generating) await this.waitFor((e) => e.type === SERVER.responseDone, 30_000, "response.done").catch(() => undefined);
      const ack = this.waitFor((e) => e.type === SERVER.audioCommitted || e.type === SERVER.error, 5_000, "committed");
      this.send(inputAudioCommit());
      const evt = await ack;
      if (evt.type === SERVER.audioCommitted) {
        this.pendingSamples = 0;
        return String(evt.item_id ?? "");
      }
      const code = String((evt.error as { type?: string } | undefined)?.type ?? "");
      if (code !== "voice_output_task_ongoing") throw new Error(`${this.name}: commit rejected: ${JSON.stringify(evt.error)}`);
    }
    throw new Error(`${this.name}: commit kept colliding with generation`);
  }

  /** session.update, resolved when acknowledged. */
  async update(session: Record<string, unknown>): Promise<void> {
    const ack = this.waitFor((e) => e.type === SERVER.sessionUpdated || e.type === SERVER.error, 8_000, "session.updated");
    this.send({ type: "session.update", session: { type: "realtime", ...session } });
    const evt = await ack;
    if (evt.type === SERVER.error) throw new Error(`${this.name}: session.update rejected: ${JSON.stringify(evt.error)}`);
  }

  async close(): Promise<void> {
    if (this.closed || this.ws.readyState === WebSocket.CLOSED) return;
    const done = new Promise<void>((r) => this.ws.once("close", () => r()));
    this.ws.close(1000, "done");
    await Promise.race([done, new Promise((r) => setTimeout(r, 3000))]);
  }
}
