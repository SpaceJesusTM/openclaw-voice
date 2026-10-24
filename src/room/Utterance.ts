import { msOf, samplesOf } from "../audio/pcm";
import { AUDIO_RATE } from "../realtime/events";

/** A piece of reply text and where in the audio it ends (from `.length` events). */
export interface Fragment {
  text: string;
  endMs: number;
}

/**
 * One turn's audio, filled as it is generated (~10x faster than real time) and
 * drained by the room at playback pace.
 */
export class Utterance {
  /** The speaker's voice item, for truncation if they get cut off. */
  itemId: string | null = null;
  fragments: Fragment[] = [];
  transcript = "";
  done = false;
  /** The response behind it failed server-side. */
  failed = false;
  /** Tool calls the voice made in the same response (stay_silent, start_work). */
  calls: { name: string; args: string; callId: string }[] = [];
  /** performance.now() when the first audio arrived, for latency measurement. */
  firstAudioAt: number | null = null;
  readonly finished: Promise<void>;
  /** Resolves on the first audio chunk (or at finish, if there was none). */
  readonly firstAudio: Promise<void>;
  private resolveFinished!: () => void;
  private resolveFirstAudio!: () => void;
  private buf = new Int16Array(AUDIO_RATE * 10);
  private written = 0;
  private readPos = 0;

  constructor(readonly speaker: string) {
    this.finished = new Promise((r) => (this.resolveFinished = r));
    this.firstAudio = new Promise((r) => (this.resolveFirstAudio = r));
  }

  static fromSamples(speaker: string, samples: Int16Array, text: string): Utterance {
    const u = new Utterance(speaker);
    u.push(samples);
    u.transcript = text;
    u.fragments = [{ text, endMs: msOf(samples.length) }];
    u.finish();
    return u;
  }

  push(pcm: Int16Array): void {
    if (this.written + pcm.length > this.buf.length) {
      const grown = new Int16Array(Math.max(this.buf.length * 2, this.written + pcm.length));
      grown.set(this.buf.subarray(0, this.written));
      this.buf = grown;
    }
    this.buf.set(pcm, this.written);
    this.written += pcm.length;
    if (pcm.length) {
      this.firstAudioAt ??= performance.now();
      this.resolveFirstAudio();
    }
  }

  finish(): void {
    if (this.done) return;
    this.done = true;
    this.resolveFirstAudio();
    this.resolveFinished();
  }

  /** Buffered samples not yet played. */
  get available(): number {
    return this.written - this.readPos;
  }

  get totalSamples(): number {
    return this.written;
  }

  read(n: number): Int16Array {
    const out = this.buf.slice(this.readPos, Math.min(this.readPos + n, this.written));
    this.readPos += out.length;
    return out;
  }

  /**
   * What was actually heard by `ms`: whole fragments, then the share of the
   * cut fragment's words that fits its played time, plus "…".
   */
  textUpTo(ms: number): string {
    if (!this.fragments.length) return this.transcript;
    let text = "";
    let startMs = 0;
    for (const f of this.fragments) {
      if (f.endMs <= ms + 50) {
        text += f.text;
        startMs = f.endMs;
        continue;
      }
      if (samplesOf(ms) >= this.written - samplesOf(100)) break; // played to the end
      const words = f.text.trim().split(/\s+/);
      const share = Math.max(0, Math.min(1, (ms - startMs) / Math.max(1, f.endMs - startMs)));
      text += ` ${words.slice(0, Math.round(words.length * share)).join(" ")}…`;
      break;
    }
    return text.trim();
  }
}
