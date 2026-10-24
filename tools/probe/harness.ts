import { mkdirSync, writeFileSync } from "node:fs";
import { SERVER, type ClientEvent } from "../../src/realtime/events";
import { ProbeSession, stripBig } from "./session";

export type Verdict = "PASS" | "FAIL" | "INFO";

export interface Finding {
  verdict: Verdict;
  msg: string;
}

/**
 * Per-check context: opens sessions (tracked, so they are closed and captured
 * when the check ends) and collects findings.
 */
export class CheckContext {
  readonly findings: Finding[] = [];
  readonly sessions: ProbeSession[] = [];
  readonly stamp = new Date().toISOString().replace(/[:.]/g, "-");

  constructor(
    readonly check: string,
    readonly apiKey: string,
    readonly verbose: boolean,
  ) {}

  async open(name: string, config: ClientEvent, model?: string, url?: string): Promise<ProbeSession> {
    const s = await ProbeSession.open(name, this.apiKey, config, this.verbose, model, url);
    this.sessions.push(s);
    return s;
  }

  pass(msg: string) {
    this.add("PASS", msg);
  }
  fail(msg: string) {
    this.add("FAIL", msg);
  }
  info(msg: string) {
    this.add("INFO", msg);
  }
  /** PASS or FAIL on a condition. */
  expect(ok: boolean, msg: string) {
    this.add(ok ? "PASS" : "FAIL", msg);
  }

  capturePath(suffix: string): string {
    mkdirSync("captures", { recursive: true });
    return `captures/${this.check}-${this.stamp}${suffix}`;
  }

  /** Close every session and write all events (audio payloads elided) to captures/. */
  async finish(): Promise<string> {
    await Promise.all(this.sessions.map((s) => s.close()));
    const path = this.capturePath(".json");
    const sessions = Object.fromEntries(
      this.sessions.map((s) => [
        s.name,
        {
          resolved: s.resolved,
          closed: s.closed,
          events: s.events.map(({ at, evt }) => ({
            at: Math.round(at),
            evt: evt.type === SERVER.outputAudioDelta ? { ...evt, delta: `<${String(evt.delta).length} b64>` } : stripBig(evt),
          })),
        },
      ]),
    );
    writeFileSync(path, JSON.stringify({ check: this.check, findings: this.findings, sessions }, null, 2));
    return path;
  }

  private add(verdict: Verdict, msg: string) {
    this.findings.push({ verdict, msg });
    console.log(`  ${verdict === "PASS" ? "✔" : verdict === "FAIL" ? "✘" : "·"} [${this.check}] ${msg}`);
  }
}
