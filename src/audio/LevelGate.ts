import { msOf } from "./pcm";

export interface LevelGateOptions {
  /** Minimum RMS level (0..32767) for a chunk to count as speech, however quiet the room. */
  threshold: number;
  /** Speech must also be this many times louder than the room's noise floor. */
  snr: number;
  /** The noise floor is the quietest chunk heard in this window. */
  floorWindowMs: number;
}

/** Tuned on real mics (including browser auto-gain) in the earlier peer-convo room. */
export const DEFAULT_LEVEL_GATE: LevelGateOptions = { threshold: 600, snr: 3, floorWindowMs: 3000 };

/**
 * Says whether a chunk of mic audio is speech. It adapts: a fixed threshold
 * let a noisy mic (or browser auto-gain) count as non-stop speech. Speech must
 * be `snr` times the noise floor, which is the quietest moment in the last few
 * seconds (speech always has gaps; steady noise doesn't).
 */
export class LevelGate {
  /** The latest reading, for diagnostics. */
  readonly last = { level: 0, floor: 0, threshold: 0 };
  private recent: { rms: number; ms: number }[] = [];

  constructor(private opts: LevelGateOptions) {}

  loud(chunk: Int16Array): boolean {
    const level = rms(chunk);
    this.recent.push({ rms: level, ms: msOf(chunk.length) });
    let span = this.recent.reduce((n, l) => n + l.ms, 0);
    while (this.recent.length > 1 && span - this.recent[0].ms >= this.opts.floorWindowMs) span -= this.recent.shift()!.ms;
    const floor = Math.min(...this.recent.map((l) => l.rms));
    const threshold = Math.max(this.opts.threshold, floor * this.opts.snr);
    Object.assign(this.last, { level: Math.round(level), floor: Math.round(floor), threshold: Math.round(threshold) });
    return level >= threshold;
  }
}

export function rms(chunk: Int16Array): number {
  let sum = 0;
  for (let i = 0; i < chunk.length; i++) sum += chunk[i] * chunk[i];
  return Math.sqrt(sum / Math.max(1, chunk.length));
}
