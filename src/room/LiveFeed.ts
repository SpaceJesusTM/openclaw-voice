import { LevelGate, type LevelGateOptions } from "../audio/LevelGate";
import { msOf } from "../audio/pcm";
import type { AudioSource } from "./AudioBus";

export interface LiveFeedOptions extends LevelGateOptions {
  /** Keep passing audio this long after the level drops, so word gaps don't end the turn. */
  hangoverMs: number;
  /** Audio kept from just before speech is detected, so first syllables aren't clipped. */
  prerollMs: number;
  /** At speech onset, buffer this much before playing, to absorb network jitter. */
  primeMs: number;
}

// hangoverMs was 600: every bit of it delays the agents noticing you stopped.
// Agents now wait for 500 ms of real silence anyway, so word gaps are covered.
export const DEFAULT_LIVE_FEED: LiveFeedOptions = {
  threshold: 600,
  snr: 3,
  floorWindowMs: 3000,
  hangoverMs: 300,
  prerollMs: 300,
  primeMs: 200,
};

/**
 * A microphone as a bus source. A live mic always produces sound, so a level
 * gate decides what is speech: only speech is passed on, which is what lets
 * everyone else in the room notice that the human has stopped talking.
 *
 * The gate adapts (LevelGate): a fixed threshold let a noisy mic count as
 * non-stop speech, and the agents waited forever for the human to finish.
 */
export class LiveFeed implements AudioSource {
  readonly done = false;
  private queue: Int16Array[] = [];
  private queued = 0;
  private preroll: Int16Array[] = [];
  private speaking = false;
  private hangMs = 0;
  private primed = false;
  private segmentMs = 0;
  private gate: LevelGate;

  /** The latest gate reading, for diagnostics. */
  get last() {
    return this.gate.last;
  }

  get isSpeaking(): boolean {
    return this.speaking;
  }

  constructor(
    private opts: LiveFeedOptions = DEFAULT_LIVE_FEED,
    /** Called when a stretch of speech ends, with its length. */
    private onSegmentEnd?: (ms: number) => void,
  ) {
    this.gate = new LevelGate(opts);
  }

  /** Feed one chunk of mic audio (PCM16, 24 kHz). */
  push(chunk: Int16Array): void {
    const loud = this.gate.loud(chunk);
    if (loud) {
      if (!this.speaking) {
        this.speaking = true;
        this.segmentMs = 0;
        for (const p of this.preroll.splice(0)) this.enqueue(p);
      }
      this.hangMs = this.opts.hangoverMs;
      this.enqueue(chunk);
    } else if (this.speaking) {
      this.enqueue(chunk);
      this.hangMs -= msOf(chunk.length);
      if (this.hangMs <= 0) {
        this.speaking = false;
        this.onSegmentEnd?.(this.segmentMs);
      }
    } else {
      this.preroll.push(chunk);
      let kept = this.preroll.reduce((n, c) => n + c.length, 0);
      while (this.preroll.length && msOf(kept) > this.opts.prerollMs) kept -= this.preroll.shift()!.length;
    }
  }

  get available(): number {
    if (!this.primed && this.queued > 0 && (msOf(this.queued) >= this.opts.primeMs || !this.speaking)) this.primed = true;
    return this.primed ? this.queued : 0;
  }

  read(n: number): Int16Array {
    const out = new Int16Array(Math.min(n, this.queued));
    let pos = 0;
    while (pos < out.length) {
      const head = this.queue[0];
      const take = Math.min(head.length, out.length - pos);
      out.set(head.subarray(0, take), pos);
      pos += take;
      if (take === head.length) this.queue.shift();
      else this.queue[0] = head.subarray(take);
    }
    this.queued -= out.length;
    if (this.queued === 0) this.primed = false;
    return out;
  }

  private enqueue(chunk: Int16Array): void {
    this.queue.push(chunk);
    this.queued += chunk.length;
    this.segmentMs += msOf(chunk.length);
  }
}
