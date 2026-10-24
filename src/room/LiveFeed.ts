import { msOf } from "../audio/pcm";
import type { AudioSource } from "./AudioBus";

export interface LiveFeedOptions {
  /** Minimum RMS level (0..32767) for a chunk to count as speech, however quiet the room. */
  threshold: number;
  /** Speech must also be this many times louder than the room's noise floor. */
  snr: number;
  /** The noise floor is the quietest chunk heard in this window. */
  floorWindowMs: number;
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
 * The gate adapts: a fixed threshold let a noisy mic (or browser auto-gain)
 * count as non-stop speech, and the agents waited forever for the human to
 * finish. Speech must be `snr` times the noise floor, which is the quietest
 * moment in the last few seconds (speech always has gaps; steady noise doesn't).
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
  private recentLevels: { rms: number; ms: number }[] = [];
  /** The latest gate reading, for diagnostics. */
  readonly last = { level: 0, floor: 0, threshold: 0 };

  get isSpeaking(): boolean {
    return this.speaking;
  }

  constructor(
    private opts: LiveFeedOptions = DEFAULT_LIVE_FEED,
    /** Called when a stretch of speech ends, with its length. */
    private onSegmentEnd?: (ms: number) => void,
  ) {}

  /** Feed one chunk of mic audio (PCM16, 24 kHz). */
  push(chunk: Int16Array): void {
    const level = rms(chunk);
    const loud = level >= this.threshold(level, msOf(chunk.length));
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

  /** Record this chunk's level, and return the speech threshold given the room's noise floor. */
  private threshold(level: number, ms: number): number {
    this.recentLevels.push({ rms: level, ms });
    let span = this.recentLevels.reduce((n, l) => n + l.ms, 0);
    while (this.recentLevels.length > 1 && span - this.recentLevels[0].ms >= this.opts.floorWindowMs) {
      span -= this.recentLevels.shift()!.ms;
    }
    const floor = Math.min(...this.recentLevels.map((l) => l.rms));
    const threshold = Math.max(this.opts.threshold, floor * this.opts.snr);
    Object.assign(this.last, { level: Math.round(level), floor: Math.round(floor), threshold: Math.round(threshold) });
    return threshold;
  }

  private enqueue(chunk: Int16Array): void {
    this.queue.push(chunk);
    this.queued += chunk.length;
    this.segmentMs += msOf(chunk.length);
  }
}

function rms(chunk: Int16Array): number {
  let sum = 0;
  for (let i = 0; i < chunk.length; i++) sum += chunk[i] * chunk[i];
  return Math.sqrt(sum / Math.max(1, chunk.length));
}
